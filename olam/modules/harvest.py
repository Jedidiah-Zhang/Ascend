"""实体演练模块 — 实体 + 事件 + 资源 + Γ（无策略）。

一个"不改框架"的完整模块：两类实体（资源堆 / 采集者）、互链关系
（目标 + 归属）、**事件驱动**的采集与结算、跨槽位守恒不变量。用于演练
接入位与研究层 Γ（行动 → 世界干预；策略由外部脚本给出，世界内无策略）：

- **事件 = 状态槽位 + 触发机制**：``harvest.gather``（事件 ``harvest``）
  写采集量；``harvest.carry`` / ``harvest.consume``（事件 ``harvest.settle``）
  结算（携带 + 资源扣减）。不触发事件则世界不动；
- **链接关系**：采集者经 ``harvester.target`` 读目标资源存量（目标），
  资源经 ``resource.owner`` 读归属采集者的采集量（归属）；链接未指派
  （空键）按父槽位声明的缺失值处理（存量 missing=0 = 无流动），指向
  不存在的实体仍 fail-closed；
- **Γ**：目标指派是行动（``ActionSpec`` → ``gamma``/``resolve`` →
  ``interventions_at`` → 帧干预），不是世界内策略；
- **守恒**：Σ 资源存量 + Σ 携带量 == 声明总量（跨槽位 reject 不变量）；
  采集量是**流量**（在途），结算后进入携带量。

时序：hold（目标保持）→ 事件 harvest（采集）→ 事件 harvest.settle
（携带 + 扣减）。采集读上一帧存量（settle 在同一帧稍后写回）。
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

__all__ = ["MODULE", "RESOURCE_INITIAL", "TOTAL_RESOURCE"]

RESOURCE_COUNT = 2
RESOURCE_INITIAL = 5
#: 声明总量（守恒不变量的事实源；与初值一致由测试锁定）。
TOTAL_RESOURCE = RESOURCE_COUNT * RESOURCE_INITIAL

_INT = ValueDomain(kind="int", bits=64)
_STOCK = ValueDomain(kind="int", bits=64, minimum=0)
#: 链接未指派（空键）时的父值：无目标/无归属 = 无流动
_STOCK_MISSING = ValueDomain(kind="int", bits=64, minimum=0, missing=0)
_KEY = ValueDomain(kind="any")
_WRITE = Permissions(intervene=True, observe=True, record=True)

GLOBAL = InstanceDecl(id="global", kind="global", identity="singleton")
RESOURCE = InstanceDecl(
    id="entity.resource", kind="entity", identity="derived_id",
    lifecycle="驱动层 spawn/despawn；耗尽不自动销毁（无策略）",
)
HARVESTER = InstanceDecl(
    id="entity.harvester", kind="entity", identity="derived_id",
    lifecycle="驱动层 spawn/despawn（演练模块）",
)

RELATION_TARGET = RelationDecl(
    id="harvester.target", kind="link", source="entity.harvester",
    target="entity.resource", key_slot="harvest.target",
)
RELATION_OWNER = RelationDecl(
    id="resource.owner", kind="link", source="entity.resource",
    target="entity.harvester", key_slot="harvest.owner",
)

PARAMETER_RATE = "harvest.rate"
PARAMETER_CAPACITY = "harvest.carry.capacity"


def _target_hold(ctx) -> object:
    """目标保持：链接键是状态（读上一帧值）。"""
    return ctx.parent("key")


def _gather(ctx) -> int:
    """采集量：速率、目标存量（上一帧）、承载余量三者取最小（下界 0）。"""
    stock = int(ctx.parent("stock"))
    carried = int(ctx.parent("carried"))
    rate = int(ctx.param(PARAMETER_RATE))
    capacity = int(ctx.param(PARAMETER_CAPACITY))
    return max(0, min(rate, stock, capacity - carried))


def _carry(ctx) -> int:
    """携带：上一帧携带 + 本帧采集量。"""
    return int(ctx.parent("carried")) + int(ctx.parent("yield"))


def _consume(ctx) -> int:
    """资源扣减：上一帧存量 − 归属采集者的本帧采集量（下界 0）。"""
    stock = int(ctx.parent("stock"))
    yield_amount = int(ctx.parent("yield"))
    return max(0, stock - yield_amount)


def _conservation(view) -> bool:
    """总量守恒：Σ 资源存量 + Σ 携带量 == 声明总量（采集量是在途流量）。"""
    stocks = sum(view["harvest.stock"].values())
    carried = sum(view["harvest.carried"].values())
    return stocks + carried == TOTAL_RESOURCE


def _bounds(view) -> bool:
    """界内：存量、携带与采集量非负。"""
    return all(
        value >= 0
        for slot_id in ("harvest.stock", "harvest.carried", "harvest.yield")
        for value in view[slot_id].values()
    )


MODULE = ModulePack(
    id="drill.harvest",
    version="1",
    instances=(GLOBAL, RESOURCE, HARVESTER),
    relations=(RELATION_TARGET, RELATION_OWNER),
    slots=(
        SlotDecl(
            id="harvest.stock", on="entity.resource", persist="state",
            domain=_STOCK_MISSING, permissions=_WRITE,
            access_interventions=("node", "persistent"),
            observation_protocols=("research.full.v1",),
            research_trace=True,
            writer="harvest.consume",
            initial=RESOURCE_INITIAL, role="mechanism_state",
            schedule="on_harvest_event", quantization="integer",
            metric="discrete",
        ),
        SlotDecl(
            id="harvest.owner", on="entity.resource", persist="state",
            domain=_KEY, permissions=_WRITE, writer="harvest.owner.hold",
            access_interventions=("node", "persistent"),
            observation_protocols=("research.full.v1",),
            research_trace=True,
            initial="", role="mechanism_state",
            schedule="on_harvest_tick", quantization="identity",
            metric="discrete",
        ),
        SlotDecl(
            id="harvest.target", on="entity.harvester", persist="state",
            domain=_KEY, permissions=_WRITE, writer="harvest.target.hold",
            access_interventions=("node", "persistent"),
            observation_protocols=("research.full.v1",),
            research_trace=True,
            initial="", role="mechanism_state",
            schedule="on_harvest_tick", quantization="identity",
            metric="discrete",
        ),
        SlotDecl(
            id="harvest.carried", on="entity.harvester", persist="state",
            domain=_STOCK, permissions=_WRITE, writer="harvest.carry",
            access_interventions=("node", "persistent"),
            observation_protocols=("research.full.v1",),
            research_trace=True,
            initial=0, role="mechanism_state",
            schedule="on_harvest_event", quantization="integer",
            metric="discrete",
        ),
        SlotDecl(
            id="harvest.yield", on="entity.harvester", persist="state",
            domain=_STOCK_MISSING, permissions=_WRITE,
            access_interventions=("node", "persistent"),
            observation_protocols=("research.full.v1",),
            research_trace=True,
            writer="harvest.gather",
            initial=0, role="mechanism_state",
            schedule="on_harvest_event", quantization="integer",
            metric="discrete",
        ),
    ),
    mechanisms=(
        MechanismDecl(
            id="harvest.owner.hold", output="harvest.owner",
            parents=(
                Parent("harvest.owner", "key", lag=1, metric="discrete",
                       lipschitz=0.0, valid_domain="稳定标识（str/int）"),
            ),
            impl=_target_hold, when=When("phase", "hold"),
            equation="owner_t = owner_{t-1}",
            boundary_cases=("空键（未指派）保持为空",),
            witnesses=(
                Witness("o0", {"key": "a"}, ("a",)),
                Witness("o1", {"key": "b"}, ("b",)),
            ),
            notes="归属保持：资源的 owner 链接键（状态）。",
        ),
        MechanismDecl(
            id="harvest.target.hold", output="harvest.target",
            parents=(
                Parent("harvest.target", "key", lag=1, metric="discrete",
                       lipschitz=0.0, valid_domain="稳定标识（str/int）"),
            ),
            impl=_target_hold, when=When("phase", "hold"),
            equation="target_t = target_{t-1}",
            boundary_cases=("空键（未指派）保持为空",),
            witnesses=(
                Witness("t0", {"key": "a"}, ("a",)),
                Witness("t1", {"key": "b"}, ("b",)),
            ),
            notes="目标保持：采集者的 target 链接键（状态）。",
        ),
        MechanismDecl(
            id="harvest.gather", output="harvest.yield",
            parents=(
                Parent("harvest.stock", "stock", lag=1,
                       relation="harvester.target", lipschitz=1.0,
                       valid_domain="finite_non_negative_integer"),
                Parent("harvest.carried", "carried", lag=1, lipschitz=1.0,
                       valid_domain="finite_non_negative_integer"),
            ),
            impl=_gather, when=When("event", "harvest"),
            params=(PARAMETER_RATE, PARAMETER_CAPACITY),
            param_arguments=(
                (PARAMETER_RATE, "rate"),
                (PARAMETER_CAPACITY, "capacity"),
            ),
            equation=(
                "yield = max(0, min(rate, stock_{t-1}, capacity - carried_{t-1}))"
            ),
            boundary_cases=(
                "未指派目标（空链接键）→ 缺失值 0（无流动）",
                "承载已满（余量 ≤ 0）→ 0",
                "目标存量为 0 → 0",
            ),
            witnesses=(
                Witness("empty", {"stock": 0, "carried": 0}, (0,),
                        params={PARAMETER_RATE: 3,
                                PARAMETER_CAPACITY: 10}),
                Witness("stocked", {"stock": 5, "carried": 0}, (3,),
                        params={PARAMETER_RATE: 3,
                                PARAMETER_CAPACITY: 10}),
                Witness("full", {"stock": 5, "carried": 9}, (1,),
                        params={PARAMETER_RATE: 3,
                                PARAMETER_CAPACITY: 10}),
            ),
            notes="事件 harvest：采集量（链接读目标存量，上一帧）。",
        ),
        MechanismDecl(
            id="harvest.carry", output="harvest.carried",
            parents=(
                Parent("harvest.carried", "carried", lag=1, lipschitz=1.0,
                       valid_domain="finite_non_negative_integer"),
                Parent("harvest.yield", "yield", lipschitz=1.0,
                       valid_domain="finite_non_negative_integer"),
            ),
            impl=_carry, when=When("event", "harvest.settle"),
            equation="carried_t = carried_{t-1} + yield_t",
            boundary_cases=(
                "采集量为 0 → 携带保持",
            ),
            witnesses=(
                Witness("c0", {"carried": 0, "yield": 0}, (0,)),
                Witness("c1", {"carried": 1, "yield": 0}, (1,)),
                Witness("c2", {"carried": 1, "yield": 2}, (3,)),
            ),
            notes="事件 harvest.settle：携带结算（读本帧采集量）。",
        ),
        MechanismDecl(
            id="harvest.consume", output="harvest.stock",
            parents=(
                Parent("harvest.stock", "stock", lag=1, lipschitz=1.0,
                       valid_domain="finite_non_negative_integer"),
                Parent("harvest.yield", "yield", relation="resource.owner",
                       lipschitz=1.0,
                       valid_domain="finite_non_negative_integer"),
            ),
            impl=_consume, when=When("event", "harvest.settle"),
            equation="stock_t = max(0, stock_{t-1} - yield_t)",
            boundary_cases=(
                "采集量为 0 → 存量保持",
                "采集量超过存量 → 下界 0（clamp）",
                "未指派归属（空链接键）→ 缺失值 0（不扣减）",
            ),
            witnesses=(
                Witness("d0", {"stock": 5, "yield": 0}, (5,)),
                Witness("d1", {"stock": 5, "yield": 2}, (3,)),
                Witness("d2", {"stock": 6, "yield": 2}, (4,)),
            ),
            notes="事件 harvest.settle：资源扣减（归属链接读采集量）。",
        ),
    ),
    invariants=(
        InvariantDecl(
            id="harvest.conservation.total",
            slots=("harvest.stock", "harvest.carried"),
            check=_conservation,
            severity="reject",
            message=f"资源不守恒（存量 + 携带 != {TOTAL_RESOURCE}）",
        ),
        InvariantDecl(
            id="harvest.bounds.non_negative",
            slots=("harvest.stock", "harvest.carried", "harvest.yield"),
            check=_bounds,
            severity="record",
            message="资源出现负值",
        ),
    ),
    parameters=(
        ParameterDecl(
            id=PARAMETER_RATE, default=3, kind="int",
            minimum=0, maximum=100, unit="unit/event",
            source="drill.harvest 声明",
        ),
        ParameterDecl(
            id=PARAMETER_CAPACITY, default=10, kind="int",
            minimum=0, maximum=1000, unit="unit",
            source="drill.harvest 声明",
        ),
    ),
    evidence=(
        "黄金语义：testbench/world/test_harvest.py",
        "验收判据：W7（实体切片）",
    ),
    notes="实体演练模块：实体、事件、资源与 Γ（无策略）。",
)
