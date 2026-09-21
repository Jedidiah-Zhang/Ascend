# ascend-lean

Ascend 研究理论的 Lean/Mathlib 形式化。
每个文件的模块级 docstring 均标注 `docs/研究理论/` 对应文档与命题出处，形成可追溯的协议证书。文档重排不会依赖易漂移的行号。

![Lean](https://img.shields.io/badge/Lean-v4.34-blue) ![Mathlib](https://img.shields.io/badge/depends-Mathlib-2596be)

## 文件分布

```text
research/lean/
├── AscendLean.lean                  # 库根模块，聚合全部 import（lake build 的默认入口）
├── lakefile.toml                    # 包定义（纯定理库，无可执行目标）
├── lean-toolchain                   # Lean 版本锁定
├── lake-manifest.json               # 依赖锁定（Mathlib）
└── AscendLean/
    └── CausalVerification/          # ← 理论篇（世界基座 01–03、工程符号体系）与第一阶段实施定义的形式化；Gen*.lean 由生产声明生成
        ├── Contraction.lean         # 推论 2.2 三档行为 + 推论 2.3 收缩链两律（外推饱和/初值遗忘）
        ├── DagPathExpansion.lean    # 命题 2.5 代数内核：递推 ⟹ 路径和展开 + 汇聚反例
        ├── LipschitzLayer.lean      # 连接命题：|x̂ − x| ≤ e_t，组合出命题 2.5 完整式（含命题 2.1 链特例）
        ├── SubSCM.lean              # 显式 do 结构：换常数方程（断入边）+ 干预版闭式 + 编码版互证
        ├── ExplicitPaths.lean       # 显式路径枚举：pathEnumSum = pathWeight 主定理（命题 2.5）
        ├── UnrolledDag.lean         # 时间展开无环：阶段次序 + 滞后父模板 ⟹ 有限窗口展开图无环（C2）
        ├── SpatialKernel.lean       # 空间核逐点 Lipschitz 界 Σ|w_σ| 与严格收缩条件（02 §5）
        ├── InterventionTypes.lean   # 节点/持续/机制干预轨迹语义：persist(1)=nodeDo、三类互异见证（W2）
        ├── Declarations.lean        # 声明层函数性质：clamp 有界/单调/Lipschitz + 数值锚点核对
        ├── GenDeclarationData.lean  # 自动生成（gen_lean.py）：声明数据段 + 对账定理，禁止手改
        └── GenUnrolledDag.lean      # 自动生成（gen_unrolled_dag.py）：声明实例 + WellFormed/无环证明，禁止手改
```

依赖方向：`GenDeclarationData → Declarations → LipschitzLayer → DagPathExpansion`；
`GenUnrolledDag → UnrolledDag`；`SubSCM → LipschitzLayer`；
`ExplicitPaths → DagPathExpansion/LipschitzLayer`；
`Contraction`、`UnrolledDag`、`SpatialKernel`、`InterventionTypes` 相互独立（仅依赖 Mathlib）。
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
`research/lean/**`、`research/equations/**`、`research/acceptance/**`、
`backend/ascend/world/**`、`backend/ascend/weather/**`、
`backend/ascend/space/**`、`backend/ascend/save/**`、
`backend/ascend/game.py`、`backend/ascend/config.py`、`backend/tests/**`
或 `data/world.json` 时运行声明漂移巡检（`export_registry.py --check`、
`gen_lean.py --check`、`export_impl_digests.py --check`）与 `lake build`；
同一 workflow 的验收 job 另跑 `research/acceptance/` 的门禁
（UnrolledDag 实例巡检、世界验收、条款对账与变异探针）。

## 文档映射

| 来源                                                                | 形式化文件              | 内容                                                                                  |
| ------------------------------------------------------------------- | ----------------------- | ------------------------------------------------------------------------------------- |
| 02-误差传播与反事实.md                                              | Contraction.lean        | 推论 2.2 三档行为（0≤Λ<1 收缩 / Λ=1 线性 / Λ>1 发散）、推论 2.3 外推饱和与初值遗忘    |
| 02-误差传播与反事实.md                                              | DagPathExpansion.lean   | 命题 2.5 代数内核（误差递推 ⟹ 路径和展开）、汇聚"取最大"反例                          |
| 02-误差传播与反事实.md                                              | LipschitzLayer.lean     | 命题 2.1 链特例 + 命题 2.5 完整式：\|x̂ − x\| ≤ e_t（节点误差按路径和放大）、干预情形     |
| 02-误差传播与反事实.md 命题 2.5 干预情形（W1/W2 判据）            | SubSCM.lean             | 显式 do 结构：subSCM 换常数方程（断入边）、ε'-形干预闭式、与编码版四重互证            |
| 02-误差传播与反事实.md §5                                          | SpatialKernel.lean      | 空间核逐点 Lipschitz 界（绝对权重和放大）与 Σ\|w_σ\|<1 的严格收缩                     |
| 工程符号体系 §3 + 第一阶段实施定义 §4/§5                           | UnrolledDag.lean        | 时间展开无环：阶段次序 + 滞后父模板 ⟹ 任意有限窗口展开图无环（秩测度 + 良基）         |
| 第一阶段实施定义 §7/§9                                             | InterventionTypes.lean  | 节点/持续/机制干预轨迹语义：persist(1)=nodeDo、干预不改过去、三类互异数值见证          |
| 世界声明（`research/equations/equations.json`）+ backend/ascend/config.py、weather/derive.py、space/climate.py | Declarations.lean       | 声明层函数性质：clamp 引理库，derive_latitude / derive_seasonal_amp / precip_type_for 的界·单调·Lipschitz·常数最优性 + config 数值核对 |
| 世界声明 → equations.json + backend/ascend/config.py              | GenDeclarationData.lean | gen_lean.py 自动生成的声明数据段 + 七条对账定理（防漂移，--check 巡检）               |
| 世界声明 → UnrolledDag.Decl（`research/acceptance/gen_unrolled_dag.py`） | GenUnrolledDag.lean     | 生产声明的父模板实例 + WellFormed 机器可判证明与时间展开无环实例见证（--check 巡检）  |
| 01-样本复杂度 / 03-时空因果可见性 / 反事实与认知 01                 | （文献结果，不形式化）  |                                                                                       |

配套验证管线见 `research/equations/`（声明层数值对照，Python 差分测试）；
Lean 只证明数学性质，不检查引擎代码是否按声明实现（那是对拍测试的职责）。

`equations.json` 为**生成物**（生产声明 → `export_world.py`，禁止手改），
来源与切片见 `backend/ascend/world/modules/` 的模块声明。

新方程接入流程演练（零污染 dry-run）：复制 `equations.json`
加演练边 → `gen_lean.py --json <副本> --out <临时 .lean>` 生成到仓库外
→ `lake env lean <临时 .lean>` 验证编译绿（新边自动 camel 命名进数据段，
对账定理模板需人工评估是否扩展）。正式接入：在模块声明包中登记机制，
重新生成 `equations.json` 与 `GenDeclarationData.lean`，不直接手改 JSON。
