# ascend-lean

Ascend 研究理论的 Lean/Mathlib 形式化（issue #44）。
每个文件的模块级 docstring 均标注 `docs/研究理论/` 对应文档与行号出处，形成可追溯的协议证书。

![Lean](https://img.shields.io/badge/Lean-v4.34-blue) ![Mathlib](https://img.shields.io/badge/depends-Mathlib-2596be)

## 文件分布

```text
research/lean/
├── AscendLean.lean                  # 库根模块，聚合全部 import（lake build 的默认入口）
├── lakefile.toml                    # 包定义（纯定理库，无可执行目标）
├── lean-toolchain                   # Lean 版本锁定
├── lake-manifest.json               # 依赖锁定（Mathlib）
└── AscendLean/
    └── CausalVerification/          # ← 对应 docs/研究理论/因果理论验证/
        ├── ChainError.lean          # 引理 2.1 链式误差传播闭式（试点）
        ├── Contraction.lean         # 推论 2.2 三档行为 + 推论 2.3 收缩链两律（外推饱和/初值遗忘）
        ├── DagPathExpansion.lean    # 定理 2.5 代数内核：递推 ⟹ 路径和展开 + 汇聚反例
        ├── LipschitzLayer.lean      # 连接定理：|Xh − X| ≤ e_t，组合出定理 2.5 完整式
        ├── SubSCM.lean              # 显式 do 结构：换常数方程（断入边）+ 干预版闭式 + 编码版互证
        ├── ExplicitPaths.lean       # 显式路径枚举：pathEnumSum = pathWeight 主定理（02 篇第 63 行）
        ├── S4Anchor.lean            # S4 探针数值锚点：链闭式 0.02×2.44 = 0.0488（对照 toy_scm.py）
        ├── Declarations.lean        # 声明层函数性质：clamp 有界/单调/Lipschitz + 数值锚点核对
        └── GenDeclarationData.lean  # 自动生成（gen_lean.py）：声明数据段 + 对账定理，禁止手改
```

依赖方向：`GenDeclarationData → Declarations → LipschitzLayer → DagPathExpansion`；
`SubSCM → LipschitzLayer`；`ExplicitPaths、S4Anchor → DagPathExpansion/LipschitzLayer`；
`ChainError`、`Contraction` 相互独立。
新增形式化时：一篇文档建一个子目录（或单文件），文件名跟内容语义走，
并在下方映射表登记。

## 构建

依赖 Mathlib（版本由 `lake-manifest.json` 锁定），首次构建先拉缓存：

```bash
cd research/lean
lake exe cache get   # 拉取 Mathlib 预编译缓存（首次必需）
lake build           # 构建并检查全部证明
```

CI：`.github/workflows/lean_action_ci.yml` 在 push / PR 触及
`research/lean/**`、`research/equations/**` 或 `backend/ascend/config.py`
时自动运行声明数据漂移巡检（`gen_lean.py --check`）与 `lake build`。

## 文档映射

| 来源                                                                | 形式化文件              | 内容                                                                                  |
| ------------------------------------------------------------------- | ----------------------- | ------------------------------------------------------------------------------------- |
| 02-误差传播与反事实.md                                              | ChainError.lean         | 引理 2.1 的链式特例闭式                                                               |
| 02-误差传播与反事实.md                                              | Contraction.lean        | 推论 2.2 三档行为（L<1 收缩 / L=1 线性 / L>1 发散）、推论 2.3 外推饱和与初值遗忘      |
| 02-误差传播与反事实.md                                              | DagPathExpansion.lean   | 定理 2.5 代数内核（误差递推 ⟹ 路径和展开）、汇聚"取最大"反例                          |
| 02-误差传播与反事实.md                                              | LipschitzLayer.lean     | 引理 2.1 完整版 + 定理 2.5 完整式\|Xh_t − X_t\| ≤ ε_t + Σ_u ε_u·W u t、干预情形       |
| 02 篇定理 2.5 干预情形 + 04 篇引擎接口                              | SubSCM.lean             | 显式 do 结构：subSCM 换常数方程（断入边）、ε'-形干预闭式、与编码版四重互证            |
| 05 篇 S4 判据 + research/toy_scm.py::_s4                             | S4Anchor.lean           | do 干预求和界的数值锚点：ε·(1+L+L²)，探针参数下 0.0488（证书↔实测对照）               |
| research/equations/equations.json + backend/ascend/config.py、weather/derive.py、space/climate.py | Declarations.lean       | 声明层函数性质：clamp 引理库，derive_latitude / derive_seasonal_amp / precip_type_for 的界·单调·Lipschitz·常数最优性 + config 数值核对 |
| research/equations/equations.json + backend/ascend/config.py        | GenDeclarationData.lean | gen_lean.py 自动生成的声明数据段 + 七条对账定理（防漂移，--check 巡检）               |
| 00-假设与记号 / 01-样本复杂度 / 03-Granger等价性 / 04-干预采样覆盖 | （待形式化）            |                                                                                       |

配套验证管线见 `research/equations/`（声明层数值对照，Python 差分测试）；
Lean 只证明数学性质，不检查引擎代码是否按声明实现（那是对拍测试的职责）。

新方程接入流程演练（issue #45，零污染 dry-run）：复制 `equations.json`
加演练边 → `gen_lean.py --json <副本> --out <临时 .lean>` 生成到仓库外
→ `lake env lean <临时 .lean>` 验证编译绿（新边自动 camel 命名进数据段，
对账定理模板需人工评估是否扩展）。
