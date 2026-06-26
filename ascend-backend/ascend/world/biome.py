"""群系生态 — 群系类型枚举、模板数据和分配逻辑。

群系 = 海拔判定（陆地/海洋）+ 气候/温度 + 次级噪声。
分配逻辑为纯函数，线程安全。
"""

from dataclasses import dataclass, field
from enum import IntEnum

from .climate import ClimateZone


class BiomeType(IntEnum):
    """群系类型。

    海洋群系由海拔 <0 判定，温度分暖/温/冷三档。
    """

    # 陆地
    TEMPERATE_DECIDUOUS_FOREST = (0, "温带落叶林")
    ARID_SHRUBLAND = (1, "干旱灌木地")
    # 后续: TROPICAL_RAINFOREST, COLD_TUNDRA, SWAMP, ALPINE

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


# ── 基础群系模板注册表 ──────────────────────────────────

_BIOME_TEMPLATES: dict[BiomeType, BiomeTemplate] = {
    BiomeType.TEMPERATE_DECIDUOUS_FOREST: BiomeTemplate(
        biome_type=BiomeType.TEMPERATE_DECIDUOUS_FOREST,
        climate_zone=ClimateZone.TEMPERATE,
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
    BiomeType.ARID_SHRUBLAND: BiomeTemplate(
        biome_type=BiomeType.ARID_SHRUBLAND,
        climate_zone=ClimateZone.ARID,
        water_ratio=0.02,
        mountain_ratio=0.08,
        tree_density=0.1,
        creature_weights={
            "lizard": 0.30, "rodent": 0.25, "nocturnal_predator": 0.10,
            "venomous_insect": 0.20, "bird": 0.10, "snake": 0.05,
        },
        resource_weights={
            "exposed_mineral": 0.30, "fiber": 0.25, "succulent": 0.20,
            "oasis_water": 0.05, "stone": 0.20,
        },
    ),
    # 海洋群系
    BiomeType.WARM_OCEAN: BiomeTemplate(
        biome_type=BiomeType.WARM_OCEAN,
        climate_zone=ClimateZone.TROPICAL,
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
        climate_zone=ClimateZone.TEMPERATE,
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
        climate_zone=ClimateZone.COLD,
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


def biome_from_climate(
    climate: ClimateZone,
    moisture_noise: float,
    altitude: float,
    sea_level_temp: float,
) -> BiomeType:
    """根据气候档位、海拔和海平面温度分配群系。

    判定顺序:
      1. 海拔 <0 → 海洋（按海平面温度分暖/温/冷）
      2. 海拔 >=0 → 陆地（按气候档位 + 次级噪声）

    Args:
        climate: 气候档位。
        moisture_noise: 湿度相关次级噪声 [-1, 1]。
        altitude: 实际海拔 (m)。
        sea_level_temp: 海平面温度 (°C)，用于海洋温度分类。

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
    if climate == ClimateZone.TEMPERATE:
        return BiomeType.TEMPERATE_DECIDUOUS_FOREST
    if climate == ClimateZone.ARID:
        return BiomeType.ARID_SHRUBLAND
    if climate == ClimateZone.TROPICAL:
        # 热带雨林（待实现）
        return BiomeType.TEMPERATE_DECIDUOUS_FOREST
    if climate == ClimateZone.COLD:
        # 寒带苔原/针叶林（待实现）
        return BiomeType.TEMPERATE_DECIDUOUS_FOREST
    return BiomeType.TEMPERATE_DECIDUOUS_FOREST


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
