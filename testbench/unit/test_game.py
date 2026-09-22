"""GameEngine tick 循环防护单元测试。

不启动完整引擎（不生成大陆、不开网络），直接驱动 _run_loop
验证熔断与异常恢复语义。完整生命周期见 integration/test_game_engine.py。
"""

import threading
import time

import pytest

from miskhak.app import GameEngine


class TestTickCircuitBreaker:
    """tick 循环熔断。"""

    def test_T1_consecutive_errors_trip_breaker(self):
        """连续异常达到阈值后熔断：循环退出、运行标志清除。"""
        engine = GameEngine(seed=1)
        calls: list[int] = []

        def _bad_tick() -> None:
            calls.append(1)
            raise RuntimeError("boom")

        engine._tick = _bad_tick
        engine._running.set()

        engine._run_loop()  # 应自行退出而非死循环

        assert not engine._running.is_set()
        assert len(calls) == GameEngine._MAX_CONSECUTIVE_ERRORS

    def test_T2_success_resets_error_counter(self):
        """异常与成功交替时计数器复位，不触发熔断。"""
        engine = GameEngine(seed=1)
        state = {"n": 0}

        def _flaky_tick() -> None:
            state["n"] += 1
            if state["n"] >= 10:
                engine._running.clear()  # 正常结束循环
                return
            if state["n"] % 2 == 1:
                raise RuntimeError("boom")  # 奇数次失败，偶数次成功

        engine._tick = _flaky_tick
        engine._running.set()

        engine._run_loop()

        # 累计 5 次异常（1,3,5,7,9）但从未连续，未熔断，跑满 10 次
        assert state["n"] == 10

    def test_T3_normal_exit_when_flag_cleared(self):
        """运行标志清除后循环正常退出，不计异常。"""
        engine = GameEngine(seed=1)

        def _tick_once() -> None:
            engine._running.clear()

        engine._tick = _tick_once
        engine._running.set()

        engine._run_loop()

        assert not engine._running.is_set()


class TestSelectBirthPoint:
    """_select_birth_point 出生点选取。"""

    class _FakeContinent:
        """最小化 ContinentData 替身。"""

        def __init__(self, w: int, h: int, land: list[int],
                     elev: list[float]) -> None:
            self.grid_width = w
            self.grid_height = h
            self.land_mask = land
            self.elevation_field = elev
            self.river_width = []

    def test_no_land_raises_runtime_error(self):
        """全海洋大陆抛 RuntimeError（携带 seed 诊断信息）。

        抛错信息携带入参 seed，不依赖实例状态。
        """
        import pytest
        cont = self._FakeContinent(
            4, 4, land=[0] * 16, elev=[-100.0] * 16,
        )
        with pytest.raises(RuntimeError, match="seed=42"):
            GameEngine._select_birth_point(cont, 42)

    def test_coastal_land_selected(self):
        """海岸陆地 chunk 被选中。"""
        w, h = 4, 4
        land = [0] * 16
        elev = [-100.0] * 16
        # chunk (1,1) 中心格 (3,3)：陆地，邻居 (2,3) 保持海洋 → 海岸
        land[3 * w + 3] = 1
        elev[3 * w + 3] = 25.0
        cont = self._FakeContinent(w, h, land, elev)
        assert GameEngine._select_birth_point(cont, 7) == (1, 1)


# ── 周期保存 ──────────────────────────


class TestPeriodicSave:
    """周期保存：帧边界捕获 / 整体提交 / 失败可见 / 线程生命周期。"""

    class _FakeChunkStore:
        """最小 chunk_store 替身（记录 capture/commit 调用）。"""

        def __init__(self):
            self.capture_calls = 0
            self.commit_calls = 0
            self.fail_commit = False

        def capture_pending(self) -> list:
            self.capture_calls += 1
            return [object()]

        def commit_captured(self, captured) -> int:
            self.commit_calls += 1
            if self.fail_commit:
                raise RuntimeError("chunk commit boom")
            return len(captured)

    class _FakeClock:
        time = 0
        speed = 1.0
        paused = False

        def pause(self) -> None:
            self.paused = True

    class _FakePlayer:
        position = (0.0, 0.0)
        entity = None

    def _engine(self) -> GameEngine:
        """最小引擎：无网络/无世界，但世界状态可捕获（帧边界路径）。"""
        engine = GameEngine(seed=1)
        engine.chunk_store = self._FakeChunkStore()
        engine.clock = self._FakeClock()
        engine.player_service = self._FakePlayer()
        engine.world_id = "a" * 32
        engine.save_manager = object()  # 非 None 即视为已就位（未真正写盘）
        engine._save_thread = threading.Thread()  # 占位：触发入队逻辑
        return engine

    def test_maybe_save_enqueues_when_due(self):
        """到点（距上次周期保存 ≥ AUTOSAVE_INTERVAL）捕获入队，不阻塞。"""
        from miskhak.config import AUTOSAVE_INTERVAL
        engine = self._engine()
        engine._last_save_at = time.monotonic() - AUTOSAVE_INTERVAL - 1

        engine._maybe_save()

        assert engine._save_queue.qsize() == 1
        assert engine.chunk_store.capture_calls == 1, "捕获在游戏线程完成"
        assert engine._last_save_at > time.monotonic() - 1, "入队后刷新计时"

    def test_maybe_save_skips_before_interval(self):
        """未到点不入队。"""
        engine = self._engine()
        engine._last_save_at = time.monotonic()

        engine._maybe_save()

        assert engine._save_queue.qsize() == 0

    def test_maybe_save_single_slot_merges(self):
        """单槽位：上一周期保存在途时跳过本次（合并），不阻塞。"""
        from miskhak.config import AUTOSAVE_INTERVAL
        engine = self._engine()
        engine._last_save_at = time.monotonic() - AUTOSAVE_INTERVAL - 1
        engine._save_queue.put_nowait(object())  # 模拟在途的周期保存

        engine._maybe_save()

        assert engine._save_queue.qsize() == 1, "在途周期保存不叠加"
        assert engine.chunk_store.capture_calls == 0, "在途时不做无谓捕获"

    def test_maybe_save_no_thread_noop(self):
        """无保存线程（服务模式）时直接跳过。"""
        engine = self._engine()
        engine._save_thread = None

        engine._maybe_save()

        assert engine._save_queue.qsize() == 0

    def test_maybe_save_capture_failure_recorded(self, monkeypatch):
        """捕获失败：记录失败并可见（不入队、不静默）。"""
        from miskhak.config import AUTOSAVE_INTERVAL
        engine = self._engine()
        engine._last_save_at = time.monotonic() - AUTOSAVE_INTERVAL - 1

        def _bad_capture():
            raise RuntimeError("capture boom")

        monkeypatch.setattr(engine, "_capture_save", _bad_capture)
        engine._maybe_save()

        assert engine._save_queue.qsize() == 0
        assert engine.save_failures == 1
        assert "capture boom" in (engine.last_save_error or "")

    def test_maybe_save_skips_invalidated_world(self):
        """世界失效后不再保存（轨迹作废，WC-9.2）。"""
        from miskhak.config import AUTOSAVE_INTERVAL
        engine = self._engine()
        engine._last_save_at = time.monotonic() - AUTOSAVE_INTERVAL - 1
        engine._world_invalidated = "帧提交失败"

        engine._maybe_save()

        assert engine._save_queue.qsize() == 0
        assert engine.chunk_store.capture_calls == 0

    def test_run_save_order(self, monkeypatch):
        """周期保存提交顺序：事件 flush → state 写入 → chunk 提交 → manifest。"""
        engine = self._engine()
        calls: list[str] = []

        from miskhak.events import world_tree
        monkeypatch.setattr(
            world_tree, "archive_pending",
            lambda: calls.append("events") or 0,
        )
        monkeypatch.setattr(engine, "_save_state_now",
                            lambda state: calls.append("state"))
        monkeypatch.setattr(engine.chunk_store, "commit_captured",
                            lambda captured: calls.append("chunk") or 0)
        monkeypatch.setattr(engine, "_persist_manifest",
                            lambda: calls.append("manifest"))

        engine._run_save(engine._capture_save())

        assert calls == ["events", "state", "chunk", "manifest"]

    def test_run_save_failure_aborts_remaining_steps(self, monkeypatch):
        """任一步失败即失败：后续步骤不再执行，异常向上传播。"""
        engine = self._engine()
        calls: list[str] = []

        from miskhak.events import world_tree

        def _bad_archive() -> int:
            calls.append("events")
            raise RuntimeError("archive boom")

        monkeypatch.setattr(world_tree, "archive_pending", _bad_archive)
        monkeypatch.setattr(engine, "_save_state_now",
                            lambda state: calls.append("state"))
        monkeypatch.setattr(engine.chunk_store, "commit_captured",
                            lambda captured: calls.append("chunk") or 0)

        with pytest.raises(RuntimeError, match="archive boom"):
            engine._run_save(engine._capture_save())

        assert calls == ["events"], "失败即中止：不得静默部分成功"

    def test_run_save_chunk_failure_keeps_state_newer(self, monkeypatch):
        """chunk 提交失败：state 已先行写入（可恢复组合），异常向上抛。"""
        engine = self._engine()
        calls: list[str] = []

        from miskhak.events import world_tree
        monkeypatch.setattr(
            world_tree, "archive_pending", lambda: calls.append("events") or 0,
        )
        monkeypatch.setattr(engine, "_save_state_now",
                            lambda state: calls.append("state"))
        engine.chunk_store.fail_commit = True

        with pytest.raises(RuntimeError, match="chunk commit boom"):
            engine._run_save(engine._capture_save())

        assert calls == ["events", "state"], "state 先于 chunk（读档可补齐）"

    def test_run_save_debug_mode_flushes_without_state(self, monkeypatch):
        """无存档位（调试模式）：仍 flush 事件/chunk，跳过 state/manifest。"""
        engine = self._engine()
        engine.world_id = None
        calls: list[str] = []

        from miskhak.events import world_tree
        monkeypatch.setattr(
            world_tree, "archive_pending", lambda: calls.append("events") or 0,
        )
        monkeypatch.setattr(engine, "_save_state_now",
                            lambda state: calls.append("state"))
        monkeypatch.setattr(engine, "_persist_manifest",
                            lambda: calls.append("manifest"))
        monkeypatch.setattr(engine.chunk_store, "commit_captured",
                            lambda captured: calls.append("chunk") or 0)

        payload = engine._capture_save()
        assert payload.state is None, "无存档位不采集 state"
        engine._run_save(payload)

        assert calls == ["events", "chunk"]

    def test_run_save_refuses_invalidated_world(self, monkeypatch):
        """失效世界拒绝保存：最后有效检查点不得被作废轨迹覆盖。"""
        from olam.runtime import WorldInvalidatedError

        engine = self._engine()
        engine._world_invalidated = "帧提交失败"
        calls: list[str] = []

        from miskhak.events import world_tree
        monkeypatch.setattr(
            world_tree, "archive_pending", lambda: calls.append("events") or 0,
        )
        monkeypatch.setattr(engine, "_save_state_now",
                            lambda state: calls.append("state"))

        with pytest.raises(WorldInvalidatedError, match="拒绝保存"):
            engine._run_save(engine._capture_save())
        assert calls == [], "失效：事件/state 都不得提交"

    def test_save_worker_records_failure_and_continues(self, monkeypatch):
        """保存线程：单次周期保存失败记录计数，线程继续服务下一次。"""
        engine = self._engine()

        def _bad_save(payload) -> None:
            raise RuntimeError("save boom")

        monkeypatch.setattr(engine, "_run_save", _bad_save)
        engine._running.set()
        engine._save_queue.put_nowait(object())
        thread = threading.Thread(target=engine._save_worker)
        thread.start()
        deadline = time.monotonic() + 3.0
        while engine.save_failures == 0 and time.monotonic() < deadline:
            time.sleep(0.01)
        engine._running.clear()
        thread.join(timeout=3.0)

        assert engine.save_failures == 1
        assert "save boom" in (engine.last_save_error or "")

    def test_final_save_runs_all_steps(self, monkeypatch):
        """退出/快照排空：_final_save 执行完整周期保存。"""
        engine = self._engine()
        called: list[str] = []

        monkeypatch.setattr(engine, "_run_save", lambda: called.append("save"))
        engine._final_save()
        assert called == ["save"]

    def test_final_save_failure_visible_and_optional_raise(self, monkeypatch):
        """收尾保存失败：默认记录不打断清理；快照路径要求抛出。"""
        engine = self._engine()

        def _bad_save() -> None:
            raise RuntimeError("final boom")

        monkeypatch.setattr(engine, "_run_save", _bad_save)
        engine._final_save()  # 不抛
        assert engine.save_failures == 1
        with pytest.raises(RuntimeError, match="final boom"):
            engine._final_save(raise_on_failure=True)
        assert engine.save_failures == 2

    def test_save_worker_exits_after_running_cleared(self):
        """保存线程在 _running 清除后退出（心跳超时）。"""
        engine = self._engine()
        engine._running.set()
        thread = threading.Thread(target=engine._save_worker)
        thread.start()
        engine._running.clear()
        thread.join(timeout=3.0)
        assert not thread.is_alive(), "保存线程应自行退出"


class TestWorldInvalidation:
    """世界失效接线：停表、停止保存、标记可查询。"""

    class _FakeScheduler:
        def __init__(self, reason):
            self._reason = reason

        @property
        def invalidated(self):
            return self._reason

    class _FakeClock:
        paused = False
        time = 0

        def tick(self) -> None:
            pass

        def pause(self) -> None:
            self.paused = True

    def _engine(self, reason):
        engine = GameEngine(seed=1)
        engine.clock = self._FakeClock()
        engine._scheduler = self._FakeScheduler(reason)
        engine._save_thread = None
        return engine

    def test_tick_pauses_clock_and_marks_invalidated(self):
        engine = self._engine("帧提交失败（应用动作 1/1 抛错）")

        engine._tick()

        assert engine.world_invalidated is not None
        assert "帧提交失败" in engine.world_invalidated
        assert engine.clock.paused is True, "失效后停表（时间不得继续走）"

    def test_tick_keeps_running_when_valid(self):
        engine = self._engine(None)

        engine._tick()

        assert engine.world_invalidated is None
        assert engine.clock.paused is False

    def test_invalidation_marked_once(self):
        engine = self._engine("boom")
        engine._tick()
        first = engine.world_invalidated
        engine._tick()
        assert engine.world_invalidated == first


class TestTimeSyncBroadcast:
    """表现层时间同步：跨游戏分钟边界广播 time_sync（不进世界树）。"""

    class _FakeServer:
        def __init__(self) -> None:
            self.messages: list[dict] = []

        def broadcast(self, message: dict) -> None:
            self.messages.append(message)

    class _FakeClock:
        def __init__(self) -> None:
            self.time = 0

        def tick(self) -> None:
            self.time += 1

    def _engine(self) -> GameEngine:
        engine = GameEngine(seed=1)
        engine.clock = self._FakeClock()
        engine.server = self._FakeServer()
        engine._save_thread = None
        return engine

    def test_first_tick_broadcasts_current_time(self):
        """首帧广播一次当前时刻（初始游标为 -1）。"""
        from olam.constants import GAME_HOUR

        engine = self._engine()
        engine.clock.time = 6 * GAME_HOUR  # 06:00
        engine._tick()

        assert len(engine.server.messages) == 1
        msg = engine.server.messages[0]
        assert msg["type"] == "event"
        assert msg["event_type"] == "time_sync"
        assert msg["payload"]["game_hour"] == 6
        assert msg["payload"]["game_minute"] == 0
        assert msg["payload"]["data"]["day"] == 1
        assert msg["payload"]["data"]["game_time"] == engine.clock.time

    def test_same_minute_not_rebroadcast(self):
        """同一游戏分钟内不重复广播。"""
        engine = self._engine()
        for _ in range(10):
            engine._tick()
        assert len(engine.server.messages) == 1

    def test_minute_boundary_rebroadcasts(self):
        """跨游戏分钟边界广播新时刻（跳转后同样一次到位）。"""
        from olam.constants import GAME_DAY, GAME_MINUTE

        engine = self._engine()
        engine._tick()
        engine.clock.time = 2 * GAME_DAY + 3 * GAME_MINUTE + 5  # 跳到第 3 天
        engine._tick()

        assert len(engine.server.messages) == 2
        payload = engine.server.messages[-1]["payload"]
        assert payload["game_hour"] == 0
        assert payload["game_minute"] == 3
        assert payload["data"]["day"] == 3

    def test_service_mode_does_not_broadcast(self):
        """服务模式（无世界）不广播时间同步。"""
        engine = self._engine()
        engine._service_mode = True
        engine._tick()
        assert engine.server.messages == []
