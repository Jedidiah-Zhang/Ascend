"""种子派生原语 — Loom of Fate（命运的织机）的数学内核。

契约（设计文档: docs/世界框架/随机系统/设计.md）:
  - ``derive(parent, *parts)`` 是纯函数：同参同值，跨进程/跨平台位级一致。
  - 身份编码无歧义：str 带 2B 长度前缀、int 固定 32B 补码，
    杜绝 ``("a", 1)`` 与 ``("a1",)`` 之类拼接碰撞。
  - 禁用内建 ``hash()``（PYTHONHASHSEED 按进程加盐，跨进程不确定）。
  - 输出为完整 sha256 摘要 → 256-bit（0..2^256-1），碰撞抗性生日界 2^128。

用途: 任何"与世界 seed 相关的确定性随机身份"必须经本模块派生——
namespace/entity/purpose/tick 的语义差异在此雪崩为统计独立的种子。
"""

import hashlib

MASK_256: int = (1 << 256) - 1

# 派生算法版本字节：未来改变编码/算法时 +1，旧值全部失效
_VERSION: bytes = b"\x01"
_PARENT_BYTES: int = 32


def _encode_part(part: str | int) -> bytes:
    """单个身份分量的规范字节编码（类型标签 + 长度/定宽）。"""
    if isinstance(part, str):
        raw = part.encode("utf-8")
        if len(raw) > 0xFFFF:
            raise ValueError(f"身份分量过长: {len(raw)} 字节")
        return b"s" + len(raw).to_bytes(2, "big") + raw
    if isinstance(part, int):
        return b"i" + part.to_bytes(_PARENT_BYTES, "big", signed=True)
    raise TypeError(f"身份分量必须为 str/int，实际 {type(part).__name__}")


def derive(parent: int, *parts: str | int) -> int:
    """从父种子与身份分量派生确定性子种子。

    Args:
        parent: 父种子（世界 seed 或上级派生值），按 256-bit 掩码归一。
        parts: 身份分量（namespace/entity/purpose/tick 等），str 或 int。
            int 分量按 32B 有符号编码——范围 ±2^255（超出抛 OverflowError；
            现实取值：tick/坐标远在此内）。

    Returns:
        256-bit 派生种子（0..2^256-1）。同参恒等，任一分量微变即雪崩。
    """
    h = hashlib.sha256()
    h.update(_VERSION)
    h.update((parent & MASK_256).to_bytes(_PARENT_BYTES, "big"))
    for part in parts:
        h.update(_encode_part(part))
    return int.from_bytes(h.digest(), "big")


__all__ = ["derive", "MASK_256"]