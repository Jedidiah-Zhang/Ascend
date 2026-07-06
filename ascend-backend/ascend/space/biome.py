"""群系生态 — 群系类型枚举、模板数据和分配逻辑。

群系 = 海拔判定（陆地/海洋）+ 气候属性（温度/降雨/海拔）+ 次级噪声。
分配逻辑为纯函数，线程安全。

陆地群系按气候档细分（每档 2 子型），共 16 种陆地群系 + 3 种海洋群系。
细分维度用连续场（降雨/温度/海拔/moisture_noise），tile 生成时按隶属度
加权混合 TerrainBias，保证 chunk 边界连续。chunk 级 biome 标签取主隶属。
"""

from dataclasses import dataclass, field
from enum import IntEnum

from .climate import ClimateZone, classify


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
    值从 0 开始连续编号，uint16 足以容纳。
    """

    # 档 0 EQUATORIAL_RAINFOREST — 细分维度 rainfall
    TROPICAL_MONSOON_FOREST = (0, "热带季雨林")
    TROPICAL_RAINFOREST = (1, "热带雨林")

    # 档 1 TROPICAL_SAVANNA — 细分维度 rainfall
    TROPICAL_SAVANNA = (2, "热带草原")
    TROPICAL_WOODLAND = (3, "热带疏林")

    # 档 2 DESERT — 细分维度 moisture_noise
    SANDY_DESERT = (4, "沙质沙漠")
    ROCKY_DESERT = (5, "砾石戈壁")

    # 档 3 STEPPE — 细分维度 rainfall
    SHORT_GRASS_STEPPE = (6, "矮草草原")
    TALL_GRASS_STEPPE = (7, "高草草原")

    # 档 4 TEMPERATE_FOREST — 细分维度 temperature
    TEMPERATE_MIXED_FOREST = (8, "温带混交林")
    TEMPERATE_DECIDUOUS_FOREST = (9, "温带落叶林")

    # 档 5 SUBARCTIC_TAIGA — 细分维度 altitude
    BOREAL_WETLAND = (10, "北方湿地")
    BOREAL_FOREST = (11, "北方针叶林")

    # 档 6 POLAR_TUNDRA — 细分维度 temperature
    POLAR_BARREN = (12, "极地荒原")
    TUNDRA = (13, "苔原")

    # 档 7 ALPINE — 细分维度 altitude
    ALPINE_MEADOW = (14, "高山草甸")
    ALPINE_BARREN = (15, "高山裸岩")

    # 海洋
    WARM_OCEAN = (16, "暖水海洋")
    TEMPERATE_OCEAN = (17, "温带海洋")
    COLD_OCEAN = (18, "冷水海洋")

    def __new__(cls, value: int, label: str) -> "BiomeType":
        """重写 __new__ 以支持双参数 (value, label)。"""
        obj = int.__new__(cls, value)
        obj._value_ = value
        obj.label = label
        return obj

    @property
    def is_ocean(self) -> bool:
        """是否为海洋群系。"""
        return self.value >= 16

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


# ═══════════════════════════════════════════════════════════
# 群系模板注册表
# ═══════════════════════════════════════════════════════════

_BIOME_TEMPLATES: dict[BiomeType, BiomeTemplate] = {
    # ── 档 0 EQUATORIAL_RAINFOREST ──────────────────────────
    BiomeType.TROPICAL_MONSOON_FOREST: BiomeTemplate(
        biome_type=BiomeType.TROPICAL_MONSOON_FOREST,
        climate_zone=ClimateZone.EQUATORIAL_RAINFOREST,
        water_ratio=0.10,
        mountain_ratio=0.03,
        tree_density=0.70,
        terrain_bias=TerrainBias(
            sand_cap_delta=-20.0, fertile_shift=0.0,
            rock_threshold_delta=-50.0, marsh_tendency=0.10,
        ),
        creature_weights={
            "monkey": 0.15, "parrot": 0.15, "jaguar": 0.05,
            "tree_frog": 0.15, "insect": 0.30, "canopy_deer": 0.20,
        },
        resource_weights={
            "hardwood": 0.25, "fruit": 0.20, "herb": 0.20,
            "vine": 0.15, "resin": 0.10, "shallow_mineral": 0.10,
        },
    ),
    BiomeType.TROPICAL_RAINFOREST: BiomeTemplate(
        biome_type=BiomeType.TROPICAL_RAINFOREST,
        climate_zone=ClimateZone.EQUATORIAL_RAINFOREST,
        water_ratio=0.12,
        mountain_ratio=0.03,
        tree_density=0.95,
        terrain_bias=TerrainBias(
            sand_cap_delta=-30.0, fertile_shift=-50.0,
            rock_threshold_delta=-50.0, marsh_tendency=0.20,
        ),
        creature_weights={
            "monkey": 0.20, "parrot": 0.20, "jaguar": 0.05,
            "tree_frog": 0.20, "insect": 0.25, "sloth": 0.10,
        },
        resource_weights={
            "hardwood": 0.30, "fruit": 0.25, "herb": 0.20,
            "vine": 0.10, "resin": 0.10, "shallow_mineral": 0.05,
        },
    ),

    # ── 档 1 TROPICAL_SAVANNA ───────────────────────────────
    BiomeType.TROPICAL_SAVANNA: BiomeTemplate(
        biome_type=BiomeType.TROPICAL_SAVANNA,
        climate_zone=ClimateZone.TROPICAL_SAVANNA,
        water_ratio=0.05,
        mountain_ratio=0.05,
        tree_density=0.20,
        terrain_bias=TerrainBias(
            sand_cap_delta=20.0, fertile_shift=0.0,
            rock_threshold_delta=-50.0, marsh_tendency=0.0,
        ),
        creature_weights={
            "zebra": 0.20, "antelope": 0.20, "lion": 0.05,
            "elephant": 0.05, "bird": 0.20, "insect": 0.30,
        },
        resource_weights={
            "softwood": 0.15, "grass_fiber": 0.35, "herb": 0.20,
            "stone": 0.15, "shallow_mineral": 0.15,
        },
    ),
    BiomeType.TROPICAL_WOODLAND: BiomeTemplate(
        biome_type=BiomeType.TROPICAL_WOODLAND,
        climate_zone=ClimateZone.TROPICAL_SAVANNA,
        water_ratio=0.06,
        mountain_ratio=0.04,
        tree_density=0.45,
        terrain_bias=TerrainBias(
            sand_cap_delta=0.0, fertile_shift=-30.0,
            rock_threshold_delta=-50.0, marsh_tendency=0.05,
        ),
        creature_weights={
            "antelope": 0.20, "deer": 0.15, "lion": 0.05,
            "bird": 0.20, "insect": 0.20, "monkey": 0.10, "rodent": 0.10,
        },
        resource_weights={
            "softwood": 0.30, "hardwood": 0.10, "grass_fiber": 0.25,
            "herb": 0.20, "stone": 0.10, "shallow_mineral": 0.05,
        },
    ),

    # ── 档 2 DESERT ─────────────────────────────────────────
    BiomeType.SANDY_DESERT: BiomeTemplate(
        biome_type=BiomeType.SANDY_DESERT,
        climate_zone=ClimateZone.DESERT,
        water_ratio=0.01,
        mountain_ratio=0.08,
        tree_density=0.02,
        terrain_bias=TerrainBias(
            sand_cap_delta=60.0, fertile_shift=80.0,
            rock_threshold_delta=-100.0, marsh_tendency=0.0,
        ),
        creature_weights={
            "lizard": 0.30, "rodent": 0.25, "nocturnal_predator": 0.10,
            "venomous_insect": 0.20, "bird": 0.10, "snake": 0.05,
        },
        resource_weights={
            "sand": 0.40, "exposed_mineral": 0.15, "succulent": 0.15,
            "oasis_water": 0.05, "stone": 0.25,
        },
    ),
    BiomeType.ROCKY_DESERT: BiomeTemplate(
        biome_type=BiomeType.ROCKY_DESERT,
        climate_zone=ClimateZone.DESERT,
        water_ratio=0.01,
        mountain_ratio=0.14,
        tree_density=0.03,
        terrain_bias=TerrainBias(
            sand_cap_delta=-20.0, fertile_shift=120.0,
            rock_threshold_delta=-300.0, marsh_tendency=0.0,
        ),
        creature_weights={
            "rodent": 0.30, "lizard": 0.15, "nocturnal_predator": 0.15,
            "venomous_insect": 0.10, "snake": 0.15, "eagle": 0.05, "hyrax": 0.10,
        },
        resource_weights={
            "exposed_mineral": 0.40, "stone": 0.25, "succulent": 0.10,
            "sand": 0.10, "shallow_mineral": 0.15,
        },
    ),

    # ── 档 3 STEPPE ─────────────────────────────────────────
    BiomeType.SHORT_GRASS_STEPPE: BiomeTemplate(
        biome_type=BiomeType.SHORT_GRASS_STEPPE,
        climate_zone=ClimateZone.STEPPE,
        water_ratio=0.02,
        mountain_ratio=0.08,
        tree_density=0.10,
        terrain_bias=TerrainBias(
            sand_cap_delta=30.0, fertile_shift=50.0,
            rock_threshold_delta=-100.0, marsh_tendency=0.0,
        ),
        creature_weights={
            "rodent": 0.25, "antelope": 0.10, "nocturnal_predator": 0.15,
            "bird": 0.15, "insect": 0.20, "snake": 0.15,
        },
        resource_weights={
            "exposed_mineral": 0.30, "fiber": 0.25, "succulent": 0.15,
            "shallow_mineral": 0.10, "stone": 0.20,
        },
    ),
    BiomeType.TALL_GRASS_STEPPE: BiomeTemplate(
        biome_type=BiomeType.TALL_GRASS_STEPPE,
        climate_zone=ClimateZone.STEPPE,
        water_ratio=0.04,
        mountain_ratio=0.06,
        tree_density=0.18,
        terrain_bias=TerrainBias(
            sand_cap_delta=10.0, fertile_shift=0.0,
            rock_threshold_delta=-50.0, marsh_tendency=0.05,
        ),
        creature_weights={
            "antelope": 0.25, "rodent": 0.15, "horse": 0.10,
            "nocturnal_predator": 0.05, "bird": 0.20, "insect": 0.20, "fox": 0.05,
        },
        resource_weights={
            "grass_fiber": 0.40, "herb": 0.20, "exposed_mineral": 0.15,
            "shallow_mineral": 0.10, "stone": 0.10, "sod": 0.05,
        },
    ),

    # ── 档 4 TEMPERATE_FOREST ───────────────────────────────
    BiomeType.TEMPERATE_MIXED_FOREST: BiomeTemplate(
        biome_type=BiomeType.TEMPERATE_MIXED_FOREST,
        climate_zone=ClimateZone.TEMPERATE_FOREST,
        water_ratio=0.10,
        mountain_ratio=0.08,
        tree_density=0.65,
        terrain_bias=TerrainBias(
            sand_cap_delta=0.0, fertile_shift=0.0,
            rock_threshold_delta=-150.0, marsh_tendency=0.10,
        ),
        creature_weights={
            "moose": 0.20, "wolf": 0.15, "bear": 0.10,
            "hare": 0.20, "lynx": 0.10, "bird": 0.15, "insect": 0.10,
        },
        resource_weights={
            "softwood": 0.40, "resin": 0.20, "berry": 0.15,
            "fungus": 0.10, "shallow_mineral": 0.10, "hardwood": 0.05,
        },
    ),
    BiomeType.TEMPERATE_DECIDUOUS_FOREST: BiomeTemplate(
        biome_type=BiomeType.TEMPERATE_DECIDUOUS_FOREST,
        climate_zone=ClimateZone.TEMPERATE_FOREST,
        water_ratio=0.08,
        mountain_ratio=0.05,
        tree_density=0.70,
        terrain_bias=TerrainBias(
            sand_cap_delta=0.0, fertile_shift=0.0,
            rock_threshold_delta=0.0, marsh_tendency=0.05,
        ),
        creature_weights={
            "deer": 0.30, "rabbit": 0.20, "wolf": 0.10,
            "bear": 0.05, "bird": 0.25, "insect": 0.10,
        },
        resource_weights={
            "hardwood": 0.35, "softwood": 0.25, "berry": 0.15,
            "herb": 0.10, "fungus": 0.10, "shallow_mineral": 0.05,
        },
    ),

    # ── 档 5 SUBARCTIC_TAIGA ────────────────────────────────
    BiomeType.BOREAL_WETLAND: BiomeTemplate(
        biome_type=BiomeType.BOREAL_WETLAND,
        climate_zone=ClimateZone.SUBARCTIC_TAIGA,
        water_ratio=0.22,
        mountain_ratio=0.05,
        tree_density=0.45,
        terrain_bias=TerrainBias(
            sand_cap_delta=0.0, fertile_shift=0.0,
            rock_threshold_delta=-50.0, marsh_tendency=0.60,
        ),
        creature_weights={
            "moose": 0.25, "crane": 0.15, "beaver": 0.10,
            "hare": 0.15, "wolf": 0.10, "bird": 0.15, "insect": 0.10,
        },
        resource_weights={
            "softwood": 0.25, "peat": 0.20, "moss": 0.20,
            "berry": 0.10, "fungus": 0.10, "shallow_mineral": 0.10, "herb": 0.05,
        },
    ),
    BiomeType.BOREAL_FOREST: BiomeTemplate(
        biome_type=BiomeType.BOREAL_FOREST,
        climate_zone=ClimateZone.SUBARCTIC_TAIGA,
        water_ratio=0.08,
        mountain_ratio=0.10,
        tree_density=0.60,
        terrain_bias=TerrainBias(
            sand_cap_delta=0.0, fertile_shift=0.0,
            rock_threshold_delta=-100.0, marsh_tendency=0.10,
        ),
        creature_weights={
            "moose": 0.20, "wolf": 0.15, "bear": 0.10,
            "hare": 0.20, "lynx": 0.05, "bird": 0.20, "insect": 0.10,
        },
        resource_weights={
            "softwood": 0.45, "resin": 0.20, "berry": 0.15,
            "fungus": 0.10, "shallow_mineral": 0.10,
        },
    ),

    # ── 档 6 POLAR_TUNDRA ───────────────────────────────────
    BiomeType.POLAR_BARREN: BiomeTemplate(
        biome_type=BiomeType.POLAR_BARREN,
        climate_zone=ClimateZone.POLAR_TUNDRA,
        water_ratio=0.05,
        mountain_ratio=0.15,
        tree_density=0.00,
        terrain_bias=TerrainBias(
            sand_cap_delta=40.0, fertile_shift=150.0,
            rock_threshold_delta=-300.0, marsh_tendency=0.0,
        ),
        creature_weights={
            "polar_bear": 0.10, "seal": 0.10, "arctic_bird": 0.15,
            "lemming": 0.20,
        },
        resource_weights={
            "ice_core": 0.20, "exposed_mineral": 0.35,
            "stone": 0.25, "lichen": 0.10, "ice": 0.10,
        },
    ),
    BiomeType.TUNDRA: BiomeTemplate(
        biome_type=BiomeType.TUNDRA,
        climate_zone=ClimateZone.POLAR_TUNDRA,
        water_ratio=0.05,
        mountain_ratio=0.10,
        tree_density=0.05,
        terrain_bias=TerrainBias(
            sand_cap_delta=20.0, fertile_shift=80.0,
            rock_threshold_delta=-200.0, marsh_tendency=0.10,
        ),
        creature_weights={
            "caribou": 0.25, "arctic_fox": 0.15, "hare": 0.20,
            "seal": 0.10, "bird": 0.20, "insect": 0.10,
        },
        resource_weights={
            "lichen": 0.35, "berry": 0.20, "moss": 0.20,
            "shallow_mineral": 0.15, "ice": 0.10,
        },
    ),

    # ── 档 7 ALPINE ─────────────────────────────────────────
    BiomeType.ALPINE_MEADOW: BiomeTemplate(
        biome_type=BiomeType.ALPINE_MEADOW,
        climate_zone=ClimateZone.ALPINE,
        water_ratio=0.05,
        mountain_ratio=0.30,
        tree_density=0.15,
        terrain_bias=TerrainBias(
            sand_cap_delta=0.0, fertile_shift=0.0,
            rock_threshold_delta=-200.0, peak_threshold_delta=600.0,
            marsh_tendency=0.05,
        ),
        creature_weights={
            "ibex": 0.25, "marmot": 0.20, "eagle": 0.15,
            "hare": 0.20, "insect": 0.20,
        },
        resource_weights={
            "exposed_mineral": 0.30, "herb": 0.25, "stone": 0.25,
            "shallow_mineral": 0.15, "ice": 0.05,
        },
    ),
    BiomeType.ALPINE_BARREN: BiomeTemplate(
        biome_type=BiomeType.ALPINE_BARREN,
        climate_zone=ClimateZone.ALPINE,
        water_ratio=0.03,
        mountain_ratio=0.60,
        tree_density=0.02,
        terrain_bias=TerrainBias(
            sand_cap_delta=0.0, fertile_shift=100.0,
            rock_threshold_delta=-400.0, peak_threshold_delta=200.0,
            marsh_tendency=0.0,
        ),
        creature_weights={
            "snow_leopard": 0.10, "eagle": 0.20,
            "marmot": 0.15, "ibex": 0.10,
        },
        resource_weights={
            "exposed_mineral": 0.45, "stone": 0.35,
            "shallow_mineral": 0.15, "ice": 0.05,
        },
    ),

    # ── 海洋群系 ───────────────────────────────────────────
    BiomeType.WARM_OCEAN: BiomeTemplate(
        biome_type=BiomeType.WARM_OCEAN,
        climate_zone=ClimateZone.EQUATORIAL_RAINFOREST,
        water_ratio=1.0,
        mountain_ratio=0.0,
        tree_density=0.0,
        terrain_bias=TerrainBias(),
        creature_weights={
            "tropical_fish": 0.35, "reef_fish": 0.25, "shark": 0.05,
            "coral": 0.20, "turtle": 0.10, "dolphin": 0.05,
        },
        resource_weights={
            "fish": 0.40, "coral": 0.25, "salt": 0.20, "pearl": 0.05,
            "kelp": 0.10,
        },
    ),
    BiomeType.TEMPERATE_OCEAN: BiomeTemplate(
        biome_type=BiomeType.TEMPERATE_OCEAN,
        climate_zone=ClimateZone.TEMPERATE_FOREST,
        water_ratio=1.0,
        mountain_ratio=0.0,
        tree_density=0.0,
        terrain_bias=TerrainBias(),
        creature_weights={
            "temperate_fish": 0.30, "squid": 0.15, "whale": 0.05,
            "seal": 0.10, "seabird": 0.20, "crab": 0.20,
        },
        resource_weights={
            "fish": 0.35, "kelp": 0.25, "salt": 0.20, "oil": 0.10,
            "shellfish": 0.10,
        },
    ),
    BiomeType.COLD_OCEAN: BiomeTemplate(
        biome_type=BiomeType.COLD_OCEAN,
        climate_zone=ClimateZone.POLAR_TUNDRA,
        water_ratio=1.0,
        mountain_ratio=0.0,
        tree_density=0.0,
        terrain_bias=TerrainBias(),
        creature_weights={
            "cold_fish": 0.30, "krill": 0.25, "whale": 0.10,
            "penguin": 0.15, "seal": 0.15, "polar_bear": 0.05,
        },
        resource_weights={
            "fish": 0.30, "oil": 0.20, "salt": 0.20,
            "krill": 0.20, "ice_core": 0.10,
        },
    ),
}


# ═══════════════════════════════════════════════════════════
# 群系细分配置 — 每气候档的子型 + 细分维度
# ═══════════════════════════════════════════════════════════

# 海平面海拔阈值
_SEA_LEVEL: float = 0.0

# 海洋温度分界 (°C，取海平面温度)
_OCEAN_COLD_CUTOFF: float = 5.0    # < 5°C → 冷水
_OCEAN_WARM_CUTOFF: float = 20.0   # >= 20°C → 暖水


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
        split: 分界点（归一化后 [0,1]，默认 0.5 即中点）。
    """
    dimension: str
    low: BiomeType
    high: BiomeType
    value_min: float
    value_max: float
    split: float = 0.5


# 8 档气候 → 细分配置
# value_min/value_max 基于大陆场该档内实际分布的 P50 校准，
# 使归一化中点对准实际中位数 → 两子型比例均衡。
_SUBDIV_CONFIGS: dict[ClimateZone, _SubdivConfig] = {
    ClimateZone.EQUATORIAL_RAINFOREST: _SubdivConfig(
        dimension=_SUBDIV_RAINFALL,
        low=BiomeType.TROPICAL_MONSOON_FOREST,
        high=BiomeType.TROPICAL_RAINFOREST,
        value_min=1500.0, value_max=2200.0,
    ),
    ClimateZone.TROPICAL_SAVANNA: _SubdivConfig(
        dimension=_SUBDIV_RAINFALL,
        low=BiomeType.TROPICAL_SAVANNA,
        high=BiomeType.TROPICAL_WOODLAND,
        value_min=600.0, value_max=1500.0,
    ),
    ClimateZone.DESERT: _SubdivConfig(
        dimension=_SUBDIV_MOISTURE,
        low=BiomeType.SANDY_DESERT,
        high=BiomeType.ROCKY_DESERT,
        value_min=-1.0, value_max=1.0,
    ),
    ClimateZone.STEPPE: _SubdivConfig(
        dimension=_SUBDIV_RAINFALL,
        low=BiomeType.SHORT_GRASS_STEPPE,
        high=BiomeType.TALL_GRASS_STEPPE,
        value_min=200.0, value_max=600.0,
    ),
    ClimateZone.TEMPERATE_FOREST: _SubdivConfig(
        dimension=_SUBDIV_TEMPERATURE,
        low=BiomeType.TEMPERATE_MIXED_FOREST,
        high=BiomeType.TEMPERATE_DECIDUOUS_FOREST,
        value_min=5.0, value_max=20.0,
    ),
    ClimateZone.SUBARCTIC_TAIGA: _SubdivConfig(
        dimension=_SUBDIV_ALTITUDE,
        low=BiomeType.BOREAL_WETLAND,
        high=BiomeType.BOREAL_FOREST,
        value_min=0.0, value_max=800.0,
    ),
    ClimateZone.POLAR_TUNDRA: _SubdivConfig(
        dimension=_SUBDIV_TEMPERATURE,
        low=BiomeType.POLAR_BARREN,
        high=BiomeType.TUNDRA,
        value_min=-14.0, value_max=0.0,
    ),
    ClimateZone.ALPINE: _SubdivConfig(
        dimension=_SUBDIV_ALTITUDE,
        low=BiomeType.ALPINE_MEADOW,
        high=BiomeType.ALPINE_BARREN,
        value_min=2000.0, value_max=2600.0,
    ),
}


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


def biome_from_climate(
    climate: ClimateZone,
    moisture_noise: float,
    altitude: float,
    sea_level_temp: float,
) -> BiomeType:
    """兼容旧 API：由气候档位分配群系。

    内部转调 biome_from_attrs。注意：旧接口未提供温度/降雨，
    无法精确重算 classify；此处用气候档位的细分配置取主隶属。
    海洋仍按海拔+海平面温度判定。

    Args:
        climate: 气候档位。
        moisture_noise: 湿度相关次级噪声 [-1, 1]。
        altitude: 实际海拔 (m)。
        sea_level_temp: 海平面温度 (°C)。

    Returns:
        群系类型。
    """
    if altitude < _SEA_LEVEL:
        if sea_level_temp >= _OCEAN_WARM_CUTOFF:
            return BiomeType.WARM_OCEAN
        elif sea_level_temp >= _OCEAN_COLD_CUTOFF:
            return BiomeType.TEMPERATE_OCEAN
        else:
            return BiomeType.COLD_OCEAN

    cfg = _SUBDIV_CONFIGS.get(climate)
    if cfg is None:
        return BiomeType.TEMPERATE_DECIDUOUS_FOREST

    # 用配置值域中点作为输入，取主隶属
    mid = (cfg.value_min + cfg.value_max) / 2.0
    if cfg.dimension == _SUBDIV_RAINFALL:
        return biome_from_attrs(
            sea_level_temp, mid, altitude, sea_level_temp, moisture_noise,
        )
    elif cfg.dimension == _SUBDIV_TEMPERATURE:
        return biome_from_attrs(
            mid, 800.0, altitude, sea_level_temp, moisture_noise,
        )
    elif cfg.dimension == _SUBDIV_ALTITUDE:
        return biome_from_attrs(
            sea_level_temp, 800.0, mid, sea_level_temp, moisture_noise,
        )
    else:  # _SUBDIV_MOISTURE
        return biome_from_attrs(
            sea_level_temp, 100.0, altitude, sea_level_temp, moisture_noise,
        )


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
    "biome_from_climate",
    "get_template",
]
