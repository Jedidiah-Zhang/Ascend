"""指令执行结果类型。"""

from dataclasses import dataclass


@dataclass
class CommandResult:
    """指令执行结果。

    Attributes:
        success: 是否成功执行。
        output: 执行输出的文本（空字符串表示无输出）。
        is_quit: 是否为退出指令。
    """
    success: bool = True
    output: str = ""
    is_quit: bool = False
