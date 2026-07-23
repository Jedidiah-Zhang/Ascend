"""天气系统 — 结合时间与空间信息动态生成 chunk 级游戏内天气。

解析算架构（无快照，每刻连续）：
  - 温度 = baseline + 季节偏移 + 昼夜偏移 + 大气扰动
  - 湿度/风速 = baseline + 大气扰动
  - 降雨 = 事件调度（RainSchedule，从年降雨量推算频率/持续/强度）

事件按感知类别发布（"cold"→"cool"、"dry"→"comfortable" 等）：
  - 感知层事件：temperature_change / humidity_change / wind_change / sunshine_change
    仅在类别跨越边界时触发，附带精确 numeric 值。
  - 离散事件：precipitation_start/stop / season_change / sunrise/sunset / extreme weather
  - API 查询：get_weather(cx, cy, time) 获取任意位置当前/过去时刻的精确值

天气状态存于 chunk 级（复用 ChunkData.annual_baseline 为基线），
tile 级天气通过双线性插值运行时计算，保证跨 chunk 平滑。

用法:
    from ascend.weather import WeatherEngine, Season

    engine = WeatherEngine(clock, seed=42)
    engine.register_chunk(cx, cy, baseline, climate, sea_level_temp)
    # 事件：感知通知（AI 决策、行为变化）
    # API 查询：精确值（UI 面板、生态模拟）
    wp = engine.get_weather(cx, cy)
    engine.shutdown()
"""

from .atmosphere import AtmosphereField
from .events import register_weather_schemas
from .weather_engine import (
    WeatherEngine, classify_temperature, classify_humidity,
    classify_wind, classify_sunshine, classify_sunlight_intensity,
)
from .weather_modifier import ModifierEvent, ModifierSchedule, ModifierConfig, WEATHER_MODIFIERS
from .rain_events import RainEvent, RainSchedule, intensity_at, mean_interval_hours
from .season import Season
from .weather_field import WeatherField

__all__ = [
    "WeatherEngine",
    "WeatherField",
    "Season",
    "AtmosphereField",
    "register_weather_schemas",
    "classify_temperature",
    "classify_humidity",
    "classify_wind",
    "classify_sunshine",
    "classify_sunlight_intensity",
    "RainEvent",
    "RainSchedule",
    "intensity_at",
    "mean_interval_hours",
]
