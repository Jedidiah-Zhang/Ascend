"""声明校验与摘要 — 模块包的自洽性检查 + 内容摘要。

- ``validate_module``：模块内部的 id 唯一性与字段级一致性（字段级不变量
  已在声明构造时拒绝，这里做集合级检查）；
- ``source_digest`` / ``mechanism_digest`` / ``module_digest`` / ``kernel_digest``：
  声明 + 实现源码 + 源文件依赖的规范摘要（进世界身份，WC-1.1）。
"""

from __future__ import annotations

import inspect
from dataclasses import asdict, is_dataclass
from pathlib import Path

from ascend.world.kernel import (
    ALGORITHM,
    TABLE_DIGEST,
    digest_object,
    digest_text,
    file_digest,
)

from .declarations import MechanismDecl, ModulePack

__all__ = [
    "kernel_digest",
    "mechanism_digest",
    "module_digest",
    "source_digest",
    "validate_module",
]


def _duplicates(values: list[str]) -> list[str]:
    seen: set[str] = set()
    duplicated: list[str] = []
    for value in values:
        if value in seen and value not in duplicated:
            duplicated.append(value)
        seen.add(value)
    return duplicated


def validate_module(pack: ModulePack) -> tuple[str, ...]:
    """模块自洽性检查；返回问题列表（空 = 通过）。"""
    issues: list[str] = []
    for label, values in (
        ("实例", [item.id for item in pack.instances]),
        ("关系", [item.id for item in pack.relations]),
        ("槽位", [item.id for item in pack.slots]),
        ("机制", [item.id for item in pack.mechanisms]),
        ("不变量", [item.id for item in pack.invariants]),
        ("参数", [item.id for item in pack.parameters]),
        ("旋钮", [item.id for item in pack.knobs]),
    ):
        for duplicate in _duplicates(values):
            issues.append(f"模块 {pack.id}: {label} id 重复: {duplicate}")

    slot_ids = {slot.id for slot in pack.slots}
    for mechanism in pack.mechanisms:
        for slot in mechanism.outputs():
            if slot not in slot_ids:
                issues.append(
                    f"模块 {pack.id}: 机制 {mechanism.id} 输出槽位未声明: {slot}"
                )
        labels = [witness.label for witness in mechanism.witnesses]
        for duplicate in _duplicates(labels):
            issues.append(
                f"模块 {pack.id}: 机制 {mechanism.id} 见证 label 重复: {duplicate}"
            )
    for slot in pack.slots:
        if slot.persist == "state" and slot.writer:
            writers = [
                mechanism.id
                for mechanism in pack.mechanisms
                if mechanism.id == slot.writer
            ]
            if not writers:
                issues.append(
                    f"模块 {pack.id}: 槽位 {slot.id} 的 writer 未声明: {slot.writer}"
                )
    return tuple(issues)


def source_digest(function: object) -> str:
    """实现源码摘要（取不到源码时退化为模块.限定名，仍然稳定）。"""
    try:
        text = inspect.getsource(function)  # type: ignore[arg-type]
    except (OSError, TypeError):
        module = getattr(function, "__module__", "?")
        qualname = getattr(function, "__qualname__", repr(function))
        return digest_text(f"{module}.{qualname}")
    return digest_text(text)


def _jsonable(value: object) -> object:
    """把声明对象转成可 JSON 化的规范结构（摘要用）。"""
    if is_dataclass(value) and not isinstance(value, type):
        payload = {}
        for key, item in asdict(value).items():
            payload[key] = _jsonable(item)
        return payload
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    return value


def mechanism_digest(mechanism: MechanismDecl) -> str:
    """机制摘要 = 声明字段 + 实现源码 + 加速实现源码。"""
    payload = _jsonable(mechanism)
    if isinstance(payload, dict):
        payload.pop("impl", None)
        payload.pop("accelerated", None)
        payload["impl"] = source_digest(mechanism.impl)
        if mechanism.accelerated is not None:
            payload["accelerated"] = source_digest(mechanism.accelerated)
    return digest_object(payload)


def module_digest(pack: ModulePack, *, root: Path | None = None) -> str:
    """模块摘要 = 声明 + 各机制实现摘要 + 源文件依赖内容摘要。"""
    payload = {
        "id": pack.id,
        "version": pack.version,
        "instances": _jsonable(pack.instances),
        "relations": _jsonable(pack.relations),
        "slots": _jsonable(pack.slots),
        "mechanisms": [mechanism_digest(item) for item in pack.mechanisms],
        "invariants": [
            {
                "id": item.id,
                "slot": item.slot,
                "severity": item.severity,
                "message": item.message,
                "check": source_digest(item.check),
            }
            for item in pack.invariants
        ],
        "parameters": _jsonable(pack.parameters),
        "knobs": _jsonable(pack.knobs),
        "depends_on": list(pack.depends_on),
        "source_deps": {
            str(path): file_digest(root / path if root is not None else path)
            for path in pack.source_deps
        },
        "evidence": list(pack.evidence),
    }
    return digest_object(payload)


def kernel_digest() -> str:
    """数值内核摘要：冻表 + 地址算法 + 定点原语源码。"""
    fixed_path = Path(__file__).resolve().parents[1] / "kernel" / "fixed.py"
    return digest_object(
        {
            "tables": TABLE_DIGEST,
            "rng": ALGORITHM,
            "fixed": file_digest(fixed_path),
        }
    )
