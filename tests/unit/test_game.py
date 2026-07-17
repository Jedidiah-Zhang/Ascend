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
