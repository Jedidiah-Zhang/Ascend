"""声明层验证 — 引擎实现 vs 声明（research/equations/equations.json）。

运行: .venv/bin/python research/equations/verify_equations.py [--fast]

判据（预注册，05 篇总则风格）：
  V0 Lean 生成物漂移：gen_lean --check 通过（GenDeclarationData.lean 与
      equations.json + config.py 真值一致；issue #44 防漂移机制）；
  V1 声明加载 + 结构校验：schema.validate 无问题；
  V2 L_j 对账：声明 L 与 config 常量解析计算一致（容差 1e-12）；
     derive_latitude = (LATITUDE_MAX−LATITUDE_MIN)/(LATITUDE_T_MAX−LATITUDE_T_MIN)；
     seasonal_amp[temp] = (SEASONAL_AMP_MAX−SEASONAL_AMP_MIN)/(SEASONAL_AMP_T_MAX−SEASONAL_AMP_T_MIN)；
     seasonal_amp[rain] = SEASONAL_AMP_R_BONUS/SEASONAL_AMP_R_REF；
     precip_type 为离散输出，L 退化为 0，不做解析对账；
  V3 引擎符合性（ascend.weather.derive）：
     precip_type_for 满足声明语义 round(1) 后 ≤0 为雪，输出 ∈ {snow, rain}；
     derive_latitude 输出落在声明界 [0,80]、锚点 (-5→80, 35→0)、单调不增；
     derive_seasonal_amp 输出落在声明界 [1,30]、锚点 (-5,2000→28, 35,2000→2)。
"""

from __future__ import annotations

import argparse
import random
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))                       # 供 import schema
sys.path.insert(0, str(HERE.parents[1] / "backend"))  # 供 import ascend

import schema
import gen_lean  # noqa: E402  V0 巡检用（同目录）

from ascend.config import (  # noqa: E402
    LATITUDE_MAX, LATITUDE_MIN, LATITUDE_T_MAX, LATITUDE_T_MIN,
    SEASONAL_AMP_MAX, SEASONAL_AMP_MIN,
    SEASONAL_AMP_R_BONUS, SEASONAL_AMP_R_REF,
    SEASONAL_AMP_T_MAX, SEASONAL_AMP_T_MIN,
)
from ascend.weather.derive import (  # noqa: E402
    derive_latitude, derive_seasonal_amp, precip_type_for,
)

JSON_PATH = HERE / "equations.json"
TOL = 1e-12


def line(name: str, ok: bool, detail: str = "") -> str:
    return f"[{'PASS' if ok else 'FAIL'}] {name} | {detail}"


def main() -> int:
    ap = argparse.ArgumentParser(description="声明层验证")
    ap.add_argument("--fast", action="store_true",
                    help="减少采样点（快速模式）")
    ap.add_argument("--json", default=str(JSON_PATH),
                    help="声明 JSON 路径")
    args = ap.parse_args()

    rng = random.Random(20260826)
    n = 2_000 if args.fast else 20_000
    results: list[tuple[str, bool, str]] = []

    # ── V0 Lean 生成物漂移巡检（issue #44）────────────
    # 固定锚定默认单一事实来源 equations.json（生成物入库对应它，
    # 不跟随 --json 的自定义路径，避免对拍临时片段误报入库产物漂移）。
    ok0, detail0 = gen_lean.check()
    results.append(("V0 Lean 生成物漂移", ok0, detail0))

    # ── V1 声明加载 + 结构校验 ────────────────────────
    graph = schema.load_declaration(args.json)
    issues = schema.validate(graph)
    ok = not issues
    results.append(("V1 声明加载 + 结构校验",
                    ok, "无问题" if ok else "; ".join(issues)))
    if ok:
        results.append(("V1 拓扑序", True,
                        " -> ".join(graph.toposort())))
        results.append(("V1 结构边/总边", True,
                        f"{len([e for e in graph.edges() if e[2].role == 'structural'])}/{len(graph.edges())}"))

    # ── V2 L_j 对账 ──────────────────────────────────
    expected = {
        ("sea_level_temp", "latitude"):
            (LATITUDE_MAX - LATITUDE_MIN) / (LATITUDE_T_MAX - LATITUDE_T_MIN),
        ("temperature", "seasonal_amp"):
            (SEASONAL_AMP_MAX - SEASONAL_AMP_MIN)
            / (SEASONAL_AMP_T_MAX - SEASONAL_AMP_T_MIN),
        ("rainfall", "seasonal_amp"):
            SEASONAL_AMP_R_BONUS / SEASONAL_AMP_R_REF,
    }
    for (p, c), exp in expected.items():
        spec = graph.edge(p, c)
        if spec is None:
            results.append((f"V2 L_j 对账 {p}->{c}", False,
                            "声明中无此边"))
            continue
        diff = abs(spec.L - exp)
        results.append((f"V2 L_j 对账 {p}->{c} (声明 {spec.L})",
                        diff <= TOL, f"解析值 {exp:.6g}，差 {diff:.2e}"))
    precip_edge = graph.edge("temperature", "precip_type")
    precip_ok = precip_edge is not None and precip_edge.L == 0.0
    results.append(("V2 L_j 对账 temperature->precip_type",
                    precip_ok,
                    "离散输出，L=0 不做解析对账（01 篇 margin 条件处理）"))

    # ── V3 引擎符合性 ────────────────────────────────
    def precip_ref(t: float) -> str:
        return "snow" if round(t, 1) <= 0 else "rain"

    temps = [-40.0, -1.0, -0.5, -0.05, -0.049, 0.0, 0.049, 0.05,
             0.5, 1.0, 40.0] + [rng.uniform(-40, 40) for _ in range(n)]
    bad = [t for t in temps
           if precip_type_for(t) != precip_ref(t)
           or precip_type_for(t) not in ("snow", "rain")]
    results.append(("V3 precip_type_for 阈值语义 (round(1) ≤0 为雪)",
                    not bad, f"{len(temps)} 样本，反例 {len(bad)}"))

    lat_samples = [-40.0, -5.0, 0.0, 10.0, 20.0, 30.0, 35.0, 40.0,
                   60.0] + [rng.uniform(-40, 60) for _ in range(n)]
    out_of_bounds = [t for t in lat_samples
                     if not (0.0 <= derive_latitude(t) <= 80.0)]
    monotonic = all(
        derive_latitude(a) >= derive_latitude(b)
        for a, b in zip(sorted(lat_samples), sorted(lat_samples)[1:])
    )
    anchors = [("derive_latitude(-5) = 80", abs(derive_latitude(-5.0) - 80.0) <= TOL),
               ("derive_latitude(35) = 0", abs(derive_latitude(35.0)) <= TOL)]
    lat_ok = not out_of_bounds and monotonic and all(o for _, o in anchors)
    anchor_txt = ", ".join(f"{n}{'✓' if o else '✗'}" for n, o in anchors)
    results.append(("V3 derive_latitude 界内 + 单调 + 锚点",
                    lat_ok,
                    f"{len(lat_samples)} 样本，越界 {len(out_of_bounds)}，{anchor_txt}"))

    sa_samples = [(rng.uniform(-20, 50), rng.uniform(0, 4000))
                  for _ in range(n)] + [(-5.0, 2000.0), (35.0, 2000.0)]
    sa_bad = [x for x in sa_samples
              if not (1.0 <= derive_seasonal_amp(*x) <= 30.0)]
    sa_anchors = [("(-5,2000) = 28", abs(derive_seasonal_amp(-5.0, 2000.0) - 28.0) <= TOL),
                  ("(35,2000) = 2", abs(derive_seasonal_amp(35.0, 2000.0) - 2.0) <= TOL)]
    sa_ok = not sa_bad and all(o for _, o in sa_anchors)
    sa_txt = ", ".join(f"{n}{'✓' if o else '✗'}" for n, o in sa_anchors)
    results.append(("V3 derive_seasonal_amp 界内 + 锚点",
                    sa_ok,
                    f"{len(sa_samples)} 样本，越界 {len(sa_bad)}，{sa_txt}"))

    # ── 汇总 ─────────────────────────────────────────
    passed = sum(1 for _, ok, _ in results if ok)
    for name, ok, detail in results:
        print(line(name, ok, detail))
    print(f"\n汇总: {passed}/{len(results)} 通过")
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())