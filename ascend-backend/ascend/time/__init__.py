"""时间系统 — 世界时钟、游戏日历。

Usage:
    from ascend.time import WorldClock, GameCalendar, GAME_MINUTE, GAME_HOUR

    clock = WorldClock()
    clock.tick()        # 每帧调用
    clock.speed = 2.0   # 双倍速
    clock.pause()
"""

from .constants import TICK_RATE, GAME_MINUTE, GAME_HOUR, GAME_DAY, GAME_YEAR
from .clock import WorldClock
from .calendar import GameCalendar

__all__ = [
    "WorldClock",
    "GameCalendar",
    "TICK_RATE",
    "GAME_MINUTE",
    "GAME_HOUR",
    "GAME_DAY",
    "GAME_YEAR",
]
