"""天气系统 — 结合时间与空间信息动态生成 chunk 级游戏内天气。

解析算架构（无快照，每刻连续）：
  - 温度 = baseline + 季节偏移 + 昼夜偏移 + 大气扰动
  - 湿度/风速 = baseline + 大气扰动
  - 降雨 = 事件调度（RainSchedule，从年降雨量推算频率/持续/强度）

事件按参数拆分（什么变了发什么）：temperature_change / humidity_change /
wind_change / precipitation_start / precipitation_stop，变化超阈值才发。

天气状态存于 chunk 级（复用 ChunkData.annual_baseline 为基线），
tile 级天气通过双线性插值运行时计算，保证跨 chunk 平滑。

用法:
    from ascend.weather import WeatherEngine, Season

    engine = WeatherEngine(clock, seed=42)
    engine.register_chunk(cx, cy, baseline, climate)
    engine.shutdown()
    # 天气数据通过订阅 temperature_change 等事件获取，不开放查询
"""

from .atmosphere import AtmosphereField
from .events import register_weather_schemas
from .weather_modifier import ModifierEvent, ModifierSchedule, ModifierConfig, WEATHER_MODIFIERS
from .rain_events import RainEvent, RainSchedule, intensity_at, mean_interval_hours
from .season import Season
from .weather_engine import WeatherEngine
from .weather_field import WeatherField

__all__ = [
    "WeatherEngine",
    "WeatherField",
    "Season",
    "AtmosphereField",
    "register_weather_schemas",
    "RainEvent",
    "RainSchedule",
    "intensity_at",
    "mean_interval_hours",
]
