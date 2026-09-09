# 世界基座 10：研究 trace

> 记号按[工程符号体系](../工程符号体系.md) §4/§6 与
> [第一阶段实施定义](../第一阶段实施定义.md) §8。工程入口是
> `backend/ascend/causal/trace.py`。

## 1. 定位

本篇对应 issue #46 的 P3 交付：**分层研究日志**。研究日志用于验证声明与
实现，**不等同于智能体感知数据，也不是玩法事件**（实施定义 §8）。

它回答的问题是：引擎算出一个值之后，能不能逐项说清它是怎么来的？

| 记录项（§8 清单） | 工程字段 |
| --- | --- |
| 分量实例 $(v,j,t)$ 与更新阶段 $r_v$ | `node_id` / `instance` / `frame` / `microstep` |
| 方程及声明版本 | `mechanism_id` / `equation_version` / `resolved_version` |
| 父引用标识和实际父值 | `parents` |
| 消费的随机地址（必要时含实际值） | `random_addresses` / `random_values` |
| 生效的干预及优先级 | `intervention` / `rep` |
| 输出值和边界处理结果 | `output` / `boundary` |
| （参数槽位实际取值） | `parameters` |

**两条纪律**：

1. **与玩法事件分库**：trace 只存在于 `causal/` 与研究通道（net 研究 API /
   终端 `trace` 指令组）。它不订阅世界树、不发布事件，**绝不进入
   `weather/events.py` 的事件载荷**——事件是"给智能体/前端看的"，trace 是
   "给研究者看的"，两者不能互相污染。门禁：
   `test_trace_wiring.py::TestTraceIsSeparateFromGameplayEvents`。
2. **fail-closed**：记录要么完整要么拒绝。开启 trace 后，若某次求值缺更新
   阶段、缺方程版本、父值与声明不符、声明的随机源缺值，**直接拒绝求值**
   （抛错），而不是留一条残缺记录。

## 2. 数据契约

```
RandomAddress   source / frame / instance / draw_index
TraceRecord     node_id / frame / instance / microstep /
                mechanism_id / equation_version / resolved_version /
                parents / parameters / random_addresses / random_values /
                intervention / rep / output / boundary
TraceLog        record() / records(frame,node_id) / clear() / replay() / verify()
```

- **方程版本零开销**：`MechanismRegistry` 在构造期预计算每个节点的
  `equation_version`（方程源码 + 显式依赖摘要）与 `resolved_version`
  （方程 + 参数 + 边界组合摘要），trace 与声明快照共用同一份。求值点
  不再重算源码摘要。
- **值覆盖如实记录**：值干预命中时生成结果被替换、原入边被切断，记录
  `rep="value"`、`mechanism_id=""`、`equation_version=""`，并保留被替换的
  干预记录（`intervention`，含 `seq`/`applied_at`）。
- **参数槽位**：记录的是**实际生效值**（声明默认值或参数干预覆盖后的值），
  这是"重算"必需的输入。
- **随机地址**：按机制声明的随机源生成（源 ID + 帧 + 实例 + 抽取序号），
  并记录实际抽取值（重算自包含）。生产声明当前不含外生随机源，因此地址集
  为空——接口就绪，登记源后自动生效（见 §5）。
- **有界内存**：`TraceLog` 是环形缓冲（缺省 4096 条），不落盘（P4 存档只
  存 $\mathcal W_t$，不含日志）。

## 3. 日志可重算任意节点

实施定义 §8 的要求是"日志或可重建追踪必须包含……"；P3 把它落成一条
**可执行断言**：

```
replay(record) = 用记录里的父值/参数/随机值执行记录里的声明方程
verify(record) = replay(record) == record.output     # 精确相等，无容差
```

对每条记录都跑（`verify_all()`）。任何一条对不上，就精确定位到该节点与
其父边——这正是"声明与实现分歧"的定位手段。值覆盖记录直接返回替换值
（无方程可重算），同样可校验。

开启 trace 不改变任何求值结果（纯观察层），有测试锁定。

## 4. 接口

**终端 `trace`**（`backend/ascend/terminal/trace_commands.py`）

| 指令 | 语义 |
| --- | --- |
| `trace on [capacity]` | 开启研究日志（缺省容量 4096） |
| `trace off` | 关闭（已记录内容保留） |
| `trace status` | 开关与记录数 |
| `trace list [frame N] [node ID]` | 按帧/节点筛选记录 |
| `trace show <node> [frame N]` | 打印单条记录全部字段 |
| `trace verify` | 全部记录重算校验，列出不一致项 |
| `trace clear` | 清空记录 |

**研究 API**（`backend/ascend/net/handlers/research_handler.py`）

| 请求 | 载荷 | 成功响应 |
| --- | --- | --- |
| `research_trace_list` | `{frame?, node_id?}` | `{success, records}` |
| `research_trace_replay` | `{node_id, frame?}` | `{success, record, replayed, consistent}` |
| `research_trace_clear` | — | `{success, cleared}` |

两个入口同源：同一 `TraceLog` 实例（挂在 `WeatherEngine.trace`）、同一重算
实现、同一 fail-closed 语义。字段校验全部 fail-closed（非法类型返回
`{success: false, error}` 而不是抛异常）。

## 5. 边界与遗留

- **W5 观测隔离不在本篇**：当前没有智能体观测主体（实体管理器"视野=全部"，
  事件桥接面向渲染），观测映射层 $G^i$ 与 W5 验收并入 P5 统一 runner。
  trace 与观测的边界在此固定：**智能体数据只能由声明的 $G^i$ 生成，研究
  日志不得成为智能体后门**。
- **随机地址当前为空**：生产声明不含外生随机源（天气切片的随机性在统一
  天气场内部，被声明为 `slice_boundary` 边界输入）。更值得注意的是：天气场
  的五个通道由**同一组特征核**驱动，即存在一个**未声明的共同原因**
  （实施定义 §6.1 的标准处置是"让同一个显式外生源被多个方程消费"）。
  把天气场噪声登记为共享外生源是下一篇工作（会改声明 hash 并重生成
  两份生成物），trace 的地址接口为此就绪。
- **不落盘**：日志是研究期内存缓冲；跨会话复现由 P4 的完整状态 + 随机
  地址重算承担。
- **未覆盖的求值点**：`registry.evaluate` 的直接调用（不经
  `InterventionEvaluator`）不带 trace；生产求值点全部经评估器，已接线。
  研究切片 `InterventionFrameExecutor` 的记录留待 P5 runner 按需接线。

## 6. 验证

- `tests/unit/test_trace.py`：记录字段与 JSON 视图、fail-closed 负例
  （未声明节点/缺阶段/缺版本/父集不符/值覆盖缺输出）、容量淘汰、
  `replay`/`verify` 一致与不一致、值覆盖重算、注册表变化后重算报错、
  评估器接线（正常/值干预/不改变输出）。
- `tests/unit/test_trace_wiring.py`：引擎挂载（默认关闭、幂等、关闭后停止、
  不改变天气读数、值干预留痕）、**事件载荷无 trace 字段**门禁、终端指令组
  （中英）、研究 API（负例与成功路径）。
- 全链：pytest 全量（单元 + 集成）、`export_registry --check`、
  `gen_lean --check`、`verify_equations`、`graph_check`、Lean `lake build`。
