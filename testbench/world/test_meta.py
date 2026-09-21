"""元模型测试 — 六种声明的字段级不变量与模块校验/摘要。"""

from __future__ import annotations

import pytest

from olam.meta.declarations import (
    Arithmetic,
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
)
from olam.meta.validate import (
    mechanism_digest,
    module_digest,
    source_digest,
    validate_module,
)
from olam.modules.primitives import GLOBAL


def _impl(ctx: object) -> int:
    return 1


def _impl_other(ctx: object) -> int:
    return 2


def _mech(mechanism_id: str, output: str) -> MechanismDecl:
    return MechanismDecl(
        id=mechanism_id,
        output=output,
        parents=(),
        impl=_impl,
        witnesses=(Witness("base", {}, (1,)),),
    )


class TestValueDomain:
    def test_int_requires_bits(self):
        with pytest.raises(ValueError):
            ValueDomain(kind="int")

    def test_enum_requires_choices(self):
        with pytest.raises(ValueError):
            ValueDomain(kind="enum")

    def test_inverted_range(self):
        with pytest.raises(ValueError):
            ValueDomain(kind="float", minimum=1.0, maximum=0.0)


class TestSlotRules:
    def test_state_requires_writer(self):
        with pytest.raises(ValueError):
            SlotDecl(id="t.a", on="global", persist="state")

    def test_derived_requires_writer_and_recompute(self):
        with pytest.raises(ValueError):
            SlotDecl(id="t.a", on="global", persist="derived")

    def test_parameter_cannot_have_writer(self):
        with pytest.raises(ValueError):
            SlotDecl(
                id="t.a", on="global", persist="parameter", writer="m.a",
            )


class TestScheduleAndTime:
    def test_duplicate_phase(self):
        with pytest.raises(ValueError):
            Schedule(phases=("main", "main"))

    def test_period_positive(self):
        with pytest.raises(ValueError):
            Schedule(periods=(("day", 0),))

    def test_unknown_mode(self):
        with pytest.raises(ValueError):
            When(mode="whenever", key="x")


class TestMechanismRules:
    def test_non_callable_impl(self):
        with pytest.raises(ValueError):
            MechanismDecl(
                id="m.a",
                output="t.a",
                parents=(),
                impl="nope",  # type: ignore[arg-type]
            )

    def test_negative_lag(self):
        with pytest.raises(ValueError):
            Parent(slot="t.a", argument="a", lag=-1)

    def test_unknown_aggregation(self):
        with pytest.raises(ValueError):
            Parent(slot="t.a", argument="a", aggregation="median")

    def test_arithmetic_domain(self):
        with pytest.raises(ValueError):
            Arithmetic(domain="quantum")
        with pytest.raises(ValueError):
            Arithmetic(domain="fixed", bits=0)


class TestModuleValidation:
    def test_duplicate_ids(self):
        pack = ModulePack(
            id="test.dup",
            instances=(GLOBAL,),
            slots=(
                SlotDecl(id="t.a", on="global", persist="state", writer="m.a"),
                SlotDecl(id="t.a", on="global", persist="state", writer="m.a"),
            ),
            mechanisms=(_mech("m.a", "t.a"),),
        )
        issues = validate_module(pack)
        assert any("槽位 id 重复" in issue for issue in issues)

    def test_output_slot_must_be_declared(self):
        pack = ModulePack(
            id="test.out",
            instances=(GLOBAL,),
            mechanisms=(_mech("m.a", "t.missing"),),
        )
        issues = validate_module(pack)
        assert any("输出槽位未声明" in issue for issue in issues)

    def test_writer_must_be_declared(self):
        pack = ModulePack(
            id="test.writer",
            instances=(GLOBAL,),
            slots=(
                SlotDecl(id="t.a", on="global", persist="state", writer="m.nope"),
            ),
        )
        issues = validate_module(pack)
        assert any("writer 未声明" in issue for issue in issues)


class TestDigests:
    def test_module_digest_stable_and_sensitive(self):
        pack = ModulePack(
            id="test.digest",
            instances=(GLOBAL,),
            slots=(
                SlotDecl(id="t.a", on="global", persist="state", writer="m.a"),
            ),
            mechanisms=(_mech("m.a", "t.a"),),
        )
        same = ModulePack(
            id="test.digest",
            instances=(GLOBAL,),
            slots=(
                SlotDecl(id="t.a", on="global", persist="state", writer="m.a"),
            ),
            mechanisms=(_mech("m.a", "t.a"),),
        )
        assert module_digest(pack) == module_digest(same)
        assert mechanism_digest(_mech("m.a", "t.a")) != mechanism_digest(
            MechanismDecl(
                id="m.a",
                output="t.a",
                parents=(),
                impl=_impl_other,
                witnesses=(Witness("base", {}, (2,)),),
            )
        )
        assert source_digest(_impl) != source_digest(_impl_other)


class TestInstanceRules:
    def test_lattice_size_positive(self):
        with pytest.raises(ValueError):
            InstanceDecl(id="lattice.x", kind="lattice", size=(0,))

    def test_non_lattice_size_rejected(self):
        with pytest.raises(ValueError):
            InstanceDecl(id="global.x", kind="global", size=(2,))

    def test_level_ratio_positive(self):
        with pytest.raises(ValueError):
            InstanceDecl(id="lattice.x", kind="lattice", parent="lattice.y", ratio=0)


class TestPermissions:
    def test_defaults_deny(self):
        permissions = Permissions()
        assert not permissions.intervene
        assert not permissions.observe
        assert not permissions.record
