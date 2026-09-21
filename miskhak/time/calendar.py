"""游戏日历 — 由世界时钟派生的日/时/分边界与变更事件（WC-4.5）。

不保存"上次值"隐藏状态：当前日/时/分与变更计数均由 ``clock.time``
纯函数派生；唯一游标是上次观察时刻，构造时取自时钟（读档重建后
首帧不产生伪边界事件）。边界事件仍发布到 WorldTree（观测通道）。
"""

from miskhak.events import world_tree, Event, AffectedParty, WorldEvent, SubscriptionScope
from miskhak.log import get_logger
from .clock import WorldClock
from .events import DayChange, DayEnd, HourChange, MinuteChange
from olam.constants import GAME_DAY, GAME_HOUR, GAME_MINUTE

logger = get_logger(__name__)


def tick_to_hms(game_time: int) -> tuple[int, int, int]:
    """tick 数 → (当日小时, 当日分钟, 当日秒)，时刻换算的统一入口。

    事件广播（EventBridge）、终端时间显示（executor）等所有时刻换算
    均经此函数。

    Args:
        game_time: 游戏时间（tick 数）。

    Returns:
        (小时, 分钟, 秒)，小时范围 [0, 24)。
    """
    tod = game_time % GAME_DAY
    hour = tod // GAME_HOUR
    minute = (tod % GAME_HOUR) // GAME_MINUTE
    second = (tod % GAME_MINUTE) * 60 // GAME_MINUTE
    return hour, minute, second


class GameCalendar:
    """游戏日历（时钟派生视图）。

    通过 WorldClock.on_tick/on_skip 接收时间推进，比较"上一观察时刻"
    与当前时刻的派生值，在分钟/小时/天边界发布 WorldTree 事件。

    用法:
        clock = WorldClock()
        calendar = GameCalendar(clock)
        calendar.day    # 当前游戏日（从 1 开始）
        calendar.hour   # 当前小时（0-23）
        calendar.minute # 当前分钟（0-59）
    """

    def __init__(
        self, clock: WorldClock, start_day: int = 1,
        world_tree_arg=None,
    ) -> None:
        """初始化日历。

        Args:
            clock: 世界时钟，通过 on_tick/on_skip 接收时间推进。
            start_day: 起始游戏日，必须 ≥ 1。
            world_tree_arg: 可选 WorldTree 实例（测试注入隔离），
                默认使用模块级单例。
        """
        if start_day < 1:
            raise ValueError(f"起始日必须 >= 1，实际为 {start_day}")

        self._wt = world_tree_arg if world_tree_arg is not None else world_tree
        self._clock = clock
        self._start_day: int = start_day
        # 上次观察时刻（观察游标，非世界状态）：构造时对齐当前时钟，
        # 读档重建后首帧不产生伪边界事件。
        self._last_game_time: int = clock.time

        self._scope = SubscriptionScope()
        self._scope.capture(clock.on_tick(self._on_tick_advance))
        self._scope.capture(clock.on_skip(self._on_skip_advance))

        logger.debug("日历初始化: day=%d", self.day)

    # ── 派生视图（单一事实源 = clock.time）──────────────────

    @property
    def day(self) -> int:
        """当前游戏日（从 1 开始）。"""
        return self.day_at(self._clock.time)

    @property
    def hour(self) -> int:
        """当前小时（0-23）。"""
        return self.hour_at(self._clock.time)

    @property
    def minute(self) -> int:
        """当前分钟（0-59）。"""
        return self.minute_at(self._clock.time)

    @property
    def day_change_count(self) -> int:
        """自起始日起跨过的日边界数（观测可重算，读档不重置）。"""
        return max(0, self.day - self._start_day)

    @property
    def hour_change_count(self) -> int:
        """自起始日起跨过的小时边界数（观测可重算，读档不重置）。"""
        elapsed = self._clock.time - self._start_time()
        return max(0, elapsed // GAME_HOUR)

    @property
    def elapsed_days(self) -> int:
        """从起始日至今经过的天数（不含起始日）。"""
        return max(0, self.day - self._start_day)

    def day_at(self, game_time: int) -> int:
        """计算指定游戏时间对应的游戏日（从 1 开始）。"""
        return int(game_time / GAME_DAY) + 1

    def hour_at(self, game_time: int) -> int:
        """计算指定游戏时间的当日小时（0-23）。"""
        return int(self.time_of_day(game_time) / GAME_HOUR)

    def minute_at(self, game_time: int) -> int:
        """计算指定游戏时间的当日分钟（0-59）。"""
        return int((game_time % GAME_HOUR) / GAME_MINUTE)

    def time_of_day(self, game_time: int) -> int:
        """计算指定游戏时间在当天的 tick 偏移 [0, GAME_DAY)。"""
        return game_time % GAME_DAY

    def _start_time(self) -> int:
        return (self._start_day - 1) * GAME_DAY

    # ── 时间推进 ──────────────────────────────────────────

    def _on_tick_advance(self, game_time: int) -> None:
        """每 tick 回调 — 检测分钟/小时/天边界并发布事件。"""
        self._check_boundaries(game_time)

    def _on_skip_advance(self, skipped: int, game_time: int) -> None:
        """跳转回调 — 检测跳过的边界并发布事件。"""
        self._check_boundaries(game_time)

    def _publish(self, game_time: int, ev: WorldEvent) -> None:
        """发布全局日历事件（location=(0,0)）。"""
        self._wt.publish(Event(
            timestamp=game_time,
            location=(0, 0, None, None),
            initiator_type="system",
            initiator_id="game_calendar",
            affected=[AffectedParty("world", "subject")],
            event_type=ev.event_type,
            data=ev.as_dict(),
        ))

    def _check_boundaries(self, game_time: int) -> None:
        """比较上一观察时刻与当前时刻的派生边界，发布对应事件。"""
        previous = self._last_game_time
        if game_time < previous:
            # 时钟倒退（生产流程不会发生）：静默重同步游标，不发布
            # 倒退的 day_end/day_change 事件。
            self._last_game_time = game_time
            logger.warning(
                "日历检测到时钟倒退，静默重同步到 day=%d",
                self.day_at(game_time),
            )
            return
        if game_time == previous:
            return
        self._last_game_time = game_time

        previous_day = self.day_at(previous)
        current_day = self.day_at(game_time)
        if current_day != previous_day:
            self._publish(game_time, DayEnd(
                day=previous_day,
                elapsed_days=max(0, previous_day - self._start_day),
            ))
            self._publish(game_time, DayChange(
                day=current_day,
                previous_day=previous_day,
                elapsed_days=max(0, current_day - self._start_day),
                day_change_count=max(0, current_day - self._start_day),
                skipped_days=current_day - previous_day - 1,
            ))
            logger.info(
                "日期变更: day %d → %d (累计 %d 天, 跳过 %d 天)",
                previous_day, current_day, self.elapsed_days,
                current_day - previous_day - 1,
            )

        previous_hour = self.hour_at(previous)
        current_hour = self.hour_at(game_time)
        if current_hour != previous_hour:
            self._publish(game_time, HourChange(
                day=current_day,
                hour=current_hour,
                previous_hour=previous_hour,
                hour_change_count=self.hour_change_count,
            ))
            logger.debug(
                "整点: day %d %02d:00 (累计 %d 次)",
                current_day, current_hour, self.hour_change_count,
            )

        previous_minute = self.minute_at(previous)
        current_minute = self.minute_at(game_time)
        if current_minute != previous_minute:
            self._publish(game_time, MinuteChange(
                day=current_day,
                hour=current_hour,
                minute=current_minute,
                game_time=game_time,
            ))

    def shutdown(self) -> None:
        """取消订阅，释放资源。"""
        self._scope.close()
        logger.debug("日历已关闭: day=%d", self.day)

    def __repr__(self) -> str:
        return (
            f"GameCalendar(day={self.day}, "
            f"elapsed_days={self.elapsed_days}, "
            f"day_changes={self.day_change_count})"
        )
