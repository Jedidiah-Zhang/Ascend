"""验收切片世界 — 按世界验收协议构造的最小声明（每项判据一个切片）。

切片是**独立的声明**，不复用生产天气声明：这样验收的是"声明语义"本身，
而不是"某个具体公式碰巧对上了"。全部切片在构造期通过 C0/C1（注册表自检）。

- W0：三阶段全局单例（mid1 → mid2 → x），验证帧内顺序；
- W1：rt → med → out 链 + 独立 ind，验证节点干预与 CRN；
- W2：单分量 x_{t+1}=x_t+1，验证值/持续/机制三类干预互异；
- W3：一维五格父模板（滞后 1 + 空间偏移 ±1），验证空间父模板与边界；
- I0/I1：探针世界（观测等价/干预可分、分布预测/配对效应）。
"""

from __future__ import annotations

from ascend.causal import (
    AccessPolicy,
    DependencyWitness,
    InstanceDomain,
    MathMetadata,
    MechanismRegistry,
    MechanismSpec,
    NodeSpec,
    ParentSpec,
    StateOwnership,
    UpdateContract,
    ValueDomain,
)

# ── 通用构建助手 ──────────────────────────────────────────────

_S1, _S2, _S3, _S4 = "s1", "s2", "s3", "s4"
_W1_STEPS = (_S1, _S2, _S3, _S4)
_W2_STEPS = (_S1,)


def node(
    node_id: str,
    *,
    microstep: str = _S1,
    bounds: tuple[float, float] = (-1000.0, 1000.0),
    role: str = "mechanism_state",
    origin: str = "mechanism",
    interventions: tuple[str, ...] | None = None,
    spatial: bool = False,
    protocols: tuple[str, ...] = ("research.full.v1",),
    kind: str = "float",
) -> NodeSpec:
    """构造一个最小但字段完整的节点声明（C0 全字段显式）。"""
    if interventions is None:
        interventions = (
            ()
            if role == "readout" or origin == "slice_boundary"
            else ("node", "persistent", "mechanism")
        )
    instance_domain = (
        InstanceDomain(
            kind="spatial_field",
            axes=("cell",),
            creation="world_initialization",
            destruction="world_teardown",
        )
        if spatial
        else InstanceDomain(
            kind="global_singleton",
            axes=(),
            creation="world_initialization",
            destruction="world_teardown",
        )
    )
    return NodeSpec(
        node_id=node_id,
        role=role,
        origin=origin,
        instance_domain=instance_domain,
        value=ValueDomain(
            kind=kind, unit="unit",
            bounds=bounds if kind in ("float", "integer") else None,
            choices=(), missing="forbidden",
            quantization="continuous" if kind in ("float", "integer") else "exact",
        ),
        state=StateOwnership(
            in_world_state=True, reconstruction="recompute_on_demand",
        ),
        update=UpdateContract(
            schedule="each_frame", microstep=microstep,
            when_not_updated="retain_previous_value",
            writer="single_writer", merge_rule="single_writer",
        ),
        access=AccessPolicy(
            interventions=interventions,
            research_trace=True,
            observation_protocols=protocols,
        ),
        math=MathMetadata(
            error_budget=1.0, metric="absolute_difference", valid_domain="full",
        ),
    )


def parent(
    parent_id: str,
    argument: str,
    *,
    lag: int = 0,
    source_microstep: str = _S1,
    spatial_offsets: tuple[tuple[int, ...], ...] = ((0,),),
    boundary_operator: str = "none",
) -> ParentSpec:
    """构造一个最小但字段完整的父引用声明。"""
    return ParentSpec(
        parent=parent_id, argument=argument, lag=lag,
        source_microstep=source_microstep,
        spatial_offsets=spatial_offsets,
        entity_relation="self",
        aggregation="identity", broadcast="identity",
        boundary_operator=boundary_operator,
        guard="always", lipschitz=1.0,
        metric="absolute_difference", valid_domain="full",
        analysis_role="forward",
    )


def witness(parent_id: str, inputs, alternate, expected) -> DependencyWitness:
    """C1 功能依赖见证（固定上下文，只改一个父值）。"""
    return DependencyWitness(
        label=f"w_{parent_id}", parent=parent_id, inputs=tuple(inputs),
        alternate_value=alternate, expected_outputs=tuple(expected),
    )


def mechanism(
    mechanism_id: str,
    output: str,
    function,
    *,
    parents=(),
    witnesses=(),
) -> MechanismSpec:
    """构造一条结构方程声明。"""
    return MechanismSpec(
        mechanism_id=mechanism_id, output=output, equation=output,
        function=function, parents=tuple(parents), parameters=(),
        random_sources=(), boundary_cases=("declared",),
        source_dependencies=(), witnesses=tuple(witnesses),
    )


def registry(
    declaration_id: str,
    *,
    steps: tuple[str, ...],
    nodes: tuple[NodeSpec, ...],
    mechanisms: tuple[MechanismSpec, ...],
    wired: frozenset[str],
) -> MechanismRegistry:
    """组装切片注册表（构造期自动跑 C0/C1）。"""
    return MechanismRegistry(
        schema_version=3,
        declaration_id=declaration_id,
        declaration_version="1",
        microstep_order=steps,
        slice_boundary="验收切片（world acceptance protocol）",
        wired_nodes=wired,
        nodes=nodes,
        parameters=(),
        exogenous_sources=(),
        mechanisms=mechanisms,
    )


# ── W0：帧内顺序（04 §3.1）────────────────────────────────────

def w0_registry() -> MechanismRegistry:
    """mid1 → mid2 → x 三阶段链；读旧值会得到不同结果。"""
    return registry(
        "research.w0",
        steps=_W1_STEPS,
        nodes=(
            node("U_mid", microstep=_S1, role="persistent_state",
                 origin="slice_boundary"),
            node("mid1", microstep=_S2),
            node("mid2", microstep=_S3),
            node("x", microstep=_S4),
        ),
        mechanisms=(
            mechanism(
                "w0.mid1", "mid1", lambda x_prev, u: x_prev + u,
                parents=(
                    parent("x", "x_prev", lag=1, source_microstep=_S4),
                    parent("U_mid", "u", source_microstep=_S1),
                ),
                witnesses=(
                    witness("x", (("x", 1.0), ("U_mid", 2.0)), 2.0, (3.0, 4.0)),
                    witness("U_mid", (("x", 1.0), ("U_mid", 2.0)), 3.0, (3.0, 4.0)),
                ),
            ),
            mechanism(
                "w0.mid2", "mid2", lambda mid1: 2.0 * mid1,
                parents=(parent("mid1", "mid1", source_microstep=_S2),),
                witnesses=(witness("mid1", (("mid1", 3.0),), 4.0, (6.0, 8.0)),),
            ),
            mechanism(
                "w0.x", "x", lambda mid2: mid2,
                parents=(parent("mid2", "mid2", source_microstep=_S3),),
                witnesses=(witness("mid2", (("mid2", 6.0),), 7.0, (6.0, 7.0)),),
            ),
        ),
        wired=frozenset({"mid1", "mid2", "x"}),
    )


# ── W1/W2：干预（04 §3.2/§3.3）───────────────────────────────

def w1_registry() -> MechanismRegistry:
    """rt → med → out 链 + 独立 ind（U_* 为边界输入）。"""
    boundary = ("U_rt", "U_med", "U_out", "U_ind")
    nodes = tuple(
        node(node_id, role="persistent_state", origin="slice_boundary")
        for node_id in boundary
    ) + (
        node("rt", microstep=_S2),
        node("med", microstep=_S3),
        node("out", microstep=_S4),
        node("ind", microstep=_S2),
    )
    return registry(
        "research.w1",
        steps=_W1_STEPS,
        nodes=nodes,
        mechanisms=(
            mechanism(
                "w1.rt", "rt", lambda u_rt: u_rt,
                parents=(parent("U_rt", "u_rt"),),
                witnesses=(witness("U_rt", (("U_rt", 1.0),), 2.0, (1.0, 2.0)),),
            ),
            mechanism(
                "w1.med", "med", lambda rt, u_med: rt + u_med,
                parents=(
                    parent("rt", "rt", source_microstep=_S2),
                    parent("U_med", "u_med"),
                ),
                witnesses=(
                    witness("rt", (("rt", 1.0), ("U_med", 2.0)), 2.0, (3.0, 4.0)),
                    witness("U_med", (("rt", 1.0), ("U_med", 2.0)), 3.0, (3.0, 4.0)),
                ),
            ),
            mechanism(
                "w1.out", "out", lambda med, u_out: med + u_out,
                parents=(
                    parent("med", "med", source_microstep=_S3),
                    parent("U_out", "u_out"),
                ),
                witnesses=(
                    witness("med", (("med", 3.0), ("U_out", 3.0)), 4.0, (6.0, 7.0)),
                    witness("U_out", (("med", 3.0), ("U_out", 3.0)), 4.0, (6.0, 7.0)),
                ),
            ),
            mechanism(
                "w1.ind", "ind", lambda u_ind: u_ind,
                parents=(parent("U_ind", "u_ind"),),
                witnesses=(witness("U_ind", (("U_ind", 4.0),), 5.0, (4.0, 5.0)),),
            ),
        ),
        wired=frozenset({"rt", "med", "out", "ind"}),
    )


def w2_registry(bounds: tuple[float, float] = (0.0, 300.0)) -> MechanismRegistry:
    """单分量 x_{t+1} = x_t + 1（对应 Lean inc1，x0=0）。"""
    return registry(
        "research.w2",
        steps=_W2_STEPS,
        nodes=(node("x", bounds=bounds),),
        mechanisms=(
            mechanism(
                "w2.inc", "x", lambda x_prev: x_prev + 1,
                parents=(parent("x", "x_prev", lag=1),),
                witnesses=(witness("x", (("x", 1.0),), 2.0, (2.0, 3.0)),),
            ),
        ),
        wired=frozenset({"x"}),
    )


# ── W3：空间父模板与边界（04 §3.4）────────────────────────────

W3_CELLS: int = 5
W3_WEIGHTS: tuple[float, float, float] = (0.25, 0.5, 0.25)


def _w3_equation(u_prev) -> float:
    """一维三邻域加权平均；``u_prev`` 是 (左, 中, 右) 三元组。

    边界算子 ``replicate`` 由调用方在传入前完成（越界邻格取边缘格自身），
    因此方程本身与内部格相同——算子差异体现在输入上。
    """
    left, center, right = u_prev
    return W3_WEIGHTS[0] * left + W3_WEIGHTS[1] * center + W3_WEIGHTS[2] * right


def w3_registry() -> MechanismRegistry:
    """一维五格：v 在 s 处 = 1/4·u(s-1) + 1/2·u(s) + 1/4·u(s+1)（滞后 1）。

    边界算子 ``replicate``：s=0 的 s-1 与 s=4 的 s+1 取边缘格自身。
    """
    return registry(
        "research.w3",
        steps=_W2_STEPS,
        nodes=(
            node("u", spatial=True),
            node("v", spatial=True),
        ),
        mechanisms=(
            mechanism(
                "w3.u", "u", lambda u_prev: u_prev,
                parents=(parent("u", "u_prev", lag=1),),
                witnesses=(witness("u", (("u", 1.0),), 2.0, (1.0, 2.0)),),
            ),
            mechanism(
                "w3.v", "v", _w3_equation,
                parents=(
                    parent(
                        "u", "u_prev", lag=1,
                        spatial_offsets=((-1,), (0,), (1,)),
                        boundary_operator="replicate",
                    ),
                ),
                witnesses=(
                    witness("u", (("u", (1.0, 1.0, 1.0)),), (2.0, 1.0, 1.0),
                            (_w3_equation((1.0, 1.0, 1.0)),
                             _w3_equation((2.0, 1.0, 1.0)))),
                ),
            ),
        ),
        wired=frozenset({"u", "v"}),
    )
