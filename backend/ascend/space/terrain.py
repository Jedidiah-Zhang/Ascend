"""地形类型定义 — 详细地图层每米格子的地面类型。

TerrainType 是 IntEnum，可直接存入 array('H') 紧凑存储。
TerrainProps 携带游戏性相关属性，通过 lookup 查询。
"""

from dataclasses import dataclass
from enum import IntEnum


class TerrainType(IntEnum):
    """地形类型 — IntEnum，值存入 TileGrid 的 array('H')。

    值从 0 开始编号，uint16 足以容纳未来扩展。
    """

    GRASSLAND = 0       # 草地 — 最常见的平地
    SAND = 1            # 沙地 — 干旱区域或水边
    FERTILE_SOIL = 2    # 沃土 — 高肥力，适合种植
    ROCK = 3            # 岩石地 — 不可建造，含可采矿
    STEEP_SLOPE = 4     # 陡坡 — 高移动消耗，不可建造
    MOUNTAIN_PEAK = 5   # 山巅 — 不可通行，不可建造
    SHALLOW_WATER = 6   # 浅水 — 可涉水通过，减速
    DEEP_WATER = 7      # 深水 — 不可通行，需游泳基因
    MARSH = 8           # 沼泽 — 湿地，减速，中等肥力


@dataclass(slots=True)
class TerrainProps:
    """地形的游戏性属性。

    Attributes:
        label: 中文名称。
        passable: 实体能否在此地形上通行。
        buildable: 能否在此地形上建造建筑。
        movement_cost: 通过此地形所需的移动消耗倍率（1.0 = 正常）。
        fertility: 土壤肥力 [0, 1]，影响种植产量。
    """

    label: str
    passable: bool
    buildable: bool
    movement_cost: float
    fertility: float


# ── 属性查找表 ──────────────────────────────────────────────

_TERRAIN_PROPS: dict[TerrainType, TerrainProps] = {
    TerrainType.GRASSLAND: TerrainProps(
        label="草地",
        passable=True,
        buildable=True,
        movement_cost=1.0,
        fertility=0.5,
    ),
    TerrainType.SAND: TerrainProps(
        label="沙地",
        passable=True,
        buildable=True,
        movement_cost=1.2,
        fertility=0.2,
    ),
    TerrainType.FERTILE_SOIL: TerrainProps(
        label="沃土",
        passable=True,
        buildable=True,
        movement_cost=1.0,
        fertility=1.0,
    ),
    TerrainType.ROCK: TerrainProps(
        label="岩石地",
        passable=True,
        buildable=False,
        movement_cost=1.5,
        fertility=0.0,
    ),
    TerrainType.STEEP_SLOPE: TerrainProps(
        label="陡坡",
        passable=True,
        buildable=False,
        movement_cost=2.0,
        fertility=0.0,
    ),
    TerrainType.MOUNTAIN_PEAK: TerrainProps(
        label="山巅",
        passable=False,
        buildable=False,
        movement_cost=float("inf"),
        fertility=0.0,
    ),
    TerrainType.SHALLOW_WATER: TerrainProps(
        label="浅水",
        passable=True,
        buildable=False,
        movement_cost=2.5,
        fertility=0.0,
    ),
    TerrainType.DEEP_WATER: TerrainProps(
        label="深水",
        passable=False,
        buildable=False,
        movement_cost=float("inf"),
        fertility=0.0,
    ),
    TerrainType.MARSH: TerrainProps(
        label="沼泽",
        passable=True,
        buildable=False,
        movement_cost=2.0,
        fertility=0.4,
    ),
}


def get_terrain_props(terrain: TerrainType) -> TerrainProps:
    """查询地形属性。

    Args:
        terrain: 地形类型。

    Returns:
        对应的 TerrainProps。若未注册则返回草地属性作为兜底。
    """
    return _TERRAIN_PROPS.get(
        terrain,
        _TERRAIN_PROPS[TerrainType.GRASSLAND],
    )


def is_passable(terrain: TerrainType) -> bool:
    """查询地形是否可行走。

    Args:
        terrain: 地形类型。

    Returns:
        True 表示可通行。
    """
    return _TERRAIN_PROPS[terrain].passable


def is_buildable(terrain: TerrainType) -> bool:
    """查询地形是否可建造。

    Args:
        terrain: 地形类型。

    Returns:
        True 表示可建造。
    """
    return _TERRAIN_PROPS[terrain].buildable


def movement_cost(terrain: TerrainType) -> float:
    """查询地形移动消耗倍率。

    Args:
        terrain: 地形类型。

    Returns:
        移动消耗倍率（1.0 = 正常）。
    """
    return _TERRAIN_PROPS[terrain].movement_cost


def fertility(terrain: TerrainType) -> float:
    """查询地形肥力。

    Args:
        terrain: 地形类型。

    Returns:
        肥力值 [0, 1]。
    """
    return _TERRAIN_PROPS[terrain].fertility
