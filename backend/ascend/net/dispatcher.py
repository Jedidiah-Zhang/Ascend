"""消息分发器 — 根据 request_type 将传入请求路由到注册的处理程序。

线程安全。从游戏线程调用 process()（不是从服务器接收线程）。
"""

from ascend.log import get_logger

logger = get_logger(__name__)


class MessageDispatcher:
    """将传入的客户端消息路由到已注册的处理程序。

    用法:
        dispatcher = MessageDispatcher(server)
        dispatcher.register("get_chunks", handle_get_chunks)

        # 每个 tick:
        dispatcher.process()
    """

    def __init__(self, server) -> None:
        """初始化分发器。

        Args:
            server: GameServer 实例（用于接收消息和广播响应）。
        """
        self._server = server
        self._handlers: dict[str, callable] = {}

    def __repr__(self) -> str:
        """返回分发器状态摘要。

        Returns:
            含已注册处理程序数量的 repr 字符串。
        """
        return (
            f"MessageDispatcher(handlers={list(self._handlers.keys())})"
        )

    def register(self, request_type: str, handler: callable) -> None:
        """注册一个请求处理程序。

        Args:
            request_type: 要处理的请求类型字符串。
            handler: 可调用对象，接收 (message_dict) 返回响应字典或 None。

        Raises:
            ValueError: 如果 request_type 已注册。
        """
        if request_type in self._handlers:
            raise ValueError(
                f"处理程序已注册: request_type={request_type}"
            )
        self._handlers[request_type] = handler
        logger.debug("注册处理程序: request_type=%s", request_type)

    def process(self) -> None:
        """处理所有排队消息（每个游戏 tick 调用一次）。"""
        messages = self._server.receive_all()
        for msg in messages:
            self._dispatch(msg)

    def _dispatch(self, msg: dict) -> None:
        """将一条消息路由到其处理程序。

        Args:
            msg: 从客户端收到的消息字典。
        """
        msg_type = msg.get("type", "")
        if msg_type != "request":
            logger.debug("忽略非请求消息: type=%s", msg_type)
            return

        req_type = msg.get("request_type", "")
        if not req_type:
            logger.warning("请求缺少 request_type")
            self._server.broadcast({
                "type": "error",
                "request_type": "",
                "seq": msg.get("seq", 0),
                "error": "missing request_type",
            })
            return

        handler = self._handlers.get(req_type)
        if handler is None:
            logger.warning("无处理程序: request_type=%s", req_type)
            self._server.broadcast({
                "type": "error",
                "request_type": req_type,
                "seq": msg.get("seq", 0),
                "error": f"unknown request_type: {req_type}",
            })
            return

        try:
            response = handler(msg)
            if response is not None:
                response["seq"] = msg.get("seq", 0)
                self._server.broadcast(response)
        except Exception as exc:
            logger.exception("处理程序错误: request_type=%s", req_type)
            self._server.broadcast({
                "type": "error",
                "request_type": req_type,
                "seq": msg.get("seq", 0),
                "error": str(exc),
            })
