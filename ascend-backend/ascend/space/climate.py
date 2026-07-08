"""气候系统 — 8 档气候分类、气象参数结构和物理推导。

生成因果链：
  海拔（第一性）→ 温度（气温直减率）→ 气候档位 → 群系
  纬度噪声 → 海平面温度基线

设计要点：
  - 气候档位是**纯静态判定**（年均温 + 年降雨 + 海拔），不依赖季节模块。
  - 气候是季节系统的**输入**（ClimateTemplate 携带 seasonality 字段，
    供 WeatherEngine 选择湿度季节曲线形状 — 余弦 vs 季风阶梯）。
  - 连续气候属性（温度/降雨/海拔）按 100m 场存储，chunk/tile 级双线性插值，
    避免离散档位在 chunk 边界跳变。ClimateZone 仅作派生标签（UI 显示 + 群系映射中间量）。

所有函数为纯函数，无内部状态，天然线程安全。
"""

from dataclasses import dataclass
from enum import IntEnum


class ClimateZone(IntEnum):
    """8 档气候类型 — 由年均温、年降雨量、海拔纯静态判定。

    判定顺序（见 classify）：
      海拔 ≥2000 → ALPINE
      温度 <-5   → POLAR_TUNDRA
      降雨 <200  → DESERT
      降雨 <600 且温度 >5 → STEPPE
      温度 ≥20   → EQUATORIAL_RAINFOREST / TROPICAL_SAVANNA（按降雨）
      温度 ≥5    → TEMPERATE_FOREST
      否则（-5≤T<5）→ SUBARCTIC_TAIGA / POLAR_TUNDRA（按降雨）

    Attributes:
        label: 中文名称。
    """

    EQUATORIAL_RAINFOREST = (0, "热带雨林")
    TROPICAL_SAVANNA = (1, "热带草原")
    DESERT = (2, "沙漠")
    STEPPE = (3, "草原")
    TEMPERATE_FOREST = (4, "温带森林")
    SUBARCTIC_TAIGA = (5, "亚寒带针叶林")
    POLAR_TUNDRA = (6, "极地苔原")
    ALPINE = (7, "高山")

    def __new__(cls, value: int, label: str) -> "ClimateZone":
        """重写 __new__ 以支持双参数 (value, label)。"""
        obj = int.__new__(cls, value)
        obj._value_ = value
        obj.label = label
        return obj

    def __repr__(self) -> str:
        return f"ClimateZone.{self.name}"


# ── 季节性模式（预留，供未来季节系统使用，当前仅存储不算）─────────


class SeasonalityMode(IntEnum):
    """季节性模式 — 气候档位的季节特征标签。

    作为 ClimateTemplate 的元数据存储，供 WeatherEngine 选择湿度季节曲线
    形状（标准余弦 vs 季风阶梯化）。
    """

    NONE = 0          # 无明显季节（赤道常年）
    MONSOON = 1       # 旱雨两季（热带草原）
    FOUR_SEASON = 2   # 四季分明（温带）
    POLAR = 3         # 冬长夏短或无夏（亚寒带/极地）
    ALPINE = 4        # 高山季节（随海拔剧变）


# ── 物理常量 ──────────────────────────────────────────────

# 气温直减率: 海拔每升高 1000m 温度下降的度数
# 注: 游戏性放大值（国际标准大气为 6.5，此处 9.0 有意放大温差以增强气候多样性）
LAPSE_RATE: float = 9.0  # °C / 1000m

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


@dataclass(slots=True)
class ClimateTemplate:
    """气候档位模板 — 定义该档位内的生成参数和季节指导元数据。

    Attributes:
        climate: 对应的 ClimateZone。
        sunshine_range: 日照时长区间 (小时/天)。
        humidity_range: 相对湿度区间 (%)。
        wind_speed_range: 风速区间 (m/s)。
        seasonality: 季节性模式，供 WeatherEngine 选择湿度季节曲线形状。
        display_color: UI 显示色（hex），在此统一定义供渲染层引用。
    """

    climate: ClimateZone
    sunshine_range: tuple[float, float]
    humidity_range: tuple[float, float]
    wind_speed_range: tuple[float, float]
    seasonality: SeasonalityMode = SeasonalityMode.NONE
    display_color: str = "#888888"


# ── 8 档气候模板注册表 ──────────────────────────────────────

_CLIMATE_TEMPLATES: dict[ClimateZone, ClimateTemplate] = {
    ClimateZone.EQUATORIAL_RAINFOREST: ClimateTemplate(
        climate=ClimateZone.EQUATORIAL_RAINFOREST,
        sunshine_range=(10.0, 14.0),
        humidity_range=(75.0, 95.0),
        wind_speed_range=(0.0, 6.0),
        seasonality=SeasonalityMode.NONE,
        display_color="#1a6b3a",
    ),
    ClimateZone.TROPICAL_SAVANNA: ClimateTemplate(
        climate=ClimateZone.TROPICAL_SAVANNA,
        sunshine_range=(9.0, 13.0),
        humidity_range=(40.0, 75.0),
        wind_speed_range=(1.0, 8.0),
        seasonality=SeasonalityMode.MONSOON,
        display_color="#c4a43e",
    ),
    ClimateZone.DESERT: ClimateTemplate(
        climate=ClimateZone.DESERT,
        sunshine_range=(10.0, 14.0),
        humidity_range=(5.0, 30.0),
        wind_speed_range=(2.0, 15.0),
        seasonality=SeasonalityMode.NONE,
        display_color="#e6c878",
    ),
    ClimateZone.STEPPE: ClimateTemplate(
        climate=ClimateZone.STEPPE,
        sunshine_range=(9.0, 13.0),
        humidity_range=(20.0, 50.0),
        wind_speed_range=(2.0, 12.0),
        seasonality=SeasonalityMode.FOUR_SEASON,
        display_color="#b8a060",
    ),
    ClimateZone.TEMPERATE_FOREST: ClimateTemplate(
        climate=ClimateZone.TEMPERATE_FOREST,
        sunshine_range=(8.0, 16.0),
        humidity_range=(45.0, 80.0),
        wind_speed_range=(0.0, 12.0),
        seasonality=SeasonalityMode.FOUR_SEASON,
        display_color="#4a7c3f",
    ),
    ClimateZone.SUBARCTIC_TAIGA: ClimateTemplate(
        climate=ClimateZone.SUBARCTIC_TAIGA,
        sunshine_range=(4.0, 14.0),
        humidity_range=(40.0, 75.0),
        wind_speed_range=(2.0, 15.0),
        seasonality=SeasonalityMode.POLAR,
        display_color="#3a6a8a",
    ),
    ClimateZone.POLAR_TUNDRA: ClimateTemplate(
        climate=ClimateZone.POLAR_TUNDRA,
        sunshine_range=(2.0, 12.0),
        humidity_range=(30.0, 70.0),
        wind_speed_range=(2.0, 20.0),
        seasonality=SeasonalityMode.POLAR,
        display_color="#d8d8e8",
    ),
    ClimateZone.ALPINE: ClimateTemplate(
        climate=ClimateZone.ALPINE,
        sunshine_range=(6.0, 14.0),
        humidity_range=(30.0, 70.0),
        wind_speed_range=(3.0, 25.0),
        seasonality=SeasonalityMode.ALPINE,
        display_color="#b0b0c0",
    ),
}


def get_climate_template(climate: ClimateZone) -> ClimateTemplate:
    """获取气候档位模板。

    Args:
        climate: 气候档位。

    Returns:
        对应的 ClimateTemplate。若未注册则返回温带森林模板作为兜底。
    """
    return _CLIMATE_TEMPLATES.get(
        climate,
        _CLIMATE_TEMPLATES[ClimateZone.TEMPERATE_FOREST],
    )


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


# ── 气候档位判定阈值（游戏设计常量，非物理精确值）──────────────

_ALPINE_ALTITUDE: float = 2000.0   # 高山海拔阈值 (m)
_POLAR_TEMP: float = -5.0          # 极地温度阈值 (°C)
_DESERT_RAINFALL: float = 200.0    # 沙漠降雨阈值 (mm)
_STEPPE_RAINFALL: float = 600.0   # 草原降雨阈值 (mm)
_STEPPE_MIN_TEMP: float = 5.0     # 草原温度下限 (°C)
_TROPICAL_TEMP: float = 20.0      # 热带温度阈值 (°C)
_TEMPERATE_TEMP: float = 5.0      # 温带温度下限 (°C)
_RAINFOREST_RAINFALL: float = 1500.0  # 雨林降雨阈值 (mm)
_TAIGA_RAINFALL: float = 400.0    # 针叶林降雨阈值 (mm)


def classify(
    mean_temp: float,
    annual_rainfall: float,
    altitude: float,
) -> ClimateZone:
    """由年均温、年降雨量、海拔纯静态判定气候档位。

    判定顺序（前者优先）：
      1. 海拔 ≥ 2000m → ALPINE（覆盖纬度气候，高山独立）
      2. 温度 < -5°C → POLAR_TUNDRA（极地，不论降雨）
      3. 降雨 < 200mm → DESERT（极端干旱，不论温暖）
      4. 降雨 < 600mm 且温度 > 5°C → STEPPE（半干旱草原）
      5. 温度 ≥ 20°C → EQUATORIAL_RAINFOREST（R≥1500）/ TROPICAL_SAVANNA
      6. 温度 ≥ 5°C → TEMPERATE_FOREST
      7. -5≤T<5°C → SUBARCTIC_TAIGA（R≥400）/ POLAR_TUNDRA（冷干合并）

    纯函数，线程安全。

    Args:
        mean_temp: 年均温度 (°C)。
        annual_rainfall: 年降雨量 (mm)。
        altitude: 海拔 (m)。

    Returns:
        对应的 ClimateZone。
    """
    # 1. 高山（海拔优先，覆盖纬度气候）
    if altitude >= _ALPINE_ALTITUDE:
        return ClimateZone.ALPINE

    # 2. 极地（严寒优先于干旱判定）
    if mean_temp < _POLAR_TEMP:
        return ClimateZone.POLAR_TUNDRA

    # 3. 沙漠（极端干旱）
    if annual_rainfall < _DESERT_RAINFALL:
        return ClimateZone.DESERT

    # 4. 草原（半干旱 + 温暖）
    if annual_rainfall < _STEPPE_RAINFALL and mean_temp > _STEPPE_MIN_TEMP:
        return ClimateZone.STEPPE

    # 5. 热带（高温）— 到此处 R ≥ 600
    if mean_temp >= _TROPICAL_TEMP:
        if annual_rainfall >= _RAINFOREST_RAINFALL:
            return ClimateZone.EQUATORIAL_RAINFOREST
        return ClimateZone.TROPICAL_SAVANNA

    # 6. 温带
    if mean_temp >= _TEMPERATE_TEMP:
        return ClimateZone.TEMPERATE_FOREST

    # 7. 亚寒带 / 极地苔原（-5 ≤ T < 5）
    if annual_rainfall >= _TAIGA_RAINFALL:
        return ClimateZone.SUBARCTIC_TAIGA
    return ClimateZone.POLAR_TUNDRA


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
    从气候档位模板的区间表中用噪声插值。

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
    tmpl = get_climate_template(climate)
    bounds = _PARAM_BOUNDS

    def _derive(lo_hi: tuple[float, float], noise: float, bound_key: str) -> float:
        lo, hi = lo_hi
        blo, bhi = bounds[bound_key]
        value = lo + (noise + 1.0) * 0.5 * (hi - lo)
        return max(blo, min(bhi, value))

    return WeatherParams(
        temperature=temperature,
        rainfall=rainfall,
        sunshine=_derive(tmpl.sunshine_range, sunshine_noise, "sunshine"),
        altitude=altitude,
        humidity=_derive(tmpl.humidity_range, humidity_noise, "humidity"),
        wind_speed=_derive(tmpl.wind_speed_range, wind_noise, "wind_speed"),
    )


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
