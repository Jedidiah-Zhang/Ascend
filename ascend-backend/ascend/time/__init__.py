"""时间系统 — 世界时钟、时间模式与游戏日历。

用法:
    from ascend.time import WorldClock, GameCalendar, TimeMode, GAME_SECONDS_PER_REAL_SECOND

    clock = WorldClock()
    calendar = GameCalendar()
    clock.tick(0.016)  # 约 60fps
"""

from .mode import TimeMode, GAME_SECONDS_PER_REAL_SECOND, GAME_MINUTE, GAME_HOUR, GAME_DAY, GAME_YEAR
from .clock import WorldClock
from .calendar import GameCalendar

__all__ = [
    "WorldClock",
    "GameCalendar",
    "TimeMode",
    "GAME_SECONDS_PER_REAL_SECOND",
    "GAME_MINUTE",
    "GAME_HOUR",
    "GAME_DAY",
    "GAME_YEAR",
]
