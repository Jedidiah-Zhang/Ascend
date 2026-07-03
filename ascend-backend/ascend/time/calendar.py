"""游戏日历 — 追踪游戏日和整点，发布 day_end、day_change、hour_change。"""

from ascend.world_tree import world_tree, Event, AffectedParty
from ascend.log import get_logger
from .mode import GAME_DAY, GAME_HOUR

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


class GameCalendar:
    """游戏日历。

    订阅 game_tick 事件，追踪当前游戏日和整点。
    日期变更时发布 day_change，整点时发布 hour_change。

    用法:
        calendar = GameCalendar()
        calendar.day   # 当前游戏日（从 1 开始）
        calendar.hour  # 当前小时（0-23）
    """

    def __init__(self, start_day: int = 1) -> None:
        if start_day < 1:
            raise ValueError(f"起始日必须 >= 1，实际为 {start_day}")

        self._day: int = start_day
        self._hour: int | None = None
        self._start_day: int = start_day
        self._day_change_count: int = 0
        self._hour_change_count: int = 0
        self._last_game_time: int = 0

        self._unsub_tick = world_tree.subscribe("game_tick", self._on_time_advance)
        self._unsub_skip = world_tree.subscribe("time_skip", self._on_time_advance)

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

    def _on_time_advance(self, event: Event) -> None:
        game_time: int = event.data["game_time"]
        self._last_game_time = game_time

        current_day = int(game_time / GAME_DAY) + 1
        if current_day != self._day:
            previous_day = self._day

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
                },
            ))
            logger.info(
                "日期变更: day %d → %d (累计 %d 天)",
                previous_day, current_day, self.elapsed_days,
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
