"""玩具模块 — P0 验收与测试用的最小世界构造。

- ``W0``：三阶段帧内顺序（机制读前序阶段写入，不依赖调用顺序）；
- ``W1``：链式机制 + 地址随机（节点干预/CRN 的验收对象）；
- ``SPATIAL``：一维场的关系偏移/边界/聚合 + 周期 + 事件。

这些模块不是游戏内容：它们是新核心的"最小可手算世界"，用于验收与
回归。真实世界模块（天气/地形）在 P1 接入。
"""

from __future__ import annotations

from ascend.world.kernel import round_half_even_div
from ascend.world.meta.declarations import (
    AddressUse,
    Arithmetic,
    InstanceDecl,
    InvariantDecl,
    MechanismDecl,
    ModulePack,
    Parent,
    ParameterDecl,
    Permissions,
    RelationDecl,
    SlotDecl,
    ValueDomain,
    When,
    Witness,
)
from ascend.world.modules.primitives import GLOBAL

__all__ = ["SPATIAL", "W0", "W1"]

_INT = ValueDomain(kind="int", bits=32)
_INTERVENE = Permissions(intervene=True, observe=True, record=True)


# ── W0：三阶段帧内顺序 ─────────────────────────────────────────────


def _w0_mid1(ctx: object) -> int:
    return ctx.parent("x") + ctx.param("toy.u_mid")


def _w0_mid2(ctx: object) -> int:
    return 2 * ctx.parent("mid1")


def _w0_advance(ctx: object) -> int:
    return ctx.parent("mid2")


W0 = ModulePack(
    id="toy.w0",
    version="1",
    instances=(GLOBAL,),
    parameters=(
        ParameterDecl(
            id="toy.u_mid", default=2, minimum=0, maximum=1000,
            unit="dimensionless",
        ),
    ),
    slots=(
        SlotDecl(
            id="toy.x", on="global", persist="state", domain=_INT,
            permissions=_INTERVENE, writer="toy.x.advance", initial=1,
        ),
        SlotDecl(
            id="toy.mid1", on="global", persist="state", domain=_INT,
            writer="toy.mid1.compute",
        ),
        SlotDecl(
            id="toy.mid2", on="global", persist="state", domain=_INT,
            writer="toy.mid2.compute",
        ),
    ),
    mechanisms=(
        MechanismDecl(
            id="toy.mid1.compute",
            output="toy.mid1",
            parents=(Parent(slot="toy.x", argument="x", lag=1),),
            impl=_w0_mid1,
            when=When(mode="phase", key="stage1"),
            equation="mid1 = x_frame_start + u_mid",
            params=("toy.u_mid",),
            witnesses=(
                Witness("base", {"x": 1}, (3,), {"toy.u_mid": 2}),
                Witness("x_changed", {"x": 2}, (4,), {"toy.u_mid": 2}),
            ),
        ),
        MechanismDecl(
            id="toy.mid2.compute",
            output="toy.mid2",
            parents=(Parent(slot="toy.mid1", argument="mid1"),),
            impl=_w0_mid2,
            when=When(mode="phase", key="stage2"),
            equation="mid2 = 2 * mid1",
            witnesses=(
                Witness("base", {"mid1": 3}, (6,)),
                Witness("mid1_changed", {"mid1": 4}, (8,)),
            ),
        ),
        MechanismDecl(
            id="toy.x.advance",
            output="toy.x",
            parents=(Parent(slot="toy.mid2", argument="mid2"),),
            impl=_w0_advance,
            when=When(mode="phase", key="stage3"),
            equation="x = mid2",
            witnesses=(
                Witness("base", {"mid2": 6}, (6,)),
                Witness("mid2_changed", {"mid2": 7}, (7,)),
            ),
        ),
    ),
    notes="W0 构造：mid1@stage1 → mid2@stage2 → x@stage3。",
)


# ── W1：链式机制 + 地址随机 ────────────────────────────────────────


def _w1_rt(ctx: object) -> int:
    return ctx.draw_range(0, 9)


def _w1_med(ctx: object) -> int:
    return ctx.parent("rt") + ctx.draw_range(0, 9)


def _w1_out(ctx: object) -> int:
    return ctx.parent("med") + ctx.draw_range(0, 9)


def _w1_ind(ctx: object) -> int:
    return ctx.draw_range(0, 9)


W1 = ModulePack(
    id="toy.w1",
    version="1",
    instances=(GLOBAL,),
    slots=(
        SlotDecl(
            id="toy.rt", on="global", persist="state", domain=_INT,
            writer="toy.rt.update",
        ),
        SlotDecl(
            id="toy.med", on="global", persist="state", domain=_INT,
            permissions=_INTERVENE, writer="toy.med.update",
        ),
        SlotDecl(
            id="toy.out", on="global", persist="state", domain=_INT,
            writer="toy.out.update",
        ),
        SlotDecl(
            id="toy.ind", on="global", persist="state", domain=_INT,
            writer="toy.ind.update",
        ),
    ),
    mechanisms=(
        MechanismDecl(
            id="toy.rt.update",
            output="toy.rt",
            parents=(),
            impl=_w1_rt,
            when=When(mode="phase", key="stage1"),
            equation="rt ~ uniform{0..9}",
            address=AddressUse(namespace="toy", purpose="rt"),
            witnesses=(
                Witness("seed0", {}, (5,), seed=0, tick=0),
                Witness("seed1", {}, (2,), seed=1, tick=0),
            ),
        ),
        MechanismDecl(
            id="toy.med.update",
            output="toy.med",
            parents=(Parent(slot="toy.rt", argument="rt"),),
            impl=_w1_med,
            when=When(mode="phase", key="stage2"),
            equation="med = rt + uniform{0..9}",
            address=AddressUse(namespace="toy", purpose="med"),
            witnesses=(
                Witness("rt5", {"rt": 5}, (7,), seed=0, tick=0),
                Witness("rt6", {"rt": 6}, (8,), seed=0, tick=0),
            ),
        ),
        MechanismDecl(
            id="toy.out.update",
            output="toy.out",
            parents=(Parent(slot="toy.med", argument="med"),),
            impl=_w1_out,
            when=When(mode="phase", key="stage3"),
            equation="out = med + uniform{0..9}",
            address=AddressUse(namespace="toy", purpose="out"),
            witnesses=(
                Witness("med5", {"med": 5}, (13,), seed=0, tick=0),
                Witness("med6", {"med": 6}, (14,), seed=0, tick=0),
            ),
        ),
        MechanismDecl(
            id="toy.ind.update",
            output="toy.ind",
            parents=(),
            impl=_w1_ind,
            when=When(mode="phase", key="stage1"),
            equation="ind ~ uniform{0..9}",
            address=AddressUse(namespace="toy", purpose="ind"),
            witnesses=(
                Witness("seed0", {}, (8,), seed=0, tick=0),
                Witness("seed1", {}, (8,), seed=1, tick=0),
            ),
        ),
    ),
    notes="W1 构造：rt@stage1 → med@stage2 → out@stage3，ind 独立。",
)


# ── SPATIAL：场关系 / 周期 / 事件 ──────────────────────────────────


def _spatial_hold(ctx: object) -> int:
    return ctx.parent("u")


def _spatial_smooth(ctx: object) -> int:
    return round_half_even_div(
        ctx.parent("left") + 2 * ctx.parent("center") + ctx.parent("right"),
        4,
    )


def _spatial_day_count(ctx: object) -> int:
    return ctx.parent("count") + 1


def _spatial_flash_bump(ctx: object) -> int:
    return ctx.parent("n") + 1


def _spatial_bounds(value: object) -> bool:
    return all(0 <= item <= 1000 for item in value.values())


SPATIAL = ModulePack(
    id="toy.spatial",
    version="1",
    instances=(
        GLOBAL,
        InstanceDecl(id="lattice.line", kind="lattice", identity="xy",
                     size=(5,)),
    ),
    relations=(
        RelationDecl(
            id="toy.left", kind="spatial", source="lattice.line",
            target="lattice.line", offsets=((-1,),), boundary="clamp",
        ),
        RelationDecl(
            id="toy.center", kind="spatial", source="lattice.line",
            target="lattice.line", offsets=((0,),), boundary="clamp",
        ),
        RelationDecl(
            id="toy.right", kind="spatial", source="lattice.line",
            target="lattice.line", offsets=((1,),), boundary="clamp",
        ),
    ),
    slots=(
        SlotDecl(
            id="toy.u", on="lattice.line", persist="state", domain=_INT,
            permissions=_INTERVENE, writer="toy.u.hold", initial=0,
        ),
        SlotDecl(
            id="toy.v", on="lattice.line", persist="state", domain=_INT,
            writer="toy.v.smooth", initial=0,
        ),
        SlotDecl(
            id="toy.daycount", on="global", persist="state", domain=_INT,
            writer="toy.daycount.tick", initial=0,
        ),
        SlotDecl(
            id="toy.flash", on="global", persist="state", domain=_INT,
            writer="toy.flash.bump", initial=0,
        ),
    ),
    mechanisms=(
        MechanismDecl(
            id="toy.u.hold",
            output="toy.u",
            parents=(Parent(slot="toy.u", argument="u", lag=1),),
            impl=_spatial_hold,
            when=When(mode="phase", key="stage1"),
            equation="u_t = u_{t-1}（初始场由装配提供）",
            witnesses=(
                Witness("base", {"u": 7}, (7,)),
                Witness("u_changed", {"u": 8}, (8,)),
            ),
        ),
        MechanismDecl(
            id="toy.v.smooth",
            output="toy.v",
            parents=(
                Parent(slot="toy.u", argument="left",
                       relation="toy.left"),
                Parent(slot="toy.u", argument="center",
                       relation="toy.center"),
                Parent(slot="toy.u", argument="right",
                       relation="toy.right"),
            ),
            impl=_spatial_smooth,
            when=When(mode="phase", key="stage2"),
            equation="v = (left + 2*center + right) / 4",
            witnesses=(
                Witness("base", {"left": 4, "center": 8, "right": 12}, (8,)),
                Witness("left_changed", {"left": 8, "center": 8, "right": 12}, (9,)),
                Witness("center_changed", {"left": 4, "center": 12, "right": 12}, (10,)),
                Witness("right_changed", {"left": 4, "center": 8, "right": 8}, (7,)),
            ),
        ),
        MechanismDecl(
            id="toy.daycount.tick",
            output="toy.daycount",
            parents=(Parent(slot="toy.daycount", argument="count", lag=1),),
            impl=_spatial_day_count,
            when=When(mode="period", key="day"),
            equation="daycount_t = daycount_{t-1} + 1（每游戏日）",
            witnesses=(
                Witness("base", {"count": 0}, (1,)),
                Witness("count_changed", {"count": 3}, (4,)),
            ),
        ),
        MechanismDecl(
            id="toy.flash.bump",
            output="toy.flash",
            parents=(Parent(slot="toy.flash", argument="n", lag=1),),
            impl=_spatial_flash_bump,
            when=When(mode="event", key="flash"),
            equation="flash_t = flash_{t-1} + 1（事件触发时）",
            witnesses=(
                Witness("base", {"n": 0}, (1,)),
                Witness("n_changed", {"n": 2}, (3,)),
            ),
        ),
    ),
    invariants=(
        InvariantDecl(
            id="toy.v.bounds", slot="toy.v", check=_spatial_bounds,
            severity="reject", message="toy.v 超出 [0, 1000]",
        ),
    ),
    notes="场关系/边界/聚合 + 周期 + 事件的最小构造。",
)
