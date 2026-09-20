"""地形状态模块 — 统一演化内核（从旧实现逐位移植）。"""

from __future__ import annotations

from . import kernel
from .module import MODULE

__all__ = ["MODULE", "kernel"]
