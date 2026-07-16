"""游戏日历 — 追踪游戏日、整点和分钟，发布 day/hour/minute 变更事件。

通过 WorldClock.on_tick/on_skip 接收时间推进信号（不经过 WorldTree），
检测到分钟/小时/天边界后发布对应的语义事件到 WorldTree。
"""

from ascend.world_tree import world_tree, Event, AffectedParty
from ascend.log import get_logger
from .clock import WorldClock
from ascend.config import GAME_DAY, GAME_HOUR, GAME_MINUTE

logger = get_logger(__name__)

world_tree.register_event_schema(
    "day_end",
    required={"day": int, "elapsed_days": int},
    description="每日结束时发布（day_change 之前），用于日终结算",
)
world_tree.register_event_schema(
    "day_change",
    required={
        "day": int,
        "previous_day": int,
        "elapsed_days": int,
        "day_change_count": int,
        "skipped_days": int,
    },
    description="日期变更时发布，触发群体/生态等日更模块",
)
world_tree.register_event_schema(
    "hour_change",
    required={
        "day": int,
        "hour": int,
        "previous_hour": int,
        "hour_change_count": int,
    },
    description="整点变更时发布，用于高频定期任务",
)
world_tree.register_event_schema(
    "minute_change",
    required={
        "day": int,
        "hour": int,
        "minute": int,
        "game_time": int,
    },
    description="分钟变更时发布，用于需要分钟级更新的模块（如天气引擎）",
)


class GameCalendar:
    """游戏日历。

    通过 WorldClock.on_tick/on_skip 接收时间推进，追踪当前游戏日、
    整点和分钟。分钟/小时/天边界变更时发布 WorldTree 事件。

    用法:
        clock = WorldClock()
        calendar = GameCalendar(clock)
        calendar.day    # 当前游戏日（从 1 开始）
        calendar.hour   # 当前小时（0-23）
        calendar.minute # 当前分钟（0-59）
    """

    def __init__(self, clock: WorldClock, start_day: int = 1) -> None:
        """初始化日历。

        Args:
            clock: 世界时钟，通过 on_tick/on_skip 接收时间推进。
            start_day: 起始游戏日，必须 ≥ 1。
        """
        if start_day < 1:
            raise ValueError(f"起始日必须 >= 1，实际为 {start_day}")

        self._day: int = start_day
        self._hour: int | None = None
        self._minute: int | None = None
        self._start_day: int = start_day
        self._day_change_count: int = 0
        self._hour_change_count: int = 0
        self._last_game_time: int = 0

        self._unsub_tick = clock.on_tick(self._on_tick_advance)
        self._unsub_skip = clock.on_skip(self._on_skip_advance)

        logger.debug("日历初始化: day=%d", self._day)

    @property
    def day(self) -> int:
        """当前游戏日（从 1 开始）。"""
        return self._day

    @property
    def hour(self) -> int:
        """当前小时（0-23），初始化前返回 0。"""
        return self._hour if self._hour is not None else 0

    @property
    def minute(self) -> int:
        """当前分钟（0-59），初始化前返回 0。"""
        return self._minute if self._minute is not None else 0

    @property
    def day_change_count(self) -> int:
        """累计日期变更次数。"""
        return self._day_change_count

    @property
    def hour_change_count(self) -> int:
        """累计整点变更次数。"""
        return self._hour_change_count

    @property
    def elapsed_days(self) -> int:
        """从起始日至今经过的天数（不含起始日）。"""
        return self._day - self._start_day

    def _on_tick_advance(self, game_time: int) -> None:
        """每 tick 回调 — 检测分钟/小时/天边界并发布事件。"""
        self._check_boundaries(game_time)

    def _on_skip_advance(self, skipped: int, game_time: int) -> None:
        """跳转回调 — 检测跳过的边界并发布事件。"""
        self._check_boundaries(game_time)

    def _check_boundaries(self, game_time: int) -> None:
        """检测分钟/小时/天边界，发布对应事件。

        Args:
            game_time: 当前游戏时间（tick）。
        """
        self._last_game_time = game_time

        current_day = int(game_time / GAME_DAY) + 1
        if current_day != self._day:
            previous_day = self._day
            real_skipped = current_day - previous_day - 1
            if real_skipped < 0:
                real_skipped = 0

            world_tree.publish(Event(
                timestamp=game_time,
                location=(0, 0, None, None),
                initiator_type="system",
                initiator_id="game_calendar",
                affected=[AffectedParty("world", "subject")],
                event_type="day_end",
                data={
                    "day": previous_day,
                    "elapsed_days": previous_day - self._start_day,
                },
            ))

            self._day = current_day
            self._day_change_count += 1

            world_tree.publish(Event(
                timestamp=game_time,
                location=(0, 0, None, None),
                initiator_type="system",
                initiator_id="game_calendar",
                affected=[AffectedParty("world", "subject")],
                event_type="day_change",
                data={
                    "day": current_day,
                    "previous_day": previous_day,
                    "elapsed_days": self.elapsed_days,
                    "day_change_count": self._day_change_count,
                    "skipped_days": real_skipped,
                },
            ))
            logger.info(
                "日期变更: day %d → %d (累计 %d 天, 跳过 %d 天)",
                previous_day, current_day, self.elapsed_days, real_skipped,
            )

        current_hour = int((game_time % GAME_DAY) / GAME_HOUR)
        if self._hour is None:
            self._hour = current_hour
        elif current_hour != self._hour:
            previous_hour = self._hour
            self._hour = current_hour
            self._hour_change_count += 1

            world_tree.publish(Event(
                timestamp=game_time,
                location=(0, 0, None, None),
                initiator_type="system",
                initiator_id="game_calendar",
                affected=[AffectedParty("world", "subject")],
                event_type="hour_change",
                data={
                    "day": self._day,
                    "hour": current_hour,
                    "previous_hour": previous_hour,
                    "hour_change_count": self._hour_change_count,
                },
            ))
            logger.debug(
                "整点: day %d %02d:00 (累计 %d 次)",
                self._day, current_hour, self._hour_change_count,
            )

        current_minute = int((game_time % GAME_HOUR) / GAME_MINUTE)
        if self._minute is None:
            self._minute = current_minute
        elif current_minute != self._minute:
            self._minute = current_minute
            world_tree.publish(Event(
                timestamp=game_time,
                location=(0, 0, None, None),
                initiator_type="system",
                initiator_id="game_calendar",
                affected=[AffectedParty("world", "subject")],
                event_type="minute_change",
                data={
                    "day": self._day,
                    "hour": self._hour,
                    "minute": current_minute,
                    "game_time": game_time,
                },
            ))

    def day_at(self, game_time: int) -> int:
        """计算指定游戏时间对应的游戏日。

        Args:
            game_time: 游戏时间（tick 数）。

        Returns:
            对应的游戏日（从 1 开始）。
        """
        return int(game_time / GAME_DAY) + 1

    def time_of_day(self, game_time: int) -> int:
        """计算指定游戏时间在当天的 tick 偏移。

        Args:
            game_time: 游戏时间（tick 数）。

        Returns:
            当天内的 tick 偏移 [0, GAME_DAY)。
        """
        return game_time % GAME_DAY

    def shutdown(self) -> None:
        """取消订阅，释放资源。"""
        self._unsub_tick()
        self._unsub_skip()
        logger.debug("日历已关闭: day=%d", self._day)

    def __repr__(self) -> str:
        return (
            f"GameCalendar(day={self._day}, "
            f"elapsed_days={self.elapsed_days}, "
            f"day_changes={self._day_change_count})"
        )
