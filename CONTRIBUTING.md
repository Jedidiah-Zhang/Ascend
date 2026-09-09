# 贡献指南

[English](CONTRIBUTING.en.md) | 中文

欢迎参与 **Ascend** —— 自下向上构造因果世界的 AI 原生模拟平台。

本文档面向首次接触本项目的贡献者，帮助你了解项目定位、设计原则，以及开发、测试、提交与发布的约定。

## 项目定位

Ascend 兼具双重身份：

- **研究平台**：可复现、可干预、可追溯的因果世界，产出时空序列用于世界模型训练
- **游戏**：玩家作为世界个体，通过基因改造影响族群演化；NPC 由 AI 动态驱动

## 设计原则（必须遵守）

请在动手前确认，你的改动不违反以下原则：

### 深模块低耦合

模块对外只暴露**小而深的接口**，把内部复杂度藏在里面，不泄露实现细节。调用方只需理解接口契约，无需了解内部机制。

### 无补丁式代码

不写绕过问题的 hack。遇到 bug 时，请先定位**根因**、系统性解决问题。补丁式代码会掩盖问题、累积技术债。

### 前后端分离

后端只负责逻辑，前端只负责渲染、UI、输入、音频。两端之间只通过协议（当前 JSON over TCP）通信，互不直接调用。

### 数据与算法拆分

数据是数据、算法是算法，各自独立演进、互不耦合。改动数据定义不应波及相关算法，反之亦然。

### 不承诺向后兼容

现阶段无历史包袱，重构优先，允许直接修改接口。不要为了兼容旧接口而保留冗余设计。

## 开发流程

开发新模块时，请遵循以下统一顺序：

1. **明确需求**：先想清楚要解决什么问题
2. **定义接口**：确定模块对外契约
3. **写测试**：先写测试定义预期行为
4. **再写代码**：实现直至测试通过

## 世界内容数据（JSON）

地形、群系、气候、天气、世界生成参数等"可调内容"以 JSON 存在 `data/`
（`terrain.json`/`climate.json`/`biome.json`/`weather.json`/`world.json`），
**改内容只改数据文件、不用改代码**。约定：

- 键为命名空间 id（如 `ascend:grassland`）；`value` 显式声明、唯一且连续
  0..n-1，**已发布值不可改、只追加**（改序号会破坏存档/缓存）
- 显示名只存 `label_key`（如 `terrain.grassland`），文案在 `lang/*.json`
  （中英两文件同步，有对账测试）
- **豁免（仅适用于键值型注册表）**：`weather.json` 的 `features` 为
  "效果条目"注册表——运行时以命名空间 id 的 local 部分（`type_name`，
  如 `cold_snap`）为标识，不参与 `value` 连续编码，也不含 `label_key`
  （无界面显示名；文案走事件键，如 `weather.intensity`）
- 加内容 = 数据文件加一项（`value` 追加 + `label_key`）；import 期校验 +
  测试兜底契约；发行自动携带 `data/` 与 `lang/`

## 测试

### 后端（Python / pytest）

每次代码变更后，请运行受影响的单元测试（日常开发不必跑全量）：

```bash
cd backend && PYTHONPATH=. ../.venv/bin/python -m pytest --testmon -n 4 -q
```

- `-n 4` 为保守的并行数：若用 `-n auto`，会按本机核数开满 worker，可能导致世界生成时内存/CPU 双爆、进程卡死。该命令仅用于单元测试
- 集成测试必须串行（端口/子进程冲突）：`cd backend && ../.venv/bin/python -m pytest tests/integration/ -v`
- 全量测试无需本地手动执行，发布前由 CI 的 `test` job 自动运行

### 前端（GDScript / GUT）

```bash
cd frontend && ./run_tests.sh unit
```

GUT 不随仓库分发（`frontend/addons/gut` 只需在本地安装），因此前端测试仅在本地运行。

### 研究声明管线

研究方程的唯一事实源是生产机制注册表（声明片段在 `backend/ascend/weather|space/mechanisms.py`，由 `backend/ascend/causal/world.py` 组装，
详见 docs/研究理论/世界基座/07-机制注册表.md）。修改任何已登记方程、
参数、节点声明或 `data/world.json` 后，必须重新生成并提交两个产物，
否则 CI 漂移门禁会失败：

```bash
.venv/bin/python research/equations/export_registry.py          # 注册表 → equations.json
.venv/bin/python research/equations/gen_lean.py                 # equations.json → Lean 数据段
.venv/bin/python research/equations/verify_equations.py --fast  # 全链对拍（V0–V3）
.venv/bin/python research/equations/graph_check.py              # 图健康巡检（G0–G6）
```

`equations.json` 与 `GenDeclarationData.lean` 均为生成物，禁止手改。

**干预执行器**（P2，docs/研究理论/世界基座/08-干预执行器.md）：研究者干预经
`InterventionTable` 登记后在求值点替换生成；新增可干预分量时必须同步
`causal/world.py::WIRED_NODES` 与实际求值点（`WeatherEngine.evaluate_node`），
否则 `tests/unit/test_intervention_wiring.py` 的漂移巡检会失败。

**完整存档**（P4，docs/研究理论/世界基座/09-完整存档.md）：新增"无法由世界
设置重算"的运行时状态（研究者施加的量、随机制演化的标记）时，必须同步
`state.json.enc` 载荷与恢复路径（`save/serializer.py` + 该子系统的
`persist_*` / `restore_*`），并补 W4 双跑一致断言；可重算的解析量**不得**
落盘。改动状态载荷格式时递增 `STATE_VERSION`（旧档即不可读，不写迁移）。

## 提交约定

请遵循 [Conventional Commits](https://www.conventionalcommits.org/) 规范，并使用中文撰写提交描述。

## 贡献许可

- 提交 Pull Request 前，请阅读并同意 [CLA.md](CLA.md)（贡献者许可协议）——授权维护者对包含你贡献的软件进行商业化许可与分发
- 提交 PR 时请在模板中勾选"贡献者确认"，即视为同意 CLA
- CI 会通过 [CLA Check](.github/workflows/cla.yml) 自动核验，未同意的 PR 标记为不合并
- 若贡献含第三方材料（代码、库、资源等），务必在 PR 中注明来源与适用许可证
- 项目采用 [CC BY-NC-SA 4.0](LICENSE) 协议，你的贡献亦在该协议下公开发布

## 发布

- 版本号单一来源：`build/nuitka/version.txt`（Release 命名、产物文件名、Windows exe 属性、主菜单显示均由它派生）
- 推送标签会触发 CI 自动发版——目前仅发行后端（研究平台）；此操作通常由维护者执行：

```bash
git push origin main
git tag v<版本> && git push origin v<版本>
```

- 本地打包：`bash build/build_release.sh all`（详见 `build/README.md`）
- 前端发行（含闭源资产）走私有流程，不在本仓库 CI
