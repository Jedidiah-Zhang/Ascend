"""干预执行器契约测试 — 记录/干预表语义、执行前校验、CRN、W1/W2、Lean 对拍。

契约来源：第一阶段实施定义 §7（六条校验/两轴语义）、工程符号体系 §5、
世界基座 04 §3.2/§3.3（W1/W2 通过标准）、InterventionTypes.lean 数值见证、
世界基座 08（可达性 fail-closed / 单一事实源）。

研究切片世界（W1）：rt → med → out 链 + 独立 ind；U_* 外生流以
slice_boundary 边界节点建模（贴近真实切片架构，C1 见证可完整评估）。
研究切片世界（W2）：单分量 x_{t+1} = x_t + 1（对应 Lean inc1，x0=0）。
研究切片世界（W3）：带真实外生随机源的 CRN 契约（值覆盖不消费随机地址）。
"""

from __future__ import annotations

import pytest

from ascend.causal import (
    AccessPolicy,
    DependencyWitness,
    ExogenousSourceSpec,
    InstanceDomain,
    MathMetadata,
    MechanismRegistry,
    MechanismSpec,
    InterventionRecord,
    InterventionTable,
    NodeSpec,
    ParameterBinding,
    ParameterSpec,
    ParentSpec,
    RandomBinding,
    StateOwnership,
    UpdateContract,
    ValueDomain,
)
from ascend.causal.intervention import default_duration
from ascend.causal.intervention_engine import (
    InterventionEvaluator,
    InterventionFrameExecutor,
)


# ── 研究切片世界构建 ─────────────────────────────────────────

_S1, _S2, _S3, _S4 = "s1", "s2", "s3", "s4"
_W1_STEPS = (_S1, _S2, _S3, _S4)
_W2_STEPS = (_S1,)


def _global_node(
    node_id: str,
    *,
    microstep: str,
    bounds: tuple[float, float] = (-1000.0, 1000.0),
    role: str = "mechanism_state",
    origin: str = "mechanism",
    interventions: tuple[str, ...] | None = None,
    spatial: bool = False,
) -> NodeSpec:
    if interventions is None:
        # 与生产声明同规则：读出/边界分量不可干预
        interventions = (
            ()
            if role == "readout" or origin == "slice_boundary"
            else ("node", "persistent", "mechanism")
        )
    instance_domain = (
        InstanceDomain(
            kind="spatial_field",
            axes=("chunk_x", "chunk_y"),
            creation="chunk_registration",
            destruction="chunk_unregistration",
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
            kind="float",
            unit="unit",
            bounds=bounds,
            choices=(),
            missing="forbidden",
            quantization="continuous",
        ),
        state=StateOwnership(
            in_world_state=True,
            reconstruction="recompute_on_demand",
        ),
        update=UpdateContract(
            schedule="each_frame",
            microstep=microstep,
            when_not_updated="retain_previous_value",
            writer="single_writer",
            merge_rule="single_writer",
        ),
        access=AccessPolicy(
            interventions=interventions,
            research_trace=True,
            observation_protocols=("research.full.v1",),
        ),
        math=MathMetadata(
            error_budget=1.0,
            metric="absolute_difference",
            valid_domain="full",
        ),
    )


def _parent(
    parent: str,
    argument: str,
    *,
    lag: int = 0,
    source_microstep: str = _S1,
) -> ParentSpec:
    return ParentSpec(
        parent=parent,
        argument=argument,
        lag=lag,
        source_microstep=source_microstep,
        spatial_offsets=(),
        entity_relation="none",
        aggregation="identity",
        broadcast="identity",
        boundary_operator="identity",
        guard="none",
        lipschitz=1.0,
        metric="absolute_difference",
        valid_domain="full",
        analysis_role="forward",
    )


def _rand(source: str, argument: str) -> RandomBinding:
    return RandomBinding(source=source, argument=argument)


def _source(source_id: str) -> ExogenousSourceSpec:
    return ExogenousSourceSpec(
        source_id=source_id,
        distribution="uniform",
        distribution_parameters=(("low", 0.0), ("high", 1.0)),
        draw_microstep=_S1,
        instance_axes=(),
        address_template=(source_id,),
        shared_by=(),
        dynamic_field=False,
    )


def _witness(
    parent: str,
    inputs: tuple[tuple[str, object], ...],
    alternate: object,
    outputs: tuple[object, object],
) -> DependencyWitness:
    return DependencyWitness(
        label=f"w:{parent}",
        parent=parent,
        inputs=inputs,
        alternate_value=alternate,
        expected_outputs=outputs,
    )


def _mech(
    mechanism_id: str,
    output: str,
    function,
    *,
    parents=(),
    witnesses=(),
    random_sources=(),
) -> MechanismSpec:
    return MechanismSpec(
        mechanism_id=mechanism_id,
        output=output,
        equation=output,
        function=function,
        parents=parents,
        parameters=(),
        random_sources=random_sources,
        boundary_cases=("declared",),
        source_dependencies=(),
        witnesses=witnesses,
    )


def build_w1_registry() -> MechanismRegistry:
    """rt → med → out 链 + 独立 ind；U_* 为 slice_boundary 边界输入。"""
    boundary = ("U_rt", "U_med", "U_out", "U_ind")
    nodes = tuple(
        _global_node(
            node_id, microstep=_S1, role="persistent_state",
            origin="slice_boundary",
        )
        for node_id in boundary
    ) + (
        _global_node("rt", microstep=_S2),
        _global_node("med", microstep=_S3),
        _global_node("out", microstep=_S4),
        _global_node("ind", microstep=_S2),
    )
    mechanisms = (
        _mech(
            "w1.rt", "rt", lambda u_rt: u_rt,
            parents=(_parent("U_rt", "u_rt"),),
            witnesses=(_witness("U_rt", (("U_rt", 1.0),), 2.0, (1.0, 2.0)),),
        ),
        _mech(
            "w1.med", "med", lambda rt, u_med: rt + u_med,
            parents=(
                _parent("rt", "rt", source_microstep=_S2),
                _parent("U_med", "u_med"),
            ),
            witnesses=(
                _witness("rt", (("rt", 1.0), ("U_med", 2.0)), 2.0, (3.0, 4.0)),
                _witness("U_med", (("rt", 1.0), ("U_med", 2.0)), 3.0, (3.0, 4.0)),
            ),
        ),
        _mech(
            "w1.out", "out", lambda med, u_out: med + u_out,
            parents=(
                _parent("med", "med", source_microstep=_S3),
                _parent("U_out", "u_out"),
            ),
            witnesses=(
                _witness("med", (("med", 3.0), ("U_out", 3.0)), 4.0, (6.0, 7.0)),
                _witness("U_out", (("med", 3.0), ("U_out", 3.0)), 4.0, (6.0, 7.0)),
            ),
        ),
        _mech(
            "w1.ind", "ind", lambda u_ind: u_ind,
            parents=(_parent("U_ind", "u_ind"),),
            witnesses=(_witness("U_ind", (("U_ind", 4.0),), 5.0, (4.0, 5.0)),),
        ),
    )
    return MechanismRegistry(
        schema_version=3,
        declaration_id="research.w1",
        declaration_version="1",
        microstep_order=_W1_STEPS,
        slice_boundary="研究切片世界（W1 构造）",
        wired_nodes=frozenset({"rt", "med", "out", "ind"}),
        nodes=nodes,
        parameters=(),
        exogenous_sources=(),
        mechanisms=mechanisms,
    )


def build_w2_registry(bounds: tuple[float, float] = (0.0, 300.0)) -> MechanismRegistry:
    """单分量 x_{t+1} = x_t + 1（对应 Lean inc1，x0=0）。"""
    nodes = (_global_node("x", microstep=_S1, bounds=bounds),)
    mechanisms = (
        _mech(
            "w2.inc", "x", lambda x_prev: x_prev + 1,
            parents=(_parent("x", "x_prev", lag=1),),
            witnesses=(_witness("x", (("x", 1.0),), 2.0, (2.0, 3.0)),),
        ),
    )
    return MechanismRegistry(
        schema_version=3,
        declaration_id="research.w2",
        declaration_version="1",
        microstep_order=_W2_STEPS,
        slice_boundary="研究切片世界（W2 构造）",
        wired_nodes=frozenset({"x"}),
        nodes=nodes,
        parameters=(),
        exogenous_sources=(),
        mechanisms=mechanisms,
    )


def build_crn_registry() -> MechanismRegistry:
    """带真实外生随机源的切片：rt → med（med 消费 R_med）。

    用于验证 CRN 随机流契约——值覆盖不消费随机地址、机制覆盖只消费
    替换机制声明的源（``InterventionFrameExecutor.drawn``）。
    """
    nodes = (
        _global_node("rt", microstep=_S1),
        _global_node("med", microstep=_S2),
    )
    mechanisms = (
        _mech("crn.rt", "rt", lambda: 1.0),
        _mech(
            "crn.med", "med", lambda rt, r: rt + r,
            parents=(_parent("rt", "rt"),),
            random_sources=(_rand("R_med", "r"),),
            witnesses=(
                _witness("rt", (("rt", 1.0),), 2.0, (1.5, 2.5)),
            ),
        ),
    )
    return MechanismRegistry(
        schema_version=3,
        declaration_id="research.crn",
        declaration_version="1",
        microstep_order=(_S1, _S2),
        slice_boundary="研究切片世界（CRN 构造）",
        wired_nodes=frozenset({"rt", "med"}),
        nodes=nodes,
        parameters=(),
        exogenous_sources=(_source("R_med"), _source("R_new")),
        mechanisms=mechanisms,
    )


_W1_BOUNDARY = {"U_rt": 1.0, "U_med": 2.0, "U_out": 3.0, "U_ind": 4.0}


def _run_w1(frames: int, table: InterventionTable | None = None):
    registry = build_w1_registry()
    executor = InterventionFrameExecutor(registry, table)
    state = {
        "rt": None, "med": None, "out": None, "ind": None,
        **_W1_BOUNDARY,
    }
    trajectory = []
    for tick in range(1, frames + 1):
        state = executor.run_frame(tick, state)
        trajectory.append(dict(state))
    return trajectory, executor


def _run_w2(table: InterventionTable | None = None):
    registry = build_w2_registry()
    executor = InterventionFrameExecutor(registry, table)
    state = {"x": 0.0}
    trajectory = []
    for tick in range(1, 4):
        state = executor.run_frame(tick, state, state)
        trajectory.append(dict(state))
    return trajectory, executor


def _mech_x_plus_100() -> MechanismSpec:
    return _mech(
        "w2.inc100", "x", lambda x_prev: x_prev + 100,
        parents=(_parent("x", "x_prev", lag=1),),
    )


def _mech_x_plus_1() -> MechanismSpec:
    return _mech(
        "w2.inc_same", "x", lambda x_prev: x_prev + 1,
        parents=(_parent("x", "x_prev", lag=1),),
    )


def _parameter(
    parameter_id: str = "p", *,
    allowed: bool = True,
    bounds: tuple[float, float] = (0.0, 10.0),
) -> ParameterSpec:
    return ParameterSpec(
        parameter_id=parameter_id, value_type="float", unit="u",
        bounds=bounds, value=5.0, version="v1",
        intervention_allowed=allowed, source="code-only:test",
    )


def _parameter_registry(parameter: ParameterSpec) -> MechanismRegistry:
    """参数被已接线机制 y 绑定（否则参数干预不可达，登记会被拒绝）。"""
    mechanism = MechanismSpec(
        mechanism_id="y.m", output="y", equation="y",
        function=lambda p: p * 2.0,
        parents=(),
        parameters=(ParameterBinding(parameter.parameter_id, "p"),),
        random_sources=(),
        boundary_cases=("declared",),
        source_dependencies=(),
        witnesses=(),
    )
    return MechanismRegistry(
        schema_version=3, declaration_id="research.param",
        declaration_version="1", microstep_order=_W2_STEPS,
        slice_boundary="param", wired_nodes=frozenset({"y"}),
        nodes=(_global_node("y", microstep=_S1),),
        parameters=(parameter,),
        exogenous_sources=(), mechanisms=(mechanism,),
    )


# ── 干预表基础语义 ───────────────────────────────────────────

class TestInterventionTableBasics:
    def test_commit_and_resolve_value_window(self):
        table = InterventionTable(build_w2_registry())
        table.commit(InterventionRecord(
            target_space="node", target="x", rep="value",
            value=10.0, frame_t0=1, duration=1,
        ))
        res = table.resolve_node("x", (), 1)
        assert res.rep == "value" and res.value == 10.0
        assert table.resolve_node("x", (), 0).rep is None
        assert table.resolve_node("x", (), 2).rep is None

    def test_later_commit_replaces_earlier_same_key(self):
        table = InterventionTable(build_w2_registry())
        table.commit(InterventionRecord(
            target_space="node", target="x", rep="value",
            value=10.0, frame_t0=1, duration=1,
        ))
        table.commit(InterventionRecord(
            target_space="node", target="x", rep="value",
            value=20.0, frame_t0=1, duration=1,
        ))
        assert table.resolve_node("x", (), 1).value == 20.0
        assert len(table.history) == 2
        assert [rec.seq for rec in table.history] == [1, 2]

    def test_applied_at_is_stamped_by_table(self):
        """applied_at 由表盖章（调用方无法设置，也不可能丢）。"""
        table = InterventionTable(build_w2_registry(), now=lambda: 4242)
        record = table.commit(InterventionRecord(
            target_space="node", target="x", rep="value",
            value=10.0, frame_t0=1, duration=1,
        ))
        assert record.applied_at == 4242
        assert table.history[0].applied_at == 4242
        assert table.snapshot()["values"][0]["applied_at"] == 4242
        assert table.history_plain()[0]["applied_at"] == 4242

    def test_environment_change_is_derived(self):
        table = InterventionTable(_parameter_registry(_parameter()))
        record = table.commit(InterventionRecord(
            target_space="parameter", target="p", rep="value",
            value=7.0, frame_t0=0,
        ))
        assert record.environment_change is True
        node_record = InterventionTable(build_w2_registry()).commit(InterventionRecord(
            target_space="node", target="x", rep="value",
            value=1.0, frame_t0=0, duration=1,
        ))
        assert node_record.environment_change is False

    def test_value_beats_mechanism_on_same_node(self):
        table = InterventionTable(build_w2_registry())
        table.commit(InterventionRecord(
            target_space="node", target="x", rep="value",
            value=10.0, frame_t0=1, duration=1,
        ))
        table.commit(InterventionRecord(
            target_space="node", target="x", rep="mechanism",
            mechanism=_mech_x_plus_100(), frame_t0=1,
        ))
        res = table.resolve_node("x", (), 1)
        assert res.rep == "value" and res.value == 10.0
        res2 = table.resolve_node("x", (), 2)
        assert res2.rep == "mechanism"

    def test_clear_removes_records_and_reports_kinds(self):
        table = InterventionTable(build_w2_registry())
        table.commit(InterventionRecord(
            target_space="node", target="x", rep="value",
            value=10.0, frame_t0=1, duration=1,
        ))
        assert table.clear("node", "x") == ("value",)
        assert table.resolve_node("x", (), 1).rep is None
        assert table.clear("node", "x") == ()

    def test_clear_all_kinds_and_rep_filter(self):
        """rep=None 清空该键全部规格；rep 指定时只清该规格（终端同语义）。"""
        table = InterventionTable(build_w2_registry())
        table.commit(InterventionRecord(
            target_space="node", target="x", rep="value",
            value=10.0, frame_t0=1, duration=None,
        ))
        table.commit(InterventionRecord(
            target_space="node", target="x", rep="mechanism",
            mechanism=_mech_x_plus_100(), frame_t0=1,
        ))
        assert table.clear("node", "x", rep="value") == ("value",)
        assert table.resolve_node("x", (), 5).rep == "mechanism"
        assert table.clear("node", "x") == ("mechanism",)
        assert table.resolve_node("x", (), 5).rep is None

    def test_clear_parameter_and_feature(self):
        table = InterventionTable(_parameter_registry(_parameter()))
        table.commit(InterventionRecord(
            target_space="parameter", target="p", rep="value",
            value=7.0, frame_t0=0,
        ))
        assert table.clear("parameter", "p", rep="mechanism") == ()
        assert table.resolve_parameter("p", 0) == (True, 7.0)
        assert table.clear("parameter", "p") == ("value",)
        assert table.resolve_parameter("p", 0) == (False, None)
        assert table.clear("parameter", "p") == ()

        w2 = InterventionTable(build_w2_registry())
        w2.commit(InterventionRecord(
            target_space="field_feature", target="storm", instance=(0, 0),
            rep="value", value={"active": True}, frame_t0=0, duration=None,
        ))
        assert w2.clear("field_feature", "storm", (0, 0), rep="mechanism") == ()
        assert w2.clear("field_feature", "storm", (0, 0)) == ("value",)
        assert w2.snapshot()["features"] == []

    def test_snapshot_is_deterministic_across_instances(self):
        """快照确定性：同登记序列的两个独立表产生完全相同的快照。"""
        def build() -> InterventionTable:
            table = InterventionTable(build_w2_registry(), now=lambda: 7)
            table.commit(InterventionRecord(
                target_space="node", target="x", rep="value",
                value=10.0, frame_t0=1, duration=1,
            ))
            table.commit(InterventionRecord(
                target_space="node", target="x", rep="mechanism",
                mechanism=_mech_x_plus_100(), frame_t0=2,
            ))
            return table
        assert build().snapshot() == build().snapshot()


# ── 执行前校验（§7 六条）────────────────────────────────────

class TestValidation:
    def test_unknown_target(self):
        table = InterventionTable(build_w2_registry())
        with pytest.raises(ValueError, match="未声明"):
            table.commit(InterventionRecord(
                target_space="node", target="ghost", rep="value",
                value=1.0, frame_t0=0, duration=1,
            ))

    def test_unwired_target_rejected(self):
        """已声明但引擎未执行的生成点拒绝登记（防静默无效干预）。"""
        registry = build_w1_registry()  # U_* 未列入 wired_nodes
        table = InterventionTable(registry)
        with pytest.raises(ValueError, match="未接线"):
            table.commit(InterventionRecord(
                target_space="node", target="U_med", rep="value",
                value=1.0, frame_t0=0, duration=1,
            ))

    def test_value_out_of_domain(self):
        table = InterventionTable(build_w2_registry(bounds=(0.0, 50.0)))
        with pytest.raises(ValueError, match="超出"):
            table.commit(InterventionRecord(
                target_space="node", target="x", rep="value",
                value=100.0, frame_t0=0, duration=1,
            ))

    def test_invalid_frame_and_duration(self):
        table = InterventionTable(build_w2_registry())
        with pytest.raises(ValueError, match="生效帧"):
            table.commit(InterventionRecord(
                target_space="node", target="x", rep="value",
                value=1.0, frame_t0=-1, duration=1,
            ))
        with pytest.raises(ValueError, match="生效帧"):
            table.commit(InterventionRecord(
                target_space="node", target="x", rep="value",
                value=1.0, frame_t0=True, duration=1,
            ))
        with pytest.raises(ValueError, match="时长"):
            table.commit(InterventionRecord(
                target_space="node", target="x", rep="value",
                value=1.0, frame_t0=0, duration=0,
            ))
        with pytest.raises(ValueError, match="时长"):
            table.commit(InterventionRecord(
                target_space="node", target="x", rep="value",
                value=1.0, frame_t0=0, duration=True,
            ))

    def test_permission_mapping_by_duration(self):
        """单帧→node；窗口与长期→persistent；机制→mechanism（唯一映射处）。"""
        nodes = (_global_node("x", microstep=_S1, interventions=("node",)),)
        base = build_w2_registry()
        restricted = MechanismRegistry(
            schema_version=3, declaration_id="research.w2p",
            declaration_version="1", microstep_order=_W2_STEPS,
            slice_boundary="x", wired_nodes=frozenset({"x"}),
            nodes=nodes, parameters=(),
            exogenous_sources=(), mechanisms=base.mechanisms.values(),
        )
        table = InterventionTable(restricted)
        table.commit(InterventionRecord(
            target_space="node", target="x", rep="value",
            value=1.0, frame_t0=0, duration=1,
        ))
        for duration in (2, None):
            with pytest.raises(ValueError, match="不允许 persistent"):
                table.commit(InterventionRecord(
                    target_space="node", target="x", rep="value",
                    value=1.0, frame_t0=0, duration=duration,
                ))

    def test_mechanism_permission_required(self):
        nodes = (
            _global_node(
                "x", microstep=_S1, interventions=("node", "persistent"),
            ),
        )
        base = build_w2_registry()
        registry = MechanismRegistry(
            schema_version=3, declaration_id="research.w2m",
            declaration_version="1", microstep_order=_W2_STEPS,
            slice_boundary="x", wired_nodes=frozenset({"x"}),
            nodes=nodes, parameters=(),
            exogenous_sources=(), mechanisms=base.mechanisms.values(),
        )
        table = InterventionTable(registry)
        with pytest.raises(ValueError, match="不允许 mechanism"):
            table.commit(InterventionRecord(
                target_space="node", target="x", rep="mechanism",
                mechanism=_mech_x_plus_100(), frame_t0=0,
            ))

    def test_readout_and_boundary_must_declare_no_interventions(self):
        """§7 读出分量保护 = 注册表声明期不变量。"""
        with pytest.raises(ValueError, match="不得声明干预权限"):
            MechanismRegistry(
                schema_version=3, declaration_id="research.bad",
                declaration_version="1", microstep_order=_W2_STEPS,
                slice_boundary="x", wired_nodes=frozenset({"r"}),
                nodes=(
                    _global_node(
                        "r", microstep=_S1, role="readout",
                        interventions=("node",),
                    ),
                ),
                parameters=(), exogenous_sources=(),
                mechanisms=(_mech("r.m", "r", lambda: 1.0),),
            )
        with pytest.raises(ValueError, match="不得声明干预权限"):
            MechanismRegistry(
                schema_version=3, declaration_id="research.bad2",
                declaration_version="1", microstep_order=_W2_STEPS,
                slice_boundary="x", wired_nodes=frozenset({"b"}),
                nodes=(
                    _global_node(
                        "b", microstep=_S1, role="persistent_state",
                        origin="slice_boundary",
                        interventions=("persistent",),
                    ),
                ),
                parameters=(), exogenous_sources=(),
                mechanisms=(_mech("b.m", "b", lambda: 1.0),),
            )

    def test_boundary_node_intervention_rejected(self):
        """边界分量不可干预（C0 声明期保证；登记时再按声明拒绝）。"""
        table = InterventionTable(build_w1_registry())
        with pytest.raises(ValueError, match="未接线|不允许"):
            table.commit(InterventionRecord(
                target_space="node", target="U_med", rep="value",
                value=1.0, frame_t0=0, duration=1,
            ))

    def test_instance_domain_shape(self):
        table = InterventionTable(build_w2_registry())
        with pytest.raises(ValueError, match="实例"):
            table.commit(InterventionRecord(
                target_space="node", target="x", rep="value",
                value=1.0, instance=(0, 0), frame_t0=0, duration=1,
            ))

    def test_instance_existence_checked(self):
        """实例存在性由世界句柄注入；不存在的实例拒绝登记。"""
        table = InterventionTable(
            build_w2_registry(),
            instance_exists=lambda _node, inst: inst == ("ok",),
        )
        # 全局分量恒存在，不受回调影响
        table.commit(InterventionRecord(
            target_space="node", target="x", rep="value",
            value=1.0, frame_t0=0, duration=1,
        ))
        spatial_nodes = (
            _global_node("s", microstep=_S1, spatial=True),
        )
        registry = MechanismRegistry(
            schema_version=3, declaration_id="research.spatial",
            declaration_version="1", microstep_order=_W2_STEPS,
            slice_boundary="s", wired_nodes=frozenset({"s"}),
            nodes=spatial_nodes, parameters=(), exogenous_sources=(),
            mechanisms=(_mech("s.m", "s", lambda: 1.0),),
        )
        spatial = InterventionTable(
            registry, instance_exists=lambda _node, inst: inst == (0, 0),
        )
        with pytest.raises(ValueError, match="实例不存在"):
            spatial.commit(InterventionRecord(
                target_space="node", target="s", rep="value",
                value=1.0, instance=(1, 2), frame_t0=0, duration=1,
            ))

    def test_commit_rejects_non_sequence_instance(self):
        """instance 非序列 → ValueError（不是 TypeError，与 docstring 一致）。"""
        table = InterventionTable(build_w2_registry())
        with pytest.raises(ValueError, match="实例必须为元组或列表"):
            table.commit(InterventionRecord(
                target_space="node", target="x", rep="value",
                value=1.0, instance=3, frame_t0=0, duration=1,
            ))
        # None 视作空元组（全局分量）
        record = table.commit(InterventionRecord(
            target_space="node", target="x", rep="value",
            value=1.0, instance=None, frame_t0=0, duration=1,
        ))
        assert record.instance == ()

    def test_mechanism_parents_must_be_subset(self):
        table = InterventionTable(build_w1_registry())
        replacement = _mech(
            "w1.med_bad", "med",
            lambda rt, u_med, u_ind: rt + u_med + u_ind,
            parents=(_parent("rt", "rt"), _parent("U_ind", "u_ind")),
            random_sources=(_rand("U_med", "u_med"),),
        )
        with pytest.raises(ValueError, match="未声明"):
            table.commit(InterventionRecord(
                target_space="node", target="med", rep="mechanism",
                mechanism=replacement, frame_t0=1,
            ))

    def test_mechanism_output_mismatch(self):
        table = InterventionTable(build_w2_registry())
        with pytest.raises(ValueError, match="输出"):
            table.commit(InterventionRecord(
                target_space="node", target="x", rep="mechanism",
                mechanism=_mech("other", "out", lambda: 1.0),
                frame_t0=1,
            ))

    def test_mechanism_random_overlap_rejected(self):
        """替换机制随机源不得与无关机制重叠（真实注册表，非桩）。"""
        nodes = (
            _global_node("rt", microstep=_S1),
            _global_node("med", microstep=_S2),
            _global_node("ind", microstep=_S2),
        )
        registry = MechanismRegistry(
            schema_version=3, declaration_id="research.crn2",
            declaration_version="1", microstep_order=(_S1, _S2),
            slice_boundary="crn", wired_nodes=frozenset({"rt", "med", "ind"}),
            nodes=nodes, parameters=(),
            exogenous_sources=(_source("R_med"), _source("R_ind")),
            mechanisms=(
                _mech("c.rt", "rt", lambda: 1.0),
                _mech(
                    "c.med", "med", lambda rt, r: rt + r,
                    parents=(_parent("rt", "rt"),),
                    random_sources=(_rand("R_med", "r"),),
                    witnesses=(
                        _witness("rt", (("rt", 1.0),), 2.0, (1.5, 2.5)),
                    ),
                ),
                _mech(
                    "c.ind", "ind", lambda r_ind: r_ind,
                    random_sources=(_rand("R_ind", "r_ind"),),
                ),
            ),
        )
        table = InterventionTable(registry)
        replacement = _mech(
            "c.med_steal", "med", lambda rt, r_ind: rt + r_ind,
            parents=(_parent("rt", "rt"),),
            random_sources=(_rand("R_ind", "r_ind"),),
        )
        with pytest.raises(ValueError, match="重叠"):
            table.commit(InterventionRecord(
                target_space="node", target="med", rep="mechanism",
                mechanism=replacement, frame_t0=1,
            ))

    def test_parameter_intervention_rules(self):
        registry = _parameter_registry(_parameter())
        table = InterventionTable(registry)
        record = table.commit(InterventionRecord(
            target_space="parameter", target="p", rep="value",
            value=7.0, frame_t0=0,
        ))
        assert record.environment_change is True
        assert table.resolve_parameter("p", 5) == (True, 7.0)
        with pytest.raises(ValueError, match="不支持时长类别"):
            table.commit(InterventionRecord(
                target_space="parameter", target="p", rep="value",
                value=7.0, frame_t0=0, duration=5,
            ))
        with pytest.raises(ValueError, match="bounds"):
            table.commit(InterventionRecord(
                target_space="parameter", target="p", rep="value",
                value=99.0, frame_t0=0,
            ))
        forbidden = InterventionTable(
            _parameter_registry(_parameter("q", allowed=False)),
        )
        with pytest.raises(ValueError, match="不允许干预"):
            forbidden.commit(InterventionRecord(
                target_space="parameter", target="q", rep="value",
                value=2.0, frame_t0=0,
            ))

    def test_unwired_parameter_rejected(self):
        """未被已接线机制消费的参数：登记会被拒绝（环境变化不会生效）。"""
        table = InterventionTable(build_w2_registry())
        with pytest.raises(ValueError, match="未声明"):
            table.commit(InterventionRecord(
                target_space="parameter", target="ghost", rep="value",
                value=1.0, frame_t0=0,
            ))

    def test_feature_duration_must_be_forever(self):
        table = InterventionTable(build_w2_registry())
        with pytest.raises(ValueError, match="不支持时长类别"):
            table.commit(InterventionRecord(
                target_space="field_feature", target="storm",
                instance=(0, 0), rep="value", value={"active": True},
                frame_t0=0, duration=2,
            ))

    def test_mechanism_window_allowed(self):
        table = InterventionTable(build_w2_registry())
        table.commit(InterventionRecord(
            target_space="node", target="x", rep="mechanism",
            mechanism=_mech_x_plus_100(), frame_t0=1, duration=2,
        ))
        assert table.resolve_node("x", (), 1).rep == "mechanism"
        assert table.resolve_node("x", (), 2).rep == "mechanism"
        assert table.resolve_node("x", (), 3).rep is None


# ── C1 见证随机上下文（占位值掩蔽）──────────────────────────

def _masked_parent_function(p: float, r: float) -> float:
    """父依赖在 r=0.5 处抵消（占位随机值会误判"输出未变化"）。"""
    return (p - 1.0) * (r - 0.5)


def _witness_registry(random_values=()) -> MechanismRegistry:
    mechanism = MechanismSpec(
        mechanism_id="ctx.m", output="y", equation="y",
        function=_masked_parent_function,
        parents=(_parent("p", "p", source_microstep=_S1),),
        parameters=(),
        random_sources=(_rand("R", "r"),),
        boundary_cases=("declared",),
        source_dependencies=(),
        witnesses=(DependencyWitness(
            label="w:p", parent="p", inputs=(("p", 1.0),),
            alternate_value=2.0, expected_outputs=(0.0, -0.25),
            random_values=random_values,
        ),),
    )
    return MechanismRegistry(
        schema_version=3, declaration_id="research.ctx",
        declaration_version="1", microstep_order=(_S1, _S2),
        slice_boundary="ctx", wired_nodes=frozenset({"y"}),
        nodes=(
            _global_node(
                "p", microstep=_S1, role="persistent_state",
                origin="slice_boundary",
            ),
            _global_node("y", microstep=_S2),
        ),
        parameters=(),
        exogenous_sources=(_source("R"),),
        mechanisms=(mechanism,),
    )


class TestWitnessRandomContext:
    def test_default_placeholder_cannot_satisfy_masked_dependency(self):
        """占位值 r=0.5 时父依赖被抵消 → 该机制无法声明（已复现的误拒）。"""
        with pytest.raises(ValueError, match="C1 结构最小性失败"):
            _witness_registry()

    def test_explicit_context_allows_masked_dependency(self):
        registry = _witness_registry(random_values=(("R", 0.25),))
        assert registry.validate_c1() == ()

    def test_context_must_reference_declared_source(self):
        with pytest.raises(ValueError, match="随机上下文"):
            _witness_registry(random_values=(("R_ghost", 0.25),))


# ── 覆盖感知求值器 ───────────────────────────────────────────

class TestInterventionEvaluator:
    def test_value_override_short_circuits(self):
        table = InterventionTable(build_w2_registry())
        table.commit(InterventionRecord(
            target_space="node", target="x", rep="value",
            value=10.0, frame_t0=1, duration=1,
        ))
        evaluator = InterventionEvaluator(build_w2_registry(), table)
        assert evaluator.evaluate(
            "x", {"x": 0.0}, frame=1, instance=(),
        ) == 10.0
        assert evaluator.evaluate(
            "x", {"x": 0.0}, frame=2, instance=(),
        ) == 1.0

    def test_passthrough_without_table(self):
        evaluator = InterventionEvaluator(build_w2_registry())
        assert evaluator.evaluate("x", {"x": 1.0}) == 2.0

    def test_parameter_override_flows_into_binding(self):
        registry = _parameter_registry(_parameter())
        table = InterventionTable(registry)
        table.commit(InterventionRecord(
            target_space="parameter", target="p", rep="value",
            value=7.0, frame_t0=1,
        ))
        evaluator = InterventionEvaluator(registry, table)
        assert evaluator.evaluate("y", {}, frame=1) == 14.0
        assert evaluator.evaluate("y", {}, frame=0) == 10.0

    def test_table_parameter_beats_caller_value(self):
        """表内活跃参数干预优先于调用方 parameter_values（单一优先级）。"""
        registry = _parameter_registry(_parameter())
        table = InterventionTable(registry)
        table.commit(InterventionRecord(
            target_space="parameter", target="p", rep="value",
            value=3.0, frame_t0=0,
        ))
        evaluator = InterventionEvaluator(registry, table)
        assert evaluator.evaluate(
            "y", {}, frame=0, parameter_values={"p": 8.0},
        ) == 6.0

    def test_mechanism_override_uses_replacement(self):
        table = InterventionTable(build_w2_registry())
        table.commit(InterventionRecord(
            target_space="node", target="x", rep="mechanism",
            mechanism=_mech_x_plus_100(), frame_t0=1,
        ))
        evaluator = InterventionEvaluator(build_w2_registry(), table)
        assert evaluator.evaluate("x", {"x": 5.0}, frame=1) == 105.0


# ── CRN 随机流契约（真实外生随机源）─────────────────────────

class TestCRN:
    def _run(self, table=None, draws=None):
        registry = build_crn_registry()
        draws = draws if draws is not None else {}
        executor = InterventionFrameExecutor(
            registry, table,
            exogenous=lambda source, frame, instance: draws.get(
                (source, frame), 0.5,
            ),
        )
        state = {"rt": 0.0, "med": 0.0}
        for tick in (1, 2):
            state = executor.run_frame(tick, state)
        return state, executor

    def test_value_override_consumes_no_random_address(self):
        """值覆盖整段不消费原机制随机地址（drawn 为空）。"""
        table = InterventionTable(build_crn_registry())
        table.commit(InterventionRecord(
            target_space="node", target="med", rep="value",
            value=9.0, frame_t0=1, duration=1,
        ))
        state, executor = self._run(table)
        assert state["med"] == 1.0 + 0.5          # 帧 2 回到原机制
        assert ("R_med", 1, ()) not in executor.drawn
        assert ("R_med", 2, ()) in executor.drawn

    def test_mechanism_override_consumes_own_sources_only(self):
        """机制覆盖只消费替换机制声明的随机源。"""
        table = InterventionTable(build_crn_registry())
        replacement = _mech(
            "crn.med_new", "med", lambda rt, r_new: rt * 10.0 + r_new,
            parents=(_parent("rt", "rt"),),
            random_sources=(_rand("R_new", "r_new"),),
        )
        table.commit(InterventionRecord(
            target_space="node", target="med", rep="mechanism",
            mechanism=replacement, frame_t0=1,
        ))
        state, executor = self._run(table)
        assert state["med"] == 10.0 + 0.5
        assert ("R_new", 1, ()) in executor.drawn
        assert ("R_med", 1, ()) not in executor.drawn

    def test_value_override_reads_no_boundary_inputs(self):
        table = InterventionTable(build_w1_registry())
        table.commit(InterventionRecord(
            target_space="node", target="med", rep="value",
            value=5.0, frame_t0=2, duration=1,
        ))
        _, executor = _run_w1(3, table)
        # 帧 2：med 值干预命中 → rt 与 U_med 均不被读取（断入边，CRN）
        assert ("U_med", 2, ()) not in executor.reads
        assert ("rt", 2, ()) not in executor.reads
        assert ("U_med", 1, ()) in executor.reads
        assert ("U_med", 3, ()) in executor.reads
        for source in ("U_rt", "U_out", "U_ind"):
            assert (source, 2, ()) in executor.reads

    def test_mechanism_override_reads_own_parents_only(self):
        table = InterventionTable(build_w1_registry())
        replacement = _mech(
            "w1.med_alt", "med",
            lambda rt: rt * 10.0,
            parents=(_parent("rt", "rt"),),
        )
        table.commit(InterventionRecord(
            target_space="node", target="med", rep="mechanism",
            mechanism=replacement, frame_t0=2,
        ))
        _, executor = _run_w1(3, table)
        assert ("U_med", 2, ()) not in executor.reads
        assert ("rt", 2, ()) in executor.reads


# ── W1：节点干预与 CRN 验收（04 §3.2）────────────────────────

class TestW1:
    def _baseline(self):
        trajectory, executor = _run_w1(3)
        return trajectory[-1], executor

    def test_w1_node_do(self):
        table = InterventionTable(build_w1_registry())
        table.commit(InterventionRecord(
            target_space="node", target="med", rep="value",
            value=5.0, frame_t0=2, duration=1,
        ))
        trajectory, executor = _run_w1(3, table)
        baseline, _ = self._baseline()
        frame = trajectory[1]  # tick=2
        assert frame["med"] == 5.0
        assert frame["out"] == 5.0 + _W1_BOUNDARY["U_out"]
        assert frame["rt"] == baseline["rt"]
        assert frame["ind"] == baseline["ind"]
        assert ("U_med", 2, ()) not in executor.reads
        assert frame["ind"] == _W1_BOUNDARY["U_ind"]

    def test_w1_parent_link_cut(self):
        """干预后 med 不再消费 rt / U_med：同时干预 rt 不影响 med。"""
        table = InterventionTable(build_w1_registry())
        table.commit(InterventionRecord(
            target_space="node", target="med", rep="value",
            value=5.0, frame_t0=2, duration=1,
        ))
        table.commit(InterventionRecord(
            target_space="node", target="rt", rep="value",
            value=999.0, frame_t0=2, duration=1,
        ))
        trajectory, _ = _run_w1(3, table)
        assert trajectory[1]["med"] == 5.0
        assert trajectory[1]["rt"] == 999.0

    def test_w1_persistent_window(self):
        table = InterventionTable(build_w1_registry())
        table.commit(InterventionRecord(
            target_space="node", target="med", rep="value",
            value=5.0, frame_t0=2, duration=2,
        ))
        trajectory, _ = _run_w1(3, table)
        assert trajectory[0]["med"] == 3.0
        assert trajectory[1]["med"] == 5.0
        assert trajectory[2]["med"] == 5.0
        assert trajectory[2]["out"] == 5.0 + _W1_BOUNDARY["U_out"]


# ── W2：三类干预互异 + Lean 数值对拍（04 §3.3）───────────────

class TestW2:
    def test_three_trajectories_distinct(self):
        baseline, _ = _run_w2()

        table1 = InterventionTable(build_w2_registry())
        table1.commit(InterventionRecord(
            target_space="node", target="x", rep="value",
            value=10.0, frame_t0=1, duration=1,
        ))
        node_do, _ = _run_w2(table1)

        table2 = InterventionTable(build_w2_registry())
        table2.commit(InterventionRecord(
            target_space="node", target="x", rep="value",
            value=10.0, frame_t0=1, duration=2,
        ))
        persist, _ = _run_w2(table2)

        table3 = InterventionTable(build_w2_registry())
        table3.commit(InterventionRecord(
            target_space="node", target="x", rep="mechanism",
            mechanism=_mech_x_plus_100(), frame_t0=1,
        ))
        mech_do, _ = _run_w2(table3)

        assert [row["x"] for row in node_do] == [10.0, 11.0, 12.0]
        assert [row["x"] for row in persist] == [10.0, 10.0, 11.0]
        assert [row["x"] for row in mech_do] == [100.0, 200.0, 300.0]
        assert [row["x"] for row in baseline] == [1.0, 2.0, 3.0]
        series = {
            tuple(row["x"] for row in traj)
            for traj in (node_do, persist, mech_do, baseline)
        }
        assert len(series) == 4

    def test_lean_numeric_witnesses(self):
        """与 InterventionTypes.lean 数值见证逐一对拍。"""
        table = InterventionTable(build_w2_registry())
        table.commit(InterventionRecord(
            target_space="node", target="x", rep="value",
            value=10.0, frame_t0=1, duration=1,
        ))
        node_do, _ = _run_w2(table)
        assert node_do[0]["x"] == 10.0   # nodeDo_t1
        assert node_do[1]["x"] == 11.0   # nodeDo_t2 (F(10)=11)

        table = InterventionTable(build_w2_registry())
        table.commit(InterventionRecord(
            target_space="node", target="x", rep="value",
            value=10.0, frame_t0=1, duration=2,
        ))
        persist, _ = _run_w2(table)
        assert persist[1]["x"] == 10.0   # persist2_t2
        assert persist[2]["x"] == 11.0   # persist2_t3

        table = InterventionTable(build_w2_registry())
        table.commit(InterventionRecord(
            target_space="node", target="x", rep="mechanism",
            mechanism=_mech_x_plus_100(), frame_t0=1,
        ))
        mech_do, _ = _run_w2(table)
        assert mech_do[0]["x"] == 100.0  # mech_t1
        assert mech_do[1]["x"] == 200.0  # mech_t2

    def test_lean_persist_one_eq_node_do(self):
        """persist_one_eq_nodeDo：时长 1 的持续 ≡ 节点干预（Python 侧显式对拍）。"""
        node_table = InterventionTable(build_w2_registry())
        node_table.commit(InterventionRecord(
            target_space="node", target="x", rep="value",
            value=10.0, frame_t0=1, duration=1,
        ))
        persist_table = InterventionTable(build_w2_registry())
        persist_table.commit(InterventionRecord(
            target_space="node", target="x", rep="value",
            value=10.0, frame_t0=1, duration=1,
        ))
        node_traj, _ = _run_w2(node_table)
        persist_traj, _ = _run_w2(persist_table)
        assert [r["x"] for r in node_traj] == [r["x"] for r in persist_traj]

    def test_lean_before_eq_traj(self):
        """nodeDo_before_eq_traj：干预帧之前轨迹与基线一致。"""
        baseline, _ = _run_w2()
        table = InterventionTable(build_w2_registry())
        table.commit(InterventionRecord(
            target_space="node", target="x", rep="value",
            value=10.0, frame_t0=2, duration=1,
        ))
        node_do, _ = _run_w2(table)
        assert node_do[0]["x"] == baseline[0]["x"]

    def test_lean_mech_same_eq_traj(self):
        """mechDo_same_eq_traj：F'=F 时与无干预轨迹一致（替换惰性）。"""
        baseline, _ = _run_w2()
        table = InterventionTable(build_w2_registry())
        table.commit(InterventionRecord(
            target_space="node", target="x", rep="mechanism",
            mechanism=_mech_x_plus_1(), frame_t0=1,
        ))
        mech_do, _ = _run_w2(table)
        assert [row["x"] for row in mech_do] == [row["x"] for row in baseline]

    def test_value_pin_until_cleared(self):
        """长期值干预持续钉住，clear 后恢复原机制。"""
        table = InterventionTable(build_w2_registry())
        table.commit(InterventionRecord(
            target_space="node", target="x", rep="value",
            value=10.0, frame_t0=1, duration=None,
        ))
        trajectory, _ = _run_w2(table)
        assert [row["x"] for row in trajectory] == [10.0, 10.0, 10.0]
        assert table.clear("node", "x", rep="value") == ("value",)
        baseline, _ = _run_w2()
        assert [row["x"] for row in _run_w2(table)[0]] == [
            row["x"] for row in baseline
        ]


# ── 缺省解析（终端 do 与研究 API 共用）───────────────────────

class TestDefaults:
    def test_default_frame_is_next_tick(self):
        table = InterventionTable(build_w2_registry(), now=lambda: 100)
        assert table.default_frame() == 101

    def test_default_frame_without_clock(self):
        assert InterventionTable(build_w2_registry()).default_frame() == 0

    def test_default_duration_by_space_and_rep(self):
        assert default_duration("node", "value") == 1
        assert default_duration("node", "mechanism") is None
        assert default_duration("parameter", "value") is None
        assert default_duration("feature", "value") is None


# ── 逐帧执行器错误路径 ───────────────────────────────────────

class TestFrameExecutorErrors:
    def test_lag_greater_than_one_rejected(self):
        mechanism = _mech(
            "w2.lag2", "x", lambda x_prev: x_prev + 1,
            parents=(_parent("x", "x_prev", lag=2),),
            witnesses=(_witness("x", (("x", 1.0),), 2.0, (2.0, 3.0)),),
        )
        registry = MechanismRegistry(
            schema_version=3, declaration_id="research.lag2",
            declaration_version="1", microstep_order=_W2_STEPS,
            slice_boundary="x", wired_nodes=frozenset({"x"}),
            nodes=(_global_node("x", microstep=_S1),),
            parameters=(), exogenous_sources=(),
            mechanisms=(mechanism,),
        )
        executor = InterventionFrameExecutor(registry)
        with pytest.raises(ValueError, match="仅支持 lag"):
            executor.run_frame(1, {"x": 0.0})

    def test_missing_parent_rejected(self):
        registry = build_w1_registry()
        executor = InterventionFrameExecutor(registry)
        with pytest.raises(KeyError, match="缺少父值"):
            executor.run_frame(1, {"rt": 0.0, "med": 0.0, "out": 0.0, "ind": 0.0})
