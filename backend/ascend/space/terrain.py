"""地形类型定义 — 详细地图层每米格子的地表材质。

单一事实源 = data/terrain.json：TerrainType 枚举、游戏性属性、
状态演化参数（地形 × 状态矩阵）全部由它派生。**加一个材质 =
数据文件加一项（value 追加）**，枚举/查询/状态引擎/生成算法
零改动（完整性由测试兜底）。

issue #42 语义收窄：TerrainType 只表达**地表材质**（8 种），
地貌形态（山巅/陡坡）与水体深浅不再进枚举——海拔/坡度/水深
均为场（宏观海拔 / 面内坡度 / elevation 反推水深），从场推导。

身份约定（Mod 三层基础设施第 1 层，从首发即启用命名空间）：
- **注册表键 / 持久化标识 = 命名空间 id**（`<ns>:<local>`，如
  `ascend:grassland`），ns 天然隔离不同来源，避免撞车。
- **枚举值 = 持久化契约**（chunk BLOB 内 terrain id）：显式声明、
  全局唯一且连续 0..n-1，已发布值不可改，新材质只能追加（value=n）。
- 枚举成员名 = local 大写（`TerrainType.GRASSLAND`）——仅供代码
  书写，解析经 _ENUM_NAME_TO_NS 回到命名空间 id。
"""

from dataclasses import dataclass
from enum import IntEnum
from types import MappingProxyType
from typing import Mapping

from ascend.data import load_content, split_ns_id
from ascend.config import WATER_WADE_COST, WATER_WADE_DEPTH_M
from .state_defs import STATE_TYPES, StateParams


@dataclass(frozen=True, slots=True)
class TerrainDef:
    """材质完整定义（注册表行，见模块 docstring）。

    Attributes:
        ns_id: 命名空间 id（`<ns>:<local>`，注册表键 / 持久化标识）。
        value: 持久化值（BLOB 契约；显式声明，唯一且连续，只能追加）。
        label_key: i18n 键（显示名，文案在 lang/*.json）。
        passable: 材质面级通行基线（True = 可落脚；水面由
            water_passability 按水深实时推导覆盖）。
        buildable: 能否建造建筑（面内坡度等派生约束推后到 #30）。
        movement_cost: 移动消耗倍率（1.0 = 正常；"inf" = 不可通行）。
        fertility: 土壤肥力 [0, 1]。
        states: 状态演化参数（None = 不适用，恒 0）。
        water: 水域标志（水体语义：结冰状态 / 岸边过渡豁免等）。
    """

    ns_id: str
    value: int
    label_key: str
    passable: bool = True
    buildable: bool = True
    movement_cost: float = 1.0
    fertility: float = 0.0
    states: Mapping[str, StateParams | None] = MappingProxyType({})
    water: bool = False


def _local_name(ns_id: str) -> str:
    """命名空间 id 的 local 部分（枚举成员名来源，大写；非法格式 fail fast）。"""
    return split_ns_id(ns_id)[1].upper()


def _opt_float(raw: object) -> float | None:
    if raw is None:
        return None
    return float(raw)


def _parse_cost(raw: object) -> float:
    """移动消耗：数字或 "inf"（不可通行，JSON 无无穷字面量）。"""
    if isinstance(raw, str) and raw == "inf":
        return float("inf")
    return float(raw)


def _parse_state_params(raw: object) -> StateParams | None:
    if raw is None:
        return None
    if not isinstance(raw, Mapping):
        raise ValueError(f"states 参数必须是对象或 null，got {raw!r}")
    return StateParams(
        deposit=float(raw.get("deposit", 0.0)),
        drain=float(raw.get("drain", 0.0)),
        melt=float(raw.get("melt", 0.0)),
        freeze=float(raw.get("freeze", 0.0)),
    )


def _parse_terrain_def(ns_id: str, raw: Mapping) -> TerrainDef:
    """单行 JSON → TerrainDef（含命名空间格式与状态矩阵完整性校验）。"""
    split_ns_id(ns_id)  # 非法格式 fail fast（返回值未用，local 经 _local_name）
    if "value" not in raw or "label_key" not in raw:
        raise ValueError(f"{ns_id}: 缺少必需字段 value/label_key")
    states_raw = raw.get("states", {})
    if not isinstance(states_raw, Mapping):
        raise ValueError(f"{ns_id}: states 必须是对象")
    missing = sorted(set(STATE_TYPES) - set(states_raw))
    if missing:
        raise ValueError(f"{ns_id}: states 漏声明 {missing}")
    unknown = sorted(set(states_raw) - set(STATE_TYPES))
    if unknown:
        raise ValueError(f"{ns_id}: states 含未知状态 {unknown}")
    states = MappingProxyType({
        key: _parse_state_params(states_raw[key]) for key in STATE_TYPES
    })
    return TerrainDef(
        ns_id=ns_id,
        value=int(raw["value"]),
        label_key=str(raw["label_key"]),
        passable=bool(raw.get("passable", True)),
        buildable=bool(raw.get("buildable", True)),
        movement_cost=_parse_cost(raw.get("movement_cost", 1.0)),
        fertility=float(raw.get("fertility", 0.0)),
        states=states,
        water=bool(raw.get("water", False)),
    )


def _build_terrain_defs(doc: Mapping) -> dict[str, TerrainDef]:
    """data/terrain.json → 注册表，校验持久化契约。

    契约：value 显式声明，全局唯一且连续 0..n-1（已发布值不可改，
    新材质只能追加 value=n）；local 名全局唯一（枚举成员名来源）。
    校验失败 import 期 fail fast。
    """
    raw_map = doc.get("terrain")
    if not isinstance(raw_map, Mapping) or not raw_map:
        raise ValueError("data/terrain.json: 缺少 terrain 注册表")
    defs = {
        ns_id: _parse_terrain_def(ns_id, raw)
        for ns_id, raw in raw_map.items()
    }
    values = [d.value for d in defs.values()]
    if len(set(values)) != len(values):
        by_value: dict[int, list[str]] = {}
        for ns_id, d in defs.items():
            by_value.setdefault(d.value, []).append(ns_id)
        dup = {
            v: names for v, names in by_value.items() if len(names) > 1
        }
        raise ValueError(
            f"材质 value 冲突 {dup}（持久化契约要求唯一）"
        )
    if sorted(values) != list(range(len(defs))):
        raise ValueError(
            f"材质 value 不连续（契约要求 0..{len(defs) - 1}，got {sorted(values)}）"
        )
    local_names = [_local_name(ns) for ns in defs]
    if len(set(local_names)) != len(local_names):
        raise ValueError("材质 local 名重复（枚举成员名来源，须唯一）")
    return defs


# ── 地形注册表（单一事实源，加载自 data/terrain.json）────────────
# 每行 = 完整材质定义；注册表键 = 命名空间 id，value 显式声明
# （唯一 + 连续 0..n-1，契约）。加载失败在 import 期 fail fast。

_TERRAIN_DEFS: dict[str, TerrainDef] = _build_terrain_defs(
    load_content("terrain")
)

# 注册表不可变视图（外部只读，防误改契约）
TERRAIN_DEFS: Mapping[str, TerrainDef] = MappingProxyType(_TERRAIN_DEFS)

# 枚举成员名（local 大写）→ 命名空间 id（代码书写名 → 注册表键）
_ENUM_NAME_TO_NS: Mapping[str, str] = MappingProxyType({
    _local_name(ns): ns for ns in _TERRAIN_DEFS
})

# 注册表动态生成枚举：成员 = local 大写（代码书写），值 = value 升序
TerrainType = IntEnum(
    "TerrainType",
    {
        _local_name(ns): d.value
        for ns, d in sorted(_TERRAIN_DEFS.items(), key=lambda kv: kv[1].value)
    },
)


def terrain_by_id(ns_id: str) -> TerrainType:
    """命名空间 id → 枚举成员（注册表键 → 代码书写名）。"""
    return TerrainType[_local_name(ns_id)]


def terrain_ns_id(terrain: TerrainType) -> str:
    """枚举成员 → 命名空间 id（代码书写名 → 注册表键）。"""
    return _ENUM_NAME_TO_NS[terrain.name]


def get_terrain_def(terrain: TerrainType) -> TerrainDef:
    """查询材质完整定义（注册表直查——枚举成员必在注册表中）。

    Args:
        terrain: 地形类型。

    Returns:
        对应的 TerrainDef。
    """
    return _TERRAIN_DEFS[_ENUM_NAME_TO_NS[terrain.name]]


def is_passable(terrain: TerrainType) -> bool:
    """查询材质通行基线（水面按水深实时推导，见 water_passability）。"""
    return _TERRAIN_DEFS[_ENUM_NAME_TO_NS[terrain.name]].passable


def is_buildable(terrain: TerrainType) -> bool:
    """查询材质是否可建造。"""
    return _TERRAIN_DEFS[_ENUM_NAME_TO_NS[terrain.name]].buildable


def movement_cost(terrain: TerrainType) -> float:
    """查询材质移动消耗倍率（1.0 = 正常）。"""
    return _TERRAIN_DEFS[_ENUM_NAME_TO_NS[terrain.name]].movement_cost


def fertility(terrain: TerrainType) -> float:
    """查询材质肥力 [0, 1]。"""
    return _TERRAIN_DEFS[_ENUM_NAME_TO_NS[terrain.name]].fertility


def state_params(terrain: TerrainType, key: str) -> StateParams | None:
    """查询材质 × 状态 的演化参数（注册表直查，无解析链）。

    Args:
        terrain: 地形类型。
        key: 状态名（STATE_TYPES 的键）。

    Returns:
        StateParams；None = 状态对该材质不适用（恒 0）。

    Raises:
        KeyError: 注册表该材质漏声明状态——fail fast（矩阵测试兜底）。
    """
    return _TERRAIN_DEFS[_ENUM_NAME_TO_NS[terrain.name]].states[key]


def water_passability(terrain: TerrainType, elevation: float) -> tuple[bool, float]:
    """水体通行性按水深实时推导（issue #42：无 depth 通道，从场反推）。

    非水体返回注册表基线。水体（WATER）：水深 = −海拔（海洋；湖底
    高于海平面时 depth=0 视为可涉水——湖泊精确水深需湖面高程，推后
    到玩法阶段）。水深 ≤ WATER_WADE_DEPTH_M 可涉水，否则不可通行。

    **消费方注意**：水面通行必须走本函数（实时按水深推导）；注册表
    is_passable/movement_cost 的 WATER 基线 = 不可通行（保守默认），
    直接查基线会误判浅水可涉水，勿绕过本函数。

    Args:
        terrain: 地形类型。
        elevation: tile 海拔 (m)。

    Returns:
        (可通行, 移动消耗倍率)。
    """
    if terrain != TerrainType.WATER:
        defn = get_terrain_def(terrain)
        return defn.passable, defn.movement_cost
    depth = max(0.0, -elevation)
    if depth <= WATER_WADE_DEPTH_M:
        return True, WATER_WADE_COST
    return False, float("inf")
