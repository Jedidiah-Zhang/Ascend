"""群系生态 — 群系类型枚举、模板数据和分配逻辑。

群系 = 海拔判定（陆地/海洋）+ 气候属性（温度/降雨/海拔）+ 次级噪声。
分配逻辑为纯函数，线程安全。

陆地群系从连续气候属性直接映射（经 classify 得气候档位再映群系），
8 档气候对应 8 种陆地群系，边界由连续场决定，chunk 边界自然渐变。
"""

from dataclasses import dataclass, field
from enum import IntEnum

from .climate import ClimateZone, classify


class BiomeType(IntEnum):
    """群系类型。

    陆地群系与 8 档气候一一对应；海洋群系由海拔 <0 判定，温度分暖/温/冷三档。
    """

    # 陆地（与 ClimateZone 对应）
    TEMPERATE_DECIDUOUS_FOREST = (0, "温带落叶林")
    TROPICAL_RAINFOREST = (1, "热带雨林")
    TROPICAL_SAVANNA = (2, "热带草原")
    DESERT = (3, "沙漠")
    STEPPE_SHRUBLAND = (4, "灌木草原")
    TAIGA = (5, "针叶林")
    TUNDRA = (6, "苔原")
    ALPINE_MEADOW = (7, "高山草甸")

    # 海洋
    WARM_OCEAN = (10, "暖水海洋")
    TEMPERATE_OCEAN = (11, "温带海洋")
    COLD_OCEAN = (12, "冷水海洋")

    def __new__(cls, value: int, label: str) -> "BiomeType":
        """重写 __new__ 以支持双参数 (value, label)。"""
        obj = int.__new__(cls, value)
        obj._value_ = value
        obj.label = label
        return obj

    @property
    def is_ocean(self) -> bool:
        """是否为海洋群系。"""
        return self.value >= 10

    def __repr__(self) -> str:
        return f"BiomeType.{self.name}"


@dataclass
class BiomeTemplate:
    """群系模板 — 定义该群系内的生成参数。

    分块生成时由模板实例化，叠加噪声细节。

    Attributes:
        biome_type: 群系类型枚举。
        climate_zone: 所属气候档位。
        water_ratio: 水体面积占比 [0, 1]。
        mountain_ratio: 山地面积占比 [0, 1]。
        tree_density: 植被密度系数 [0, 1]。
        creature_weights: 生物种类及其基础出现权重。
        resource_weights: 资源类型及其基础分布权重。
    """

    biome_type: BiomeType
    climate_zone: ClimateZone

    water_ratio: float = 0.05
    mountain_ratio: float = 0.05
    tree_density: float = 0.5

    creature_weights: dict[str, float] = field(default_factory=dict)
    resource_weights: dict[str, float] = field(default_factory=dict)

    def __repr__(self) -> str:
        return (
            f"BiomeTemplate({self.biome_type.label}, "
            f"climate={self.climate_zone.label}, "
            f"water={self.water_ratio:.0%}, "
            f"trees={self.tree_density:.0%})"
        )


# ── 群系模板注册表 ──────────────────────────────────────

_BIOME_TEMPLATES: dict[BiomeType, BiomeTemplate] = {
    BiomeType.TEMPERATE_DECIDUOUS_FOREST: BiomeTemplate(
        biome_type=BiomeType.TEMPERATE_DECIDUOUS_FOREST,
        climate_zone=ClimateZone.TEMPERATE_FOREST,
        water_ratio=0.08,
        mountain_ratio=0.05,
        tree_density=0.7,
        creature_weights={
            "deer": 0.30, "rabbit": 0.20, "wolf": 0.10,
            "bear": 0.05, "bird": 0.25, "insect": 0.10,
        },
        resource_weights={
            "hardwood": 0.35, "softwood": 0.25, "berry": 0.15,
            "herb": 0.10, "fungus": 0.10, "shallow_mineral": 0.05,
        },
    ),
    BiomeType.TROPICAL_RAINFOREST: BiomeTemplate(
        biome_type=BiomeType.TROPICAL_RAINFOREST,
        climate_zone=ClimateZone.EQUATORIAL_RAINFOREST,
        water_ratio=0.12,
        mountain_ratio=0.03,
        tree_density=0.95,
        creature_weights={
            "monkey": 0.20, "parrot": 0.20, "jaguar": 0.05,
            "tree_frog": 0.20, "insect": 0.25, "snake": 0.10,
        },
        resource_weights={
            "hardwood": 0.30, "fruit": 0.25, "herb": 0.20,
            "vine": 0.10, "resin": 0.10, "shallow_mineral": 0.05,
        },
    ),
    BiomeType.TROPICAL_SAVANNA: BiomeTemplate(
        biome_type=BiomeType.TROPICAL_SAVANNA,
        climate_zone=ClimateZone.TROPICAL_SAVANNA,
        water_ratio=0.05,
        mountain_ratio=0.05,
        tree_density=0.2,
        creature_weights={
            "zebra": 0.20, "antelope": 0.20, "lion": 0.05,
            "elephant": 0.05, "bird": 0.20, "insect": 0.30,
        },
        resource_weights={
            "softwood": 0.20, "grass_fiber": 0.30, "herb": 0.20,
            "stone": 0.15, "shallow_mineral": 0.15,
        },
    ),
    BiomeType.DESERT: BiomeTemplate(
        biome_type=BiomeType.DESERT,
        climate_zone=ClimateZone.DESERT,
        water_ratio=0.01,
        mountain_ratio=0.10,
        tree_density=0.02,
        creature_weights={
            "lizard": 0.30, "rodent": 0.25, "nocturnal_predator": 0.10,
            "venomous_insect": 0.20, "bird": 0.10, "snake": 0.05,
        },
        resource_weights={
            "exposed_mineral": 0.35, "sand": 0.25, "succulent": 0.15,
            "oasis_water": 0.05, "stone": 0.20,
        },
    ),
    BiomeType.STEPPE_SHRUBLAND: BiomeTemplate(
        biome_type=BiomeType.STEPPE_SHRUBLAND,
        climate_zone=ClimateZone.STEPPE,
        water_ratio=0.03,
        mountain_ratio=0.08,
        tree_density=0.15,
        creature_weights={
            "rodent": 0.25, "antelope": 0.15, "nocturnal_predator": 0.10,
            "bird": 0.20, "insect": 0.20, "snake": 0.10,
        },
        resource_weights={
            "exposed_mineral": 0.25, "fiber": 0.25, "succulent": 0.15,
            "shallow_mineral": 0.10, "stone": 0.25,
        },
    ),
    BiomeType.TAIGA: BiomeTemplate(
        biome_type=BiomeType.TAIGA,
        climate_zone=ClimateZone.SUBARCTIC_TAIGA,
        water_ratio=0.10,
        mountain_ratio=0.10,
        tree_density=0.6,
        creature_weights={
            "moose": 0.20, "wolf": 0.15, "bear": 0.10,
            "hare": 0.20, "lynx": 0.05, "bird": 0.20, "insect": 0.10,
        },
        resource_weights={
            "softwood": 0.45, "resin": 0.20, "berry": 0.15,
            "fungus": 0.10, "shallow_mineral": 0.10,
        },
    ),
    BiomeType.TUNDRA: BiomeTemplate(
        biome_type=BiomeType.TUNDRA,
        climate_zone=ClimateZone.POLAR_TUNDRA,
        water_ratio=0.05,
        mountain_ratio=0.10,
        tree_density=0.05,
        creature_weights={
            "caribou": 0.25, "arctic_fox": 0.15, "hare": 0.20,
            "seal": 0.10, "bird": 0.20, "insect": 0.10,
        },
        resource_weights={
            "lichen": 0.35, "berry": 0.20, "moss": 0.20,
            "shallow_mineral": 0.15, "ice": 0.10,
        },
    ),
    BiomeType.ALPINE_MEADOW: BiomeTemplate(
        biome_type=BiomeType.ALPINE_MEADOW,
        climate_zone=ClimateZone.ALPINE,
        water_ratio=0.05,
        mountain_ratio=0.40,
        tree_density=0.15,
        creature_weights={
            "ibex": 0.25, "marmot": 0.20, "eagle": 0.15,
            "hare": 0.20, "insect": 0.20,
        },
        resource_weights={
            "exposed_mineral": 0.30, "herb": 0.25, "stone": 0.25,
            "shallow_mineral": 0.15, "ice": 0.05,
        },
    ),
    # 海洋群系
    BiomeType.WARM_OCEAN: BiomeTemplate(
        biome_type=BiomeType.WARM_OCEAN,
        climate_zone=ClimateZone.EQUATORIAL_RAINFOREST,
        water_ratio=1.0,
        mountain_ratio=0.0,
        tree_density=0.0,
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


# ── 群系分配（纯函数）───────────────────────────────────

# 海平面海拔阈值
_SEA_LEVEL: float = 0.0

# 海洋温度分界 (°C，取海平面温度)
_OCEAN_COLD_CUTOFF: float = 5.0    # < 5°C → 冷水
_OCEAN_WARM_CUTOFF: float = 20.0   # >= 20°C → 暖水
# 中间 → 温带海洋

# 气候档位 → 陆地群系 映射
_CLIMATE_TO_LAND_BIOME: dict[ClimateZone, BiomeType] = {
    ClimateZone.EQUATORIAL_RAINFOREST: BiomeType.TROPICAL_RAINFOREST,
    ClimateZone.TROPICAL_SAVANNA: BiomeType.TROPICAL_SAVANNA,
    ClimateZone.DESERT: BiomeType.DESERT,
    ClimateZone.STEPPE: BiomeType.STEPPE_SHRUBLAND,
    ClimateZone.TEMPERATE_FOREST: BiomeType.TEMPERATE_DECIDUOUS_FOREST,
    ClimateZone.SUBARCTIC_TAIGA: BiomeType.TAIGA,
    ClimateZone.POLAR_TUNDRA: BiomeType.TUNDRA,
    ClimateZone.ALPINE: BiomeType.ALPINE_MEADOW,
}


def biome_from_attrs(
    mean_temp: float,
    annual_rainfall: float,
    altitude: float,
    sea_level_temp: float,
    moisture_noise: float = 0.0,
) -> BiomeType:
    """根据连续气候属性分配群系。

    判定顺序:
      1. 海拔 <0 → 海洋（按海平面温度分暖/温/冷）
      2. 海拔 >=0 → 陆地（classify 得气候档位再映群系）

    群系从连续属性经 classify 映射，档位边界由连续场决定。
    moisture_noise 当前保留供未来群系内部细分使用。

    Args:
        mean_temp: 年均温度 (°C)。
        annual_rainfall: 年降雨量 (mm)。
        altitude: 实际海拔 (m)。
        sea_level_temp: 海平面温度 (°C)，用于海洋温度分类。
        moisture_noise: 湿度相关次级噪声 [-1, 1]（预留）。

    Returns:
        群系类型。
    """
    # ── 海洋判定 ──────────────────────────────────────
    if altitude < _SEA_LEVEL:
        if sea_level_temp >= _OCEAN_WARM_CUTOFF:
            return BiomeType.WARM_OCEAN
        elif sea_level_temp >= _OCEAN_COLD_CUTOFF:
            return BiomeType.TEMPERATE_OCEAN
        else:
            return BiomeType.COLD_OCEAN

    # ── 陆地判定 ──────────────────────────────────────
    climate = classify(mean_temp, annual_rainfall, altitude)
    return _CLIMATE_TO_LAND_BIOME.get(
        climate, BiomeType.TEMPERATE_DECIDUOUS_FOREST
    )


def biome_from_climate(
    climate: ClimateZone,
    moisture_noise: float,
    altitude: float,
    sea_level_temp: float,
) -> BiomeType:
    """兼容旧 API：由气候档位分配群系。

    内部转调 biome_from_attrs。注意：旧接口未提供温度/降雨，
    无法精确重算 classify；此处用气候档位反查映射，海洋仍按海拔+海平面温度判定。
    新代码应直接使用 biome_from_attrs。

    Args:
        climate: 气候档位（陆地分支用其映射）。
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

    return _CLIMATE_TO_LAND_BIOME.get(
        climate, BiomeType.TEMPERATE_DECIDUOUS_FOREST
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
