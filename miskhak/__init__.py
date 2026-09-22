"""游戏包装 — 应用进程、存档、通信、终端、实体、时间、事件总线。

- 应用入口：``miskhak.app``（GameEngine / WorldInitialized / main）
- 存档：``miskhak.save``（世界状态载荷、快照、血统、加密、chunk 存储）
- 通信：``miskhak.net``（前后端协议与 handler；含研究 API 通道）
- 终端：``miskhak.terminal``（do/trace 研究操作面 + 游戏调试指令组）
- 实体/时间：``miskhak.entity``（仅玩家服务；实体运行时在
  ``olam.adapters.entity``） / ``miskhak.time``
- 事件总线：``miskhak.events``（玩法事件图与归档）

本包 ``__init__`` 不导入子模块。
"""
