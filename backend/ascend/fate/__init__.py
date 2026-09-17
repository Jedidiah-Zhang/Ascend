"""Loom of Fate（命运的织机）— 种子派生与地址随机。

提供世界级确定性随机源：
  - derive: 派生原语（sha256 规范编码，256-bit，跨平台位级一致）
  - LoomOfFate: 命运织机（domain 子域的无状态派生便利层）
  - FateAddress / address_seed / address_value: 地址 → 值纯函数（WC-5）

设计文档: docs/世界框架/随机系统/设计.md
"""

from .address import FATE_ALGORITHM, FateAddress, address_seed, address_value
from .derive import MASK_256, derive
from .loom_of_fate import LoomOfFate

__all__ = [
    "FATE_ALGORITHM",
    "FateAddress",
    "LoomOfFate",
    "MASK_256",
    "address_seed",
    "address_value",
    "derive",
]
