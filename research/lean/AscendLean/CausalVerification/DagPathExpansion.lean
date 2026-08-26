import Mathlib

/-!
# 定理 2.5 试点形式化 — 汇聚拓扑的路径和展开与"取最大"反例

出处：`docs/研究理论/世界基座/02-误差传播与反事实.md` 定理 2.5（第 49-69 行）

- 第 55 行：误差递推 `e_i = ε_i + Σ_{j ∈ Pa_G(i)} L_{j,i}·e_j`（干预节点 `e_s = 0`）；
- 第 61-63 行：路径和展开
  `e_t = Σ_{u ∈ Anc(t)\S} ε_u · Σ_{paths u→t} Π_{(a,b)∈path} L_{a,b}`
  —— 对所有路径**求和**，而非取最大（汇聚节点处多父误差相加；链情形退化回引理 2.1）；
- 第 65 行：反例 `X1, X2 → X3`（`L = 1`，`ε₁ = ε₂ = 0.1`）：
  真实误差 `e₃ = ε₃ + 0.2`，单路径界只给 `ε₃ + 0.1 < e₃`。

四个部分：
1. 汇聚闭式（2 父 1 子）：`e₃ = ε₃ + L₁₃·ε₁ + L₂₃·ε₂`；
2. "取最大"严格弱于求和（一般原理 + 数值反例）；
3. n 父星形一般化：`eₙ = εₙ + Σ_{i<n} L_i·ε_i`；
4. 一般 DAG 的路径和展开：带权邻接矩阵 + 路径权重和的递归定义 +
   沿拓扑序（自然数序）强归纳的闭式证明。
-/

open Finset
open scoped BigOperators

namespace AscendLean.CausalVerification

/-! ## 第一部分：汇聚拓扑（X1, X2 → X3）的闭式 -/

/-- 汇聚拓扑的节点：X1、X2 无父，X3 以两者为父（02 篇第 61-65 行） -/
inductive ConvNode where
  | x1 | x2 | x3
deriving DecidableEq, Repr

/-- 汇聚闭式（试点 1）：`e₃ = ε₃ + L₁₃·ε₁ + L₂₃·ε₂`。
递推（02 篇第 55 行）在 2 父 1 子处的路径和展开（第 61-63 行）：
X3 的误差 = 自身模型误差 + 每条父路径（X1→X3、X2→X3）贡献的**和**。 -/
theorem converge_closed_form (e ε : ConvNode → ℝ) (L13 L23 : ℝ)
    (h1 : e ConvNode.x1 = ε ConvNode.x1)
    (h2 : e ConvNode.x2 = ε ConvNode.x2)
    (h3 : e ConvNode.x3 = ε ConvNode.x3 + L13 * e ConvNode.x1 + L23 * e ConvNode.x2) :
    e ConvNode.x3 = ε ConvNode.x3 + L13 * ε ConvNode.x1 + L23 * ε ConvNode.x2 := by
  rw [h3, h1, h2]

/-! ## 第二部分："取最大"不成立（02 篇第 65 行反例） -/

/-- "取最大"不成立的一般原理：两父误差均严格为正时，
`ε₃ + max(ε₁, ε₂) < ε₃ + ε₁ + ε₂`，即取最大界严格小于求和界，
说明汇聚节点处误差必须对父路径**求和**。 -/
theorem max_bound_strictly_weaker {ε1 ε2 ε3 : ℝ} (h1 : 0 < ε1) (h2 : 0 < ε2) :
    ε3 + max ε1 ε2 < ε3 + ε1 + ε2 := by
  by_cases h : ε1 ≤ ε2
  · rw [max_eq_right h]
    linarith
  · have h' : ε2 ≤ ε1 := le_of_lt (lt_of_not_ge h)
    rw [max_eq_left h']
    linarith

/-- 数值反例：`X1, X2 → X3`，`L₁₃ = L₂₃ = 1`，`ε₁ = ε₂ = 0.1`。
真实误差 `e₃ = ε₃ + 0.2`（双父误差相加，02 篇第 65 行）；
"取最大"界只给 `ε₃ + max(0.1, 0.1) = ε₃ + 0.1 < e₃`（严格不等式）。 -/
theorem converge_max_counterexample (ε3 : ℝ) :
    (ε3 + (1 : ℝ) * (0.1 : ℝ) + 1 * (0.1 : ℝ)) = ε3 + (0.2 : ℝ) ∧
      ε3 + max ((0.1 : ℝ)) ((0.1 : ℝ)) < ε3 + (1 : ℝ) * (0.1 : ℝ) + 1 * (0.1 : ℝ) := by
  constructor
  · ring_nf
  · norm_num

/-- 反例的完整实例化：从汇聚闭式出发，`L = 1`、`ε₁ = ε₂ = 0.1` 时
`e₃ = ε₃ + 0.2`，严格大于单路径（取最大）界 `ε₃ + max(ε₁, ε₂) = ε₃ + 0.1`，
故"取最大"不成立（02 篇第 65 行）。 -/
theorem converge_max_via_closed (e ε : ConvNode → ℝ)
    (h1 : e ConvNode.x1 = ε ConvNode.x1)
    (h2 : e ConvNode.x2 = ε ConvNode.x2)
    (h3 : e ConvNode.x3 = ε ConvNode.x3 + 1 * e ConvNode.x1 + 1 * e ConvNode.x2)
    (hε1 : ε ConvNode.x1 = (0.1 : ℝ)) (hε2 : ε ConvNode.x2 = (0.1 : ℝ)) :
    e ConvNode.x3 = ε ConvNode.x3 + (0.2 : ℝ) ∧
      ε ConvNode.x3 + max (ε ConvNode.x1) (ε ConvNode.x2) < e ConvNode.x3 := by
  have hc := converge_closed_form e ε 1 1 h1 h2 h3
  rw [hc]
  constructor
  · rw [hε1, hε2]
    ring_nf
  · rw [hε1, hε2]
    norm_num

/-! ## 第三部分：n 父星形一般化（试点 3） -/

/-- n 父汇聚的闭式（星形一般化）：`eₙ = εₙ + Σ_{i<n} L_i·ε_i`。
父节点 `i < n` 无父（`e_i = ε_i`）；子节点 n 的误差为自身误差
加上所有父贡献之和（02 篇第 55、61-63 行的 Star 特例）。 -/
theorem star_closed_form (n : ℕ) (e ε L : ℕ → ℝ)
    (hleaf : ∀ i, i < n → e i = ε i)
    (hroot : e n = ε n + ∑ i ∈ range n, L i * e i) :
    e n = ε n + ∑ i ∈ range n, L i * ε i := by
  rw [hroot]
  congr 1
  apply Finset.sum_congr rfl
  intro i hi
  rw [hleaf i (Finset.mem_range.mp hi)]

/-! ## 第四部分：一般 DAG 的路径和展开（试点 4） -/

/-- 路径权重和 `W u t`：从 u 到 t 的所有有向路径的权重和
    `W u t = Σ_{paths u→t} Π_{(a,b)∈path} L_{a,b}`（02 篇第 63 行）；
    `u = t` 时记空路径权重 1（即"自身误差 ε_t 直接计入"）。
    实现：节点 = ℕ（拓扑序 = 自然数序，父索引 < 子索引）；
    带权邻接 `adj j i = L_{j,i}`（无边为 0）；沿终点 t 做 well-founded 递归
    （`∑ j : Fin t` 使成员证明在类型里，规避 `Finset.sum` 无法向递归调用
    传递 `j < t` 约束的问题）。 -/
noncomputable def pathWeight (adj : ℕ → ℕ → ℝ) (u t : ℕ) : ℝ :=
  WellFounded.fix (C := fun _ : ℕ => ℕ → ℝ) (measure (fun t : ℕ => t)).wf
    (fun t rec u => if u = t then 1 else ∑ j : Fin t, adj j.1 t * rec j.1 (Fin.isLt j) u) t u

/-- `pathWeight` 的展开方程（well-founded 定义不自动生成，手动声明） -/
theorem pathWeight_eq (adj : ℕ → ℕ → ℝ) (u t : ℕ) :
    pathWeight adj u t = if u = t then 1 else ∑ j : Fin t, adj j.1 t * pathWeight adj u j.1 := by
  unfold pathWeight
  rw [WellFounded.fix_eq]

@[simp] lemma pathWeight_self (adj : ℕ → ℕ → ℝ) (u : ℕ) : pathWeight adj u u = 1 := by
  rw [pathWeight_eq]
  simp

/-- `pathWeight` 的递归方程（u ≠ t 时沿入边展开，02 篇第 55 行递推的镜像） -/
lemma pathWeight_rec (adj : ℕ → ℕ → ℝ) {u t : ℕ} (h : u ≠ t) :
    pathWeight adj u t = ∑ j ∈ range t, adj j t * pathWeight adj u j := by
  rw [pathWeight_eq]
  simp [h]
  rw [Fin.sum_univ_eq_sum_range (fun i : ℕ => adj i t * pathWeight adj u i) t]

/-- 反向（u > j）无路径：`W u j = 0`（沿拓扑序无"倒退"边） -/
lemma pathWeight_eq_zero_of_gt (adj : ℕ → ℕ → ℝ) {u j : ℕ} (h : u > j) :
    pathWeight adj u j = 0 := by
  induction' j using Nat.strong_induction_on with j ih
  rw [pathWeight_rec adj (by omega : u ≠ j)]
  apply Finset.sum_eq_zero
  intro k hk
  have hkj : k < j := Finset.mem_range.mp hk
  have huk : u > k := lt_trans hkj h
  rw [ih k hkj huk]
  ring

/-- 三角换序：`Σ_{j<t} Σ_{u<j} a j u = Σ_{u<t} Σ_{j<t, u<j} a j u` -/
lemma sum_swap_triangle (t : ℕ) (a : ℕ → ℕ → ℝ) :
    (∑ j ∈ range t, ∑ u ∈ range j, a j u) =
      ∑ u ∈ range t, ∑ j ∈ range t, if u < j then a j u else 0 := by
  calc
    (∑ j ∈ range t, ∑ u ∈ range j, a j u)
        = ∑ j ∈ range t, ∑ u ∈ range t, if u < j then a j u else 0 := by
          apply Finset.sum_congr rfl
          intro j hj
          have hj' : j < t := Finset.mem_range.mp hj
          rw [← Finset.sum_filter]
          congr 1
          ext u
          simp [Finset.mem_filter, Finset.mem_range]
          omega
    _ = ∑ u ∈ range t, ∑ j ∈ range t, if u < j then a j u else 0 := by
          rw [Finset.sum_comm]

/-- `W` 的拆解：u < t 时 `W u t = adj u t + Σ_{j: u<j<t} adj j t · W u j`
    （`j = u` 项即"边 u→t 的直接贡献"，`j < u` 项无路径为 0） -/
lemma pathWeight_split (adj : ℕ → ℕ → ℝ) {u t : ℕ} (h : u < t) :
    pathWeight adj u t =
      adj u t + ∑ j ∈ (range t).filter (fun j => u < j), adj j t * pathWeight adj u j := by
  rw [pathWeight_rec adj (by omega : u ≠ t)]
  have hsplit : (∑ j ∈ range t, adj j t * pathWeight adj u j) =
      (∑ j ∈ (range t).filter (fun j => u < j), adj j t * pathWeight adj u j) +
        (∑ j ∈ range (u + 1), adj j t * pathWeight adj u j) := by
    rw [← Finset.sum_filter_add_sum_filter_not
      (s := range t) (p := fun j => u < j) (f := fun j => adj j t * pathWeight adj u j)]
    congr 1
    rw [show (range t).filter (fun j => ¬ u < j) = range (u + 1) by
      ext j
      simp [Finset.mem_filter, not_lt]
      intro hj
      omega]
  rw [hsplit]
  rw [Finset.sum_range_succ]
  have hz : (∑ j ∈ range u, adj j t * pathWeight adj u j) = 0 := by
    apply Finset.sum_eq_zero
    intro j hj
    have hju : j < u := Finset.mem_range.mp hj
    rw [pathWeight_eq_zero_of_gt adj (by omega : u > j)]
    ring
  rw [hz]
  rw [pathWeight_self]
  ring

/-- 尾段贡献：`Σ_{j<t, u<j} adj j t · W u j = W u t - adj u t` -/
lemma tail_contribution (adj : ℕ → ℕ → ℝ) {u t : ℕ} (h : u < t) :
    (∑ j ∈ range t, if u < j then adj j t * pathWeight adj u j else 0) =
      pathWeight adj u t - adj u t := by
  rw [← Finset.sum_filter]
  rw [pathWeight_split adj h]
  ring

/-- 递推求和的闭式代数核心：
    `Σ_j adj j t·ε_j + Σ_j Σ_{u<j} adj j t·ε_u·W u j = Σ_u ε_u·W u t` -/
lemma expansion_sum (adj : ℕ → ℕ → ℝ) (ε : ℕ → ℝ) (t : ℕ) :
    (∑ j ∈ range t, adj j t * ε j) +
        (∑ j ∈ range t, ∑ u ∈ range j, adj j t * (ε u * pathWeight adj u j)) =
      ∑ u ∈ range t, ε u * pathWeight adj u t := by
  calc
    (∑ j ∈ range t, adj j t * ε j) +
        (∑ j ∈ range t, ∑ u ∈ range j, adj j t * (ε u * pathWeight adj u j))
        = (∑ u ∈ range t, adj u t * ε u) +
            ∑ u ∈ range t, (ε u * ∑ j ∈ range t, if u < j then adj j t * pathWeight adj u j else 0) := by
          rw [sum_swap_triangle t (fun j u => adj j t * (ε u * pathWeight adj u j))]
          rw [show (∑ j ∈ range t, adj j t * ε j) = ∑ u ∈ range t, adj u t * ε u by rfl]
          congr 1
          apply Finset.sum_congr rfl
          intro u hu
          rw [Finset.mul_sum]
          apply Finset.sum_congr rfl
          intro j hj
          by_cases h : u < j
          · simp [h]
            ring
          · simp [h]
    _ = (∑ u ∈ range t, adj u t * ε u) + ∑ u ∈ range t, ε u * (pathWeight adj u t - adj u t) := by
          congr 1
          apply Finset.sum_congr rfl
          intro u hu
          congr 1
          exact tail_contribution adj (Finset.mem_range.mp hu)
    _ = (∑ u ∈ range t, adj u t * ε u) +
          ((∑ u ∈ range t, ε u * pathWeight adj u t) - ∑ u ∈ range t, ε u * adj u t) := by
          congr 1
          rw [← Finset.sum_sub_distrib]
          apply Finset.sum_congr rfl
          intro u hu
          rw [mul_sub]
    _ = ∑ u ∈ range t, ε u * pathWeight adj u t := by
          rw [show (∑ u ∈ range t, adj u t * ε u) = ∑ u ∈ range t, ε u * adj u t by
            apply Finset.sum_congr rfl
            intro u hu
            ring]
          ring

/-- **一般 DAG 的路径和展开**（02 篇定理 2.5，第 55、61-63 行的代数内核）：
    节点 = ℕ（拓扑序 = 自然数序，父索引 < 子索引）；
    带权邻接 `adj j i = L_{j,i}`（无边为 0）；误差递推
    `e i = ε i + Σ_{j < i} adj j i · e j`（第 55 行）；
    闭式：`e t = ε t + Σ_{u < t} ε u · W u t`（第 61-63 行）——
    每个祖先 u 的误差经"从 u 到 t 的所有路径权重和"`W u t` 放大后**相加**（而非取最大）。
    干预节点 s 的编码：`ε s = 0` 且入边 `adj j s = 0`（递推给出 `e s = 0`，
    闭式中 s 的贡献项 `ε s · W s t` 自动消失，对应文档 `Σ_{u ∈ Anc(t) \ S}`）。 -/
theorem dag_path_expansion (adj : ℕ → ℕ → ℝ) (e ε : ℕ → ℝ)
    (hrec : ∀ i, e i = ε i + ∑ j ∈ range i, adj j i * e j) (t : ℕ) :
    e t = ε t + ∑ u ∈ range t, ε u * pathWeight adj u t := by
  induction' t using Nat.strong_induction_on with t ih
  rw [hrec t]
  congr 1
  trans ∑ j ∈ range t, adj j t * (ε j + ∑ u ∈ range j, ε u * pathWeight adj u j)
  · apply Finset.sum_congr rfl
    intro j hj
    rw [ih j (Finset.mem_range.mp hj)]
  · calc
      ∑ j ∈ range t, adj j t * (ε j + ∑ u ∈ range j, ε u * pathWeight adj u j)
          = ∑ j ∈ range t, (adj j t * ε j + ∑ u ∈ range j, adj j t * (ε u * pathWeight adj u j)) := by
            apply Finset.sum_congr rfl
            intro j hj
            rw [mul_add, Finset.mul_sum]
      _ = (∑ j ∈ range t, adj j t * ε j) +
            (∑ j ∈ range t, ∑ u ∈ range j, adj j t * (ε u * pathWeight adj u j)) := by
            rw [Finset.sum_add_distrib]
      _ = ∑ u ∈ range t, ε u * pathWeight adj u t := by
            exact expansion_sum adj ε t

/-- 干预节点的误差为 0（02 篇第 55 行 `e_s = 0`（干预节点））：
    干预后 s 的模型误差 `ε s = 0` 且入边全部断开（`adj j s = 0`，`j < s`），
    递推直接给出 `e s = 0`。 -/
theorem do_intervention_zero (adj : ℕ → ℕ → ℝ) (e ε : ℕ → ℝ)
    (hrec : ∀ i, e i = ε i + ∑ j ∈ range i, adj j i * e j)
    {s : ℕ} (hε : ε s = 0) (hadj : ∀ j, j < s → adj j s = 0) :
    e s = 0 := by
  rw [hrec s, hε]
  simp
  apply Finset.sum_eq_zero
  intro j hj
  have hjs : j < s := Finset.mem_range.mp hj
  rw [hadj j hjs]
  ring

/-! ## 第五部分：闭环演示 — 汇聚图（X1, X2 → X3）作为一般 DAG 的特例 -/

/-- 汇聚图邻接：只有边 1→3（L13）与 2→3（L23），其余为 0 -/
def convergeAdj (L13 L23 : ℝ) : ℕ → ℕ → ℝ
  | 1, 3 => L13
  | 2, 3 => L23
  | _, _ => 0

/-- 小值（终点 ≤ 2）：无 3 相关边时，`W u t = if u = t then 1 else 0` -/
lemma weight_small_no_edges (adj : ℕ → ℕ → ℝ) (u t : ℕ) (_hu : u ≤ 2) (ht : t ≤ 2)
    (hno : ∀ ⦃j i⦄, i ≤ 2 → adj j i = 0) :
    pathWeight adj u t = if u = t then 1 else 0 := by
  induction' t using Nat.strong_induction_on with t ih
  by_cases h : u = t
  · simp [h]
  · rw [pathWeight_rec adj h]
    simp [h]
    apply Finset.sum_eq_zero
    intro j hj
    have hjt : j < t := Finset.mem_range.mp hj
    have hw := ih j hjt (by omega : j ≤ 2)
    rw [hw]
    have hadj0 : adj j t = 0 := hno (by omega : t ≤ 2)
    rw [hadj0]
    ring

lemma convergeAdj_weight_small (L13 L23 : ℝ) (_u t : ℕ) (_hu : _u ≤ 2) (ht : t ≤ 2) :
    pathWeight (convergeAdj L13 L23) _u t = if _u = t then 1 else 0 := by
  apply weight_small_no_edges (convergeAdj L13 L23) _u t _hu ht
  intro j i hi
  interval_cases i <;> simp [convergeAdj]

/-- 汇聚图中的路径权重：`W 1 3 = L13`、`W 2 3 = L23`、`W 0 3 = 0`
    （X3 的祖先只有 X1、X2；路径 X1→X3 权重 L13，X2→X3 权重 L23） -/
lemma convergeAdj_weight_13 (L13 L23 : ℝ) : pathWeight (convergeAdj L13 L23) 1 3 = L13 := by
  rw [pathWeight_eq]
  rw [Fin.sum_univ_eq_sum_range (fun i : ℕ => convergeAdj L13 L23 i 3 * pathWeight (convergeAdj L13 L23) 1 i) 3]
  rw [Finset.sum_range_succ, Finset.sum_range_succ, Finset.sum_range_succ]
  rw [convergeAdj_weight_small L13 L23 1 0 (by norm_num) (by norm_num),
      convergeAdj_weight_small L13 L23 1 1 (by norm_num) (by norm_num),
      convergeAdj_weight_small L13 L23 1 2 (by norm_num) (by norm_num)]
  norm_num [convergeAdj]

lemma convergeAdj_weight_23 (L13 L23 : ℝ) : pathWeight (convergeAdj L13 L23) 2 3 = L23 := by
  rw [pathWeight_eq]
  rw [Fin.sum_univ_eq_sum_range (fun i : ℕ => convergeAdj L13 L23 i 3 * pathWeight (convergeAdj L13 L23) 2 i) 3]
  rw [Finset.sum_range_succ, Finset.sum_range_succ, Finset.sum_range_succ]
  rw [convergeAdj_weight_small L13 L23 2 0 (by norm_num) (by norm_num),
      convergeAdj_weight_small L13 L23 2 1 (by norm_num) (by norm_num),
      convergeAdj_weight_small L13 L23 2 2 (by norm_num) (by norm_num)]
  norm_num [convergeAdj]

lemma convergeAdj_weight_03 (L13 L23 : ℝ) : pathWeight (convergeAdj L13 L23) 0 3 = 0 := by
  rw [pathWeight_eq]
  rw [Fin.sum_univ_eq_sum_range (fun i : ℕ => convergeAdj L13 L23 i 3 * pathWeight (convergeAdj L13 L23) 0 i) 3]
  rw [Finset.sum_range_succ, Finset.sum_range_succ, Finset.sum_range_succ]
  rw [convergeAdj_weight_small L13 L23 0 0 (by norm_num) (by norm_num),
      convergeAdj_weight_small L13 L23 0 1 (by norm_num) (by norm_num),
      convergeAdj_weight_small L13 L23 0 2 (by norm_num) (by norm_num)]
  norm_num [convergeAdj]

/-- **闭环演示**：`dag_path_expansion` 在汇聚图（X1, X2 → X3）上的特例
    = 第一部分 `converge_closed_form`：`e₃ = ε₃ + L₁₃·ε₁ + L₂₃·ε₂`
    （02 篇第 61-63 行：对祖先 {X1, X2} 的路径贡献求和）。
    这验证了"路径和展开"定义在具体图上给出的正是递推的闭式。 -/
theorem dag_converge_closed (L13 L23 : ℝ) (e ε : ℕ → ℝ)
    (hrec : ∀ i, e i = ε i + ∑ j ∈ range i, convergeAdj L13 L23 j i * e j) :
    e 3 = ε 3 + L13 * ε 1 + L23 * ε 2 := by
  have hd := dag_path_expansion (convergeAdj L13 L23) e ε hrec 3
  rw [hd]
  rw [Finset.sum_range_succ, Finset.sum_range_succ, Finset.sum_range_succ]
  rw [convergeAdj_weight_03 L13 L23, convergeAdj_weight_13 L13 L23, convergeAdj_weight_23 L13 L23]
  ring

end AscendLean.CausalVerification