"""气候系统 — 气候档位枚举、气象参数结构和噪声→参数的映射。

所有函数为纯函数，无内部状态，天然线程安全。
"""

from dataclasses import dataclass
from enum import IntEnum


class ClimateZone(IntEnum):
    """气候档位 — 用于群系分类的离散标签。

    每个分块分配一个气候类型，由噪声函数在分块坐标处采样后映射得到。

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


# ── 气候档位的参数区间 ──────────────────────────────────

# 每个气候类型定义各参数的年均基线范围 [min, max]
_CLIMATE_RANGES: dict[ClimateZone, dict[str, tuple[float, float]]] = {
    ClimateZone.TROPICAL: {
        "temperature": (20.0, 30.0),   # °C
        "rainfall":    (1500.0, 3500.0),  # mm/年
        "sunshine":    (10.0, 14.0),      # 小时/天
        "altitude":    (0.0, 800.0),      # m
        "humidity":    (60.0, 95.0),      # %
        "wind_speed":  (0.0, 8.0),        # m/s
    },
    ClimateZone.TEMPERATE: {
        "temperature": (5.0, 20.0),
        "rainfall":    (600.0, 1800.0),
        "sunshine":    (8.0, 16.0),
        "altitude":    (0.0, 2000.0),
        "humidity":    (45.0, 80.0),
        "wind_speed":  (0.0, 12.0),
    },
    ClimateZone.COLD: {
        "temperature": (-10.0, 5.0),
        "rainfall":    (200.0, 800.0),
        "sunshine":    (2.0, 12.0),
        "altitude":    (0.0, 3500.0),
        "humidity":    (30.0, 70.0),
        "wind_speed":  (2.0, 20.0),
    },
    ClimateZone.ARID: {
        "temperature": (10.0, 30.0),
        "rainfall":    (50.0, 400.0),
        "sunshine":    (10.0, 14.0),
        "altitude":    (0.0, 1500.0),
        "humidity":    (10.0, 40.0),
        "wind_speed":  (2.0, 15.0),
    },
}

# 各参数通用绝对边界（用于 clamp）
_PARAM_BOUNDS: dict[str, tuple[float, float]] = {
    "temperature": (-30.0, 50.0),
    "rainfall":    (0.0, 5000.0),
    "sunshine":    (0.0, 24.0),
    "altitude":    (-100.0, 5000.0),
    "humidity":    (0.0, 100.0),
    "wind_speed":  (0.0, 50.0),
}


@dataclass(slots=True)
class WeatherParams:
    """气象六参数 — 某一时刻的具体天气数值。

    用于生理需求计算、作物生长判定、基因适应区间匹配。

    Attributes:
        temperature: 温度 (°C)。
        rainfall: 降雨量 (mm/天)。
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


# ── 噪声→参数映射（纯函数）──────────────────────────────

def climate_zone_from_noise(temperature_noise: float, rainfall_noise: float) -> ClimateZone:
    """由温度和降雨量噪声值映射到气候档位。

    噪声值范围约 [-1, 1]，映射规则：
    - 高温 + 高降雨 → 热带
    - 低温 → 寒带
    - 高温 + 低降雨 → 干旱带
    - 中等 → 温带

    Args:
        temperature_noise: 温度相关噪声值 [-1, 1]。
        rainfall_noise: 降雨量相关噪声值 [-1, 1]。

    Returns:
        对应的 ClimateZone。
    """
    temp = temperature_noise  # [-1, 1]，-1=冷，+1=热
    rain = rainfall_noise     # [-1, 1]，-1=干，+1=湿

    if temp < -0.3:
        return ClimateZone.COLD
    if temp > 0.3 and rain < -0.2:
        return ClimateZone.ARID
    if temp > 0.3 and rain > 0.0:
        return ClimateZone.TROPICAL
    return ClimateZone.TEMPERATE


def annual_baseline(climate: ClimateZone, noise_values: dict[str, float]) -> WeatherParams:
    """根据气候档位和噪声值生成年均基线气象参数。

    每个参数的噪声值在气候档位定义的 [min, max] 区间内插值。

    Args:
        climate: 气候档位。
        noise_values: 每个参数对应的噪声值 {-1..1}，键为参数名。
                     噪声值 -1 → 区间最小值，+1 → 区间最大值。

    Returns:
        年均基线 WeatherParams。
    """
    ranges = _CLIMATE_RANGES[climate]
    bounds = _PARAM_BOUNDS

    def _map(param: str, noise: float) -> float:
        lo, hi = ranges[param]
        blo, bhi = bounds[param]
        # 噪声 [-1, 1] → 线性映射到 [lo, hi]
        value = lo + (noise + 1.0) * 0.5 * (hi - lo)
        # clamp 到绝对边界
        return max(blo, min(bhi, value))

    return WeatherParams(
        temperature=_map("temperature", noise_values.get("temperature", 0.0)),
        rainfall=_map("rainfall", noise_values.get("rainfall", 0.0)),
        sunshine=_map("sunshine", noise_values.get("sunshine", 0.0)),
        altitude=_map("altitude", noise_values.get("altitude", 0.0)),
        humidity=_map("humidity", noise_values.get("humidity", 0.0)),
        wind_speed=_map("wind_speed", noise_values.get("wind_speed", 0.0)),
    )
