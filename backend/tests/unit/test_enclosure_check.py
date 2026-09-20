"""V5 包含对拍测试：生产输出必须落在声明包络内。"""

from __future__ import annotations

import sys
from pathlib import Path

_EQ = Path(__file__).resolve().parents[3] / "research" / "equations"
sys.path.insert(0, str(_EQ))

import enclosure_check  # noqa: E402
import export_world  # noqa: E402


def test_enclosure_builders_contain_production_outputs():
    report = enclosure_check.check_enclosures(
        export_world.build_program(),
    )
    assert report.uncovered == [], report.uncovered
    assert report.problems == [], report.problems[:3]
    assert report.samples >= 200, report
    # 逐机制覆盖：每个 builder 至少一个有效样本（防"零样本仍绿"）
    zero = [k for k, n in report.samples_by_mechanism.items() if n == 0]
    assert zero == [], f"零样本机制（包络未被验证）: {zero}"
    # 域外跳过率受控（说明采样盒落在声明域内）
    assert report.skipped <= max(10, report.samples // 10), report.skipped
