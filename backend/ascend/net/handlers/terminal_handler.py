"""终端指令网络处理程序 — 将 Godot 前端的终端指令路由到 CommandExecutor。

通过 make_terminal_handler() 工厂函数创建，返回 {request_type: handler} 映射，
与 map_handler.py 中 make_map_handlers() 的模式一致。
"""

from ascend.log import get_logger

logger = get_logger(__name__)


def make_terminal_handler(executor):
    """为给定的 CommandExecutor 创建终端指令处理程序。

    Args:
        executor: CommandExecutor 实例，负责解析和执行指令文本。

    Returns:
        一个字典，将 "terminal_cmd" 映射到处理函数。
        处理函数接收消息字典，返回响应字典。
    """

    def handle_terminal_cmd(msg: dict) -> dict:
        """处理 "terminal_cmd" 请求。

        从 msg["payload"]["command"] 取指令文本，
        调用 executor.execute() 执行，返回文本输出。

        Args:
            msg: 请求消息字典。预期 payload 含 "command" 字段。

        Returns:
            响应字典:
                {type: "response", request_type: "terminal_cmd",
                 payload: {success: bool, output: "..."}}
        """
        payload = msg.get("payload", {})
        command: str = payload.get("command", "")

        if not command:
            return {
                "type": "response",
                "request_type": "terminal_cmd",
                "payload": {"success": True, "output": ""},
            }

        logger.debug("terminal_cmd: %s", command)
        result = executor.execute(command)
        logger.debug("terminal_cmd 完成: success=%s, output_len=%d",
                      result.success, len(result.output))

        return {
            "type": "response",
            "request_type": "terminal_cmd",
            "payload": {"success": result.success, "output": result.output},
        }

    return {
        "terminal_cmd": handle_terminal_cmd,
    }
