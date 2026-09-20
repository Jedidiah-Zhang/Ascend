"""研究层协议测试 — 观测视野、脚本主体、实验管线与控制世界。

- 观测协议：视野/量化/噪声/缺失策略/泄漏审计；
- 脚本主体：帧边界观测 → Γ → 干预；
- 实验管线：oracle 单位级运行 + 基线评分 + CRN 配对效应；
- 控制世界：I0 / I1 / 伪相关 / 无效干预 / 机制替换的设计性质。
"""

from __future__ import annotations

import pytest

from ascend.world import (
    InstanceDecl,
    MechanismDecl,
    ModulePack,
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
from ascend.world.modules.primitives import GLOBAL
from ascend.world.research import (
    ActionSpec,
    Arm,
    ExperimentSpec,
    Intervention,
    ObservationSpec,
    Oracle,
    ScriptedSubject,
    ScriptedSubjectSpec,
    i0_control,
    i1_control,
    interventions_at,
    invalid_intervention_control,
    mechanism_replacement_control,
    observe,
    paired_effects,
    pseudo_correlation_control,
    run_experiment,
)
from ascend.world.runtime.state import DynamicField

_INT = ValueDomain(kind="int", bits=32)
_WRITE = Permissions(intervene=True, observe=True, record=True)


def _identity_impl(ctx: object) -> int:
    return ctx.parent("x")


def _chunk_pack() -> ModulePack:
    chunk = InstanceDecl(
        id="lattice.chunk", kind="lattice", identity="xy", size=None,
        axes=("cx", "cy"),
    )
    hold = MechanismDecl(
        id="obs.a.hold",
        output="obs.a",
        parents=(Parent("obs.a", "x", lag=1),),
        impl=_identity_impl,
        when=When("phase", "one"),
        witnesses=(
            Witness("x0", {"x": 0}, (0,)),
            Witness("x1", {"x": 1}, (1,)),
        ),
    )
    return ModulePack(
        id="obs.world",
        version="1",
        instances=(GLOBAL, chunk),
        slots=(
            SlotDecl(
                id="obs.a", on="lattice.chunk", persist="state",
                domain=_INT, permissions=_WRITE, writer=hold.id, initial=7,
            ),
        ),
        mechanisms=(hold,),
    )


@pytest.fixture()
def chunk_program():
    return compile_world(
        WorldSpec(
            modules=(_chunk_pack(),), schedule=Schedule(phases=("one",)),
        )
    )


class TestObservationProtocol:
    def test_viewport_reads_offsets(self, chunk_program):
        process = WorldProcess(chunk_program)
        process.materialize("lattice.chunk", (0, 0))
        process.materialize("lattice.chunk", (1, 0))
        process.step()
        spec = ObservationSpec(
            id="o1",
            slots=("obs.a",),
            viewport=((0, 0), (1, 0)),
        )
        observation = observe(
            chunk_program, process, spec, position=(0, 0),
        )
        values = observation["values"]
        assert values[("obs.a", (0, 0))] == 7
        assert values[("obs.a", (1, 0))] == 7

    def test_missing_policies(self, chunk_program):
        process = WorldProcess(chunk_program)
        process.materialize("lattice.chunk", (0, 0))
        process.step()
        raise_spec = ObservationSpec(
            id="o1", slots=("obs.a",), viewport=((5, 5),),
        )
        with pytest.raises(KeyError):
            observe(chunk_program, process, raise_spec, position=(0, 0))
        omit_spec = ObservationSpec(
            id="o1", slots=("obs.a",), viewport=((5, 5),),
            missing_policy="omit",
        )
        assert observe(
            chunk_program, process, omit_spec, position=(0, 0),
        )["values"] == {}
        default_spec = ObservationSpec(
            id="o1", slots=("obs.a",), viewport=((5, 5),),
            missing_policy="default", missing=-1,
        )
        assert observe(
            chunk_program, process, default_spec, position=(0, 0),
        )["values"][("obs.a", (5, 5))] == -1

    def test_quantize_and_noise(self, chunk_program):
        from ascend.world.meta.declarations import AddressUse

        process = WorldProcess(chunk_program)
        process.materialize("lattice.chunk", (0, 0))
        process.step()
        spec = ObservationSpec(
            id="o1",
            slots=("obs.a",),
            quantize_shift=1,
            noise_address=AddressUse("obs", "noise"),
            noise_span=1,
        )
        first = observe(chunk_program, process, spec, position=(0, 0))
        second = observe(chunk_program, process, spec, position=(0, 0))
        assert first == second  # 地址随机：确定性

    def test_leakage_audit(self, chunk_program):
        process = WorldProcess(chunk_program)
        process.materialize("lattice.chunk", (0, 0))
        process.step()
        spec = ObservationSpec(id="o1", slots=("obs.nope",))
        with pytest.raises(PermissionError):
            observe(chunk_program, process, spec)


class TestScriptedSubject:
    def test_observe_act_cycle(self):
        template = i1_control()
        program = template.programs[0]
        subject = ScriptedSubject(
            ScriptedSubjectSpec(
                id="scripted-1",
                position=(0, 0),
                observation=template.observation,
                actions=(
                    ActionSpec("set-g-1", "i1.g", 1, duration=1),
                    ActionSpec("set-g-0", "i1.g", 0, duration=1),
                ),
            )
        )
        process = WorldProcess(program)
        process.step()
        observation = subject.observe(program, process)
        assert "i1.o" in observation["values"]
        first = subject.act(tick=2)
        second = subject.act(tick=3)
        assert first.target_slot == "i1.g" and first.value == 1
        assert second.value == 0
        assert interventions_at((first,), 2) == {"i1.g": 1}
        assert [entry["kind"] for entry in subject.history] == [
            "observation", "action", "action",
        ]


class TestExperimentPipeline:
    def test_paired_effect_is_exact(self):
        template = i1_control()
        program = template.programs[0]
        experiment = ExperimentSpec(
            id="i1.exp",
            arms=template.arms,
            unit_seeds=(1, 2, 3),
            horizon=4,
            observation=template.observation,
        )
        results = run_experiment(program, experiment)
        by_arm = {(r.seed, r.arm): r for r in results}
        for seed in experiment.unit_seeds:
            assert by_arm[(seed, "g1")].paired["i1.o"] == 10.0
        baseline = by_arm[(1, "g0")]
        assert baseline.scores  # 基线分数已产出

    def test_paired_effects_helper(self):
        frames_arm = (
            {"values": {"x": 3}},
            {"values": {"x": 5}},
        )
        frames_base = (
            {"values": {"x": 1}},
            {"values": {"x": 2}},
        )
        assert paired_effects(frames_arm, frames_base) == {"x": 2.5}


class TestControlWorlds:
    def test_i0_observational_equivalence_and_separation(self):
        template = i0_control()
        u1, u2 = template.programs
        seeds = tuple(range(16))
        baseline = template.arms[0]
        intervened = template.arms[1]
        oracle_u1 = Oracle(u1, template.observation)
        oracle_u2 = Oracle(u2, template.observation)
        u1_effects = []
        u2_effects = []
        for seed in seeds:
            for program, oracle, effects in (
                (u1, oracle_u1, u1_effects),
                (u2, oracle_u2, u2_effects),
            ):
                frames = oracle.rollout(seed=seed, arm=baseline, horizon=2)
                values = frames[0]["values"]
                assert values["i0.a"] == values["i0.b"]  # 观测等价
                arm_frames = oracle.rollout(
                    seed=seed, arm=intervened, horizon=2,
                )
                effects.append(arm_frames[0]["values"]["i0.b"])
        assert u1_effects == [0] * len(seeds)  # U1：B = A = 0
        assert any(value != 0 for value in u2_effects)  # U2：B 仍为 E

    def test_pseudo_correlation_does_not_change_effect(self):
        template = pseudo_correlation_control()
        program = template.programs[0]
        oracle = Oracle(program, template.observation)
        baseline, intervened = template.arms
        for seed in range(8):
            base = oracle.rollout(seed=seed, arm=baseline, horizon=2)
            arm = oracle.rollout(seed=seed, arm=intervened, horizon=2)
            assert arm[0]["values"]["pc.a"] == 99
            assert arm[0]["values"]["pc.b"] == base[0]["values"]["pc.b"]

    def test_invalid_intervention_zero_effect(self):
        template = invalid_intervention_control()
        program = template.programs[0]
        oracle = Oracle(program, template.observation)
        baseline, intervened = template.arms
        for seed in range(8):
            base = oracle.rollout(seed=seed, arm=baseline, horizon=2)
            arm = oracle.rollout(seed=seed, arm=intervened, horizon=2)
            assert arm[0]["values"]["iv.z"] == 99
            assert arm[0]["values"]["iv.y"] == base[0]["values"]["iv.y"]

    def test_mechanism_replacement_distinguishable(self):
        template = mechanism_replacement_control()
        v1, v2 = template.programs
        oracle_v1 = Oracle(v1, template.observation)
        oracle_v2 = Oracle(v2, template.observation)
        arm = template.arms[0]
        first = oracle_v1.rollout(seed=0, arm=arm, horizon=1)
        second = oracle_v2.rollout(seed=0, arm=arm, horizon=1)
        assert first[0]["values"]["rep.y"] == 5
        assert second[0]["values"]["rep.y"] == 6
