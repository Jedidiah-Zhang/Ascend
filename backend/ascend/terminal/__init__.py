"""终端指令系统 — 将控制台指令封装为可复用的 CommandExecutor。

提供 CommandResult 数据类和 CommandExecutor 执行器。
支持 status/time/weather/entity/continent/tp/lang/events 等指令。
指令组实现按 mixin 拆分至 *_commands.py。
"""

from .executor import CommandExecutor, ExecutorConfig
from .result import CommandResult

__all__ = ["CommandExecutor", "ExecutorConfig", "CommandResult"]
