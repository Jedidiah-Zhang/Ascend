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
        ├── DagPathExpansion.lean    # 定理 2.5 代数内核：递推 ⟹ 路径和展开 + 汇聚反例
        └── LipschitzLayer.lean      # 连接定理：|Xh − X| ≤ e_t，组合出定理 2.5 完整式
```

依赖方向：`LipschitzLayer → DagPathExpansion`；`ChainError` 独立。
新增形式化时：一篇文档建一个子目录（或单文件），文件名跟内容语义走，
并在下方映射表登记。

## 构建

依赖 Mathlib（版本由 `lake-manifest.json` 锁定），首次构建先拉缓存：

```bash
cd research/lean
lake exe cache get   # 拉取 Mathlib 预编译缓存（首次必需）
lake build           # 构建并检查全部证明
```

CI：`.github/workflows/lean_action_ci.yml` 在 push / PR 触及 `research/lean/**`
时自动运行 `lake build`。

## 文档映射

| 文档（docs/研究理论/因果理论验证/）                                | 形式化文件            | 内容                                                                                  |
| ------------------------------------------------------------------ | --------------------- | ------------------------------------------------------------------------------------- |
| 02-误差传播与反事实.md                                             | ChainError.lean       | 引理 2.1 的链式特例闭式                                                               |
| 02-误差传播与反事实.md                                             | DagPathExpansion.lean | 定理 2.5 代数内核（误差递推 ⟹ 路径和展开）、汇聚"取最大"反例                         |
| 02-误差传播与反事实.md                                             | LipschitzLayer.lean   | 引理 2.1 完整版 + 定理 2.5 完整式\|Xh_t − X_t\| ≤ ε_t + Σ_u ε_u·W u t、干预情形 |
| 00-假设与记号 / 01-样本复杂度 / 03-Granger等价性 / 04-干预采样覆盖 | （待形式化）          |                                                                                       |

配套验证管线见 `research/equations/`（声明层数值对照，Python 差分测试）；
Lean 只证明数学性质，不检查引擎代码是否按声明实现（那是对拍测试的职责）。
