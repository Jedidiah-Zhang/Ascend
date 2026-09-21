#!/usr/bin/env python3
"""生成预计算表数据模块（backend/ascend/world/kernel/frozen_tables.py）。

生成物禁止手改：表内容即真值，测试锁定"表 == 入库摘要"。生成使用
标准库数学函数，因此**重新生成可能产生末位差异**——请只在明确需要
扩表/改规格时运行，并把差异一并提交（摘要随之更新）。

用法:
    .venv/bin/python research/equations/export_frozen_tables.py
"""

from __future__ import annotations

import hashlib
import json
import math
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
OUT = ROOT / "backend" / "ascend" / "world" / "kernel" / "frozen_tables.py"

BITS = 30
SCALE = 1 << BITS
COS_SEGMENTS = 1024          # cos 四分之一周期 [0, π/2]
TANH_SEGMENTS = 2048         # tanh 区间 [-8, 8]（域外饱和，误差 ≤ 2.3e-7）
TANH_MIN = -8.0
TANH_MAX = 8.0
ACOS_SEGMENTS = 16384        # acos 区间 [-1, 1]（端点导数无界，误差声明化）


def _digest(payload: dict) -> str:
    return hashlib.sha256(json.dumps(
        payload, sort_keys=True, separators=(",", ":"),
    ).encode()).hexdigest()


def build() -> str:
    pi_q = round(math.pi * SCALE)
    half_pi_q = pi_q // 2
    two_pi_q = 2 * pi_q
    cos_q = tuple(
        round(math.cos(i / COS_SEGMENTS * math.pi / 2) * SCALE)
        for i in range(COS_SEGMENTS + 1)
    )
    tanh_q = tuple(
        round(math.tanh(TANH_MIN + (TANH_MAX - TANH_MIN) * i / TANH_SEGMENTS)
              * SCALE)
        for i in range(TANH_SEGMENTS + 1)
    )
    acos_q = tuple(
        round(math.acos(-1.0 + 2.0 * i / ACOS_SEGMENTS) * SCALE)
        for i in range(ACOS_SEGMENTS + 1)
    )
    digest = _digest({
        "bits": BITS,
        "cos": {"segments": COS_SEGMENTS, "pi_q": pi_q, "table": cos_q},
        "tanh": {"segments": TANH_SEGMENTS, "min_q": round(TANH_MIN * SCALE),
                 "max_q": round(TANH_MAX * SCALE), "table": tanh_q},
        "acos": {"segments": ACOS_SEGMENTS, "table": acos_q},
    })
    lines = [
        '"""预计算表数据（生成物，禁止手改）。',
        "",
        "由 research/equations/export_frozen_tables.py 生成：",
        f"- COS 四分之一周期均匀采样（{COS_SEGMENTS} 段），Q({BITS})；",
        f"- TANH 区间 [{TANH_MIN}, {TANH_MAX}] 均匀采样（{TANH_SEGMENTS} 段），Q({BITS})；",
        f"- ACOS 区间 [-1, 1] 均匀采样（{ACOS_SEGMENTS} 段），Q({BITS})。",
        "查询路径为纯整数运算；表内容即真值（内容摘要见 TABLE_DIGEST）。",
        '"""',
        "",
        "from __future__ import annotations",
        "",
        f"TABLE_BITS: int = {BITS}",
        f"SEGMENTS: int = {COS_SEGMENTS}",
        f"PI_Q: int = {pi_q}",
        f"HALF_PI_Q: int = {half_pi_q}",
        f"TWO_PI_Q: int = {two_pi_q}",
        f"TANH_SEGMENTS: int = {TANH_SEGMENTS}",
        f"TANH_MIN_Q: int = {round(TANH_MIN * SCALE)}",
        f"TANH_MAX_Q: int = {round(TANH_MAX * SCALE)}",
        f"ACOS_SEGMENTS: int = {ACOS_SEGMENTS}",
        f'TABLE_DIGEST: str = "sha256:{digest}"',
        "",
        "COS_QUARTER_Q: tuple[int, ...] = (",
    ]
    lines += _emit(cos_q)
    lines += [")", "", "TANH_TABLE_Q: tuple[int, ...] = ("]
    lines += _emit(tanh_q)
    lines += [")", "", "ACOS_TABLE_Q: tuple[int, ...] = ("]
    lines += _emit(acos_q)
    lines += [")", ""]
    return "\n".join(lines)


def _emit(values: tuple[int, ...]) -> list[str]:
    return [
        "    " + ", ".join(str(v) for v in values[i:i + 8]) + ","
        for i in range(0, len(values), 8)
    ]


def main() -> int:
    content = build()
    OUT.write_text(content, encoding="utf-8")
    print(f"[PASS] 生成 {OUT}")
    print(f"       cos {COS_SEGMENTS + 1} / tanh {TANH_SEGMENTS + 1}"
          f" / acos {ACOS_SEGMENTS + 1} 点")
    return 0


if __name__ == "__main__":
    sys.exit(main())
