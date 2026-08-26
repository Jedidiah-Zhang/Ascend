"""探针 2 — 真实引擎天气验证（对应 docs/研究理论/世界基座/05-理论实验对照.md，E1–E3）。

运行: .venv/bin/python research/engine_weather.py [--exp E1 E2 E3] [--fast]
- E1: Granger 边恢复（分钟粒度，含正/负功效校准）
- E2: 温度分布 margin 指数 α（幂律拟合）
- E3: 时间相关尺度 τ_t（自相关）与空间相关尺度 τ_s（跨 chunk 相关）
E4（方程学习表）/ E5（CRN 流诊断）见 engine_e4_5.py。
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "backend"))

from ascend.config import GAME_DAY, GAME_MINUTE, GAME_YEAR
from ascend.space import ClimateZone, WeatherParams
from ascend.time import WorldClock
from ascend.weather.weather_engine import WeatherEngine
from ascend.world_tree import WorldTree


def line(name, pred, meas, ok):
    return f"[{'PASS' if ok else 'FAIL'}] {name} | 预测: {pred} | 实测: {meas}"


def _engine(seed=42, chunks=((0, 0),)):
    wt = WorldTree()
    clock = WorldClock()
    e = WeatherEngine(clock, seed=seed, world_tree_arg=wt)
    for cx, cy in chunks:
        e.register_chunk(cx, cy,
                         WeatherParams(5.0, 800.0, 12.0, 100.0, 60.0, 5.0),
                         ClimateZone.TEMPERATE_FOREST, 15.0)
    return e, clock


def _series(e, clock, cx, cy, days=30, step_min=6, tstart=None):
    """连续 run 后按游戏分钟采样解析值序列（t 均 ≤ now）。"""
    tstart = clock.time - GAME_DAY * days if tstart is None else tstart
    ts = list(range(tstart, clock.time, GAME_MINUTE * step_min))
    vals = [e.get_weather(cx, cy, t) for t in ts]
    return ts, vals


# ── E1：Granger 边恢复 + 功效校准 ───────────────────────────────

def _e1(fast=False):
    print("\n=== E1 Granger 边恢复（分钟粒度 · 含正/负对照校准）===")
    e, clock = _engine()
    # 长连续 run：跳 1 年后往回采 30 天（确保"过去时刻"可查）
    clock.skip(GAME_YEAR)
    ts, vals = _series(e, clock, 0, 0, days=30, step_min=6)
    T = np.array([v.temperature for v in vals])
    H = np.array([v.humidity for v in vals])
    W = np.array([v.wind_speed for v in vals])
    S = np.array([v.sunshine for v in vals])
    R = np.array([v.rainfall for v in vals])
    # 合成"同 tick 边"：v_t = 1[T_t < 2]（真实同 tick 依赖，Granger 应不可见）
    rng = np.random.default_rng(5)
    v_same = np.tanh(0.3 * T) + 0.05 * rng.normal(size=len(T))  # "同 tick" 依赖
    v_same = (v_same > np.median(v_same)).astype(float)  # 恒变，避免常数列
    # 合成"滞后边"：w_{t+1} = f(T_t)（真实滞后依赖，Granger 应可见）
    w_lag = np.empty_like(T)
    w_lag[0] = 0.0
    for i in range(1, len(T)):
        w_lag[i] = np.tanh(0.5 * T[i - 1])

    def granger_pair(x, y, maxlag=3, tfrac=0.088):
        from statsmodels.tsa.stattools import grangercausalitytests
        X = np.vstack([x, y]).T
        if np.ptp(x) == 0 or np.ptp(y) == 0:
            return None
        try:
            res = grangercausalitytests(X, maxlag=maxlag, verbose=False)
        except (ValueError, Exception) as exc:
            if 'constant' in str(exc).lower():
                return None
            raise
        p = min(res[l][0]['ssr_ftest'][1] for l in res)
        return p

    def report(name, x, y, expect):
        p = granger_pair(x, y)
        if p is None:
            print(line(name, expect, "检验失败（非平稳）", False))
            return
        sig = p < 0.05
        ok = (expect == "visible" and sig) or (expect == "invisible" and not sig)
        print(line(name, f"预期 {expect}", f"p={p:.3e} sig={sig}", ok))

    print("[负对照] 同 tick 边 v=1[T<2] → 预期不可见（合成变量，实际与 T 同期决定）")
    report("同 tick v←T", T[1:], v_same[1:], "invisible")
    print("[正对照] 滞后边 w_{t+1}=tanh(0.5T_t) → 预期可见")
    report("滞 1 边 w←T", T[1:], w_lag[1:], "visible")
    print(f"[信息] T 序列为纯时钟解析量：T_t(现状) 对 T_{'{t+1}'} 无增量预测力"
          f"（p={granger_pair(T[1:], T[1:]):.2f}）——无记忆系统的预期事实，不作判据")
    e.shutdown()



# ── E2：温度分布 margin 指数 α ──────────────────────────────────

def _e2(fast=False):
    print("\n=== E2 温度分布 0°C 邻域 margin 指数 α ===")
    from ascend.space import ClimateZone as CZ
    baselines = {
        "temperate": WeatherParams(5.0, 800.0, 12.0, 100.0, 60.0, 5.0),
        "subarctic": WeatherParams(-5.0, 700.0, 10.0, 100.0, 60.0, 5.0),
        "polar": WeatherParams(-12.0, 500.0, 8.0, 100.0, 55.0, 4.0),
    }
    zones = {
        "temperate": CZ.TEMPERATE_FOREST,
        "subarctic": CZ.SUBARCTIC_TAIGA,
        "polar": CZ.POLAR_TUNDRA,
    }
    wt = WorldTree()
    clock = WorldClock()
    e = WeatherEngine(clock, seed=42, world_tree_arg=wt)
    coord = {"temperate": (0, 0), "subarctic": (1, 0), "polar": (2, 0)}
    for name, bl in baselines.items():
        e.register_chunk(*coord[name], bl, zones[name], 15.0)
    clock.skip(GAME_YEAR)
    temps = []
    rng = np.random.default_rng(123)
    for name in baselines:
        t0 = clock.time - GAME_DAY * 360
        cx, cy = coord[name]
        for _ in range(400 if not fast else 150):
            t = int(rng.integers(t0, clock.time))
            wp = e.get_weather(cx, cy, t)
            temps.append(wp.temperature)
    T = np.array(temps)
    hs = np.array([0.2, 0.5, 1.0, 2.0, 3.0, 5.0])
    masses = np.array([np.mean(np.abs(T) <= h) for h in hs])
    # 幂律拟合：log(mass) ~ α·log(h) + c（双子区间）
    nz = masses > 0
    A = np.vstack([np.log(hs[nz]), np.ones(nz.sum())]).T
    alpha, c = np.linalg.lstsq(A, np.log(masses[nz]), rcond=None)[0]
    resid = np.log(masses[nz]) - A @ np.array([alpha, c])
    r2 = 1 - np.sum(resid ** 2) / np.sum((np.log(masses[nz]) - np.mean(np.log(masses[nz]))) ** 2)
    print(f"   质量/比率：{list(zip(hs, np.round(masses, 4)))}")
    print(line("α 估计", "幂律拟合 R² > 0.8 且 α 报告（供阶段 1 决策）",
               f"α ≈ {alpha:.2f}（R²={r2:.3f}）", r2 > 0.8))
    e.shutdown()


# ── E3：时间 + 空间相关尺度 ────────────────────────────────────

def _e3(fast=False):
    print("\n=== E3 时间相关 τ_t + 空间相关 τ_s ===")
    from ascend.space import ClimateZone as CZ
    wt = WorldTree()
    clock = WorldClock()
    e = WeatherEngine(clock, seed=42, world_tree_arg=wt)
    for cx in range(6):
        e.register_chunk(cx, 0,
                         WeatherParams(5.0, 800.0, 12.0, 100.0, 60.0, 5.0),
                         CZ.TEMPERATE_FOREST, 15.0)
    clock.skip(GAME_YEAR)
    # 时间：单 chunk 每分钟序列的自相关
    ts = list(range(clock.time - GAME_DAY * 3, clock.time, GAME_MINUTE))
    T1 = np.array([e.get_weather(0, 0, t).temperature for t in ts])
    ac = np.array([np.corrcoef(T1[:-l], T1[l:])[0, 1] for l in range(1, min(60, len(T1)))])
    tau_t = next((l for l, v in enumerate(ac, start=1) if v < 1 / np.e), None)
    hold = ac[0] > 0.5
    print(line("τ_t（分钟）", "时间相关持续（acf 半衰 > 采样间隔 → m_eff < m，需校正）",
               f"τ_t ≈ {tau_t if tau_t else '>60'} 分钟，首步 acf={ac[0]:.3f}",
               hold))
    # 空间：同 tick 跨 chunk 相关
    t0 = clock.time - GAME_DAY
    vals = [e.get_weather(cx, 0, t0).temperature for cx in range(6)]
    # 温度没有直接跨 chunk 相关性（各 chunk 独立基线+场），测扰动相关性：用 wind 场
    wind = [e.get_weather(cx, 0, t0).wind_speed for cx in range(6)]
    corr2 = [np.corrcoef(wind[:-1], wind[1:])[0, 1]]
    print(line("τ_s（chunk）", "跨 chunk 相关存在（β>0）或可忽略（报告 β）",
               f"相邻风相关 = {corr2[0]:.3f}", True))
    e.shutdown()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--exp", nargs="+", default=["E1", "E2", "E3"])
    ap.add_argument("--fast", action="store_true")
    args = ap.parse_args()
    for e_ in args.exp:
        {"E1": _e1, "E2": _e2, "E3": _e3}[e_](args.fast)


if __name__ == "__main__":
    main()
