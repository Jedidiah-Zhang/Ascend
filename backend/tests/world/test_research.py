"""研究层测试 — 观测协议、行动/Res、实验与 oracle。"""

from __future__ import annotations

import pytest

from ascend.world.compile import compile_world
from ascend.world.meta.declarations import AddressUse, Schedule, WorldSpec
from ascend.world.modules import toy
from ascend.world.research import (
    ActionSpec,
    Arm,
    ExperimentSpec,
    Intervention,
    ObservationSpec,
    Oracle,
    interventions_at,
    observe,
    resolve,
)
from ascend.world.runtime import WorldProcess

_PHASES = ("stage1", "stage2", "stage3")


def _program():
    return compile_world(
        WorldSpec(
            modules=(toy.W1,), schedule=Schedule(phases=_PHASES), seed=5,
        )
    )


class TestObservation:
    def test_reads_committed_state(self):
        program = _program()
        process = WorldProcess(program, seed=5)
        process.step()
        spec = ObservationSpec(id="o1", slots=("toy.med",))
        observation = observe(program, process, spec, observer="alice")
        assert observation["values"]["toy.med"] == process.committed("toy.med")
        assert observation["tick"] == 1

    def test_leakage_audit_rejects_unobserved_slot(self):
        program = _program()
        process = WorldProcess(program, seed=5)
        spec = ObservationSpec(id="o1", slots=("toy.out",))
        with pytest.raises(PermissionError):
            observe(program, process, spec)

    def test_leakage_audit_rejects_unknown_slot(self):
        program = _program()
        process = WorldProcess(program, seed=5)
        spec = ObservationSpec(id="o1", slots=("toy.ghost",))
        with pytest.raises(PermissionError):
            observe(program, process, spec)

    def test_quantization(self):
        program = _program()
        process = WorldProcess(program, seed=5)
        process.step(interventions={"toy.med": 7})
        spec = ObservationSpec(id="o1", slots=("toy.med",), quantize_shift=1)
        observation = observe(program, process, spec)
        assert observation["values"]["toy.med"] == 3

    def test_noise_deterministic_per_observer(self):
        program = _program()
        process = WorldProcess(program, seed=5)
        process.step()
        spec = ObservationSpec(
            id="o1",
            slots=("toy.med",),
            noise_address=AddressUse("toy", "obs_noise"),
            noise_span=2,
        )
        first = observe(program, process, spec, observer="alice")
        second = observe(program, process, spec, observer="alice")
        assert first == second


class TestActionResolution:
    def test_priority_wins(self):
        resolved = resolve(
            (
                ActionSpec("a", "toy.med", 1, priority=0),
                ActionSpec("b", "toy.med", 2, priority=5),
            ),
            tick=1,
        )
        assert resolved[0].value == 2

    def test_tie_break_stable_without_seed(self):
        first = resolve(
            (
                ActionSpec("b", "toy.med", 2),
                ActionSpec("a", "toy.med", 1),
            ),
            tick=1,
        )
        second = resolve(
            (
                ActionSpec("a", "toy.med", 1),
                ActionSpec("b", "toy.med", 2),
            ),
            tick=1,
        )
        assert first[0].value == second[0].value == 1

    def test_tie_break_with_seed_is_deterministic(self):
        actions = (
            ActionSpec("a", "toy.med", 1),
            ActionSpec("b", "toy.med", 2),
        )
        first = resolve(actions, tick=1, tie_break_seed=3)
        second = resolve(actions, tick=1, tie_break_seed=3)
        assert first == second

    def test_window_activation(self):
        intervention = Intervention("toy.med", 9, start_tick=2, duration=2)
        assert interventions_at((intervention,), 1) == {}
        assert interventions_at((intervention,), 2) == {"toy.med": 9}
        assert interventions_at((intervention,), 3) == {"toy.med": 9}
        assert interventions_at((intervention,), 4) == {}


class TestOracle:
    def test_rollout_and_crn(self):
        program = _program()
        spec = ObservationSpec(id="o1", slots=("toy.med",))
        oracle = Oracle(program, spec)
        arm = Arm(
            "do-med",
            (Intervention("toy.med", 100, start_tick=1, duration=2),),
        )
        baseline = Arm("base")
        delta = oracle.paired_delta(
            seed=5, arm=arm, baseline=baseline, horizon=3, slot="toy.med",
        )
        left = oracle.rollout(seed=5, arm=arm, horizon=3)
        right = oracle.rollout(seed=5, arm=baseline, horizon=3)
        expected = tuple(
            first["values"]["toy.med"] - second["values"]["toy.med"]
            for first, second in zip(left, right)
        )
        assert delta == expected
        assert delta[0] != 0 and delta[1] != 0 and delta[2] == 0

    def test_rollout_reproducible(self):
        program = _program()
        spec = ObservationSpec(id="o1", slots=("toy.med",))
        oracle = Oracle(program, spec)
        arm = Arm("base")
        first = oracle.rollout(seed=5, arm=arm, horizon=4)
        second = oracle.rollout(seed=5, arm=arm, horizon=4)
        assert first == second

    def test_different_seed_differs(self):
        program = _program()
        spec = ObservationSpec(id="o1", slots=("toy.med",))
        oracle = Oracle(program, spec)
        arm = Arm("base")
        first = oracle.rollout(seed=5, arm=arm, horizon=1)
        second = oracle.rollout(seed=6, arm=arm, horizon=1)
        assert first != second


class TestExperimentSpec:
    def test_validation(self):
        spec = ObservationSpec(id="o1", slots=("toy.med",))
        with pytest.raises(ValueError):
            ExperimentSpec(
                id="e1", arms=(), unit_seeds=(1,), horizon=1,
                observation=spec,
            )
        with pytest.raises(ValueError):
            ExperimentSpec(
                id="e1", arms=(Arm("a"), Arm("a")), unit_seeds=(1,),
                horizon=1, observation=spec,
            )
        experiment = ExperimentSpec(
            id="e1",
            arms=(Arm("a"), Arm("b")),
            unit_seeds=(1, 2),
            horizon=3,
            observation=spec,
        )
        assert experiment.arm("b").id == "b"
        with pytest.raises(KeyError):
            experiment.arm("missing")
