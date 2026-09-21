"""机制求值上下文 — 声明与实现之间的唯一接口。

实现函数只看到这个对象：父值（运行时按关系/lag 解析，或见证按输入给定）、
参数、逻辑帧、当前实例与地址随机。实现不得直接触达状态容器、编译器或
全局单例——这是"机制是纯函数"（WC-4.3）的接口保证。
"""

from __future__ import annotations

from typing import Mapping

from olam.kernel import Address, address_seed

__all__ = ["MechanismContext"]


class MechanismContext:
    """一次机制求值的上下文（由运行时或见证执行器构造）。"""

    def __init__(
        self,
        *,
        mechanism: object,
        root_seed: int,
        tick: int = 0,
        instance: tuple[str | int, ...] = (),
        parent_values: Mapping[str, object] | None = None,
        params: Mapping[str, object] | None = None,
    ) -> None:
        self.mechanism = mechanism
        self.root_seed = root_seed
        self.tick = tick
        self.instance = tuple(instance)
        self._parents = dict(parent_values or {})
        self._params = dict(params or {})

    def parent(self, argument: str) -> object:
        """按 argument 取父值；未提供即拒绝（fail-closed）。"""
        try:
            return self._parents[argument]
        except KeyError:
            raise KeyError(
                f"机制 {self.mechanism.id} 未获得父值 {argument!r}"
            ) from None

    def param(self, parameter_id: str) -> object:
        """取世界装配解析后的参数值。"""
        try:
            return self._params[parameter_id]
        except KeyError:
            raise KeyError(
                f"机制 {self.mechanism.id} 未获得参数 {parameter_id!r}"
            ) from None

    def draw_seed(self, draw_index: int = 0) -> int:
        """本机制当前实例/帧的地址抽取值（256-bit）。"""
        address = self.mechanism.address
        if address is None:
            raise ValueError(f"机制 {self.mechanism.id} 未声明随机地址")
        return address_seed(
            self.root_seed,
            Address(
                namespace=address.namespace,
                purpose=address.purpose,
                instance=self.instance,
                time=self.tick,
                draw_index=draw_index,
            ),
        )

    def draw_range(
        self,
        minimum: int,
        maximum: int,
        *,
        draw_index: int = 0,
    ) -> int:
        """地址 → [minimum, maximum] 整数（取模映射）。"""
        if maximum < minimum:
            raise ValueError(f"值域为空: [{minimum}, {maximum}]")
        return minimum + self.draw_seed(draw_index) % (maximum - minimum + 1)

    def draw_fixed(self, bits: int, *, draw_index: int = 0) -> int:
        """地址 → Q(bits) 的 [0, 1) 均匀值。"""
        if type(bits) is not int or bits <= 0:
            raise ValueError(f"精度必须为正整数: {bits!r}")
        return self.draw_seed(draw_index) % (1 << bits)
