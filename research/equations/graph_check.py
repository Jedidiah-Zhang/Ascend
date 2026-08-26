"""声明图健康巡检 — 因果图级设计判据（L2 层）。

运行: .venv/bin/python research/equations/graph_check.py [--json PATH]

与 verify_equations.py（L1 自洽性）互补：本工具回答"设计是否合理"，
判据全部来自已形式化的定理（02 篇 + Lean 证书）：
  - 推论 2.2/2.3（Contraction.lean）：环收缩性、收缩/发散两律
  - 定理 2.5（DagPathExpansion.lean / ExplicitPaths.lean）：路径权重和
    W(u,t) = Σ_{u→t 路径} Π L，反事实误差上界 ε_t + Σ_u ε_u·W(u,t)
  - S4 探针（06 篇）：多父节点必须按"求和"而非取最大

判据（预注册，05 篇总则风格；阈值先定后跑，后续按实测校准）：
  G0 声明加载 + 结构校验：schema.validate 无问题；
  G1 反馈环收缩：全图任意环上 L 乘积 < 1（推论 2.2 收敛）；≥1 ⟹ FAIL
     （推论 2.3：=1 线性累积、>1 指数发散）；
  G2 路径权重上界：max_u,t W(u,t) ≤ 8（预注册；链长 ≤3、单边 L≤2 的
     几何和上界，超出则误差放大多级，需 ε 补偿或收缩边兜底）；
  G3 反事实误差界：对每个有值域（bounds）的目标 t，Σ_u ε_u·W(u,t)
     ≤ 5% × 值域宽度（预注册；量纲归一——不同变量单位不同，不能用
     全局标量阈值；需声明 variables[*].eps，未声明 ⟹ 缺口报告）；
     无 bounds 的目标（外生根）界≡ε 自身，不做判定；
  G4 遗忘深度：θ=0.01，报告"权重衰减到 θ 以下所需深度 vs 实际路径
     长度"——收缩快的路径上深层上游误差可忽略（ε 可放宽，推论 2.2 记忆
     衰减的直接推论；报告型，无 PASS/FAIL）；
  G5 放大边定位：L>1 的边全部列出 + 以其为源的最大路径权重（报告型）；
  G6 汇聚节点：入度 ≥2 的节点列出（求和语义节点，定理 2.5/S4；报告型）。

缺口报告（非判据，但阻止 L2 判定完整）：
  - variables[*].eps 未声明：定理 2.5 的上界输入缺失，G3 无法计算。
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parents[1] / "backend"))

import schema  # noqa: E402

from ascend.world_tree.root import VariableGraph  # noqa: E402

JSON_PATH = HERE / "equations.json"

W_MAX = 8.0          # G2 预注册阈值
REL_CTF = 0.05       # G3 相对界：Σ ε_u·W(u,t) ≤ 5% × 值域宽度
THETA = 0.01         # G4 遗忘阈值


def line(name: str, ok: bool, detail: str = "") -> str:
    return f"[{'PASS' if ok else 'FAIL'}] {name} | {detail}"


def find_cycle_products(
    adj: dict[str, list[tuple[str, float]]],
) -> list[tuple[list[str], float]]:
    """DFS 找环并计算环上 L 乘积（推论 2.2/2.3 判据）。"""
    cycles: list[tuple[list[str], float]] = []
    color: dict[str, int] = {}
    stack: list[str] = []

    def dfs(v: str) -> None:
        color[v] = 1
        stack.append(v)
        for (w, _) in adj.get(v, []):
            if color.get(w, 0) == 0:
                dfs(w)
            elif color.get(w, 0) == 1:
                i = stack.index(w)
                cyc = stack[i:] + [w]
                prod = 1.0
                for (a, b) in zip(cyc, cyc[1:]):
                    for (x, lx) in adj[a]:
                        if x == b:
                            prod *= lx
                cycles.append((cyc, prod))
        stack.pop()
        color[v] = 2

    for v in adj:
        if color.get(v, 0) == 0:
            dfs(v)
    return cycles


def path_weights(
    graph: VariableGraph,
) -> tuple[dict[tuple[str, str], float], dict[tuple[str, str], int]]:
    """DAG 拓扑序 DP：W(u,t) = Σ_{u→t 路径} Π L 与最长路径深度。

    多父求和（S4/定理 2.5 语义）；L=0 边贡献 0（离散边不放大误差）。
    """
    order = graph.toposort()
    nodes = list(graph.variables)
    w: dict[tuple[str, str], float] = {(u, u): 1.0 for u in nodes}
    depth: dict[tuple[str, str], int] = {(u, u): 0 for u in nodes}
    for t in order:
        in_edges = [(p, es.L) for (p, c, es) in graph.edges() if c == t]
        for (p, l) in in_edges:
            for (k, wup) in [(k, v) for (k, v) in w.items() if k[1] == p]:
                u = k[0]
                w[(u, t)] = w.get((u, t), 0.0) + wup * l
                depth[(u, t)] = max(depth.get((u, t), 0),
                                    depth.get((u, p), 0) + 1)
    return w, depth


def main() -> int:
    ap = argparse.ArgumentParser(description="声明图健康巡检（L2 设计判据）")
    ap.add_argument("--json", default=str(JSON_PATH),
                    help="声明 JSON 路径")
    args = ap.parse_args()

    results: list[tuple[str, bool, str]] = []
    gaps: list[str] = []

    # ── G0 加载 + 结构校验 ────────────────────────────
    data = json.loads(Path(args.json).read_text(encoding="utf-8"))
    graph = schema.load_declaration(args.json)
    issues = schema.validate(graph)
    ok = not issues
    results.append(("G0 声明加载 + 结构校验",
                    ok, "无问题" if ok else "; ".join(issues)))
    if not ok:
        for name, ok_, detail in results:
            print(line(name, ok_, detail))
        print("\n汇总: 0/1 通过（结构校验失败，后续判据无法运行）")
        return 1

    nodes = graph.variables
    adj_all: dict[str, list[tuple[str, float]]] = {n: [] for n in nodes}
    for (p, c, es) in graph.edges():
        adj_all[p].append((c, es.L))
    results.append(("G0 图规模", True,
                    f"{len(nodes)} 节点 / {len(graph.edges())} 边 / "
                    f"拓扑序 {' -> '.join(graph.toposort())}"))

    # ── G1 反馈环收缩（推论 2.2/2.3）──────────────────
    cycles = find_cycle_products(adj_all)
    if not cycles:
        results.append(("G1 反馈环收缩", True, "无环（DAG）— 误差不沿环传播"))
    else:
        bad = [(c, p) for (c, p) in cycles if p >= 1.0]
        results.append((
            "G1 反馈环收缩",
            not bad,
            "; ".join(f"{'→'.join(c)} 乘积 {p:.3g}"
                      + ("(≥1, 推论 2.3 不收敛)" if p >= 1.0 else "")
                      for c, p in cycles)
            or "无环",
        ))

    # ── 路径权重（G2–G5 共用）────────────────────────
    acyclic = not cycles
    if acyclic:
        w, depth = path_weights(graph)
    else:
        w, depth = {}, {}
        for name in ("G2 路径权重上界", "G3 反事实误差界",
                     "G4 遗忘深度", "G5 放大边定位"):
            results.append((name, False, "存在环，路径权重不可计算"))

    if acyclic:
        # ── G2 路径权重上界 ─────────────────────────
        real = [(k, v) for (k, v) in w.items() if k[0] != k[1]]
        if not real:
            results.append(("G2 路径权重上界", True, "无 u≠t 路径"))
        else:
            top = sorted(real, key=lambda kv: kv[1], reverse=True)[:5]
            max_key, max_w = top[0]
            u, t = max_key
            top_txt = ", ".join(
                f"W({u0}→{t0})={v:.3g}" for (u0, t0), v in top)
            results.append((
                "G2 路径权重上界",
                max_w <= W_MAX,
                f"max W({u}→{t}) = {max_w:.3g}（阈值 {W_MAX}）；top5: {top_txt}"))

        # ── G3 反事实误差界（需 eps 声明）────────────
        eps_map = {n: graph.get_variable(n).eps for n in nodes}
        missing_eps = sorted(n for n, e in eps_map.items() if e is None)
        if missing_eps:
            gaps.append(
                f"variables[*].eps 未声明: {missing_eps} — 定理 2.5 输入"
                "缺失，G3 无法计算（ε_i 与 L_ij 成对才可算反事实界）")
            results.append(("G3 反事实误差界", False,
                            f"{len(missing_eps)} 个变量缺 eps，跳过"))
        else:
            worst = 0.0
            worst_t = None
            checked = 0
            for t in nodes:
                spec = graph.get_variable(t)
                if spec.bounds is None:
                    continue
                bound = sum(float(eps_map[u]) * val
                            for (u, tt), val in w.items() if tt == t)
                rel = REL_CTF * (spec.bounds[1] - spec.bounds[0])
                checked += 1
                if bound > worst:
                    worst, worst_t = bound, t
                if bound > rel:
                    results.append((
                        "G3 反事实误差界",
                        False,
                        f"{t}: Σ ε_u·W(u,·) = {bound:.3g} > 5%×范围 {rel:.3g}"))
            if not any(name.startswith("G3") and not ok_
                       for name, ok_, _ in results):
                results.append((
                    "G3 反事实误差界",
                    True,
                    f"全部 {checked} 个有界目标 ≤ 5%×范围；"
                    f"最紧 @{worst_t} = {worst:.3g}"))

        # ── G4 遗忘深度（报告型）────────────────────
        forget = []
        for (u, t), val in w.items():
            if u != t and val > 0:
                dpt = depth[(u, t)]
                lam = val ** (1.0 / dpt) if dpt else 1.0
                need = (0.0 if lam >= 1
                        else math.log(THETA) / math.log(lam))
                forget.append((u, t, val, dpt, lam, need))
        forget.sort(key=lambda x: x[2], reverse=True)
        shallow = [f for f in forget if f[2] < THETA]
        txt = "; ".join(
            f"{u}→{t}: W={val:.3g} 深{dpt} 均L={lam:.3f} "
            f"遗忘需{('∞' if lam >= 1 else f'{need:.1f}')}层"
            for u, t, val, dpt, lam, need in forget[:6])
        results.append((
            "G4 遗忘深度", True,
            f"{len(forget)} 条路径；{len(shallow)} 条已衰减<θ(={THETA})。"
            + (txt or "无路径")))

        # ── G5 放大边定位（报告型）──────────────────
        amps = [(p, c, es.L) for (p, c, es) in graph.edges() if es.L > 1.0]
        if amps:
            info = []
            for (p, c, l) in amps:
                down = [(k, v) for (k, v) in w.items()
                        if k[0] == c and k[1] != c]
                max_d = max((v for _, v in down), default=0.0)
                info.append(f"{p}→{c} L={l:.3g}"
                            + (f"（下游最大路径权重 {max_d:.3g}）" if down
                               else "（无下游路径）"))
            results.append(("G5 放大边定位", True, "; ".join(info)))
        else:
            results.append(("G5 放大边定位", True, "无 L>1 边"))

        # ── G6 汇聚节点（报告型）────────────────────
        indeg: dict[str, list[tuple[str, float]]] = {}
        for (p, c, es) in graph.edges():
            indeg.setdefault(c, []).append((p, es.L))
        sinks = [(c, ps) for (c, ps) in indeg.items() if len(ps) >= 2]
        if sinks:
            txt = "; ".join(
                f"{c} ← {'+'.join(p for p, _ in ps)}（L={[l for _, l in ps]}）"
                for c, ps in sinks)
            results.append((
                "G6 汇聚节点", True,
                f"{len(sinks)} 个多父节点，须按求和语义（定理 2.5/S4）: {txt}"))
        else:
            results.append(("G6 汇聚节点", True, "无多父节点"))

    # ── 汇总 ─────────────────────────────────────────
    passed = sum(1 for _, ok_, _ in results if ok_)
    for name, ok_, detail in results:
        print(line(name, ok_, detail))
    if gaps:
        print("\n── 声明缺口（阻止 L2 判定完整）──")
        for g in gaps:
            print("  ⚠ " + g)
    print(f"\n汇总: {passed}/{len(results)} 通过"
          + (f" + {len(gaps)} 缺口" if gaps else ""))
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())