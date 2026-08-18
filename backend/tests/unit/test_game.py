"""GameEngine tick 循环防护单元测试。

不启动完整引擎（不生成大陆、不开网络），直接驱动 _run_loop
验证熔断与异常恢复语义。完整生命周期见 integration/test_game_engine.py。
"""

import threading
import time

from ascend.game import GameEngine


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

        回归:曾因 @staticmethod 内引用 self.seed 先炸 NameError。
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


# ── 保存脉搏（Issue #40 地基） ──────────────────────────


class TestSavePulse:
    """保存脉搏：调度入队 / 执行顺序 / 失败隔离 / 线程生命周期。"""

    class _FakeChunkStore:
        """最小 chunk_store 替身（仅记录 flush 调用）。"""

        def __init__(self):
            self.flush_calls = 0
            self.fail = False

        def flush(self) -> int:
            self.flush_calls += 1
            if self.fail:
                raise RuntimeError("chunk flush boom")
            return 0

    def _engine(self) -> GameEngine:
        """最小引擎：无网络/无世界，仅脉搏相关字段。"""
        engine = GameEngine(seed=1)
        engine.chunk_store = self._FakeChunkStore()
        engine._save_thread = threading.Thread()  # 占位：触发入队逻辑
        return engine

    def test_maybe_save_pulse_enqueues_when_due(self):
        """到点（距上次脉搏 ≥ SAVE_PULSE_INTERVAL）入队，不阻塞。"""
        from ascend.config import SAVE_PULSE_INTERVAL
        engine = self._engine()
        engine._last_pulse = time.monotonic() - SAVE_PULSE_INTERVAL - 1

        engine._maybe_save_pulse()

        assert engine._save_queue.qsize() == 1
        assert engine._last_pulse > time.monotonic() - 1, "入队后刷新计时"

    def test_maybe_save_pulse_skips_before_interval(self):
        """未到点不入队。"""
        engine = self._engine()
        engine._last_pulse = time.monotonic()

        engine._maybe_save_pulse()

        assert engine._save_queue.qsize() == 0

    def test_maybe_save_pulse_single_slot_merges(self):
        """单槽位防堆积：上一脉搏在途时跳过本次（合并），不阻塞。"""
        from ascend.config import SAVE_PULSE_INTERVAL
        engine = self._engine()
        engine._last_pulse = time.monotonic() - SAVE_PULSE_INTERVAL - 1
        engine._save_queue.put_nowait(None)  # 模拟在途脉搏

        engine._maybe_save_pulse()

        assert engine._save_queue.qsize() == 1, "在途脉搏不叠加"

    def test_maybe_save_pulse_no_thread_noop(self):
        """无保存线程（服务模式）时直接跳过。"""
        engine = self._engine()
        engine._save_thread = None

        engine._maybe_save_pulse()

        assert engine._save_queue.qsize() == 0

    def test_run_pulse_order(self, monkeypatch):
        """脉搏执行顺序：事件 flush → state 写入 → chunk flush。"""
        engine = self._engine()
        calls: list[str] = []

        from ascend.world_tree import world_tree
        monkeypatch.setattr(
            world_tree, "archive_pending",
            lambda: calls.append("events") or 0,
        )
        monkeypatch.setattr(engine, "_save_state_now", lambda: calls.append("state"))
        monkeypatch.setattr(engine.chunk_store, "flush",
                            lambda: calls.append("chunk") or 0)

        engine._run_pulse()

        assert calls == ["events", "state", "chunk"]
        assert engine.chunk_store.flush_calls == 0, "flush 已被 mock"

    def test_run_pulse_step_failure_isolated(self, monkeypatch):
        """单步失败不阻断其余步骤（下一次脉搏重试）。"""
        engine = self._engine()
        calls: list[str] = []

        from ascend.world_tree import world_tree

        def _bad_archive() -> int:
            calls.append("events")
            raise RuntimeError("archive boom")

        monkeypatch.setattr(world_tree, "archive_pending", _bad_archive)
        monkeypatch.setattr(engine, "_save_state_now", lambda: calls.append("state"))
        engine.chunk_store.fail = True
        monkeypatch.setattr(engine.chunk_store, "flush",
                            lambda: calls.append("chunk") or 0)

        engine._run_pulse()  # 不抛异常

        assert calls == ["events", "state", "chunk"], "三步全部执行，失败仅记录"

    def test_final_pulse_runs_full_pulse(self, monkeypatch):
        """退出/快照排空：_final_pulse 执行完整脉搏。"""
        engine = self._engine()
        called: list[str] = []

        monkeypatch.setattr(engine, "_run_pulse", lambda: called.append("pulse"))
        engine._final_pulse()
        assert called == ["pulse"]

    def test_save_worker_exits_after_running_cleared(self):
        """保存线程在 _running 清除后退出（心跳超时）。"""
        engine = self._engine()
        engine._running.set()
        thread = threading.Thread(target=engine._save_worker)
        thread.start()
        engine._running.clear()
        thread.join(timeout=3.0)
        assert not thread.is_alive(), "保存线程应自行退出"
