#!/usr/bin/env python3
"""生产声明 → Lean UnrolledDag 实例生成器（issue #46 P5）。

把**真实生产声明**（`ASCEND_MECHANISMS`）实例化为
`AscendLean.CausalVerification.UnrolledDag.Decl`，并生成：

  数据段 —— 微步序索引、节点索引、每个节点的父模板表（父节点索引 + 滞后 +
            父阶段）；
  定理段 —— `wellFormed_real`：生产声明满足 `WellFormed`（机器可判）；
            `unroll_acyclic_real`：由 `unroll_acyclic` 得到时间展开无环。

为什么需要它：`UnrolledDag.lean` 证明的是"任何合法声明展开无环"，但**从未
与真实声明对上**。本生成器把"生产声明合法"也变成机器可判的命题，C2 于是
同时具备理论内核与实例见证。

运行:
  .venv/bin/python research/acceptance/gen_unrolled_dag.py            # 生成/刷新
  .venv/bin/python research/acceptance/gen_unrolled_dag.py --check    # 巡检

退出码: 0 一致/成功; 1 漂移或生成物缺失; 2 来源读取错误。
"""

from __future__ import annotations

import argparse
import difflib
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent            # research/acceptance
ROOT = HERE.parents[1]                            # 仓库根
sys.path.insert(0, str(ROOT / "backend"))

OUT_PATH = (ROOT / "research" / "lean" / "AscendLean" / "CausalVerification"
            / "GenUnrolledDag.lean")


def _load_registry():
    from ascend.causal.world import ASCEND_MECHANISMS
    return ASCEND_MECHANISMS


def _sanitize(name: str) -> str:
    """节点 ID → Lean 标识符片段。"""
    out = []
    for char in name:
        out.append(char if char.isalnum() else "_")
    return "".join(out)


def _lag_bound() -> int:
    """滞后上界 K：实际用到的最大滞后 + 1（至少 2）。"""
    registry = _load_registry()
    max_lag = 0
    for mechanism in registry.mechanisms.values():
        for parent in mechanism.parents:
            max_lag = max(max_lag, parent.lag)
    return max(2, max_lag + 1)


def _render() -> str:
    registry = _load_registry()
    lag_count = _lag_bound()
    order = list(registry.microstep_order)
    steps = {name: index for index, name in enumerate(order)}
    nodes = sorted(registry.nodes)
    node_index = {node_id: index for index, node_id in enumerate(nodes)}

    # 每个节点的父模板（父节点索引, 滞后, 父阶段）；无写者节点为空
    by_output = {}
    for mechanism in registry.mechanisms.values():
        by_output[mechanism.output] = mechanism

    lines: list[str] = []
    lines.append("import AscendLean.CausalVerification.UnrolledDag")
    lines.append("")
    lines.append("/-! AUTO-GENERATED — 本文件由工具生成，禁止手改。")
    lines.append("")
    lines.append("生成器：research/acceptance/gen_unrolled_dag.py（issue #46 P5）")
    lines.append("生成命令：.venv/bin/python research/acceptance/gen_unrolled_dag.py")
    lines.append("巡检命令：.venv/bin/python research/acceptance/gen_unrolled_dag.py --check")
    lines.append("")
    lines.append("来源：backend/ascend/causal/world.py 的 ASCEND_MECHANISMS 生产声明")
    lines.append(f"规模：{len(nodes)} 节点 / {len(registry.mechanisms)} 机制 / "
                 f"{sum(len(m.parents) for m in registry.mechanisms.values())} 条父引用")
    lines.append("")
    lines.append("角色边界：本文件只把声明**数据**实例化为 UnrolledDag.Decl 并给出")
    lines.append("`WellFormed` 的机器可判证明；无环定理本身在 UnrolledDag.lean。 -/")
    lines.append("")
    lines.append("namespace AscendLean.GenUnrolledDag")
    lines.append("")
    lines.append("open AscendLean.CausalVerification")
    lines.append("")
    lines.append("-- ═══ 第一节 规模常量 ═══")
    lines.append("")
    lines.append(f"/-- 分量模板数（生产声明节点数）。 -/")
    lines.append(f"def nodeCount : ℕ := {len(nodes)}")
    lines.append("")
    lines.append("/-- 更新阶段数（微步序长度）。 -/")
    lines.append(f"def stepCount : ℕ := {len(order)}")
    lines.append("")
    lines.append("/-- 滞后上界（实际用到的最大滞后 + 1）。 -/")
    lines.append(f"def lagCount : ℕ := {lag_count}")
    lines.append("")
    lines.append("/-- 本文件使用的父模板结构别名（绑定生产声明的规模）。 -/")
    lines.append("abbrev PSpec : Type :=")
    lines.append("  AscendLean.CausalVerification.ParentSpec nodeCount stepCount lagCount")
    lines.append("")
    lines.append("-- ═══ 第二节 生产声明的父模板表（有限索引，可计算）═══")
    lines.append("")
    lines.append("/-- 分量模板索引按节点 ID 排序（与注册表 `sorted(nodes)` 同序）。 -/")
    for index, node_id in enumerate(nodes):
        lines.append(f"def node{_sanitize(node_id)} : ℕ := {index}")
    lines.append("")
    lines.append("/-- 父模板表：分量索引 → 阶段 → 父模板列表。 -/")
    lines.append("def realParents : ℕ → ℕ → List PSpec := fun v r =>")
    lines.append("  match v, r with")
    for node_id in nodes:
        spec = registry.nodes[node_id]
        stage = steps[spec.update.microstep]
        mechanism = by_output.get(node_id)
        parents = mechanism.parents if mechanism is not None else ()
        rendered = ", ".join(
            "({ par := ⟨%d, by decide⟩, lag := ⟨%d, by decide⟩,"
            " pr := ⟨%d, by decide⟩ } : PSpec)" % (
                node_index[parent.parent],
                parent.lag,
                steps[parent.source_microstep],
            )
            for parent in parents
        )
        body = "[" + rendered + "]" if rendered else "([] : List PSpec)"
        lines.append(f"  | {node_index[node_id]}, {stage} => {body}")
    lines.append("  | _, _ => []")
    lines.append("")
    lines.append("/-- **生产声明**（有限索引：`Fin nodeCount` × `Fin stepCount`）。 -/")
    lines.append("def realDecl : Decl nodeCount stepCount lagCount := fun v r =>")
    lines.append("  realParents v.val r.val")
    lines.append("")
    lines.append("-- ═══ 第三节 机器可判的合法性证明 ═══")
    lines.append("")
    lines.append("/-- **生产声明合法**（判定式在有界索引上穷尽枚举）。 -/")
    lines.append("theorem wellFormed_real : WellFormed realDecl :=")
    lines.append("  wellFormed_of_check (by native_decide)")
    lines.append("")
    lines.append("/-- **生产声明的时间展开图无环**（C2 的实例见证）。 -/")
    lines.append("theorem unroll_acyclic_real : Acyclic (UnrollEdge lagCount realDecl) :=")
    lines.append("  unroll_acyclic wellFormed_real")
    lines.append("")
    lines.append("end AscendLean.GenUnrolledDag")
    lines.append("")
    return "\n".join(lines)


def _write(text: str) -> None:
    OUT_PATH.write_text(text, encoding="utf-8")


def main_check() -> int:
    """巡检模式入口（供验收 runner --check 复用）。"""
    return main(["--check"])


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="生成 UnrolledDag 生产实例")
    ap.add_argument("--check", action="store_true", help="只巡检不写入")
    args = ap.parse_args(argv)
    try:
        text = _render()
    except Exception as exc:  # 来源读取/数据错误
        print(f"[FAIL] 生成失败: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2
    if args.check:
        if not OUT_PATH.is_file():
            print(f"[FAIL] 生成物缺失: {OUT_PATH}")
            return 1
        current = OUT_PATH.read_text(encoding="utf-8")
        if current != text:
            diff = "\n".join(difflib.unified_diff(
                current.splitlines(), text.splitlines(),
                fromfile="existing", tofile="regenerated", lineterm="",
            ))
            print("[FAIL] 生产声明与 UnrolledDag 实例漂移：")
            print(diff[:4000])
            return 1
        print("[PASS] UnrolledDag 实例 | 与生产声明一致")
        return 0
    _write(text)
    print(f"[PASS] 已生成 {OUT_PATH}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
