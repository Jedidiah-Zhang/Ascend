"""研究 trace 契约测试 — 记录完整性、fail-closed 与"日志可重算任意节点"。

契约来源：第一阶段实施定义 §8（研究日志清单）、issue #46 P3
（TraceRecord + fail-closed + 与玩法事件分库 + 可重算）。

研究切片世界：x_{t+1} = x_t + 1（滞后 1），同帧派生 y = 2x。
注册表与干预表由本文件自建，不依赖生产天气声明。
"""

from __future__ import annotations

import pytest

from ascend.causal import (
    AccessPolicy,
    DependencyWitness,
    InstanceDomain,
    InterventionRecord,
    InterventionTable,
    MathMetadata,
    MechanismRegistry,
    MechanismSpec,
    NodeSpec,
    ParentSpec,
    RandomAddress,
    StateOwnership,
    TraceLog,
    TraceRecord,
    UpdateContract,
    ValueDomain,
)
from ascend.causal.intervention_engine import InterventionEvaluator

_S1, _S2 = "s1", "s2"
_STEPS = (_S1, _S2)


def _node(node_id: str, *, microstep: str = _S1) -> NodeSpec:
    return NodeSpec(
        node_id=node_id,
        role="mechanism_state",
        origin="mechanism",
        instance_domain=InstanceDomain(
            kind="global_singleton",
            axes=(),
            creation="world_initialization",
            destruction="world_teardown",
        ),
        value=ValueDomain(
            kind="float", unit="u", bounds=(-1000.0, 1000.0), choices=(),
            missing="forbidden", quantization="continuous",
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
            interventions=("node", "persistent", "mechanism"),
            research_trace=True,
            observation_protocols=("research.full.v1",),
        ),
        math=MathMetadata(
            error_budget=1.0, metric="absolute_difference", valid_domain="full",
        ),
    )


def _parent(parent: str, argument: str, *, lag: int = 0) -> ParentSpec:
    return ParentSpec(
        parent=parent, argument=argument, lag=lag,
        source_microstep=_S1,
        spatial_offsets=((0,),), entity_relation="self",
        aggregation="identity", broadcast="identity",
        boundary_operator="none", guard="always", lipschitz=1.0,
        metric="absolute_difference", valid_domain="full",
        analysis_role="forward",
    )


def _witness(parent: str, inputs, alternate, expected) -> DependencyWitness:
    return DependencyWitness(
        label=f"w_{parent}", parent=parent, inputs=tuple(inputs),
        alternate_value=alternate, expected_outputs=tuple(expected),
    )


def _registry() -> MechanismRegistry:
    return MechanismRegistry(
        schema_version=3,
        declaration_id="research.trace",
        declaration_version="1",
        microstep_order=_STEPS,
        slice_boundary="研究切片世界（trace 构造）",
        wired_nodes=frozenset({"x", "y"}),
        nodes=(_node("x"), _node("y", microstep=_S2)),
        parameters=(),
        exogenous_sources=(),
        mechanisms=(
            MechanismSpec(
                mechanism_id="trace.inc1", output="x", equation="x_prev + 1",
                function=lambda x_prev: x_prev + 1,
                parents=(_parent("x", "x_prev", lag=1),),
                parameters=(), random_sources=(), boundary_cases=("declared",),
                source_dependencies=(),
                witnesses=(_witness("x", (("x", 1.0),), 2.0, (2.0, 3.0)),),
            ),
            MechanismSpec(
                mechanism_id="trace.double", output="y", equation="2 * x",
                function=lambda x: 2.0 * x,
                parents=(_parent("x", "x"),),
                parameters=(), random_sources=(), boundary_cases=("declared",),
                source_dependencies=(),
                witnesses=(_witness("x", (("x", 1.0),), 2.0, (2.0, 4.0)),),
            ),
        ),
    )


def _registry_without(mechanism_id: str) -> MechanismRegistry:
    """同构注册表但把指定机制改名（模拟声明已变，结构不变）。"""
    registry = _registry()
    mechanisms = tuple(
        MechanismSpec(
            mechanism_id=(
                f"{spec.mechanism_id}.renamed"
                if spec.mechanism_id == mechanism_id else spec.mechanism_id
            ),
            output=spec.output, equation=spec.equation,
            function=spec.function, parents=spec.parents,
            parameters=spec.parameters, random_sources=spec.random_sources,
            boundary_cases=spec.boundary_cases,
            source_dependencies=spec.source_dependencies,
            witnesses=spec.witnesses,
        )
        for spec in registry.mechanisms.values()
    )
    return MechanismRegistry(
        schema_version=3,
        declaration_id="research.trace",
        declaration_version="1",
        microstep_order=_STEPS,
        slice_boundary="研究切片世界（trace 构造）",
        wired_nodes=registry.wired_nodes,
        nodes=tuple(registry.nodes.values()),
        parameters=(),
        exogenous_sources=(),
        mechanisms=mechanisms,
    )


@pytest.fixture()
def registry() -> MechanismRegistry:
    return _registry()


@pytest.fixture()
def log(registry) -> TraceLog:
    return TraceLog(registry, capacity=8)


def _record(**overrides) -> TraceRecord:
    base = dict(
        node_id="x", frame=1, instance=(), microstep=_S1,
        mechanism_id="trace.inc1", equation_version="sha256:aa",
        resolved_version="sha256:bb",
        parents=(("x", 1.0),), parameters=(), random_addresses=(),
        random_values=(), intervention=None, rep=None, output=2.0,
        boundary=("declared",),
    )
    base.update(overrides)
    return TraceRecord(**base)


class TestRecordShape:
    """记录字段与序列化视图。"""

    def test_address_plain_round_trip(self):
        address = RandomAddress(
            source="u_weather", frame=7, instance=(1, 2), draw_index=3,
        )
        assert address.plain() == {
            "source": "u_weather", "frame": 7,
            "instance": [1, 2], "draw_index": 3,
        }

    def test_record_plain_is_json_safe(self):
        import json
        entry = TraceRecord(
            node_id="x", frame=3, instance=(), microstep=_S1,
            mechanism_id="trace.inc1", equation_version="sha256:aa",
            resolved_version="sha256:bb",
            parents=(("x", 1.0),), parameters=(),
            random_addresses=(RandomAddress("u", 3),),
            random_values=((RandomAddress("u", 3), 0.5),),
            intervention=None, rep=None, output=2.0,
            boundary=("declared",),
        )
        view = entry.plain()
        assert json.loads(json.dumps(view)) == view
        assert view["parents"] == {"x": 1.0}
        assert view["random_addresses"][0]["source"] == "u"


class TestFailClosed:
    """记录不完整即拒绝（fail-closed）。"""

    def test_valid_record_accepted(self, log):
        assert log.record(_record()) is not None
        assert len(log) == 1

    def test_unknown_node_rejected(self, log):
        with pytest.raises(ValueError, match="节点未声明"):
            log.record(_record(node_id="ghost"))

    def test_missing_microstep_rejected(self, log):
        with pytest.raises(ValueError, match="缺少更新阶段"):
            log.record(_record(microstep=""))

    def test_wrong_microstep_rejected(self, log):
        with pytest.raises(ValueError, match="更新阶段与声明不符"):
            log.record(_record(microstep=_S2))

    def test_missing_equation_version_rejected(self, log):
        with pytest.raises(ValueError, match="缺少方程版本"):
            log.record(_record(equation_version=""))

    def test_unknown_mechanism_rejected(self, log):
        with pytest.raises(ValueError, match="机制未登记"):
            log.record(_record(mechanism_id="nope"))

    def test_mechanism_output_mismatch_rejected(self, log):
        with pytest.raises(ValueError, match="机制输出与节点不符"):
            log.record(_record(mechanism_id="trace.double"))

    def test_parent_set_mismatch_rejected(self, log):
        with pytest.raises(ValueError, match="父值不匹配"):
            log.record(_record(parents=(("y", 1.0),)))

    def test_value_override_needs_output(self, log):
        with pytest.raises(ValueError, match="缺少输出"):
            log.record(_record(rep="value", mechanism_id="", output=None))

    def test_value_override_skips_equation_checks(self, log):
        entry = log.record(_record(
            rep="value", mechanism_id="", equation_version="", output=5.0,
        ))
        assert entry.rep == "value"

    def test_capacity_evicts_oldest(self, registry):
        log = TraceLog(registry, capacity=2)
        for frame in (1, 2, 3):
            log.record(_record(frame=frame))
        assert [entry.frame for entry in log.records()] == [2, 3]


class TestReplay:
    """日志可重算任意节点（实施定义 §8 的可执行断言）。"""

    def test_replay_matches_recorded_output(self, log):
        entry = log.record(_record(parents=(("x", 41.0),), output=42.0))
        assert log.replay(entry) == 42.0
        assert log.verify(entry) is True
        assert log.verify_all() == []

    def test_replay_detects_divergence(self, log):
        """记录被篡改（父值与输出不一致）时重算必须报不一致。"""
        entry = log.record(_record(parents=(("x", 41.0),), output=99.0))
        assert log.verify(entry) is False
        assert log.verify_all() == [entry]

    def test_replay_value_override_returns_recorded_value(self, log):
        entry = log.record(_record(
            rep="value", mechanism_id="", equation_version="",
            parents=(), output=7.0,
        ))
        assert log.replay(entry) == 7.0
        assert log.verify(entry) is True

    def test_replay_unknown_mechanism_raises(self):
        """记录引用的机制在另一个注册表里不存在 → 重算显式报错。"""
        entry = _record()
        other = TraceLog(_registry_without("trace.inc1"))
        with pytest.raises(KeyError, match="未登记"):
            other.replay(entry)

    def test_clear_returns_count(self, log):
        log.record(_record())
        log.record(_record(frame=2))
        assert log.clear() == 2
        assert len(log) == 0


class TestEvaluatorTracing:
    """求值点接线：正常求值 / 值干预都留下完整记录。"""

    def test_plain_evaluation_recorded(self, registry):
        log = TraceLog(registry)
        evaluator = InterventionEvaluator(registry, trace=log)
        assert evaluator.evaluate("x", {"x": 1.0}, frame=5, instance=()) == 2.0
        entry = log.records(node_id="x")[-1]
        assert entry.frame == 5
        assert entry.mechanism_id == "trace.inc1"
        assert entry.equation_version == registry.equation_version("x")
        assert dict(entry.parents) == {"x": 1.0}
        assert entry.rep is None and entry.intervention is None
        assert log.verify_all() == []

    def test_value_intervention_recorded(self, registry):
        table = InterventionTable(registry, now=lambda: 0)
        table.commit(InterventionRecord(
            target_space="node", target="x", rep="value",
            value=30.0, frame_t0=1, duration=None,
        ))
        log = TraceLog(registry)
        evaluator = InterventionEvaluator(registry, table, trace=log)
        assert evaluator.evaluate("x", {"x": 1.0}, frame=1, instance=()) == 30.0
        entry = log.records(node_id="x")[-1]
        assert entry.rep == "value"
        assert entry.mechanism_id == "" and entry.equation_version == ""
        assert entry.intervention is not None
        assert entry.intervention["target"] == "x"
        assert entry.output == 30.0
        assert log.verify(entry) is True

    def test_trace_does_not_change_output(self, registry):
        """开启 trace 不改变任何求值结果（纯观察层）。"""
        table = InterventionTable(registry, now=lambda: 0)
        plain = InterventionEvaluator(registry, table)
        traced = InterventionEvaluator(registry, table, trace=TraceLog(registry))
        for frame in range(1, 4):
            assert plain.evaluate("x", {"x": 1.0}, frame=frame) == \
                traced.evaluate("x", {"x": 1.0}, frame=frame)

    def test_no_trace_means_no_records(self, registry):
        evaluator = InterventionEvaluator(registry)
        evaluator.evaluate("x", {"x": 1.0}, frame=1)
        assert evaluator.trace is None
