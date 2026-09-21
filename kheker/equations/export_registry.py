#!/usr/bin/env python3
"""从世界声明生成研究声明快照（事实源 = WorldProgram）。"""

from __future__ import annotations

import argparse
import difflib
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
BACKEND = ROOT
DEFAULT_OUT = HERE / "equations.json"

sys.path.insert(0, str(BACKEND))
sys.path.insert(0, str(ROOT))  # 供 import olam / miskhak
sys.path.insert(0, str(HERE))

import export_world  # noqa: E402


def content() -> str:
    """返回世界声明的稳定 JSON 快照（世界声明投影）。"""
    return export_world.to_json()


def compare(path: Path = DEFAULT_OUT) -> tuple[bool, str, str | None]:
    """比较入库快照与生产声明投影。"""
    expected = content()
    if not path.exists():
        return False, f"声明快照不存在: {path}", None
    current = path.read_text(encoding="utf-8")
    if current == expected:
        return True, "equations.json 与生产世界声明一致", None
    diff = "\n".join(difflib.unified_diff(
        current.splitlines(),
        expected.splitlines(),
        fromfile=f"{path} (现存)",
        tofile=f"{path} (声明投影)",
        lineterm="",
    ))
    return False, "equations.json 已偏离生产世界声明", diff


def check(path: Path = DEFAULT_OUT) -> tuple[bool, str]:
    """供其他验收工具调用的无输出巡检入口。"""
    ok, summary, _ = compare(path)
    return ok, summary


def main() -> int:
    parser = argparse.ArgumentParser(description="世界声明 -> 研究快照")
    parser.add_argument("--check", action="store_true", help="仅巡检，不写文件")
    parser.add_argument("--out", default=str(DEFAULT_OUT), help="输出 JSON 路径")
    args = parser.parse_args()
    output = Path(args.out)

    if args.check:
        ok, summary, diff = compare(output)
        print(f"[{'PASS' if ok else 'FAIL'}] 声明快照 | {summary}")
        if diff:
            print(diff)
        if not ok:
            print("修复: python kheker/equations/export_registry.py")
        return 0 if ok else 1

    output.write_text(content(), encoding="utf-8")
    declaration = export_world.project(
        export_world.build_program()
    )["declaration"]
    print(f"[PASS] 生成 {output}")
    print(f"       声明 {declaration['id']}@{declaration['version']}")
    print(f"       摘要 {declaration['hash']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
