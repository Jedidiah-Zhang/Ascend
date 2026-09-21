"""运行时测试 — 帧事务、干预、时间模式、场与快照。"""

from __future__ import annotations

import pytest

from olam.compile import compile_world
from olam.meta.declarations import (
    InstanceDecl,
    InvariantDecl,
    MechanismDecl,
    ModulePack,
    Parent,
    Permissions,
    RelationDecl,
    Schedule,
    SlotDecl,
    ValueDomain,
    When,
    Witness,
    WorldSpec,
)
from olam.modules import toy
from olam.modules.primitives import GLOBAL
from olam.runtime import (
    FrameFailure,
    LatticeField,
    WorldInvalidatedError,
    WorldProcess,
)

_INT = ValueDomain(kind="int", bits=32)
_PHASES = ("stage1", "stage2", "stage3")


def _div_impl(ctx: object) -> int:
    return 100 // ctx.parent("d")


def _fail_pack(invariant: InvariantDecl | None = None) -> ModulePack:
    return ModulePack(
        id="test.fail",
        version="1",
        instances=(GLOBAL,),
        slots=(
            SlotDecl(
                id="t.den", on="global", persist="external", domain=_INT,
                permissions=Permissions(observe=True),
            ),
            SlotDecl(
                id="t.q", on="global", persist="state", domain=_INT,
                writer="t.q.update",
            ),
        ),
        mechanisms=(
            MechanismDecl(
                id="t.q.update",
                output="t.q",
                parents=(Parent("t.den", "d"),),
                impl=_div_impl,
                when=When("phase", "main"),
                witnesses=(
                    Witness("ok", {"d": 5}, (20,)),
                    Witness("changed", {"d": 4}, (25,)),
                ),
            ),
        ),
        invariants=() if invariant is None else (invariant,),
    )


def _mean_impl(ctx: object) -> int:
    return ctx.parent("pair")


def _hold_impl(ctx: object) -> int:
    return ctx.parent("f")


def _mean_pack() -> ModulePack:
    return ModulePack(
        id="test.mean",
        version="1",
        instances=(
            GLOBAL,
            InstanceDecl(
                id="lattice.line", kind="lattice", identity="xy", size=(3,),
            ),
        ),
        relations=(
            RelationDecl(
                id="t.pair", kind="spatial", source="lattice.line",
                target="lattice.line", offsets=((-1,), (1,)), boundary="clamp",
            ),
        ),
        slots=(
            SlotDecl(
                id="t.f", on="lattice.line", persist="state", domain=_INT,
                writer="t.f.hold",
            ),
            SlotDecl(
                id="t.m", on="lattice.line", persist="state", domain=_INT,
                writer="t.m.mean",
            ),
        ),
        mechanisms=(
            MechanismDecl(
                id="t.f.hold",
                output="t.f",
                parents=(Parent("t.f", "f", lag=1),),
                impl=_hold_impl,
                when=When("phase", "hold"),
                witnesses=(
                    Witness("a", {"f": 1}, (1,)),
                    Witness("b", {"f": 2}, (2,)),
                ),
            ),
            MechanismDecl(
                id="t.m.mean",
                output="t.m",
                parents=(
                    Parent(
                        "t.f", "pair", relation="t.pair",
                        aggregation="mean",
                    ),
                ),
                impl=_mean_impl,
                when=When("phase", "main"),
                witnesses=(
                    Witness("a", {"pair": 2}, (2,)),
                    Witness("b", {"pair": 3}, (3,)),
                ),
            ),
        ),
    )


def _lag_impl(ctx: object) -> int:
    return ctx.parent("h") + 1


def _lag_pack() -> ModulePack:
    return ModulePack(
        id="test.lag",
        version="1",
        instances=(GLOBAL,),
        slots=(
            SlotDecl(
                id="t.h", on="global", persist="state", domain=_INT,
                writer="t.h.step", initial=0,
            ),
        ),
        mechanisms=(
            MechanismDecl(
                id="t.h.step",
                output="t.h",
                parents=(Parent("t.h", "h", lag=2),),
                impl=_lag_impl,
                when=When("phase", "main"),
                witnesses=(
                    Witness("a", {"h": 0}, (1,)),
                    Witness("b", {"h": 1}, (2,)),
                ),
            ),
        ),
    )


def _double_impl(ctx: object) -> int:
    return ctx.parent("a") * 2


def _derived_pack() -> ModulePack:
    return ModulePack(
        id="test.derived",
        version="1",
        instances=(GLOBAL,),
        slots=(
            SlotDecl(
                id="t.a", on="global", persist="state", domain=_INT,
                writer="t.a.hold", initial=3,
            ),
            SlotDecl(
                id="t.d", on="global", persist="derived", domain=_INT,
                writer="t.d.calc", recompute="d = a * 2",
            ),
        ),
        mechanisms=(
            MechanismDecl(
                id="t.a.hold",
                output="t.a",
                parents=(Parent("t.a", "a", lag=1),),
                impl=lambda ctx: ctx.parent("a"),
                when=When("phase", "main"),
                witnesses=(
                    Witness("a", {"a": 1}, (1,)),
                    Witness("b", {"a": 2}, (2,)),
                ),
            ),
            MechanismDecl(
                id="t.d.calc",
                output="t.d",
                parents=(Parent("t.a", "a"),),
                impl=_double_impl,
                when=When("phase", "later"),
                witnesses=(
                    Witness("a", {"a": 1}, (2,)),
                    Witness("b", {"a": 2}, (4,)),
                ),
            ),
        ),
    )


class TestFrameTransaction:
    def test_success_commits_and_advances(self):
        program = compile_world(
            WorldSpec(modules=(_fail_pack(),), schedule=Schedule())
        )
        process = WorldProcess(program)
        result = process.step(inputs={"t.den": 5})
        assert result.tick == 1
        assert process.tick == 1
        assert process.committed("t.q") == 20
        assert result.writes["t.q"] == 20

    def test_frame_failure_rolls_back_and_retries(self):
        program = compile_world(
            WorldSpec(modules=(_fail_pack(),), schedule=Schedule())
        )
        process = WorldProcess(program)
        with pytest.raises(FrameFailure):
            process.step(inputs={"t.den": 0})
        assert process.tick == 0
        assert process.committed("t.q") == 0
        process.step(inputs={"t.den": 5})
        assert process.committed("t.q") == 20

    def test_record_failure_invalidates_world(self):
        program = compile_world(
            WorldSpec(
                modules=(toy.W1,), schedule=Schedule(phases=_PHASES),
            )
        )
        process = WorldProcess(program)

        def _boom(result: object) -> None:
            raise RuntimeError("record failed")

        with pytest.raises(WorldInvalidatedError):
            process.step(record=_boom)
        assert process.tick == 1
        assert process.invalidated
        with pytest.raises(WorldInvalidatedError):
            process.step()

    def test_invariant_reject_rolls_back(self):
        invariant = InvariantDecl(
            id="t.inv", slots=("t.q",),
            check=lambda view: view["t.q"] <= 10,
            severity="reject",
        )
        program = compile_world(
            WorldSpec(
                modules=(_fail_pack(invariant),), schedule=Schedule(),
            )
        )
        process = WorldProcess(program)
        with pytest.raises(FrameFailure):
            process.step(inputs={"t.den": 5})
        assert process.tick == 0
        assert process.committed("t.q") == 0

    def test_invariant_record_collects_violation(self):
        invariant = InvariantDecl(
            id="t.inv", slots=("t.q",),
            check=lambda view: view["t.q"] <= 10,
            severity="record",
        )
        program = compile_world(
            WorldSpec(
                modules=(_fail_pack(invariant),), schedule=Schedule(),
            )
        )
        process = WorldProcess(program)
        result = process.step(inputs={"t.den": 5})
        assert result.violations and process.tick == 1


class TestInterventions:
    def test_target_validation(self):
        program = compile_world(
            WorldSpec(
                modules=(toy.W1,), schedule=Schedule(phases=_PHASES),
            )
        )
        process = WorldProcess(program)
        with pytest.raises(FrameFailure):
            process.step(interventions={"toy.nope": 1})
        with pytest.raises(FrameFailure):
            process.step(interventions={"toy.rt": 1})
        external = compile_world(
            WorldSpec(modules=(_fail_pack(),), schedule=Schedule())
        )
        with pytest.raises(FrameFailure):
            WorldProcess(external).step(interventions={"t.den": 1})

    def test_cuts_edges_and_keeps_other_addresses(self):
        program = compile_world(
            WorldSpec(
                modules=(toy.W1,), schedule=Schedule(phases=_PHASES),
                seed=3,
            )
        )
        baseline = WorldProcess(program, seed=3)
        arm = WorldProcess(program, seed=3)
        baseline.step()
        arm.step(interventions={"toy.med": 100})
        assert arm.committed("toy.med") == 100
        expected = 100 + (
            baseline.committed("toy.out") - baseline.committed("toy.med")
        )
        assert arm.committed("toy.out") == expected
        assert arm.committed("toy.rt") == baseline.committed("toy.rt")
        assert arm.committed("toy.ind") == baseline.committed("toy.ind")


class TestTimeModes:
    def test_period_fires_on_multiples(self):
        program = compile_world(
            WorldSpec(
                modules=(toy.SPATIAL,),
                schedule=Schedule(
                    phases=("stage1", "stage2"), periods=(("day", 3),),
                ),
            )
        )
        process = WorldProcess(program)
        for _ in range(6):
            process.step()
        assert process.committed("toy.daycount") == 2

    def test_event_only_when_requested(self):
        program = compile_world(
            WorldSpec(
                modules=(toy.SPATIAL,),
                schedule=Schedule(
                    phases=("stage1", "stage2"), periods=(("day", 3),),
                ),
            )
        )
        process = WorldProcess(program)
        process.step()
        assert process.committed("toy.flash") == 0
        process.step(events=("flash",))
        assert process.committed("toy.flash") == 1
        process.step()
        assert process.committed("toy.flash") == 1


class TestLatticeEvaluation:
    def test_clamp_boundary_and_rounding(self):
        program = compile_world(
            WorldSpec(
                modules=(toy.SPATIAL,),
                schedule=Schedule(
                    phases=("stage1", "stage2"), periods=(("day", 24),),
                ),
            )
        )
        field = LatticeField((5,), 0)
        for index, value in enumerate((0, 10, 20, 30, 40)):
            field.set((index,), value)
        process = WorldProcess(program, initial_state={"toy.u": field})
        process.step()
        assert process.committed("toy.v").values() == (2, 10, 20, 30, 38)

    def test_mean_aggregation(self):
        program = compile_world(
            WorldSpec(
                modules=(_mean_pack(),),
                schedule=Schedule(phases=("hold", "main")),
            )
        )
        field = LatticeField((3,), 0)
        for index, value in enumerate((0, 2, 4)):
            field.set((index,), value)
        process = WorldProcess(program, initial_state={"t.f": field})
        process.step()
        assert process.committed("t.m").values() == (1, 2, 3)


class TestLagHistory:
    def test_lag_two_reads_initial_then_history(self):
        program = compile_world(
            WorldSpec(modules=(_lag_pack(),), schedule=Schedule())
        )
        process = WorldProcess(program)
        values = []
        for _ in range(5):
            process.step()
            values.append(process.committed("t.h"))
        assert values == [1, 1, 2, 2, 3]


class TestSnapshot:
    def test_restore_continues_identically(self):
        program = compile_world(
            WorldSpec(
                modules=(toy.SPATIAL,),
                schedule=Schedule(
                    phases=("stage1", "stage2"), periods=(("day", 3),),
                ),
                seed=9,
            )
        )
        process = WorldProcess(program, seed=9)
        for _ in range(5):
            process.step()
        snapshot = process.snapshot()
        restored = WorldProcess.restore(program, snapshot, seed=9)
        process.step(events=("flash",))
        restored.step(events=("flash",))
        for slot in ("toy.u", "toy.v", "toy.daycount", "toy.flash"):
            assert restored.committed(slot) == process.committed(slot)

    def test_restore_rejects_identity_mismatch(self):
        program = compile_world(
            WorldSpec(modules=(toy.W0,), schedule=Schedule(phases=_PHASES))
        )
        process = WorldProcess(program)
        snapshot = process.snapshot()
        snapshot["identity"] = "sha256:deadbeef"
        with pytest.raises(ValueError):
            WorldProcess.restore(program, snapshot)

    def test_restore_rejects_missing_slots(self):
        program = compile_world(
            WorldSpec(modules=(toy.W0,), schedule=Schedule(phases=_PHASES))
        )
        snapshot = WorldProcess(program).snapshot()
        states = dict(snapshot["states"])
        states.pop("toy.x")
        snapshot["states"] = states
        with pytest.raises(ValueError):
            WorldProcess.restore(program, snapshot)

    def test_restore_continues_identically_with_lag_two(self):
        program = compile_world(
            WorldSpec(modules=(_lag_pack(),), schedule=Schedule())
        )
        process = WorldProcess(program)
        for _ in range(4):
            process.step()
        restored = WorldProcess.restore(program, process.snapshot())
        process.step()
        restored.step()
        assert restored.committed("t.h") == process.committed("t.h")

    def test_snapshot_excludes_derived(self):
        program = compile_world(
            WorldSpec(
                modules=(_derived_pack(),),
                schedule=Schedule(phases=("main", "later")),
            )
        )
        process = WorldProcess(program)
        process.step()
        assert process.committed("t.d") == 6
        snapshot = process.snapshot()
        assert "t.d" not in snapshot["states"]
        assert "t.a" in snapshot["states"]
