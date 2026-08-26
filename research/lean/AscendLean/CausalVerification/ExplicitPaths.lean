import Mathlib
import AscendLean.CausalVerification.DagPathExpansion

/-!
# 显式路径枚举 — W u t 的路径对象语义（02 篇第 63 行）

出处：`docs/研究理论/因果理论验证/02-误差传播与反事实.md` 定理 2.5 第 61-63 行：
`e_t = Σ_{u ∈ Anc(t)\S} ε_u · Σ_{paths u→t} Π_{(a,b)∈path} L_{a,b}`。
`DagPathExpansion.pathWeight` 以递归给出了 W（语义等价但无显式路径对象）；
本文件补上"具体路径"层并证明**枚举求和 = 递归 W**：

1. `seqWeight`：节点序列的边权乘积——显式路径（严格递增节点序列，
   由中间节点集唯一确定）的权重；`seqWeight_mid_append` 为 peel 代数核心；
2. `sort_insert_max`：最大元插末排序引理。证明取**插入排序作第二表示**
   （库给 `pairwise_insertionSort` 成对有序、`perm_insertionSort` 置换），
   两次 `List.Perm.eq_of_pairwise'` 唯一性收口（L ≃ 插入排序形 ≃ 目标 append 形）；
3. `pathEnumSum`：全部路径的权重和——按中间节点集 S ⊆ Icc (u+1) (t−1)
   枚举（powerset 天然有限），约定与 W 一致：u=t 记 1、u>t 记 0；
4. `enumSum_peel_gen`：按最大中间元 c 分块——`Finset.sum_powerset_insert`
   剥离 + 最大元插末排序引理 + 内层恰为 `pathEnumSum u (c+1)`；
5. 主定理 `pathEnumSum_eq_pathWeight`：`pathWeight_split` 对齐递推，强归纳收口。
-/

open Finset
open scoped BigOperators

namespace AscendLean.CausalVerification

/-! ## 第一节：显式路径的权重（节点序列的边权乘积） -/

/-- 节点序列的边权乘积：相邻对逐一相乘；少于两个节点时空积 = 1
    （空路径约定与 `pathWeight_self` 一致）。 -/
noncomputable def seqWeight (adj : ℕ → ℕ → ℝ) : List ℕ → ℝ
  | [] => 1
  | [_] => 1
  | a :: b :: rest => adj a b * seqWeight adj (b :: rest)

/-- 三段式序列的分解（peel 分块的代数核心）：中间表 m 末尾接一对节点
    `[j, t]` 时，权重 = 前段（终点 j）的权重 × 边权 adj j t。对 m 归纳。 -/
theorem seqWeight_mid_append (adj : ℕ → ℕ → ℝ) :
    ∀ (m : List ℕ) (u j t : ℕ),
      seqWeight adj (u :: (m ++ [j, t]))
        = seqWeight adj (u :: (m ++ [j])) * adj j t := by
  intro m
  induction m with
  | nil =>
    intro u j t
    simp only [List.nil_append, seqWeight]
    ring
  | cons x m' ih =>
    intro u j t
    simp only [List.cons_append, seqWeight]
    rw [ih x j t]
    ring

/-- 过滤集与闭区间的集合恒等（对齐 `pathWeight_split` 的求和域）。 -/
theorem filter_range_eq_Icc (u t : ℕ) :
    (range t).filter (fun j => u < j) = Finset.Icc (u + 1) (t - 1) := by
  ext j
  simp only [mem_filter, mem_range, mem_Icc]
  omega

/-! ## 第二节：最大元插末尾的排序引理 -/

/-- 最大元插入：a 大于等于 S 全部元素且 a ∉ S 时，
    insert a S 的升序排序 = S 的升序排序 + [a]。
    证明：第二表示取插入排序（库给成对有序与置换），
    两次 `List.Perm.eq_of_pairwise'` 唯一性收口。 -/
theorem sort_insert_max {a : ℕ} :
    ∀ (S : Finset ℕ), (∀ b ∈ S, b ≤ a) → a ∉ S →
      (insert a S).sort (· ≤ ·) = S.sort (· ≤ ·) ++ [a] := by
  intro S hb ha
  -- 第二表示：插入排序（库给 Pairwise 有序 + 置换）
  have hLpair : ((insert a S).sort (· ≤ ·)).Pairwise (· ≤ ·) :=
    Finset.pairwise_sort (insert a S) (· ≤ ·)
  have hRpair : ((S.toList ++ [a]).insertionSort (· ≤ ·)).Pairwise (· ≤ ·) :=
    List.pairwise_insertionSort (r := (· ≤ ·)) _
  -- L ~ R：sort ~ toList ~ cons-form ~ append-form ~ insertionSort
  have hperm1 : List.Perm ((insert a S).sort (· ≤ ·))
      ((S.toList ++ [a]).insertionSort (· ≤ ·)) := by
    refine List.Perm.trans (Finset.sort_perm_toList _ _) ?_
    refine List.Perm.trans (Finset.toList_insert ha) ?_
    refine List.Perm.trans ?_ (List.perm_insertionSort (r := (· ≤ ·)) _).symm
    exact (List.perm_append_singleton a (S.toList)).symm
  have heq1 : ((insert a S).sort (· ≤ ·))
      = ((S.toList ++ [a]).insertionSort (· ≤ ·)) :=
    List.Perm.eq_of_pairwise' hLpair hRpair hperm1
  -- 目标 append 形与 R 的 pairwise-唯一性（a 为最大元 ⟹ 确为有序）
  have hTpair : (S.sort (· ≤ ·) ++ [a]).Pairwise (· ≤ ·) := by
    rw [List.pairwise_append]
    refine ⟨Finset.pairwise_sort S (· ≤ ·), ?_, fun x hx y hy => ?_⟩
    · simp
    · rw [List.mem_singleton.mp hy]
      exact hb x ((Finset.mem_sort (r := (· ≤ ·))).mp hx)
  have hperm2 : List.Perm ((S.toList ++ [a]).insertionSort (· ≤ ·))
      (S.sort (· ≤ ·) ++ [a]) := by
    refine List.Perm.trans (List.perm_insertionSort (r := (· ≤ ·)) _) ?_
    exact List.Perm.append_right _ (Finset.sort_perm_toList S (· ≤ ·)).symm
  have heq2 : ((S.toList ++ [a]).insertionSort (· ≤ ·))
      = S.sort (· ≤ ·) ++ [a] :=
    List.Perm.eq_of_pairwise' hRpair hTpair hperm2
  exact heq1.trans heq2

/-! ## 第三节：枚举和的定义 -/

/-- 全部路径（u→t）的权重和：按中间节点集 S ⊆ (u, t) 枚举，
    路径 = u → S 升序排列 → t。约定与 W 一致：u=t 空路径记 1，u>t 记 0。 -/
noncomputable def pathEnumSum (adj : ℕ → ℕ → ℝ) (u t : ℕ) : ℝ :=
  if u < t then
    ∑ S ∈ (Finset.Icc (u + 1) (t - 1)).powerset,
      seqWeight adj (u :: (S.sort (· ≤ ·) ++ [t]))
  else if u = t then 1
  else 0

/-! ## 第四节：peel 引理 — 按最大中间元逐层分块 -/

/-- **peel 引理**：枚举和按最大中间元 c 分块——不含 c 的部分由归纳给出
    （同终点、cap 减一），含 c 的块 = （u→c 的枚举和）× 边权 adj c e。
    展开到底即得 `pathWeight_split` 的枚举侧镜像。 -/
theorem enumSum_peel_gen (adj : ℕ → ℕ → ℝ) (u e : ℕ) :
    ∀ c : ℕ, u < c → c ≤ e - 1 →
      (∑ S ∈ (Finset.Icc (u + 1) c).powerset,
          seqWeight adj (u :: (S.sort (· ≤ ·) ++ [e])))
        = adj u e + ∑ j ∈ Finset.Icc (u + 1) c, adj j e * pathEnumSum adj u j := by
  intro c
  induction c with
  | zero => intro h1 _; omega
  | succ c ih =>
    intro hc hcE
    rcases Nat.lt_or_ge u c with hult | huge
    · -- 一般情形：域分裂 Icc (u+1) (c+1) = insert (c+1) (Icc (u+1) c)
      have hne : (c + 1 : ℕ) ∉ Finset.Icc (u + 1) c := by
        intro hmem
        have := Finset.mem_Icc.mp hmem
        omega
      have hsplitF := Finset.sum_powerset_insert (a := (c + 1 : ℕ))
        (s := Finset.Icc (u + 1) c)
        (f := fun S : Finset ℕ => seqWeight adj (u :: (S.sort (· ≤ ·) ++ [e])))
        hne
      -- 含 c+1 的块：最大元插末 + 权重乘边权 + 内层恰为 pathEnumSum u (c+1)
      have hPE : pathEnumSum adj u (c + 1)
          = ∑ S ∈ (Finset.Icc (u + 1) c).powerset,
            seqWeight adj (u :: (S.sort (· ≤ ·) ++ [c + 1])) := by
        rw [pathEnumSum, ite_eq_left (show u < c + 1 by omega), Nat.add_sub_cancel]
      have hT2 : (∑ S ∈ (Finset.Icc (u + 1) c).powerset,
          seqWeight adj (u :: ((insert (c + 1) S).sort (· ≤ ·) ++ [e])))
          = adj (c + 1) e * pathEnumSum adj u (c + 1) := by
        rw [hPE, Finset.mul_sum]
        refine Finset.sum_congr rfl fun S hS => ?_
        have hsub : ∀ b ∈ S, b ≤ c := fun b hb =>
          (Finset.mem_Icc.mp (Finset.mem_powerset.mp hS hb)).2
        have hmax : ∀ b ∈ S, b ≤ c + 1 := fun b hb =>
          le_trans (hsub b hb) (by omega)
        have hsne : (c + 1 : ℕ) ∉ S := by
          intro hb'
          have hle := (Finset.mem_Icc.mp (Finset.mem_powerset.mp hS hb')).2
          omega
        rw [sort_insert_max S hmax hsne]
        have hx := seqWeight_mid_append adj S.sort u (c + 1) e
        simp only [List.append_assoc, List.cons_append, List.nil_append] at hx ⊢
        rw [hx]
        ring
      -- 不含 c+1 的块由 IH 收口；求和域扩一位后两侧同构
      have hIH := ih hult (by omega)
      have hIns : Finset.Icc (u + 1) (c + 1)
          = insert (c + 1) (Finset.Icc (u + 1) c) := by
        ext j
        constructor
        · intro hj
          have hji : u + 1 ≤ j ∧ j ≤ c + 1 := Finset.mem_Icc.mp hj
          rcases Nat.lt_or_ge j c with hlt | hge
          · exact Finset.mem_insert_of_mem
              (Finset.mem_Icc.mpr ⟨hji.1, hlt.le⟩)
          · rcases Nat.eq_or_lt_of_le hge with hjc | hltc
            · rw [hjc]
              exact Finset.mem_insert_of_mem
                (Finset.mem_Icc.mpr ⟨hji.1, le_rfl⟩)
            · have hjc' : j = c + 1 := by omega
              rw [hjc']
              exact Finset.mem_insert_self _ _
        · intro hj
          rcases Finset.mem_insert.mp hj with hjc | hji
          · rw [hjc]
            exact Finset.mem_Icc.mpr ⟨by omega, by omega⟩
          · exact Finset.mem_Icc.mpr ⟨(Finset.mem_Icc.mp hji).1,
              le_trans (Finset.mem_Icc.mp hji).2 (by omega)⟩
      rw [hIns, hsplitF, hT2, hIH, Finset.sum_insert hne]
      ring
    · -- 退化：u = c ⟹ 域 Icc (u+1) (u+1) = {u+1}；两块：直达边 + 经 u+1
      have hue : u = c := by omega
      subst hue
      have hSing : Finset.Icc (u + 1) (u + 1)
          = insert (u + 1) (∅ : Finset ℕ) := by
        ext j
        constructor
        · intro hj
          have := Finset.mem_Icc.mp hj
          rw [show j = u + 1 from by omega]
          exact Finset.mem_insert_self _ _
        · intro hj
          rcases Finset.mem_insert.mp hj with rfl | hmem
          · exact Finset.mem_Icc.mpr ⟨by omega, by omega⟩
          · exact absurd hmem (by simp)
      rw [hSing]
      rw [Finset.sum_powerset_insert
        (by simp : (u + 1 : ℕ) ∉ (∅ : Finset ℕ))
        (f := fun S : Finset ℕ => seqWeight adj (u :: (S.sort (· ≤ ·) ++ [e])))]
      have hE0 : seqWeight adj (u :: ((∅ : Finset ℕ).sort (· ≤ ·) ++ [e]))
          = adj u e := by
        simp [seqWeight]
      have hE1 : seqWeight adj (u :: ((insert (u + 1) (∅ : Finset ℕ)).sort (· ≤ ·) ++ [e]))
          = adj u (u + 1) * adj (u + 1) e := by
        rw [sort_insert_max (a := (u + 1 : ℕ)) (∅ : Finset ℕ) (by simp) (by simp)]
        simp [seqWeight]
      have hQ' : Finset.Icc (u + 1) u = ∅ := by
        rw [Finset.eq_empty_iff_forall_notMem]
        intro j hj
        have := Finset.mem_Icc.mp hj
        omega
      have hPE : pathEnumSum adj u (u + 1) = adj u (u + 1) := by
        rw [pathEnumSum, ite_eq_left (show u < u + 1 by omega), Nat.add_sub_cancel]
        rw [hQ', Finset.powerset_empty, Finset.sum_singleton]
        simp [seqWeight]
      rw [Finset.powerset_empty]
      rw [Finset.sum_singleton, Finset.sum_singleton]
      rw [Finset.sum_insert (by simp : (u + 1 : ℕ) ∉ (∅ : Finset ℕ)),
        Finset.sum_empty, add_zero]
      rw [hE0, hE1, hPE]
      ring

/-! ## 第五节：主定理 — 枚举求和 = 递归 W -/

/-- **主定理**：显式路径枚举求和 = 递归定义的路径权重和 W
    （02 篇第 63 行"对所有路径求和"的机器验证：两条定义严格相等）。 -/
theorem pathEnumSum_eq_pathWeight (adj : ℕ → ℕ → ℝ) (u t : ℕ) :
    pathEnumSum adj u t = pathWeight adj u t := by
  revert u
  induction' t using Nat.strong_induction_on with t ih
  intro u
  unfold pathEnumSum
  by_cases hut : u = t
  · subst hut
    rw [ite_eq_right (by omega : ¬(u < u)), ite_eq_left rfl, pathWeight_self]
  · rcases Nat.lt_or_ge u t with hlt | hgt
    · rw [ite_eq_left hlt]
      have hsplit := pathWeight_split adj hlt
      rw [filter_range_eq_Icc] at hsplit
      rcases Nat.lt_or_ge t (u + 2) with ht2 | ht2'
      · -- t = u+1：中间域为空，枚举只剩直达边
        have ht : t = u + 1 := by omega
        subst ht
        have hQ : Finset.Icc (u + 1) ((u + 1) - 1) = ∅ := by
          rw [Finset.eq_empty_iff_forall_notMem]
          intro j hj
          have := Finset.mem_Icc.mp hj
          omega
        rw [hQ, Finset.powerset_empty, Finset.sum_singleton, hsplit]
        rw [hQ]
        simp only [Finset.sum_empty, add_zero, Finset.sort_empty, List.nil_append,
          seqWeight]
        ring
      · -- 一般：peel 到位 + IH 收口
        have hpeel := enumSum_peel_gen adj u t (t - 1) (by omega) (by omega)
        rw [hpeel]
        have hIH : ∀ j ∈ Finset.Icc (u + 1) (t - 1),
            pathEnumSum adj u j = pathWeight adj u j := by
          intro j hj
          have hjt : j < t := by
            have := Finset.mem_Icc.mp hj
            omega
          exact ih j hjt u
        rw [Finset.sum_congr rfl fun j hj => by rw [hIH j hj]]
        rw [← hsplit]
    · rw [ite_eq_right (by omega), ite_eq_right (by omega),
        pathWeight_eq_zero_of_gt adj (by omega)]

end AscendLean.CausalVerification
