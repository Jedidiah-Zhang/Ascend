"""模块共享原语 — 跨模块复用的实例声明。

相同声明跨模块幂等合并（编译器 ``_merge``）：共享原语在这里声明一次，
各模块按需引用，不产生重复定义。
"""

from __future__ import annotations

from olam.meta.declarations import InstanceDecl

__all__ = ["GLOBAL"]

GLOBAL = InstanceDecl(id="global", kind="global", identity="singleton")
