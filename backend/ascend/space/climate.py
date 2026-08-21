"""气候系统 — 8 档气候分类、气象参数结构和物理推导。

设计要点：
  - 气候档位是**纯静态判定**（年均温 + 年降雨 + 海拔），不依赖季节模块。
  - 气候是季节系统的**输入**（ClimateTemplate 携带 seasonality 字段，
    供 WeatherEngine 选择湿度季节曲线形状）。
  - 温雨由大陆 C 模型物理计算并缓存为 chunk 级值，tile 级仅海拔和
    moisture 噪声变化，避免 chunk 边界跳变。

所有函数为纯函数，无内部状态，天然线程安全。
"""

from dataclasses import dataclass
from enum import IntEnum
from typing import Mapping

from ascend.data import load_content, split_ns_id
from ascend.i18n import get_default

_I18N = get_default()


class ClimateZone(IntEnum):
    """8 档气候类型 — 由年均温、年降雨量、海拔纯静态判定。

    判定顺序（见 classify；下列数值为 config 默认值，运行期阈值
    由 config 注入 C 层，以 config 为准）：
      海拔 ≥2000 → ALPINE
      温度 <-5   → POLAR_TUNDRA
      降雨 <200  → DESERT
      降雨 <600 且温度 >5 → STEPPE
      温度 ≥20   → EQUATORIAL_RAINFOREST / TROPICAL_SAVANNA（按降雨）
      温度 ≥5    → TEMPERATE_FOREST
      否则（-5≤T<5）→ SUBARCTIC_TAIGA / POLAR_TUNDRA（按降雨）

    枚举值 = 持久化/协议契约（0..7，不可改，只能追加）；显示名与
    模板数据在 data/climate.json（label_key 经 i18n 惰性解析）。
    """

    EQUATORIAL_RAINFOREST = 0
    TROPICAL_SAVANNA = 1
    DESERT = 2
    STEPPE = 3
    TEMPERATE_FOREST = 4
    SUBARCTIC_TAIGA = 5
    POLAR_TUNDRA = 6
    ALPINE = 7

    def __init__(self, value: int) -> None:
        """数据加载后由 loader 填充 label_key（i18n 键）。"""
        self.label_key: str = ""

    @property
    def label(self) -> str:
        """本地化显示名（label_key 经 i18n 惰性解析，随语言切换）。"""
        return _I18N.t(self.label_key) if self.label_key else self.name

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


# ── 物理常量（单一事实来源 ascend.config，此处按原名引用） ──────
from ascend.config import (
    LAPSE_RATE,
    PARAM_BOUNDS as _PARAM_BOUNDS,
)


@dataclass(slots=True)
class ClimateTemplate:
    """气候档位模板 — 定义该档位内的生成参数和季节指导元数据。

    Attributes:
        climate: 对应的 ClimateZone。
        humidity_range: 相对湿度区间 (%)。
        wind_speed_range: 风速区间 (m/s)。
        seasonality: 季节性模式，供 WeatherEngine 选择湿度季节曲线形状。
        display_color: UI 显示色（hex），在此统一定义供渲染层引用。
    """

    climate: ClimateZone
    humidity_range: tuple[float, float]
    wind_speed_range: tuple[float, float]
    seasonality: SeasonalityMode = SeasonalityMode.NONE
    display_color: str = "#888888"


# ── 8 档气候模板注册表（数据驱动，data/climate.json）──────────

_SEASONALITY_BY_NAME: Mapping[str, SeasonalityMode] = {
    m.name.lower(): m for m in SeasonalityMode
}


def _parse_climate_template(ns_id: str, raw: Mapping) -> tuple[ClimateTemplate, str]:
    """单行 JSON → (ClimateTemplate, label_key)（不就地修改枚举成员）。

    校验枚举一致性、seasonality 合法；label_key 由调用方在全部校验
    通过后统一填充，避免解析中途失败留下半修改的枚举状态。
    """
    zone = ClimateZone[split_ns_id(ns_id)[1].upper()]
    if int(raw["value"]) != zone.value:
        raise ValueError(
            f"{ns_id}: 数据 value {raw['value']} 与枚举 {zone.value} 不一致"
        )
    if "label_key" not in raw:
        raise ValueError(f"{ns_id}: 缺少必需字段 label_key")
    label_key = str(raw["label_key"])
    seasonality = _SEASONALITY_BY_NAME.get(str(raw.get("seasonality", "none")))
    if seasonality is None:
        raise ValueError(f"{ns_id}: 非法 seasonality {raw.get('seasonality')!r}")
    tmpl = ClimateTemplate(
        climate=zone,
        humidity_range=tuple(float(x) for x in raw["humidity_range"]),
        wind_speed_range=tuple(float(x) for x in raw["wind_speed_range"]),
        seasonality=seasonality,
        display_color=str(raw.get("display_color", "#888888")),
    )
    return tmpl, label_key


def _build_climate_templates(doc: Mapping) -> dict[ClimateZone, ClimateTemplate]:
    """data/climate.json → 注册表。先全量校验，通过后统一填充 label_key。"""
    raw_map = doc.get("climate")
    if not isinstance(raw_map, Mapping) or not raw_map:
        raise ValueError("data/climate.json: 缺少 climate 注册表")
    parsed: list[tuple[ClimateTemplate, str]] = [
        _parse_climate_template(ns_id, raw) for ns_id, raw in raw_map.items()
    ]
    templates = {tmpl.climate: tmpl for tmpl, _ in parsed}
    missing = set(ClimateZone) - set(templates)
    if missing:
        raise ValueError(f"data/climate.json: 缺气候档 {[z.name for z in missing]}")
    for tmpl, label_key in parsed:
        tmpl.climate.label_key = label_key
    return templates


_CLIMATE_TEMPLATES: dict[ClimateZone, ClimateTemplate] = _build_climate_templates(
    load_content("climate")
)


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
        rainfall: 降雨量 — 年均基线为 mm/年，当前天气为 mm/小时（瞬时强度）。
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
    """纬度噪声 → 海平面年均温度（绑定 C 端公式）。

    实现本体在 _hydrology.c（hydrology_sea_level_temperature）：
    与生产链路（_hydrology.c compute_climate）同一公式、同一 clamp，
    此处仅为 ctypes 绑定——单源 C，无 Python 侧双实现。

    Args:
        latitude_noise: 纬度噪声值 [-1, 1]。

    Returns:
        海平面年均温度 (°C)。
    """
    from .hydrology import sea_level_temperature_c
    return sea_level_temperature_c(latitude_noise)


def apply_lapse_rate(sea_level_temp: float, altitude: float) -> float:
    """气温直减率：海拔每升高 1000m 温度下降 LAPSE_RATE °C（绑定 C）。

    实现本体在 _hydrology.c（hydrology_apply_lapse_rate），与场计算
    统一语义：直减率仅作用于陆地（altitude>0），海域返回海面温度
    本身——负海拔不再产生深度伪影；陆地 clamp [-20, 36]。

    Args:
        sea_level_temp: 海平面温度 (°C)。
        altitude: 海拔 (m)。

    Returns:
        实际温度 (°C)。
    """
    from .hydrology import apply_lapse_rate_c
    return apply_lapse_rate_c(sea_level_temp, altitude)


def rainfall_from_noise(rainfall_noise: float) -> float:
    """降雨噪声 → 年降雨量 (mm/年)（绑定 C 端公式）。

    实现本体在 _hydrology.c（hydrology_rainfall_from_noise）：
    与生产链路同一公式、同一 clamp，此处仅为 ctypes 绑定。

    Args:
        rainfall_noise: 降雨噪声 [-1, 1]，-1=极干，+1=极湿。

    Returns:
        年降雨量 (mm)。
    """
    from .hydrology import rainfall_from_noise_c
    return rainfall_from_noise_c(rainfall_noise)


def classify(
    mean_temp: float,
    annual_rainfall: float,
    altitude: float,
) -> ClimateZone:
    """由年均温、年降雨量、海拔纯静态判定气候档位（绑定 C）。

    判定顺序（前者优先；下列数值为 config 默认值，运行期阈值
    由 config 注入 C 层，以 config 为准）：
      1. 海拔 ≥ 2000m → ALPINE（覆盖纬度气候，高山独立）
      2. 温度 < -5°C → POLAR_TUNDRA（极地，不论降雨）
      3. 降雨 < 200mm → DESERT（极端干旱，不论温暖）
      4. 降雨 < 600mm 且温度 > 5°C → STEPPE（半干旱草原）
      5. 温度 ≥ 20°C → EQUATORIAL_RAINFOREST（R≥1500）/ TROPICAL_SAVANNA
      6. 温度 ≥ 5°C → TEMPERATE_FOREST
      7. -5≤T<5°C → SUBARCTIC_TAIGA（R≥400）/ POLAR_TUNDRA（冷干合并）

    实现本体在 _hydrology.c（hydrology_classify）。判定阈值单一
    事实源在 ascend/config.py，由 hydrology 模块导入期注入 C
    （apply_config_climate_constants），C 侧无阈值副本；此处仅为
    ctypes 绑定——单源 C，无 Python 侧双实现。纯函数，线程安全。

    Args:
        mean_temp: 年均温度 (°C)。
        annual_rainfall: 年降雨量 (mm)。
        altitude: 海拔 (m)。

    Returns:
        对应的 ClimateZone。
    """
    from .hydrology import classify_climate_c
    return ClimateZone(classify_climate_c(mean_temp, annual_rainfall, altitude))


def annual_baseline(
    altitude: float,
    sea_level_temp: float,
    rainfall: float,
    climate: ClimateZone,
    *,
    humidity_noise: float = 0.0,
    wind_noise: float = 0.0,
) -> WeatherParams:
    """组装完整的年均基线气象参数。

    温度和降雨由物理推导得出，湿度/风速
    从气候档位模板的区间表中用噪声插值。
    日照固定为 12.0（天文年均，季节变化由天气引擎单独处理）。

    Args:
        altitude: 海拔 (m)。
        sea_level_temp: 海平面温度 (°C)。
        rainfall: 年降雨量 (mm)。
        climate: 气候档位。
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
        return clamp(value, blo, bhi)

    return WeatherParams(
        temperature=temperature,
        rainfall=rainfall,
        sunshine=12.0,
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
