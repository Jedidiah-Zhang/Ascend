"""执行内核声明 — 参考实现与加速实现（世界基座 13 篇）。

一个**内核对**（KernelPair）把一个可执行锚点绑定到两份实现：

- ``reference``：语义规范（可读、可对拍；当前为 Python）。机制输出
  锚点的 ``reference`` 必须与机制注册表声明的实现为**同一可调用对象**
  （注册表是唯一事实源，编译期校验不一致即拒绝）；更新点锚点的
  ``reference`` 即该锚点实现本身；
- ``accelerated``：加速实现（当前为 C；可暂缺，加速批次逐步填入）。

**对拍契约**：同一锚点的两份实现必须逐位一致（bit-identical）。对拍由
``tests/unit/test_kernels.py`` 锁定；任何"近似相等"的偏差都是语义漂移，
必须改实现而不是放宽容差。

锚点（anchor）是机制输出节点 ID 或更新点 ID。内核语义版本（version）
进入世界身份：更换实现 = 新世界身份（WC-1.3）。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable


@dataclass(frozen=True, slots=True)
class KernelPair:
    """一个锚点的参考实现与加速实现。

    Attributes:
        anchor: 机制输出节点 ID 或更新点 ID。
        version: 内核语义版本（进世界身份；实现语义变更必须 bump）。
        reference: 参考实现（语义规范，可调用）。
        accelerated: 加速实现；None = 尚未提供（参考实现即当前路径）。
        note: 说明（不参与身份）。
    """

    anchor: str
    version: str
    reference: Callable[..., object]
    accelerated: Callable[..., object] | None
    note: str


def default_kernel_pairs() -> tuple[KernelPair, ...]:
    """生产内核对（惰性导入领域模块，避免声明层与 pack 层的导入环）。"""
    from ascend.space.state_reference import state_evolve_reference
    from ascend.space.tile_state import state_evolve_arrays

    return (
        KernelPair(
            anchor="terrain.integrate",
            version="state.kernel.v1",
            reference=state_evolve_reference,
            accelerated=state_evolve_arrays,
            note="地形状态统一演化内核（_state.c ↔ Python 参考）",
        ),
    )
