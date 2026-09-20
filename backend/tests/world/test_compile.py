"""编译器测试 — 静态校验、身份与更新点计划。"""

from __future__ import annotations

from dataclasses import replace

import pytest

from ascend.world.compile import CompileError, compile_world
from ascend.world.meta.declarations import (
    AddressUse,
    InstanceDecl,
    InvariantDecl,
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
    WorldSpec,
)
from ascend.world.modules import toy
from ascend.world.modules.primitives import GLOBAL

_INT = ValueDomain(kind="int", bits=32)


def _slot(slot_id: str, writer: str, *, on: str = "global") -> SlotDecl:
    return SlotDecl(
        id=slot_id, on=on, persist="state", domain=_INT, writer=writer,
    )


def _const_impl(ctx: object) -> int:
    return 1


def _plus_one_impl(ctx: object) -> int:
    return ctx.parent("a") + 1


def _mech(
    mechanism_id: str,
    output: str,
    *,
    impl: object = _const_impl,
    parents: tuple[Parent, ...] = (),
    witnesses: tuple[Witness, ...] | None = None,
    when: When = When("phase", "main"),
    **kwargs: object,
) -> MechanismDecl:
    if witnesses is None:
        witnesses = (Witness("base", {}, (1,)),)
    return MechanismDecl(
        id=mechanism_id,
        output=output,
        parents=parents,
        impl=impl,  # type: ignore[arg-type]
        when=when,
        witnesses=witnesses,
        **kwargs,  # type: ignore[arg-type]
    )


def _pack(module_id: str = "test.mod", **kwargs: object) -> ModulePack:
    defaults: dict[str, object] = {
        "instances": (GLOBAL,),
        "slots": (),
        "mechanisms": (),
        "relations": (),
        "parameters": (),
        "invariants": (),
        "depends_on": (),
    }
    defaults.update(kwargs)
    return ModulePack(id=module_id, version="1", **defaults)  # type: ignore[arg-type]


def _spec(pack: ModulePack, **kwargs: object) -> WorldSpec:
    defaults: dict[str, object] = {
        "modules": (pack,),
        "schedule": Schedule(phases=("main",)),
    }
    defaults.update(kwargs)
    return WorldSpec(**defaults)  # type: ignore[arg-type]


def _compile_error(spec: WorldSpec) -> str:
    with pytest.raises(CompileError) as excinfo:
        compile_world(spec)
    return str(excinfo.value)


class TestToyCompilation:
    def test_plan_and_identity_stable(self):
        spec = WorldSpec(
            modules=(toy.W0,),
            schedule=Schedule(phases=("stage1", "stage2", "stage3")),
        )
        program = compile_world(spec)
        assert [group.id for group in program.update_plan] == [
            "phase:stage1", "phase:stage2", "phase:stage3",
        ]
        assert program.identity == compile_world(spec).identity
        assert program.world_identity(1) != program.world_identity(2)

    def test_identity_sensitive_to_parameter(self):
        base = WorldSpec(
            modules=(toy.W0,),
            schedule=Schedule(phases=("stage1", "stage2", "stage3")),
        )
        changed = replace(base, parameters={"toy.u_mid": 3})
        assert compile_world(base).identity != compile_world(changed).identity

    def test_identity_sensitive_to_schedule(self):
        base = WorldSpec(
            modules=(toy.W0,),
            schedule=Schedule(phases=("stage1", "stage2", "stage3")),
        )
        changed = replace(
            base,
            schedule=Schedule(
                phases=("stage1", "stage2", "stage3"),
                periods=(("day", 24),),
            ),
        )
        assert compile_world(base).identity != compile_world(changed).identity

    def test_merged_plan_orders_modes(self):
        program = compile_world(
            WorldSpec(
                modules=(toy.W0, toy.W1, toy.SPATIAL),
                schedule=Schedule(
                    phases=("stage1", "stage2", "stage3"),
                    periods=(("day", 24),),
                ),
            )
        )
        ids = [group.id for group in program.update_plan]
        assert ids[-2:] == ["period:day", "event:flash"]


class TestMergeAndDependencies:
    def test_identical_declarations_are_idempotent(self):
        first = _pack(
            "test.a",
            slots=(_slot("t.a", "m.a"),),
            mechanisms=(_mech("m.a", "t.a"),),
        )
        second = _pack(
            "test.b",
            slots=(_slot("t.a", "m.a"),),
            mechanisms=(_mech("m.a", "t.a"),),
        )
        program = compile_world(
            WorldSpec(modules=(first, second), schedule=Schedule())
        )
        assert list(program.mechanisms) == ["m.a"]

    def test_conflicting_declarations_rejected(self):
        first = _pack(
            "test.a",
            slots=(_slot("t.a", "m.a"),),
            mechanisms=(_mech("m.a", "t.a", equation="a = 1"),),
        )
        second = _pack(
            "test.b",
            slots=(_slot("t.a", "m.a"),),
            mechanisms=(_mech("m.a", "t.a", equation="a = 2"),),
        )
        message = _compile_error(
            WorldSpec(modules=(first, second), schedule=Schedule())
        )
        assert "冲突" in message

    def test_missing_dependency(self):
        pack = _pack(
            "test.a",
            slots=(_slot("t.a", "m.a"),),
            mechanisms=(_mech("m.a", "t.a"),),
            depends_on=("test.missing",),
        )
        assert "依赖缺失" in _compile_error(_spec(pack))


class TestParameterResolution:
    def test_undeclared_parameter_used(self):
        pack = _pack(
            "test.a",
            slots=(_slot("t.a", "m.a"),),
            mechanisms=(
                _mech("m.a", "t.a", params=("t.p",)),
            ),
        )
        assert "参数未声明" in _compile_error(_spec(pack))

    def test_parameter_out_of_range(self):
        pack = _pack(
            "test.a",
            slots=(_slot("t.a", "m.a"),),
            mechanisms=(_mech("m.a", "t.a"),),
            parameters=(
                ParameterDecl("t.p", default=5, minimum=0, maximum=10),
            ),
        )
        assert "高于上界" in _compile_error(
            _spec(pack, parameters={"t.p": 11})
        )

    def test_unknown_parameter_key(self):
        pack = _pack(
            "test.a",
            slots=(_slot("t.a", "m.a"),),
            mechanisms=(_mech("m.a", "t.a"),),
        )
        assert "未在任何模块声明" in _compile_error(
            _spec(pack, parameters={"t.ghost": 1})
        )

    def test_knob_invalid_value(self):
        from ascend.world.meta.declarations import KnobDecl

        pack = _pack(
            "test.a",
            slots=(_slot("t.a", "m.a"),),
            mechanisms=(_mech("m.a", "t.a"),),
            knobs=(KnobDecl("t.k", values=(1, 2), default=1),),
        )
        assert "不在候选值" in _compile_error(_spec(pack, knobs={"t.k": 3}))

    def test_parameter_slot_requires_declaration(self):
        pack = _pack(
            "test.a",
            slots=(
                SlotDecl(
                    id="t.p", on="global", persist="parameter",
                    domain=_INT,
                ),
            ),
        )
        assert "缺少同名参数声明" in _compile_error(_spec(pack))


class TestStructureChecks:
    def test_single_writer(self):
        pack = _pack(
            "test.a",
            slots=(_slot("t.a", "m.a"), _slot("t.b", "m.b")),
            mechanisms=(
                _mech("m.a", "t.a"),
                _mech("m.b", "t.a"),
            ),
        )
        message = _compile_error(_spec(pack))
        assert "双写者" in message or "声明写者" in message

    def test_same_frame_order(self):
        pack = _pack(
            "test.a",
            slots=(_slot("t.a", "m.a"), _slot("t.b", "m.b")),
            mechanisms=(
                _mech("m.a", "t.a"),
                _mech(
                    "m.b",
                    "t.b",
                    impl=_plus_one_impl,
                    parents=(Parent("t.a", "a"),),
                    witnesses=(
                        Witness("base", {"a": 0}, (1,)),
                        Witness("a_changed", {"a": 1}, (2,)),
                    ),
                ),
            ),
        )
        assert "必须更早执行" in _compile_error(_spec(pack))

    def test_self_parent_requires_lag(self):
        pack = _pack(
            "test.a",
            slots=(_slot("t.a", "m.a"),),
            mechanisms=(
                _mech(
                    "m.a",
                    "t.a",
                    impl=_plus_one_impl,
                    parents=(Parent("t.a", "a"),),
                    witnesses=(
                        Witness("base", {"a": 0}, (1,)),
                        Witness("a_changed", {"a": 1}, (2,)),
                    ),
                ),
            ),
        )
        assert "自引用必须 lag≥1" in _compile_error(_spec(pack))

    def test_unknown_phase_and_period(self):
        pack = _pack(
            "test.a",
            slots=(_slot("t.a", "m.a"),),
            mechanisms=(_mech("m.a", "t.a", when=When("phase", "nope")),),
        )
        assert "未声明的阶段" in _compile_error(_spec(pack))
        pack = _pack(
            "test.a",
            slots=(_slot("t.a", "m.a"),),
            mechanisms=(_mech("m.a", "t.a", when=When("period", "nope")),),
        )
        assert "未声明的周期" in _compile_error(_spec(pack))

    def test_duplicate_address(self):
        pack = _pack(
            "test.a",
            slots=(_slot("t.a", "m.a"), _slot("t.b", "m.b")),
            mechanisms=(
                _mech(
                    "m.a", "t.a",
                    address=AddressUse("toy", "x"),
                    witnesses=(Witness("base", {}, (1,), seed=0),),
                ),
                _mech(
                    "m.b", "t.b",
                    address=AddressUse("toy", "x"),
                    witnesses=(Witness("base", {}, (1,), seed=0),),
                ),
            ),
        )
        assert "地址重复声明" in _compile_error(_spec(pack))

    def test_cross_instance_parent_rejected(self):
        line = InstanceDecl(
            id="lattice.line", kind="lattice", identity="xy", size=(3,),
        )
        pack = _pack(
            "test.a",
            instances=(GLOBAL, line),
            slots=(
                SlotDecl(
                    id="t.f", on="lattice.line", persist="state",
                    domain=_INT, writer="m.f",
                ),
                _slot("t.g", "m.g"),
            ),
            mechanisms=(
                _mech("m.f", "t.f"),
                _mech(
                    "m.g", "t.g",
                    impl=_plus_one_impl,
                    parents=(Parent("t.f", "a"),),
                    witnesses=(
                        Witness("base", {"a": 0}, (1,)),
                        Witness("a_changed", {"a": 1}, (2,)),
                    ),
                ),
            ),
        )
        assert "跨实例类型父引用" in _compile_error(_spec(pack))

    def test_invariant_requires_state_slot(self):
        pack = _pack(
            "test.a",
            slots=(
                SlotDecl(
                    id="t.e", on="global", persist="external",
                    domain=_INT, permissions=Permissions(observe=True),
                ),
                _slot("t.a", "m.a"),
            ),
            mechanisms=(_mech("m.a", "t.a"),),
            invariants=(
                InvariantDecl(
                    id="t.inv", slots=("t.e",),
                    check=lambda view: True,
                ),
            ),
        )
        assert "只能作用于 state/derived" in _compile_error(_spec(pack))


class TestWitnessEvidence:
    def test_witness_required(self):
        pack = _pack(
            "test.a",
            slots=(_slot("t.a", "m.a"), _slot("t.b", "m.b")),
            mechanisms=(
                _mech("m.a", "t.a"),
                MechanismDecl(
                    id="m.b",
                    output="t.b",
                    parents=(Parent("t.a", "a"),),
                    impl=_plus_one_impl,
                    when=When("phase", "main"),
                    witnesses=(),
                ),
            ),
        )
        assert "缺少见证" in _compile_error(_spec(pack))

    def test_witness_coverage(self):
        pack = _pack(
            "test.a",
            slots=(_slot("t.a", "m.a"), _slot("t.b", "m.b")),
            mechanisms=(
                _mech("m.a", "t.a"),
                _mech(
                    "m.b", "t.b",
                    impl=_plus_one_impl,
                    parents=(Parent("t.a", "a"),),
                    witnesses=(
                        Witness("base", {"a": 0}, (1,)),
                        Witness("dup", {"a": 0}, (1,)),
                    ),
                ),
            ),
        )
        assert "缺少只变该值的见证" in _compile_error(_spec(pack))

    def test_witness_output_mismatch(self):
        pack = _pack(
            "test.a",
            slots=(_slot("t.a", "m.a"),),
            mechanisms=(
                _mech(
                    "m.a", "t.a",
                    witnesses=(Witness("bad", {}, (999,)),),
                ),
            ),
        )
        assert "!= 预期" in _compile_error(_spec(pack))
