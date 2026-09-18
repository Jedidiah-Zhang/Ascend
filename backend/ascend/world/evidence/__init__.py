"""证据 — 见证执行（C1）与新核心验收。"""

from __future__ import annotations

from .acceptance import AcceptanceResult, run_acceptance
from .witnesses import run_witnesses, witness_coverage_issues

__all__ = [
    "AcceptanceResult",
    "run_acceptance",
    "run_witnesses",
    "witness_coverage_issues",
]
