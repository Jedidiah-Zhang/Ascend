"""群系生态 — 群系类型枚举、模板数据和分配逻辑。

群系由气候档位 + 次级噪声决定。模板定义地形、生物、资源参数。
分配逻辑为纯函数，线程安全。
"""

from dataclasses import dataclass, field
from enum import IntEnum

from .climate import ClimateZone


class BiomeType(IntEnum):
    """群系类型。

    初始两个基础群系，后续按需扩展。
    """

    TEMPERATE_DECIDUOUS_FOREST = (0, "温带落叶林")
    ARID_SHRUBLAND = (1, "干旱灌木地")
    # 后续扩展:
    # TROPICAL_RAINFOREST = (2, "热带雨林")
    # COLD_TUNDRA = (3, "寒带苔原")
    # SWAMP = (4, "沼泽湿地")
    # ALPINE = (5, "高山")

    def __new__(cls, value: int, label: str) -> "BiomeType":
        """重写 __new__ 以支持双参数 (value, label)。"""
        obj = int.__new__(cls, value)
        obj._value_ = value
        obj.label = label
        return obj

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

    # 地形构成
    water_ratio: float = 0.05
    mountain_ratio: float = 0.05

    # 植被
    tree_density: float = 0.5

    # 生物权重（待生物系统实现后扩展为结构化列表）
    creature_weights: dict[str, float] = field(default_factory=dict)

    # 资源权重
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
            "deer": 0.30,
            "rabbit": 0.20,
            "wolf": 0.10,
            "bear": 0.05,
            "bird": 0.25,
            "insect": 0.10,
        },
        resource_weights={
            "hardwood": 0.35,
            "softwood": 0.25,
            "berry": 0.15,
            "herb": 0.10,
            "fungus": 0.10,
            "shallow_mineral": 0.05,
        },
    ),
    BiomeType.ARID_SHRUBLAND: BiomeTemplate(
        biome_type=BiomeType.ARID_SHRUBLAND,
        climate_zone=ClimateZone.ARID,
        water_ratio=0.02,
        mountain_ratio=0.08,
        tree_density=0.1,
        creature_weights={
            "lizard": 0.30,
            "rodent": 0.25,
            "nocturnal_predator": 0.10,
            "venomous_insect": 0.20,
            "bird": 0.10,
            "snake": 0.05,
        },
        resource_weights={
            "exposed_mineral": 0.30,
            "fiber": 0.25,
            "succulent": 0.20,
            "oasis_water": 0.05,
            "stone": 0.20,
        },
    ),
}


# ── 群系分配（纯函数）───────────────────────────────────

def biome_from_climate(
    climate: ClimateZone,
    moisture_noise: float,
    altitude_noise: float,
) -> BiomeType:
    """根据气候档位和次级噪声分配群系类型。

    当前只有两个基础群系：温带→落叶林，干旱带→灌木地。
    后续气候档位扩展时，同一档位内的多个群系由 moisture_noise 和
    altitude_noise 进一步区分。

    Args:
        climate: 气候档位。
        moisture_noise: 湿度相关次级噪声 [-1, 1]。
        altitude_noise: 海拔相关次级噪声 [-1, 1]。

    Returns:
        群系类型。
    """
    if climate == ClimateZone.TEMPERATE:
        # 后续可用 moisture_noise 区分温带落叶林 vs 温带草原
        return BiomeType.TEMPERATE_DECIDUOUS_FOREST
    if climate == ClimateZone.ARID:
        # 后续可用 altitude_noise 区分沙漠 vs 灌木地
        return BiomeType.ARID_SHRUBLAND
    if climate == ClimateZone.TROPICAL:
        # 热带雨林（待实现）
        return BiomeType.TEMPERATE_DECIDUOUS_FOREST  # fallback
    if climate == ClimateZone.COLD:
        # 寒带苔原（待实现）
        return BiomeType.ARID_SHRUBLAND  # fallback
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
