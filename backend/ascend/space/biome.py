"""群系生态 — 群系类型枚举、模板数据和分配逻辑。

群系 = 海拔判定（陆地/海洋）+ 气候属性（温度/降雨/海拔）+ 次级噪声。
分配逻辑为纯函数，线程安全。

陆地群系按气候档细分（每档 2 子型），共 16 种陆地群系 + 3 种海洋群系。
细分维度用连续场（降雨/温度/海拔/moisture_noise），tile 生成时按隶属度
加权混合 TerrainBias，保证 chunk 边界连续。chunk 级 biome 标签取主隶属。
"""

from dataclasses import dataclass, field
from enum import IntEnum
from typing import Mapping

from ascend.data import load_content, split_ns_id
from ascend.i18n import get_default
from .climate import ClimateZone, classify

_I18N = get_default()


# ═══════════════════════════════════════════════════════════
# TerrainBias — 群系对地形分类的偏移参数
# ═══════════════════════════════════════════════════════════


@dataclass(slots=True)
class TerrainBias:
    """群系对 tile 地形分类的偏移参数。

    基线 = TEMPERATE_DECIDUOUS_FOREST（全 0，用默认海拔带阈值）。
    其他群系相对基线偏移。tile 生成时 bias = Σ weight_i × bias_i。

    Attributes:
        sand_cap_delta: SAND 海拔上限偏移 (m)，+值=更多沙地。
        fertile_shift: FERTILE_SOIL 海拔带整体平移 (m)，+值=上移(抑制沃土)。
        rock_threshold_delta: ROCK 海拔阈值偏移 (m)，-值=更低海拔出岩石。
            只影响 ROCK 阈值，不影响 STEEP/PEAK。
        peak_threshold_delta: MOUNTAIN_PEAK 海拔阈值偏移 (m)，
            +值=更高海拔才出雪顶（高山草甸用），-值=更低海拔出雪顶。
        marsh_tendency: MARSH 倾向 [0,1]，湿地概率加成。
    """

    sand_cap_delta: float = 0.0
    fertile_shift: float = 0.0
    rock_threshold_delta: float = 0.0
    peak_threshold_delta: float = 0.0
    marsh_tendency: float = 0.0


# ═══════════════════════════════════════════════════════════
# BiomeType — 群系类型枚举
# ═══════════════════════════════════════════════════════════


class BiomeType(IntEnum):
    """群系类型 — 16 陆地（8 气候档 × 2 子型）+ 3 海洋。

    陆地群系按气候档细分，每档 2 个子型，细分维度用连续场。
    值从 0 开始连续编号（0..18），uint16 足以容纳。

    枚举值 = 持久化/协议契约（不可改，只能追加）；显示名与模板数据
    在 data/biome.json（label_key 经 i18n 惰性解析）。
    """

    TROPICAL_MONSOON_FOREST = 0
    TROPICAL_RAINFOREST = 1
    TROPICAL_SAVANNA = 2
    TROPICAL_WOODLAND = 3
    SANDY_DESERT = 4
    ROCKY_DESERT = 5
    SHORT_GRASS_STEPPE = 6
    TALL_GRASS_STEPPE = 7
    TEMPERATE_MIXED_FOREST = 8
    TEMPERATE_DECIDUOUS_FOREST = 9
    BOREAL_WETLAND = 10
    BOREAL_FOREST = 11
    POLAR_BARREN = 12
    TUNDRA = 13
    ALPINE_MEADOW = 14
    ALPINE_BARREN = 15
    WARM_OCEAN = 16
    TEMPERATE_OCEAN = 17
    COLD_OCEAN = 18

    def __init__(self, value: int) -> None:
        """数据加载后由 loader 填充 label_key（i18n 键）。"""
        self.label_key: str = ""

    @property
    def label(self) -> str:
        """本地化显示名（label_key 经 i18n 惰性解析，随语言切换）。"""
        return _I18N.t(self.label_key) if self.label_key else self.name

    @property
    def is_ocean(self) -> bool:
        """是否为海洋群系（由 data 的 ocean 标志派生）。"""
        return self in _OCEAN_BIOMES

    def __repr__(self) -> str:
        return f"BiomeType.{self.name}"


# ═══════════════════════════════════════════════════════════
# BiomeTemplate — 群系模板
# ═══════════════════════════════════════════════════════════


@dataclass
class BiomeTemplate:
    """群系模板 — 定义该群系内的生成参数和生态内容。

    分块生成时由模板实例化，叠加噪声细节。

    Attributes:
        biome_type: 群系类型枚举。
        climate_zone: 所属气候档位。
        water_ratio: 水体面积占比 [0, 1]。
        mountain_ratio: 山地面积占比 [0, 1]。
        tree_density: 植被密度系数 [0, 1]。
        terrain_bias: 地形分类偏移参数。
        creature_weights: 生物种类及其基础出现权重。
        resource_weights: 资源类型及其基础分布权重。
    """

    biome_type: BiomeType
    climate_zone: ClimateZone

    water_ratio: float = 0.05
    mountain_ratio: float = 0.05
    tree_density: float = 0.5
    terrain_bias: TerrainBias = field(default_factory=TerrainBias)

    creature_weights: dict[str, float] = field(default_factory=dict)
    resource_weights: dict[str, float] = field(default_factory=dict)

    def __repr__(self) -> str:
        return (
            f"BiomeTemplate({self.biome_type.label}, "
            f"climate={self.climate_zone.label}, "
            f"water={self.water_ratio:.0%}, "
            f"trees={self.tree_density:.0%})"
        )


def _ns_local(ns_id: str) -> str:
    """命名空间 id 的 local 部分（大写，枚举成员名；非法格式 fail fast）。"""
    return split_ns_id(ns_id)[1].upper()


def _parse_biome_template(ns_id: str, raw: Mapping) -> tuple[BiomeTemplate, str]:
    """单行 JSON → (BiomeTemplate, label_key)（不就地修改枚举成员）。

    校验枚举一致性、必需字段；label_key 由调用方在全部校验通过后统一填充，
    避免解析中途失败留下半修改的模块级枚举状态。
    """
    biome = BiomeType[_ns_local(ns_id)]
    if int(raw["value"]) != biome.value:
        raise ValueError(
            f"{ns_id}: 数据 value {raw['value']} 与枚举 {biome.value} 不一致"
        )
    if "label_key" not in raw:
        raise ValueError(f"{ns_id}: 缺少必需字段 label_key")
    label_key = str(raw["label_key"])
    climate = ClimateZone[_ns_local(str(raw["climate"]))]
    bias_raw = raw.get("terrain_bias", {})
    if not isinstance(bias_raw, Mapping):
        raise ValueError(f"{ns_id}: terrain_bias 必须是对象")
    tmpl = BiomeTemplate(
        biome_type=biome,
        climate_zone=climate,
        water_ratio=float(raw.get("water_ratio", 0.05)),
        mountain_ratio=float(raw.get("mountain_ratio", 0.05)),
        tree_density=float(raw.get("tree_density", 0.5)),
        terrain_bias=TerrainBias(**{k: float(v) for k, v in bias_raw.items()}),
        creature_weights={
            str(k): float(v) for k, v in raw.get("creature_weights", {}).items()
        },
        resource_weights={
            str(k): float(v) for k, v in raw.get("resource_weights", {}).items()
        },
    )
    return tmpl, label_key


def _build_biome_templates(doc: Mapping) -> dict[BiomeType, BiomeTemplate]:
    """data/biome.json → 群系模板注册表，并派生 _OCEAN_BIOMES。

    先全量校验/解析，全部通过后才统一填充枚举 label_key（原子性）。
    """
    raw_map = doc.get("biome")
    if not isinstance(raw_map, Mapping) or not raw_map:
        raise ValueError("data/biome.json: 缺少 biome 注册表")
    parsed: list[tuple[BiomeTemplate, str]] = []
    oceans: list[BiomeType] = []
    for ns_id, raw in raw_map.items():
        tmpl, label_key = _parse_biome_template(ns_id, raw)
        parsed.append((tmpl, label_key))
        if raw.get("ocean"):
            oceans.append(tmpl.biome_type)
    templates = {tmpl.biome_type: tmpl for tmpl, _ in parsed}
    missing = set(BiomeType) - set(templates)
    if missing:
        raise ValueError(f"data/biome.json: 缺群系 {[b.name for b in missing]}")
    # 全部校验通过后统一填充（避免半修改状态）
    for tmpl, label_key in parsed:
        tmpl.biome_type.label_key = label_key
    global _OCEAN_BIOMES
    _OCEAN_BIOMES = frozenset(oceans)
    return templates


# ═══════════════════════════════════════════════════════════
# 群系模板注册表
# ═══════════════════════════════════════════════════════════

_BIOME_TEMPLATES: dict[BiomeType, BiomeTemplate] = _build_biome_templates(
    load_content("biome")
)

# 海洋群系集合 — 由 data ocean 标志派生（is_ocean 判定依据），
# 在 _build_biome_templates 内赋值（见函数体 global 声明）


# ═══════════════════════════════════════════════════════════
# 群系细分配置 — 每气候档的子型 + 细分维度
# ═══════════════════════════════════════════════════════════

# 海平面海拔阈值 — 定义于 ascend.config
from ascend.config import (
    SEA_LEVEL_ELEV as _SEA_LEVEL,
    OCEAN_COLD_CUTOFF as _OCEAN_COLD_CUTOFF,
    OCEAN_WARM_CUTOFF as _OCEAN_WARM_CUTOFF,
)


# 细分维度枚举
_SUBDIV_RAINFALL = "rainfall"
_SUBDIV_TEMPERATURE = "temperature"
_SUBDIV_ALTITUDE = "altitude"
_SUBDIV_MOISTURE = "moisture"


@dataclass(slots=True)
class _SubdivConfig:
    """一个气候档的群系细分配置。

    Attributes:
        dimension: 细分维度名称。
        low: 低端子型（维度值小→此子型）。
        high: 高端子型（维度值大→此子型）。
        value_min: 该档维度值域下限（归一化用）。
        value_max: 该档维度值域上限（归一化用）。
    """
    dimension: str
    low: BiomeType
    high: BiomeType
    value_min: float
    value_max: float


# 8 档气候 → 细分配置
# value_min/value_max 基于大陆场该档内实际分布的 P50 校准，
# 使归一化中点对准实际中位数 → 两子型比例均衡。

def _build_subdiv_configs(doc: Mapping) -> dict[ClimateZone, _SubdivConfig]:
    """data/biome.json 的 subdiv 段 → 每气候档细分配置。"""
    raw_map = doc.get("subdiv")
    if not isinstance(raw_map, Mapping) or not raw_map:
        raise ValueError("data/biome.json: 缺少 subdiv 配置")
    configs: dict[ClimateZone, _SubdivConfig] = {}
    for clim_ns, raw in raw_map.items():
        zone = ClimateZone[_ns_local(clim_ns)]
        configs[zone] = _SubdivConfig(
            dimension=str(raw["dimension"]),
            low=BiomeType[_ns_local(str(raw["low"]))],
            high=BiomeType[_ns_local(str(raw["high"]))],
            value_min=float(raw["value_min"]),
            value_max=float(raw["value_max"]),
        )
    missing = set(ClimateZone) - set(configs)
    if missing:
        raise ValueError(f"data/biome.json: 缺细分配置 {[z.name for z in missing]}")
    return configs


_SUBDIV_CONFIGS: dict[ClimateZone, _SubdivConfig] = _build_subdiv_configs(
    load_content("biome")
)


# ═══════════════════════════════════════════════════════════
# 群系隶属度计算（纯函数）
# ═══════════════════════════════════════════════════════════


def biome_membership(
    mean_temp: float,
    annual_rainfall: float,
    altitude: float,
    sea_level_temp: float,
    moisture_noise: float = 0.0,
    subdiv_ranges: dict[int, tuple[float, float]] | None = None,
) -> list[tuple[BiomeType, float]]:
    """计算 tile/chunk 对各群系的隶属度。

    海洋（altitude < 0）直接返回单一海洋群系（隶属度 1.0）。
    陆地先 classify 得气候档，再按该档细分维度归一化，
    用三角形隶属函数算两子型权重。

    归一化值 v ∈ [0,1]，两子型中心 c_lo=0.25、c_hi=0.75：
      w_lo = max(0, 1 - |v - 0.25| / 0.5)
      w_hi = max(0, 1 - |v - 0.75| / 0.5)
    归一化后 w_lo + w_hi = 1。边界处（v≈0.5）两权重各 0.5 → 平滑混合。

    Args:
        mean_temp: 年均温度 (°C)。
        annual_rainfall: 年降雨量 (mm)。
        altitude: 实际海拔 (m)。
        sea_level_temp: 海平面温度 (°C)，用于海洋温度分类。
        moisture_noise: 湿度次级噪声 [-1, 1]（沙漠细分用）。
        subdiv_ranges: 动态值域 {ClimateZone_int: (P10, P90)}，
            由 ContinentData.subdiv_ranges 提供。提供时覆盖静态
            _SUBDIV_CONFIGS 的 value_min/max，使档内子型比例均衡。
            None 时用静态默认值。

    Returns:
        [(BiomeType, weight), ...] 权重和为 1.0。
        海洋返回单项列表。陆地返回 1-2 项（边界处 2 项）。
    """
    # ── 海洋判定 ──────────────────────────────────────
    if altitude < _SEA_LEVEL:
        if sea_level_temp >= _OCEAN_WARM_CUTOFF:
            return [(BiomeType.WARM_OCEAN, 1.0)]
        elif sea_level_temp >= _OCEAN_COLD_CUTOFF:
            return [(BiomeType.TEMPERATE_OCEAN, 1.0)]
        else:
            return [(BiomeType.COLD_OCEAN, 1.0)]

    # ── 陆地判定 ──────────────────────────────────────
    climate = classify(mean_temp, annual_rainfall, altitude)
    cfg = _SUBDIV_CONFIGS.get(climate)
    if cfg is None:
        return [(BiomeType.TEMPERATE_DECIDUOUS_FOREST, 1.0)]

    # 取细分维度的连续值
    if cfg.dimension == _SUBDIV_RAINFALL:
        raw = annual_rainfall
    elif cfg.dimension == _SUBDIV_TEMPERATURE:
        raw = mean_temp
    elif cfg.dimension == _SUBDIV_ALTITUDE:
        raw = altitude
    else:  # _SUBDIV_MOISTURE
        raw = moisture_noise

    # 归一化到 [0,1]：优先用动态值域，否则用静态默认
    if subdiv_ranges is not None and climate.value in subdiv_ranges:
        v_min, v_max = subdiv_ranges[climate.value]
    else:
        v_min, v_max = cfg.value_min, cfg.value_max

    span = v_max - v_min
    if span <= 0:
        v = 0.5
    else:
        v = (raw - v_min) / span
        v = max(0.0, min(1.0, v))

    # 三角形隶属函数
    c_lo, c_hi = 0.25, 0.75
    w_lo = max(0.0, 1.0 - abs(v - c_lo) / 0.5)
    w_hi = max(0.0, 1.0 - abs(v - c_hi) / 0.5)

    # 归一化
    total = w_lo + w_hi
    if total <= 0:
        return [(cfg.low if v < 0.5 else cfg.high, 1.0)]

    w_lo /= total
    w_hi /= total

    result: list[tuple[BiomeType, float]] = []
    if w_lo > 0.001:
        result.append((cfg.low, w_lo))
    if w_hi > 0.001:
        result.append((cfg.high, w_hi))
    return result


def biome_from_attrs(
    mean_temp: float,
    annual_rainfall: float,
    altitude: float,
    sea_level_temp: float,
    moisture_noise: float = 0.0,
    subdiv_ranges: dict[int, tuple[float, float]] | None = None,
) -> BiomeType:
    """根据连续气候属性分配群系（取主隶属）。

    判定顺序:
      1. 海拔 <0 → 海洋（按海平面温度分暖/温/冷）
      2. 海拔 >=0 → 陆地（classify 得气候档，档内细分取主隶属）

    Args:
        mean_temp: 年均温度 (°C)。
        annual_rainfall: 年降雨量 (mm)。
        altitude: 实际海拔 (m)。
        sea_level_temp: 海平面温度 (°C)，用于海洋温度分类。
        moisture_noise: 湿度次级噪声 [-1, 1]（沙漠细分用）。
        subdiv_ranges: 动态值域（来自 ContinentData.subdiv_ranges）。

    Returns:
        群系类型（主隶属）。
    """
    membership = biome_membership(
        mean_temp, annual_rainfall, altitude, sea_level_temp, moisture_noise,
        subdiv_ranges=subdiv_ranges,
    )
    return max(membership, key=lambda x: x[1])[0]


def get_template(biome: BiomeType) -> BiomeTemplate:
    """获取群系模板。

    Args:
        biome: 群系类型。

    Returns:
        对应的 BiomeTemplate。若未注册则返回温带落叶林模板作为兜底。
    """
    return _BIOME_TEMPLATES.get(
        biome,
        _BIOME_TEMPLATES[BiomeType.TEMPERATE_DECIDUOUS_FOREST],
    )


__all__ = [
    "TerrainBias",
    "BiomeType",
    "BiomeTemplate",
    "biome_membership",
    "biome_from_attrs",
    "get_template",
]
