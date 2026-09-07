import Mathlib

/-!
# 空间核的 Lipschitz 界与收缩条件（02 篇 §5）

出处：`docs/研究理论/世界基座/02-误差传播与反事实.md` §5。

设线性空间核 `y(s) = Σ_{σ ∈ S} w_σ · x(s + σ)`，其中 `S` 为有限偏移集，
`w : ℤ → ℝ` 为核权重，`x : ℤ → ℝ` 为场值。本文件证明：

1. 上确界范数意义下的逐点 Lipschitz 界：
   若 ∀s, |x s − y s| ≤ D，则 ∀s, |kernel S w x s − kernel S w y s| ≤ (Σ_σ |w_σ|) · D；
2. 收缩条件：Σ_σ |w_σ| < 1 时，核把距离 D 严格压缩——这正是
   02 篇 §5"核的绝对权重和 < 1 ⟹ 严格收缩"的表述。
（clamp 的 1-Lipschitz 性质见 `Declarations.lean`；边界算子属于
空间机制声明的一部分，不在此建模。）
-/

open Finset
open scoped BigOperators

namespace AscendLean.CausalVerification

/-- 有限偏移集上的线性空间核。 -/
def kernel (S : Finset ℤ) (w : ℤ → ℝ) (x : ℤ → ℝ) (s : ℤ) : ℝ :=
  ∑ σ ∈ S, w σ * x (s + σ)

/-- **核的逐点 Lipschitz 界**：逐点差被绝对权重和放大。 -/
theorem kernel_lipschitz {S : Finset ℤ} {w : ℤ → ℝ} {x y : ℤ → ℝ} {D : ℝ}
    (hD : ∀ s : ℤ, |x s - y s| ≤ D) (_hDnn : 0 ≤ D) :
    ∀ s : ℤ, |kernel S w x s - kernel S w y s| ≤ (∑ σ ∈ S, |w σ|) * D := by
  intro s
  calc
    |kernel S w x s - kernel S w y s|
        = |∑ σ ∈ S, (w σ * x (s + σ) - w σ * y (s + σ))| := by
            rw [kernel, kernel, ← Finset.sum_sub_distrib]
    _ ≤ ∑ σ ∈ S, |w σ * x (s + σ) - w σ * y (s + σ)| := abs_sum_le_sum_abs _ _
    _ = ∑ σ ∈ S, |w σ| * |x (s + σ) - y (s + σ)| := by
            apply Finset.sum_congr rfl
            intro σ hσ
            rw [← mul_sub, abs_mul]
    _ ≤ ∑ σ ∈ S, |w σ| * D := by
            apply Finset.sum_le_sum
            intro σ hσ
            exact mul_le_mul_of_nonneg_left (hD (s + σ)) (abs_nonneg (w σ))
    _ = (∑ σ ∈ S, |w σ|) * D := by
            rw [Finset.sum_mul]

/-- **核的严格收缩**：绝对权重和小于 1 时，正距离被严格压缩。 -/
theorem kernel_contraction {S : Finset ℤ} {w : ℤ → ℝ} {x y : ℤ → ℝ} {D : ℝ}
    (hD : ∀ s : ℤ, |x s - y s| ≤ D) (hDpos : 0 < D)
    (hw : (∑ σ ∈ S, |w σ|) < 1) :
    ∀ s : ℤ, |kernel S w x s - kernel S w y s| < D := by
  intro s
  have hlip := kernel_lipschitz (S := S) (w := w) hD (le_of_lt hDpos) s
  exact lt_of_le_of_lt hlip (by
    simpa [mul_comm] using (mul_lt_of_lt_one_left hDpos hw))

end AscendLean.CausalVerification
