import Mathlib
import AscendLean.CausalVerification.LipschitzLayer

/-!
# SubSCM — 显式 do 干预结构（02 篇定理 2.5 干预情形的定义级编码）

出处：`docs/研究理论/因果理论验证/02-误差传播与反事实.md` 定理 2.5
（第 49-69 行：设定 do(X_s = x_s)、命题递推与路径和闭式）；
干预的工程语义见 04 篇 §"与引擎实现的接口"（变量级 do：冻结/覆盖单变量求值器）。

`LipschitzLayer.lean` 第五节对干预的处理是**假设级**的：
`hpin : X s = Xh s`（双方钉死同值）+ 自洽性只在 i ≠ s 处要求。
本文件把 do 提升为**定义级结构**：

1. `subSCM f s v`：把 f 的第 s 个方程替换为常数 v（换常数方程）。
   结构方程编码下这同时实现"断入边"——干预点取值与输入无关
   （`subSCM_const_indep`）；
2. 假设传递三引理（`subSCM_err_update` / `subSCM_loc` / `subSCM_lip`）：
   替换后 SCM 逐条继承原 SCM 的分析假设，干预点处模型误差归零；
3. 主定理 `subSCM_counterfactual_bound`：从泛型
   `counterfactual_closed_form` 以 ε' = Function.update ε s 0 直接实例化——
   干预源项 ε' s = 0（对应第 63 行求和限制在 Anc(t)\S，
   见 `subSCM_source_vanishes` 与清洁形式 `subSCM_counterfactual_bound_erase`）。
   接口沿用本库约定：递推上界以假设对 (e, he) 供给；
4. 互证：接口等价（`encoded_hyps_of_subSCM` / `subSCM_selfcons_of_encoded`）、
   双路线一致（`subSCM_bound_via_encoded`：经编码版
   `counterfactual_closed_form_do` 导出与主定理相同的界）、
   升级关系（`subSCM_bound_of_encoded`：任意编码版应用可无损升级为
   更紧的 ε'-形界）。
-/

open Finset
open scoped BigOperators

namespace AscendLean.CausalVerification

/-! ## 第一节：SubSCM 定义与基本方程 -/

/-- **do(X_s := v) 后的子模型**（SubSCM）：把第 s 个结构方程替换为常数 v，
    其余方程原样保留（02 篇第 53-55 行干预设定的结构方程编码；
    04 篇"变量级 do：冻结/覆盖单变量求值器"）。
    换常数方程同时实现断入边：干预点取值与输入无关
    （`subSCM_const_indep`）。 -/
def subSCM (f : ℕ → (ℕ → ℝ) → ℝ) (s : ℕ) (v : ℝ) : ℕ → (ℕ → ℝ) → ℝ :=
  fun i x => if i = s then v else f i x

/-- SubSCM 在干预点取常数 v（换常数方程）。 -/
@[simp] theorem subSCM_eq_const (f : ℕ → (ℕ → ℝ) → ℝ) (s : ℕ) (v : ℝ) (x : ℕ → ℝ) :
    subSCM f s v s x = v := by
  simp [subSCM]

/-- SubSCM 在其余节点保持原方程（未干预部分不受干预影响）。 -/
theorem subSCM_eq_orig (f : ℕ → (ℕ → ℝ) → ℝ) {s : ℕ} {v : ℝ} {i : ℕ} (h : i ≠ s)
    (x : ℕ → ℝ) : subSCM f s v i x = f i x := by
  simp [subSCM, h]

/-- **断入边**：干预点的方程不读任何输入坐标
    （02 篇第 55 行 `e_s = 0` 的结构来源：do 后 s 被钉死，父坐标失去影响）。 -/
theorem subSCM_const_indep (f : ℕ → (ℕ → ℝ) → ℝ) (s : ℕ) (v : ℝ) (x y : ℕ → ℝ) :
    subSCM f s v s x = subSCM f s v s y := by
  rw [subSCM_eq_const, subSCM_eq_const]

/-- **SubSCM 自洽性的展开形式**：轨迹是 `subSCM f s v` 的自洽轨迹
    ⟺ 干预点钉死为 v 且其余节点走原方程。两套表述的双向桥。 -/
theorem subSCM_selfcons_iff {f : ℕ → (ℕ → ℝ) → ℝ} {X : ℕ → ℝ} {s : ℕ} {v : ℝ} :
    (∀ i, X i = subSCM f s v i X) ↔ (X s = v ∧ ∀ i, i ≠ s → X i = f i X) := by
  constructor
  · intro h
    refine ⟨?_, fun i hi => ?_⟩
    · simpa [subSCM] using h s
    · have hi' := h i
      rwa [subSCM_eq_orig f hi] at hi'
  · rintro ⟨hsv, hrest⟩ i
    by_cases hi : i = s
    · rw [hi]
      simpa [subSCM] using hsv
    · rw [subSCM_eq_orig f hi]
      exact hrest i hi

/-! ## 第二节：假设传递 — 替换后 SCM 继承原 SCM 的分析假设 -/

/-- 干预点外 ε' = update ε s 0 退回原 ε（具体化引理，供重写使用；
    `Function.update_of_ne` 半应用时模式含元变量，不能直接 rw）。 -/
theorem update_eps_ne {ε : ℕ → ℝ} {s u : ℕ} (h : u ≠ s) :
    Function.update ε s 0 u = ε u :=
  Function.update_of_ne h 0 ε

/-- 假设传递 · 逐点模型误差：干预点两侧同为常数 v 故误差归零
    （ε' = Function.update ε s 0 的 s 分量），其余节点逐字传原界。 -/
theorem subSCM_err_update {f fh : ℕ → (ℕ → ℝ) → ℝ} {ε : ℕ → ℝ} {s : ℕ} {v : ℝ}
    (herr : ∀ i (x : ℕ → ℝ), |fh i x - f i x| ≤ ε i) :
    ∀ i (x : ℕ → ℝ),
      |subSCM fh s v i x - subSCM f s v i x| ≤ Function.update ε s 0 i := by
  intro i x
  by_cases hi : i = s
  · subst hi
    rw [subSCM_eq_const, subSCM_eq_const]
    simp
  · rw [subSCM_eq_orig fh hi, subSCM_eq_orig f hi,
      Function.update_of_ne hi 0 ε]
    exact herr i x

/-- 假设传递 · 局部性：干预点方程为常数天然局部；其余节点传原局部性。 -/
theorem subSCM_loc {f : ℕ → (ℕ → ℝ) → ℝ} {s : ℕ} {v : ℝ}
    (hloc : ∀ i (x y : ℕ → ℝ), (∀ k < i, x k = y k) → f i x = f i y) :
    ∀ i (x y : ℕ → ℝ), (∀ k < i, x k = y k) →
      subSCM f s v i x = subSCM f s v i y := by
  intro i x y hag
  by_cases hi : i = s
  · subst hi
    rw [subSCM_eq_const, subSCM_eq_const]
  · rw [subSCM_eq_orig f hi, subSCM_eq_orig f hi]
    exact hloc i x y hag

/-- 假设传递 · 单坐标 Lipschitz：干预点方程为常数故界平凡成立
    （只需 adj 非负）；其余节点逐字传原界。 -/
theorem subSCM_lip {f : ℕ → (ℕ → ℝ) → ℝ} {adj : ℕ → ℕ → ℝ} {s : ℕ} {v : ℝ}
    (hlip : ∀ i j (x y : ℕ → ℝ), j < i → (∀ k < i, k ≠ j → x k = y k) →
      |f i x - f i y| ≤ adj j i * |x j - y j|)
    (hadjnn : ∀ j i, 0 ≤ adj j i) :
    ∀ i j (x y : ℕ → ℝ), j < i → (∀ k < i, k ≠ j → x k = y k) →
      |subSCM f s v i x - subSCM f s v i y| ≤ adj j i * |x j - y j| := by
  intro i j x y hj hag
  by_cases hi : i = s
  · subst hi
    rw [subSCM_eq_const, subSCM_eq_const, sub_self, abs_zero]
    exact mul_nonneg (hadjnn j i) (abs_nonneg _)
  · rw [subSCM_eq_orig f hi, subSCM_eq_orig f hi]
    exact hlip i j x y hj hag

/-! ## 第三节：主定理 — SubSCM 反事实误差界 -/

/-- **SubSCM 反事实误差界**（02 篇定理 2.5 干预情形，第 51-63 行）：
    do(X_s := v) 下双方同值（CRN + 同一 do 值），替换后 SCM 各自自洽，
    则对一切 t
    `|Xh t − X t| ≤ ε' t + Σ_{u<t} ε' u · W u t`，其中 ε' = Function.update ε s 0。
    证明：以替换后 SCM 与 ε' 实例化泛型 `counterfactual_closed_form`
    （假设传递见第二节；干预点方程为常数使单坐标 Lipschitz 平凡成立）；
    e/he 沿本库约定以假设对供给。 -/
theorem subSCM_counterfactual_bound
    (f fh : ℕ → (ℕ → ℝ) → ℝ) (X Xh ε e : ℕ → ℝ) (adj : ℕ → ℕ → ℝ)
    (s : ℕ) (v : ℝ)
    (hX : ∀ i, X i = subSCM f s v i X)
    (hXh : ∀ i, Xh i = subSCM fh s v i Xh)
    (herr : ∀ i (x : ℕ → ℝ), |fh i x - f i x| ≤ ε i)
    (hloc : ∀ i (x y : ℕ → ℝ), (∀ k < i, x k = y k) → f i x = f i y)
    (hlip : ∀ i j (x y : ℕ → ℝ), j < i → (∀ k < i, k ≠ j → x k = y k) →
      |f i x - f i y| ≤ adj j i * |x j - y j|)
    (hadjnn : ∀ j i, 0 ≤ adj j i)
    (he : ∀ i, e i = Function.update ε s 0 i +
      ∑ j ∈ range i, adj j i * e j)
    (t : ℕ) :
    |Xh t - X t| ≤ Function.update ε s 0 t +
      ∑ u ∈ range t, Function.update ε s 0 u * pathWeight adj u t := by
  exact counterfactual_closed_form (subSCM f s v) (subSCM fh s v) X Xh
    (Function.update ε s 0) e adj hX hXh (subSCM_err_update herr)
    (subSCM_loc hloc) (subSCM_lip hlip hadjnn) hadjnn he t

/-- 干预源项消失（02 篇第 63 行求和限制在 `Anc(t)\S` 的对应物）：
    ε' s = 0 ⟹ 闭式中 u = s 项恒为零，无论路径权重为何。 -/
theorem subSCM_source_vanishes (ε : ℕ → ℝ) (adj : ℕ → ℕ → ℝ) (s t : ℕ) :
    Function.update ε s 0 s * pathWeight adj s t = 0 := by
  rw [Function.update_self]
  ring

/-- **清洁形式**（t ≠ s）：界中 ε' 换回原 ε，且 u = s 项从求和中剔除——
    02 篇第 63 行 `Σ_{u ∈ Anc(t)\S}` 的字面对应
    （剔除集以 `filter (· ≠ s)` 编码；s ∉ range t 时该集合等于全集）。 -/
theorem subSCM_counterfactual_bound_erase
    (f fh : ℕ → (ℕ → ℝ) → ℝ) (X Xh ε e : ℕ → ℝ) (adj : ℕ → ℕ → ℝ)
    (s : ℕ) (v : ℝ) {t : ℕ} (_hts : t ≠ s)
    (hX : ∀ i, X i = subSCM f s v i X)
    (hXh : ∀ i, Xh i = subSCM fh s v i Xh)
    (herr : ∀ i (x : ℕ → ℝ), |fh i x - f i x| ≤ ε i)
    (hloc : ∀ i (x y : ℕ → ℝ), (∀ k < i, x k = y k) → f i x = f i y)
    (hlip : ∀ i j (x y : ℕ → ℝ), j < i → (∀ k < i, k ≠ j → x k = y k) →
      |f i x - f i y| ≤ adj j i * |x j - y j|)
    (hadjnn : ∀ j i, 0 ≤ adj j i)
    (he : ∀ i, e i = Function.update ε s 0 i +
      ∑ j ∈ range i, adj j i * e j) :
    |Xh t - X t| ≤ ε t +
      ∑ u ∈ (range t).filter (fun u => u ≠ s), ε u * pathWeight adj u t := by
  have hmain := subSCM_counterfactual_bound f fh X Xh ε e adj s v hX hXh herr
    hloc hlip hadjnn he t
  rw [Function.update_of_ne _hts 0 ε] at hmain
  have hsum : (∑ u ∈ range t, Function.update ε s 0 u * pathWeight adj u t)
      = ∑ u ∈ (range t).filter (fun u => u ≠ s), ε u * pathWeight adj u t := by
    calc ∑ u ∈ range t, Function.update ε s 0 u * pathWeight adj u t
        = ∑ u ∈ (range t).filter (fun u => u ≠ s),
            Function.update ε s 0 u * pathWeight adj u t
          + ∑ u ∈ (range t).filter (fun u => ¬(u ≠ s)),
            Function.update ε s 0 u * pathWeight adj u t :=
          (Finset.sum_filter_add_sum_filter_not (s := range t)
            (p := fun u => u ≠ s)
            (f := fun u => Function.update ε s 0 u * pathWeight adj u t)).symm
      _ = ∑ u ∈ (range t).filter (fun u => u ≠ s),
            Function.update ε s 0 u * pathWeight adj u t := by
          have hzero : (∑ u ∈ (range t).filter (fun u => ¬(u ≠ s)),
              Function.update ε s 0 u * pathWeight adj u t) = 0 := by
            apply Finset.sum_eq_zero
            intro u hu
            have hu2 : ¬(u ≠ s) := (Finset.mem_filter.mp hu).2
            have hus : u = s := by
              by_contra hne
              exact hu2 hne
            rw [hus, Function.update_self, zero_mul]
          rw [hzero]
          exact add_zero _
      _ = ∑ u ∈ (range t).filter (fun u => u ≠ s), ε u * pathWeight adj u t := by
          apply Finset.sum_congr rfl
          intro u hu
          have hus : u ≠ s := (Finset.mem_filter.mp hu).2
          rw [Function.update_of_ne hus 0 ε]
  rwa [hsum] at hmain

/-! ## 第四节：与编码版互证（LipschitzLayer 接口等价性） -/

/-- **互证①（SubSCM ⟹ 编码式）**：SubSCM 自洽轨迹满足编码版
    `error_recurrence_bound_do` / `counterfactual_closed_form_do` 的
    钉死 + 部分自洽假设——编码接口被 SubSCM 设定涵盖。 -/
theorem encoded_hyps_of_subSCM {f fh : ℕ → (ℕ → ℝ) → ℝ} {X Xh : ℕ → ℝ}
    {s : ℕ} {v : ℝ}
    (hX : ∀ i, X i = subSCM f s v i X)
    (hXh : ∀ i, Xh i = subSCM fh s v i Xh) :
    X s = Xh s ∧ (∀ i, i ≠ s → X i = f i X) ∧ (∀ i, i ≠ s → Xh i = fh i Xh) := by
  refine ⟨?_, fun i hi => ?_, fun i hi => ?_⟩
  · have h1 : X s = v := by
      simpa [subSCM] using hX s
    have h2 : Xh s = v := by
      simpa [subSCM] using hXh s
    exact h1.trans h2.symm
  · have hi' := hX i
    rwa [subSCM_eq_orig f hi] at hi'
  · have hi' := hXh i
    rwa [subSCM_eq_orig fh hi] at hi'

/-- **互证②（编码式 ⟹ SubSCM）**：钉死 + 部分自洽 ⟹ 双方轨迹分别是
    替换后 SCM（干预值取共同钉死值 X s）的自洽轨迹。
    与互证①合起来：两套接口刻画同一类轨迹对。 -/
theorem subSCM_selfcons_of_encoded {f fh : ℕ → (ℕ → ℝ) → ℝ} {X Xh : ℕ → ℝ}
    {s : ℕ} (hpin : X s = Xh s)
    (hX : ∀ i, i ≠ s → X i = f i X) (hXh : ∀ i, i ≠ s → Xh i = fh i Xh) :
    (∀ i, X i = subSCM f s (X s) i X) ∧ (∀ i, Xh i = subSCM fh s (X s) i Xh) := by
  constructor
  · intro i
    by_cases hi : i = s
    · rw [hi]
      simp [subSCM]
    · rw [subSCM_eq_orig f hi]
      exact hX i hi
  · intro i
    by_cases hi : i = s
    · rw [hi, hpin]
      simp [subSCM]
    · rw [subSCM_eq_orig fh hi]
      exact hXh i hi

/-- **互证③（双路线一致）**：把替换后 SCM 整体代入编码版
    `counterfactual_closed_form_do`，得到与主定理逐字相同的界——
    泛型无-do 路线（主定理）与编码 do 路线在此重合。 -/
theorem subSCM_bound_via_encoded
    (f fh : ℕ → (ℕ → ℝ) → ℝ) (X Xh ε e : ℕ → ℝ) (adj : ℕ → ℕ → ℝ)
    (s : ℕ) (v : ℝ)
    (hX : ∀ i, X i = subSCM f s v i X)
    (hXh : ∀ i, Xh i = subSCM fh s v i Xh)
    (herr : ∀ i (x : ℕ → ℝ), |fh i x - f i x| ≤ ε i)
    (hloc : ∀ i (x y : ℕ → ℝ), (∀ k < i, x k = y k) → f i x = f i y)
    (hlip : ∀ i j (x y : ℕ → ℝ), j < i → (∀ k < i, k ≠ j → x k = y k) →
      |f i x - f i y| ≤ adj j i * |x j - y j|)
    (hadjnn : ∀ j i, 0 ≤ adj j i)
    (he : ∀ i, e i = Function.update ε s 0 i +
      ∑ j ∈ range i, adj j i * e j)
    (t : ℕ) :
    |Xh t - X t| ≤ Function.update ε s 0 t +
      ∑ u ∈ range t, Function.update ε s 0 u * pathWeight adj u t := by
  obtain ⟨hpin, -, -⟩ := encoded_hyps_of_subSCM hX hXh
  exact counterfactual_closed_form_do (subSCM f s v) (subSCM fh s v) X Xh
    (Function.update ε s 0) e adj hpin (fun i _ => hX i) (fun i _ => hXh i)
    (subSCM_err_update herr) (subSCM_loc hloc) (subSCM_lip hlip hadjnn)
    hadjnn he t

/-- **互证④（升级关系）**：任意编码版设定的应用可无损升级为
    主定理更紧的 ε'-形界（干预源项显式归零）——旧接口的一切用例
    都被 SubSCM 主定理涵盖并改进。 -/
theorem subSCM_bound_of_encoded
    (f fh : ℕ → (ℕ → ℝ) → ℝ) (X Xh ε e : ℕ → ℝ) (adj : ℕ → ℕ → ℝ)
    {s : ℕ} (hpin : X s = Xh s)
    (hX : ∀ i, i ≠ s → X i = f i X) (hXh : ∀ i, i ≠ s → Xh i = fh i Xh)
    (herr : ∀ i (x : ℕ → ℝ), |fh i x - f i x| ≤ ε i)
    (hloc : ∀ i (x y : ℕ → ℝ), (∀ k < i, x k = y k) → f i x = f i y)
    (hlip : ∀ i j (x y : ℕ → ℝ), j < i → (∀ k < i, k ≠ j → x k = y k) →
      |f i x - f i y| ≤ adj j i * |x j - y j|)
    (hadjnn : ∀ j i, 0 ≤ adj j i)
    (he : ∀ i, e i = Function.update ε s 0 i +
      ∑ j ∈ range i, adj j i * e j)
    (t : ℕ) :
    |Xh t - X t| ≤ Function.update ε s 0 t +
      ∑ u ∈ range t, Function.update ε s 0 u * pathWeight adj u t := by
  obtain ⟨hselfX, hselfXh⟩ := subSCM_selfcons_of_encoded hpin hX hXh
  exact subSCM_counterfactual_bound f fh X Xh ε e adj s (X s) hselfX hselfXh
    herr hloc hlip hadjnn he t

end AscendLean.CausalVerification
