#!/usr/bin/env python3
"""条款↔证据对账门禁（《世界契约》附录 D.5 / WC-11.2）。

校验 ``clause_evidence.py`` 的对账表与契约正文、验收判据、证据文件一致：

- 条款集合一一对应（缺条款/未知条款/重复即红）；
- 状态 ∈ {covered, partial, gap}；covered 必须有检查码或证据文件；
  partial/gap 必须给出 note（原因与补齐计划）；
- 检查码必须存在于 ``world_checks.CHECK_CODES``；
- 证据文件路径必须存在。

运行:
    .venv/bin/python kheker/acceptance/clause_ledger.py --check
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(ROOT))  # 供 import olam / miskhak

import clause_evidence  # noqa: E402

import world_checks  # noqa: E402

CONTRACT = ROOT / "docs" / "研究理论" / "世界契约.md"
_CLAUSE_RE = re.compile(r"^\*\*(WC-\d+\.\d+)")
_STATUSES = ("covered", "partial", "gap")


def contract_clauses() -> tuple[str, ...]:
    """契约正文的条款号（出现顺序）。"""
    ids: list[str] = []
    for line in CONTRACT.read_text(encoding="utf-8").splitlines():
        match = _CLAUSE_RE.match(line)
        if match:
            ids.append(match.group(1))
    return tuple(ids)


def validate() -> tuple[str, ...]:
    """对账表校验；返回问题列表（空 = 通过）。"""
    issues: list[str] = []
    known = set(contract_clauses())
    seen: set[str] = set()
    for entry in clause_evidence.CLAUSES:
        if entry.clause not in known:
            issues.append(f"未知条款: {entry.clause}")
        if entry.clause in seen:
            issues.append(f"条款重复: {entry.clause}")
        seen.add(entry.clause)
        if entry.status not in _STATUSES:
            issues.append(f"{entry.clause}: 未知状态 {entry.status!r}")
        if entry.status == "covered" and not (entry.checks or entry.evidence):
            issues.append(f"{entry.clause}: covered 但无检查码/证据")
        if entry.status == "covered" and not entry.positive:
            issues.append(f"{entry.clause}: covered 但缺少正证据描述")
        if entry.status in ("partial", "gap") and not entry.note.strip():
            issues.append(
                f"{entry.clause}: {entry.status} 必须给出 note（原因与补齐计划）"
            )
        for code in entry.checks:
            if code not in world_checks.CHECK_CODES:
                issues.append(f"{entry.clause}: 未知检查码 {code}")
        for path in entry.evidence:
            if not (ROOT / path).exists():
                issues.append(f"{entry.clause}: 证据文件不存在 {path}")
    missing = sorted(known - seen)
    if missing:
        issues.append(f"条款未登记: {missing}")
    return tuple(issues)


def main() -> int:
    ap = argparse.ArgumentParser(description="条款↔证据对账门禁")
    ap.add_argument("--check", action="store_true", help="巡检（缺省即巡检）")
    ap.parse_args()
    issues = validate()
    ok = not issues
    partial = sum(
        1 for entry in clause_evidence.CLAUSES
        if entry.status != "covered"
    )
    print(
        f"[{'PASS' if ok else 'FAIL'}] 条款↔证据对账 | "
        f"{len(clause_evidence.CLAUSES)} 条款（partial/gap {partial}）"
        + ("" if ok else f"；问题 {len(issues)}")
    )
    for issue in issues[:20]:
        print("   ", issue)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
