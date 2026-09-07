import Mathlib

/-!
# 三类动态干预的轨迹语义（第一阶段实施定义 §7 / 世界基座 04 篇 W2）

出处：
- `docs/研究理论/第一阶段实施定义.md` §7（干预执行契约）、§9.2/§9.3（W1/W2）
- `docs/研究理论/世界基座/04-世界验收协议.md` §3.2/§3.3
- `docs/研究理论/研究综述.md` §2.4（干预是结构方程的外部替换，非状态任意修改）

干预不是对状态的任意修改，而是对时间展开结构方程中指定节点的外部替换。
本文件在一维自治系统 `x_{t+1} = F x_t` 上形式化三类动态干预的轨迹语义：

- 节点干预 `nodeDo`：只替换目标逻辑帧的方程，干预后由原机制继续演化；
- 持续干预 `persist`：在窗口内逐帧重复节点替换，窗口结束后恢复原机制；
- 机制干预 `mechDo`：从目标帧起替换机制，此后一直使用新机制。

证明内容：
1. `persist` 窗口长度 1 与 `nodeDo` 轨迹逐帧相等（"一次设值"是"持续冻结"的
   特例，但不能反过来说持续冻结是一次设值）；
2. 干预前轨迹与原轨迹一致（干预不改变过去）；
3. 数值见证：在 `F x = x + 1` 上，节点干预（v=10，t=1）、持续干预
   （窗口 [1,2)）、机制干预（F' x = x + 100）三条轨迹在可手算的帧上
   两两不同——对应 W2 的"三种操作不得实现成同一个操作"；
4. 机制干预不改变机制时（F' = F）与无干预轨迹一致（替换是"惰性"的）。

参数干预与机制干预在轨迹层同形（都是替换函数），区别在于替换的是参数槽位
还是机制槽位——该区别由世界规范的元数据（T_d、rep_d 的槽位）区分，
不在本文件建模。
-/

namespace AscendLean.CausalVerification

/-- 自治轨迹：x₀ 后逐帧应用 F。 -/
def traj (F : ℝ → ℝ) (x₀ : ℝ) : ℕ → ℝ
  | 0 => x₀
  | n + 1 => F (traj F x₀ n)

/-- 节点干预：第 t 帧方程替换为常数 v，其余帧照常。 -/
def nodeDo (F : ℝ → ℝ) (x₀ v : ℝ) (t : ℕ) : ℕ → ℝ
  | 0 => x₀
  | n + 1 => if n + 1 = t then v else F (nodeDo F x₀ v t n)

/-- 持续干预：窗口 [t, t+w) 内逐帧替换为常数 v，窗口结束后恢复原机制。 -/
def persist (F : ℝ → ℝ) (x₀ v : ℝ) (t w : ℕ) : ℕ → ℝ
  | 0 => x₀
  | n + 1 => if t ≤ n + 1 ∧ n + 1 < t + w then v else F (persist F x₀ v t w n)

/-- 机制干预：第 t 帧起机制替换为 F'。 -/
def mechDo (F F' : ℝ → ℝ) (x₀ : ℝ) (t : ℕ) : ℕ → ℝ
  | 0 => x₀
  | n + 1 => if n + 1 < t then F (mechDo F F' x₀ t n) else F' (mechDo F F' x₀ t n)

/-- 窗口条件 [t, t+1) 与"恰好是第 t 帧"等价（persist w=1 = nodeDo 的关键）。 -/
lemma persist_window_one {t n : ℕ} : (t ≤ n ∧ n < t + 1) ↔ n = t := by
  omega

/-- **persist 窗口长度 1 = nodeDo**：一次设值是持续冻结的特例。 -/
theorem persist_one_eq_nodeDo (F : ℝ → ℝ) (x₀ v : ℝ) (t : ℕ) :
    persist F x₀ v t 1 = nodeDo F x₀ v t := by
  funext n
  induction n with
  | zero => simp [persist, nodeDo]
  | succ n ih =>
      simp only [persist, nodeDo]
      by_cases hw : t ≤ n + 1 ∧ n + 1 < t + 1
      · have h : n + 1 = t := by omega
        exact Eq.trans (ite_eq_left hw) (Eq.symm (ite_eq_left h))
      · have h : ¬ n + 1 = t := by omega
        rw [ite_eq_right hw, ite_eq_right h, ih]

/-- 干预不改变过去：干预帧之前的轨迹与原轨迹一致。 -/
theorem nodeDo_before_eq_traj (F : ℝ → ℝ) (x₀ v : ℝ) (t : ℕ) {n : ℕ} (hn : n < t) :
    nodeDo F x₀ v t n = traj F x₀ n := by
  induction n with
  | zero => simp [nodeDo, traj]
  | succ n ih =>
      have hn' : n < t := by omega
      have hne : n + 1 ≠ t := by omega
      simp [nodeDo, traj, ih hn', hne]

/-- 机制干预不改变机制时（F' = F）与无干预轨迹一致。 -/
theorem mechDo_same_eq_traj (F : ℝ → ℝ) (x₀ : ℝ) (t : ℕ) :
    mechDo F F x₀ t = traj F x₀ := by
  funext n
  induction n with
  | zero => simp [mechDo, traj]
  | succ n ih =>
      simp only [mechDo, traj, ih]
      by_cases h : n + 1 < t <;> simp [h]

/-! ## 数值见证（W2：三种操作不得实现成同一个操作） -/

def inc1 : ℝ → ℝ := fun x => x + 1
def inc100 : ℝ → ℝ := fun x => x + 100

/-- 节点干预（t=1, v=10）在第 1 帧钉死为 10。 -/
theorem nodeDo_t1 : nodeDo inc1 0 10 1 1 = 10 := by
  norm_num [nodeDo, inc1]

/-- 节点干预（t=1, v=10）在第 2 帧回到原机制：F(10) = 11。 -/
theorem nodeDo_t2 : nodeDo inc1 0 10 1 2 = 11 := by
  norm_num [nodeDo, inc1]

/-- 持续干预（窗口 [1,2), v=10）在第 2 帧仍在窗口内：仍为 10。 -/
theorem persist2_t2 : persist inc1 0 10 1 2 2 = 10 := by
  norm_num [persist, inc1]

/-- 持续干预窗口结束后恢复原机制：第 3 帧 F(10) = 11。 -/
theorem persist2_t3 : persist inc1 0 10 1 2 3 = 11 := by
  norm_num [persist, inc1]

/-- 机制干预（t=1, F' = +100）在第 1 帧：F'(0) = 100。 -/
theorem mech_t1 : mechDo inc1 inc100 0 1 1 = 100 := by
  norm_num [mechDo, inc1, inc100]

/-- 机制干预（t=1, F' = +100）在第 2 帧：F'(100) = 200。 -/
theorem mech_t2 : mechDo inc1 inc100 0 1 2 = 200 := by
  norm_num [mechDo, inc1, inc100]

/-- 节点干预 ≠ 持续干预（第 2 帧：11 vs 10）。 -/
theorem nodeDo_ne_persist2 : nodeDo inc1 0 10 1 ≠ persist inc1 0 10 1 2 := by
  intro h
  have h2 := congrFun h 2
  norm_num [nodeDo, persist, inc1] at h2

/-- 节点干预 ≠ 机制干预（第 1 帧：10 vs 100）。 -/
theorem nodeDo_ne_mech : nodeDo inc1 0 10 1 ≠ mechDo inc1 inc100 0 1 := by
  intro h
  have h1 := congrFun h 1
  norm_num [nodeDo, mechDo, inc1, inc100] at h1

/-- 持续干预 ≠ 机制干预（第 1 帧：10 vs 100）。 -/
theorem persist2_ne_mech : persist inc1 0 10 1 2 ≠ mechDo inc1 inc100 0 1 := by
  intro h
  have h1 := congrFun h 1
  norm_num [persist, mechDo, inc1, inc100] at h1

end AscendLean.CausalVerification
