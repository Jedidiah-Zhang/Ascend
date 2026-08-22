"""探针 2 — E6：空间-时间维度的预测（全图模型 + 相邻区条件增益）。

回答 05 篇之后的补充问题：
(1) 全图模型：把"位置特征（基线/纬度/海拔/气候） + 时间相位"作为输入，
    模型能否预测任一同区任一时段的天气？（空间维度补上）
(2) 条件预测增益：观测到 A 区此刻降雨，对 B 区（相邻）未来降雨的预测力提升多少？
    随时间延迟如何衰减？（噪声场的空间-时间平滑性 → 可外推的窗口）

运行: .venv/bin/python research/engine_e6.py [--fast]
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


def _make_engine(fast=False):
    wt = WorldTree()
    clock = WorldClock()
    e = WeatherEngine(clock, seed=42, world_tree_arg=wt)
    # 6 区块 × 3 气候带（拓宽基线/纬度/海拔范围）
    specs = [
        ((0, 0), WeatherParams(5.0, 800.0, 12.0, 100.0, 60.0, 5.0), ClimateZone.TEMPERATE_FOREST, 15.0),
        ((0, 1), WeatherParams(8.0, 900.0, 12.5, 80.0, 62.0, 5.5), ClimateZone.TEMPERATE_FOREST, 16.0),
        ((1, 0), WeatherParams(-3.0, 700.0, 10.0, 250.0, 58.0, 4.0), ClimateZone.SUBARCTIC_TAIGA, 4.0),
        ((1, 1), WeatherParams(-6.0, 600.0, 9.0, 300.0, 55.0, 3.5), ClimateZone.SUBARCTIC_TAIGA, 2.0),
        ((2, 0), WeatherParams(-14.0, 400.0, 6.0, 200.0, 50.0, 3.0), ClimateZone.POLAR_TUNDRA, -8.0),
        ((2, 1), WeatherParams(22.0, 1100.0, 13.5, 50.0, 70.0, 6.5), ClimateZone.TROPICAL_SAVANNA, 26.0),
    ]
    for (cx, cy), bl, cz, slt in specs:
        e.register_chunk(cx, cy, bl, cz, slt)
    clock.skip(GAME_YEAR)
    return e, clock, specs


RAIN_EVENT = 0.05  # mm/h 之上的降雨视为"下雨事件"


def _e6(fast=False):
    print("\n=== E6 空间-时间维度：全图模型 + 相邻区条件增益 ===")
    e, clock, specs = _make_engine(fast)
    rng = np.random.default_rng(9)
    t0 = clock.time - GAME_DAY * 300
    # (1) 全图采样：位置特征 + 时间相位 → 天气
    rows = []
    n = 4000 if not fast else 800
    for _ in range(n):
        cx, cy = specs[rng.integers(0, len(specs))][0]
        t = int(rng.integers(t0, clock.time))
        ctx = e._tick_context(t)
        wp = e.get_weather(cx, cy, t)
        bl = e._fields[(cx, cy)].baseline
        rows.append({
            "bl_temp": bl.temperature, "seasonal_amp": bl.seasonal_amp,
            "diurnal_amp": bl.diurnal_amp, "lat": bl.latitude,
            "alt": bl.altitude, "rain_base": bl.rainfall,
            "season_cos": ctx["season_cos"], "diurnal_cos": ctx["diurnal_cos"],
            "temp": wp.temperature, "rain": wp.rainfall,
        })
    from sklearn.linear_model import LinearRegression
    from sklearn.model_selection import cross_val_score
    X = np.array([[r["bl_temp"], r["seasonal_amp"], r["diurnal_amp"], r["lat"],
                   r["alt"], r["rain_base"], r["season_cos"], r["diurnal_cos"]]
                  for r in rows])
    yT = np.array([r["temp"] for r in rows])
    lr = LinearRegression()
    cvT = cross_val_score(lr, X, yT, cv=3, scoring="r2")
    print(line("全图温度模型", "位置特征+时间相位 → 线性即可（R²>0.9）",
               f"CV R² = {cvT.mean():.3f}", cvT.mean() > 0.9))
    yR = np.array([r["rain"] for r in rows])
    cvR = cross_val_score(lr, X, yR, cv=3, scoring="r2")
    print(line("全图降雨模型", "降雨依赖空间-时间噪声场，线性学不全（预期低 R²）",
               f"CV R² = {cvR.mean():.3f}（信号场为主，先验仅气候带）", cvR.mean() < 0.5))
    # (2) 相邻区条件增益：A=(0,0) 下雨 → B=(0,1) 未来 dt 下雨概率
    A, B = (0, 0), (0, 1)
    lags_min = [0, 6, 30, 60, 120, 240, 480, 720]
    print("   延迟(分) | P(rain@B|rain@A) | P(rain@B) 基线 | 增益倍数")
    for lag in lags_min:
        rains_a = []
        rains_b_cond = []
        rains_b_marg = []
        for _ in range(600 if not fast else 200):
            t = int(rng.integers(t0, clock.time - lag * GAME_MINUTE))
            wa = e.get_weather(*A, t)
            wb0 = e.get_weather(*B, t)
            wb1 = e.get_weather(*B, t + lag * GAME_MINUTE)
            # 近似事件型：用强度>阈值的比例代替条件概率（数据量有限取均值）
            rains_a.append(1.0 if wa.rainfall > RAIN_EVENT else 0.0)
            rains_b_marg.append(1.0 if wb1.rainfall > RAIN_EVENT else 0.0)
            rains_b_cond.append(
                (1.0 if wb1.rainfall > RAIN_EVENT else 0.0) * (1.0 if wa.rainfall > RAIN_EVENT else 0.0))
        pa = float(np.mean(rains_a))
        pm = float(np.mean(rains_b_marg))
        pk = float(np.mean(rains_b_cond))
        # P(rainB at t+lag | rainA at t) ≈ P(both rain)/P(rainA)（当 lag>0 为时序近似）
        cond = pk / pa if pa > 0 else 0.0
        gain = cond / pm if pm > 0 else 0.0
        print(f"   {lag:5d} | {cond:.3f} | {pm:.3f} | {gain:.2f}×")
    e.shutdown()


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--fast", action="store_true")
    args = ap.parse_args()
    _e6(args.fast)
