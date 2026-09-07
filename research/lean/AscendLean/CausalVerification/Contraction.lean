import Mathlib

/-!
# 收缩链界（02 篇推论 2.2 / 推论 2.3）

文档出处：`docs/研究理论/世界基座/02-误差传播与反事实.md`

- 推论 2.2：等 Lipschitz 链（Λ_j ≡ Λ）三档行为，
  0≤Λ<1 收缩有界 e_τ ≤ ε/(1−Λ)；Λ=1 线性累积；Λ>1 指数增长 ε·Λ^τ/(Λ−1)。
- 推论 2.3：收缩链两条律，外推误差有上界 ε/(1−Λ) 且随 τ 单调上升；
  初值不确定性 b₀ 的贡献按 b₀·Λ^τ 衰减，遗忘到 η 只需对数深度。

与 `LipschitzLayer.lean` 第六节（命题 2.1 的链式特例）的关系：那里给出一般
链式闭式；本文件取"等 Lipschitz、每步误差恒为 ε"的特化，退化为几何和，直接用
Mathlib `Finset.geom_sum_eq` 得闭式；递推衔接见 `chainErr_recurrence`。
两文件相互独立（无代码依赖），关系仅为数学内容的特化。

符号约定：代码局部变量 `L`、`δ₀`、`τ` 分别对应文档的 `Λ`、`b₀`、`n`（链长）；
按文档语境取 0 < L（负 L 不在引擎场景内）；ε 为每步误差幅度，非负。
-/

open Finset
open scoped BigOperators

namespace AscendLean.CausalVerification

/-! ### 几何和闭式（推论 2.2 / 2.3 的共同前置） -/

/-- **推论 2.2 前置**：等比和闭式 Σ_{i∈range τ} Λ^i = (1−Λ^τ)/(1−Λ)。 -/
theorem geom_sum_doc (L : ℝ) (hL : L ≠ 1) (τ : ℕ) :
    ∑ i ∈ range τ, L ^ i = (1 - L ^ τ) / (1 - L) := by
  rw [geom_sum_eq hL τ]
  field_simp
  ring

/-- **推论 2.2/2.3 的共同对象**：等 Lipschitz 链 τ 步累积误差 e_τ = Σ_{i∈range τ} ε·Λ^i。 -/
def chainErr (ε L : ℝ) (τ : ℕ) : ℝ := ε * ∑ i ∈ range τ, L ^ i

/-- 与命题 2.1（LipschitzLayer.lean 第六节链特例）的递推衔接：e_{τ+1} = ε + L·e_τ（等 Lipschitz 特化）。 -/
theorem chainErr_recurrence (ε L : ℝ) (τ : ℕ) :
    chainErr ε L (τ + 1) = ε + L * chainErr ε L τ := by
  rcases eq_or_ne L 1 with rfl | hL
  · simp only [chainErr, one_pow, Finset.sum_const, Finset.card_range]
    ring
  · unfold chainErr
    rw [geom_sum_doc L hL, geom_sum_doc L hL]
    field_simp
    rw [pow_succ]
    ring

/-! ### 推论 2.2：等 Lipschitz 三档行为 -/

/-- **推论 2.2 第一档（收缩链）**：0<L<1 时 e_τ ≤ ε/(1−L)，与链长 τ 无关。 -/
theorem err_bound_contractive (ε L : ℝ) (hε : 0 ≤ ε) (hL0 : 0 < L) (hL1 : L < 1) (τ : ℕ) :
    chainErr ε L τ ≤ ε / (1 - L) := by
  have hpos : 0 < 1 - L := by linarith
  have hpow : 0 ≤ L ^ τ := pow_nonneg hL0.le τ
  have hsum : ∑ i ∈ range τ, L ^ i ≤ 1 / (1 - L) := by
    rw [geom_sum_doc L (ne_of_lt hL1) τ]
    exact (div_le_div_iff_of_pos_right hpos).mpr (by linarith)
  calc chainErr ε L τ = ε * ∑ i ∈ range τ, L ^ i := rfl
    _ ≤ ε * (1 / (1 - L)) := mul_le_mul_of_nonneg_left hsum hε
    _ = ε / (1 - L) := by ring

/-- **推论 2.2 第二档（临界）**：L=1 时 e_τ = τ·ε（线性累积）。 -/
theorem err_bound_unit (ε : ℝ) (τ : ℕ) : chainErr ε 1 τ = τ * ε := by
  unfold chainErr
  simp only [one_pow]
  rw [Finset.sum_const, Finset.card_range]
  ring

/-- **推论 2.2 第三档（发散链）**：1<L 时 e_τ ≤ ε·L^τ/(L−1)，随深度指数增长。 -/
theorem err_bound_divergent (ε L : ℝ) (hε : 0 ≤ ε) (hL1 : 1 < L) (τ : ℕ) :
    chainErr ε L τ ≤ ε * L ^ τ / (L - 1) := by
  have hpos : 0 < L - 1 := by linarith
  have hsum : ∑ i ∈ range τ, L ^ i ≤ L ^ τ / (L - 1) := by
    rw [geom_sum_eq (ne_of_gt hL1) τ]
    exact (div_le_div_iff_of_pos_right hpos).mpr (by linarith)
  calc chainErr ε L τ = ε * ∑ i ∈ range τ, L ^ i := rfl
    _ ≤ ε * (L ^ τ / (L - 1)) := mul_le_mul_of_nonneg_left hsum hε
    _ = ε * L ^ τ / (L - 1) := by ring

/-! ### 推论 2.3 · 外推误差饱和 -/

/-- e_τ 关于 τ 单调上升（只需 ε≥0、L≥0；收缩情形即推论 2.3 要求的单调性）。 -/
theorem err_monotone (ε L : ℝ) (hε : 0 ≤ ε) (hL0 : 0 ≤ L) :
    Monotone fun τ => chainErr ε L τ :=
  monotone_nat_of_le_succ fun τ => by
    unfold chainErr
    rw [Finset.sum_range_succ, mul_add]
    exact le_add_of_nonneg_right (mul_nonneg hε (pow_nonneg hL0 τ))

/-- **推论 2.3 · 外推误差饱和**：e_τ 随 τ 单调上升且具有与深度无关的
    上界 ε/(1−L)。该上界给出 ε/(1−L) ≤ η 这一充分条件。 -/
theorem err_saturation (ε L : ℝ) (hε : 0 ≤ ε) (hL0 : 0 < L) (hL1 : L < 1) :
    (∀ τ, chainErr ε L τ ≤ ε / (1 - L)) ∧ Monotone fun τ => chainErr ε L τ :=
  ⟨fun τ => err_bound_contractive ε L hε hL0 hL1 τ, err_monotone ε L hε hL0.le⟩

/-! ### 推论 2.3 · 初始不确定性遗忘 -/

/-- 初值扰动沿等 Lipschitz 链传播：u₀=δ₀、u_{k+1}=L·u_k ⟹ u_τ = δ₀·L^τ。 -/
theorem init_contrib (L δ₀ : ℝ) (u : ℕ → ℝ) (h0 : u 0 = δ₀) (hrec : ∀ k, u (k + 1) = L * u k)
    (τ : ℕ) : u τ = δ₀ * L ^ τ := by
  induction τ with
  | zero => rw [h0, pow_zero, mul_one]
  | succ n ih => rw [hrec n, ih, pow_succ]; ring

/-- **衰减刻画**（推论 2.3 的对数形式；对一切 L>0 成立）：
    δ₀·L^τ ≤ η ⟺ τ·log L ≤ log(η/δ₀)。乘法形态不含除法，无符号翻转问题。 -/
theorem contrib_decay_iff (τ : ℕ) (L δ₀ η : ℝ) (hL0 : 0 < L) (hδ : 0 < δ₀) (hη : 0 < η) :
    δ₀ * L ^ τ ≤ η ↔ (τ : ℝ) * Real.log L ≤ Real.log (η / δ₀) := by
  have hpow : 0 < δ₀ * L ^ τ := mul_pos hδ (pow_pos hL0 τ)
  constructor
  · intro h
    have h1 : Real.log (δ₀ * L ^ τ) ≤ Real.log η := (Real.log_le_log_iff hpow hη).mpr h
    rw [Real.log_mul hδ.ne' (pow_pos hL0 τ).ne', Real.log_pow] at h1
    rw [Real.log_div hη.ne' hδ.ne']
    linarith
  · intro h
    rw [Real.log_div hη.ne' hδ.ne'] at h
    have h1 : Real.log (δ₀ * L ^ τ) ≤ Real.log η := by
      rw [Real.log_mul hδ.ne' (pow_pos hL0 τ).ne', Real.log_pow]
      linarith
    exact (Real.log_le_log_iff hpow hη).mp h1

/-- **推论 2.3 · 初始不确定性遗忘**（阈值形式）：
    0<L<1 时初值贡献衰减到 η 只需 τ ≥ log(δ₀/η)/log(1/L)。
    符号说明：0<L<1 ⇒ log L<0、log(1/L)=−log L>0，故此阈值分子分母同号为正。
    历史注：文档原稿曾写作 log(δ₀/η)/log L——分母为负使阈值为负、形同虚设，
    系笔误；已由本形式化发现并在文档修正为除以 log(1/L)=|log L|
    （等价地 log(η/δ₀)/log L）。本定理采用修正后形式。 -/
theorem forget_by_depth {τ : ℕ} {L δ₀ η : ℝ} (hL0 : 0 < L) (hLt : L < 1)
    (hδ : 0 < δ₀) (hη : 0 < η)
    (hτ : Real.log (δ₀ / η) / Real.log (1 / L) ≤ (τ : ℝ)) :
    δ₀ * L ^ τ ≤ η := by
  have hsign : Real.log L < 0 := Real.log_neg hL0 hLt
  have hnum : Real.log (δ₀ / η) = -Real.log (η / δ₀) := by
    rw [Real.log_div hδ.ne' hη.ne', Real.log_div hη.ne' hδ.ne']
    ring
  rw [contrib_decay_iff τ L δ₀ η hL0 hδ hη]
  have hτ' : Real.log (η / δ₀) / Real.log L ≤ (τ : ℝ) := by
    have h1 : Real.log (1 / L) = -Real.log L := by rw [one_div, Real.log_inv]
    rw [hnum, h1, neg_div_neg_eq] at hτ
    exact hτ
  exact (div_le_iff_of_neg hsign).mp hτ'

/-- 除法形式的完整刻画（收缩链 0<L<1）：等价于乘法形态的 iff 两端同除以负数 log L，
    不等号同步翻转后形态不变，这正是早期文档笔误的根源
    （该笔误已修正，见 `forget_by_depth` 历史注）。
    任务所述 `log L ≠ 0` 条件在 0<L<1 下自动成立（log L<0）；若 L>1 则方向翻转为
    τ ≤ log(η/δ₀)/log L，故本刻画按文档语境限定收缩链。 -/
theorem forget_iff_depth {τ : ℕ} {L δ₀ η : ℝ} (hL0 : 0 < L) (hLt : L < 1)
    (hδ : 0 < δ₀) (hη : 0 < η) :
    δ₀ * L ^ τ ≤ η ↔ Real.log (η / δ₀) / Real.log L ≤ (τ : ℝ) :=
  (contrib_decay_iff τ L δ₀ η hL0 hδ hη).trans
    (div_le_iff_of_neg (Real.log_neg hL0 hLt)).symm

end AscendLean.CausalVerification
