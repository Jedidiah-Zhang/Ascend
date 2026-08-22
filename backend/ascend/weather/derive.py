"""感知与基线派生 — 天气分级 / 降水类型判定 / 季节振幅与纬度推导。

纯函数集合（无状态、无 IO），从 WeatherEngine 拆出：
  - 分级的唯一实现（事件 tier / 终端显示 / 查询 API 共用同一阈值语义）
  - 降水类型判定（事件侧与查询侧共用的单一实现）
  - 季节振幅 / 纬度连续推导（保证气候带边界无跳变）

阈值单一事实来源 = ascend.config 的 *_TIER_BOUNDARIES 与
SEASONAL_AMP_*/LATITUDE_* 系列常量。
"""

from dataclasses import dataclass

from ascend.config import (HUMIDITY_TIER_BOUNDARIES, LATITUDE_MAX,
                           LATITUDE_MIN, LATITUDE_T_MAX, LATITUDE_T_MIN,
                           SEASONAL_AMP_BOUNDS, SEASONAL_AMP_MAX,
                           SEASONAL_AMP_MIN, SEASONAL_AMP_R_BONUS,
                           SEASONAL_AMP_R_REF, SEASONAL_AMP_T_MAX,
                           SEASONAL_AMP_T_MIN,
                           SUNLIGHT_INTENSITY_TIER_BOUNDARIES,
                           SUNSHINE_TIER_BOUNDARIES, TEMP_TIER_BOUNDARIES,
                           WIND_TIER_BOUNDARIES)
from ascend.space import clamp


@dataclass(frozen=True, slots=True)
class DaySummary:
    """单日解析天气摘要（地形状态结算器采样契约，见 WeatherEngine.get_day_summary）。

    Attributes:
        day: 游戏日（1-based）。
        mean_temp: 采样均温 (°C)。
        rain_mm: 当日雨量合计（mm；采样强度 × 采样间隔时长）。
        snow_mm: 当日雪量合计（mm；采样强度 × 采样间隔时长）。
    """

    day: int
    mean_temp: float
    rain_mm: float
    snow_mm: float


def precip_type_for(temperature: float) -> str:
    """降水类型判定 — 事件侧与查询侧（weather_handler）共用的单一实现。

    冰点阈值 0°C：<=0 为雪、>0 为雨。统一先 round(1) 再判定，
    保证事件广播与 UI 显示文案一致。

    Args:
        temperature: 气温 (°C)。

    Returns:
        "snow" 或 "rain"。
    """
    return "snow" if round(temperature, 1) <= 0 else "rain"


def _classify(value: float, boundaries: tuple[float, ...]) -> int:
    """按阈值返回等级索引（0-based）。

    Args:
        value: 待分类的数值。
        boundaries: 阈值升序元组。

    Returns:
        int，value < boundaries[i] 的最小 i，或在边界外返回 len(boundaries)。
    """
    for i, limit in enumerate(boundaries):
        if value < limit:
            return i
    return len(boundaries)


def classify_temperature(temp: float,
                         boundaries: tuple[float, ...]
                         = TEMP_TIER_BOUNDARIES) -> int:
    """温度 → 等级索引。

    Args:
        temp: 温度 (°C)。
        boundaries: 可选自定义阈值，默认用全局配置。
                    用于不同物种/场景的分级调整。

    Returns:
        int，等级索引（0=最冷，len(boundaries)=最热）。
    """
    return _classify(temp, boundaries)


def classify_humidity(hum: float,
                      boundaries: tuple[float, ...]
                      = HUMIDITY_TIER_BOUNDARIES) -> int:
    """湿度 → 等级索引。

    Args:
        hum: 相对湿度 (%)。
        boundaries: 可选自定义阈值，默认用全局配置。

    Returns:
        int，等级索引（0=最干燥，len(boundaries)=最潮湿）。
    """
    return _classify(hum, boundaries)


def classify_wind(speed: float,
                  boundaries: tuple[float, ...]
                  = WIND_TIER_BOUNDARIES) -> int:
    """风速 → 等级索引。

    Args:
        speed: 风速 (m/s)。
        boundaries: 可选自定义阈值，默认用全局配置。

    Returns:
        int，等级索引（0=无风，len(boundaries)=最大风力）。
    """
    return _classify(speed, boundaries)


def classify_sunshine(sun: float,
                      boundaries: tuple[float, ...]
                      = SUNSHINE_TIER_BOUNDARIES) -> int:
    """日照时长 → 等级索引。

    Args:
        sun: 日照时长 (小时/天)。
        boundaries: 可选自定义阈值，默认用全局配置。

    Returns:
        int，等级索引（0=最短，len(boundaries)=最长）。
    """
    return _classify(sun, boundaries)


def classify_sunlight_intensity(intensity: float,
                                boundaries: tuple[float, ...]
                                = SUNLIGHT_INTENSITY_TIER_BOUNDARIES) -> int:
    """日照强度 (0~1) → 等级索引。

    Args:
        intensity: 归一化日照强度，0=黑夜 1=正午烈日。
        boundaries: 可选自定义阈值，默认用全局配置。

    Returns:
        int，等级索引（0=最暗，len(boundaries)=最亮）。
    """
    return _classify(intensity, boundaries)


def derive_seasonal_amp(temperature: float, rainfall: float) -> float:
    """从年均温 + 年降雨连续推导季节温度振幅 (°C)。

    年均温越低 → 振幅越大（极地 ~28, 赤道 ~2）；
    干旱区（低降雨）大陆性气候 → 振幅偏大（+最多 4°C）；
    高降雨区海洋调节 → 振幅偏小（-最多 2°C）。

    保证空间连续：相邻 chunk 的 baseline 温度/降雨接近 →
    seasonal_amp 接近，无气候带边界跳变。

    Args:
        temperature: 年均温度 (°C)。
        rainfall: 年降雨量 (mm/年)。

    Returns:
        季节温度振幅 (°C)，钳制在 [1, 30]。
    """
    t_ratio = (temperature - SEASONAL_AMP_T_MIN) / (
        SEASONAL_AMP_T_MAX - SEASONAL_AMP_T_MIN
    )
    base_amp = SEASONAL_AMP_MAX - t_ratio * (
        SEASONAL_AMP_MAX - SEASONAL_AMP_MIN
    )
    rain_factor = clamp(
        (SEASONAL_AMP_R_REF - rainfall) / SEASONAL_AMP_R_REF,
        -0.5, 1.0,
    )
    rain_bonus = rain_factor * SEASONAL_AMP_R_BONUS
    return clamp(base_amp + rain_bonus, *SEASONAL_AMP_BOUNDS)


def derive_latitude(sea_level_temp: float) -> float:
    """从海平面温度连续推导纬度 (°)。

    海平面温度是连续场（纬度噪声推导），不受海拔/气候档位离散判定影响，
    保证气候带交界处纬度连续 → 日照季节振幅 + 日出/日落时刻无跳变。

    线性映射：sea_temp=-5（极地）→ lat=80，sea_temp=35（赤道）→ lat=0。

    Args:
        sea_level_temp: 海平面年均温度 (°C)。

    Returns:
        纬度 (°)，范围 [0, 80]。
    """
    t_ratio = (sea_level_temp - LATITUDE_T_MIN) / (
        LATITUDE_T_MAX - LATITUDE_T_MIN
    )
    lat = LATITUDE_MAX - t_ratio * (LATITUDE_MAX - LATITUDE_MIN)
    return clamp(lat, LATITUDE_MIN, LATITUDE_MAX)
