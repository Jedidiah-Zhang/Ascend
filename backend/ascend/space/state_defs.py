"""地形状态定义 — 状态注册表 + 实体遮蔽规格（纯数据，无算法）。

状态是 TileGrid 上的动态叠加层（湿润/覆雪/结冰），由统一天气场驱动。
本模块声明"有哪些状态"（STATE_TYPES）与实体遮蔽规格（COVERAGE_SPECS）；
地形 × 状态的演化参数矩阵在 terrain.TERRAIN_DEFS（每个地形定义自带
states 行，引用本模块的 StateParams 模板或自定义）。
演化/涂抹/结算算法在 tile_state.py（统一内核由注册表驱动）。

增删状态 = STATE_TYPES 加/减一行 + bump TileGrid.TILE_GRID_VERSION
+ 前端 STATE_KEYS 同步——序列化/涂抹/结算代码由注册表驱动，零改动。
加一个地形 = terrain.TERRAIN_DEFS 加一行（含 states 行），零算法改动。
"""

from dataclasses import dataclass
from typing import Callable, Mapping


@dataclass(frozen=True, slots=True)
class StateParams:
    """状态演化参数（统一内核系数，按游戏日标定）。

    Attributes:
        deposit: 降水沉积系数——每单位降水（mm/日）产生的状态增量。
        drain: 排水系数——坡度排水衰减（state × drain × (1+slope)）。
        melt: 温度衰减系数——state × melt × max(0, T−melt_above)。
        freeze: 低温冻结系数——(freeze_below−T) × freeze。
    """

    deposit: float = 0.0
    drain: float = 0.0
    melt: float = 0.0
    freeze: float = 0.0


@dataclass(frozen=True, slots=True)
class StateConfig:
    """状态注册项 — 存储属性 + 统一内核激活条件（数据化）。

    Attributes:
        key: 状态名（跨前后端契约；blob 数组顺序 = STATE_TYPES 注册顺序）。
        bounds: 语义范围 (min, max)，写入时 clamp。
        dtype: array 类型码（B=uint8 起步）。
        overrides_passability: 是否覆盖基底通行性（仅结冰授权）。
        thresholds: 叙事/行为阈值阶梯（跨阈发事件，升序）。
        precip_trigger: 降水沉积触发类型（"rain"/"snow"/None）。
        freeze_below: 低温冻结激活门限（低于此值开始冻结）。
        melt_above: 高温衰减激活门限（高于此值开始衰减；None=0）。
        apply: 扩展点——套不上通用内核的自定义演化函数
            (state, params, ...) -> delta；None=通用内核。
    """

    key: str
    bounds: tuple[int, int]
    dtype: str = "B"
    overrides_passability: bool = False
    thresholds: tuple[int, ...] = ()
    precip_trigger: str | None = None
    freeze_below: float | None = None
    melt_above: float | None = None
    apply: Callable | None = None


# ── 状态注册表（单一事实源）──────────────────────────────
# 序列化布局、TileGrid 状态数组、结算/涂抹循环全部由此驱动。
# 不适用规则（岩石无湿润、水面先结冰等）由 terrain.TERRAIN_DEFS
# 每行 states 按基底逐型声明（None=不适用），不在本表表达。

STATE_TYPES: dict[str, StateConfig] = {
    "moisture": StateConfig(
        key="moisture",
        bounds=(0, 100),
        precip_trigger="rain",
    ),
    "snow": StateConfig(
        key="snow",
        bounds=(0, 255),
        precip_trigger="snow",
        melt_above=0.0,
        thresholds=(15, 30, 50),
    ),
    "ice": StateConfig(
        key="ice",
        bounds=(0, 255),
        overrides_passability=True,
        freeze_below=0.0,
        melt_above=0.0,
    ),
}


def state_keys() -> tuple[str, ...]:
    """状态注册顺序（blob 布局与前端 STATE_KEYS 的契约基准）。"""
    return tuple(STATE_TYPES)


# ── 实体遮蔽规格（覆盖度 = 实体层属性，非 tile 状态） ──────────
# 建筑/树冠下方不按常规沉积：状态引擎查询遮蔽系数后乘以沉积量。
# 实体标识 = EntityType.name 或实体 data 标记（如 {"canopy": true}）。


@dataclass(frozen=True, slots=True)
class CoverageSpec:
    """遮蔽规格（对状态沉积的乘法因子，1.0=露天无遮蔽）。

    Attributes:
        snow: 覆雪沉积倍率（建筑全遮蔽 0、树冠 0.3）。
        moisture: 湿润沉积倍率（建筑全遮蔽 0、树冠 0.8）。
    """

    snow: float = 1.0
    moisture: float = 1.0


COVERAGE_SPECS: dict[str, CoverageSpec] = {
    "STRUCTURE": CoverageSpec(snow=0.0, moisture=0.0),  # 建筑全遮蔽
    "canopy": CoverageSpec(snow=0.3, moisture=0.8),     # 树冠部分遮蔽
}


def coverage_for(
    entity_type_name: str,
    data: Mapping | None = None,
) -> CoverageSpec:
    """查询实体遮蔽规格：STRUCTURE 全遮蔽；data 带 canopy 标记的部分遮蔽。

    Args:
        entity_type_name: EntityType.name（如 "STRUCTURE"）。
        data: 实体附加数据（可含 "canopy": true）。

    Returns:
        CoverageSpec（未知实体 = 露天 1.0）。
    """
    if entity_type_name == "STRUCTURE":
        return COVERAGE_SPECS["STRUCTURE"]
    if data and data.get("canopy"):
        return COVERAGE_SPECS["canopy"]
    return CoverageSpec()