"""终端指令系统 — 将控制台指令封装为可复用的 CommandExecutor。

提供 CommandResult 数据类和 CommandExecutor 执行器。
支持 pause/resume/tick/sleep/travel/jump/mode/lang/events/map 等指令。
"""

from .executor import CommandExecutor, CommandResult

__all__ = ["CommandExecutor", "CommandResult"]
