"""WorldClock 单元测试。

覆盖：推进语义（tick/step/skip/run_to）、speed 倍率与浮点累加器、
暂停/恢复、回调注册/退订/异常隔离、边界校验。
"""

import pytest

from ascend.config import GAME_HOUR, GAME_DAY, GAME_YEAR
from ascend.time import WorldClock


class TestClockInit:
    """初始化与属性。"""

    def test_T1_default_epoch_is_6am(self):
        """默认 epoch 为 06:00（6 * GAME_HOUR）。"""
        clock = WorldClock()
        assert clock.time == 6 * GAME_HOUR

    def test_T2_custom_epoch(self):
        """自定义 epoch 生效。"""
        clock = WorldClock(epoch=12345)
        assert clock.time == 12345

    def test_T3_initial_state(self):
        """初始 speed=1.0、未暂停、tick_count=0。"""
        clock = WorldClock(epoch=0)
        assert clock.speed == 1.0
        assert clock.paused is False
        assert clock.tick_count == 0


class TestClockTick:
    """tick() 推进语义。"""

    def test_T4_tick_advances_by_speed_1(self):
        """speed=1 时每次 tick 推进 1。"""
        clock = WorldClock(epoch=0)
        clock.tick()
        assert clock.time == 1
        assert clock.tick_count == 1

    def test_T5_tick_speed_2_advances_2(self):
        """speed=2 时一次 tick 推进 2 tick，tick_count 仅 +1。"""
        clock = WorldClock(epoch=0)
        clock.speed = 2.0
        clock.tick()
        assert clock.time == 2
        assert clock.tick_count == 1

    def test_T6_fractional_speed_accumulates(self):
        """speed=0.5 时每 2 帧推进 1 tick（浮点累加器）。"""
        clock = WorldClock(epoch=0)
        clock.speed = 0.5
        clock.tick()
        assert clock.time == 0
        clock.tick()
        assert clock.time == 1

    def test_T7_zero_speed_no_advance(self):
        """speed=0 时 tick 不推进。"""
        clock = WorldClock(epoch=0)
        clock.speed = 0.0
        clock.tick()
        assert clock.time == 0
        assert clock.tick_count == 0

    def test_T8_negative_speed_raises(self):
        """负 speed 抛 ValueError。"""
        clock = WorldClock(epoch=0)
        with pytest.raises(ValueError):
            clock.speed = -1.0

    def test_T9_paused_tick_noop(self):
        """暂停时 tick 空转，resume 后恢复。"""
        clock = WorldClock(epoch=0)
        clock.pause()
        assert clock.paused is True
        clock.tick()
        assert clock.time == 0
        clock.resume()
        clock.tick()
        assert clock.time == 1


class TestClockStep:
    """step() 强制推进。"""

    def test_T10_step_ignores_pause(self):
        """step 忽略暂停，恰好推进 1 tick。"""
        clock = WorldClock(epoch=0)
        clock.pause()
        clock.step()
        assert clock.time == 1

    def test_T11_step_ignores_speed(self):
        """step 忽略 speed 倍率。"""
        clock = WorldClock(epoch=0)
        clock.speed = 100.0
        clock.step()
        assert clock.time == 1


class TestClockSkip:
    """skip() 瞬间跳转。"""

    def test_T12_skip_advances(self):
        """skip 直接加 N tick。"""
        clock = WorldClock(epoch=0)
        clock.skip(3 * GAME_DAY)
        assert clock.time == 3 * GAME_DAY

    def test_T13_skip_nonpositive_raises(self):
        """skip(0) 和负数抛 ValueError。"""
        clock = WorldClock(epoch=0)
        with pytest.raises(ValueError):
            clock.skip(0)
        with pytest.raises(ValueError):
            clock.skip(-5)

    def test_T14_skip_notifies_skip_callbacks_not_tick(self):
        """skip 触发 on_skip 回调，不触发 on_tick 回调。"""
        clock = WorldClock(epoch=0)
        tick_calls: list[int] = []
        skip_calls: list[tuple[int, int]] = []
        clock.on_tick(tick_calls.append)
        clock.on_skip(lambda s, t: skip_calls.append((s, t)))

        clock.skip(100)

        assert tick_calls == []
        assert skip_calls == [(100, 100)]


class TestClockRunTo:
    """run_to() 逐 tick 模拟。"""

    def test_T15_run_to_reaches_target(self):
        """run_to 推进到目标时间，每个中间 tick 触发回调。"""
        clock = WorldClock(epoch=0)
        ticks: list[int] = []
        clock.on_tick(ticks.append)

        clock.run_to(10)

        assert clock.time == 10
        assert ticks == list(range(1, 11))

    def test_T16_run_to_past_raises(self):
        """目标 ≤ 当前时间抛 ValueError。"""
        clock = WorldClock(epoch=100)
        with pytest.raises(ValueError):
            clock.run_to(100)
        with pytest.raises(ValueError):
            clock.run_to(50)

    def test_T17_run_to_zero_speed_raises(self):
        """speed=0 时 run_to 抛 ValueError（否则死循环）。"""
        clock = WorldClock(epoch=0)
        clock.speed = 0.0
        with pytest.raises(ValueError):
            clock.run_to(10)

    def test_T18_run_to_restores_pause_state(self):
        """暂停状态下 run_to 临时恢复，完成后还原为暂停。"""
        clock = WorldClock(epoch=0)
        clock.pause()
        clock.run_to(5)
        assert clock.time == 5
        assert clock.paused is True


class TestClockCallbacks:
    """回调注册/退订/异常隔离。"""

    def test_T19_on_tick_callback_receives_time(self):
        """on_tick 回调收到推进后的 game_time。"""
        clock = WorldClock(epoch=10)
        received: list[int] = []
        clock.on_tick(received.append)
        clock.tick()
        assert received == [11]

    def test_T20_unsubscribe_stops_callback(self):
        """退订后回调不再触发，重复退订幂等。"""
        clock = WorldClock(epoch=0)
        received: list[int] = []
        unsub = clock.on_tick(received.append)
        clock.tick()
        unsub()
        clock.tick()
        unsub()  # 幂等，不抛异常
        assert received == [1]

    def test_T21_callback_exception_isolated(self):
        """一个回调抛异常不影响后续回调执行。"""
        clock = WorldClock(epoch=0)
        received: list[int] = []

        def _bad(_t: int) -> None:
            raise RuntimeError("boom")

        clock.on_tick(_bad)
        clock.on_tick(received.append)

        clock.tick()  # 不应向外抛异常
        assert received == [1]


class TestClockDerived:
    """派生查询。"""

    def test_T22_game_days_and_years(self):
        """game_days/game_years 换算正确。"""
        clock = WorldClock(epoch=GAME_DAY * 2)
        assert clock.game_days() == pytest.approx(2.0)
        assert clock.game_years() == pytest.approx(2 * GAME_DAY / GAME_YEAR)
