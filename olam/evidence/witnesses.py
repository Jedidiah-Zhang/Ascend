"""见证执行 — 结构最小性（C1）在编译期重跑。

- ``witness_coverage_issues``：每个父引用 ``argument`` 必须有一对"仅该
  argument 不同且输出不同"的见证；无父机制至少一条见证（无证据不生效）。
- ``run_witnesses``：按见证输入构造 :class:`MechanismContext` 重跑实现，
  输出必须一致——整数/布尔精确相等；浮点按 ``1e-9`` 容差（浮点孤岛的
  声明误差由包络另覆盖）。
"""

from __future__ import annotations

import math

from olam.meta.context import MechanismContext
from olam.meta.declarations import MechanismDecl, Witness

__all__ = ["run_witnesses", "witness_coverage_issues"]


def witness_coverage_issues(mechanism: MechanismDecl) -> tuple[str, ...]:
    """结构最小性检查；返回问题列表（空 = 通过）。"""
    if not mechanism.witnesses:
        return (f"机制 {mechanism.id}: 缺少见证",)
    if not mechanism.parents:
        return ()
    issues: list[str] = []
    for parent in mechanism.parents:
        covered = False
        for first in mechanism.witnesses:
            if covered:
                break
            for second in mechanism.witnesses:
                if first is second:
                    continue
                if first.inputs.get(parent.argument) == second.inputs.get(
                    parent.argument
                ):
                    continue
                keys = set(first.inputs) | set(second.inputs)
                others_equal = all(
                    key == parent.argument
                    or first.inputs.get(key) == second.inputs.get(key)
                    for key in keys
                )
                if others_equal and first.outputs != second.outputs:
                    covered = True
                    break
        if not covered:
            issues.append(
                f"机制 {mechanism.id}: 父引用 {parent.argument} "
                f"缺少只变该值的见证"
            )
    return tuple(issues)


def run_witnesses(mechanism: MechanismDecl) -> tuple[str, ...]:
    """重跑全部见证；返回问题列表（空 = 通过）。"""
    issues: list[str] = []
    for witness in mechanism.witnesses:
        issues.extend(_run_one(mechanism, witness))
    return tuple(issues)


def _run_one(mechanism: MechanismDecl, witness: Witness) -> list[str]:
    ctx = MechanismContext(
        mechanism=mechanism,
        root_seed=witness.seed,
        tick=witness.tick,
        instance=(),
        parent_values=witness.inputs,
        params=witness.params,
    )
    try:
        result = mechanism.impl(ctx)
    except Exception as exc:  # noqa: BLE001 - 见证失败必须报出全部信息
        return [
            f"机制 {mechanism.id}/{witness.label}: 求值失败: {exc!r}"
        ]
    actual = _outputs(mechanism, result)
    if len(actual) != len(witness.outputs) or not all(
        _same(left, right) for left, right in zip(actual, witness.outputs)
    ):
        return [
            f"机制 {mechanism.id}/{witness.label}: "
            f"输出 {actual!r} != 预期 {witness.outputs!r}"
        ]
    return []


def _outputs(mechanism: MechanismDecl, result: object) -> tuple[object, ...]:
    slots = mechanism.outputs()
    if len(slots) == 1:
        return (result,)
    if not isinstance(result, dict):
        raise TypeError(
            f"机制 {mechanism.id} 声明多输出，实现必须返回槽位映射"
        )
    return tuple(result[slot] for slot in slots)


def _same(actual: object, expected: object) -> bool:
    if isinstance(actual, float) and isinstance(expected, (int, float)):
        return math.isclose(actual, float(expected), rel_tol=1e-9, abs_tol=1e-9)
    if isinstance(expected, float) and isinstance(actual, (int, float)):
        return math.isclose(float(actual), expected, rel_tol=1e-9, abs_tol=1e-9)
    return actual == expected
