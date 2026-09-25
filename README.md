# Ascend

> 面向因果世界模型研究与模拟游戏的 AI 原生平台。项目当前处于完全重构阶段。

[English](README.en.md) | 中文

<p align="center">
  <img src="https://img.shields.io/badge/C%2B%2B-17-00599C?logo=cplusplus&logoColor=white" alt="C++17">
  <img src="https://img.shields.io/badge/Godot-4.x-478CBF?logo=godotengine&logoColor=white" alt="Godot 4.x">
  <img src="https://img.shields.io/badge/License-CC%20BY--NC--SA%204.0-EF9421" alt="License">
</p>

## 当前进度

新架构按世界库、智能体库、游戏平台和研究平台逐步建设。目前落地的是研究平台的一个 C++17 无界面切片：模块注册、公开量与方法的检查、类型化绑定和调用。它不代表完整研究协议、世界库或游戏平台已经实现。

尚未实施的主要部分包括世界库、智能体、研究工作台、实验组织，以及可玩的 Godot 客户端。`miskhak/client/` 目前只保留 Godot 项目壳和启动场景。

## 架构与研究依据

- [整体架构](docs/整体架构.md) — 两库两平台职责、依赖方向与待决边界
- [世界](docs/世界/综述.md) — 世界库职责与设计进度
- [智能体](docs/智能体/综述.md) — 智能体职责与设计进度
- [游戏平台](docs/游戏平台/综述.md) — 游戏装配、会话与表现
- [研究平台](docs/研究平台/综述.md) — 因果实验环境、声明引擎与研究计划
- [研究综述](docs/研究理论/研究综述.md) — 作者撰写的研究最高指导依据

设计状态与实现状态分开记录；候选方案不是实施要求。文档维护规则见[贡献指南](CONTRIBUTING.md#文档维护)。

## 构建与验证

当前 C++ 核心需要 CMake 3.20 和支持 C++17 的编译器。在仓库根目录运行：

```bash
cmake -S kheker/native -B build/work/native -DCMAKE_BUILD_TYPE=Debug -DBUILD_TESTING=ON
cmake --build build/work/native --config Debug --parallel 4
ctest --test-dir build/work/native --build-config Debug --output-on-failure
```

构建产物位于已忽略的 `build/work/native/`。当前验证范围及限制见[声明引擎实现文档](docs/研究平台/实验环境与声明接入/实现.md#4-代码与构建入口)。

## 参与贡献

见 [CONTRIBUTING.md](CONTRIBUTING.md)（设计原则、文档维护、测试与提交约定）。提交 PR 前请阅读 [CLA.md](CLA.md)。

## License

根据 [CC BY-NC-SA 4.0](LICENSE) 许可证授权。商业使用需联系作者。
