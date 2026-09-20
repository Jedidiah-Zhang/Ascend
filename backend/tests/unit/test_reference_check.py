"""独立参考对拍测试。

全 35 生产机制必须：
- 被覆盖（可执行方程表达式或 ``REFERENCE_IMPLS`` 独立实现）；
- 在 C1 见证上下文 + 随机抖动样本上与生产求值一致（V4 的测试内对照）。
"""

from __future__ import annotations

import sys
from pathlib import Path

_EQ = Path(__file__).resolve().parents[3] / "research" / "equations"
sys.path.insert(0, str(_EQ))

import export_world  # noqa: E402
import reference_check  # noqa: E402


def test_all_mechanisms_covered_and_matching():
    program = export_world.build_program()
    report = reference_check.check_mechanisms(program)
    assert report.mechanisms == len(program.mechanisms)
    assert report.uncovered == [], (
        f"方程字符串或参考实现未覆盖: {report.uncovered}"
    )
    assert report.problems == [], (
        f"独立参考与生产不一致: {report.problems[:5]}"
    )
    # 采样覆盖下界（防对拍退化为空转）
    assert report.samples - report.skipped >= 500, report
    assert report.expression_ids and report.impl_ids


def test_impossible_equation_is_uncovered():
    """无法解析且无参考实现的方程必须被覆盖门禁抓住（判别力）。"""
    from dataclasses import replace

    program = export_world.build_program()
    mechanism = program.mechanisms["weather.tick.derive_day.v1"]
    broken = replace(
        mechanism, id="toy.broken.v1",
        equation="this is not an expression",
    )
    from mechanism_reference import unresolved_names

    assert unresolved_names(broken), "无法解析的方程必须报未覆盖"
