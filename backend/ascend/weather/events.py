"""天气事件契约 — 等级变化事件 + 离散事件。

事件类型：
  - temperature_change / humidity_change / wind_change / sunshine_change：
        等级变化时发布（如 tier 2→3），附带 numeric 值、prev_tier 和 tier。
  - precipitation_start / precipitation_stop：降雨事件切换
  - cold_snap_start / cold_snap_stop：寒潮事件切换
  - heat_wave_start / heat_wave_stop：热浪事件切换
  - storm_start / storm_stop：暴风雨事件切换
  - season_change / sunrise / sunset：全局季节 / per-chunk 昼夜

data 键即 dataclass 字段，event_type 由类属性声明（不再重复写字符串）。
"""

from dataclasses import dataclass
from typing import ClassVar

from ascend.world_tree.event import WorldEvent


@dataclass
class TemperatureChange(WorldEvent):
    """温度等级变化。prev_tier 为变化前等级，tier 为当前等级。"""

    event_type: ClassVar[str] = "temperature_change"
    temperature: float
    prev_tier: int
    tier: int
    season: int
    time_of_day: int


@dataclass
class HumidityChange(WorldEvent):
    """湿度等级变化。prev_tier 为变化前等级，tier 为当前等级。"""

    event_type: ClassVar[str] = "humidity_change"
    humidity: float
    prev_tier: int
    tier: int
    time_of_day: int


@dataclass
class WindChange(WorldEvent):
    """风速等级变化。prev_tier 为变化前等级，tier 为当前等级。"""

    event_type: ClassVar[str] = "wind_change"
    wind_speed: float
    prev_tier: int
    tier: int
    wind_dir_x: float
    wind_dir_y: float
    time_of_day: int


@dataclass
class SunshineChange(WorldEvent):
    """日照等级变化。prev_tier 为变化前等级，tier 为当前等级。"""

    event_type: ClassVar[str] = "sunshine_change"
    sunshine: float
    prev_tier: int
    tier: int
    season: int
    time_of_day: int


@dataclass
class PrecipitationStart(WorldEvent):
    """降水开始。precip_type: rain|snow，由当前温度判定。

    chunks: 区域涉及的 chunk 坐标（必填）——状态引擎按此批量涂抹，
    前端区域高亮亦可用。坐标 = 区域连通域 chunk 集合。
    """

    event_type: ClassVar[str] = "precipitation_start"
    precip_type: str
    intensity: float
    time_of_day: int
    chunks: tuple[tuple[int, int], ...]


@dataclass
class PrecipitationStop(WorldEvent):
    """降水停止（区域降水事件结束）。

    chunks: 区域涉及的 chunk 坐标（必填，与 start 同集合）。
    """

    event_type: ClassVar[str] = "precipitation_stop"
    time_of_day: int
    chunks: tuple[tuple[int, int], ...]


@dataclass
class SeasonChange(WorldEvent):
    """季节切换（全局事件，location=(0,0)）。season 0=春 1=夏 2=秋 3=冬。"""

    event_type: ClassVar[str] = "season_change"
    season: int
    time_of_day: int


@dataclass
class Sunrise(WorldEvent):
    """日出（per-chunk，用 chunk 纬度算昼夜切换）。

    daylight_hours 为当日天文日照时长（小时/天），供下游种植/生理系统使用。
    """

    event_type: ClassVar[str] = "sunrise"
    time_of_day: int
    daylight_hours: float


@dataclass
class Sunset(WorldEvent):
    """日落（per-chunk）。daylight_hours 为当日天文日照时长（小时/天）。"""

    event_type: ClassVar[str] = "sunset"
    time_of_day: int
    daylight_hours: float


# ── 特征核事件（寒潮/热浪/暴风雨）────────────────────────────────
# 事件类由 FeatureConfig 注册表指定（start_event_cls / stop_event_cls），
# 新增特征类型 = 注册表加一行 + 指定事件类（或复用既有字段结构）。


@dataclass
class TemperatureOffsetStart(WorldEvent):
    """温度偏移型修改器 start 事件的字段结构（寒潮/热浪共用，不直接发布）。"""

    event_type: ClassVar[str] = ""
    temperature_offset: float
    time_of_day: int


@dataclass
class ColdSnapStart(TemperatureOffsetStart):
    event_type: ClassVar[str] = "cold_snap_start"


@dataclass
class HeatWaveStart(TemperatureOffsetStart):
    event_type: ClassVar[str] = "heat_wave_start"


@dataclass
class StormStart(WorldEvent):
    event_type: ClassVar[str] = "storm_start"
    wind_multiplier: float
    rain_multiplier: float
    time_of_day: int


@dataclass
class ModifierStop(WorldEvent):
    """修改器 stop 事件的字段结构（共用，不直接发布）。"""

    event_type: ClassVar[str] = ""
    time_of_day: int


@dataclass
class ColdSnapStop(ModifierStop):
    event_type: ClassVar[str] = "cold_snap_stop"


@dataclass
class HeatWaveStop(ModifierStop):
    event_type: ClassVar[str] = "heat_wave_stop"


@dataclass
class StormStop(ModifierStop):
    event_type: ClassVar[str] = "storm_stop"
