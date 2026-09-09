"""世界验收 runner — C0–C2 / W0–W5 / I0–I1 统一执行与产物落盘。

运行:
    .venv/bin/python research/acceptance/run_acceptance.py [--json out.json] [--fast]

判据见 ``checks.py``；每项独立报告输入、参考输出、引擎输出与首个分歧
（04 §1：只保存最终状态哈希不足以定位分歧）。退出码 0 = 全部通过。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parents[1] / "backend"))

import checks  # noqa: E402
import gen_unrolled_dag  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description="世界验收 runner")
    ap.add_argument("--json", default="", help="产物输出路径（缺省只打印）")
    ap.add_argument("--fast", action="store_true", help="快速模式（缩短窗口）")
    ap.add_argument(
        "--check", action="store_true",
        help="只巡检 Lean UnrolledDag 生成物与生产声明是否漂移",
    )
    args = ap.parse_args()

    if args.check:
        return gen_unrolled_dag.main_check()

    results = []
    for check in checks.ALL_CHECKS:
        try:
            results.append(check())
        except Exception as exc:  # 判据自身异常 = 失败（不掩盖）
            code = check.__name__.removeprefix("check_").upper()
            results.append(checks.CheckResult(
                code=code, title="<执行异常>", passed=False,
                detail=f"{type(exc).__name__}: {exc}",
            ))
    for result in results:
        mark = "PASS" if result.passed else "FAIL"
        print(f"[{mark}] {result.code} {result.title} | {result.detail}")
        if result.first_divergence is not None:
            print(f"        首分歧: {result.first_divergence}")
    passed = sum(1 for result in results if result.passed)
    print(f"\n汇总: {passed}/{len(results)} 通过")

    if args.json:
        payload = {
            "passed": passed,
            "total": len(results),
            "results": [result.plain() for result in results],
        }
        Path(args.json).write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        print(f"产物已写入: {args.json}")
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())
