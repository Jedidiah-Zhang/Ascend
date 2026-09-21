#!/usr/bin/env python3
"""舞台目录 → zip 归档（顶层目录重命名为对外产品名）。

用法:
    python3 build/lib/make_zip.py --stage <舞台目录> --top <顶层名> --output <zip 路径>

只依赖标准库；在 Linux 与 Git Bash（Windows runner）下行为一致，
不依赖外部 zip 命令。
"""

from __future__ import annotations

import argparse
import sys
import zipfile
from pathlib import Path


def make_zip(stage: Path, top: str, output: Path) -> None:
    """把 stage 目录内容打进 zip，条目前缀为 top。"""
    output.parent.mkdir(parents=True, exist_ok=True)
    output.unlink(missing_ok=True)
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as zf:
        for path in sorted(stage.rglob("*")):
            if path.is_file():
                zf.write(path, Path(top) / path.relative_to(stage))


def main() -> int:
    """解析命令行参数并生成 zip，返回退出码（0 = 成功，1 = 舞台目录不存在）。"""
    parser = argparse.ArgumentParser(description="舞台目录 → zip 归档")
    parser.add_argument("--stage", required=True, help="舞台目录")
    parser.add_argument("--top", required=True, help="归档内顶层目录名")
    parser.add_argument("--output", required=True, help="输出 zip 路径")
    args = parser.parse_args()

    stage = Path(args.stage)
    if not stage.is_dir():
        print(f"舞台目录不存在: {stage}", file=sys.stderr)
        return 1
    make_zip(stage, args.top, Path(args.output))
    print(f"已生成: {args.output}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
