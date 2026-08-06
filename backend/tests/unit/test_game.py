"""GameEngine tick 循环防护单元测试。

不启动完整引擎（不生成大陆、不开网络），直接驱动 _run_loop
验证熔断与异常恢复语义。完整生命周期见 integration/test_game_engine.py。
"""

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


class TestReloadLoop:
    """读档重建：常驻 tick 线程内执行 _reload 后循环继续。"""

    def test_pending_load_runs_reload_and_continues(self):
        """_pending_load 置位时在循环内执行 _reload，完成后循环不退出。"""
        engine = GameEngine(seed=1)
        engine._tick = lambda: None
        received: list[tuple] = []

        def _fake_reload(*args):
            received.append(args)
            engine._running.clear()  # 模拟重建完成后的正常退出

        engine._reload = _fake_reload
        engine._pending_load = ("world-x", None)
        engine._running.set()

        engine._run_loop()

        assert received == [("world-x", None)]
        assert engine._pending_load is None
        assert not engine._running.is_set()

    def test_stop_during_reload_exits_loop_without_resurrection(self):
        """stop() 在读档期间调用：_reload 不再执行，循环立即退出。"""
        engine = GameEngine(seed=1)
        ticks: list = []
        engine._tick = lambda: ticks.append(1)
        engine._reload = lambda *a: ticks.append("reload")
        engine._pending_load = ("world-x", None)
        engine._running.set()
        engine._stop_requested.set()  # 模拟 stop() 在读档期被调用

        engine._run_loop()

        assert ticks == [], "取消标志置位后循环不应执行任何 tick/读档"
        assert engine._pending_load is not None, "读档请求不应被消费"

    def test_stop_requested_mid_loop_exits_next_iteration(self):
        """循环运行中 stop() 到达：本轮 tick 正常执行，下一轮退出。"""
        engine = GameEngine(seed=1)
        ticks: list = []

        def _tick_once() -> None:
            ticks.append(1)
            engine._stop_requested.set()  # 模拟外部 stop()

        engine._tick = _tick_once
        engine._running.set()

        engine._run_loop()

        assert ticks == [1], "恰好执行一轮后退出"


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
