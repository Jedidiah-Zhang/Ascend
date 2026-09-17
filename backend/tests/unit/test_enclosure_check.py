"""V5 包含对拍测试（issue #53 P4）：生产输出必须落在声明包络内。"""

from __future__ import annotations

import sys
from pathlib import Path

_EQ = Path(__file__).resolve().parents[3] / "research" / "equations"
sys.path.insert(0, str(_EQ))

import enclosure_check  # noqa: E402

from ascend.causal.world import ASCEND_MECHANISMS  # noqa: E402


def test_enclosure_builders_contain_production_outputs():
    report = enclosure_check.check_enclosures(ASCEND_MECHANISMS)
    assert report.uncovered == [], report.uncovered
    assert report.problems == [], report.problems[:3]
    assert report.samples >= 200, report
