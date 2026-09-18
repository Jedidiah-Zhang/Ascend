"""世界模块包 — P0 提供时钟与玩具模块；真实世界模块在 P1 接入。"""

from __future__ import annotations

from . import clock, primitives, toy

__all__ = ["clock", "primitives", "toy"]
