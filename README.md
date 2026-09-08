# Ascend

> 构造具有完整因果真值的可交互世界，并以真实生成机制评价智能体的认知与行为。

[English](README.en.md) | 中文

<p align="center">
  <img src="https://img.shields.io/badge/Python-3.14-3776AB?logo=python&logoColor=white" alt="Python">
  <img src="https://img.shields.io/badge/C-99-A8B9CC?logo=c&logoColor=white" alt="C">
  <img src="https://img.shields.io/badge/Godot-4.x-478CBF?logo=godotengine&logoColor=white" alt="Godot">
  <img src="https://img.shields.io/badge/SQLite-3-003B57?logo=sqlite&logoColor=white" alt="SQLite">
  <img src="https://img.shields.io/badge/JSON-over%20TCP-000000?logo=json&logoColor=white" alt="JSON over TCP">
  <img src="https://img.shields.io/badge/Lean-4.34%20%2B%20Mathlib-000000" alt="Lean + Mathlib">
  <img src="https://img.shields.io/badge/License-CC%20BY--NC--SA%204.0-EF9421" alt="License">
</p>

## 简介

**Ascend** 是一个面向研究与游戏的 AI 原生世界模拟平台。世界不是 NPC 的静态背景，而是一个持续演化的系统：其完整状态、帧内更新顺序、结构方程、外生随机源与合法干预全部由研究者明确声明，构成因果真值；智能体只能在受限感知与行动中逐步认识它。

核心思想：

> 构造一个具有完整因果真值的可交互世界，使初始状态、外生随机性、干预和观测过程都能够被明确声明，并以真实生成机制产生的结果评价智能体的世界模型。

## 核心亮点

- **可执行因果真值** — 世界的完整状态、结构方程、随机源与合法干预全部显式声明，引擎轨迹逐节点可追溯，实现与声明对拍验收。
- **单位级反事实** — 同一随机实验单位可生成严格配对的干预/基线平行轨迹（CRN），把干预效应与两次独立随机波动区分开来。
- **可证伪的智能体评价** — 以操作性因果能力为判据：预注册查询族、保留测试干预与严格评分规则，不做无法被实验反驳的声明。
- **研究与游戏同源** — 玩家与智能体生活在同一个被声明的世界中，研究结论与玩法设计共享同一套世界机制。

研究问题分为三个递进阶段：

- **第一阶段 · 可执行的因果真值（施工中）** — 把世界声明变成唯一可执行、可追溯、可干预的动态结构因果系统，并验证引擎实现与声明一致。
- **第二阶段 · 识别边界与单智能体能力（未开始）** — 具身智能体能否在预注册的历史分布、干预范围与预测窗口内，对未见干预的后果作出正确的概率预测。
- **第三阶段 · 多智能体因果与宏观结构（未开始）** — 个体能否区分物理后果、他者响应与联合策略的影响，局部交互能否产生稳定的宏观结构。

完整的研究动机、统一形式系统、四类研究协议与阶段计划见[研究综述](docs/研究理论/研究综述.md)。

当前实现的游戏功能限于世界生成、时间与天气推进、事件记录、存档回滚与调试终端；NPC、群体社会与玩家玩法尚在设计阶段。游戏愿景见[游戏综述与世界观](docs/游戏综述与世界观.md)。

## 设计理念

- **世界先于智能体** — 因果世界独立于任何智能体的知识而存在；智能体只能通过自身的观测与行动逐步认识世界，其内部表征不等于世界的真实状态。
- **真值声明，而非事后发现** — 世界的生成机制作为因果真值被明确声明；事件记录与研究日志只用于追溯与验证，不是因果机制本身。
- **可复现是基础设施** — 世界生成、随机过程与干预执行均有确定性的重放机制，同一随机实验单位可生成严格配对的平行轨迹。

## 快速开始

依赖：Python 3.14、Godot 4.x。

```bash
python -m venv .venv
.venv/bin/pip install -r requirements.txt
cd backend && ../.venv/bin/python run_server.py   # 启动后端（默认 localhost）
```

用 Godot 打开 `frontend/` 运行游戏。打包与发行见 [build/README.md](build/README.md)。

## 文档

- [研究综述](docs/研究理论/研究综述.md) — 研究目标、概念定义和符号的唯一总纲
- [第一阶段实施定义](docs/研究理论/第一阶段实施定义.md) — 世界声明与引擎实现的最低契约
- [游戏综述与世界观](docs/游戏综述与世界观.md)
- [设计文档](docs/) — 按模块组织：世界框架、生命个体、心智系统、基因系统、群体社会、玩家行动、表现层

## 参与贡献

见 [CONTRIBUTING.md](CONTRIBUTING.md)（设计原则、开发流程、测试与提交约定）。提交 PR 前请阅读 [CLA.md](CLA.md)。

## License


根据 [CC BY-NC-SA 4.0](LICENSE) 许可证授权。商业使用需联系作者。
