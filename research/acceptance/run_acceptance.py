"""世界验收 runner — C0–C2 / W0–W5 / I0–I1 / L3 统一执行与产物落盘。

运行:
    .venv/bin/python research/acceptance/run_acceptance.py [--json out.json]
    .venv/bin/python research/acceptance/run_acceptance.py --check
    .venv/bin/python research/acceptance/run_acceptance.py --mutation

判据见 ``checks.py``；每项独立报告输入、参考输出、引擎输出与首个分歧
（04 §1：只保存最终状态哈希不足以定位分歧）。退出码 0 = 全部通过。

产物 manifest（#50）：声明 ID/摘要、世界程序身份、实现摘要表、契约版本、
代码版本（git commit）与运行环境——实验档案必须可绑定到"哪个世界、
哪份代码"。逐判据的种子/输入在其 ``input`` 字段中。

``--mutation``：生产实现变异探针——分别破坏一处实现（观测不量化、
泄露检测恒真、边界算子换环绕、注入核投影置空），对应判据必须变红；
任一未被检出即失败（判据徒有其表）。
"""

from __future__ import annotations

import argparse
import inspect
import json
import platform
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parents[1] / "backend"))

import checks  # noqa: E402
import gen_unrolled_dag  # noqa: E402


def build_manifest() -> dict:
    """实验档案身份：世界声明/程序/实现 + 代码版本 + 运行环境（#50）。"""
    from ascend.causal.impl_digests import get_impl_digests
    from ascend.causal.program import get_default_program
    from ascend.causal.world import ASCEND_MECHANISMS

    program = get_default_program()
    manifest = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "contract_version": program.contract_version,
        "registry": ASCEND_MECHANISMS.declaration_settings(),
        "world_program_identity": program.identity,
        "world_program_settings": program.settings(),
        "impl_digests_digest": get_impl_digests().digest(),
        "python": platform.python_version(),
        "platform": platform.platform(),
    }
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            capture_output=True, text=True, timeout=5,
        )
        manifest["git_commit"] = (
            result.stdout.strip() if result.returncode == 0 else None
        )
    except (OSError, subprocess.SubprocessError):
        manifest["git_commit"] = None
    return manifest


def run_checks(as_json: str) -> int:
    """执行全部判据并（可选）落盘 JSON 产物。"""
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

    if as_json:
        payload = {
            "manifest": build_manifest(),
            "passed": passed,
            "total": len(results),
            "results": [result.plain() for result in results],
        }
        Path(as_json).write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        print(f"产物已写入: {as_json}")
    return 0 if passed == len(results) else 1


# ── 生产实现变异探针（#50）────────────────────────────────────


def _mutation_observe_unquantized() -> tuple[object, object]:
    """观测映射不量化（绕过协议）：W5 必须变红。"""
    import ascend.causal as causal

    def unquantized(protocol, world, *, allow):
        return {node: world[node] for node in allow if node in world}

    return causal, ("observe", unquantized)


def _mutation_leak_detector_always_true() -> tuple[object, object]:
    """泄露检测恒真（把干净载荷也判泄露）：W5 必须变红。"""
    import ascend.causal as causal

    return causal, ("leaks_research_truth", lambda payload: True)


def _mutation_boundary_wrap() -> tuple[object, object]:
    """空间边界算子换成"周期环绕"：W3 必须变红。"""
    from ascend.causal.intervention_engine import InterventionFrameExecutor

    def wrap_neighbours(values, parent, cell, cells):
        low, high = cells[0], cells[-1]
        span = high - low + 1
        out = []
        for offset in parent.spatial_offsets:
            position = low + (cell + offset[0] - low) % span
            if position not in values:
                raise KeyError(f"帧状态缺少位置 {position}: {parent.parent}")
            out.append(values[position])
        return tuple(out)

    return InterventionFrameExecutor, ("_neighbours", staticmethod(wrap_neighbours))


def _mutation_projection_noop() -> tuple[object, object]:
    """注入核投影置空（读档丢失注入核）：W4 必须变红。"""
    from ascend.weather.weather_engine import WeatherEngine

    return WeatherEngine, ("_project_injected_features", lambda self: 0)


MUTATIONS = (
    ("观测不量化（W5）", _mutation_observe_unquantized, checks.check_w5),
    ("泄露检测恒真（W5）", _mutation_leak_detector_always_true, checks.check_w5),
    ("边界算子环绕（W3）", _mutation_boundary_wrap, checks.check_w3),
    ("注入核投影置空（W4）", _mutation_projection_noop, checks.check_w4),
)


def run_mutation() -> int:
    """生产实现变异探针：破坏一处实现，对应判据必须变红（#50）。"""
    failures: list[str] = []
    for name, mutate, check in MUTATIONS:
        target, (attr, value) = mutate()
        original = inspect.getattr_static(target, attr)
        setattr(target, attr, value)
        try:
            try:
                caught = not check().passed
            except Exception:
                caught = True  # 判据抛错也算检出（不静默放行）
        finally:
            setattr(target, attr, original)
        mark = "PASS" if caught else "FAIL"
        print(f"[{mark}] 变异探针 {name} | {'判据变红' if caught else '未被检出'}")
        if not caught:
            failures.append(name)
    print(f"\n变异汇总: {len(MUTATIONS) - len(failures)}/{len(MUTATIONS)} 被检出")
    return 0 if not failures else 1


def main() -> int:
    ap = argparse.ArgumentParser(description="世界验收 runner")
    ap.add_argument("--json", default="", help="产物输出路径（缺省只打印）")
    ap.add_argument(
        "--check", action="store_true",
        help="只巡检 Lean UnrolledDag 生成物与生产声明是否漂移",
    )
    ap.add_argument(
        "--mutation", action="store_true",
        help="只运行生产实现变异探针（破坏实现，判据必须变红）",
    )
    args = ap.parse_args()

    if args.check:
        return gen_unrolled_dag.main_check()
    if args.mutation:
        return run_mutation()
    return run_checks(args.json)


if __name__ == "__main__":
    sys.exit(main())
