"""神迹系统契约测试 — OverrideTable 语义、执行前校验、W1/W2、Lean 对拍。

契约来源：第一阶段实施定义 §7（六条校验/四类语义）、工程符号体系 §5、
世界基座 04 §3.2/§3.3（W1/W2 通过标准）、InterventionTypes.lean 数值见证。

研究切片世界（W1）：rt → med → out 链 + 独立 ind；U_* 外生流以
slice_boundary 边界节点建模（贴近真实切片架构，C1 见证可完整评估）。
研究切片世界（W2）：单分量 x_{t+1} = x_t + 1（对应 Lean inc1，x0=0）。
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
    MiracleRecord,
    MiracleTable,
    NodeSpec,
    ParameterSpec,
    ParentSpec,
    RandomBinding,
    StateOwnership,
    UpdateContract,
    ValueDomain,
)
from ascend.causal.miracle_engine import (
    MiracleEvaluator,
    MiracleFrameExecutor,
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
    interventions: tuple[str, ...] = ("node", "persistent", "mechanism"),
) -> NodeSpec:
    return NodeSpec(
        node_id=node_id,
        role=role,
        origin=origin,
        instance_domain=InstanceDomain(
            kind="global_singleton",
            axes=(),
            creation="world_initialization",
            destruction="world_teardown",
        ),
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
        nodes=nodes,
        parameters=(),
        exogenous_sources=(),
        mechanisms=mechanisms,
    )


_W1_BOUNDARY = {"U_rt": 1.0, "U_med": 2.0, "U_out": 3.0, "U_ind": 4.0}


def _run_w1(frames: int, table: MiracleTable | None = None):
    registry = build_w1_registry()
    executor = MiracleFrameExecutor(registry, table)
    state = {
        "rt": None, "med": None, "out": None, "ind": None,
        **_W1_BOUNDARY,
    }
    trajectory = []
    for tick in range(1, frames + 1):
        state = executor.run_frame(tick, state)
        trajectory.append(dict(state))
    return trajectory, executor


def _run_w2(table: MiracleTable | None = None):
    registry = build_w2_registry()
    executor = MiracleFrameExecutor(registry, table)
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


# ── 神迹表基础语义 ───────────────────────────────────────────

class TestMiracleTableBasics:
    def test_commit_and_resolve_value_window(self):
        table = MiracleTable(build_w2_registry())
        table.commit(MiracleRecord(
            target_space="node", target="x", rep="value",
            value=10.0, frame_t0=1, duration=1,
        ))
        res = table.resolve_node("x", (), 1)
        assert res.rep == "value" and res.value == 10.0
        assert table.resolve_node("x", (), 0).rep is None
        assert table.resolve_node("x", (), 2).rep is None

    def test_later_commit_replaces_earlier_same_key(self):
        table = MiracleTable(build_w2_registry())
        table.commit(MiracleRecord(
            target_space="node", target="x", rep="value",
            value=10.0, frame_t0=1, duration=1,
        ))
        table.commit(MiracleRecord(
            target_space="node", target="x", rep="value",
            value=20.0, frame_t0=1, duration=1,
        ))
        assert table.resolve_node("x", (), 1).value == 20.0
        assert len(table.history) == 2
        assert [rec.seq for rec in table.history] == [1, 2]

    def test_value_beats_mechanism_on_same_node(self):
        table = MiracleTable(build_w2_registry())
        table.commit(MiracleRecord(
            target_space="node", target="x", rep="mechanism",
            mechanism=_mech_x_plus_100(), frame_t0=1,
        ))
        table.commit(MiracleRecord(
            target_space="node", target="x", rep="value",
            value=10.0, frame_t0=1, duration=2,
        ))
        res = table.resolve_node("x", (), 1)
        assert res.rep == "value" and res.value == 10.0
        res2 = table.resolve_node("x", (), 3)
        assert res2.rep == "mechanism"

    def test_clear_removes_record(self):
        table = MiracleTable(build_w2_registry())
        table.commit(MiracleRecord(
            target_space="node", target="x", rep="value",
            value=10.0, frame_t0=1, duration=1,
        ))
        assert table.clear("node", "x", rep="value") is True
        assert table.resolve_node("x", (), 1).rep is None
        assert table.clear("node", "x", rep="value") is False

    def test_snapshot_is_deterministic(self):
        table = MiracleTable(build_w2_registry())
        table.commit(MiracleRecord(
            target_space="node", target="x", rep="value",
            value=10.0, frame_t0=1, duration=1,
        ))
        assert table.snapshot() == table.snapshot()


# ── 执行前校验（§7 六条）──────────────────────────────────────

class TestValidation:
    def test_unknown_target(self):
        table = MiracleTable(build_w2_registry())
        with pytest.raises(ValueError, match="未声明"):
            table.commit(MiracleRecord(
                target_space="node", target="ghost", rep="value",
                value=1.0, frame_t0=0, duration=1,
            ))

    def test_value_out_of_domain(self):
        table = MiracleTable(build_w2_registry(bounds=(0.0, 50.0)))
        with pytest.raises(ValueError, match="超出"):
            table.commit(MiracleRecord(
                target_space="node", target="x", rep="value",
                value=100.0, frame_t0=0, duration=1,
            ))

    def test_invalid_frame_and_duration(self):
        table = MiracleTable(build_w2_registry())
        with pytest.raises(ValueError, match="生效帧"):
            table.commit(MiracleRecord(
                target_space="node", target="x", rep="value",
                value=1.0, frame_t0=-1, duration=1,
            ))
        with pytest.raises(ValueError, match="时长"):
            table.commit(MiracleRecord(
                target_space="node", target="x", rep="value",
                value=1.0, frame_t0=0, duration=0,
            ))

    def test_permission_persistent_denied(self):
        nodes = (
            _global_node("x", microstep=_S1, interventions=("node",)),
        )
        mech = _mech(
            "w2.inc", "x", lambda x_prev: x_prev + 1,
            parents=(_parent("x", "x_prev", lag=1),),
            witnesses=(_witness("x", (("x", 1.0),), 2.0, (2.0, 3.0)),),
        )
        restricted = MechanismRegistry(
            schema_version=3, declaration_id="research.w2p",
            declaration_version="1", microstep_order=_W2_STEPS,
            slice_boundary="x", nodes=nodes, parameters=(),
            exogenous_sources=(), mechanisms=(mech,),
        )
        table = MiracleTable(restricted)
        with pytest.raises(ValueError, match="不允许"):
            table.commit(MiracleRecord(
                target_space="node", target="x", rep="value",
                value=1.0, frame_t0=0, duration=2,
            ))
        table.commit(MiracleRecord(
            target_space="node", target="x", rep="value",
            value=1.0, frame_t0=0, duration=1,
        ))

    def test_readout_and_boundary_not_intervenable(self):
        readout_node = _global_node("r", microstep=_S1, role="readout")
        boundary_node = _global_node(
            "b", microstep=_S1, role="persistent_state",
            origin="slice_boundary",
        )
        registry = MechanismRegistry(
            schema_version=3, declaration_id="research.w2p",
            declaration_version="1", microstep_order=_W2_STEPS,
            slice_boundary="x",
            nodes=(readout_node, boundary_node),
            parameters=(),
            exogenous_sources=(),
            mechanisms=(_mech("r.m", "r", lambda: 1.0),),
        )
        table = MiracleTable(registry)
        with pytest.raises(ValueError, match="不可独立干预"):
            table.commit(MiracleRecord(
                target_space="node", target="r", rep="value",
                value=1.0, frame_t0=0, duration=1,
            ))
        with pytest.raises(ValueError, match="不可干预"):
            table.commit(MiracleRecord(
                target_space="node", target="b", rep="value",
                value=1.0, frame_t0=0, duration=1,
            ))

    def test_mechanism_parents_must_be_subset(self):
        table = MiracleTable(build_w1_registry())
        replacement = _mech(
            "w1.med_bad", "med",
            lambda rt, u_med, u_ind: rt + u_med + u_ind,
            parents=(_parent("rt", "rt"), _parent("U_ind", "u_ind")),
            random_sources=(_rand("U_med", "u_med"),),
        )
        with pytest.raises(ValueError, match="未声明"):
            table.commit(MiracleRecord(
                target_space="node", target="med", rep="mechanism",
                mechanism=replacement, frame_t0=1,
            ))

    def test_mechanism_output_mismatch(self):
        table = MiracleTable(build_w2_registry())
        with pytest.raises(ValueError, match="输出"):
            table.commit(MiracleRecord(
                target_space="node", target="x", rep="mechanism",
                mechanism=_mech("other", "out", lambda: 1.0),
                frame_t0=1,
            ))

    def test_mechanism_random_overlap_rejected(self):
        class _StubRegistry:
            """仅支撑随机源重叠校验的最小桩（C1 不支持随机源的注册表）。"""

            def __init__(self):
                self.nodes = {"med": _global_node("med", microstep=_S2)}
                self.parameters = {}
                self.exogenous_sources = {}
                self.mechanisms = {
                    "w1.med": _mech(
                        "w1.med", "med",
                        lambda rt, u_med: rt + u_med,
                        parents=(_parent("rt", "rt"),),
                        random_sources=(_rand("U_med", "u_med"),),
                    ),
                    "w1.ind": _mech(
                        "w1.ind", "ind",
                        lambda u_ind: u_ind,
                        random_sources=(_rand("U_ind", "u_ind"),),
                    ),
                }
                self._by_output = dict(self.mechanisms)

            def mechanism_for(self, target):
                for mechanism in self.mechanisms.values():
                    if mechanism.output == target:
                        return mechanism
                raise KeyError(target)

        table = MiracleTable(_StubRegistry())
        replacement = _mech(
            "w1.med_steal", "med",
            lambda rt, u_ind: rt + u_ind,
            parents=(_parent("rt", "rt"),),
            random_sources=(_rand("U_ind", "u_ind"),),
        )
        with pytest.raises(ValueError, match="重叠"):
            table.commit(MiracleRecord(
                target_space="node", target="med", rep="mechanism",
                mechanism=replacement, frame_t0=1,
            ))

    def test_parameter_intervention_rules(self):
        parameter = ParameterSpec(
            parameter_id="p", value_type="float", unit="u",
            bounds=(0.0, 10.0), value=5.0, version="v1",
            intervention_allowed=True, source="code-only:test",
        )
        registry = MechanismRegistry(
            schema_version=3, declaration_id="research.w2p",
            declaration_version="1", microstep_order=_W2_STEPS,
            slice_boundary="x", nodes=(), parameters=(parameter,),
            exogenous_sources=(), mechanisms=(),
        )
        table = MiracleTable(registry)
        record = table.commit(MiracleRecord(
            target_space="parameter", target="p", rep="value",
            value=7.0, frame_t0=0,
        ))
        assert record.environment_change is True
        assert table.resolve_parameter("p", 5) == (True, 7.0)
        with pytest.raises(ValueError, match="长期"):
            table.commit(MiracleRecord(
                target_space="parameter", target="p", rep="value",
                value=7.0, frame_t0=0, duration=5,
            ))
        with pytest.raises(ValueError, match="bounds"):
            table.commit(MiracleRecord(
                target_space="parameter", target="p", rep="value",
                value=99.0, frame_t0=0,
            ))
        forbidden = ParameterSpec(
            parameter_id="q", value_type="float", unit="u",
            bounds=None, value=1.0, version="v1",
            intervention_allowed=False, source="code-only:test",
        )
        registry2 = MechanismRegistry(
            schema_version=3, declaration_id="research.w2p",
            declaration_version="1", microstep_order=_W2_STEPS,
            slice_boundary="x", nodes=(), parameters=(forbidden,),
            exogenous_sources=(), mechanisms=(),
        )
        table2 = MiracleTable(registry2)
        with pytest.raises(ValueError, match="不允许神迹"):
            table2.commit(MiracleRecord(
                target_space="parameter", target="q", rep="value",
                value=2.0, frame_t0=0,
            ))


# ── 覆盖感知求值器 ───────────────────────────────────────────

class TestMiracleEvaluator:
    def test_value_override_short_circuits(self):
        table = MiracleTable(build_w2_registry())
        table.commit(MiracleRecord(
            target_space="node", target="x", rep="value",
            value=10.0, frame_t0=1, duration=1,
        ))
        evaluator = MiracleEvaluator(build_w2_registry(), table)
        assert evaluator.evaluate(
            "x", {"x": 0.0}, frame=1, instance=(),
        ) == 10.0
        assert evaluator.evaluate(
            "x", {"x": 0.0}, frame=2, instance=(),
        ) == 1.0

    def test_passthrough_without_table(self):
        evaluator = MiracleEvaluator(build_w2_registry())
        assert evaluator.evaluate("x", {"x": 1.0}) == 2.0

    def test_parameter_override_flows_into_binding(self):
        from ascend.causal import ParameterBinding

        parameter = ParameterSpec(
            parameter_id="p", value_type="float", unit="u",
            bounds=(0.0, 100.0), value=5.0, version="v1",
            intervention_allowed=True, source="code-only:test",
        )
        mech = MechanismSpec(
            mechanism_id="y.m", output="y", equation="y",
            function=lambda p: p * 2.0,
            parents=(),
            parameters=(ParameterBinding("p", "p"),),
            random_sources=(),
            boundary_cases=("declared",),
            source_dependencies=(),
            witnesses=(),
        )
        registry = MechanismRegistry(
            schema_version=3, declaration_id="research.w2p",
            declaration_version="1", microstep_order=_W2_STEPS,
            slice_boundary="x",
            nodes=(_global_node("y", microstep=_S1),),
            parameters=(parameter,),
            exogenous_sources=(),
            mechanisms=(mech,),
        )
        table = MiracleTable(registry)
        table.commit(MiracleRecord(
            target_space="parameter", target="p", rep="value",
            value=7.0, frame_t0=1,
        ))
        evaluator = MiracleEvaluator(registry, table)
        assert evaluator.evaluate("y", {}, frame=1) == 14.0
        assert evaluator.evaluate("y", {}, frame=0) == 10.0


# ── CRN 随机流契约 ───────────────────────────────────────────

class TestCRN:
    def test_value_override_reads_no_boundary_inputs(self):
        table = MiracleTable(build_w1_registry())
        table.commit(MiracleRecord(
            target_space="node", target="med", rep="value",
            value=5.0, frame_t0=2, duration=1,
        ))
        _, executor = _run_w1(3, table)
        # 帧 2：med 值神迹命中 → rt 与 U_med 均不被读取（断入边，CRN）
        assert ("U_med", 2, ()) not in executor.reads
        assert ("rt", 2, ()) not in executor.reads
        assert ("U_med", 1, ()) in executor.reads
        assert ("U_med", 3, ()) in executor.reads
        for source in ("U_rt", "U_out", "U_ind"):
            assert (source, 2, ()) in executor.reads

    def test_mechanism_override_reads_own_parents_only(self):
        table = MiracleTable(build_w1_registry())
        replacement = _mech(
            "w1.med_alt", "med",
            lambda rt: rt * 10.0,
            parents=(_parent("rt", "rt"),),
        )
        table.commit(MiracleRecord(
            target_space="node", target="med", rep="mechanism",
            mechanism=replacement, frame_t0=2,
        ))
        _, executor = _run_w1(3, table)
        assert ("U_med", 2, ()) not in executor.reads
        assert ("rt", 2, ()) in executor.reads


# ── W1：节点神迹与 CRN 验收（04 §3.2）────────────────────────

class TestW1:
    def _baseline(self):
        trajectory, executor = _run_w1(3)
        return trajectory[-1], executor

    def test_w1_node_do(self):
        table = MiracleTable(build_w1_registry())
        table.commit(MiracleRecord(
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
        table = MiracleTable(build_w1_registry())
        table.commit(MiracleRecord(
            target_space="node", target="med", rep="value",
            value=5.0, frame_t0=2, duration=1,
        ))
        table.commit(MiracleRecord(
            target_space="node", target="rt", rep="value",
            value=999.0, frame_t0=2, duration=1,
        ))
        trajectory, _ = _run_w1(3, table)
        assert trajectory[1]["med"] == 5.0
        assert trajectory[1]["rt"] == 999.0

    def test_w1_persistent_window(self):
        table = MiracleTable(build_w1_registry())
        table.commit(MiracleRecord(
            target_space="node", target="med", rep="value",
            value=5.0, frame_t0=2, duration=2,
        ))
        trajectory, _ = _run_w1(3, table)
        assert trajectory[0]["med"] == 3.0
        assert trajectory[1]["med"] == 5.0
        assert trajectory[2]["med"] == 5.0
        assert trajectory[2]["out"] == 5.0 + _W1_BOUNDARY["U_out"]


# ── W2：三类神迹互异 + Lean 数值对拍（04 §3.3）───────────────

class TestW2:
    def test_three_trajectories_distinct(self):
        baseline, _ = _run_w2()

        table1 = MiracleTable(build_w2_registry())
        table1.commit(MiracleRecord(
            target_space="node", target="x", rep="value",
            value=10.0, frame_t0=1, duration=1,
        ))
        node_do, _ = _run_w2(table1)

        table2 = MiracleTable(build_w2_registry())
        table2.commit(MiracleRecord(
            target_space="node", target="x", rep="value",
            value=10.0, frame_t0=1, duration=2,
        ))
        persist, _ = _run_w2(table2)

        table3 = MiracleTable(build_w2_registry())
        table3.commit(MiracleRecord(
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
        table = MiracleTable(build_w2_registry())
        table.commit(MiracleRecord(
            target_space="node", target="x", rep="value",
            value=10.0, frame_t0=1, duration=1,
        ))
        node_do, _ = _run_w2(table)
        assert node_do[0]["x"] == 10.0   # nodeDo_t1
        assert node_do[1]["x"] == 11.0   # nodeDo_t2 (F(10)=11)

        table = MiracleTable(build_w2_registry())
        table.commit(MiracleRecord(
            target_space="node", target="x", rep="value",
            value=10.0, frame_t0=1, duration=2,
        ))
        persist, _ = _run_w2(table)
        assert persist[1]["x"] == 10.0   # persist2_t2
        assert persist[2]["x"] == 11.0   # persist2_t3

        table = MiracleTable(build_w2_registry())
        table.commit(MiracleRecord(
            target_space="node", target="x", rep="mechanism",
            mechanism=_mech_x_plus_100(), frame_t0=1,
        ))
        mech_do, _ = _run_w2(table)
        assert mech_do[0]["x"] == 100.0  # mech_t1
        assert mech_do[1]["x"] == 200.0  # mech_t2

    def test_lean_before_eq_traj(self):
        """nodeDo_before_eq_traj：干预帧之前轨迹与基线一致。"""
        baseline, _ = _run_w2()
        table = MiracleTable(build_w2_registry())
        table.commit(MiracleRecord(
            target_space="node", target="x", rep="value",
            value=10.0, frame_t0=2, duration=1,
        ))
        node_do, _ = _run_w2(table)
        assert node_do[0]["x"] == baseline[0]["x"]

    def test_lean_mech_same_eq_traj(self):
        """mechDo_same_eq_traj：F'=F 时与无干预轨迹一致（替换惰性）。"""
        baseline, _ = _run_w2()
        table = MiracleTable(build_w2_registry())
        table.commit(MiracleRecord(
            target_space="node", target="x", rep="mechanism",
            mechanism=_mech_x_plus_1(), frame_t0=1,
        ))
        mech_do, _ = _run_w2(table)
        assert [row["x"] for row in mech_do] == [row["x"] for row in baseline]

    def test_value_pin_until_cleared(self):
        """长期值神迹持续钉住，clear 后恢复原机制。"""
        table = MiracleTable(build_w2_registry())
        table.commit(MiracleRecord(
            target_space="node", target="x", rep="value",
            value=10.0, frame_t0=1, duration=None,
        ))
        trajectory, _ = _run_w2(table)
        assert [row["x"] for row in trajectory] == [10.0, 10.0, 10.0]
        table.clear("node", "x", rep="value")
        baseline, _ = _run_w2()
        assert [row["x"] for row in _run_w2(table)[0]] == [
            row["x"] for row in baseline
        ]