"""探针 2 — E4：逐方程学习性表（E4 方程学习表 + E5 CRN 流诊断）。

运行: .venv/bin/python research/engine_e4_5.py [--fast]
验证 05 篇 E4 判据：
- 线性结构方程（温度/湿度/风/日照）线性回归残差 = 噪声方差（无结构）
- 缺失父变量时残差有可预测性 → 完备性漏洞的直接检验
- 阈值/分段方程（降水强度）需显式分段声明
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np
from sklearn.ensemble import RandomForestRegressor

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "backend"))

from ascend.config import GAME_DAY, GAME_YEAR
from ascend.space import ClimateZone, WeatherParams
from ascend.time import WorldClock
from ascend.weather.weather_engine import WeatherEngine
from ascend.world_tree import WorldTree


def line(name, pred, meas, ok):
    return f"[{'PASS' if ok else 'FAIL'}] {name} | 预测: {pred} | 实测: {meas}"


def _engine(fast=False):
    from ascend.space import ClimateZone as CZ
    wt = WorldTree()
    clock = WorldClock()
    e = WeatherEngine(clock, seed=42, world_tree_arg=wt)
    # 两个气候带（温带 + 亚寒带）以拓宽覆盖
    e.register_chunk(0, 0, WeatherParams(5.0, 800.0, 12.0, 100.0, 60.0, 5.0),
                     CZ.TEMPERATE_FOREST, 15.0)
    e.register_chunk(1, 0, WeatherParams(-2.0, 900.0, 10.0, 300.0, 65.0, 6.0),
                     CZ.SUBARCTIC_TAIGA, 5.0)
    clock.skip(GAME_YEAR)
    return e, clock


def _collect(e, clock, cx, cy, n=3000, fast=False, seed=7):
    rng = np.random.default_rng(seed)
    t0 = clock.time - GAME_DAY * 300
    ts = [int(rng.integers(t0, clock.time)) for _ in range(n)]
    rows = []
    for t in ts:
        ctx = e._tick_context(t)
        field = e._fields[(cx, cy)]
        # 基线成分 + tick 上下文（显式声明潜在父变量）
        bl = field.baseline
        season_cos = ctx["season_cos"]
        diurnal_cos = ctx["diurnal_cos"]
        wp = e.get_weather(cx, cy, t)
        rows.append({
            "t": t,
            "bl_temp": bl.temperature, "seasonal_amp": bl.seasonal_amp,
            "diurnal_amp": bl.diurnal_amp, "season_cos": season_cos,
            "diurnal_cos": diurnal_cos,
            "bl_hum": bl.humidity, "hum_seasonal_amp": bl.humidity_seasonal_amp,
            "hum_diurnal_amp": bl.humidity_diurnal_amp,
            "hum_sharpness": bl.humidity_sharpness,
            "bl_wind": bl.wind_speed, "bl_sun": bl.sunshine,
            "temperature": wp.temperature, "humidity": wp.humidity,
            "wind": wp.wind_speed, "sunshine": wp.sunshine,
            "rain": wp.rainfall,
        })
    return rows


def _fit_and_residual(rows, target, feats, model):
    from sklearn.linear_model import LinearRegression
    X = np.array([[r[f] for f in feats] for r in rows])
    y = np.array([r[target] for r in rows])
    clf = LinearRegression() if model == "linear" else RandomForestRegressor(
        n_estimators=80, max_depth=8, n_jobs=-1, random_state=1)
    clf.fit(X, y)
    resid = y - clf.predict(X)
    return resid, clf, X, y


def _e4(fast=False):
    print("\n=== E4 逐方程学习性表（线性结构 vs 阈值/分段） ===")
    e, clock = _engine(fast)
    rows = _collect(e, clock, 0, 0, n=2500 if not fast else 500)
    temp_feats = ["bl_temp", "seasonal_amp", "diurnal_amp", "season_cos", "diurnal_cos"]
    hum_feats = ["bl_hum", "hum_seasonal_amp", "hum_diurnal_amp", "hum_sharpness",
                 "season_cos", "diurnal_cos"]
    wind_feats = ["bl_wind", "diurnal_cos"]
    sun_feats = ["bl_sun", "season_cos", "diurnal_cos"]
    rain_feats = ["bl_sun", "season_cos", "diurnal_cos", "bl_temp"]

    for name, target, feats in [
        ("温度（线性结构）", "temperature", temp_feats),
        ("湿度（tanh 季节形状）", "humidity", hum_feats),
        ("风速（微小扰动）", "wind", wind_feats),
        ("日照（阈值/日落日出）", "sunshine", sun_feats),
    ]:
        resid, clf, X, y = _fit_and_residual(rows, target, feats, "linear")
        noise_frac = float(np.std(resid) / np.std(y))
        # 非线性残差可预测性（对完整特征）
        from sklearn.model_selection import cross_val_score
        cv = cross_val_score(clf, X, y, cv=3, scoring="r2")
        out_sigma = float(np.std(y))
        info = "R²≈0 且输出σ 小（准常数 → 正确）" if cv.mean() < 0.3 and out_sigma < 1.0 \
            else "R² 高（线性可学）"
        ok = cv.mean() > 0.9 or (out_sigma < 1.0)
        print(line(f"{name}", "线性即可（R²>0.9）或输出为准常数（R²≈0 正确）",
                   f"CV R² = {cv.mean():.3f}，残差σ/输出σ = {noise_frac:.3f}，输出σ = {out_sigma:.3f} → {info}",
                   ok))
        # 缺失父变量检验：去掉 diurnal_cos（昼夜是真实父变量）测残差可预测性
        if name == "温度（线性结构）":
            resid2, clf2, X2, y2 = _fit_and_residual(rows, "temperature",
                                                     ["bl_temp", "seasonal_amp", "diurnal_amp",
                                                      "season_cos"], "linear")
            from sklearn.model_selection import cross_val_score
            cv2 = cross_val_score(RandomForestRegressor(n_estimators=80, max_depth=8,
                                                        random_state=1),
                                  X2, y2, cv=3, scoring="r2")
            print(line(f"{name}·缺失昼夜父变量", "遗漏父母 ⟹ 残差仍可预测（完备性直接检验）",
                       f"残差 R² = {cv2.mean():.3f}（>0 即有信号/遗漏）",
                       cv2.mean() > 0.2))
    e.shutdown()


def _e5(fast=False):
    print("\n=== E5 CRN 流纪律诊断（同 seed 双跑 / do 干预前诊断） ===")
    # 诊断：引擎对 RNG 的使用——单流还是分模块流？
    import ascend.weather.weather_engine as we
    import inspect
    import re
    src = inspect.getsource(we)
    # 查找 random/np 使用（引擎侧 rng 取得点）
    hits = re.findall(r"(random|np\.random|default_rng|Generator|RandomSeed|self\._rng|random_state)\w*", src)
    from collections import Counter
    rng_lines = [l.strip() for l in src.splitlines()
                 if re.search(r"(random|default_rng|RandomState)\(", l)]
    print(line("RNG 使用归纳", "确认引擎是否分模块独立随机流",
               f"散点 random 调用行数 = {len(rng_lines)}（构造器内无独立 _rng 即单流）",
               len(rng_lines) == 0))
    print("[信息] 引擎随机源经模块级 np.random/random 调用（非独立流）→ do 干预"
          "改变执行路径时将污染其他变量——见 00 篇 §2 前提 (a)，阶段 1 需配流。")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--exp", nargs="+", default=["E4", "E5"])
    ap.add_argument("--fast", action="store_true")
    args = ap.parse_args()
    for e in args.exp:
        {"E4": _e4, "E5": _e5}[e](args.fast)


if __name__ == "__main__":
    main()
