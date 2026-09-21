"""守恒演练模块 — 流量 / 守恒不变量 / 多分辨率。

两级 lattice（流域 → 地块）+ 逐帧流量机制 + 跨槽位守恒不变量：

- **多分辨率**：地块经 level 关系 restrict 读所属流域库存；流域经 prolong
  聚合地块流出量（sum）；
- **流量**：地块本帧入流 = ``min(rate, 流域库存, 容量 − 地块库存)``（上界由
  声明参数给出，入流下界 0）；
- **守恒**：所有地块库存 + 所有流域库存 == 声明总量（跨槽位不变量，reject）；
- **界内**：库存与流量非负（record）。

稳态：入流把流域库存抽干、灌满地块（容量 10 × 4 地块 = 总量 40）。

时序：flow（读上一帧库存）→ apply（地块入库）→ drain（流域扣减），
逐帧总量不变（每一单位的移动都在同一帧内配对）。
"""

from __future__ import annotations

from olam.meta.declarations import (
    InstanceDecl,
    InvariantDecl,
    MechanismDecl,
    ModulePack,
    ParameterDecl,
    Parent,
    Permissions,
    RelationDecl,
    SlotDecl,
    ValueDomain,
    When,
    Witness,
)

__all__ = ["BASIN_INITIAL", "MODULE", "TOTAL_WATER"]

BASIN_COUNT = 2
PLOT_COUNT = 4
BASIN_INITIAL = 20
#: 声明总量（守恒不变量的事实源；与初值一致由测试锁定）。
TOTAL_WATER = BASIN_COUNT * BASIN_INITIAL

_INT = ValueDomain(kind="int", bits=64)
_STOCK = ValueDomain(kind="int", bits=64, minimum=0)
_WRITE = Permissions(intervene=True, observe=True, record=True)
_OBSERVE = Permissions(observe=True, record=True)

GLOBAL = InstanceDecl(id="global", kind="global", identity="singleton")
BASIN = InstanceDecl(
    id="lattice.basin", kind="lattice", identity="xy",
    size=(BASIN_COUNT,), axes=("x",),
)
PLOT = InstanceDecl(
    id="lattice.plot", kind="lattice", identity="xy",
    size=(PLOT_COUNT,), axes=("x",),
    parent="lattice.basin", ratio=2,
)

RELATION = RelationDecl(
    id="plot.of_basin", kind="level",
    source="lattice.plot", target="lattice.basin",
)

PARAMETER_RATE = "water.flow.rate"
PARAMETER_CAPACITY = "water.plot.capacity"


def _plot_flow(ctx) -> int:
    """地块入流：速率、流域库存、容量余量三者取最小（下界 0）。"""
    basin = int(ctx.parent("basin"))
    stock = int(ctx.parent("stock"))
    rate = int(ctx.param(PARAMETER_RATE))
    capacity = int(ctx.param(PARAMETER_CAPACITY))
    return max(0, min(rate, basin, capacity - stock))


def _plot_apply(ctx) -> int:
    """地块入库：上一帧库存 + 本帧入流。"""
    return int(ctx.parent("stock")) + int(ctx.parent("inflow"))


def _basin_drain(ctx) -> int:
    """流域扣减：上一帧库存 − 本帧全部子地块流出（prolong sum）。"""
    return int(ctx.parent("stock")) - int(ctx.parent("outflow"))


def _conservation(view) -> bool:
    """总量守恒：地块库存 + 流域库存 == 声明总量（跨槽位视图）。"""
    plots = sum(view["water.plot.stock"].values())
    basins = sum(view["water.basin.stock"].values())
    return plots + basins == TOTAL_WATER


def _bounds(view) -> bool:
    """界内：库存与流量非负（容量上界由流量机制的取值保证）。"""
    return all(
        value >= 0
        for slot_id in ("water.plot.stock", "water.basin.stock",
                        "water.plot.inflow")
        for value in view[slot_id].values()
    )


MODULE = ModulePack(
    id="drill.conservation",
    version="1",
    instances=(GLOBAL, BASIN, PLOT),
    relations=(RELATION,),
    slots=(
        SlotDecl(
            id="water.basin.stock", on="lattice.basin", persist="state",
            domain=_STOCK, permissions=_WRITE, writer="water.basin.drain",
            access_interventions=("node", "persistent"),
            observation_protocols=("research.full.v1",),
            research_trace=True,
            initial=BASIN_INITIAL, role="mechanism_state",
            schedule="on_conservation_tick", quantization="integer",
            metric="discrete",
        ),
        SlotDecl(
            id="water.plot.stock", on="lattice.plot", persist="state",
            domain=_STOCK, permissions=_WRITE, writer="water.plot.apply",
            access_interventions=("node", "persistent"),
            observation_protocols=("research.full.v1",),
            research_trace=True,
            initial=0, role="mechanism_state",
            schedule="on_conservation_tick", quantization="integer",
            metric="discrete",
        ),
        SlotDecl(
            id="water.plot.inflow", on="lattice.plot", persist="state",
            domain=_STOCK, permissions=_OBSERVE, writer="water.plot.flow",
            observation_protocols=("research.full.v1",),
            research_trace=True,
            initial=0, role="mechanism_state",
            schedule="on_conservation_tick", quantization="integer",
            metric="discrete",
        ),
    ),
    mechanisms=(
        MechanismDecl(
            id="water.plot.flow",
            output="water.plot.inflow",
            parents=(
                Parent("water.basin.stock", "basin", lag=1,
                       relation="plot.of_basin", lipschitz=1.0,
                       valid_domain="finite_non_negative_integer"),
                Parent("water.plot.stock", "stock", lag=1, lipschitz=1.0,
                       valid_domain="finite_non_negative_integer"),
            ),
            impl=_plot_flow,
            when=When("phase", "flow"),
            params=(PARAMETER_RATE, PARAMETER_CAPACITY),
            param_arguments=(
                (PARAMETER_RATE, "rate"),
                (PARAMETER_CAPACITY, "capacity"),
            ),
            equation="inflow = max(0, min(rate, basin_{t-1}, capacity - stock_{t-1}))",
            boundary_cases=(
                "流域库存为 0 → 入流 0",
                "容量余量 ≤ 0（超灌）→ 入流 0",
                "rate = 0 → 入流 0",
            ),
            witnesses=(
                Witness("dry", {"basin": 0, "stock": 0},
                        (0,), params={PARAMETER_RATE: 3,
                                      PARAMETER_CAPACITY: 10}),
                Witness("full_basin", {"basin": 5, "stock": 0},
                        (3,), params={PARAMETER_RATE: 3,
                                      PARAMETER_CAPACITY: 10}),
                Witness("near_full", {"basin": 5, "stock": 9},
                        (1,), params={PARAMETER_RATE: 3,
                                      PARAMETER_CAPACITY: 10}),
            ),
            notes="多分辨率 restrict：读所属流域（上一帧库存）。",
        ),
        MechanismDecl(
            id="water.plot.apply",
            output="water.plot.stock",
            parents=(
                Parent("water.plot.stock", "stock", lag=1, lipschitz=1.0,
                       valid_domain="finite_non_negative_integer"),
                Parent("water.plot.inflow", "inflow", lipschitz=1.0,
                       valid_domain="finite_non_negative_integer"),
            ),
            impl=_plot_apply,
            when=When("phase", "apply"),
            equation="stock_t = stock_{t-1} + inflow_t",
            boundary_cases=(
                "入流为 0 → 库存保持",
            ),
            witnesses=(
                Witness("a0", {"stock": 0, "inflow": 0}, (0,)),
                Witness("a1", {"stock": 1, "inflow": 0}, (1,)),
                Witness("a2", {"stock": 1, "inflow": 2}, (3,)),
            ),
        ),
        MechanismDecl(
            id="water.basin.drain",
            output="water.basin.stock",
            parents=(
                Parent("water.basin.stock", "stock", lag=1, lipschitz=1.0,
                       valid_domain="finite_non_negative_integer"),
                Parent("water.plot.inflow", "outflow",
                       relation="plot.of_basin", aggregation="sum",
                       lipschitz=1.0,
                       valid_domain="finite_non_negative_integer"),
            ),
            impl=_basin_drain,
            when=When("phase", "drain"),
            equation="stock_t = stock_{t-1} - Σ inflow_t",
            boundary_cases=(
                "子地块流出总和超过库存 → 库存转负（界内不变量 record 留痕，守恒仍成立）",
                "无子地块流出 → 库存保持",
            ),
            witnesses=(
                Witness("d0", {"stock": 5, "outflow": 0}, (5,)),
                Witness("d1", {"stock": 5, "outflow": 2}, (3,)),
                Witness("d2", {"stock": 6, "outflow": 2}, (4,)),
            ),
            notes="多分辨率 prolong：聚合全部子地块流出（sum）。",
        ),
    ),
    invariants=(
        InvariantDecl(
            id="water.conservation.total",
            slots=("water.plot.stock", "water.basin.stock"),
            check=_conservation,
            severity="reject",
            message=f"水量不守恒（地块 + 流域 != {TOTAL_WATER}）",
        ),
        InvariantDecl(
            id="water.bounds.non_negative",
            slots=("water.plot.stock", "water.basin.stock",
                   "water.plot.inflow"),
            check=_bounds,
            severity="record",
            message="水量出现负值",
        ),
    ),
    parameters=(
        ParameterDecl(
            id=PARAMETER_RATE, default=3, kind="int",
            minimum=0, maximum=100, unit="unit/tick",
            source="drill.conservation 声明",
        ),
        ParameterDecl(
            id=PARAMETER_CAPACITY, default=10, kind="int",
            minimum=0, maximum=1000, unit="unit",
            source="drill.conservation 声明",
        ),
    ),
    evidence=(
        "黄金语义：testbench/world/test_conservation.py",
        "验收判据：W6（守恒切片）",
    ),
    notes="守恒演练模块：流量、守恒不变量与多分辨率。",
)
