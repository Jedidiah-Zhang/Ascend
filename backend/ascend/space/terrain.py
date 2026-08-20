"""地形类型定义 — 详细地图层每米格子的地面类型。

单一事实源 = TERRAIN_DEFS 注册表：TerrainType 枚举、游戏性属性、
状态演化参数（原 state_defs.TERRAIN_STATE_PARAMS）、海拔带生成分类、
陡坡重分类豁免全部由它派生。**加一个地形 = 注册表加一行**，
枚举/查询/状态引擎/生成算法零改动（完整性由测试兜底）。

TerrainType 由注册表动态生成（IntEnum）。**枚举值 = 持久化契约**
（chunk BLOB 内 terrain id），已发布值不可改，新地形只能追加。
"""

from dataclasses import dataclass
from enum import IntEnum
from types import MappingProxyType
from typing import Mapping

from ascend.config import (
    BASE_FERTILE_HI,
    BASE_FERTILE_LO,
    BASE_GRASSLAND_CAP,
    BASE_PEAK_THRESHOLD,
    BASE_SAND_CAP,
)
from .state_defs import StateParams


@dataclass(frozen=True, slots=True)
class AltitudeBand:
    """海拔带（生成分类用）——tile 海拔落在 [lo, hi) 且优先级最高时生成该地形。

    Attributes:
        lo: 带下界（None = −∞）。
        hi: 带上界（None = +∞）。
        priority: 带重叠时的优先序（越大越优先）。
        lo_delta / hi_delta: 群系偏移键——TerrainBias 的字段名，
            生成时 lo/hi 各加对应偏移量（None = 无偏移）。
    """

    lo: float | None = None
    hi: float | None = None
    priority: int = 0
    lo_delta: str | None = None
    hi_delta: str | None = None


@dataclass(frozen=True, slots=True)
class TerrainDef:
    """地形完整定义（注册表行，见模块 docstring）。

    Attributes:
        value: 持久化值（BLOB 契约，不可改，只能追加）。
        label: 中文名称。
        passable: 实体能否通行。
        buildable: 能否建造建筑。
        movement_cost: 移动消耗倍率（1.0 = 正常）。
        fertility: 土壤肥力 [0, 1]。
        states: 状态演化参数（None = 不适用，恒 0）；引用模板或自定义。
        water: 水域标志（水域语义：陡坡重分类豁免等）。
        altitude: 海拔带（参与生成分类）；None = 不参与生成。
        no_steep_reclass: 陡坡重分类豁免。
        fallback: 无海拔带命中时的兜底地形（全表唯一，如 SAND）。
    """

    value: int
    label: str
    passable: bool = True
    buildable: bool = True
    movement_cost: float = 1.0
    fertility: float = 0.0
    states: Mapping[str, StateParams | None] = MappingProxyType({})
    water: bool = False
    altitude: AltitudeBand | None = None
    no_steep_reclass: bool = False
    fallback: bool = False


# ── 状态参数模板（定义期引用，无运行时解析链；逐型微调用
#    {**_TMPL, "key": ...} 展开新 dict）───────────────────────

_SOIL: Mapping[str, StateParams | None] = MappingProxyType({
    "moisture": StateParams(deposit=1.6, drain=0.10, melt=0.02),
    "snow": StateParams(deposit=1.0, melt=0.15),
    "ice": None,
})

_SAND: Mapping[str, StateParams | None] = MappingProxyType({
    # 湿润不适用（沙漠语义，沙干得快）；预留：未来"湿沙影响行动"加参数即可
    "moisture": None,
    "snow": StateParams(deposit=1.0, melt=0.15),
    "ice": None,
})

_ROCK: Mapping[str, StateParams | None] = MappingProxyType({
    # 岩石不吸水——"淋湿变深色"是渲染层视觉贴花，不进状态层
    "moisture": None,
    "snow": StateParams(deposit=1.0, melt=0.15),
    "ice": None,
})

_WATER: Mapping[str, StateParams | None] = MappingProxyType({
    "moisture": None,  # 开放水面无湿润（无意义）
    # 覆雪仅冰上承载（设计：水面先结冰，雪落冰上）。简化：不实现
    # 状态间依赖（snow 沉积需 ice>0）——冻结期 ice 数小时内形成，
    # 过渡偏差可忽略；如未来要求精确，加 requires_state 数据字段
    "snow": StateParams(deposit=1.0, melt=0.15),
    "ice": StateParams(freeze=1.5, melt=0.8),
})


# ── 地形注册表（单一事实源） ────────────────────────────────
# 每行 = 完整地形定义；枚举顺序 = value 升序（持久化契约）。
# 海拔带基准值引用 config 调参常量，偏移键对应 TerrainBias 字段。

_TERRAIN_DEFS: dict[str, TerrainDef] = {
    "GRASSLAND": TerrainDef(
        value=0,
        label="草地",
        movement_cost=1.0,
        fertility=0.5,
        states=_SOIL,
        altitude=AltitudeBand(
            lo=BASE_SAND_CAP, hi=BASE_GRASSLAND_CAP,
            priority=7, lo_delta="sand_cap_delta",
        ),
    ),
    "SAND": TerrainDef(
        value=1,
        label="沙地",
        movement_cost=1.2,
        fertility=0.2,
        states=_SAND,
        altitude=AltitudeBand(
            lo=0.0, hi=BASE_SAND_CAP, priority=6, hi_delta="sand_cap_delta",
        ),
        no_steep_reclass=True,
        fallback=True,
    ),
    "FERTILE_SOIL": TerrainDef(
        value=2,
        label="沃土",
        fertility=1.0,
        states=_SOIL,
        altitude=AltitudeBand(
            lo=BASE_FERTILE_LO, hi=BASE_FERTILE_HI, priority=8,
            lo_delta="fertile_shift", hi_delta="fertile_shift",
        ),
    ),
    "ROCK": TerrainDef(
        value=3,
        label="岩石地",
        buildable=False,
        movement_cost=1.5,
        states=_ROCK,
        altitude=AltitudeBand(
            lo=BASE_GRASSLAND_CAP, hi=BASE_PEAK_THRESHOLD,
            priority=9, lo_delta="rock_threshold_delta",
            hi_delta="peak_threshold_delta",  # 与 PEAK lo 共享偏移键，防缝隙
        ),
    ),
    "STEEP_SLOPE": TerrainDef(
        value=4,
        label="陡坡",
        buildable=False,
        movement_cost=2.0,
        states=_ROCK,
    ),
    "MOUNTAIN_PEAK": TerrainDef(
        value=5,
        label="山巅",
        passable=False,
        buildable=False,
        movement_cost=float("inf"),
        states=_ROCK,
        altitude=AltitudeBand(
            lo=BASE_PEAK_THRESHOLD, priority=10, lo_delta="peak_threshold_delta",
        ),
        no_steep_reclass=True,
    ),
    "SHALLOW_WATER": TerrainDef(
        value=6,
        label="浅水",
        buildable=False,
        movement_cost=2.5,
        states=_WATER,
        water=True,
        altitude=AltitudeBand(lo=-100.0, hi=0.0, priority=2),
    ),
    "DEEP_WATER": TerrainDef(
        value=7,
        label="深水",
        passable=False,
        buildable=False,
        movement_cost=float("inf"),
        states=_WATER,
        water=True,
        altitude=AltitudeBand(hi=-100.0, priority=1),
    ),
    "MARSH": TerrainDef(
        value=8,
        label="沼泽",
        buildable=False,
        movement_cost=2.0,
        fertility=0.4,
        # 沼泽：排水慢（湿地语义），湿润保留更久
        states=MappingProxyType({
            **_SOIL, "moisture": StateParams(deposit=1.6, drain=0.03, melt=0.02),
        }),
    ),
}

# 注册表不可变视图（外部只读，防误改契约）
TERRAIN_DEFS: Mapping[str, TerrainDef] = MappingProxyType(_TERRAIN_DEFS)

# 注册表动态生成枚举：成员 = 注册表键，值 = value 升序（持久化契约）
TerrainType = IntEnum(
    "TerrainType",
    {
        name: d.value
        for name, d in sorted(_TERRAIN_DEFS.items(), key=lambda kv: kv[1].value)
    },
)

WATER_TYPES: frozenset = frozenset(
    t for t in TerrainType if _TERRAIN_DEFS[t.name].water
)


def get_terrain_def(terrain: TerrainType) -> TerrainDef:
    """查询地形完整定义（注册表直查——枚举成员必在注册表中）。

    Args:
        terrain: 地形类型。

    Returns:
        对应的 TerrainDef。
    """
    return _TERRAIN_DEFS[terrain.name]


def is_passable(terrain: TerrainType) -> bool:
    """查询地形是否可行走。"""
    return _TERRAIN_DEFS[terrain.name].passable


def is_buildable(terrain: TerrainType) -> bool:
    """查询地形是否可建造。"""
    return _TERRAIN_DEFS[terrain.name].buildable


def movement_cost(terrain: TerrainType) -> float:
    """查询地形移动消耗倍率（1.0 = 正常）。"""
    return _TERRAIN_DEFS[terrain.name].movement_cost


def fertility(terrain: TerrainType) -> float:
    """查询地形肥力 [0, 1]。"""
    return _TERRAIN_DEFS[terrain.name].fertility


def state_params(terrain: TerrainType, key: str) -> StateParams | None:
    """查询地形 × 状态 的演化参数（注册表直查，无解析链）。

    Args:
        terrain: 地形类型。
        key: 状态名（STATE_TYPES 的键）。

    Returns:
        StateParams；None = 状态对该地形不适用（恒 0）。

    Raises:
        KeyError: 注册表该地形漏声明状态——fail fast（矩阵测试兜底）。
    """
    return _TERRAIN_DEFS[terrain.name].states[key]
