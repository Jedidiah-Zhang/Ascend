#!/usr/bin/env python3
"""从生产机制注册表生成实现内容摘要表（构建期嵌入，issue #49）。

打包（Nuitka，无源码）模式无法读取方程源码；本表在源码模式下按与运行期
完全相同的算法计算每个机制的 ``equation_version``，随包配送，使打包身份
与源码身份逐位一致。禁用手改：表由本脚本生成，``--check`` 为漂移门禁。
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
    BACKEND / "ascend" / "causal" / "declarations" / "impl_digests.json"
)

sys.path.insert(0, str(BACKEND))

from ascend.causal.registry import _sourceless  # noqa: E402
from ascend.causal.world import ASCEND_MECHANISMS  # noqa: E402

CONTRACT_VERSION = "v0.1"
SCHEMA_VERSION = 1


def content() -> str:
    """返回实现摘要表的稳定 JSON 文本（按 mechanism_id 排序）。"""
    if _sourceless():
        raise RuntimeError(
            "实现摘要表必须在源码模式生成（当前为打包/无源码模式）"
        )
    entries = []
    for mechanism in sorted(
        ASCEND_MECHANISMS.mechanisms.values(),
        key=lambda spec: spec.mechanism_id,
    ):
        entries.append({
            "mechanism_id": mechanism.mechanism_id,
            "output": mechanism.output,
            "equation_version": ASCEND_MECHANISMS.equation_version(
                mechanism.output
            ),
        })
    payload = {
        "schema_version": SCHEMA_VERSION,
        "contract_version": CONTRACT_VERSION,
        "digests": entries,
    }
    return json.dumps(payload, ensure_ascii=False, indent=2) + "\n"


def compare(path: Path = DEFAULT_OUT) -> tuple[bool, str, str | None]:
    """比较入库摘要表与生产注册表重算值。"""
    expected = content()
    if not path.exists():
        return False, f"实现摘要表不存在: {path}", None
    current = path.read_text(encoding="utf-8")
    if current == expected:
        return True, "impl_digests.json 与生产注册表重算值一致", None
    diff = "\n".join(difflib.unified_diff(
        current.splitlines(),
        expected.splitlines(),
        fromfile=f"{path} (现存)",
        tofile=f"{path} (注册表重算)",
        lineterm="",
    ))
    return False, "impl_digests.json 已偏离生产注册表（改实现后未重生成）", diff


def check(path: Path = DEFAULT_OUT) -> tuple[bool, str]:
    """供其他验收工具调用的无输出巡检入口。"""
    ok, summary, _ = compare(path)
    return ok, summary


def main() -> int:
    parser = argparse.ArgumentParser(
        description="生产机制注册表 -> 实现内容摘要表",
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
    print(f"       机制 {len(ASCEND_MECHANISMS.mechanisms)} 条")
    return 0


if __name__ == "__main__":
    sys.exit(main())
