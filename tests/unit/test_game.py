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
