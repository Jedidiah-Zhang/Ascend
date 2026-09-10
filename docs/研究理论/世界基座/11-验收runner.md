# 世界基座 11：验收 runner

> 判据来源：[世界验收协议](04-世界验收协议.md)（C0–C2 / W0–W5 / I0–I1）
> 与[第一阶段实施定义](../第一阶段实施定义.md) §9–§10。
> 工程入口：`research/acceptance/`。

## 1. 定位

本篇对应 issue #46 的 P5 交付：**统一验收 runner**。P1–P4 各自交付了
能力（注册表 / 干预 / 存档 / trace），但"第一阶段是否闭合"需要一套
**独立、可重复、产物可审计**的判据来回答。

```
research/acceptance/
  run_acceptance.py     入口：逐项执行 + 产物落盘（--json）+ 退出码
  checks.py             11 项判据（C0–C2 / W0–W5 / I0–I1）
  slices.py             验收切片声明（每项判据一个最小声明）
  reference.py          独立参考解释器（不复用引擎求值路径）
  gen_unrolled_dag.py   生产声明 → Lean UnrolledDag 实例（防漂移）
```

## 2. 为什么参考实现要独立（04 §4.1）

> 直接调用同一个生产函数再与生产函数比较，只能检查数据搬运，不能发现
> 方程实现错误。

因此 `reference.py` 只读 `MechanismRegistry` 的**声明数据**（父集、参数、
随机源、更新阶段），自己按阶段序求值；`SpatialReferenceInterpreter` 按格
展开空间父模板（含边界算子）。引擎侧只提供被比较的轨迹。

每项判据都记录**输入、参考输出、引擎输出与首个分歧**
（`first_divergence`：帧、节点、双方取值），满足 04 §1"只保存最终状态
哈希不足以定位分歧"。

## 3. 判据清单

| 判据 | 内容 | 关键断言 |
| --- | --- | --- |
| C0 | 声明完整性 | 生产注册表 C0 全字段校验通过（47 节点 / 35 机制） |
| C1 | 结构最小性 | 64 个功能依赖见证实测重跑通过 |
| C2 | 时间展开无环 | 3 帧窗口展开 192 条实例边，秩严格上升 |
| C2' | C2 判别力自检 | 违规声明（同帧父位于更晚阶段）必须被拒绝 |
| W0 | 帧内顺序 | 三阶段构造：参考 / 引擎 / 手算逐值一致（3, 6, 6） |
| W1 | 节点干预与 CRN | 干预值、入边切断、后代传播、非后代不变、参考一致 |
| W2 | 三类动态干预 | 值(1帧)=[10,11,12] / 值(2帧)=[10,10,11] / 机制=基线，互异且手算一致 |
| W3 | 空间父模板与边界 | **引擎侧**（`spatial_cells`）× 参考解释器 × 手算三层一致；单位扰动响应 (0, ¼, ½, ¼, 0)；replicate 边界正确 |
| W4 | 状态充分性 | 存档读档轨迹 vs 不存档轨迹逐位一致（12 采样点）；**判别力负例**：剔除干预/注入核后必须分叉 |
| W5 | 观测隔离 | 同一状态 + 两份主体观测：可区分、为研究视图真子集、主体载荷只含标量（泄露检测有判别力） |
| I0 | 观测等价、干预可分 | 两候选世界观测分布相同，`do(A=0)` 下 B 的分布不同 |
| I1 | 分布预测与配对效应 | CRN 配对差恒为 1（4 个 ω） |

**关于 W2 的机制臂**：生产注册表 C0 强制"节点单写者"，当前 `do mech`
只能登记恒等替换（F'=F，Lean `mechDo_same_eq_traj`）。判据如实反映该
现状——机制臂 = 恒等替换 → 与基线一致，仍与值/持续两臂互异。真正的
公式替换由研究切片验证（08 §8）。

**关于 W5 的观测映射**：当前没有智能体观测主体，`AccessPolicy.
observation_protocols` 是**权限**（哪些协议允许读该分量），生产里两个
协议都被授予了全部节点——"按协议自动收窄可见集"尚未实现。判据用
`causal/observe.py` 的观测映射（按协议量化，`agent.weather.v1` → 1 位小数）
验证隔离**性质**：两份观测可区分、主体视图是研究视图的**显式**真子集、
主体载荷只含"节点 → 标量"（泄露检测对父值映射/随机地址列表/干预记录
这类非标量结构有判别力，有测试锁定）。

**关于 oracle 的独立性边界**：`reference.py` 独立实现**调度层**（微步全序、
`lag` → 前序/上帧、干预覆盖、空间偏移与边界算子），但每次方程求值仍走
`registry.evaluate_mechanism`——即它验的是"调度与干预"，**不覆盖方程实现**。
要覆盖后者需独立重写全部方程（另一量级工作）；当前方程实现由 C1 见证
（每条父边的功能依赖实测重跑）与声明快照漂移门禁承担。

## 4. 运行与门禁

```bash
.venv/bin/python research/acceptance/run_acceptance.py          # 全部判据
.venv/bin/python research/acceptance/run_acceptance.py --json out.json
.venv/bin/python research/acceptance/run_acceptance.py --check   # Lean 实例漂移巡检
```

退出码 0 = 全部通过。CI（`.github/workflows/lean_action_ci.yml`）的
`acceptance` job 依次跑 **`--check`（Lean 实例漂移）** 与**全部判据**，
任一失败即红，并上传 `acceptance.json` 产物。watch 路径已扩展到
`research/acceptance/**`、`backend/ascend/{causal,save}/**`、
`backend/ascend/game.py`、`backend/tests/**` 等。

## 5. Lean 实例化（C2 的理论内核 × 生产声明）

`UnrolledDag.lean` 证明"任何合法声明展开无环"，但此前**从未与真实声明
对上**。`gen_unrolled_dag.py` 把生产声明实例化为 `UnrolledDag.Decl`，
并给出**机器可判的合法性证明**：

- `wellFormed_real : WellFormed realDecl`——生产声明合法；
- `unroll_acyclic_real : Acyclic (UnrollEdge realDecl)`——由通用定理
  `unroll_acyclic` 直接得到生产声明的时间展开图无环；
- `run_acceptance.py --check` 巡检生成物与生产声明是否漂移（接入 CI）。

**有限索引（关键设计）**：`Decl`、`Node` 与 `ParentSpec` 的参数一律以
有界索引声明——`Fin n`（分量）、`Fin m`（更新阶段）、`Fin K`（滞后上界）：

```
ParentSpec (n m K : ℕ)   par : Fin n, lag : Fin K, pr : Fin m
Decl (n m K : ℕ)         := Fin n → Fin m → List (ParentSpec n m K)
WellFormed (D : Decl n m K) := 每个父模板满足"同帧父来自更早阶段"
```

理由：合法性本身包含"参数在界内"，把这条**编码进类型**之后，命题在
有限域上**可判定**，实例化时由判定式 `wellFormedCheck`（在 `Fin` 与
列表视图上显式递归求与，保持可计算）经 `native_decide` 一次判定，
不需要在无限域（`ℕ`）上做全称推理——后者因 `ℕ` 无 `Fintype` 而
无法合成 `Decidable`，是此前卡住的根因。

值用 `List` 而非 `Finset`：声明是生成物、重复项由生成器保证不出现，
而 `Finset.toList` 依赖选择公理（`noncomputable`），会破坏判定式的可计算性。

## 6. 验证

- `tests/unit/test_acceptance_runner.py`：切片自检（C0/C1）、参考解释器
  手算对拍（W0/W2/W3）、首分歧定位、11 项判据全部可执行且报告 JSON 往返、
  关键判据的判别力（W2 三臂互异、W3 响应落在核支持内、W5 真子集）。
- `tests/unit/test_spatial_template.py`：多偏移父模板的元组值契约、
  单偏移值域校验、`tuple` 值类型。
- 全链：pytest 全量（单元 + 集成）、`run_acceptance.py`、`export_registry
  --check`、`gen_lean --check`、`run_acceptance.py --check`、Lean `lake build`。
