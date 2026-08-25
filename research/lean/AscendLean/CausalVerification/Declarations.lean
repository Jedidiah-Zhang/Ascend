import Mathlib
import AscendLean.CausalVerification.LipschitzLayer

/-!
# 声明层函数性质 — equations.json 承诺的机器验证

出处：`research/equations/equations.json`（世界参数声明的单一事实来源）+
`backend/ascend/config.py` 第 268-281 行常量 +
`backend/ascend/weather/derive.py` 引擎公式 +
`research/equations/verify_equations.py` 判据 V2/V3。

本文件把声明数据的三条承诺变成定理：

1. **输出界**（variables 条目的 `bounds`）；
2. **单调性**（inverse 角色边的方向语义）；
3. **Lipschitz 常数**（edges 条目的 `L`，verify_equations.py V2 解析对账）。

角色边界：Lean 只证明**声明层性质**（镜像公式的数学事实），
不检查引擎 Python 代码是否按声明实现——那是对拍测试
（verify_equations.py V3）的活；本文件的锚点/数值核对定理是
V2 判据的形式化对应物。

编码取舍：
- `clamp` 自建（Mathlib 无内置；语义取 climate.py 的
  `max lo (min hi value)`，即 `min hi (max lo x)`）；
- Lipschitz 用轻量谓词 `Lip K g`（绝对值形式，K : ℝ）而非 Mathlib 的
  `LipschitzWith`（ℝ≥0 系数 + edist）——声明数据的 L_j 是普通非负实数，
  绝对值形式与 equations.json 的承诺逐字对应；
- `precip_type_for` 建模为理想阈值 `T ≤ 0 → snow`：引擎先 round(1) 再判定
  是事件广播/UI 显示一致的浮点细节，不在数学规范内（见第四节注释）。

与 LipschitzLayer.lean 的衔接见第五节：本文件的逐边 `Lip` 定理
供给连接定理 `error_recurrence_bound` 的 `hlip` 假设。
-/

namespace AscendLean.Declarations

/-! ## 第一节：clamp 引理库（通用小工具）

镜像 `backend/ascend/space/climate.py` 的 `max lo (min hi value)`。
前置条件 `lo ≤ hi` 由引理显式携带（与 Python 版一致：乱序输入行为未定义）。 -/

/-- 钳制函数：`climate.py` 的 `max lo (min hi value)`。 -/
def clamp (lo hi x : ℝ) : ℝ := min hi (max lo x)

/-- **clamp 有界性·内部恒等**：x ∈ [lo, hi] 时钳制不动。
    （verify_equations.py V3「锚点」判据的基础机制） -/
theorem clamp_eq_self {lo hi x : ℝ} (hlo : lo ≤ x) (hhi : x ≤ hi) :
    clamp lo hi x = x := by
  show min hi (max lo x) = x
  rw [max_eq_right hlo, min_eq_right hhi]

/-- clamp 在下界以下取下界。 -/
theorem clamp_eq_lo {lo hi x : ℝ} (hle : lo ≤ hi) (h : x < lo) :
    clamp lo hi x = lo := by
  show min hi (max lo x) = lo
  rw [max_eq_left (le_of_lt h), min_eq_right hle]

/-- clamp 在上界以上取上界。 -/
theorem clamp_eq_hi {lo hi x : ℝ} (h : hi < x) :
    clamp lo hi x = hi := by
  show min hi (max lo x) = hi
  refine min_eq_left ?_
  exact le_trans (le_of_lt h) (le_max_right lo x)

/-- **clamp 保界·上侧**。 -/
theorem clamp_le_hi (lo hi x : ℝ) : clamp lo hi x ≤ hi := by
  show min hi (max lo x) ≤ hi
  exact min_le_left hi (max lo x)

/-- **clamp 保界·下侧**（需 lo ≤ hi）。 -/
theorem clamp_ge_lo {lo hi x : ℝ} (hle : lo ≤ hi) : lo ≤ clamp lo hi x := by
  show lo ≤ min hi (max lo x)
  exact le_min hle (le_max_left lo x)

/-- **clamp 保界**：输出永远落在 [lo, hi]。
    （对应 variables 条目 bounds 承诺的第一半：钳制输出界内） -/
theorem clamp_mem {lo hi : ℝ} (hle : lo ≤ hi) (x : ℝ) :
    lo ≤ clamp lo hi x ∧ clamp lo hi x ≤ hi :=
  ⟨clamp_ge_lo hle, clamp_le_hi lo hi x⟩

/-- **clamp 保 Lipschitz（非扩张）**：|clamp a − clamp b| ≤ |a − b|。
    证明：min 与 max 各自至多放大一项的变化
    （`abs_min_sub_min_le_max` / `abs_max_sub_max_le_max`），另一项相同归零。 -/
theorem abs_clamp_sub_clamp_le (lo hi a b : ℝ) :
    |clamp lo hi a - clamp lo hi b| ≤ |a - b| := by
  show |min hi (max lo a) - min hi (max lo b)| ≤ |a - b|
  have hz1 : |(hi:ℝ) - hi| = 0 := by rw [sub_self, abs_zero]
  have hx0 : |(lo:ℝ) - lo| = 0 := by rw [sub_self, abs_zero]
  have h3 : max |hi - hi| |max lo a - max lo b| ≤ max 0 |a - b| := by
    rw [hz1]
    calc max 0 |max lo a - max lo b| = |max lo a - max lo b| :=
          max_eq_right (abs_nonneg _)
      _ ≤ max |lo - lo| |a - b| := abs_max_sub_max_le_max lo a lo b
      _ = max 0 |a - b| := by rw [hx0]
  calc |min hi (max lo a) - min hi (max lo b)|
      ≤ max |hi - hi| |max lo a - max lo b| :=
        abs_min_sub_min_le_max hi (max lo a) hi (max lo b)
    _ ≤ max 0 |a - b| := h3
    _ = |a - b| := max_eq_right (abs_nonneg _)

/-! ### Lipschitz 谓词与小复合代数 -/

/-- 声明层 Lipschitz 谓词：全局 K-Lipschitz（绝对值形式）。
    取轻量自定义而非 Mathlib `LipschitzWith`：声明数据的 L_j 是普通
    非负实数，绝对值形式与 equations.json 的承诺逐字对应。 -/
def Lip (K : ℝ) (g : ℝ → ℝ) : Prop := ∀ x y, |g x - g y| ≤ K * |x - y|

/-- **Lipschitz 函数复合 clamp 常数不变**（02 篇误差传播视角：钳制只会
    吃掉变化量，不会制造变化量）。 -/
theorem lip_clamp_comp {K : ℝ} {g : ℝ → ℝ} (hg : Lip K g) (lo hi : ℝ) :
    Lip K (fun x => clamp lo hi (g x)) := by
  intro x y
  calc |clamp lo hi (g x) - clamp lo hi (g y)| ≤ |g x - g y| :=
      abs_clamp_sub_clamp_le lo hi (g x) (g y)
    _ ≤ K * |x - y| := hg x y

/-- 加常数不改 Lipschitz 常数。 -/
theorem lip_add_const {K c : ℝ} {g : ℝ → ℝ} (hg : Lip K g) :
    Lip K (fun x => g x + c) := by
  intro x y
  have h : g x + c - (g y + c) = g x - g y := by ring
  rw [h]
  exact hg x y

theorem lip_const_add {K c : ℝ} {g : ℝ → ℝ} (hg : Lip K g) :
    Lip K (fun x => c + g x) := by
  intro x y
  have h : c + g x - (c + g y) = g x - g y := by ring
  rw [h]
  exact hg x y

/-- 乘非负常数 M 把 Lipschitz 常数放大到 K·M。 -/
theorem lip_mul_const {K M : ℝ} {g : ℝ → ℝ} (hg : Lip K g) (hM : 0 ≤ M) :
    Lip (K * M) (fun x => g x * M) := by
  intro x y
  have h : g x * M - g y * M = (g x - g y) * M := by ring
  rw [h, abs_mul, abs_of_nonneg hM]
  calc |g x - g y| * M ≤ K * |x - y| * M :=
        mul_le_mul_of_nonneg_right (hg x y) hM
    _ = K * M * |x - y| := by ring

/-- **clamp 保单调（反向）**：Antitone 函数过钳制仍 Antitone。
    （verify_equations.py V3「单调不增」判据的机制层） -/
theorem antitone_clamp {f : ℝ → ℝ} (hf : Antitone f) (lo hi : ℝ) :
    Antitone (fun x => clamp lo hi (f x)) := by
  intro a b hab
  show clamp lo hi (f b) ≤ clamp lo hi (f a)
  unfold clamp
  exact min_le_min le_rfl (max_le_max le_rfl (hf hab))

/-! ## 第二节：derive_latitude（equations.json 边 sea_level_temp→latitude）

声明：role=inverse，L=2.0；变量 bounds=[0,80]。
config 键：LATITUDE_T_MIN=-5, LATITUDE_T_MAX=35, LATITUDE_MIN=0, LATITUDE_MAX=80。
verify_equations.py 判据：V2 L_j 对账 sea_level_temp->latitude；
V3 derive_latitude 界内 + 单调 + 锚点。

一般化：参数化四个常量，仅假设 T_MIN < T_MAX、LAT_MIN ≤ LAT_MAX。 -/

/-- 递减仿射映射：`hi − (t − tMin)/(tMax − tMin) · (hi − lo)`。
    tMin ↦ hi（冷端取高值）、tMax ↦ lo（热端取低值）的线性反转，
    derive_latitude 与 derive_seasonal_amp 的温度方向共用此形。 -/
noncomputable def decAffine (tMin tMax lo hi t : ℝ) : ℝ :=
  hi - (t - tMin) / (tMax - tMin) * (hi - lo)

/-- 线性部分的逐点差恒等式：斜率处处相同（全局 Lipschitz 常数的来源）。 -/
lemma decAffine_sub {tMin tMax lo hi a b : ℝ} (hT : tMin < tMax) :
    decAffine tMin tMax lo hi a - decAffine tMin tMax lo hi b
      = ((hi - lo) / (tMax - tMin)) * (b - a) := by
  have hd : tMax - tMin ≠ 0 := ne_of_gt (sub_pos.mpr hT)
  simp only [decAffine]
  field_simp
  ring

/-- 冷端锚点：tMin ↦ hi。（V3 锚点 derive_latitude(-5) = 80 的一般形） -/
lemma decAffine_at_tMin {tMin tMax lo hi : ℝ} :
    decAffine tMin tMax lo hi tMin = hi := by
  simp only [decAffine, sub_self, zero_div, zero_mul, sub_zero]

/-- 热端锚点：tMax ↦ lo。（V3 锚点 derive_latitude(35) = 0 的一般形） -/
lemma decAffine_at_tMax {tMin tMax lo hi : ℝ} (hT : tMin < tMax) :
    decAffine tMin tMax lo hi tMax = lo := by
  have hd : tMax - tMin ≠ 0 := ne_of_gt (sub_pos.mpr hT)
  simp only [decAffine]
  field_simp
  ring

/-- 线性部分 Lipschitz 常数**恰为**斜率 (hi−lo)/(tMax−tMin)（精确等式）。 -/
lemma decAffine_lip {tMin tMax lo hi : ℝ} (hT : tMin < tMax) (hnn : lo ≤ hi) :
    Lip ((hi - lo) / (tMax - tMin)) (decAffine tMin tMax lo hi) := by
  intro a b
  have hsnn : (0:ℝ) ≤ (hi - lo) / (tMax - tMin) :=
    div_nonneg (by linarith) (by linarith)
  rw [decAffine_sub hT, abs_mul, abs_of_nonneg hsnn, abs_sub_comm b a]

/-- 线性部分单调不增。 -/
lemma decAffine_antitone {tMin tMax lo hi : ℝ} (hT : tMin < tMax) (hnn : lo ≤ hi) :
    Antitone (decAffine tMin tMax lo hi) := by
  intro a b hab
  have hsnn : (0:ℝ) ≤ (hi - lo) / (tMax - tMin) :=
    div_nonneg (by linarith) (by linarith)
  have h : decAffine tMin tMax lo hi b - decAffine tMin tMax lo hi a
      = ((hi - lo) / (tMax - tMin)) * (a - b) :=
    decAffine_sub (tMin := tMin) (tMax := tMax) (lo := lo) (hi := hi) hT
  have hn : ((hi - lo:ℝ) / (tMax - tMin)) * (a - b) ≤ 0 :=
    mul_nonpos_of_nonneg_of_nonpos hsnn (sub_nonpos.mpr hab)
  linarith

/-- derive_latitude 镜像公式（derive.py 第 195-199 行）：
    `lat(T) = clamp(LAT_MAX − (T−T_MIN)/(T_MAX−T_MIN)·(LAT_MAX−LAT_MIN),
    LAT_MIN, LAT_MAX)`。 -/
-- 注意：字段必须逐行声明；Lean 4.34 下「一行多字段 + 后续命题字段引用」
-- 会触发 auto-bound implicit 误绑定（LE ?m 卡死）。
structure LatCfg where
  tMin : ℝ
  tMax : ℝ
  latMin : ℝ
  latMax : ℝ
  tMinLtTMax : tMin < tMax
  latMinLeLatMax : latMin ≤ latMax

/-- 纬度推导的线性核。 -/
noncomputable def latLinear (c : LatCfg) (t : ℝ) : ℝ :=
  decAffine c.tMin c.tMax c.latMin c.latMax t

/-- derive_latitude 镜像定义。 -/
noncomputable def deriveLat (c : LatCfg) (t : ℝ) : ℝ :=
  clamp c.latMin c.latMax (latLinear c t)

/-- **输出界**：lat(T) ∈ [LAT_MIN, LAT_MAX]。
    （variables.latitude bounds=[0,80] 的承诺；V3「界内」判据） -/
theorem deriveLat_mem (c : LatCfg) (t : ℝ) :
    c.latMin ≤ deriveLat c t ∧ deriveLat c t ≤ c.latMax :=
  clamp_mem c.latMinLeLatMax _

/-- **单调不增**：温度越高纬度越低，钳制不破坏方向。
    （V3「单调」判据） -/
theorem deriveLat_antitone (c : LatCfg) :
    Antitone (deriveLat c) :=
  antitone_clamp (decAffine_antitone c.tMinLtTMax c.latMinLeLatMax) c.latMin c.latMax

/-- **Lipschitz 上界：常数 = (LAT_MAX−LAT_MIN)/(T_MAX−T_MIN)**。
    线性核斜率处处相同（精确等式），外层 clamp 非扩张不增常数。
    （V2 对账 sea_level_temp->latitude 的一般形；equations.json 声明 L=2.0） -/
theorem deriveLat_lip_upper (c : LatCfg) :
    Lip ((c.latMax - c.latMin) / (c.tMax - c.tMin)) (deriveLat c) :=
  lip_clamp_comp (decAffine_lip c.tMinLtTMax c.latMinLeLatMax) c.latMin c.latMax

/-- **Lipschitz 常数的最优性**：任何 L < 斜率都不是 Lipschitz 常数。
    反例取两端点 tMin/tMax：两点都在钳制不动区（tMin↦latMax 为内部点、
    tMax↦latMin 为边界点），故钳制前后的差完全相同。
    「恰为」= 上界 + 此下界。 -/
theorem deriveLat_lip_optimal (c : LatCfg) (hlat : c.latMin < c.latMax) (L : ℝ) :
    Lip L (deriveLat c) ↔ (c.latMax - c.latMin) / (c.tMax - c.tMin) ≤ L := by
  constructor
  · -- 反方向：L 太小则两端点构成反例
    intro hlip
    by_contra hcon
    have hDa : deriveLat c c.tMin = c.latMax := by
      rw [deriveLat, latLinear, decAffine_at_tMin]
      exact clamp_eq_self c.latMinLeLatMax le_rfl
    have hDb : deriveLat c c.tMax = c.latMin := by
      rw [deriveLat, latLinear, decAffine_at_tMax c.tMinLtTMax]
      exact clamp_eq_self le_rfl c.latMinLeLatMax
    have hrpos : (0:ℝ) < c.latMax - c.latMin := sub_pos.mpr hlat
    have hTpos : (0:ℝ) < c.tMax - c.tMin := sub_pos.mpr c.tMinLtTMax
    have hkey := hlip c.tMin c.tMax
    -- 两端点的绝对值差化简为正数差
    have h1 : |c.latMax - c.latMin| = c.latMax - c.latMin := abs_of_pos hrpos
    have h2 : |c.tMin - c.tMax| = c.tMax - c.tMin := by
      rw [abs_sub_comm c.tMin c.tMax]
      exact abs_of_pos hTpos
    rw [hDa, hDb, h1, h2] at hkey
    -- hkey : (latMax − latMin) ≤ L·(tMax − tMin)；而 L < 斜率 ⟹ L·Δ < range，矛盾
    have hcon' : L < (c.latMax - c.latMin) / (c.tMax - c.tMin) := not_le.mp hcon
    have hLt : L * (c.tMax - c.tMin)
        < (c.latMax - c.latMin) / (c.tMax - c.tMin) * (c.tMax - c.tMin) :=
      mul_lt_mul_of_pos_right hcon' hTpos
    have hcanc : (c.latMax - c.latMin) / (c.tMax - c.tMin) * (c.tMax - c.tMin)
        = c.latMax - c.latMin :=
      div_mul_cancel₀ _ (ne_of_gt hTpos)
    linarith
  · -- 正方向：上界的放大
    intro hle x y
    exact le_trans (deriveLat_lip_upper c x y)
      (mul_le_mul_of_nonneg_right hle (abs_nonneg (x - y)))

/-! ### 特例实例化：config 数值（backend/ascend/config.py 第 269-272 行） -/

/-- config 数值的纬度推导配置：
    LATITUDE_T_MIN=-5, LATITUDE_T_MAX=35, LATITUDE_MIN=0, LATITUDE_MAX=80。 -/
def latitudeConfig : LatCfg :=
  ⟨-5, 35, 0, 80, by norm_num, by norm_num⟩

/-- **数值核对定理（V2 判据 sea_level_temp->latitude）**：
    解析斜率 (80−0)/(35−(−5)) = 80/40 = 2，与 equations.json 声明
    `"parent": "sea_level_temp", "child": "latitude", "L": 2.0` 一致。 -/
theorem latitude_config_L :
    (latitudeConfig.latMax - latitudeConfig.latMin)
      / (latitudeConfig.tMax - latitudeConfig.tMin) = 2 := by
  show ((80:ℝ) - 0) / (35 - (-5)) = 2
  norm_num

/-- 锚点核对（V3 判据）：derive_latitude(-5) = 80（极地端）。 -/
theorem deriveLat_anchor_polar : deriveLat latitudeConfig (-5) = 80 := by
  show clamp (0:ℝ) 80 (80 - (-5 - (-5)) / (35 - (-5)) * (80 - 0)) = 80
  rw [sub_self, zero_div, zero_mul, sub_zero]
  exact clamp_eq_self (by norm_num) le_rfl

/-- 锚点核对（V3 判据）：derive_latitude(35) = 0（赤道端）。 -/
theorem deriveLat_anchor_equator : deriveLat latitudeConfig 35 = 0 := by
  show clamp (0:ℝ) 80 (80 - (35 - (-5)) / (35 - (-5)) * (80 - 0)) = 0
  have h : (80:ℝ) - (35 - (-5)) / (35 - (-5)) * (80 - 0) = 0 := by norm_num
  rw [h]
  exact clamp_eq_self le_rfl (by norm_num)

/-! ## 第三节：derive_seasonal_amp（equations.json 边
      temperature→seasonal_amp 与 rainfall→seasonal_amp）

声明：两条边 role=inverse，L=0.65 / 0.002；变量 bounds=[1,30]。
config 键：SEASONAL_AMP_T_MIN=-5, SEASONAL_AMP_T_MAX=35,
SEASONAL_AMP_MAX=28, SEASONAL_AMP_MIN=2,
SEASONAL_AMP_R_REF=2000, SEASONAL_AMP_R_BONUS=4,
SEASONAL_AMP_BOUNDS=(1,30)。
verify_equations.py 判据：V2 L_j 对账 temperature->seasonal_amp 与
rainfall->seasonal_amp；V3 derive_seasonal_amp 界内 + 锚点。

镜像忠实度（derive.py 第 167-178 行）：
- `t_ratio` 不做钳制（线性核裸算），最终输出才钳到 [1,30]；
- `rain_factor` 先钳到 [-0.5, 1.0] 再乘 R_BONUS；
- 两项相加后一次钳制。 -/

/-- 季节振幅推导配置。 -/
structure AmpCfg where
  tMin : ℝ
  tMax : ℝ
  ampMax : ℝ
  ampMin : ℝ
  rRef : ℝ
  rBonus : ℝ
  bLo : ℝ
  bHi : ℝ
  tMinLtTMax : tMin < tMax
  ampMinLeAmpMax : ampMin ≤ ampMax
  rRefPos : 0 < rRef
  rBonusNonneg : 0 ≤ rBonus
  bLoLeBHi : bLo ≤ bHi

/-- 原始雨因子 `(R_REF − rainfall)/R_REF`（不钳制版本）。 -/
noncomputable def rainFactorRaw (rRef : ℝ) (r : ℝ) : ℝ := (rRef - r) / rRef

/-- 雨因子：先钳到 [-0.5, 1.0]（derive.py 第 173-176 行的字面镜像）。 -/
noncomputable def rainFactor (c : AmpCfg) (r : ℝ) : ℝ :=
  clamp (-0.5) 1 (rainFactorRaw c.rRef r)

/-- 基础振幅：低温端取 ampMax、高温端取 ampMin 的递减仿射
    （derive.py 第 167-172 行，t_ratio 不钳制）。 -/
noncomputable def baseAmp (c : AmpCfg) (t : ℝ) : ℝ :=
  decAffine c.tMin c.tMax c.ampMin c.ampMax t

/-- derive_seasonal_amp 镜像定义（derive.py 第 178 行：
    `clamp(base_amp + rain_bonus, 1, 30)`）。 -/
noncomputable def deriveAmp (c : AmpCfg) (t r : ℝ) : ℝ :=
  clamp c.bLo c.bHi (baseAmp c t + rainFactor c r * c.rBonus)

/-- **输出界**：seasonal_amp ∈ SEASONAL_AMP_BOUNDS。
    （variables.seasonal_amp bounds=[1,30] 的承诺；V3「界内」判据） -/
theorem deriveAmp_mem (c : AmpCfg) (t r : ℝ) :
    c.bLo ≤ deriveAmp c t r ∧ deriveAmp c t r ≤ c.bHi :=
  clamp_mem c.bLoLeBHi _

/-- 雨因子的 Lipschitz 常数 = 1/R_REF（线性精确，外层钳制非扩张）。 -/
lemma rainFactorRaw_lip {rRef : ℝ} (hp : 0 < rRef) :
    Lip (1 / rRef) (rainFactorRaw rRef) := by
  intro a b
  unfold rainFactorRaw
  have hd : (rRef - a) / rRef - (rRef - b) / rRef = (b - a) / rRef := by
    field_simp
    ring
  rw [hd, abs_div, abs_of_pos hp]
  have hrw : (1:ℝ) / rRef * |a - b| = |a - b| / rRef := by
    field_simp
  rw [hrw, abs_sub_comm a b]

/-- **温度方向的 Lipschitz**：固定降雨时，seasonal_amp 关于温度的
    Lipschitz 常数 ≤ (AMP_MAX−AMP_MIN)/(AMP_T_MAX−AMP_T_MIN)。
    依据：基础振幅是斜率恒定的线性核（降雨项关于温度为常数偏移，
    直接加 0 贡献），外层钳制非扩张不增常数。
    （V2 对账 temperature->seasonal_amp 的一般形；equations.json L=0.65） -/
theorem deriveAmp_lip_temperature (c : AmpCfg) (r : ℝ) :
    Lip ((c.ampMax - c.ampMin) / (c.tMax - c.tMin)) (fun t => deriveAmp c t r) := by
  unfold deriveAmp
  exact lip_clamp_comp
    (lip_add_const (decAffine_lip c.tMinLtTMax c.ampMinLeAmpMax)) c.bLo c.bHi

/-- **降雨方向的 Lipschitz**：固定温度时，seasonal_amp 关于降雨的
    Lipschitz 常数 ≤ R_BONUS/R_REF。
    依据：雨因子 1/R_REF-Lipschitz × 非负增益 R_BONUS，
    基础振幅关于降雨为常数偏移，外层钳制非扩张。
    （V2 对账 rainfall->seasonal_amp 的一般形；equations.json L=0.002） -/
theorem deriveAmp_lip_rainfall (c : AmpCfg) (t : ℝ) :
    Lip (c.rBonus / c.rRef) (fun r => deriveAmp c t r) := by
  unfold deriveAmp
  have h1 : Lip (1 / c.rRef) (rainFactor c) :=
    lip_clamp_comp (rainFactorRaw_lip c.rRefPos) (-0.5) 1
  have h3 : (1:ℝ) / c.rRef * c.rBonus = c.rBonus / c.rRef := by
    field_simp
  rw [← h3]
  exact lip_clamp_comp (lip_const_add (lip_mul_const h1 c.rBonusNonneg))
    c.bLo c.bHi

/-! ### 特例实例化：config 数值（backend/ascend/config.py 第 275-281 行） -/

/-- config 数值的季节振幅推导配置。 -/
def ampConfig : AmpCfg :=
  ⟨-5, 35, 28, 2, 2000, 4, 1, 30,
    by norm_num, by norm_num, by norm_num, by norm_num, by norm_num⟩

/-- **数值核对定理（V2 判据 temperature->seasonal_amp）**：
    解析斜率 (28−2)/(35−(−5)) = 26/40 = 0.65，与 equations.json
    声明 `"parent": "temperature", "child": "seasonal_amp", "L": 0.65` 一致。 -/
theorem amp_config_L_temp :
    (ampConfig.ampMax - ampConfig.ampMin)
      / (ampConfig.tMax - ampConfig.tMin) = 0.65 := by
  show ((28:ℝ) - 2) / (35 - (-5)) = 0.65
  norm_num

/-- **数值核对定理（V2 判据 rainfall->seasonal_amp）**：
    解析值 4/2000 = 0.002，与 equations.json 声明
    `"parent": "rainfall", "child": "seasonal_amp", "L": 0.002` 一致。 -/
theorem amp_config_L_rain : ampConfig.rBonus / ampConfig.rRef = 0.002 := by
  show (4:ℝ) / 2000 = 0.002
  norm_num

/-- 锚点核对（V3 判据）：derive_seasonal_amp(-5, 2000) = 28（极地大陆性）。 -/
theorem deriveAmp_anchor_cold_continental :
    deriveAmp ampConfig (-5) 2000 = 28 := by
  simp only [deriveAmp, baseAmp, rainFactor, rainFactorRaw, decAffine, ampConfig]
  have h1 : ((28:ℝ) - (-5 - -5) / (35 - -5) * (28 - 2)) = 28 := by norm_num
  have hz : ((2000:ℝ) - 2000) / 2000 = 0 := by norm_num
  rw [h1, hz,
    show clamp (-0.5) (1:ℝ) 0 = 0 from clamp_eq_self (by norm_num) (by norm_num),
    zero_mul, add_zero]
  exact clamp_eq_self (by norm_num : (1:ℝ) ≤ 28) (by norm_num : (28:ℝ) ≤ 30)

/-- 锚点核对（V3 判据）：derive_seasonal_amp(35, 2000) = 2（赤道海洋性）。 -/
theorem deriveAmp_anchor_hot_marine : deriveAmp ampConfig 35 2000 = 2 := by
  simp only [deriveAmp, baseAmp, rainFactor, rainFactorRaw, decAffine, ampConfig]
  have h1 : ((28:ℝ) - (35 - -5) / (35 - -5) * (28 - 2)) = 2 := by norm_num
  have hz : ((2000:ℝ) - 2000) / 2000 = 0 := by norm_num
  rw [h1, hz,
    show clamp (-0.5) (1:ℝ) 0 = 0 from clamp_eq_self (by norm_num) (by norm_num),
    zero_mul, add_zero]
  exact clamp_eq_self (by norm_num : (1:ℝ) ≤ 2) (by norm_num : (2:ℝ) ≤ 30)

/-! ## 第四节：precip_type_for（equations.json 边 temperature→precip_type）

声明：role=structural，离散输出，L 退化为 0。
verify_equations.py 判据：V3 precip_type_for 阈值语义。

编码取舍：引擎实现（derive.py 第 55 行）
`return "snow" if round(temperature, 1) <= 0 else "rain"` 中 round(1)
是事件广播与 UI 显示文案一致的浮点细节；数学规范建模为理想阈值
`snow iff T ≤ 0`。舍入映射 T ↦ round(T,1) 保序（单调不减），故
理想模型的单调性结论在加舍入后依然成立，只是阈值处 ±0.05 的
半开区间归属细节不同——该差异属于引擎对拍测试（V3）的管辖范围。 -/

/-- 降水类型的离散输出域。 -/
inductive PrecipType : Type where
  | snow
  | rain

open scoped Classical in
/-- precip_type_for 的理想阈值模型：snow iff T ≤ 0。 -/
noncomputable def precipTypeFor (t : ℝ) : PrecipType :=
  if t ≤ 0 then PrecipType.snow else PrecipType.rain

/-- **全性**：输出必为 {snow, rain} 之一。
    （离散输出域封闭性；V3「输出 ∈ {snow, rain}」判据） -/
theorem precipTypeFor_cases (t : ℝ) :
    precipTypeFor t = PrecipType.snow ∨ precipTypeFor t = PrecipType.rain := by
  by_cases ht : t ≤ 0
  · refine Or.inl ?_
    rw [precipTypeFor, ite_eq_left ht]
  · refine Or.inr ?_
    rw [precipTypeFor, ite_eq_right ht]

/-- **单调性**：温度不降而判定翻转只允许 snow→rain 方向：
    T₁ ≤ T₂ 且 snow(T₂) 则 snow(T₁)。
    （离散边的单调语义，对应连续边 Antitone 的结构性对应物） -/
theorem precipTypeFor_snow_of_le {t₁ t₂ : ℝ} (hle : t₁ ≤ t₂)
    (hs : precipTypeFor t₂ = PrecipType.snow) :
    precipTypeFor t₁ = PrecipType.snow := by
  by_cases ht : t₁ ≤ 0
  · rw [precipTypeFor, ite_eq_left ht]
  · exfalso
    have ht2 : ¬(t₂ ≤ 0) := fun hc => ht (le_trans hle hc)
    rw [precipTypeFor, ite_eq_right ht2] at hs
    cases hs

/-! ## 第五节：与 LipschitzLayer.hlip 的衔接

LipschitzLayer.error_recurrence_bound（02 篇定理 2.5 连接定理）要求假设
`hlip : ∀ i j x y, j < i → (∀ k < i, k ≠ j → x k = y k) →
   |f i x − f i y| ≤ adj j i * |x j − y j|`
（单父坐标 Lipschitz，02 篇第 53 行「L_{j,i} 关于父 j」）。

本文件的供给关系：
- sea_level_temp→latitude 边：取 adj 0 1 = L_lat = 2.0，
  由 `latitude_config_L`（数值核对）+ `deriveLat_lip_upper`（上界定理）背书；
- temperature→seasonal_amp 边：取 adj = 0.65，
  由 `amp_config_L_temp` + `deriveAmp_lip_temperature` 背书——
  「固定 r 变 t」正是单坐标语义（另一父坐标 rainfall 不动）；
- rainfall→seasonal_amp 边：取 adj = 0.002，
  由 `amp_config_L_rain` + `deriveAmp_lip_rainfall` 背书；
- temperature→precip_type 边：离散输出，L=0 退化
  （01 篇 margin 条件处理，不入 adj 数值表）。

下面的适配器定理展示形状转换：结构方程只读单一父坐标 +
全局 Lip 定理 ⟹ hlip 所需的单坐标界。完整 SCM 实例化
（节点编码、局部性假设、ε 的选取）留给后续 issue。 -/

/-- **形状适配器**：单变量全局 Lip 定理供给 hlip 所需的单坐标形式——
    结构方程 `g ∘ (· j)` 只读第 j 坐标时界照搬。
    注：全局 Lip 下其余坐标一致性前提自动满足，故 hagree 未被使用；
    保留该参数以镜像 hlip 的调用形状。 -/
theorem single_coord_bridge {j : ℕ} {g : ℝ → ℝ} {L : ℝ}
    (hg : Lip L g) (x y : ℕ → ℝ) (_hagree : ∀ k, k ≠ j → x k = y k) :
    |g (x j) - g (y j)| ≤ L * |x j - y j| :=
  hg (x j) (y j)

/-- 具体数值桥接示例：sea_level_temp→latitude 边。
    节点编码 sea_level_temp = 0、latitude = 1，结构方程
    `f 1 x = deriveLat latitudeConfig (x 0)`；hlip 在 (i=1, j=0) 处
    所需的界由声明 L=2 供给（latitude_config_L 背书数值一致性）。 -/
example (x y : ℕ → ℝ) (hagree : ∀ k, k ≠ 0 → x k = y k) :
    |deriveLat latitudeConfig (x 0) - deriveLat latitudeConfig (y 0)|
      ≤ 2 * |x 0 - y 0| := by
  have h2 : Lip 2 (deriveLat latitudeConfig) := by
    rw [← latitude_config_L]
    exact deriveLat_lip_upper latitudeConfig
  exact single_coord_bridge (j := 0) (g := deriveLat latitudeConfig) h2 x y hagree

end AscendLean.Declarations
