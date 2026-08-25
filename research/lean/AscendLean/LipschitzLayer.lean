import Mathlib
import AscendLean.Theorem25

/-!
# Lipschitz 函数层 — 从"真实预测误差"到"路径和闭式"的完整闭环

出处：`docs/研究理论/因果理论验证/02-误差传播与反事实.md`

- 引理 2.1（第 5-19 行）：Lipschitz 复合误差传播律，三步拆解在第 15 行：
  `|模型复合 Fh(x) − 真值复合 F(x)| ≤ |fh_ℓ(ẑ) − f_ℓ(ẑ)| + |f_ℓ(ẑ) − f_ℓ(z)| ≤ ε_ℓ + L_ℓ·|ẑ − z|`；
- 定理 2.5 命题（第 51-57 行）：沿拓扑序递推
  `e_i = ε_i + Σ_{j ∈ Pa_G(i)} L_{j,i}·e_j` 给出 `|Xh_t^do − X_t^do| ≤ e_t`，
  干预节点 `e_s = 0`（第 53 行）；
- 路径和闭式（第 59-61 行）：
  `e_t = Σ_u ε_u · Σ_{paths u→t} Π L_{a,b}`，链情形退化回引理 2.1（第 59 行）。

`Theorem25.lean` 已证明代数内核（`dag_path_expansion`：递推 ⟹ 闭式）。
本文件补上缺失的一环——**凭什么真实预测误差满足那个递推上界**：

0. 记号：节点 = ℕ（拓扑序 = 自然数序）；真方程 `f i` / 模型方程 `fh i`
   吃全部上游取值（`(ℕ → ℝ) → ℝ`）；轨迹自洽 `X i = f i X`、`Xh i = fh i Xh`
   （CRN：同一噪声实现下双方都是确定性的）。
1. 望远镜引理：单坐标 Lipschitz ⟹ 多坐标同时变化时各坐标贡献相加
   （02 篇第 53 行"L_{j,i} 关于父 j"的多父语义基础）；
2. 结构方程的局部性（`f i` 只依赖坐标 `< i`）+ 单坐标 Lipschitz
   ⟹ 无条件和式 Lipschitz（截断归约）；
3. 连接定理（强归纳）：`|Xh i − X i| ≤ e i`，e 由递推定义——
   即文档第 15 行三步拆解的轨迹版；
4. 定理 2.5 完整式：组合 `dag_path_expansion` 得
   `|Xh t − X t| ≤ ε t + Σ_{u<t} ε u · W u t`；
5. 干预情形：被干预节点两侧钉死同值（`X s = Xh s`）⟹ 该点误差为 0，
   复用 `do_intervention_zero`；`ε s = 0` 使闭式中干预源项消失
   （对应第 61 行 `Anc(t) \ S`）；
6. 链特例 = 引理 2.1：单父链上实例化，路径和闭式退化回 Feasibility.lean
   的 `Σ ε_j Π L_j` 形态。

编码取舍（Lipschitz 的忠实版）：逐边 Lipschitz 采用**单父坐标**形式
（改一个父坐标、界 `L_{j,i}`，即文档"L_{j,i} 关于父 j"的字面语义），
和式版本由望远镜引理**推导**而非假设。代价是需额外引入结构方程的
局部性假设（`f i` 只读低坐标）——这是 SCM 结构方程的定义性内容，
且 ℕ 全函数编码下必须显式声明依赖窗口。
-/

open Finset
open scoped BigOperators

namespace AscendLean

/-! ## 第一节：望远镜引理（单坐标 Lipschitz ⟹ 多坐标贡献相加） -/

/-- 非依赖版单坐标更新。Mathlib 的 `Function.update` 是依赖类型版
    （值类型 `β a`），独立引理中会引入类型层强制转换、妨碍 `rw` 模式匹配，
    故包一层普通定义，把依赖性限制在证明项内部。 -/
def pointUpd (x : ℕ → ℝ) (n : ℕ) (v : ℝ) : ℕ → ℝ :=
  Function.update x n v

/-- 更新在目标坐标上取新值。 -/
@[simp] lemma pointUpd_self (x : ℕ → ℝ) (n : ℕ) (v : ℝ) :
    pointUpd x n v n = v := by
  show Function.update x n v n = v
  exact Function.update_self n v x

/-- 更新在非目标坐标上不改值。 -/
lemma pointUpd_of_ne {x : ℕ → ℝ} {n : ℕ} (v : ℝ) {k : ℕ} (h : k ≠ n) :
    pointUpd x n v k = x k := by
  show Function.update x n v k = x k
  exact Function.update_of_ne h v x

/-- **望远镜引理**：若 `g` 关于每个坐标 `j < n` 都是 `w j`-Lipschitz
    （其余坐标不动时输出变化 ≤ `w j ×`该坐标变化），
    则两个在高坐标（≥ n）上一致的输入之间有
    `|g x − g y| ≤ Σ_{j<n} w j · |x j − y j|`——多坐标同时变化时各坐标贡献相加。
    这是 02 篇第 53 行逐边常数 `L_{j,i}` 能按父求和的语义基础。
    证明：把第 m 个坐标单独换成 y 的取值，拆成"单坐标步 + 余下归纳"。 -/
theorem lipschitz_telescope {g : (ℕ → ℝ) → ℝ} {w : ℕ → ℝ} :
    ∀ n : ℕ,
      (∀ j, j < n → ∀ x y : ℕ → ℝ, (∀ k ≠ j, x k = y k) →
        |g x - g y| ≤ w j * |x j - y j|) →
      ∀ (x y : ℕ → ℝ), (∀ k, n ≤ k → x k = y k) →
        |g x - g y| ≤ ∑ j ∈ range n, w j * |x j - y j| := by
  intro n
  induction n with
  | zero =>
    intro _ x y hag
    have hxy : x = y := funext fun k => hag k (Nat.zero_le k)
    subst hxy
    simp
  | succ m ih =>
    intro hlip x y hag
    -- 中间点：只有第 m 个坐标换成 y 的取值
    -- 单坐标步（02 篇第 15 行第二项的单坐标来源）
    have hstep : |g x - g (pointUpd x m (y m))| ≤ w m * |x m - y m| := by
      have h1 := hlip m (by omega) x (pointUpd x m (y m))
        (fun k hk => by rw [pointUpd_of_ne (y m) hk])
      rwa [pointUpd_self] at h1
    -- 归纳步：中间点与 y 在 ≥ m 上一致
    have hrest0 : |g (pointUpd x m (y m)) - g y|
        ≤ ∑ j ∈ range m, w j * |pointUpd x m (y m) j - y j| := by
      refine ih (fun j hj => hlip j (by omega)) (pointUpd x m (y m)) y ?_
      intro k hk
      rcases lt_or_eq_of_le hk with h | h
      · rw [pointUpd_of_ne (y m) (by omega : k ≠ m)]
        exact hag k (by omega)
      · rw [h]
        exact pointUpd_self _ _ _
    have hsum : (∑ j ∈ range m, w j * |pointUpd x m (y m) j - y j|)
        = (∑ j ∈ range m, w j * |x j - y j|) := by
      apply Finset.sum_congr rfl
      intro j hj
      have hjm : (j : ℕ) < m := Finset.mem_range.mp hj
      rw [pointUpd_of_ne (y m) (by omega : (j : ℕ) ≠ m)]
    have hrest : |g (pointUpd x m (y m)) - g y|
        ≤ ∑ j ∈ range m, w j * |x j - y j| := by rw [← hsum]; exact hrest0
    have triangle : |g x - g y| ≤ |g x - g (pointUpd x m (y m))|
        + |g (pointUpd x m (y m)) - g y| := abs_sub_le _ _ _
    have final : |g x - g y| ≤ ∑ j ∈ range (m + 1), w j * |x j - y j| := by
      rw [Finset.sum_range_succ]
      linarith [triangle, hstep, hrest]
    exact final

/-! ## 第二节：结构方程局部性 + 单坐标 Lipschitz ⟹ 无条件和式 Lipschitz -/

/-- **无条件和式 Lipschitz**：真方程只读低坐标（局部性）+ 单父坐标 Lipschitz
    （02 篇第 53 行的字面语义）⟹ 对任意两输入（无需任何一致性前提）
    `|g x − g y| ≤ Σ_{j<n} w j · |x j − y j|`。
    证明：把两输入都截断到低 n 维（局部性保证方程值不变），
    截断后在高坐标上一致，套望远镜引理。 -/
theorem lipschitz_sum_of_single {g : (ℕ → ℝ) → ℝ} {w : ℕ → ℝ} {n : ℕ}
    (hdep : ∀ x y : ℕ → ℝ, (∀ k < n, x k = y k) → g x = g y)
    (hlip : ∀ j, j < n → ∀ x y : ℕ → ℝ, (∀ k < n, k ≠ j → x k = y k) →
      |g x - g y| ≤ w j * |x j - y j|)
    (x y : ℕ → ℝ) :
    |g x - g y| ≤ ∑ j ∈ range n, w j * |x j - y j| := by
  -- 截断到低 n 维：高坐标一律记 0（局部性保证方程值不变）
  have hconv : (∑ j ∈ range n, w j * |(fun k : ℕ => if k < n then x k else 0) j
      - (fun k : ℕ => if k < n then y k else 0) j|)
      = (∑ j ∈ range n, w j * |x j - y j|) := by
    apply Finset.sum_congr rfl
    intro j hj
    have hjn : (j : ℕ) < n := Finset.mem_range.mp hj
    simp [hjn]
  calc |g x - g y|
      = |g (fun k : ℕ => if k < n then x k else 0)
          - g (fun k : ℕ => if k < n then y k else 0)| :=
        congrArg₂ (fun a b => |a - b|) (hdep x _ fun k hk => by simp [hk])
          (hdep y _ fun k hk => by simp [hk])
    _ ≤ ∑ j ∈ range n, w j * |(fun k : ℕ => if k < n then x k else 0) j
          - (fun k : ℕ => if k < n then y k else 0) j| :=
        lipschitz_telescope n
          (fun j hj a b hab => hlip j hj a b fun k _ hjk => hab k hjk)
          _ _ (fun k hk => by
            simp [show ¬ (k : ℕ) < n from by omega])
    _ = ∑ j ∈ range n, w j * |x j - y j| := hconv

/-! ## 第三节：连接定理 — 真实误差满足递推上界（引理 2.1 的轨迹版，02 篇第 15 行三步拆解） -/

/-- **单节点展开步**（02 篇第 15 行：`|Fh(x) − F(x)| ≤ |fh_ℓ(ẑ) − f_ℓ(ẑ)| + |f_ℓ(ẑ) − f_ℓ(z)|`）：
    节点 i 处 `|Xh i − X i| ≤ ε i + Σ_{j<i} adj j i · e_j`，
    只要子节点误差已有上界 `|Xh j − X j| ≤ e j`。
    三步：模型自身误差（sup 范数假设）+ 输入误差的 Lipschitz 传播 + 子界代入。 -/
theorem step_bound (f fh : ℕ → (ℕ → ℝ) → ℝ) (X Xh ε e : ℕ → ℝ) (adj : ℕ → ℕ → ℝ)
    (i : ℕ)
    (hXi : X i = f i X) (hXhi : Xh i = fh i Xh)
    (herr : ∀ k (x : ℕ → ℝ), |fh k x - f k x| ≤ ε k)
    (hloc : ∀ k (x y : ℕ → ℝ), (∀ m < k, x m = y m) → f k x = f k y)
    (hlip : ∀ k j (x y : ℕ → ℝ), j < k → (∀ m < k, m ≠ j → x m = y m) →
      |f k x - f k y| ≤ adj j k * |x j - y j|)
    (hsub : ∀ j, j < i → |Xh j - X j| ≤ e j)
    (hadjnn : ∀ j k, 0 ≤ adj j k) :
    |Xh i - X i| ≤ ε i + ∑ j ∈ range i, adj j i * e j := by
  calc |Xh i - X i| = |fh i Xh - f i X| := by rw [hXhi, hXi]
    _ ≤ |fh i Xh - f i Xh| + |f i Xh - f i X| := by
        have h : fh i Xh - f i X = (fh i Xh - f i Xh) + (f i Xh - f i X) := by ring
        rw [h]
        exact abs_add_le _ _
    _ ≤ ε i + ∑ j ∈ range i, adj j i * |Xh j - X j| := by
        refine add_le_add (herr i _) ?_
        exact lipschitz_sum_of_single (hloc i)
          (fun j hj x y hagree => hlip i j x y hj hagree) Xh X
    _ ≤ ε i + ∑ j ∈ range i, adj j i * e j := by
        refine add_le_add le_rfl (Finset.sum_le_sum fun j hj => ?_)
        exact mul_le_mul_of_nonneg_left (hsub j (Finset.mem_range.mp hj)) (hadjnn j i)

/-- **连接定理**（02 篇定理 2.5 命题的第 51-57 行部分）：
    真值轨迹 `X i = f i X` 与模型轨迹 `Xh i = fh i Xh`（CRN 同一噪声实现下双方确定性）
    满足逐节点模型误差 `‖fh i − f i‖_∞ ≤ ε i`、真方程局部性（只读低坐标）
    与单父坐标 Lipschitz（第 53 行的 `L_{j,i}` 关于父 j）时，
    强归纳给出 `|Xh i − X i| ≤ e i`，其中 e 由递推
    `e i = ε i + Σ_{j<i} adj j i · e j` 定义。 -/
theorem error_recurrence_bound (f fh : ℕ → (ℕ → ℝ) → ℝ) (X Xh ε e : ℕ → ℝ) (adj : ℕ → ℕ → ℝ)
    (hX : ∀ i, X i = f i X) (hXh : ∀ i, Xh i = fh i Xh)
    (herr : ∀ i (x : ℕ → ℝ), |fh i x - f i x| ≤ ε i)
    (hloc : ∀ i (x y : ℕ → ℝ), (∀ k < i, x k = y k) → f i x = f i y)
    (hlip : ∀ i j (x y : ℕ → ℝ), j < i → (∀ k < i, k ≠ j → x k = y k) →
      |f i x - f i y| ≤ adj j i * |x j - y j|)
    (hadjnn : ∀ j i, 0 ≤ adj j i)
    (he : ∀ i, e i = ε i + ∑ j ∈ range i, adj j i * e j)
    (i : ℕ) :
    |Xh i - X i| ≤ e i := by
  induction' i using Nat.strong_induction_on with i ih
  rw [he i]
  exact step_bound f fh X Xh ε e adj i (hX i) (hXh i) herr hloc hlip
    (fun j hj => ih j hj) hadjnn

/-! ## 第四节：定理 2.5 完整式 — 组合路径和展开 -/

/-- **定理 2.5 完整式**（02 篇第 51-61 行）：组合连接定理与
    `Theorem25.dag_path_expansion`（递推 ⟹ 路径和闭式），得
    `|Xh t − X t| ≤ ε t + Σ_{u<t} ε u · W u t`
    ——对所有从误差源到 t 的有向路径求和，而非取最大。 -/
theorem counterfactual_closed_form (f fh : ℕ → (ℕ → ℝ) → ℝ) (X Xh ε e : ℕ → ℝ)
    (adj : ℕ → ℕ → ℝ)
    (hX : ∀ i, X i = f i X) (hXh : ∀ i, Xh i = fh i Xh)
    (herr : ∀ i (x : ℕ → ℝ), |fh i x - f i x| ≤ ε i)
    (hloc : ∀ i (x y : ℕ → ℝ), (∀ k < i, x k = y k) → f i x = f i y)
    (hlip : ∀ i j (x y : ℕ → ℝ), j < i → (∀ k < i, k ≠ j → x k = y k) →
      |f i x - f i y| ≤ adj j i * |x j - y j|)
    (hadjnn : ∀ j i, 0 ≤ adj j i)
    (he : ∀ i, e i = ε i + ∑ j ∈ range i, adj j i * e j)
    (t : ℕ) :
    |Xh t - X t| ≤ ε t + ∑ u ∈ range t, ε u * pathWeight adj u t := by
  have h1 := error_recurrence_bound f fh X Xh ε e adj hX hXh herr hloc hlip hadjnn he t
  rwa [dag_path_expansion adj e ε he t] at h1

/-! ## 第五节：干预情形 — 被干预节点钉死同值（02 篇第 49、53 行） -/

/-- 递推误差的非负性：ε ≥ 0（由 sup 范数界保证）+ Lipschitz 权重非负
    ⟹ e i ≥ 0。干预钉死分支需要它。 -/
lemma recurrence_nonneg (ε e : ℕ → ℝ) (adj : ℕ → ℕ → ℝ)
    (hεnn : ∀ i, 0 ≤ ε i) (hadjnn : ∀ j i, 0 ≤ adj j i)
    (he : ∀ i, e i = ε i + ∑ j ∈ range i, adj j i * e j) :
    ∀ i, 0 ≤ e i := by
  intro i
  induction' i using Nat.strong_induction_on with i ih
  rw [he i]
  refine add_nonneg (hεnn i) ?_
  exact Finset.sum_nonneg fun j hj => mul_nonneg (hadjnn j i) (ih j (Finset.mem_range.mp hj))

/-- **干预情形的连接定理**：do(X_s = x_s) 后，被干预节点 s 双方钉死同一干预值
    （CRN + 同一 do 值，`X s = Xh s`），其方程不再被求值
    （自洽性只在 i ≠ s 处要求）；递推在其余节点照常成立，
    故对一切 i 有 `|Xh i − X i| ≤ e i`。
    钉死分支需要 e s ≥ 0：由 sup 范数界推出 ε ≥ 0，再由递推归纳出 e ≥ 0
    （见 recurrence_nonneg）；与 Theorem25.do_intervention_zero 呼应：
    ε s = 0 且入边断开 ⟹ e s = 0。 -/
theorem error_recurrence_bound_do (f fh : ℕ → (ℕ → ℝ) → ℝ) (X Xh ε e : ℕ → ℝ)
    (adj : ℕ → ℕ → ℝ)
    {s : ℕ} (hpin : X s = Xh s)
    (hX : ∀ i, i ≠ s → X i = f i X) (hXh : ∀ i, i ≠ s → Xh i = fh i Xh)
    (herr : ∀ i (x : ℕ → ℝ), |fh i x - f i x| ≤ ε i)
    (hloc : ∀ i (x y : ℕ → ℝ), (∀ k < i, x k = y k) → f i x = f i y)
    (hlip : ∀ i j (x y : ℕ → ℝ), j < i → (∀ k < i, k ≠ j → x k = y k) →
      |f i x - f i y| ≤ adj j i * |x j - y j|)
    (hadjnn : ∀ j i, 0 ≤ adj j i)
    (he : ∀ i, e i = ε i + ∑ j ∈ range i, adj j i * e j)
    (i : ℕ) :
    |Xh i - X i| ≤ e i := by
  have hεnn : ∀ k, 0 ≤ ε k := fun k => le_trans (abs_nonneg (fh k 0 - f k 0)) (herr k 0)
  induction' i using Nat.strong_induction_on with i ih
  by_cases his : i = s
  · subst his
    rw [← hpin, sub_self, abs_zero]
    exact recurrence_nonneg ε e adj hεnn hadjnn he _
  · rw [he i]
    exact step_bound f fh X Xh ε e adj i (hX i his) (hXh i his) herr hloc hlip
      (fun j hj => ih j hj) hadjnn

/-- 干预节点的自洽性核对（02 篇第 53 行 `e_s = 0`（干预节点））：
    一侧由递推 + ε s = 0 + 入边断开给出 e s = 0（复用 Theorem25.do_intervention_zero）；
    另一侧由干预值钉死给出真实误差 |Xh s − X s| = 0。两侧一致。 -/
theorem do_intervention_consistency (X Xh ε e : ℕ → ℝ)
    (adj : ℕ → ℕ → ℝ)
    {s : ℕ} (hpin : X s = Xh s)
    (he : ∀ i, e i = ε i + ∑ j ∈ range i, adj j i * e j)
    (hεs : ε s = 0) (hcut : ∀ j, j < s → adj j s = 0) :
    e s = 0 ∧ |Xh s - X s| = 0 := by
  constructor
  · exact do_intervention_zero adj e ε he hεs hcut
  · rw [← hpin]
    simp

/-- **定理 2.5 完整式（干预版）**（02 篇第 51-61 行）：do(X_s = x_s) 后
    `|Xh t^do − X t^do| ≤ ε t + Σ_{u<t} ε u · W u t`。
    注意本定理比文档假设更强：无需显式设 ε s = 0 或断开入边——
    钉死使 `|Xh s − X s| = 0 ≤ e s` 对任意 e s 成立。
    而在 do 设定下 ε s = 0 本来就自动成立（s 的方程两侧都不再被求值），
    此时闭式中干预源 u = s 的项消失（见 do_source_term_vanishes），
    对应第 61 行求和限制在 `Anc(t) \ S`；
    e s = 0 的精确核对见 do_intervention_consistency。 -/
theorem counterfactual_closed_form_do (f fh : ℕ → (ℕ → ℝ) → ℝ) (X Xh ε e : ℕ → ℝ)
    (adj : ℕ → ℕ → ℝ)
    {s : ℕ} (hpin : X s = Xh s)
    (hX : ∀ i, i ≠ s → X i = f i X) (hXh : ∀ i, i ≠ s → Xh i = fh i Xh)
    (herr : ∀ i (x : ℕ → ℝ), |fh i x - f i x| ≤ ε i)
    (hloc : ∀ i (x y : ℕ → ℝ), (∀ k < i, x k = y k) → f i x = f i y)
    (hlip : ∀ i j (x y : ℕ → ℝ), j < i → (∀ k < i, k ≠ j → x k = y k) →
      |f i x - f i y| ≤ adj j i * |x j - y j|)
    (hadjnn : ∀ j i, 0 ≤ adj j i)
    (he : ∀ i, e i = ε i + ∑ j ∈ range i, adj j i * e j)
    (t : ℕ) :
    |Xh t - X t| ≤ ε t + ∑ u ∈ range t, ε u * pathWeight adj u t := by
  have h1 := error_recurrence_bound_do f fh X Xh ε e adj hpin hX hXh herr hloc hlip
    hadjnn he t
  rwa [dag_path_expansion adj e ε he t] at h1

/-- 闭式中干预源项消失的形式化（02 篇第 61 行 `Anc(t) \ S` 的对应物）：
    ε s = 0 时，无论路径权重 W s t 为何，u = s 的贡献恒为 0。 -/
theorem do_source_term_vanishes (adj : ℕ → ℕ → ℝ) (ε : ℕ → ℝ)
    {s : ℕ} (hεs : ε s = 0) (t : ℕ) :
    ε s * pathWeight adj s t = 0 := by
  rw [hεs]
  ring

/-! ## 第六节：链特例 = 引理 2.1（02 篇第 5-19 行；退化说明在第 59 行） -/

/-- 链邻接：唯一父边 j → j+1，权重 L j（单父链，02 篇第 7 行
    X₁ → X₂ → … → X_{ℓ+1}）。 -/
def chainAdj (L : ℕ → ℝ) (j i : ℕ) : ℝ := if i = j + 1 then L j else 0

lemma chainAdj_eq (L : ℕ → ℝ) (j i : ℕ) : chainAdj L j i = if i = j + 1 then L j else 0 := rfl

@[simp] lemma chainAdj_succ (L : ℕ → ℝ) (j : ℕ) : chainAdj L j (j + 1) = L j := by
  simp [chainAdj]

lemma chainAdj_ne (L : ℕ → ℝ) {j i : ℕ} (h : i ≠ j + 1) : chainAdj L j i = 0 := by
  simp [chainAdj, h]

/-- 链上 Lipschitz 权重非负（Lipschitz 常数天然非负）。 -/
lemma chainAdj_nonneg (L : ℕ → ℝ) (hL : ∀ i, 0 ≤ L i) (j i : ℕ) : 0 ≤ chainAdj L j i := by
  show 0 ≤ (if i = j + 1 then L j else 0)
  by_cases h : i = j + 1
  · simpa [h] using hL j
  · simp [h]

/-- 单点区间上的乘积（现版 Mathlib 无 `Finset.prod_Icc_self`，自备）。 -/
lemma chain_prod_single (L : ℕ → ℝ) (m : ℕ) :
    (∏ j ∈ Icc m m, L j) = L m := by
  have h1 : Icc m m = {m} := by
    ext a
    simp only [Finset.mem_Icc, Finset.mem_singleton]
    exact ⟨fun h => le_antisymm h.2 h.1,
      fun h => by subst h; exact ⟨le_refl _, le_refl _⟩⟩
  rw [h1, Finset.prod_singleton]

/-- 链上递推的和式坍缩：`Σ_{j<i+1} chainAdj L j (i+1) · e j = L i · e i`
    （每节点只有唯一父 i 有非零边）。 -/
lemma chain_rec_sum (L e : ℕ → ℝ) (i : ℕ) :
    ∑ j ∈ range (i + 1), chainAdj L j (i + 1) * e j = L i * e i := by
  rw [Finset.sum_eq_single i]
  · rw [chainAdj_succ]
  · intro b hb hba
    rw [chainAdj_ne L (show i + 1 ≠ b + 1 from by omega)]
    ring
  · intro hc
    exact absurd (Finset.mem_range.mpr (by omega : i < i + 1)) hc

/-- 链上的路径权重退化为乘积（02 篇第 59 行"链情形每节点单父"）：
    u ≤ n 时从 u 到 n+1 只有唯一路径，`W u (n+1) = Π_{j ∈ Icc u n} L j`
    ——沿链逐段相乘，即引理 2.1 第 9 行的 `Π_{m=j+1}^{ℓ} L_m`。 -/
lemma pathWeight_chain_lt (L : ℕ → ℝ) (n : ℕ) :
    ∀ u, u ≤ n → pathWeight (chainAdj L) u (n + 1) = ∏ j ∈ Icc u n, L j := by
  induction' n using Nat.strong_induction_on with n ih
  intro u hun
  rw [pathWeight_rec (chainAdj L) (by omega : u ≠ n + 1)]
  trans chainAdj L n (n + 1) * pathWeight (chainAdj L) u n
  · rw [Finset.sum_eq_single n]
    · intro b hb hba
      rw [chainAdj_ne L (show n + 1 ≠ b + 1 from by omega)]
      ring
    · intro hc
      exact absurd (Finset.mem_range.mpr (by omega : n < n + 1)) hc
  · rw [chainAdj_succ]
    by_cases hu : u = n
    · subst hu
      rw [pathWeight_self, chain_prod_single]
      ring
    · have hn : n = (n - 1) + 1 := by omega
      rw [hn, ih (n - 1) (by omega) u (by omega : u ≤ n - 1),
        Finset.prod_Icc_succ_top (f := L) (show u ≤ (n - 1) + 1 from by omega)]
      ring

/-- 链式闭式的两种形态桥接：路径和退化形（ε_{n+1} 单列）
    ⟺ Feasibility.lean 的统一形（空积 = 1 吸收末项，第 9 行）。 -/
lemma chain_two_forms (ε L : ℕ → ℝ) (n : ℕ) :
    ε (n + 1) + ∑ u ∈ range (n + 1), ε u * ∏ j ∈ Icc u n, L j
      = ∑ i ∈ range (n + 2), ε i * ∏ j ∈ Icc i n, L j := by
  conv_rhs => rw [Finset.sum_range_succ]
  have hp : (∏ j ∈ Icc (n + 1) n, L j) = 1 := by
    have hempty : Icc (n + 1) n = (∅ : Finset ℕ) := by
      refine Finset.eq_empty_iff_forall_notMem.mpr ?_
      intro a ha
      have := Finset.mem_Icc.mp ha
      omega
    rw [hempty, Finset.prod_empty]
  rw [hp]
  ring

/-- **链特例主定理 = 引理 2.1**（02 篇第 5-19 行，闭式在第 9 行）：
    单父链上，端到端误差
    `|Xh (n+1) − X (n+1)| ≤ Σ_{i<n+2} ε_i · Π_{j∈Icc i n} L_j`
    ——第 j 步误差 ε_j 经其后所有环节 `Π_{m>j} L_m` 放大后计入总和。
    RHS 与 Feasibility.chain_error_closed_form 的闭式完全同形（对照成立）。
    证明路线刻意经过路径和形式：泛型定理给 `Σ_u ε_u·W u (n+1)`，
    再用 pathWeight_chain_lt 把 W 退化为链乘积——展示"所有路径求和"
    在单父链上只剩一条路径（第 59 行）。 -/
theorem chain_error_propagation_bound (L : ℕ → ℝ) (f fh : ℕ → (ℕ → ℝ) → ℝ) (X Xh ε e : ℕ → ℝ)
    (hL : ∀ i, 0 ≤ L i)
    (hX : ∀ i, X i = f i X) (hXh : ∀ i, Xh i = fh i Xh)
    (herr : ∀ i (x : ℕ → ℝ), |fh i x - f i x| ≤ ε i)
    (hloc : ∀ i (x y : ℕ → ℝ), (∀ k < i, x k = y k) → f i x = f i y)
    (hpar : ∀ (x y : ℕ → ℝ) (i : ℕ), x i = y i → f (i + 1) x = f (i + 1) y)
    (hlipc : ∀ i (x y : ℕ → ℝ), (∀ k < i + 1, k ≠ i → x k = y k) →
      |f (i + 1) x - f (i + 1) y| ≤ L i * |x i - y i|)
    (he0 : e 0 = ε 0)
    (herec : ∀ i, e (i + 1) = ε (i + 1) + L i * e i)
    (n : ℕ) :
    |Xh (n + 1) - X (n + 1)| ≤ ∑ i ∈ range (n + 2), ε i * ∏ j ∈ Icc i n, L j := by
  -- 泛型假设实例化：非父边处 chainAdj = 0，界退化为等式（hpar 保证）
  have hadjnn : ∀ j i, 0 ≤ chainAdj L j i := chainAdj_nonneg L hL
  have hlipgen : ∀ i j (x y : ℕ → ℝ), j < i → (∀ k < i, k ≠ j → x k = y k) →
      |f i x - f i y| ≤ chainAdj L j i * |x j - y j| := by
    intro i j x y hj hagree
    by_cases hij : i = j + 1
    · have hji : j + 1 = i := hij.symm
      rw [← hji] at hagree ⊢
      rw [chainAdj_succ]
      exact hlipc j x y hagree
    · have hji1 : j + 1 < i := by omega
      have hip : (i - 1) + 1 = i := by omega
      have hxi : x (i - 1) = y (i - 1) := hagree (i - 1) (by omega) (by omega)
      have heq : f i x = f i y := by
        have hp := hpar x y (i - 1) hxi
        rwa [hip] at hp
      rw [chainAdj_ne L hij, heq]
      simp
  -- 泛型递推在链上的形态（Feasibility 式递推 ⟹ 泛型递推）
  have he : ∀ i, e i = ε i + ∑ j ∈ range i, chainAdj L j i * e j := by
    intro i
    cases i with
    | zero => simp [he0]
    | succ m => rw [herec m, chain_rec_sum]
  -- 定理 2.5 完整式在链上的实例：路径和形式
  have hps := counterfactual_closed_form f fh X Xh ε e (chainAdj L) hX hXh herr hloc
    hlipgen hadjnn he (n + 1)
  -- 路径权重退化为链乘积（第 59 行：链情形只剩一条路径）
  have hps' : (∑ u ∈ range (n + 1), ε u * pathWeight (chainAdj L) u (n + 1))
      = (∑ u ∈ range (n + 1), ε u * ∏ j ∈ Icc u n, L j) := by
    apply Finset.sum_congr rfl
    intro u hu
    have hun : u ≤ n := by
      have := Finset.mem_range.mp hu
      omega
    rw [pathWeight_chain_lt L n u hun]
  rw [hps'] at hps
  rwa [chain_two_forms] at hps

end AscendLean
