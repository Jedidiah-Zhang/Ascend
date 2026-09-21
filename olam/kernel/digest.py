"""内容摘要 — 声明身份与依赖的规范哈希（WC-1.1 / WC-3.5）。

- ``canonical_bytes``：规范 JSON（键排序、紧凑分隔符、UTF-8、拒绝 NaN）；
- ``digest_object`` / ``digest_text`` / ``digest_bytes``：sha256 摘要，
  统一前缀 ``sha256:``；
- ``file_digest``：文件内容摘要（源码依赖进身份用，与路径无关）。

规范编码是相等、序列化与摘要的公共底座：同一份声明在任何机器、
任何目录下产出同一摘要。
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

__all__ = [
    "canonical_bytes",
    "digest_bytes",
    "digest_object",
    "digest_text",
    "file_digest",
]

_PREFIX = "sha256:"


def canonical_bytes(value: object) -> bytes:
    """规范 JSON 编码：键排序、紧凑分隔符、UTF-8、禁止 NaN/Inf。"""
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")


def digest_bytes(data: bytes) -> str:
    """字节串摘要（``sha256:`` 前缀）。"""
    return _PREFIX + hashlib.sha256(data).hexdigest()


def digest_text(text: str) -> str:
    """文本摘要（UTF-8 编码后取 sha256）。"""
    return digest_bytes(text.encode("utf-8"))


def digest_object(value: object) -> str:
    """任意可 JSON 化对象的规范摘要。"""
    return digest_bytes(canonical_bytes(value))


def file_digest(path: str | Path) -> str:
    """文件内容摘要（不含路径：同一内容在不同检出位置摘要相同）。"""
    return digest_bytes(Path(path).read_bytes())
