import Mathlib

/-!
# 可行性试写 — 链式误差传播（02 篇引理 2.1 链式特例）

e₀ = ε₀；e_{i+1} = ε_{i+1} + L_i · e_i
闭式（统一形式）：e_{n+1} = Σ_{i=0..n+1} ε_i · (Π_{j=i..n} L_j)
其中 i = n+1 项为空积（=1），即末步自身误差 ε_{n+1}。

这是定理 2.5 路径和展开在"链"上的退化形态：
单父节点下"所有路径求和"只剩一条路径，乘积项即路径权重。
-/

open Finset
open scoped BigOperators

namespace AscendLean

/-- 两步链的直算（热身）：e₂ = ε₂ + L₁·ε₁ + L₁·L₀·ε₀ -/
theorem chain_two (e ε L : ℕ → ℝ)
    (h0 : e 0 = ε 0)
    (hrec : ∀ i : ℕ, e (i + 1) = ε (i + 1) + L i * e i) :
    e 2 = ε 2 + L 1 * ε 1 + L 1 * L 0 * ε 0 := by
  rw [hrec 1, hrec 0, h0]
  ring

/-- 链式误差传播闭式：e_{n+1} = Σ_{i=0..n+1} ε_i · Π_{j=i..n} L_j -/
theorem chain_error_closed_form (n : ℕ) (e ε L : ℕ → ℝ)
    (h0 : e 0 = ε 0)
    (hrec : ∀ i : ℕ, e (i + 1) = ε (i + 1) + L i * e i) :
    e (n + 1) = ∑ i ∈ range (n + 2), ε i * ∏ j ∈ Icc i n, L j := by
  induction' n with n ih
  · rw [hrec 0, h0]
    rw [Finset.sum_range_succ, Finset.sum_range_succ]
    simp
    ring
  · rw [hrec (n + 1), ih]
    rw [Finset.mul_sum]
    nth_rewrite 2 [Finset.sum_range_succ]
    simp
    rw [add_comm]
    congr 1
    · apply Finset.sum_congr rfl
      intro i hi
      rw [Finset.prod_Icc_succ_top
        (Nat.lt_succ_iff.mp (Finset.mem_range.mp hi))]
      ring

end AscendLean