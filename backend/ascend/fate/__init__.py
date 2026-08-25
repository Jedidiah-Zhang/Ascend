"""Loom of Fate（命运的织机）— 种子/随机流统一管理。

提供世界级确定性随机源：
  - derive: 派生原语（sha256 规范编码，256-bit，跨平台位级一致）
  - LoomOfFate: 命运织机（domain 子域 / stream 独立流）
  - FateStream: 携带身份的确定性随机流（禁重播种）

设计文档: docs/世界框架/随机系统/设计.md
"""

from .derive import MASK_256, derive
from .loom_of_fate import FateStream, LoomOfFate, format_fate_path

__all__ = [
    "LoomOfFate",
    "FateStream",
    "derive",
    "format_fate_path",
    "MASK_256",
]