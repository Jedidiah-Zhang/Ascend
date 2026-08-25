#!/usr/bin/env python3
"""声明数据 → Lean 自动生成器（issue #44 防漂移机制）。

单一事实来源（research/equations/equations.json + backend/ascend/config.py
常量真值）自动生成 research/lean/AscendLean/CausalVerification/GenDeclarationData.lean：

  数据段 —— config 两组推导常量、声明边表的 role/L、变量 bounds；
  对账段 —— 生成的数值与手写 Declarations.lean 实例逐一相等的定理，
            证明只用 rfl/norm_num 级别（无 sorry/admit）。

防漂移三层闭环：
  ① 来源文件改动 → 数据段字面量与头部 sha256 指纹变化 → --check diff 非零退出；
  ② 手改 Declarations.lean 实例数值 → 第四节对账定理失败 → lake build 红；
  ③ equations.json 的边 L 与 config 解析斜率不一致 → 新生成文件本身编译失败
     （对账定理即 verify_equations.py 判据 V2 的形式化对应物）。

角色边界：本工具只搬运声明数据并锚定一致性；数学性质（界/单调/Lipschitz）
的证明仍在手写的 Declarations.lean，不在本文件重复。

运行:
  .venv/bin/python research/equations/gen_lean.py            # 生成/刷新
  .venv/bin/python research/equations/gen_lean.py --check    # 巡检（CI/本地）

退出码: 0 一致/成功; 1 漂移或生成物缺失; 2 来源读取/数据错误。

注意（Lean 字面量记法坑）：值为整数的实数一律生成整数记法（-5.0 → "-5"）。
手写实例 latitudeConfig/ampConfig 全部为整数记法，而 OfNat 与 OfScientific
两种字面量形式在 ℝ 上不 defeq（rfl/show 直接失败），两侧必须同形。
"""

from __future__ import annotations

import argparse
import difflib
import hashlib
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent            # research/equations
ROOT = HERE.parents[1]                            # 仓库根
DEFAULT_JSON = HERE / "equations.json"
CONFIG_PY = ROOT / "backend" / "ascend" / "config.py"
OUT_PATH = (HERE.parent / "lean" / "AscendLean" / "CausalVerification"
            / "GenDeclarationData.lean")

# config.py 中本管线的常量名单（与 verify_equations.py 判据 V2 同源）
CONFIG_CONSTANTS = [
    "LATITUDE_T_MIN", "LATITUDE_T_MAX", "LATITUDE_MIN", "LATITUDE_MAX",
    "SEASONAL_AMP_T_MIN", "SEASONAL_AMP_T_MAX",
    "SEASONAL_AMP_MAX", "SEASONAL_AMP_MIN",
    "SEASONAL_AMP_R_REF", "SEASONAL_AMP_R_BONUS", "SEASONAL_AMP_BOUNDS",
]

# 已知边 → Lean def 名（未知边自动 camel 命名进数据段，但不产生对账定理——
# 新增边需人工评估是否扩入第四节协议模板）
EDGE_DEF_NAMES = {
    ("sea_level_temp", "latitude"): "edgeSeaLevelTempLatitudeL",
    ("temperature", "precip_type"): "edgeTemperaturePrecipTypeL",
    ("temperature", "seasonal_amp"): "edgeTemperatureSeasonalAmpL",
    ("rainfall", "seasonal_amp"): "edgeRainfallSeasonalAmpL",
}

# 有 bounds 的变量 → Lean def 名前缀（其余有界变量同样自动 camel 进数据段）
VAR_DEF_NAMES = {
    "latitude": "varLatitude",
    "rainfall": "varRainfall",
    "seasonal_amp": "varSeasonalAmp",
}


def fmt(x: float) -> str:
    """float → Lean 实数字面量。整数值用整数记法（见模块 docstring 的记法坑）。"""
    x = float(x)
    if x.is_integer() and abs(x) < 1e15:
        return str(int(x))
    return repr(x)


def camel(s: str) -> str:
    """snake_case → CamelCase（未知边/变量的 def 名回退方案）。"""
    return "".join(p.capitalize() for p in s.split("_"))


def short_hash(path: Path) -> str:
    """来源文件 sha256 前 16 位（写入生成文件头，供 --check 与人工核对）。"""
    return hashlib.sha256(path.read_bytes()).hexdigest()[:16]


def load_declaration(json_path: Path) -> dict:
    """读声明 JSON，缺必需键直接抛错（退出码 2 路径）。"""
    raw = json.loads(json_path.read_text(encoding="utf-8"))
    for key in ("variables", "edges"):
        if key not in raw:
            raise KeyError(f"{json_path} 缺少必需键 {key!r}")
    return raw


def load_config_constants() -> dict[str, float]:
    """以 sys.path 方式 import ascend.config 取常量真值
    （路径处理同 verify_equations.py:24-27）。"""
    sys.path.insert(0, str(ROOT / "backend"))
    from ascend import config as asc_config  # noqa: E402

    consts: dict[str, float] = {}
    for name in CONFIG_CONSTANTS:
        value = getattr(asc_config, name)
        if isinstance(value, tuple):  # SEASONAL_AMP_BOUNDS=(lo, hi)
            if len(value) != 2:
                raise ValueError(f"config.{name} 不是二元组: {value!r}")
            consts[f"{name}_LO"] = float(value[0])
            consts[f"{name}_HI"] = float(value[1])
        else:
            consts[name] = float(value)
    return consts


def rel(path: Path) -> str:
    """相对仓库根的显示路径（仓库外路径原样返回，不抛错）。"""
    try:
        return str(path.relative_to(ROOT))
    except ValueError:
        return str(path)


def build_content(json_path: Path) -> tuple[str, dict]:
    """组装 GenDeclarationData.lean 全文。返回 (内容, 元数据)。"""
    decl = load_declaration(json_path)
    cfg = load_config_constants()

    edges = {(e["parent"], e["child"]): e for e in decl["edges"]}
    for pair in EDGE_DEF_NAMES:
        if pair not in edges:
            raise ValueError(f"equations.json 缺少对账所需边 {pair[0]}->{pair[1]}")
    for var in VAR_DEF_NAMES:
        if var not in decl["variables"]:
            raise ValueError(f"equations.json 缺少对账所需变量 {var!r}")

    eq_sha = short_hash(json_path)
    cfg_sha = short_hash(CONFIG_PY)

    L: list[str] = []
    add = L.append

    # ── 文件头（import 必须在首行，注释紧随其后）─────────
    add("import AscendLean.CausalVerification.Declarations")
    add("")
    add("/-! AUTO-GENERATED — 本文件由工具生成，禁止手改。")
    add("")
    add("生成器：research/equations/gen_lean.py（issue #44 防漂移机制）")
    add("生成命令：.venv/bin/python research/equations/gen_lean.py")
    add("巡检命令：.venv/bin/python research/equations/gen_lean.py --check")
    add("巡检接入：research/equations/verify_equations.py 主流程 V0 步")
    add("")
    add("来源与指纹（sha256 前 16 位）：")
    add(f"- {rel(json_path)}   sha256:{eq_sha}")
    add(f"- {rel(CONFIG_PY)}            sha256:{cfg_sha}")
    add("")
    add("防漂移三层闭环：")
    add("① 来源改动 → 数据段字面量/本头指纹变化 → --check diff 非零退出；")
    add("② 手改 Declarations.lean 实例 → 第四节对账定理失败 → lake build 红；")
    add("③ 边 L 与 config 解析斜率不一致 → 本文件新生成版本直接编译失败。")
    add("")
    add("修复流程：跑生成命令刷新本文件，再于 research/lean 下执行")
    add("`~/.elan/bin/lake env lean AscendLean/CausalVerification/GenDeclarationData.lean`")
    add("确认对账定理仍绿；若红，说明手写侧与声明真值漂移，修手写侧。")
    add("")
    add("角色边界：本文件只搬运**声明数据**并锚定其与手写镜像的一致性；")
    add("数学性质（界/单调/Lipschitz）的证明仍在手写的 Declarations.lean。 -/")
    add("namespace AscendLean.GenDeclarationData")
    add("")
    add("open AscendLean.Declarations")
    add("")

    # ── 第一节：config 常量真值 ─────────────────────────
    add("-- ═══ 第一节 config 常量真值（backend/ascend/config.py）═══")
    add("")
    add("-- 纬度推导 LATITUDE_*")
    add(f"def cfgLatTMin : ℝ := {fmt(cfg['LATITUDE_T_MIN'])}")
    add(f"def cfgLatTMax : ℝ := {fmt(cfg['LATITUDE_T_MAX'])}")
    add(f"def cfgLatMin : ℝ := {fmt(cfg['LATITUDE_MIN'])}")
    add(f"def cfgLatMax : ℝ := {fmt(cfg['LATITUDE_MAX'])}")
    add("")
    add("-- 季节振幅推导 SEASONAL_AMP_*（BOUNDS 元组拆 LO/HI）")
    add(f"def cfgAmpTMin : ℝ := {fmt(cfg['SEASONAL_AMP_T_MIN'])}")
    add(f"def cfgAmpTMax : ℝ := {fmt(cfg['SEASONAL_AMP_T_MAX'])}")
    add(f"def cfgAmpMax : ℝ := {fmt(cfg['SEASONAL_AMP_MAX'])}")
    add(f"def cfgAmpMin : ℝ := {fmt(cfg['SEASONAL_AMP_MIN'])}")
    add(f"def cfgAmpRRef : ℝ := {fmt(cfg['SEASONAL_AMP_R_REF'])}")
    add(f"def cfgAmpRBonus : ℝ := {fmt(cfg['SEASONAL_AMP_R_BONUS'])}")
    add(f"def cfgAmpBLo : ℝ := {fmt(cfg['SEASONAL_AMP_BOUNDS_LO'])}")
    add(f"def cfgAmpBHi : ℝ := {fmt(cfg['SEASONAL_AMP_BOUNDS_HI'])}")
    add("")

    # ── 第二节：声明边表 ───────────────────────────────
    add('-- ═══ 第二节 声明边表（equations.json "edges"，保持声明原序）═══')
    add("")
    for e in decl["edges"]:
        p, c = e["parent"], e["child"]
        name = EDGE_DEF_NAMES.get((p, c), f"edge{camel(p)}{camel(c)}L")
        add(f"-- {p} → {c} | role={e.get('role', '?')} | equation={e.get('equation', '?')}")
        add(f"def {name} : ℝ := {fmt(e['L'])}")
    add("")

    # ── 第三节：声明变量界 ─────────────────────────────
    add('-- ═══ 第三节 声明变量界（equations.json "variables" 的 bounds）═══')
    add("")
    for name, spec in decl["variables"].items():
        bounds = spec.get("bounds")
        if not bounds:
            continue
        base = VAR_DEF_NAMES.get(name, f"var{camel(name)}")
        add(f"-- {name}: bounds=[{fmt(bounds[0])}, {fmt(bounds[1])}]")
        add(f"def {base}Lo : ℝ := {fmt(bounds[0])}")
        add(f"def {base}Hi : ℝ := {fmt(bounds[1])}")
    add("")

    # ── 第四节：对账定理（防漂移核心）──────────────────
    add("-- ═══ 第四节 对账定理（防漂移核心）═══")
    add("")
    add("-- 形状统一为：手写 Declarations.lean 实例的相关量 = 本文件数据段字面量。")
    add("-- 任何一侧改动都会使本节某条定理失败（lake build 红）或触发 --check diff。")
    add("-- 协议耦合说明：本节模板引用 LatCfg/AmpCfg 的字段名，若手写侧重构字段，")
    add("-- 需同步修改 gen_lean.py 的对账模板。")
    add("")
    add(f"-- 4.1 纬度斜率对账（V2 判据 sea_level_temp->latitude；声明 L={fmt(edges[('sea_level_temp', 'latitude')]['L'])}）")
    add("theorem gen_latitude_L_matches :")
    add("    (latitudeConfig.latMax - latitudeConfig.latMin)")
    add("      / (latitudeConfig.tMax - latitudeConfig.tMin)")
    add(f"      = {EDGE_DEF_NAMES[('sea_level_temp', 'latitude')]} := by")
    add(f"  show (({fmt(cfg['LATITUDE_MAX'])}:ℝ) - {fmt(cfg['LATITUDE_MIN'])})"
        f" / ({fmt(cfg['LATITUDE_T_MAX'])} - ({fmt(cfg['LATITUDE_T_MIN'])}))"
        f" = {fmt(edges[('sea_level_temp', 'latitude')]['L'])}")
    add("  norm_num")
    add("")
    add(f"-- 4.2 振幅温度向斜率对账（V2 判据 temperature->seasonal_amp；声明 L={fmt(edges[('temperature', 'seasonal_amp')]['L'])}）")
    add("theorem gen_amp_L_temp_matches :")
    add("    (ampConfig.ampMax - ampConfig.ampMin)")
    add("      / (ampConfig.tMax - ampConfig.tMin)")
    add(f"      = {EDGE_DEF_NAMES[('temperature', 'seasonal_amp')]} := by")
    add(f"  show (({fmt(cfg['SEASONAL_AMP_MAX'])}:ℝ) - {fmt(cfg['SEASONAL_AMP_MIN'])})"
        f" / ({fmt(cfg['SEASONAL_AMP_T_MAX'])} - ({fmt(cfg['SEASONAL_AMP_T_MIN'])}))"
        f" = {fmt(edges[('temperature', 'seasonal_amp')]['L'])}")
    add("  norm_num")
    add("")
    add(f"-- 4.3 振幅降雨向对账（V2 判据 rainfall->seasonal_amp；声明 L={fmt(edges[('rainfall', 'seasonal_amp')]['L'])}）")
    add("theorem gen_amp_L_rain_matches :")
    add("    ampConfig.rBonus / ampConfig.rRef")
    add(f"      = {EDGE_DEF_NAMES[('rainfall', 'seasonal_amp')]} := by")
    add(f"  show (({fmt(cfg['SEASONAL_AMP_R_BONUS'])}:ℝ)"
        f" / {fmt(cfg['SEASONAL_AMP_R_REF'])}"
        f" = {fmt(edges[('rainfall', 'seasonal_amp')]['L'])})")
    add("  norm_num")
    add("")
    add("-- 4.4 离散边退化对账（temperature->precip_type，01 篇 margin 条件处理）")
    add(f"theorem gen_precip_edge_L_zero : {EDGE_DEF_NAMES[('temperature', 'precip_type')]} = 0 := by")
    add("  rfl")
    add("")
    add("-- 4.5 纬度配置全字段对账：手写实例 ↔ config 真值 ↔ 声明 bounds 三方绑定")
    add("theorem gen_latitude_fields_match :")
    add("    latitudeConfig.tMin = cfgLatTMin ∧ latitudeConfig.tMax = cfgLatTMax")
    add("      ∧ latitudeConfig.latMin = cfgLatMin ∧ latitudeConfig.latMax = cfgLatMax")
    add("      ∧ latitudeConfig.latMin = varLatitudeLo ∧ latitudeConfig.latMax = varLatitudeHi := by")
    add("  refine ⟨?_, ?_, ?_, ?_, ?_, ?_⟩ <;> rfl")
    add("")
    add("-- 4.6 振幅配置全字段对账：手写实例 ↔ config 真值 ↔ 声明 bounds 三方绑定")
    add("theorem gen_amp_fields_match :")
    add("    ampConfig.tMin = cfgAmpTMin ∧ ampConfig.tMax = cfgAmpTMax")
    add("      ∧ ampConfig.ampMax = cfgAmpMax ∧ ampConfig.ampMin = cfgAmpMin")
    add("      ∧ ampConfig.rRef = cfgAmpRRef ∧ ampConfig.rBonus = cfgAmpRBonus")
    add("      ∧ ampConfig.bLo = cfgAmpBLo ∧ ampConfig.bHi = cfgAmpBHi")
    add("      ∧ ampConfig.bLo = varSeasonalAmpLo ∧ ampConfig.bHi = varSeasonalAmpHi := by")
    add("  refine ⟨?_, ?_, ?_, ?_, ?_, ?_, ?_, ?_, ?_, ?_⟩ <;> rfl")
    add("")
    add("-- 4.7 声明 bounds 良序（防 bounds 写反；rainfall 无手写镜像，仅自洽）")
    add("theorem gen_bounds_wellformed :")
    add("    varLatitudeLo ≤ varLatitudeHi ∧ varRainfallLo ≤ varRainfallHi")
    add("      ∧ varSeasonalAmpLo ≤ varSeasonalAmpHi := by")
    add("  refine ⟨?_, ?_, ?_⟩ <;>")
    add("    norm_num [varLatitudeLo, varLatitudeHi,")
    add("      varRainfallLo, varRainfallHi, varSeasonalAmpLo, varSeasonalAmpHi]")
    add("")
    add("end AscendLean.GenDeclarationData")
    add("")

    meta = {"eq_sha": eq_sha, "cfg_sha": cfg_sha,
            "json": str(json_path), "edges": len(decl["edges"]),
            "theorems": 7}
    return "\n".join(L), meta


def compare(out_path: Path, json_path: Path) -> tuple[bool, str, str | None]:
    """重新生成并与现存文件比较。返回 (一致?, 摘要, 完整 diff 或 None)。"""
    content, _ = build_content(json_path)
    if not out_path.exists():
        return False, f"生成物不存在: {out_path}（先运行生成命令）", None
    current = out_path.read_text(encoding="utf-8")
    if current == content:
        return True, "现存文件与来源重新生成结果一致", None
    diff_lines = list(difflib.unified_diff(
        current.splitlines(), content.splitlines(),
        fromfile=f"{out_path} (现存)", tofile=f"{out_path} (期望)",
        lineterm=""))
    summary = (f"现存 {out_path.name} 与来源重新生成结果不一致"
               f"（{len(diff_lines)} 行差异）")
    return False, summary, "\n".join(diff_lines)


def check(json_path: Path = DEFAULT_JSON, out_path: Path = OUT_PATH) -> tuple[bool, str]:
    """巡检入口（供 verify_equations.py import 调用）。

    返回 (是否一致, 中文摘要)；漂移详情由 CLI --check 打印完整 diff。
    """
    ok, summary, _ = compare(out_path, json_path)
    return ok, summary


def main() -> int:
    ap = argparse.ArgumentParser(
        description="声明数据 → Lean 生成器（issue #44 防漂移）")
    ap.add_argument("--check", action="store_true",
                    help="巡检模式：重新生成并与现存文件 diff，不一致则 exit 1")
    ap.add_argument("--json", default=str(DEFAULT_JSON),
                    help="声明 JSON 路径（默认单一事实来源 equations.json）")
    ap.add_argument("--out", default=str(OUT_PATH),
                    help="生成目标路径")
    args = ap.parse_args()

    json_path = Path(args.json)
    out_path = Path(args.out)

    try:
        if args.check:
            ok, summary, diff = compare(out_path, json_path)
            if ok:
                print(f"[PASS] Lean 生成物巡检 | {summary}")
                return 0
            print(f"[FAIL] Lean 生成物漂移 | {summary}")
            if diff is not None:
                print(diff)
            print("修复: .venv/bin/python research/equations/gen_lean.py"
                  "  然后在 research/lean 下重跑 lake env lean / lake build")
            return 1

        content, meta = build_content(json_path)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(content, encoding="utf-8")
        print(f"[PASS] 生成 {out_path}")
        print(f"       来源指纹 equations.json sha256:{meta['eq_sha']}")
        print(f"       来源指纹 config.py         sha256:{meta['cfg_sha']}")
        print(f"       边 {meta['edges']} 条，对账定理 {meta['theorems']} 条")
        print("下一步: cd research/lean && ~/.elan/bin/lake env lean "
              "AscendLean/CausalVerification/GenDeclarationData.lean")
        return 0
    except (OSError, ImportError, AttributeError,
            KeyError, ValueError, json.JSONDecodeError) as exc:
        print(f"[FAIL] 来源读取/数据错误: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
