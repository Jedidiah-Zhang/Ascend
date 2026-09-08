# ascend-lean

Ascend 研究理论的 Lean/Mathlib 形式化（issue #44）。
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
    └── CausalVerification/          # ← 对应 docs/研究理论/世界基座/ 与第一阶段实施定义
        ├── Contraction.lean         # 推论 2.2 三档行为 + 推论 2.3 收缩链两律（外推饱和/初值遗忘）
        ├── DagPathExpansion.lean    # 命题 2.5 代数内核：递推 ⟹ 路径和展开 + 汇聚反例
        ├── LipschitzLayer.lean      # 连接命题：|Xh − X| ≤ e_t，组合出命题 2.5 完整式（含命题 2.1 链特例）
        ├── SubSCM.lean              # 显式 do 结构：换常数方程（断入边）+ 干预版闭式 + 编码版互证
        ├── ExplicitPaths.lean       # 显式路径枚举：pathEnumSum = pathWeight 主定理（命题 2.5）
        ├── UnrolledDag.lean         # 时间展开无环：微步偏序 + 滞后父模板 ⟹ 有限窗口展开图无环（C2）
        ├── SpatialKernel.lean       # 空间核逐点 Lipschitz 界 Σ|w_σ| 与严格收缩条件（02 §5）
        ├── InterventionTypes.lean   # 节点/持续/机制干预轨迹语义：persist(1)=nodeDo、三类互异见证（W2）
        ├── Declarations.lean        # 声明层函数性质：clamp 有界/单调/Lipschitz + 数值锚点核对
        └── GenDeclarationData.lean  # 自动生成（gen_lean.py）：声明数据段 + 对账定理，禁止手改
```

依赖方向：`GenDeclarationData → Declarations → LipschitzLayer → DagPathExpansion`；
`SubSCM → LipschitzLayer`；`ExplicitPaths → DagPathExpansion/LipschitzLayer`；
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
`research/lean/**`、`research/equations/**`、`backend/ascend/causal/**`、
`backend/ascend/weather/derive.py`、`backend/ascend/weather/mechanisms.py`、
`backend/ascend/space/climate.py`、`backend/ascend/config.py` 或
`data/world.json` 时运行两级声明漂移巡检（`export_registry.py --check`
+ `gen_lean.py --check`）与 `lake build`。

## 文档映射

| 来源                                                                | 形式化文件              | 内容                                                                                  |
| ------------------------------------------------------------------- | ----------------------- | ------------------------------------------------------------------------------------- |
| 02-误差传播与反事实.md                                              | Contraction.lean        | 推论 2.2 三档行为（0≤Λ<1 收缩 / Λ=1 线性 / Λ>1 发散）、推论 2.3 外推饱和与初值遗忘    |
| 02-误差传播与反事实.md                                              | DagPathExpansion.lean   | 命题 2.5 代数内核（误差递推 ⟹ 路径和展开）、汇聚"取最大"反例                          |
| 02-误差传播与反事实.md                                              | LipschitzLayer.lean     | 命题 2.1 链特例 + 命题 2.5 完整式\|Xh_t − X_t\| ≤ ε_t + Σ_u ε_u·W u t、干预情形       |
| 02 篇命题 2.5 干预情形 + 世界基座 04 验收协议                       | SubSCM.lean             | 显式 do 结构：subSCM 换常数方程（断入边）、ε'-形干预闭式、与编码版四重互证            |
| 02 篇 §5                                                           | SpatialKernel.lean      | 空间核逐点 Lipschitz 界（绝对权重和放大）与 Σ\|w_σ\|<1 的严格收缩                     |
| 00 篇 §2 + 04 篇 C2 + 第一阶段实施定义 §5                           | UnrolledDag.lean        | 时间展开无环：微步偏序 + 滞后父模板 ⟹ 任意有限窗口展开图无环（秩测度 + 良基）         |
| 第一阶段实施定义 §7/§9 + 04 篇 W2                                   | InterventionTypes.lean  | 节点/持续/机制干预轨迹语义：persist(1)=nodeDo、干预不改过去、三类互异数值见证          |
| 生产机制注册表（backend/ascend/weather/mechanisms.py → equations.json）+ backend/ascend/config.py、weather/derive.py、space/climate.py | Declarations.lean       | 声明层函数性质：clamp 引理库，derive_latitude / derive_seasonal_amp / precip_type_for 的界·单调·Lipschitz·常数最优性 + config 数值核对 |
| 生产机制注册表 → equations.json + backend/ascend/config.py        | GenDeclarationData.lean | gen_lean.py 自动生成的声明数据段 + 七条对账定理（防漂移，--check 巡检）               |
| 01-样本复杂度 / 03-时空因果可见性 / 反事实与认知 01                 | （文献结果，不形式化）  |                                                                                       |

配套验证管线见 `research/equations/`（声明层数值对照，Python 差分测试）；
Lean 只证明数学性质，不检查引擎代码是否按声明实现（那是对拍测试的职责）。

`equations.json` 为**生成物**（生产注册表 → `export_registry.py`，禁止手改），
来源、切片与迁移流程见 `docs/研究理论/世界基座/07-机制注册表.md`。

新方程接入流程演练（issue #45，零污染 dry-run）：复制 `equations.json`
加演练边 → `gen_lean.py --json <副本> --out <临时 .lean>` 生成到仓库外
→ `lake env lean <临时 .lean>` 验证编译绿（新边自动 camel 命名进数据段，
对账定理模板需人工评估是否扩展）。正式接入走注册表登记 + 重新生成
（见 07 篇 §6），不直接手改 JSON。
