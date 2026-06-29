"""气候系统 — 气候档位枚举、气象参数结构和物理推导。

生成因果链：
  海拔（第一性）→ 温度（气温直减率）→ 气候档位 → 群系
  纬度噪声 → 海平面温度基线

所有函数为纯函数，无内部状态，天然线程安全。
"""

from dataclasses import dataclass
from enum import IntEnum


class ClimateZone(IntEnum):
    """气候档位 — 由温度+降雨量确定。

    Attributes:
        label: 中文名称。
    """

    TROPICAL = (0, "热带")
    TEMPERATE = (1, "温带")
    COLD = (2, "寒带")
    ARID = (3, "干旱带")

    def __new__(cls, value: int, label: str) -> "ClimateZone":
        """重写 __new__ 以支持双参数 (value, label)。"""
        obj = int.__new__(cls, value)
        obj._value_ = value
        obj.label = label
        return obj

    def __repr__(self) -> str:
        return f"ClimateZone.{self.name}"


# ── 物理常量 ──────────────────────────────────────────────

# 气温直减率: 海拔每升高 1000m 温度下降的度数
LAPSE_RATE: float = 6.5  # °C / 1000m

# 海平面温度范围（由纬度噪声映射）
_SEA_LEVEL_TEMP_MIN: float = -5.0   # 极地
_SEA_LEVEL_TEMP_MAX: float = 35.0   # 赤道

# 降雨量范围（由降雨噪声映射）
_RAINFALL_MIN: float = 50.0    # mm/年
_RAINFALL_MAX: float = 3500.0  # mm/年

# 各参数通用绝对边界
_PARAM_BOUNDS: dict[str, tuple[float, float]] = {
    "temperature": (-30.0, 50.0),
    "rainfall":    (0.0, 5000.0),
    "sunshine":    (0.0, 24.0),
    "altitude":    (-500.0, 5000.0),
    "humidity":    (0.0, 100.0),
    "wind_speed":  (0.0, 50.0),
}

# 各气候档位的派生参数区间（温度/降雨由物理推导，其余从此表映射）
_CLIMATE_PARAM_RANGES: dict[ClimateZone, dict[str, tuple[float, float]]] = {
    ClimateZone.TROPICAL: {
        "sunshine":   (10.0, 14.0),
        "humidity":   (60.0, 95.0),
        "wind_speed": (0.0, 8.0),
    },
    ClimateZone.TEMPERATE: {
        "sunshine":   (8.0, 16.0),
        "humidity":   (45.0, 80.0),
        "wind_speed": (0.0, 12.0),
    },
    ClimateZone.COLD: {
        "sunshine":   (2.0, 12.0),
        "humidity":   (30.0, 70.0),
        "wind_speed": (2.0, 20.0),
    },
    ClimateZone.ARID: {
        "sunshine":   (10.0, 14.0),
        "humidity":   (10.0, 40.0),
        "wind_speed": (2.0, 15.0),
    },
}


@dataclass(slots=True)
class WeatherParams:
    """气象六参数 — 某一时刻的具体天气数值。

    用于生理需求计算、作物生长判定、基因适应区间匹配。

    Attributes:
        temperature: 温度 (°C)。
        rainfall: 降雨量 (mm/年)。
        sunshine: 日照时长 (小时/天)。
        altitude: 海拔 (m)。
        humidity: 相对湿度 (%)。
        wind_speed: 风速 (m/s)。
    """
    temperature: float
    rainfall: float
    sunshine: float
    altitude: float
    humidity: float
    wind_speed: float

    def __repr__(self) -> str:
        return (
            f"WeatherParams(T={self.temperature:.1f}°C, "
            f"rain={self.rainfall:.1f}mm, "
            f"sun={self.sunshine:.1f}h, "
            f"alt={self.altitude:.0f}m, "
            f"RH={self.humidity:.0f}%, "
            f"wind={self.wind_speed:.1f}m/s)"
        )


# ── 物理推导（纯函数）────────────────────────────────────

def sea_level_temperature(latitude_noise: float) -> float:
    """纬度噪声 → 海平面年均温度。

    纬度噪声 [-1, +1]:
      -1 → 极地 (~ -5°C)
       0 → 中纬度 (~ 15°C)
      +1 → 赤道 (~ 35°C)

    Args:
        latitude_noise: 纬度噪声值 [-1, 1]。

    Returns:
        海平面年均温度 (°C)。
    """
    t = _SEA_LEVEL_TEMP_MIN + (latitude_noise + 1.0) * 0.5 * (
        _SEA_LEVEL_TEMP_MAX - _SEA_LEVEL_TEMP_MIN
    )
    return clamp(t, _PARAM_BOUNDS["temperature"][0], _PARAM_BOUNDS["temperature"][1])


def apply_lapse_rate(sea_level_temp: float, altitude: float) -> float:
    """气温直减率：海拔每升高 1000m 温度下降 LAPSE_RATE °C。

    Args:
        sea_level_temp: 海平面温度 (°C)。
        altitude: 海拔 (m)。

    Returns:
        实际温度 (°C)。
    """
    t = sea_level_temp - altitude * LAPSE_RATE / 1000.0
    return clamp(t, _PARAM_BOUNDS["temperature"][0], _PARAM_BOUNDS["temperature"][1])


def rainfall_from_noise(rainfall_noise: float) -> float:
    """降雨噪声 → 年降雨量 (mm/年)。

    Args:
        rainfall_noise: 降雨噪声 [-1, 1]，-1=极干，+1=极湿。

    Returns:
        年降雨量 (mm)。
    """
    r = _RAINFALL_MIN + (rainfall_noise + 1.0) * 0.5 * (_RAINFALL_MAX - _RAINFALL_MIN)
    return clamp(r, _PARAM_BOUNDS["rainfall"][0], _PARAM_BOUNDS["rainfall"][1])


def climate_zone_from_values(temperature: float, rainfall: float) -> ClimateZone:
    """由实际温度和年降雨量确定气候档位。

    判定规则（与 docs/世界框架/季节气候/设计.md 一致）：
      - 年均温 20-30°C + 年降雨高  → 热带
      - 年均温 5-20°C              → 温带
      - 年均温 <5°C                → 寒带
      - 年降雨极低                 → 干旱带（覆盖温度条件）

    Args:
        temperature: 年均温度 (°C)。
        rainfall: 年降雨量 (mm)。

    Returns:
        对应的 ClimateZone。
    """
    # 极低降雨优先判定为干旱带
    if rainfall < 400.0 and temperature > 5.0:
        return ClimateZone.ARID

    if temperature < 5.0:
        return ClimateZone.COLD
    if temperature >= 20.0 and rainfall >= 1000.0:
        return ClimateZone.TROPICAL
    return ClimateZone.TEMPERATE


def annual_baseline(
    altitude: float,
    sea_level_temp: float,
    rainfall: float,
    climate: ClimateZone,
    *,
    sunshine_noise: float = 0.0,
    humidity_noise: float = 0.0,
    wind_noise: float = 0.0,
) -> WeatherParams:
    """组装完整的年均基线气象参数。

    温度和降雨由物理推导得出，日照/湿度/风速
    从气候档位参数表中用噪声插值。

    Args:
        altitude: 海拔 (m)。
        sea_level_temp: 海平面温度 (°C)。
        rainfall: 年降雨量 (mm)。
        climate: 气候档位。
        sunshine_noise: 日照噪声 [-1, 1]。
        humidity_noise: 湿度噪声 [-1, 1]。
        wind_noise: 风速噪声 [-1, 1]。

    Returns:
        年均基线 WeatherParams。
    """
    temperature = apply_lapse_rate(sea_level_temp, altitude)
    ranges = _CLIMATE_PARAM_RANGES[climate]
    bounds = _PARAM_BOUNDS

    def _derive(param: str, noise: float) -> float:
        lo, hi = ranges.get(param, (0, 1))
        blo, bhi = bounds[param]
        value = lo + (noise + 1.0) * 0.5 * (hi - lo)
        return max(blo, min(bhi, value))

    return WeatherParams(
        temperature=temperature,
        rainfall=rainfall,
        sunshine=_derive("sunshine", sunshine_noise),
        altitude=altitude,
        humidity=_derive("humidity", humidity_noise),
        wind_speed=_derive("wind_speed", wind_noise),
    )


# ── 兼容旧接口 ────────────────────────────────────────────

def climate_zone_from_noise(temperature_noise: float, rainfall_noise: float) -> ClimateZone:
    """由温度/降雨噪声值映射到气候档位（兼容旧 API）。

    内部将噪声转为实际值再调用 climate_zone_from_values。

    Args:
        temperature_noise: 温度噪声 [-1, 1]。
        rainfall_noise: 降雨噪声 [-1, 1]。

    Returns:
        对应的 ClimateZone。
    """
    temp = sea_level_temperature(temperature_noise)
    rain = rainfall_from_noise(rainfall_noise)
    return climate_zone_from_values(temp, rain)


def clamp(value: float, lo: float, hi: float) -> float:
    """将值钳制在 [lo, hi] 区间内。

    Args:
        value: 输入值。
        lo: 下限。
        hi: 上限。

    Returns:
        钳制后的值。
    """
    return max(lo, min(hi, value))
