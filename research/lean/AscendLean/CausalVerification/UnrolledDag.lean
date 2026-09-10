import Mathlib

/-!
# 时间展开无环 — 阶段次序与滞后父模板（C2 验收的理论内核）

出处：
- `docs/研究理论/工程符号体系.md` §3（时间展开图 `\mathcal G^unroll` 无环）
- `docs/研究理论/世界基座/04-世界验收协议.md` C2（时间展开无环）
- `docs/研究理论/第一阶段实施定义.md` §4/§5（阶段声明、空间父模板）

声明层规则（第一阶段只接受满足以下规则的世界声明）：
1. 跨逻辑帧边必须带滞后 λ ≥ 1；
2. 同帧边只允许从较早阶段 r' 指向较晚阶段 r，且 r' < r；
3. 父、子阶段都在合法范围 [0, R] 内（同一阶段内的"边"被第一阶段排除）。

本文件证明：在满足上述规则的世界声明下，任意窗口内的
时间展开图无环——任意节点都不能沿有向边回到自身。

**有限索引（issue #46 P5）**：声明、节点与父模板的参数一律以
`Fin n` / `Fin m` 索引（n = 分量数，m = 更新阶段数），而不是自然数。
理由：合法性本身包含"参数在界内"，用有界索引把这条**编码进类型**，
实例化时便可直接判定（`decide`/`native_decide`），无需为
"范围外为空"这类命题在无限域上做全称推理。

编码：节点按（分量模板, 逻辑帧, 更新阶段）索引；无环性通过秩 (t, r) 的字典序证明：
每条边使秩严格上升，而秩空间经 `t·m+r` 编码进 ℕ 后良基。
-/

namespace AscendLean.CausalVerification

/-- 时间展开图的节点：分量模板（有界）、逻辑帧、更新阶段（有界）。 -/
structure Node (n m : ℕ) where
  v : Fin n
  t : ℕ
  r : Fin m
deriving DecidableEq

/-- 时间秩的字典序：先比逻辑帧，再比阶段。 -/
inductive TimeLt : ℕ × ℕ → ℕ × ℕ → Prop
  | tick (t t' r r' : ℕ) (h : t < t') : TimeLt (t, r) (t', r')
  | step (t r r' : ℕ) (h : r < r') : TimeLt (t, r) (t, r')

/-- 秩测度：阶段数 m 下，(t, r) ↦ t·(m+1)+r 沿字典序严格递增。 -/
def timeMeas (m : ℕ) (p : ℕ × ℕ) : ℕ := p.1 * (m + 1) + p.2

theorem timeMeas_lt_of_timeLt {m : ℕ} {p q : ℕ × ℕ}
    (hp : p.2 ≤ m) (hq : q.2 ≤ m) (h : TimeLt p q) :
    timeMeas m p < timeMeas m q := by
  cases h with
  | tick t t' r r' ht =>
      calc
        timeMeas m (t, r) = t * (m + 1) + r := rfl
        _ ≤ t * (m + 1) + m := Nat.add_le_add_left hp _
        _ < (t + 1) * (m + 1) := by
            rw [Nat.add_mul, Nat.one_mul]
            exact Nat.add_lt_add_left (Nat.lt_succ_self m) _
        _ ≤ t' * (m + 1) := Nat.mul_le_mul_right (m + 1) (Nat.succ_le_of_lt ht)
        _ ≤ t' * (m + 1) + r' := Nat.le_add_right _ _
  | step t r r' hr =>
      exact Nat.add_lt_add_left hr _

/-- 单个父模板声明：父分量模板（有界）、滞后步数（`Fin K`，K 为滞后上界）、
父阶段（有界）。

所有参数都是有界索引——"参数在界内"由类型保证，且结构本身有限
（`Fintype`），使 `WellFormed` 成为**可判定**命题。滞后上界单列为参数
`K`：第一阶段只用同帧与上一帧（K = 2），更大的滞后由调用方声明。 -/
structure ParentSpec (n m K : ℕ) where
  par : Fin n
  lag : Fin K
  pr : Fin m
deriving DecidableEq, Repr, Fintype

/-- 世界声明：分量模板 v 在阶段 r 的方程所声明的全部父模板。

值是 `List`（而非 `Finset`）：声明是生成物、重复项由生成器保证不出现，
而列表让 `wellFormedCheck` 保持**可计算**（`Finset.toList` 依赖选择公理）。 -/
abbrev Decl (n m K : ℕ) := Fin n → Fin m → List (ParentSpec n m K)

/-- 声明合法性：同帧父（lag = 0）必须来自更早阶段。
父分量/父阶段/滞后的界由 `Fin n` / `Fin m` / `Fin K` 类型保证。 -/
def WellFormed {n m K : ℕ} (D : Decl n m K) : Prop :=
  ∀ (v : Fin n) (r : Fin m) (spec : ParentSpec n m K),
    spec ∈ D v r → (spec.lag.val = 0 → spec.pr.val < r.val)

/-- 合法性的**可计算判定式**：真值等于 `WellFormed`。

显式给出（而不是依赖实例搜索）：`D` 是局部变量时，`Decidable (WellFormed D)`
无法由类型类合成——判定式在命题里带绑定变量，实例搜索不会展开它。
因此在 `Fin` 与 `Finset` 的**列表视图**上显式递归求与（全部可计算）。 -/
def wellFormedCheck {n m K : ℕ} (D : Decl n m K) : Bool :=
  (List.finRange n).all fun v =>
    (List.finRange m).all fun r =>
      (D v r).all fun spec =>
        decide (¬ (spec ∈ D v r)) ||
          decide (spec.lag.val = 0 → spec.pr.val < r.val)

/-- 判定式为真 ⟹ 声明合法。 -/
theorem wellFormed_of_check {n m K : ℕ} {D : Decl n m K}
    (h : wellFormedCheck D = true) : WellFormed D := by
  intro v r spec hmem
  unfold wellFormedCheck at h
  have h1 := List.all_eq_true.mp h
  have h2 := h1 v (List.mem_finRange v)
  have h3 := List.all_eq_true.mp h2
  have h4 := h3 r (List.mem_finRange r)
  have h5 := List.all_eq_true.mp h4
  have h6 := h5 spec hmem
  by_contra hc
  have hz : (decide (¬ (spec ∈ D v r)) ||
      decide (spec.lag.val = 0 → spec.pr.val < r.val)) = false := by
    rw [Bool.or_eq_false_iff]
    exact ⟨by simpa using hmem, by simpa using hc⟩
  rw [hz] at h6
  exact Bool.noConfusion h6

/-- 时间展开边：p 是 c 的直接父节点。
    滞后 0：同帧，要求 p.r = spec.pr（合法性再保证 pr.val < c.r.val）；
    滞后 ≥ 1：跨帧，要求 c.t = p.t + spec.lag。 -/
def UnrollEdge {n m : ℕ} (K : ℕ) (D : Decl n m K) (p c : Node n m) : Prop :=
  ∃ (spec : ParentSpec n m K),
    spec ∈ D c.v c.r ∧
    spec.par = p.v ∧
    spec.pr = p.r ∧
    ((spec.lag.val = 0 ∧ c.t = p.t) ∨
      (1 ≤ spec.lag.val ∧ c.t = p.t + spec.lag.val))

/-- 每条展开边都使时间秩严格上升。 -/
theorem unrollEdge_timeLt {n m K : ℕ} {D : Decl n m K} (hD : WellFormed D)
    {p c : Node n m} (hE : UnrollEdge K D p c) :
    TimeLt (p.t, p.r.val) (c.t, c.r.val) := by
  rcases hE with ⟨spec, hmem, hpar, hpr, htime⟩
  have hlag := hD c.v c.r spec hmem
  rcases htime with hsame | hcross
  · rcases hsame with ⟨hlag0, htick⟩
    have hstep : p.r.val < c.r.val := by
      have : spec.pr.val < c.r.val := hlag hlag0
      simpa [hpr] using this
    exact by simpa [htick] using TimeLt.step p.t p.r.val c.r.val hstep
  · rcases hcross with ⟨hlag1, htick⟩
    have hpos : 0 < spec.lag.val := lt_of_lt_of_le Nat.zero_lt_one hlag1
    have ht : p.t < c.t := by
      rw [htick]
      exact Nat.lt_add_of_pos_right hpos
    exact TimeLt.tick p.t c.t p.r.val c.r.val ht

/-- k 步可达：存在恰好 k 条边的有向路径。 -/
def Steps {n m : ℕ} (E : Node n m → Node n m → Prop) : ℕ → Node n m → Node n m → Prop
  | 0, a, b => a = b
  | k + 1, a, b => ∃ c, E a c ∧ Steps E k c b

/-- 无环：任意节点都不能沿非空路径回到自身。 -/
def Acyclic {n m : ℕ} (E : Node n m → Node n m → Prop) : Prop :=
  ∀ a k, 0 < k → ¬ Steps E k a a

/-- 沿合法声明的展开边，任意非空路径的秩测度严格递增。 -/
theorem steps_meas_lt {n m K : ℕ} {D : Decl n m K} (hD : WellFormed D) :
    ∀ {a b : Node n m} {k : ℕ},
      0 < k → Steps (UnrollEdge K D) k a b →
      timeMeas m (a.t, a.r.val) < timeMeas m (b.t, b.r.val) := by
  intro a b k hpos hs
  induction k generalizing a b with
  | zero => exact (Nat.lt_irrefl 0 hpos).elim
  | succ k ih =>
      rcases hs with ⟨c, hE, hstep⟩
      have hT := unrollEdge_timeLt hD hE
      have hpa : a.r.val ≤ m := Nat.le_of_lt a.r.isLt
      have hcR : c.r.val ≤ m := Nat.le_of_lt c.r.isLt
      have hac : timeMeas m (a.t, a.r.val) < timeMeas m (c.t, c.r.val) :=
        timeMeas_lt_of_timeLt hpa hcR hT
      by_cases hk : k = 0
      · subst hk
        have hcb : c = b := hstep
        rwa [← hcb]
      · have hcb : timeMeas m (c.t, c.r.val) < timeMeas m (b.t, b.r.val) :=
          ih (Nat.pos_of_ne_zero hk) hstep
        exact lt_trans hac hcb

/-- **时间展开无环**（C2 的理论内核）：合法声明下，时间展开图无环。 -/
theorem unroll_acyclic {n m K : ℕ} {D : Decl n m K} (hD : WellFormed D) :
    Acyclic (UnrollEdge K D) := by
  intro a k hpos hs
  have hlt := steps_meas_lt hD hpos hs
  exact (Nat.lt_irrefl (timeMeas m (a.t, a.r.val)) hlt).elim

end AscendLean.CausalVerification
