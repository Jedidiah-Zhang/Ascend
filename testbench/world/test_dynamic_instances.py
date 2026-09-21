"""动态实例测试 — chunk 流式物化。

覆盖：物化/卸载、逐实例求值、外部输入按实例映射、global 广播、
逐实例 lag 历史、快照/恢复（含物化集合）、物化无关。
"""

from __future__ import annotations

import pytest

from olam import (
    DynamicField,
    FrameFailure,
    InstanceDecl,
    MechanismDecl,
    ModulePack,
    ParameterDecl,
    Parent,
    Permissions,
    Schedule,
    SlotDecl,
    ValueDomain,
    When,
    Witness,
    WorldProcess,
    WorldSpec,
    compile_world,
)
from olam.modules.primitives import GLOBAL

_INT = ValueDomain(kind="int", bits=32)
_DYN = InstanceDecl(
    id="lattice.dyn", kind="lattice", identity="xy", size=None,
    axes=("x",),
)


def _hold_impl(ctx: object) -> int:
    return ctx.parent("a") + 1


def _double_impl(ctx: object) -> int:
    return ctx.parent("a") * 2


def _sum_impl(ctx: object) -> int:
    return ctx.parent("x") + ctx.parent("base")


def _offset_impl(ctx: object) -> int:
    return ctx.parent("x") + ctx.param("t.offset")


def _pack() -> ModulePack:
    return ModulePack(
        id="test.dyn",
        version="1",
        instances=(GLOBAL, _DYN),
        slots=(
            SlotDecl(
                id="t.a", on="lattice.dyn", persist="state", domain=_INT,
                permissions=Permissions(intervene=True, observe=True),
                writer="t.a.hold", initial=7,
            ),
            SlotDecl(
                id="t.b", on="lattice.dyn", persist="derived", domain=_INT,
                writer="t.b.calc", recompute="b = a * 2",
            ),
            SlotDecl(
                id="t.c", on="lattice.dyn", persist="derived", domain=_INT,
                writer="t.c.calc", recompute="c = x + base",
            ),
            SlotDecl(
                id="t.p", on="lattice.dyn", persist="derived", domain=_INT,
                writer="t.p.calc", recompute="p = x + offset",
            ),
            SlotDecl(
                id="t.x", on="lattice.dyn", persist="external", domain=_INT,
                permissions=Permissions(observe=True),
            ),
            SlotDecl(
                id="t.base", on="global", persist="external", domain=_INT,
                permissions=Permissions(observe=True),
            ),
        ),
        mechanisms=(
            MechanismDecl(
                id="t.a.hold",
                output="t.a",
                parents=(Parent("t.a", "a", lag=2),),
                impl=_hold_impl,
                when=When("phase", "one"),
                witnesses=(
                    Witness("a", {"a": 1}, (2,)),
                    Witness("b", {"a": 2}, (3,)),
                ),
            ),
            MechanismDecl(
                id="t.c.calc",
                output="t.c",
                parents=(
                    Parent("t.x", "x"),
                    Parent("t.base", "base"),
                ),
                impl=_sum_impl,
                when=When("phase", "one"),
                witnesses=(
                    Witness("a", {"x": 1, "base": 10}, (11,)),
                    Witness("b", {"x": 2, "base": 10}, (12,)),
                    Witness("c", {"x": 1, "base": 20}, (21,)),
                ),
            ),
            MechanismDecl(
                id="t.b.calc",
                output="t.b",
                parents=(Parent("t.a", "a"),),
                impl=_double_impl,
                when=When("phase", "two"),
                witnesses=(
                    Witness("a", {"a": 1}, (2,)),
                    Witness("b", {"a": 2}, (4,)),
                ),
            ),
            MechanismDecl(
                id="t.p.calc",
                output="t.p",
                parents=(Parent("t.x", "x"),),
                impl=_offset_impl,
                when=When("phase", "one"),
                params=("t.offset",),
                witnesses=(
                    Witness("a", {"x": 1}, (1,), {"t.offset": 0}),
                    Witness("b", {"x": 2}, (2,), {"t.offset": 0}),
                    Witness("c", {"x": 1}, (6,), {"t.offset": 5}),
                ),
            ),
        ),
        parameters=(
            ParameterDecl(
                id="t.offset", default=0, minimum=0, maximum=100,
            ),
        ),
        evidence=("动态实例运行时测试",),
    )


@pytest.fixture()
def program():
    return compile_world(
        WorldSpec(modules=(_pack(),), schedule=Schedule(phases=("one", "two")))
    )


def _inputs(x_values: dict, base: int = 10) -> dict:
    return {"t.x": x_values, "t.base": base}


class TestDynamicEvaluation:
    def test_materialize_seeds_initial_and_evaluates(self, program):
        process = WorldProcess(program)
        process.materialize("lattice.dyn", (0, 0))
        process.materialize("lattice.dyn", (1, 0))
        process.step(inputs=_inputs({(0, 0): 1, (1, 0): 2}))
        assert process.materialized("lattice.dyn") == ((0, 0), (1, 0))
        assert process.committed("t.a").get((0, 0)) == 8
        assert process.committed("t.a").get((1, 0)) == 8
        assert process.committed("t.b").get((0, 0)) == 16
        assert process.committed("t.c").get((0, 0)) == 11
        assert process.committed("t.c").get((1, 0)) == 12

    def test_lag_history_per_instance(self, program):
        process = WorldProcess(program)
        process.materialize("lattice.dyn", (0, 0))
        values = []
        for _ in range(4):
            process.step(inputs=_inputs({(0, 0): 0}))
            values.append(process.committed("t.a").get((0, 0)))
        assert values == [8, 8, 9, 9]

    def test_dematerialize_drops_instance(self, program):
        process = WorldProcess(program)
        process.materialize("lattice.dyn", (0, 0))
        process.materialize("lattice.dyn", (1, 0))
        process.step(inputs=_inputs({(0, 0): 1, (1, 0): 2}))
        process.dematerialize("lattice.dyn", (1, 0))
        process.step(inputs=_inputs({(0, 0): 1}))
        assert process.materialized("lattice.dyn") == ((0, 0),)
        assert process.committed("t.b").coords() == ((0, 0),)

    def test_no_materialized_instances(self, program):
        process = WorldProcess(program)
        process.step(inputs={"t.base": 10})
        assert process.committed("t.b").coords() == ()

    def test_missing_instance_input_is_frame_failure(self, program):
        process = WorldProcess(program)
        process.materialize("lattice.dyn", (0, 0))
        with pytest.raises(FrameFailure):
            process.step(inputs=_inputs({}))

    def test_per_instance_intervention_cuts_edges(self, program):
        process = WorldProcess(program)
        process.materialize("lattice.dyn", (0, 0))
        process.materialize("lattice.dyn", (1, 0))
        process.step(
            inputs=_inputs({(0, 0): 1, (1, 0): 2}),
            interventions={"t.a": {(0, 0): 100}},
        )
        assert process.committed("t.a").get((0, 0)) == 100
        assert process.committed("t.a").get((1, 0)) == 8
        assert process.committed("t.b").get((0, 0)) == 200
        assert process.committed("t.b").get((1, 0)) == 16

    def test_intervention_validation(self, program):
        process = WorldProcess(program)
        process.materialize("lattice.dyn", (0, 0))
        with pytest.raises(FrameFailure):
            process.step(
                inputs=_inputs({(0, 0): 1}),
                interventions={"t.base": 1},
            )
        with pytest.raises(FrameFailure):
            process.step(
                inputs=_inputs({(0, 0): 1}),
                interventions={"t.a": {(5, 5): 1}},
            )

    def test_parameter_override(self, program):
        process = WorldProcess(program)
        process.materialize("lattice.dyn", (0, 0))
        process.step(
            inputs=_inputs({(0, 0): 1}),
            parameters={"t.offset": 5},
        )
        assert process.committed("t.p").get((0, 0)) == 6
        process.step(inputs=_inputs({(0, 0): 1}))
        assert process.committed("t.p").get((0, 0)) == 1

    def test_materialization_independent(self, program):
        first = WorldProcess(program)
        first.materialize("lattice.dyn", (0, 0))
        first.step(inputs=_inputs({(0, 0): 5}))
        second = WorldProcess(program)
        second.materialize("lattice.dyn", (0, 0))
        second.materialize("lattice.dyn", (9, 9))
        second.step(inputs=_inputs({(0, 0): 5, (9, 9): 1}))
        assert second.committed("t.b").get((0, 0)) == (
            first.committed("t.b").get((0, 0))
        )


class TestDynamicSnapshot:
    def test_snapshot_restore_keeps_materialized_and_values(self, program):
        process = WorldProcess(program)
        process.materialize("lattice.dyn", (0, 0))
        process.materialize("lattice.dyn", (2, 1))
        for _ in range(3):
            process.step(inputs=_inputs({(0, 0): 1, (2, 1): 2}))
        snapshot = process.snapshot()
        assert snapshot["materialized"]["lattice.dyn"] == [[0, 0], [2, 1]]
        restored = WorldProcess.restore(program, snapshot)
        assert restored.materialized("lattice.dyn") == ((0, 0), (2, 1))
        assert restored.committed("t.a") == process.committed("t.a")
        process.step(inputs=_inputs({(0, 0): 1, (2, 1): 2}))
        restored.step(inputs=_inputs({(0, 0): 1, (2, 1): 2}))
        assert restored.committed("t.b") == process.committed("t.b")

    def test_snapshot_excludes_derived(self, program):
        process = WorldProcess(program)
        process.materialize("lattice.dyn", (0, 0))
        process.step(inputs=_inputs({(0, 0): 1}))
        states = process.snapshot()["states"]
        assert "t.a" in states
        assert "t.b" not in states
        assert isinstance(states["t.a"], dict)
        assert states["t.a"]["kind"] == "dynamic"
