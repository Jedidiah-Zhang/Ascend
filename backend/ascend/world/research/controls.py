"""控制世界模板 — 评价判别力的最小构造（P3）。

- **I0**：观测等价、干预可分（综述 §2.4.2 / 世界验收 I0）；
- **I1**：分布预测与 CRN 配对效应（世界验收 I1）；
- **伪相关**：共同原因 R→A、R→B，无 A→B；do(A) 不改 B；
- **无效干预**：对非祖先分量干预，CRN 下目标效应为零；
- **机制替换**：同当前状态、不同后续机制（世界变体）可区分。

每个模板返回 :class:`ControlTemplate`（程序 + 观测协议 + 臂 + 设计性质）；
断言在 ``tests/world/test_research_protocols.py``，模板本身可复用。
"""

from __future__ import annotations

from dataclasses import dataclass

from ascend.world.compile import compile_world
from ascend.world.kernel import Address, address_seed
from ascend.world.meta.declarations import (
    AddressUse,
    MechanismDecl,
    ModulePack,
    Parent,
    Permissions,
    Schedule,
    SlotDecl,
    ValueDomain,
    When,
    Witness,
    WorldSpec,
)
from ascend.world.modules.primitives import GLOBAL
from ascend.world.research.action import Intervention
from ascend.world.research.experiment import Arm
from ascend.world.research.observation import ObservationSpec

__all__ = [
    "ControlTemplate",
    "i0_control",
    "i1_control",
    "invalid_intervention_control",
    "mechanism_replacement_control",
    "pseudo_correlation_control",
]

_INT = ValueDomain(kind="int", bits=32)
_WRITE = Permissions(intervene=True, observe=True, record=True)


def _draw_impl(ctx: object) -> int:
    return ctx.draw_range(0, 1)


def _identity_impl(ctx: object) -> int:
    return ctx.parent("x")


def _plus_one_impl(ctx: object) -> int:
    return ctx.parent("x") + 1


def _uniform_impl(ctx: object) -> int:
    return ctx.draw_range(-10, 10)


def _compose_impl(ctx: object) -> int:
    return ctx.parent("g") * 10 + ctx.parent("e")


def _draw_witnesses(namespace: str, purpose: str) -> tuple[Witness, ...]:
    values = tuple(
        address_seed(seed, Address(namespace, purpose)) % 2
        for seed in (0, 1)
    )
    return (
        Witness("seed0", {}, (values[0],), seed=0),
        Witness("seed1", {}, (values[1],), seed=1),
    )


def _uniform_witnesses(namespace: str, purpose: str) -> tuple[Witness, ...]:
    values = tuple(
        address_seed(seed, Address(namespace, purpose)) % 21 - 10
        for seed in (0, 1)
    )
    return (
        Witness("seed0", {}, (values[0],), seed=0),
        Witness("seed1", {}, (values[1],), seed=1),
    )


def _identity_witnesses() -> tuple[Witness, ...]:
    return (
        Witness("x0", {"x": 0}, (0,)),
        Witness("x1", {"x": 1}, (1,)),
    )


def _plus_witnesses() -> tuple[Witness, ...]:
    return (
        Witness("x0", {"x": 0}, (1,)),
        Witness("x1", {"x": 1}, (2,)),
    )


def _slot(slot_id: str, writer: str, *, initial: int = 0) -> SlotDecl:
    return SlotDecl(
        id=slot_id,
        on="global",
        persist="state",
        domain=_INT,
        permissions=_WRITE,
        writer=writer,
        initial=initial,
    )


@dataclass(frozen=True, slots=True)
class ControlTemplate:
    """控制世界模板：程序（可多世界）+ 观测协议 + 臂 + 设计性质。"""

    id: str
    programs: tuple[object, ...]
    observation: ObservationSpec
    arms: tuple[Arm, ...]
    property: str


def _compile(pack: ModulePack, phases: tuple[str, ...]) -> object:
    return compile_world(
        WorldSpec(
            modules=(pack,), schedule=Schedule(phases=phases),
        )
    )


# ── I0：观测等价、干预可分 ────────────────────────────────────────


def _i0_program(module_id: str, forward: bool) -> object:
    source = MechanismDecl(
        id=f"{module_id}.e",
        output="i0.e",
        parents=(),
        impl=_draw_impl,
        when=When("phase", "one"),
        address=AddressUse("i0", "e"),
        witnesses=_draw_witnesses("i0", "e"),
    )
    first_output = "i0.a" if forward else "i0.b"
    second_output = "i0.b" if forward else "i0.a"
    first = MechanismDecl(
        id=f"{module_id}.first",
        output=first_output,
        parents=(Parent("i0.e", "x"),),
        impl=_identity_impl,
        when=When("phase", "two"),
        witnesses=_identity_witnesses(),
    )
    second = MechanismDecl(
        id=f"{module_id}.second",
        output=second_output,
        parents=(Parent(first_output, "x"),),
        impl=_identity_impl,
        when=When("phase", "three"),
        witnesses=_identity_witnesses(),
    )
    pack = ModulePack(
        id=module_id,
        version="1",
        instances=(GLOBAL,),
        slots=(
            _slot("i0.e", source.id),
            _slot("i0.a", first.id if forward else second.id),
            _slot("i0.b", second.id if forward else first.id),
        ),
        mechanisms=(source, first, second),
    )
    return _compile(pack, ("one", "two", "three"))


def i0_control() -> ControlTemplate:
    """观测等价、干预可分：A=B 观测相同，do(A=0) 的 B 后果不同。"""
    return ControlTemplate(
        id="I0",
        programs=(
            _i0_program("i0.u1", True),
            _i0_program("i0.u2", False),
        ),
        observation=ObservationSpec(id="i0.obs", slots=("i0.a", "i0.b")),
        arms=(
            Arm("baseline"),
            Arm(
                "do-a-0",
                (Intervention("i0.a", 0, start_tick=1, duration=1),),
            ),
        ),
        property="观测恒有 A=B；do(A=0) 后 B 在 U1 恒 0、U2 仍为 E",
    )


# ── I1：分布预测与配对效应 ────────────────────────────────────────


def i1_control() -> ControlTemplate:
    """O = g*10 + E（E ~ U{-10..10}）：CRN 配对效应恒为 10。"""
    source = MechanismDecl(
        id="i1.e.update",
        output="i1.e",
        parents=(),
        impl=_uniform_impl,
        when=When("phase", "one"),
        address=AddressUse("i1", "e"),
        witnesses=_uniform_witnesses("i1", "e"),
    )
    compose = MechanismDecl(
        id="i1.o.compose",
        output="i1.o",
        parents=(Parent("i1.g", "g"), Parent("i1.e", "e")),
        impl=_compose_impl,
        when=When("phase", "two"),
        witnesses=(
            Witness("g0e0", {"g": 0, "e": 0}, (0,)),
            Witness("g1e0", {"g": 1, "e": 0}, (10,)),
            Witness("g0e5", {"g": 0, "e": 5}, (5,)),
        ),
    )
    hold = MechanismDecl(
        id="i1.g.hold",
        output="i1.g",
        parents=(Parent("i1.g", "x", lag=1),),
        impl=_identity_impl,
        when=When("phase", "one"),
        witnesses=_identity_witnesses(),
    )
    pack = ModulePack(
        id="i1.world",
        version="1",
        instances=(GLOBAL,),
        slots=(
            _slot("i1.g", hold.id),
            _slot("i1.e", source.id),
            _slot("i1.o", compose.id),
        ),
        mechanisms=(source, hold, compose),
    )
    return ControlTemplate(
        id="I1",
        programs=(_compile(pack, ("one", "two")),),
        observation=ObservationSpec(id="i1.obs", slots=("i1.o",)),
        arms=(
            Arm(
                "g0",
                (Intervention("i1.g", 0, start_tick=1, duration=10),),
            ),
            Arm(
                "g1",
                (Intervention("i1.g", 1, start_tick=1, duration=10),),
            ),
        ),
        property="同 ω 下 O(g=1) − O(g=0) 恒为 10",
    )


# ── 伪相关：共同原因无直接边 ──────────────────────────────────────


def pseudo_correlation_control() -> ControlTemplate:
    """共同原因 R→A、R→B：do(A) 在 CRN 下不改 B。"""
    source = MechanismDecl(
        id="pc.r.update",
        output="pc.r",
        parents=(),
        impl=_draw_impl,
        when=When("phase", "one"),
        address=AddressUse("pc", "r"),
        witnesses=_draw_witnesses("pc", "r"),
    )
    left = MechanismDecl(
        id="pc.a.update",
        output="pc.a",
        parents=(Parent("pc.r", "x"),),
        impl=_identity_impl,
        when=When("phase", "two"),
        witnesses=_identity_witnesses(),
    )
    right = MechanismDecl(
        id="pc.b.update",
        output="pc.b",
        parents=(Parent("pc.r", "x"),),
        impl=_identity_impl,
        when=When("phase", "two"),
        witnesses=_identity_witnesses(),
    )
    pack = ModulePack(
        id="pc.world",
        version="1",
        instances=(GLOBAL,),
        slots=(
            _slot("pc.r", source.id),
            _slot("pc.a", left.id),
            _slot("pc.b", right.id),
        ),
        mechanisms=(source, left, right),
    )
    return ControlTemplate(
        id="伪相关",
        programs=(_compile(pack, ("one", "two")),),
        observation=ObservationSpec(id="pc.obs", slots=("pc.a", "pc.b")),
        arms=(
            Arm("baseline"),
            Arm(
                "do-a-99",
                (Intervention("pc.a", 99, start_tick=1, duration=1),),
            ),
        ),
        property="do(A) 只改 A；B 与基线逐位相同",
    )


# ── 无效干预：非祖先目标 ──────────────────────────────────────────


def invalid_intervention_control() -> ControlTemplate:
    """X→Y 链 + 独立 Z：对 Z 干预不改变 Y。"""
    source = MechanismDecl(
        id="iv.x.update",
        output="iv.x",
        parents=(),
        impl=_draw_impl,
        when=When("phase", "one"),
        address=AddressUse("iv", "x"),
        witnesses=_draw_witnesses("iv", "x"),
    )
    chain = MechanismDecl(
        id="iv.y.update",
        output="iv.y",
        parents=(Parent("iv.x", "x"),),
        impl=_identity_impl,
        when=When("phase", "two"),
        witnesses=_identity_witnesses(),
    )
    independent = MechanismDecl(
        id="iv.z.update",
        output="iv.z",
        parents=(),
        impl=_draw_impl,
        when=When("phase", "one"),
        address=AddressUse("iv", "z"),
        witnesses=_draw_witnesses("iv", "z"),
    )
    pack = ModulePack(
        id="iv.world",
        version="1",
        instances=(GLOBAL,),
        slots=(
            _slot("iv.x", source.id),
            _slot("iv.y", chain.id),
            _slot("iv.z", independent.id),
        ),
        mechanisms=(source, chain, independent),
    )
    return ControlTemplate(
        id="无效干预",
        programs=(_compile(pack, ("one", "two")),),
        observation=ObservationSpec(id="iv.obs", slots=("iv.y", "iv.z")),
        arms=(
            Arm("baseline"),
            Arm(
                "do-z-99",
                (Intervention("iv.z", 99, start_tick=1, duration=1),),
            ),
        ),
        property="对非祖先 Z 的干预：Y 与基线逐位相同",
    )


# ── 机制替换：同状态、不同后续机制 ────────────────────────────────


def _replacement_program(module_id: str, plus: bool) -> object:
    hold = MechanismDecl(
        id=f"{module_id}.x.hold",
        output="rep.x",
        parents=(Parent("rep.x", "x", lag=1),),
        impl=_identity_impl,
        when=When("phase", "one"),
        witnesses=_identity_witnesses(),
    )
    update = MechanismDecl(
        id=f"{module_id}.y.update",
        output="rep.y",
        parents=(Parent("rep.x", "x"),),
        impl=_plus_one_impl if plus else _identity_impl,
        when=When("phase", "two"),
        witnesses=_plus_witnesses() if plus else _identity_witnesses(),
    )
    pack = ModulePack(
        id=module_id,
        version="1",
        instances=(GLOBAL,),
        slots=(
            _slot("rep.x", hold.id, initial=5),
            _slot("rep.y", update.id),
        ),
        mechanisms=(hold, update),
    )
    return _compile(pack, ("one", "two"))


def mechanism_replacement_control() -> ControlTemplate:
    """同当前状态（x=5）、不同后续机制（y=x vs y=x+1）可区分。"""
    return ControlTemplate(
        id="机制替换",
        programs=(
            _replacement_program("rep.v1", False),
            _replacement_program("rep.v2", True),
        ),
        observation=ObservationSpec(id="rep.obs", slots=("rep.y",)),
        arms=(Arm("baseline"),),
        property="同初态下 v1 的 y=5、v2 的 y=6（下一帧可区分）",
    )
