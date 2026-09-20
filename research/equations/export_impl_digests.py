#!/usr/bin/env python3
"""生产声明 → 打包实现摘要表（构建期嵌入）。

打包（Nuitka，无源码）模式无法 ``inspect.getsource`` 实现函数、也无法读
内核源文件；本表在源码模式下按与运行期完全相同的算法
（``world/meta/validate.source_digest`` / ``kernel_digest``）计算每条实现与
内核的摘要，随包配送，使打包身份与源码身份逐位一致。禁用手改：表由本
脚本生成，``--check`` 为漂移门禁。
"""

from __future__ import annotations

import argparse
import difflib
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
BACKEND = ROOT / "backend"
DEFAULT_OUT = (
    BACKEND / "ascend" / "world" / "declarations" / "impl_digests.json"
)

sys.path.insert(0, str(BACKEND))

from ascend.world.meta.validate import (  # noqa: E402
    impl_key,
    kernel_source_digest,
    source_digest,
)
from ascend.world.modules import clock, terrain, weather, worldgen  # noqa: E402
from ascend.world.modules.weather import engine_inputs  # noqa: E402

#: 生产模块集（游戏进程 + 研究投影的全部实现来源）。
_MODULES = (
    clock.MODULE,
    engine_inputs.MODULE,
    weather.MODULE,
    worldgen.MODULE,
    terrain.MODULE,
)

CONTRACT_VERSION = "world-arch-v0.1"
SCHEMA_VERSION = 1


def content() -> str:
    """返回实现摘要表的稳定 JSON 文本（按键排序）。"""
    entries: dict[str, str] = {}
    count = 0
    for pack in _MODULES:
        for mechanism in pack.mechanisms:
            count += 1
            entries[impl_key(mechanism.impl)] = source_digest(mechanism.impl)
            if mechanism.accelerated is not None:
                entries[impl_key(mechanism.accelerated)] = source_digest(
                    mechanism.accelerated
                )
        for invariant in pack.invariants:
            entries[impl_key(invariant.check)] = source_digest(
                invariant.check
            )
    payload = {
        "schema_version": SCHEMA_VERSION,
        "contract_version": CONTRACT_VERSION,
        "kernel": kernel_source_digest(),
        "digests": {key: entries[key] for key in sorted(entries)},
    }
    return json.dumps(payload, ensure_ascii=False, indent=2) + "\n"


def compare(path: Path = DEFAULT_OUT) -> tuple[bool, str, str | None]:
    """比较入库摘要表与生产声明重算值。"""
    expected = content()
    if not path.exists():
        return False, f"实现摘要表不存在: {path}", None
    current = path.read_text(encoding="utf-8")
    if current == expected:
        return True, "impl_digests.json 与生产声明重算值一致", None
    diff = "\n".join(difflib.unified_diff(
        current.splitlines(),
        expected.splitlines(),
        fromfile=f"{path} (现存)",
        tofile=f"{path} (声明重算)",
        lineterm="",
    ))
    return False, "impl_digests.json 已偏离生产声明（改实现后未重生成）", diff


def check(path: Path = DEFAULT_OUT) -> tuple[bool, str]:
    """供其他验收工具调用的无输出巡检入口。"""
    ok, summary, _ = compare(path)
    return ok, summary


def main() -> int:
    parser = argparse.ArgumentParser(
        description="生产声明 -> 打包实现摘要表",
    )
    parser.add_argument("--check", action="store_true", help="仅巡检，不写文件")
    parser.add_argument("--out", default=str(DEFAULT_OUT), help="输出 JSON 路径")
    args = parser.parse_args()
    output = Path(args.out)

    if args.check:
        ok, summary, diff = compare(output)
        print(f"[{'PASS' if ok else 'FAIL'}] 实现摘要表 | {summary}")
        if diff:
            print(diff)
        if not ok:
            print("修复: python research/equations/export_impl_digests.py")
        return 0 if ok else 1

    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(content(), encoding="utf-8")
    print(f"[PASS] 生成 {output}")
    print(f"       模块 {len(_MODULES)} 个")
    return 0


if __name__ == "__main__":
    sys.exit(main())
