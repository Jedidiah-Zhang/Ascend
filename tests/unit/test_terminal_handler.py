"""终端请求处理程序单元测试。

通过 mock executor 验证 make_terminal_handler 创建的处理函数行为。
"""

import pytest
from unittest.mock import Mock, MagicMock
from ascend.net.handlers.terminal_handler import make_terminal_handler


@pytest.fixture
def mock_executor():
    """模拟 CommandExecutor 的固件。

    executor.execute() 根据命令返回对应的 CommandResult。
    """
    executor = MagicMock()

    def fake_execute(command: str) -> object:
        """根据命令返回模拟的 CommandResult。"""
        from collections import namedtuple
        CommandResult = namedtuple("CommandResult", ["success", "output", "is_quit"])

        mapping = {
            "status": CommandResult(True, "活跃: 0h 00m 00s  |  第 1 天 06:00:00  |  模式: 日常实时  |  状态: ▶ 运行中", False),
            "st": CommandResult(True, "活跃: 0h 00m 00s  |  第 1 天 06:00:00  |  模式: 日常实时  |  状态: ▶ 运行中", False),
            "pause": CommandResult(True, "游戏已暂停", False),
            "resume": CommandResult(True, "游戏已恢复", False),
            "": CommandResult(True, "", False),
        }

        result = mapping.get(command)
        if result is not None:
            return result

        # 未知命令：返回 error
        return CommandResult(False, f"未知指令 '{command}'，输入 ? 查看帮助", False)

    executor.execute.side_effect = fake_execute
    return executor


# ══════════════════════════════════════════════════════════
# T30: terminal_cmd status
# ══════════════════════════════════════════════════════════

class TestTerminalCmdStatus:
    """terminal_cmd status 请求测试。"""

    def test_T30_terminal_cmd_status(self, mock_executor):
        """handler({"payload": {"command": "status"}}) 返回含 status 输出的响应。

        Arrange:
            make_terminal_handler(mock_executor) 返回 handlers 字典。
        Act:
            获取 "terminal_cmd" handler，传入 status 请求。
        Assert:
            响应包含 type=response、request_type=terminal_cmd、
            及含状态文本的 payload.output。
        """
        handlers = make_terminal_handler(mock_executor)
        handle = handlers["terminal_cmd"]

        msg = {
            "type": "request",
            "request_type": "terminal_cmd",
            "seq": 1,
            "payload": {"command": "status"},
        }

        response = handle(msg)

        assert response["type"] == "response"
        assert response["request_type"] == "terminal_cmd"
        # seq 由 MessageDispatcher 在 dispatch 时添加，
        # 处理函数内部不负责添加 seq 字段
        payload = response["payload"]
        assert payload["success"] is True
        assert "第 1 天" in payload["output"]
        assert "运行中" in payload["output"]


# ══════════════════════════════════════════════════════════
# T31: terminal_cmd pause
# ══════════════════════════════════════════════════════════

class TestTerminalCmdPause:
    """terminal_cmd pause 请求测试。"""

    def test_T31_terminal_cmd_pause(self, mock_executor):
        """handler({"payload": {"command": "pause"}}) 返回暂停消息。

        Arrange:
            make_terminal_handler 返回的 handlers。
        Act:
            调用 handler，传入 pause 命令。
        Assert:
            响应 payload.success=True，output 包含暂停信息。
        """
        handlers = make_terminal_handler(mock_executor)
        handle = handlers["terminal_cmd"]

        msg = {
            "type": "request",
            "request_type": "terminal_cmd",
            "seq": 2,
            "payload": {"command": "pause"},
        }

        response = handle(msg)

        assert response["type"] == "response"
        assert response["payload"]["success"] is True
        assert "暂停" in response["payload"]["output"]


# ══════════════════════════════════════════════════════════
# T32: terminal_cmd invalid
# ══════════════════════════════════════════════════════════

class TestTerminalCmdInvalid:
    """terminal_cmd 无效命令测试。"""

    def test_T32_terminal_cmd_invalid(self, mock_executor):
        """handler({"payload": {"command": "foobar"}}) 返回错误。

        Arrange:
            make_terminal_handler 返回的 handlers。
        Act:
            调用 handler，传入 foobar 命令。
        Assert:
            响应 payload.success=False，output 包含错误信息。
        """
        handlers = make_terminal_handler(mock_executor)
        handle = handlers["terminal_cmd"]

        msg = {
            "type": "request",
            "request_type": "terminal_cmd",
            "seq": 3,
            "payload": {"command": "foobar"},
        }

        response = handle(msg)

        assert response["type"] == "response"
        assert response["payload"]["success"] is False
        assert "foobar" in response["payload"]["output"]


# ══════════════════════════════════════════════════════════
# T33: terminal_cmd missing command
# ══════════════════════════════════════════════════════════

class TestTerminalCmdMissingCommand:
    """terminal_cmd 缺失 command 字段测试。"""

    def test_T33_terminal_cmd_missing_command(self, mock_executor):
        """handler({"payload": {}}) 返回参数错误。

        Arrange:
            make_terminal_handler 返回的 handlers。
        Act:
            调用 handler，payload 不含 command 字段。
        Assert:
            响应 payload.success=True，output 为空（缺 command 等同于空指令）。
        """
        handlers = make_terminal_handler(mock_executor)
        handle = handlers["terminal_cmd"]

        msg = {
            "type": "request",
            "request_type": "terminal_cmd",
            "seq": 4,
            "payload": {},
        }

        response = handle(msg)

        assert response["type"] == "response"
        assert response["payload"]["success"] is True
        assert response["payload"]["output"] == ""


# ══════════════════════════════════════════════════════════
# T34: terminal_cmd empty command
# ══════════════════════════════════════════════════════════

class TestTerminalCmdEmpty:
    """terminal_cmd 空命令测试。"""

    def test_T34_terminal_cmd_empty_command(self, mock_executor):
        """handler({"payload": {"command": ""}}) 返回空输出。

        Arrange:
            make_terminal_handler 返回的 handlers。
        Act:
            调用 handler，command 为空字符串。
        Assert:
            响应 payload.success=True，output 为空字符串。
        """
        handlers = make_terminal_handler(mock_executor)
        handle = handlers["terminal_cmd"]

        msg = {
            "type": "request",
            "request_type": "terminal_cmd",
            "seq": 5,
            "payload": {"command": ""},
        }

        response = handle(msg)

        assert response["type"] == "response"
        assert response["payload"]["success"] is True
        assert response["payload"]["output"] == ""


# ══════════════════════════════════════════════════════════
# T35: terminal_handler_registration
# ══════════════════════════════════════════════════════════

class TestRegistration:
    """make_terminal_handler 注册测试。"""

    def test_T35_terminal_handler_registration(self, mock_executor):
        """make_terminal_handler 返回含 "terminal_cmd" 键的字典。

        Arrange:
            mock_executor。
        Act:
            调用 make_terminal_handler(mock_executor)。
        Assert:
            返回的 dict 包含 "terminal_cmd" 键，且值为 callable。
        """
        handlers = make_terminal_handler(mock_executor)

        assert "terminal_cmd" in handlers
        assert callable(handlers["terminal_cmd"])

    def test_handler_repr_contains_terminal_cmd(self, mock_executor):
        """handlers dict 的 repr 不报错，仅含 terminal_cmd。"""
        handlers = make_terminal_handler(mock_executor)
        assert len(handlers) == 1
        assert list(handlers.keys()) == ["terminal_cmd"]
