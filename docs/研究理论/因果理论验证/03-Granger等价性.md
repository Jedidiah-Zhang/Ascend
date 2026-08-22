# 研究理论 · 03：封闭系统的 Granger 等价性

> 研究方案 §3.2 引入 Granger 因果作为阶段 2 的检验手段，并断言"封闭系统内 Granger 逼近真因果"。本篇把这句话严格化：在什么条件下 Granger 图**等于**因果 DAG 的**直接滞后边**（而非祖先图），在什么条件下失真，失真的条件直接决定阶段 1 的时间粒度设计。

## 定义（条件 Granger 非因果）

Granger 因果的思想可以用一句话概括：如果 $X$ 的过去对预测 $Y$ 的下一步没有增量信息，就称 $X$ 不 Granger-导致 $Y$。形式化地，设 $(X_t)_{t \ge 0}$ 为多元平稳过程，$Z$ 为其余全部已观测变量，称 $X$ **不 Granger-导致** $Y$（记号 $X \nrightarrow_G Y \mid Z$），若对一切 $t$：

$$P\bigl(Y_{t+1} \mid X_{\le t},\, Y_{\le t},\, Z_{\le t}\bigr) \;=\; P\bigl(Y_{t+1} \mid Y_{\le t},\, Z_{\le t}\bigr) \qquad \text{a.s.}$$

即 $X$ 的过去对 $Y$ 的下一步预测无增量信息（Granger 1969 原始定义的条件版本）。

**线性检验**：拟合 $\mathrm{VAR}(p)$，对 $X$ 的滞后系数做联合 F 检验（Granger 1969; Sims 1972；现代表述见 Lütkepohl 2005, §2.3）。

**条件 vs 成对**：条件 Granger 与成对 Granger 是两个不同的定义，注意区分。上述定义条件于其余全部变量（**条件 Granger**），检出的是**直接**因果；而**成对**（pairwise）Granger 只条件于自身历史，检出的是**传递祖先**（含中介路径）。两者不可混用，本篇定理与 05 篇 E1 判据一律按条件 Granger 解读。

## 定理 3.1（线性 VAR · 封闭系统）

**假设**：(i) 过程由线性 SCM 生成，噪声独立；(ii) **无瞬时因果**：$t$ 时刻变量间的全部因果影响都至少延迟一个采样周期；(iii) 全部变量被观测；(iv) 平稳、有限阶。

**命题**：$X_i$ Granger-导致 $X_j$ ⟺ 存在滞后 $\tau \ge 1$ 使 $X_i$ 是 $X_j$ 在时间展开 DAG 中的**直接父母**（即 $A_\tau(j,i) \ne 0$）。

**证明梗概**：线性高斯情形下，条件 Granger 非因果 ⟺ 一切滞后系数 $A_\tau(j,i) = 0$（投影论证：$Y_{t+1}$ 关于 $(X_{\le t}, Y_{\le t}, Z_{\le t})$ 的最优线性预测不依赖 $X_{\le t}$）；而 $A_\tau(j,i) \ne 0$ ⟺ 时间展开图中存在直接边 $X_i(t-\tau) \to X_j(t)$。**中介路径不产生系数**：链 $X \to M \to Y$（各滞后 1 步）中 $A_1(M,X) \ne 0$ 但 $A_\tau(Y,X) = 0$：$M_{\le t}$ 在条件集中吸收全部中介信息，故 $X$ 不 Granger-导致 $Y$，尽管 $X$ 是 $Y$ 的祖先。∎

## 定理 3.2（一般情形 · 图形 Granger）

对非线性、非高斯结构方程的时间序列，同样的"**直接滞后父母 = 条件 Granger 边**"结论在假设 (i)–(iii) 下成立（对合适的函数类）；Eichler (2007) 的框架区分 Granger 因果（祖先关系）与 partial/direct 因果（直接边），本篇使用后者。非线性检验工具见 kernel Granger（Marinazzo et al. 2008）或非参数 transfer entropy（Schreiber 2000）。

## 失效模式（逐条对应到 Ascend）

定理 3.1 的四条假设各自对应一种失效模式，逐一映射到 Ascend 的现实：

| 失效模式 | 后果 | Ascend 的处境 |
|----------|------|---------------|
| **瞬时因果**（同周期内 $X \to Y$） | 该边对 Granger **不可见**（滞后 0 的因果贡献被同期相关吸收） | **现实风险**：天气分钟 tick 内温度→降水类型判定是同 tick 联动 |
| 未观测混杂 | 伪 Granger 边（$X \leftarrow H \to Y$，$H$ 未观测时 $X$ 似乎导致 $Y$） | 封闭世界全变量可观测，按设计排除 |
| 非线性依赖 | 线性 F 检验漏检 | 引擎方程含非线性（tanh、分段），需 kernel/非参数版 |
| 条件 vs 成对 | **条件** Granger 检直接父母（某滞后）；**成对** Granger 检传递祖先、可能含伪边 | 已知 DAG 下无妨，Granger 只做验证不做发现；E1 按条件 Granger 读"直接滞后边" |

## 推论 3.3（对阶段 1 的时间粒度要求）

定理 3.1 的假设 (ii) 翻译成工程要求：

**采样/事件粒度必须细于引擎内最快的因果传播步**。若温度在 tick $t$ 内被用于判定降水类型并发布同 tick 事件，则在 tick 粒度上这条边对 Granger 不可见。三条应对路径（阶段 1 择一或组合）：

1. **同 tick 内因果序**：事件带 tick 内序号（发布顺序即因果序），分析时用子序展开；
2. **声明层粒度**：因果声明挂在变量方程层（方程间的边天然有"计算先于结果"的序），事件层只做实例标记，这与"双层结构"的设计一致；
3. **检验粒度下采样**：Granger 检验只在比最快因果步更粗的时间尺度上做，并显式声明该尺度下不可见的边。

**推论 3.4（Granger 在 Ascend 的角色定位）**：DAG 已知，Granger 不做图发现，只做**模型验证**："模型学到的动力学是否与引擎数据一致"。阶段 2 的检验设计：对模型预测残差与引擎实际序列做 Granger 检验，断言"残差对引擎真值无 Granger 信息"（模型已榨干因果信号）；反之亦然。

## 文献（本篇）

- C. W. J. Granger. "Investigating Causal Relations by Econometric Models and Cross-spectral Methods." *Econometrica* 37(3): 424–438, 1969.
- C. A. Sims. "Money, Income, and Causality." *American Economic Review* 62(4): 540–552, 1972.
- H. Lütkepohl. *New Introduction to Multiple Time Series Analysis*. Springer, 2005.
- M. Eichler. "Granger causality and path diagrams for multivariate time series." *Journal of Econometrics* 137(2): 334–353, 2007.
- D. Marinazzo, M. Pellicoro, S. Stramaglia. "Kernel Method for Nonlinear Granger Causality." *Physical Review Letters* 100: 144103, 2008.
- T. Schreiber. "Measuring Information Transfer." *Physical Review Letters* 85: 461–464, 2000.
