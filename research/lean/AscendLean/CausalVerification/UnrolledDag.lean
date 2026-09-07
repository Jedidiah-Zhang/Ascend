import Mathlib

/-!
# 时间展开无环 — 微步偏序与滞后父模板（C2 验收的理论内核）

出处：
- `docs/研究理论/世界基座/00-假设与记号.md` §2（时间展开图 `G^unroll` 无环）
- `docs/研究理论/世界基座/04-世界验收协议.md` C2（时间展开无环）
- `docs/研究理论/第一阶段实施定义.md` §4/§5（微步偏序、空间父模板）

声明层规则（第一阶段只接受满足以下规则的世界声明）：
1. 跨逻辑帧边必须带滞后 λ ≥ 1；
2. 同帧边只允许从父微步 r' 指向子微步 r，且 r' < r；
3. 父、子微步都在合法范围 [0, R] 内（同一微步内的"边"被第一阶段排除）。

本文件证明：在满足上述规则的世界声明下，任意窗口内的
时间展开图无环——任意节点都不能沿有向边回到自身。

编码：节点 = (模板, 逻辑帧, 微步)；无环性通过秩 (t, r) 的字典序证明：
每条边使秩严格上升，而秩空间经 `t·(R+1)+r` 编码进 ℕ 后良基。
-/

namespace AscendLean.CausalVerification

/-- 时间展开图的节点：模板编号、逻辑帧、微步。 -/
structure Node where
  v : ℕ
  t : ℕ
  r : ℕ
deriving DecidableEq

/-- 时间秩的字典序：先比逻辑帧，再比微步。 -/
inductive TimeLt : ℕ × ℕ → ℕ × ℕ → Prop
  | tick (t t' r r' : ℕ) (h : t < t') : TimeLt (t, r) (t', r')
  | step (t r r' : ℕ) (h : r < r') : TimeLt (t, r) (t, r')

/-- 秩测度：在微步有界 R 时，(t, r) ↦ t·(R+1)+r 沿字典序严格递增。 -/
def timeMeas (R : ℕ) (p : ℕ × ℕ) : ℕ := p.1 * (R + 1) + p.2

theorem timeMeas_lt_of_timeLt {R : ℕ} {p q : ℕ × ℕ}
    (hp : p.2 ≤ R) (hq : q.2 ≤ R) (h : TimeLt p q) :
    timeMeas R p < timeMeas R q := by
  cases h with
  | tick t t' r r' ht =>
      calc
        timeMeas R (t, r) = t * (R + 1) + r := rfl
        _ ≤ t * (R + 1) + R := Nat.add_le_add_left hp _
        _ < (t + 1) * (R + 1) := by
            rw [Nat.add_mul, Nat.one_mul]
            exact Nat.add_lt_add_left (Nat.lt_succ_self R) _
        _ ≤ t' * (R + 1) := Nat.mul_le_mul_right (R + 1) (Nat.succ_le_of_lt ht)
        _ ≤ t' * (R + 1) + r' := Nat.le_add_right _ _
  | step t r r' hr =>
      exact Nat.add_lt_add_left hr _

/-- 单个父模板声明：父模板、滞后步数（0 = 同帧）、父微步。 -/
structure ParentSpec where
  par : ℕ
  lag : ℕ
  pr : ℕ
deriving DecidableEq, Repr

/-- 世界声明：模板 v 在微步 r 的方程所声明的全部父模板。 -/
abbrev Decl := ℕ → ℕ → Finset ParentSpec

/-- 声明合法性：所有父、子微步都在 [0, R]；同帧父（lag = 0）必须来自更早微步。 -/
def WellFormed (R : ℕ) (D : Decl) : Prop :=
  ∀ (v r : ℕ) (spec : ParentSpec),
    spec ∈ D v r → r ≤ R ∧ spec.pr ≤ R ∧ (spec.lag = 0 → spec.pr < r)

/-- 时间展开边：p 是 c 的直接父节点。
    滞后 0：同帧，要求 p.r = spec.pr（合法性再保证 pr < c.r）；
    滞后 ≥ 1：跨帧，要求 c.t = p.t + spec.lag。 -/
def UnrollEdge (D : Decl) (p c : Node) : Prop :=
  ∃ (spec : ParentSpec),
    spec ∈ D c.v c.r ∧
    spec.par = p.v ∧
    spec.pr = p.r ∧
    ((spec.lag = 0 ∧ c.t = p.t) ∨ (1 ≤ spec.lag ∧ c.t = p.t + spec.lag))

/-- 每条展开边都使时间秩严格上升，且两端微步合法。 -/
theorem unrollEdge_timeLt {R : ℕ} {D : Decl} (hD : WellFormed R D) {p c : Node}
    (hE : UnrollEdge D p c) :
    TimeLt (p.t, p.r) (c.t, c.r) ∧ p.r ≤ R ∧ c.r ≤ R := by
  rcases hE with ⟨spec, hmem, hpar, hpr, htime⟩
  have hb := hD c.v c.r spec hmem
  rcases hb with ⟨hcR, hprR, hlag⟩
  have hprR' : p.r ≤ R := by simpa [hpr] using hprR
  rcases htime with hsame | hcross
  · rcases hsame with ⟨hlag0, htick⟩
    have hstep : p.r < c.r := by
      have : spec.pr < c.r := hlag hlag0
      simpa [hpr] using this
    exact ⟨by simpa [htick] using TimeLt.step p.t p.r c.r hstep, hprR', hcR⟩
  · rcases hcross with ⟨hlag1, htick⟩
    have hpos : 0 < spec.lag := lt_of_lt_of_le Nat.zero_lt_one hlag1
    have ht : p.t < c.t := by
      rw [htick]
      exact Nat.lt_add_of_pos_right hpos
    exact ⟨TimeLt.tick p.t c.t p.r c.r ht, hprR', hcR⟩

/-- k 步可达：存在恰好 k 条边的有向路径。 -/
def Steps (E : Node → Node → Prop) : ℕ → Node → Node → Prop
  | 0, a, b => a = b
  | k + 1, a, b => ∃ c, E a c ∧ Steps E k c b

/-- 无环：任意节点都不能沿非空路径回到自身。 -/
def Acyclic (E : Node → Node → Prop) : Prop :=
  ∀ a k, 0 < k → ¬ Steps E k a a

/-- 沿合法声明的展开边，任意非空路径的秩测度严格递增。 -/
theorem steps_meas_lt {R : ℕ} {D : Decl} (hD : WellFormed R D) :
    ∀ {a b : Node} {k : ℕ},
      0 < k → Steps (UnrollEdge D) k a b →
      timeMeas R (a.t, a.r) < timeMeas R (b.t, b.r) := by
  intro a b k hpos hs
  induction k generalizing a b with
  | zero => exact (Nat.lt_irrefl 0 hpos).elim
  | succ k ih =>
      rcases hs with ⟨c, hE, hstep⟩
      have hE' := unrollEdge_timeLt hD hE
      rcases hE' with ⟨hT, hpa, hcR⟩
      have hac : timeMeas R (a.t, a.r) < timeMeas R (c.t, c.r) :=
        timeMeas_lt_of_timeLt hpa hcR hT
      by_cases hk : k = 0
      · subst hk
        have hcb : c = b := hstep
        rwa [← hcb]
      · have hcb : timeMeas R (c.t, c.r) < timeMeas R (b.t, b.r) :=
          ih (Nat.pos_of_ne_zero hk) hstep
        exact lt_trans hac hcb

/-- **时间展开无环**（C2 的理论内核）：合法声明下，时间展开图无环。 -/
theorem unroll_acyclic {R : ℕ} {D : Decl} (hD : WellFormed R D) :
    Acyclic (UnrollEdge D) := by
  intro a k hpos hs
  have hlt := steps_meas_lt hD hpos hs
  exact (Nat.lt_irrefl (timeMeas R (a.t, a.r)) hlt).elim

end AscendLean.CausalVerification
