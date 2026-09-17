"""Loom of Fate — 世界级种子派生的无状态便利层。

设计文档: docs/世界框架/随机系统/设计.md；随机语义以《世界契约》WC-5 为准。

职责边界（三层分离）:
  - Fate Loom 只提供**外生随机性 U**（不可控/外生的随机因素）。
  - 世界机制 F（状态如何演化）与代理策略 π（行动如何选择）是
    消费 U 的调用方，不归本模块管。
  - 织机不决定命运，只生成命运中的"偶然性"。

本模块只做路径累积与 derive 封装；随机取值一律经地址纯函数
（``ascend.fate.address``），不存在流状态或消费顺序。
"""

from __future__ import annotations

from .derive import MASK_256, derive

__all__ = ["LoomOfFate"]


class LoomOfFate:
    """命运织机 — 世界种子作用域内的派生器。

    用法:
        loom = LoomOfFate(world_seed)
        loom.derive("world", "birth_point")
        seed = loom.domain("weather").derive("proxy", "temp")

    子织机 path 累积：``domain("a").derive("b")`` 与
    ``derive("a", "b")`` 等价（身份拼接一致，有测试锁定）。
    """

    __slots__ = ("_root", "_path")

    def __init__(self, world_seed: int, path: tuple[str, ...] = ()) -> None:
        """初始化织机。

        Args:
            world_seed: 世界种子（manifest.seed，256-bit 空间）。
            path: 子织机路径（由 domain() 累积，通常不直接传）。
        """
        self._root = world_seed & MASK_256
        self._path = path

    def derive(self, *parts: str | int) -> int:
        """在织机作用域内派生种子（含 path 累积）。

        Args:
            parts: 身份分量。

        Returns:
            256-bit 派生种子。
        """
        return derive(self._root, *self._path, *parts)

    def domain(self, *names: str) -> "LoomOfFate":
        """创建子织机（namespace 作用域，path 累积）。

        Args:
            names: 子域名称（如 "environment"、"weather"）。

        Returns:
            新 LoomOfFate 实例，path = 自身 path + names。
        """
        for name in names:
            if not isinstance(name, str):
                raise TypeError(f"domain 名必须为 str，实际 {type(name).__name__}")
        return LoomOfFate(self._root, self._path + tuple(names))
