"""声明数据加载的公共基元（WC-3 / 附录 D）。

- DeclarationError: 声明违规的统一异常；
- canonical_bytes: 规范 JSON 编码（键排序、紧凑分隔符、UTF-8），
  供声明摘要与状态编码共用；
- require_* / expect_keys: 严格校验原语（未知字段、类型、空值一律拒绝）。
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any, NoReturn


class DeclarationError(ValueError):
    """声明数据违反契约。"""


def fail(where: str, message: str) -> NoReturn:
    raise DeclarationError(f"{where}: {message}")


def canonical_bytes(value: object) -> bytes:
    """规范 JSON 编码：相等 ↔ 逐字节相等。"""
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")


def require_mapping(value: object, where: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        fail(where, f"应为对象，实际为 {type(value).__name__}")
    return value


def require_list(value: object, where: str) -> list[Any]:
    if not isinstance(value, list):
        fail(where, f"应为数组，实际为 {type(value).__name__}")
    return value


def require_str(value: object, where: str) -> str:
    if not isinstance(value, str) or not value:
        fail(where, "应为非空字符串")
    return value


def require_bool(value: object, where: str) -> bool:
    if type(value) is not bool:
        fail(where, f"应为布尔值，实际为 {value!r}")
    return value


def require_int(value: object, where: str) -> int:
    if type(value) is not int:
        fail(where, f"应为整数，实际为 {value!r}")
    return value


def expect_keys(
    obj: Mapping[str, Any],
    required: tuple[str, ...],
    optional: tuple[str, ...],
    where: str,
) -> None:
    allowed = set(required) | set(optional)
    unknown = sorted(set(obj) - allowed)
    if unknown:
        fail(where, f"未知字段: {', '.join(unknown)}")
    missing = [key for key in required if key not in obj]
    if missing:
        fail(where, f"缺少必填字段: {', '.join(missing)}")
