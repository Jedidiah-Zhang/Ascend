import Mathlib
import AscendLean.CausalVerification.LipschitzLayer

/-!
# S4 探针数值锚点 — do 干预求和界的 Lean 证书对照

出处：`docs/研究理论/世界基座/05-理论实验对照.md` S4 判据行 +
`research/toy_scm.py::_s4`（拓扑 X₁, X₂ → X₃ → X₄，do(X₁)，CRN 对齐误差构造）。

探针判据①（预注册）：CRN 误差 ≤ 求和界 `e₄ = ε₄ + L·ε₃ + L²·ε₂`。
本文件给出该界的 **Lean 证书侧数值**，与探针实测 max 误差对照归档：

- 结构注记：do(X₁) 钉死后其误差贡献为零（探针误差剖面中 ε₁ = 0），
  剩余恰是等 Lipschitz 链 X₂ → X₃ → X₄——故用 `LipschitzLayer.chainAdj`
  实例化泛型闭式，`pathWeight_chain_lt` 免去逐路径展开；
- `s4_bound_chain`：符号级——链闭式坍缩为 `ε₄ + ε₃·L + ε₂·L²`；
- `s4_anchor`：代入探针误差剖面后的符号界 `ε·(1 + L + L²)`；
- `s4_anchor_numeric`：探针参数（L = 0.8、ε = 0.02）下的数值锚点
  `0.0488 = 0.02 × 2.44`，即 toy_scm.py 的 `sum_bound`。
-/

open Finset
open scoped BigOperators

namespace AscendLean.CausalVerification

/-- S4 探针的误差剖面：编码根与 do 源贡献为零，活动源 X₂/X₃/X₄
    均为对齐构造误差 ε（toy_scm.py 的 `eps`）。 -/
def s4Eps (ε : ℝ) : ℕ → ℝ := fun u => if u ≤ 1 then 0 else ε

/-- **S4 求和界（符号级）**：等 Lipschitz 链上（每步 L），末端误差闭式
    `e₄ = ε₄ + ε₃·L + ε₂·L²`。前位编码根贡献由剖面置零消去。
    证明：`pathWeight_chain_lt` 把每个 W 退化为链乘积
    （W(3,4)=L、W(2,4)=L²、W(1,4)=L³、W(0,4)=L⁴），前两项乘零消去。 -/
theorem s4_bound_chain (ε : ℕ → ℝ) (L : ℝ) (h0 : ε 0 = 0) (h1 : ε 1 = 0) :
    ε 4 + ∑ u ∈ range 4, ε u * pathWeight (chainAdj (fun _ => L)) u 4
      = ε 4 + ε 3 * L + ε 2 * (L * L) := by
  have hw0 : pathWeight (chainAdj (fun _ => L)) 0 4
      = ∏ j ∈ Finset.Icc 0 3, (fun _ => L) j :=
    pathWeight_chain_lt (fun _ => L) 3 0 (by omega)
  have hw1 : pathWeight (chainAdj (fun _ => L)) 1 4
      = ∏ j ∈ Finset.Icc 1 3, (fun _ => L) j :=
    pathWeight_chain_lt (fun _ => L) 3 1 (by omega)
  have hw2 : pathWeight (chainAdj (fun _ => L)) 2 4
      = ∏ j ∈ Finset.Icc 2 3, (fun _ => L) j :=
    pathWeight_chain_lt (fun _ => L) 3 2 (by omega)
  have hw3 : pathWeight (chainAdj (fun _ => L)) 3 4
      = ∏ j ∈ Finset.Icc 3 3, (fun _ => L) j :=
    pathWeight_chain_lt (fun _ => L) 3 3 (by omega)
  rw [Finset.sum_range_succ, Finset.sum_range_succ, Finset.sum_range_succ,
    Finset.sum_range_succ, hw0, hw1, hw2, hw3]
  rw [h0, h1]
  simp
  ring

/-- **S4 符号锚点**：代入探针误差剖面后，求和界 = `ε·(1 + L + L²)`
    —— 与 toy_scm.py `_s4` 的 `sum_bound` 同式。 -/
theorem s4_anchor (ε L : ℝ) :
    s4Eps ε 4 + ∑ u ∈ range 4, s4Eps ε u * pathWeight (chainAdj (fun _ => L)) u 4
      = ε + ε * L + ε * (L * L) := by
  have h2 : s4Eps ε 2 = ε := by simp [s4Eps]
  have h3 : s4Eps ε 3 = ε := by simp [s4Eps]
  have h4 : s4Eps ε 4 = ε := by simp [s4Eps]
  rw [s4_bound_chain (s4Eps ε) L (by simp [s4Eps]) (by simp [s4Eps]), h2, h3, h4]

/-- **S4 数值锚点**（探针参数 L = 0.8、ε = 0.02）：
    求和界 = 0.02 × (1 + 0.8 + 0.64) = **0.0488**，
    即 toy_scm.py `_s4` 的 `sum_bound = 2.44ε`。 -/
theorem s4_anchor_numeric :
    s4Eps (0.02 : ℝ) 4 + ∑ u ∈ range 4, s4Eps (0.02 : ℝ) u
        * pathWeight (chainAdj (fun _ => (0.8 : ℝ))) u 4
      = 0.0488 := by
  rw [s4_anchor]
  norm_num

end AscendLean.CausalVerification
