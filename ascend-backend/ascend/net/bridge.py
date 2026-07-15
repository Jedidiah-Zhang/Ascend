"""事件桥接 — 将 WorldTree 事件转发给 Godot 前端。

通过订阅 "*" 通配符监听所有事件，转换为 JSON 消息广播给连接的前端。
"""

from collections.abc import Callable

from ascend.log import get_logger
from ascend.world_tree.event import Event
from ascend.net.server import GameServer

logger = get_logger(__name__)


class EventBridge:
    """WorldTree ↔ Godot 事件桥接器。

    订阅 WorldTree 所有事件，转换为字典消息并广播。
    安装后自动转发，无需手动干预。

    Usage:
        bridge = EventBridge(world_tree, server)
        bridge.install()   # 开始转发
        bridge.uninstall() # 停止转发
    """

    def __init__(self, world_tree, server: GameServer) -> None:
        """初始化桥接器。

        Args:
            world_tree: WorldTree 实例（用于订阅事件）。
            server: GameServer 实例（用于广播消息）。
        """
        self._world_tree = world_tree
        self._server: GameServer = server
        self._installed: bool = False
        self._unsubscribe: Callable[[], None] | None = None

    def __repr__(self) -> str:
        """返回桥接器状态。

        Returns:
            含安装状态的 repr 字符串。
        """
        return f"EventBridge(installed={self._installed})"

    def install(self) -> None:
        """安装桥接器，开始转发 WorldTree 事件。

        幂等：已安装时调用无效果。
        """
        if self._installed:
            return
        self._unsubscribe = self._world_tree.subscribe("*", self._forward)
        self._installed = True
        logger.info("EventBridge 已安装，开始转发事件")

    def uninstall(self) -> None:
        """卸载桥接器，停止转发。

        幂等：未安装时调用无效果。
        """
        if not self._installed:
            return
        if self._unsubscribe:
            self._unsubscribe()
            self._unsubscribe = None
        self._installed = False
        logger.info("EventBridge 已卸载")

    def _forward(self, event: Event) -> None:
        """将 WorldTree 事件转换为字典并广播。

        回调由 WorldTree.publish() 在游戏线程中触发，
        GameServer.broadcast() 是线程安全的。

        Args:
            event: WorldTree 事件。
        """
        message = {
            "type": "event",
            "event_type": event.event_type,
            "payload": {
                "id": event.id,
                "timestamp": event.timestamp,
                "location": list(event.location),
                "initiator_type": event.initiator_type,
                "initiator_id": event.initiator_id,
                "data": event.data,
            },
        }
        self._server.broadcast(message)
