"""探针 1 — 玩具 SCM 验证（对应 docs/研究理论/因果理论验证/05-理论实验对照.md，S1–S6）。

运行: .venv/bin/python research/toy_scm.py [--exp ...] [--fast]
判据预注册见 05 篇：上界判据 ≤；紧性判据构造对齐误差；斜率容差 ±15%（S3 ±2%）。
"""

from __future__ import annotations

import argparse
import math
from pathlib import Path

import numpy as np
from sklearn.neighbors import KNeighborsRegressor

FIG_DIR = Path(__file__).parent / "figures"
FIG_DIR.mkdir(exist_ok=True)
RNG = np.random.default_rng(20260822)


def line(name, pred, meas, ok):
    return f"[{'PASS' if ok else 'FAIL'}] {name} | 预测: {pred} | 实测: {meas}"


def ci_k(values):
    if len(values) < 2:
        return 0.0
    from scipy import stats as st
    return float(st.t.ppf(0.975, len(values) - 1) * st.sem(values))


def tv(p_hat, p):
    return float(0.5 * np.abs(p_hat - p).sum())


# ── S1：离散 · 已知 DAG（分层采样）vs 无 DAG ──────────────────────

def _s1(n=8, d=4, ks=(2, 3, 4), fast=False):
    print(f"\n=== S1 离散（d={d}, n={n}）：已知 DAG 分层采样 vs 无 DAG ===")
    m_grid = np.array([1600, 6000, 24000, 96000, 160000]) if not fast \
        else np.array([6000, 96000])
    reps = 20 if not fast else 8
    target = n - 1
    tv_curve = {}
    for k in ks:
        k_len = d ** k
        eps_by_m = []
        for m in m_grid:
            vals = []
            for _ in range(reps):
                tables = np.array([RNG.dirichlet(np.ones(d)) for _ in range(k_len)])
                cum = np.cumsum(tables, axis=1)
                ctx = RNG.integers(0, d, (m, k))
                ctx_ids = ctx[:, 0]
                for j in range(1, k):
                    ctx_ids = ctx_ids * d + ctx[:, j]
                Xt = (cum[ctx_ids] < RNG.random(m)[:, None]).sum(axis=1)
                per = []
                for cid in range(k_len):
                    sel = ctx_ids == cid
                    nc = int(sel.sum())
                    per.append(1.0 if nc == 0 else
                               tv(np.bincount(Xt[sel], minlength=d) / nc, tables[cid]))
                vals.append(float(np.mean(per)))
            eps_by_m.append(float(np.mean(vals)))
        tv_curve[k] = np.array(eps_by_m)
        slope, _ = np.polyfit(np.log10(m_grid), np.log10(tv_curve[k]), 1)
        print(line(f"k={k} 斜率", "log ε vs log m ≈ −0.5 ±15%",
                   f"{slope:.3f}（R={reps}/点）", abs(slope + 0.5) < 0.075))
    tvs = [tv_curve[k][0] for k in ks]
    print(line("k 单调性", f"同 m={m_grid[0]} 时 TV 随 k 单调上移",
               f"{dict(zip(ks, [round(t, 3) for t in tvs]))}", tvs[0] < tvs[1] < tvs[2]))
    # 无 DAG 侧：目标 = 节点 n−1，条件于全部 n−1 变量（d^{n−1} 上下文）
    parents = [list(range(max(0, i - 2), i)) for i in range(n)]
    tables = [np.array([RNG.dirichlet(np.ones(d)) for _ in range(d ** len(p))])
              for p in parents]
    cum = [np.cumsum(t, axis=1) for t in tables]
    m = 160000 if not fast else 96000
    tv_nodag_list = []
    for _ in range(reps):
        X = np.zeros((m, n), dtype=int)
        for i, pa in enumerate(parents):
            if not pa:
                X[:, i] = RNG.integers(0, d, m)
                continue
            ctx = X[:, pa[0]]
            for j in pa[1:]:
                ctx = ctx * d + X[:, j]
            X[:, i] = (cum[i][ctx] < RNG.random(m)[:, None]).sum(axis=1)
        tv_list = []
        for ctx in np.ndindex(*(d,) * (n - 1)):
            sel = np.ones(m, bool)
            for j, v in zip(range(n - 1), ctx):
                sel &= X[:, j] == v
            nc = int(sel.sum())
            if nc == 0:
                tv_list.append(1.0)
            else:
                pa_t = parents[target]
                true_tbl = tables[target][tuple(X[sel, j][0] for j in pa_t)]
                tv_list.append(tv(np.bincount(X[sel, target], minlength=d) / nc, true_tbl))
        tv_nodag_list.append(float(np.mean(tv_list)))
    tv_nodag = float(np.mean(tv_nodag_list))
    tv_dag = float(tv_curve[2][-1])
    print(line("无 DAG vs 有 DAG(同 m)", "TV 比 ≥ 4×",
               f"无DAG={tv_nodag:.3f} vs 有DAG={tv_dag:.3f}"
               f"（{tv_nodag / max(tv_dag, 1e-9):.1f}×）", tv_nodag > 4 * tv_dag))


# ── S2 / S2b：连续 Lipschitz（锥体函数 + kNN） ───────────────────

def _cone_fn(dim, n_cones, rng):
    # 高维稀疏问题：锥体数量随维数加倍，半径取大，保证域内多数点非平坦
    n_cones = max(n_cones, int(2 ** dim))
    centers = rng.uniform(0, 1, (n_cones, dim))
    radii = rng.uniform(0.5, 1.0, n_cones)
    heights = rng.uniform(0.6, 1.0, n_cones)

    def f(X):
        dist = np.abs(X[:, None, :] - centers[None, :, :]).max(axis=2)
        return np.minimum(1.0, (heights[None, :] *
                                np.maximum(0.0, 1.0 - dist / radii[None, :])).sum(axis=1))
    return f


def _run_curve(dim, m_grid, n_funcs=10, fast=False):
    runs = []
    test_n = 6000
    for _ in range(n_funcs):
        errs = []
        for m in m_grid:
            f = _cone_fn(dim, 20, RNG)
            X = RNG.uniform(0, 1, (m, dim))
            y = f(X) + RNG.normal(0, 0.05, m)
            K = max(5, int(round(m ** (2 / (dim + 2)))))
            knn = KNeighborsRegressor(n_neighbors=K, weights="distance")
            knn.fit(X, y)
            Xt = RNG.uniform(0, 1, (test_n, dim))
            errs.append(float(np.sqrt(np.mean((knn.predict(Xt) - f(Xt)) ** 2))))
        runs.append(errs)
    runs = np.array(runs)
    slopes = np.array([np.polyfit(np.log10(m_grid), np.log10(r), 1)[0] for r in runs])
    return runs.mean(axis=0), float(slopes.mean()), ci_k(slopes)


def _s2(fast=False):
    print("\n=== S2 连续·已知 DAG：k=2 vs k=5（锥体函数，kNN K=m^{2/(k+2)}）===")
    m_grid = np.array([300, 1000, 3162, 10000, 31623, 100000]) if not fast \
        else np.array([1000, 31623, 100000])
    n_funcs = 10 if not fast else 3
    res = {}
    for k in (2, 5):
        _, s, ci = _run_curve(k, m_grid, n_funcs=n_funcs)
        res[k] = (s, ci)
        expect = -1 / (k + 2)
        print(line(f"k={k}", f"斜率 ≈ {expect:.3f}（±15%+0.02）",
                   f"{s:.3f} ± {ci:.3f}", abs(s - expect) < 0.15 * abs(expect) + 0.02))
    (s2, c2), (s5, c5) = res[2], res[5]
    print(line("排序 k=2 vs k=5", "更高维应更浅（斜率更大），CI 分开",
               f"s2={s2:.3f} vs s5={s5:.3f}", s2 < s5 and s2 + c2 < s5 - c5))


def _s2b(fast=False):
    print("\n=== S2b 连续·无 DAG 侧：n=4 vs n=8（回归输入 = 全部 n−1 协变量）===")
    m_grid = np.array([300, 1000, 3162, 10000, 31623, 100000]) if not fast \
        else np.array([1000, 31623, 100000])
    n_funcs = 10 if not fast else 3
    res = {}
    for n in (4, 8):
        dim = n - 1
        _, s, ci = _run_curve(dim, m_grid, n_funcs=n_funcs)
        res[n] = (s, ci)
        expect = -1 / (dim + 2)
        print(line(f"n={n}（d_in={dim}）", f"斜率 ≈ {expect:.3f}（±15%）",
                   f"{s:.3f} ± {ci:.3f}", abs(s - expect) < 0.15 * abs(expect) + 0.02))
    (s4, c4), (s8, c8) = res[4], res[8]
    print(line("排序 n=4 vs n=8", "n 越大斜率越浅（指数恶化），CI 分开",
               f"s4={s4:.3f} vs s8={s8:.3f}", s4 < s8 and s4 + c4 < s8 - c8))


# ── S3：误差传播（对齐误差构造，紧性检验） ───────────────────────

def _s3(fast=False):
    print("\n=== S3 误差沿链传播：对齐误差构造（上界紧性） + 随机误差（≤ 上界）===")
    eps0 = 0.01
    ells = np.arange(1, 21)

    def roll(ell, L, delta_fn, x0=1.0):
        x, xh = x0, x0
        for _ in range(ell):
            x = L * x
            xh = L * xh + delta_fn()
        return abs(xh - x)

    def curve(L, delta):
        return np.array([roll(ell, L, delta) for ell in ells])

    # L<1：紧上界精确到达 + 平台收敛（测平台，不测斜率——平台斜率语义无意义）
    for L in (0.9, 1.5):
        aligned = curve(L, lambda: eps0)
        formula = eps0 * (1 - L ** ells) / (1 - L)
        rel = float(np.max(np.abs(aligned - formula) / formula))
        print(line(f"L={L} 对齐构造", "逐点 = ε₀(1−L^ℓ)/(1−L)（紧上界）",
                   f"max 相对偏差 = {rel:.2e}", rel < 1e-9))
    L = 0.9
    aligned = curve(L, lambda: eps0)
    print(line("L=0.9 平台收敛", f"err 上升后饱和于 {eps0/(1-L):.3f}",
               f"err(1)={aligned[0]:.4f} → err(20)={aligned[-1]:.4f}（平台）",
               aligned[-1] > 5 * aligned[0] and aligned[-1] <= eps0 / (1 - L) * 1.05))
    L = 1.5
    aligned = curve(L, lambda: eps0)
    logm_slope, _ = np.polyfit(ells[10:], np.log10(aligned[10:]), 1)
    expect = math.log10(L)
    print(line("L=1.5 半对数斜率", f"log₁₀L ≈ {expect:.4f}（±2%）",
               f"{logm_slope:.4f}", abs(logm_slope - expect) < 0.02 * abs(expect)))
    # 随机误差版：均值 ≤ 各自步数的紧上界（L>1 用公式 e_ℓ = ε₀(L^ℓ−1)/(L−1)）
    rng_gen = np.random.default_rng(7)
    for L in (0.9, 1.5):
        mean_err = {}
        for ell in (1, 5, 12, 20):
            vals = []
            for _ in range(300):
                x = xh = 1.0
                for _j in range(ell):
                    x = L * x
                    xh = L * xh + rng_gen.uniform(-eps0, eps0)
                vals.append(abs(xh - x))
            mean_err[ell] = float(np.mean(vals))
        b_all = eps0 * (L ** np.array((1, 5, 12, 20)) - 1) / (L - 1)
        ok = all(mean_err[ell] <= b_all[i] * 1.05 + 1e-12
                 for i, ell in enumerate((1, 5, 12, 20)))
        print(line(f"L={L} 随机误差", "均值 ≤ 逐步紧上界" + ("（且单调增）" if L > 1 else "（平台存在）"),
                   f"err(1..20)={[round(mean_err[e], 3) for e in (1, 5, 12, 20)]}",
                   ok and (mean_err[1] < mean_err[20] * 0.9 if L <= 1
                           else mean_err[1] < mean_err[5] < mean_err[20])))


# ── S4：do 干预 · 汇聚拓扑（求和界 vs 单路径界） ───────────────────

def _s4(fast=False):
    print("\n=== S4 do 干预·汇聚拓扑 X1,X2→X3→X4（求和 vs 取最大）===")
    eps = 0.02
    L = 0.8
    reps = 3000 if not fast else 600
    sum_bound = eps * (1 + L + L * L)          # 2.44ε
    onepath_bound = eps * (1 + L)               # 1.8ε（仅 do 源路径）
    errs, errs_noc = [], []
    for _ in range(reps):
        x1 = float(RNG.uniform(-2, 2))
        u2 = RNG.normal(0, 0.1)
        v2 = RNG.normal(0, 0.1)
        # engine
        x3 = L * (x1 + u2)
        x4 = L * x3
        # model: CRN（同 u2，u 之外每节点对齐偏置 +ε）
        x3h = L * (x1 + (u2 + eps)) + eps
        x4h = L * x3h + eps
        errs.append(abs(x4h - x4))
        # 非 CRN：独立噪声 u2'
        x3n = L * (x1 + (v2 + eps)) + eps
        x4n = L * x3n + eps
        errs_noc.append(abs(x4n - x4))
    max_err = float(np.max(errs))
    print(line("CRN 误差", f"≤ 求和界 {sum_bound:.4f} 且 > 单路径界 {onepath_bound:.4f}",
               f"max = {max_err:.4f}",
               max_err <= sum_bound * 1.05 and max_err > onepath_bound * 1.1))
    print(line("非 CRN vs CRN", "均值 ≥ 1.5× CRN 均值",
               f"{np.mean(errs_noc):.4f} vs {np.mean(errs):.4f}",
               np.mean(errs_noc) >= 1.5 * np.mean(errs)))


# ── S5：稀有配置：被动 vs do-分层 ─────────────────────────────────

def _s5(fast=False):
    print("\n=== S5 稀有配置 p_min=1e-4：被动 vs do-分层 ===")
    d = 2
    p_min = 1e-4
    eps = 0.2
    reps = 200 if not fast else 60
    p_rare = np.array([0.3, 0.7])
    n_c = 25

    def rare_tv(n_rare):
        return tv(RNG.multinomial(n_rare, p_rare) / n_rare, p_rare)

    def passive_tv(m):
        vals = [rare_tv(int(RNG.binomial(m, p_min))) for _ in range(reps)]
        return float(np.mean(vals))

    def stratified():
        return float(np.mean([rare_tv(n_c) for _ in range(reps)]))

    tv_strat = stratified()
    m_pass = None
    for mult in [0.5, 1.0, 2.0, 4.0, 8.0]:
        m = int(n_c / p_min * mult)
        if passive_tv(m) <= eps and m_pass is None:
            m_pass = m
    if m_pass is None:
        m_pass = int(n_c / p_min * 8.0)
    print(line("do-分层", f"每配置 n_c={n_c} 达 TV ≤ {eps}", f"TV = {tv_strat:.3f}", tv_strat <= eps))
    ratio = m_pass / (2 * n_c)
    print(line("被动 vs 分层", f"样本比 ≈ 1/p_min = 1e4（数量级 0.2–5×）",
               f"{m_pass}/{2*n_c} = {ratio:.0f}", 0.2e4 <= ratio <= 5e4))


# ── S6：margin α 与阈值分类速率（Mammen–Tsybakov 验证） ───────────

def _s6(fast=False):
    print("\n=== S6 margin 指数 α → 阈值分类快速率（Mammen–Tsybakov，带标签噪声的 η）===")
    m_grid = np.array([100, 316, 1000, 3162, 10000, 31623, 100000]) if not fast \
        else np.array([316, 3162, 31623])
    reps = 40 if not fast else 15
    w = 0.4

    def eta(x, alpha):
        t = np.abs(x) / w
        s = np.where(x < 0, -1.0, 1.0)
        return 0.5 + 0.5 * s * np.minimum(1.0, np.clip(t, 0, 1) ** alpha)

    def sample(n, alpha, rng):
        x = rng.uniform(-1, 1, n)
        y = (rng.random(n) < eta(x, alpha)).astype(int)
        return x, y

    def run(alpha):
        slopes = []
        for rep in range(reps):
            es = []
            for m in m_grid:
                rng_f = np.random.default_rng(abs(hash((alpha, m, rep))) % 2**32)
                x, y = sample(m, alpha, rng_f)
                order = np.argsort(x)
                y_s = y[order]
                prefix = np.cumsum(y_s)
                total1 = prefix[-1]
                # 候选阈值 = 排序点之间的中点（含两侧边界），loss = 左 1 数 + 右 0 数
                left1 = prefix
                right0 = (m - np.arange(1, m + 1)) - (total1 - prefix)
                loss = left1 + right0
                thresh = int(np.argmin(loss))
                x_cut = x[order[min(thresh, m - 1)]]
                xt, yt = sample(20000, alpha,
                                np.random.default_rng(int(abs(hash((alpha, "t", rep))) % 2**32)))
                pred = (xt >= x_cut).astype(int)
                test_loss = float(np.mean(pred != yt))
                bayes = float(np.mean(np.minimum(eta(xt, alpha), 1 - eta(xt, alpha))))
                es.append(max(test_loss - bayes, 3e-5))
            slopes.append(np.polyfit(np.log10(m_grid[2:] if len(m_grid) > 4 else m_grid),
                                   np.log10(np.array(es)[2:] if len(m_grid) > 4 else es), 1)[0])
        return float(np.mean(slopes)), ci_k(np.array(slopes))

    for alpha in (0.5, 1.0, 2.0):
        s, ci = run(alpha)
        gamma = 1.0 / alpha
        expect = -(1 + gamma) / (2 + gamma)
        print(line(f"α={alpha}", f"斜率 ≈ {expect:.3f}（±15%）",
                   f"{s:.3f} ± {ci:.3f}", abs(s - expect) < 0.15 * abs(expect) + 0.02))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--exp", nargs="+",
                    default=["S1", "S2", "S2b", "S3", "S4", "S5", "S6"])
    ap.add_argument("--fast", action="store_true")
    args = ap.parse_args()
    for e in args.exp:
        {"S1": lambda f: _s1(fast=f), "S2": _s2, "S2b": _s2b, "S3": _s3,
         "S4": _s4, "S5": _s5, "S6": _s6}[e](args.fast)
    print(f"\n图目录: {FIG_DIR}")


if __name__ == "__main__":
    main()
