"""时间事件契约 — 日历在分钟/小时/天边界发布的 data 结构。

data 键即 dataclass 字段，event_type 由类属性声明（不再重复写字符串）。
"""

from dataclasses import dataclass
from typing import ClassVar

from ascend.world_tree.event import WorldEvent


@dataclass
class MinuteChange(WorldEvent):
    event_type: ClassVar[str] = "minute_change"
    day: int
    hour: int
    minute: int
    game_time: int


@dataclass
class HourChange(WorldEvent):
    event_type: ClassVar[str] = "hour_change"
    day: int
    hour: int
    previous_hour: int
    hour_change_count: int


@dataclass
class DayChange(WorldEvent):
    event_type: ClassVar[str] = "day_change"
    day: int
    previous_day: int
    elapsed_days: int
    day_change_count: int
    skipped_days: int


@dataclass
class DayEnd(WorldEvent):
    """当日结束事件（跨日时发布，与 day_change 成对出现）。"""

    event_type: ClassVar[str] = "day_end"
    day: int
    elapsed_days: int
