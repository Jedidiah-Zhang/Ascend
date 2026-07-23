"""GameCalendar 单元测试。

覆盖：初始化校验、首刻静默初始化、分钟/小时/天边界事件、
day_end→day_change 发布顺序、跳天计数、独立查询、shutdown 退订。

日历发布到全局 world_tree 单例，测试通过订阅收集事件。
"""

import pytest

from ascend.config import GAME_DAY, GAME_HOUR, GAME_MINUTE
from ascend.time import WorldClock, GameCalendar
from ascend.world_tree import world_tree


@pytest.fixture
def capture():
    """订阅日历相关事件，返回收集列表；测试结束自动退订。"""
    events: list = []
    unsubs = [
        world_tree.subscribe(et, events.append)
        for et in ("day_end", "day_change", "hour_change", "minute_change")
    ]
    yield events
    for u in unsubs:
        u()


def _of_type(events, event_type):
    return [e for e in events if e.event_type == event_type]


class TestCalendarInit:
    """初始化。"""

    def test_T1_start_day_below_1_raises(self):
        """start_day < 1 抛 ValueError。"""
        clock = WorldClock(epoch=0)
        with pytest.raises(ValueError):
            GameCalendar(clock, start_day=0)

    def test_T2_initial_properties(self):
        """初始化前 hour/minute 返回 0，day 为 start_day。"""
        clock = WorldClock(epoch=0)
        cal = GameCalendar(clock, start_day=3)
        try:
            assert cal.day == 3
            assert cal.hour == 0
            assert cal.minute == 0
            assert cal.elapsed_days == 0
        finally:
            cal.shutdown()

    def test_T3_first_tick_silent_init(self, capture):
        """首个 tick 静默初始化 hour/minute，不发布 hour/minute 事件。"""
        clock = WorldClock(epoch=6 * GAME_HOUR)
        cal = GameCalendar(clock)
        try:
            clock.tick()
            assert cal.hour == 6
            assert cal.minute == 0
            assert _of_type(capture, "hour_change") == []
            assert _of_type(capture, "minute_change") == []
        finally:
            cal.shutdown()


class TestCalendarBoundaries:
    """边界事件。"""

    def test_T4_minute_change_event(self, capture):
        """跨分钟边界发布 minute_change，字段完整。"""
        clock = WorldClock(epoch=6 * GAME_HOUR)
        cal = GameCalendar(clock)
        try:
            clock.tick()  # 静默初始化
            clock.skip(GAME_MINUTE)  # 06:00 → 06:01

            minutes = _of_type(capture, "minute_change")
            assert len(minutes) == 1
            data = minutes[0].data
            assert data["day"] == 1
            assert data["hour"] == 6
            assert data["minute"] == 1
            assert data["game_time"] == clock.time
        finally:
            cal.shutdown()

    def test_T5_hour_change_event(self, capture):
        """跨小时边界发布 hour_change，计数递增。"""
        clock = WorldClock(epoch=6 * GAME_HOUR)
        cal = GameCalendar(clock)
        try:
            clock.tick()
            clock.skip(GAME_HOUR)  # 06:xx → 07:xx

            hours = _of_type(capture, "hour_change")
            assert len(hours) == 1
            data = hours[0].data
            assert data["hour"] == 7
            assert data["previous_hour"] == 6
            assert data["hour_change_count"] == 1
            assert cal.hour_change_count == 1
        finally:
            cal.shutdown()

    def test_T6_day_end_before_day_change(self, capture):
        """跨天时先发 day_end（旧日）再发 day_change（新日）。"""
        clock = WorldClock(epoch=6 * GAME_HOUR)
        cal = GameCalendar(clock)
        try:
            clock.tick()
            clock.skip(GAME_DAY)  # day 1 → day 2

            day_events = [
                e for e in capture
                if e.event_type in ("day_end", "day_change")
            ]
            assert [e.event_type for e in day_events] == ["day_end", "day_change"]
            assert day_events[0].data["day"] == 1
            assert day_events[1].data["day"] == 2
            assert day_events[1].data["previous_day"] == 1
            assert day_events[1].data["skipped_days"] == 0
            assert cal.day == 2
            assert cal.elapsed_days == 1
        finally:
            cal.shutdown()

    def test_T7_multi_day_skip_counts_skipped(self, capture):
        """一次跳 3 天：day_change 仅发一次，skipped_days=2。"""
        clock = WorldClock(epoch=6 * GAME_HOUR)
        cal = GameCalendar(clock)
        try:
            clock.tick()
            clock.skip(3 * GAME_DAY)  # day 1 → day 4

            changes = _of_type(capture, "day_change")
            assert len(changes) == 1
            assert changes[0].data["day"] == 4
            assert changes[0].data["skipped_days"] == 2
            assert cal.day_change_count == 1
        finally:
            cal.shutdown()

    def test_T8_consecutive_steps_track_minutes(self, capture):
        """逐 tick 推进跨过两个分钟边界，发布两次 minute_change。"""
        clock = WorldClock(epoch=6 * GAME_HOUR)
        cal = GameCalendar(clock)
        try:
            clock.tick()
            clock.speed = float(GAME_MINUTE)
            clock.tick()  # +1 分钟
            clock.tick()  # +2 分钟
            assert len(_of_type(capture, "minute_change")) == 2
            assert cal.minute == 2
        finally:
            cal.shutdown()


class TestCalendarQueries:
    """独立查询与生命周期。"""

    def test_T9_day_at_and_time_of_day(self):
        """day_at/time_of_day 不依赖内部状态。"""
        clock = WorldClock(epoch=0)
        cal = GameCalendar(clock)
        try:
            assert cal.day_at(0) == 1
            assert cal.day_at(GAME_DAY) == 2
            assert cal.day_at(GAME_DAY * 2 + 5) == 3
            assert cal.time_of_day(GAME_DAY + 7) == 7
        finally:
            cal.shutdown()

    def test_T10_shutdown_unsubscribes(self, capture):
        """shutdown 后时钟推进不再产生日历事件。"""
        clock = WorldClock(epoch=6 * GAME_HOUR)
        cal = GameCalendar(clock)
        clock.tick()
        cal.shutdown()

        clock.skip(GAME_DAY)

        assert _of_type(capture, "day_change") == []
        assert cal.day == 1  # 状态冻结
