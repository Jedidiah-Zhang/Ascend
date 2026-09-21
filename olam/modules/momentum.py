"""动量演练模块（drill.momentum）— 按接入协议接入的验收模块。

接入只写声明 + 证据 + 测试：

- **声明面**：全局槽位 + 阶段机制 + 不变量（无新概念）；
- **lag≥2**：机制读两帧前的值（``v_{t-2}``）；"检查点充分性"（WC-7.5）
  的证据面含 lag=2——快照必须携带足够的历史深度；
- **证据义务**：方程、见证（覆盖两个父引用）、边界情形、父边误差界元数据
  （L 与有效域）、研究元数据、模块说明与证据清单；
- **验收**：``testbench/world/test_momentum.py``（递推、上界 clamp、快照往返、
  协议门禁）。

递推：``v_t = min(cap, v_{t-1} + acc · (v_{t-1} − v_{t-2}))``（离散加速度）。
``acc`` 是参数（上界 10）→ 对 ``v_{t-1}`` 的 Lipschitz 常数 ≤ 1 + 10 = 11，
对 ``v_{t-2}`` ≤ 10；``min(cap, ·)`` 为 1-Lipschitz，不放大上界。
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
    SlotDecl,
    ValueDomain,
    When,
    Witness,
)

__all__ = ["MODULE", "VALUE_CAP", "VALUE_INITIAL"]

GLOBAL = InstanceDecl(id="global", kind="global", identity="singleton")

VALUE_INITIAL = 1
#: 上界常量（不变量的事实源；与参数默认值一致由测试锁定）。
VALUE_CAP = 100

_INT = ValueDomain(kind="int", bits=64, minimum=0)
_WRITE = Permissions(intervene=True, observe=True, record=True)

PARAMETER_ACCELERATION = "momentum.acceleration"


def _step(ctx) -> int:
    """离散加速度递推：v_t = min(cap, v1 + acc · (v1 − v2))。"""
    previous = int(ctx.parent("previous"))
    older = int(ctx.parent("older"))
    acceleration = int(ctx.param(PARAMETER_ACCELERATION))
    cap = int(ctx.param("momentum.cap"))
    return min(cap, previous + acceleration * (previous - older))


def _bounds(view) -> bool:
    """界内：动量非负且不超过上界。"""
    value = view["momentum.value"]
    return 0 <= value <= VALUE_CAP


MODULE = ModulePack(
    id="drill.momentum",
    version="1",
    instances=(GLOBAL,),
    slots=(
        SlotDecl(
            id="momentum.value", on="global", persist="state",
            domain=_INT, permissions=_WRITE, writer="momentum.step",
            initial=VALUE_INITIAL,
            role="mechanism_state",
            schedule="on_momentum_tick",
            quantization="integer",
            metric="absolute_difference",
            access_interventions=("node", "persistent"),
            observation_protocols=("research.full.v1",),
            research_trace=True,
        ),
    ),
    mechanisms=(
        MechanismDecl(
            id="momentum.step", output="momentum.value",
            parents=(
                Parent("momentum.value", "previous", lag=1, lipschitz=11.0,
                       valid_domain="finite_non_negative_integer（≤ cap）"),
                Parent("momentum.value", "older", lag=2, lipschitz=10.0,
                       valid_domain="finite_non_negative_integer（≤ cap）"),
            ),
            impl=_step, when=When("phase", "step"),
            params=(PARAMETER_ACCELERATION, "momentum.cap"),
            param_arguments=(
                (PARAMETER_ACCELERATION, "acceleration"),
                ("momentum.cap", "cap"),
            ),
            equation=(
                "v_t = min(cap, v_{t-1} + acc · (v_{t-1} − v_{t-2}))"
            ),
            witnesses=(
                Witness("flat", {"previous": 1, "older": 1}, (1,),
                        params={PARAMETER_ACCELERATION: 1,
                                "momentum.cap": VALUE_CAP}),
                Witness("faster", {"previous": 2, "older": 1}, (3,),
                        params={PARAMETER_ACCELERATION: 1,
                                "momentum.cap": VALUE_CAP}),
                Witness("slower", {"previous": 2, "older": 0}, (4,),
                        params={PARAMETER_ACCELERATION: 1,
                                "momentum.cap": VALUE_CAP}),
                Witness("capped", {"previous": 100, "older": 2}, (100,),
                        params={PARAMETER_ACCELERATION: 1,
                                "momentum.cap": VALUE_CAP}),
            ),
            boundary_cases=(
                "v_{t-1} = v_{t-2}（匀速）→ 保持",
                "递推结果超过上界 → clamp 到 cap",
                "lag=2 历史不足 → 拒绝（快照必须携带足够历史，WC-7.5）",
            ),
            notes="读两帧前值：lag=2 的检查点充分性证据面。",
        ),
    ),
    invariants=(
        InvariantDecl(
            id="momentum.bounds", slots=("momentum.value",),
            check=_bounds, severity="reject",
            message=f"动量超出 [0, {VALUE_CAP}]",
        ),
    ),
    parameters=(
        ParameterDecl(
            id=PARAMETER_ACCELERATION, default=1, kind="int",
            minimum=0, maximum=10, unit="1/tick",
            source="drill.momentum 声明",
        ),
        ParameterDecl(
            id="momentum.cap", default=VALUE_CAP, kind="int",
            minimum=0, maximum=1000000, unit="unit",
            source="drill.momentum 声明",
        ),
    ),
    evidence=(
        "黄金语义：testbench/world/test_momentum.py",
        "协议门禁：testbench/world/test_protocol.py（接入协议检查）",
    ),
    notes="接入协议演练：仅凭声明与证据接入的模块（lag≥2）。",
)
