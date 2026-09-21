"""世界验收 runner — C0–C2 / W0–W7 / I0–I1 / L3 统一执行与产物落盘。

运行:
    .venv/bin/python kheker/acceptance/run_acceptance.py [--json out.json]
    .venv/bin/python kheker/acceptance/run_acceptance.py --check
    .venv/bin/python kheker/acceptance/run_acceptance.py --mutation

判据见 ``world_checks.py``；退出码 0 = 全部通过。

产物 manifest：世界程序身份、模块/内核摘要、契约版本、代码版本（git commit）
与运行环境。

``--mutation``：生产实现变异探针——分别破坏一处实现（观测不量化、记录不
校验、地址随机有序、快照身份绕过、守恒漏水），对应判据必须变红；任一未被
检出即失败。
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
ROOT = HERE.parents[1]  # 仓库根
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(ROOT))  # 供 import olam / miskhak

import gen_unrolled_dag  # noqa: E402
import world_checks  # noqa: E402


def build_manifest() -> dict:
    """实验档案身份：世界声明/程序/内核 + 代码版本 + 运行环境。"""
    from olam.kernel import TABLE_DIGEST
    from olam.meta.validate import kernel_digest

    program = world_checks._world_program()
    manifest = {
        "generated_at": datetime.now(timezone.utc).isoformat(
            timespec="seconds"
        ),
        "contract": program.contract,
        "world_program_identity": program.identity,
        "world_identity_seed0": program.world_identity(0),
        "module_digests": dict(program.module_digests),
        "kernel_digest": kernel_digest(),
        "tables_digest": TABLE_DIGEST,
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
    for check in world_checks.ALL_CHECKS:
        try:
            results.append(check())
        except Exception as exc:  # 判据自身异常 = 失败（不掩盖）
            code = check.__name__.removeprefix("check_").upper()
            results.append(world_checks.CheckResult(
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


# ── 生产实现变异探针 ────────────────────────────────────────────


def _mutation_observe_unquantized() -> tuple[object, object]:
    """观测渲染不量化（绕过协议）：W5 必须变红。"""
    from olam.protocols import observation

    def passthrough(value, *args, **kwargs):
        return value

    return observation, ("_render", passthrough)


def _mutation_record_unchecked() -> tuple[object, object]:
    """记录校验置空（坏记录也登记）：L3-历史 必须变红。"""
    from olam.protocols.records import TraceLog

    return TraceLog, ("_validate", lambda self, entry: None)


def _mutation_address_order() -> tuple[object, object]:
    """地址随机引入调用序依赖：L3-CRN 必须变红。"""
    from olam.meta import context

    original = context.address_seed
    counter = {"n": 0}

    def ordered(root, address):
        counter["n"] += 1
        return (original(root, address) + counter["n"]) % (1 << 256)

    return context, ("address_seed", ordered)


def _mutation_conservation_leak() -> tuple[object, object]:
    """流域扣减改为不减（漏水）：W6 必须变红。"""
    from dataclasses import replace

    from olam.modules import conservation

    leaky = tuple(
        replace(mechanism, impl=lambda ctx: int(ctx.parent("stock")))
        if mechanism.id == "water.basin.drain" else mechanism
        for mechanism in conservation.MODULE.mechanisms
    )
    return conservation, (
        "MODULE", replace(conservation.MODULE, mechanisms=leaky),
    )


def _mutation_event_gating() -> tuple[object, object]:
    """采集机制改为阶段模式（事件门控失效）：W7 必须变红。"""
    from dataclasses import replace

    from olam.meta.declarations import When
    from olam.modules import harvest

    broken = tuple(
        replace(mechanism, when=When("phase", "hold"))
        if mechanism.id == "harvest.gather" else mechanism
        for mechanism in harvest.MODULE.mechanisms
    )
    return harvest, (
        "MODULE", replace(harvest.MODULE, mechanisms=broken),
    )


def _mutation_identity_constant() -> tuple[object, object]:
    """快照身份恒等（绕过校验）：W4 必须变红。"""
    from olam.compile import WorldProgram

    return WorldProgram, (
        "world_identity", lambda self, seed=None: "sha256:constant",
    )


MUTATIONS = (
    ("观测不量化（W5）", _mutation_observe_unquantized,
     world_checks.check_w5),
    ("记录不校验（L3-历史）", _mutation_record_unchecked,
     world_checks.check_l3_history),
    ("地址随机有序（L3-CRN）", _mutation_address_order,
     world_checks.check_l3_crn),
    ("快照身份绕过（W4）", _mutation_identity_constant,
     world_checks.check_w4),
    ("守恒漏水（W6）", _mutation_conservation_leak,
     world_checks.check_w6),
    ("事件门控失效（W7）", _mutation_event_gating,
     world_checks.check_w7),
)


def run_mutation() -> int:
    """生产实现变异探针：破坏一处实现，对应判据必须变红。"""
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
        print(f"[{mark}] 变异探针 {name} | "
              f"{'判据变红' if caught else '未被检出'}")
        if not caught:
            failures.append(name)
    print(f"\n变异汇总: {len(MUTATIONS) - len(failures)}/{len(MUTATIONS)} 被检出")
    return 0 if not failures else 1


def main() -> int:
    """按命令行开关运行判据、变异探针或生成物巡检；返回退出码。"""
    ap = argparse.ArgumentParser(description="世界验收 runner（C0–C2 / W0–W7 / I0–I1 / L3）")
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
