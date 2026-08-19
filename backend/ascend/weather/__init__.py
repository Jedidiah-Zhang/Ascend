"""天气系统 — 统一天气场（特征 + 纹理双分量）+ 解析算天气 + 感知层事件。

解析算架构（无快照，每刻连续）：
  - 温度 = baseline + 季节偏移 + 昼夜偏移 + 统一天气场扰动（特征 + 纹理）
  - 湿度/风速 = baseline + 统一天气场扰动
  - 降雨 = 场合成值 + 气候带校准阈值判定（区域级事件，非 per-chunk 调度）
  - 极端天气 = 场特征核（寒潮/热浪/风暴/锋面，区域级事件）

事件按等级发布（整数 tier + prev_tier，边界见 config `*_TIER_BOUNDARIES`）：
  - 感知层事件：temperature_change / humidity_change / wind_change / sunshine_change
    仅在等级跨越边界时触发，附带精确 numeric 值与 prev_tier/tier。
  - 离散事件：precipitation_start/stop（区域级）/ season_change / sunrise/sunset
    / 特征核 start/stop（区域级）
  - API 查询：get_weather(cx, cy, time) 获取任意位置当前/过去时刻的精确值

场为解析量（seed + 时间可完全重算），不存状态——存档只存 manifest.seed
+ 时钟，读档可复现。

用法:
    from ascend.weather import WeatherEngine, Season

    engine = WeatherEngine(clock, seed=42)
    engine.register_chunk(cx, cy, baseline, climate, sea_level_temp)
    # 事件：感知通知（AI 决策、行为变化）
    # API 查询：精确值（UI 面板、生态模拟）
    wp = engine.get_weather(cx, cy)
    engine.shutdown()
"""

from .atmosphere import TextureField
from .features import (
    FeatureField, FeatureCore, FeatureConfig, ClimateProxy,
    FEATURE_TYPES, T_FRONT, T_STORM, T_COLD_SNAP, T_HEAT_WAVE,
)
from .field import (
    UnifiedWeatherField,
    CH_PRECIPITATION, CH_TEMPERATURE, CH_HUMIDITY, CH_WIND,
    calibrate_precip, precip_threshold,
)
from .events import (
    TemperatureChange, HumidityChange, WindChange, SunshineChange,
    PrecipitationStart, PrecipitationStop, SeasonChange, Sunrise, Sunset,
    ColdSnapStart, ColdSnapStop, HeatWaveStart, HeatWaveStop,
    StormStart, StormStop,
)
from .region_tracker import RegionTracker, RegionEvent
from .weather_engine import (
    WeatherEngine, classify_temperature, classify_humidity,
    classify_wind, classify_sunshine, classify_sunlight_intensity,
)
from .season import Season
from .weather_field import WeatherField

__all__ = [
    "WeatherEngine",
    "WeatherField",
    "Season",
    "TextureField",
    "UnifiedWeatherField",
    "RegionTracker",
    "RegionEvent",
    "FeatureField",
    "FeatureCore",
    "FeatureConfig",
    "ClimateProxy",
    "FEATURE_TYPES",
    "T_FRONT",
    "T_STORM",
    "T_COLD_SNAP",
    "T_HEAT_WAVE",
    "CH_PRECIPITATION",
    "CH_TEMPERATURE",
    "CH_HUMIDITY",
    "CH_WIND",
    "TemperatureChange",
    "HumidityChange",
    "WindChange",
    "SunshineChange",
    "PrecipitationStart",
    "PrecipitationStop",
    "SeasonChange",
    "Sunrise",
    "Sunset",
    "ColdSnapStart",
    "ColdSnapStop",
    "HeatWaveStart",
    "HeatWaveStop",
    "StormStart",
    "StormStop",
    "classify_temperature",
    "classify_humidity",
    "classify_wind",
    "classify_sunshine",
    "classify_sunlight_intensity",
    "calibrate_precip",
    "precip_threshold",
]
