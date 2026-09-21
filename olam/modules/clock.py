"""时钟模块 — 逻辑帧 external 槽位。

逻辑 tick 由运行时按帧写入（驱动层）；机制只能作为父引用读取它。
时钟不得进入任何求值的隐藏路径（WC-4.5）：时间只有这一个显式入口。
"""

from __future__ import annotations

from olam.meta.declarations import (
    InstanceDecl,
    ModulePack,
    Permissions,
    SlotDecl,
    ValueDomain,
)

__all__ = ["CLOCK_TICK", "MODULE"]

CLOCK_TICK = "world.clock.tick"

GLOBAL = InstanceDecl(id="global", kind="global", identity="singleton")

MODULE = ModulePack(
    id="clock",
    version="1",
    instances=(GLOBAL,),
    slots=(
        SlotDecl(
            id=CLOCK_TICK,
            on="global",
            persist="external",
            domain=ValueDomain(kind="int", bits=64, minimum=0),
            permissions=Permissions(observe=True, record=True),
        ),
    ),
    evidence=("运行时按帧写入 tick；机制不得以其他方式获取时间",),
    notes="逻辑帧 external 槽位（驱动层写入，非 W_t）。",
)
