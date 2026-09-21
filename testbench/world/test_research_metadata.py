"""研究元数据测试 — 声明字段规则、误差界否证与快照一致性。

- 元模型：linear/jump 误差界字段规则、ε 与 access 种类校验；
- 编译器：G7 语义（线性边见证差商 ≤ L；跳变边跳幅 ≤ jump_bound）；
- 快照一致性：声明的研究元数据与 ``kheker/equations/equations.json``
  逐项一致。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from olam import (
    MechanismDecl,
    ModulePack,
    Parent,
    Schedule,
    SlotDecl,
    ValueDomain,
    When,
    Witness,
    WorldSpec,
    compile_world,
)
from olam.compile import CompileError
from olam.modules import weather, worldgen
from olam.modules.pipeline import PIPELINE_PHASES
from olam.modules.primitives import GLOBAL

_SNAPSHOT = json.loads(
    (
        Path(__file__).parents[2] / "kheker" / "equations" / "equations.json"
    ).read_text(encoding="utf-8")
)
_INT = ValueDomain(kind="int", bits=32)
_SCHEDULE = Schedule(phases=("one", "two"))


def _times_two(ctx: object) -> int:
    return ctx.parent("x") * 2


def _pack(**kwargs: object) -> ModulePack:
    defaults: dict[str, object] = {
        "instances": (GLOBAL,),
        "slots": (
            SlotDecl(
                id="t.x", on="global", persist="state", domain=_INT,
                writer="t.x.hold",
            ),
            SlotDecl(
                id="t.y", on="global", persist="derived", domain=_INT,
                writer="t.y.update", recompute="y = 2x",
            ),
        ),
        "mechanisms": (
            MechanismDecl(
                id="t.x.hold",
                output="t.x",
                parents=(Parent("t.x", "x", lag=1),),
                impl=lambda ctx: ctx.parent("x"),
                when=When("phase", "one"),
                witnesses=(
                    Witness("a", {"x": 0}, (0,)),
                    Witness("b", {"x": 1}, (1,)),
                ),
            ),
            MechanismDecl(
                id="t.y.update",
                output="t.y",
                parents=(Parent("t.x", "x"),),
                impl=_times_two,
                when=When("phase", "two"),
                witnesses=(
                    Witness("a", {"x": 0}, (0,)),
                    Witness("b", {"x": 1}, (2,)),
                ),
            ),
        ),
    }
    defaults.update(kwargs)
    return ModulePack(id="test.meta", version="1", **defaults)  # type: ignore[arg-type]


class TestModulusFields:
    def test_linear_forbids_jump_bound(self):
        with pytest.raises(ValueError):
            Parent(
                slot="t.x", argument="x",
                modulus_kind="linear", jump_bound=1.0,
            )

    def test_jump_requires_bound(self):
        with pytest.raises(ValueError):
            Parent(slot="t.x", argument="x", modulus_kind="jump")

    def test_jump_forbids_lipschitz(self):
        with pytest.raises(ValueError):
            Parent(
                slot="t.x", argument="x",
                modulus_kind="jump", lipschitz=1.0, jump_bound=1.0,
            )

    def test_slot_metadata_validation(self):
        with pytest.raises(ValueError):
            SlotDecl(
                id="t.a", on="global", persist="state", writer="m.a",
                epsilon=-1.0,
            )
        with pytest.raises(ValueError):
            SlotDecl(
                id="t.a", on="global", persist="state", writer="m.a",
                access_interventions=("teleport",),
            )


class TestModulusCompilerCheck:
    def test_linear_bound_violation_rejected(self):
        pack = _pack(
            mechanisms=(
                _pack().mechanisms[0],
                MechanismDecl(
                    id="t.y.update",
                    output="t.y",
                    parents=(
                        Parent(
                            "t.x", "x", modulus_kind="linear", lipschitz=1.0,
                        ),
                    ),
                    impl=_times_two,
                    when=When("phase", "two"),
                    witnesses=(
                        Witness("a", {"x": 0}, (0,)),
                        Witness("b", {"x": 1}, (2,)),
                    ),
                ),
            ),
        )
        with pytest.raises(CompileError) as excinfo:
            compile_world(WorldSpec(modules=(pack,), schedule=_SCHEDULE))
        assert "L=1.0 < 见证差商" in str(excinfo.value)

    def test_jump_bound_violation_rejected(self):
        pack = _pack(
            mechanisms=(
                _pack().mechanisms[0],
                MechanismDecl(
                    id="t.y.update",
                    output="t.y",
                    parents=(
                        Parent(
                            "t.x", "x", modulus_kind="jump", jump_bound=1.0,
                        ),
                    ),
                    impl=_times_two,
                    when=When("phase", "two"),
                    witnesses=(
                        Witness("a", {"x": 0}, (0,)),
                        Witness("b", {"x": 1}, (2,)),
                    ),
                ),
            ),
        )
        with pytest.raises(CompileError) as excinfo:
            compile_world(WorldSpec(modules=(pack,), schedule=_SCHEDULE))
        assert "jump_bound=1.0 < 见证跳幅" in str(excinfo.value)

    def test_unauthenticated_linear_allowed(self):
        program = compile_world(
            WorldSpec(modules=(_pack(),), schedule=_SCHEDULE)
        )
        assert program.mechanisms["t.y.update"].parents[0].lipschitz is None


@pytest.fixture(scope="module")
def program():
    return compile_world(
        WorldSpec(
            modules=(worldgen.MODULE, weather.MODULE),
            schedule=Schedule(phases=PIPELINE_PHASES),
        )
    )


class TestMetadataSnapshotConsistency:

    def test_parent_metadata_matches_snapshot(self, program):
        for mid, mech in _SNAPSHOT["mechanisms"].items():
            declaration = program.mechanisms[mid]
            assert tuple(declaration.boundary_cases) == tuple(
                mech["boundary_cases"]
            ), mid
            for parent in mech["parents"]:
                found = next(
                    item for item in declaration.parents
                    if item.argument == parent["argument"]
                )
                assert found.modulus_kind == parent["modulus_kind"], mid
                assert found.lipschitz == parent["lipschitz"], mid
                assert found.jump_bound == parent["jump_bound"], mid
                assert found.analysis_role == parent["analysis_role"], mid
                assert found.valid_domain == parent["valid_domain"], mid
                assert found.metric == parent["metric"], mid

    def test_node_metadata_matches_snapshot(self, program):
        for nid, node in _SNAPSHOT["nodes"].items():
            slot = program.slots[nid]
            assert slot.role == node["role"], nid
            assert slot.schedule == node["update"]["schedule"], nid
            assert slot.quantization == node["value"]["quantization"], nid
            assert slot.metric == node["math"]["metric"], nid
            assert slot.epsilon == node["math"]["error_budget"], nid
            assert tuple(slot.access_interventions) == tuple(
                node["access"]["interventions"]
            ), nid
            assert slot.research_trace == node["access"]["research_trace"], nid
            assert tuple(slot.observation_protocols) == tuple(
                node["access"]["observation_protocols"]
            ), nid

    def test_edge_counts(self, program):
        linear = jump = 0
        for mechanism in program.mechanisms.values():
            for parent in mechanism.parents:
                if parent.modulus_kind == "jump":
                    jump += 1
                else:
                    linear += 1
        assert (linear, jump) == (55, 9)
