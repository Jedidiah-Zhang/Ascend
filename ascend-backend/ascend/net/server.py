"""TCP 服务器 — 监听 Godot 前端连接。

在后台线程运行，接收 Godot 消息并放入队列，
同时从发送队列取出消息推送给 Godot。
"""

import socket
import threading
import time
from collections.abc import Callable
from ascend.log import get_logger
from ascend.net.client_handler import ClientHandler
from ascend.net.protocol import encode_message

logger = get_logger(__name__)


class GameServer:
    """TCP 服务器，管理 Godot 客户端连接。

    在后台线程运行 accept 循环，每个客户端一个接收线程。
    线程安全：_send_queue 由锁保护，可从任意线程调用 broadcast()。

    Attributes:
        host: 监听地址。
        port: 监听端口。
        is_running: 服务器是否在运行。
    """

    def __init__(self, host: str = "127.0.0.1", port: int = 9081) -> None:
        """初始化服务器。

        Args:
            host: 监听地址，默认仅本地。
            port: 监听端口。
        """
        self.host: str = host
        self.port: int = port
        self.is_running: bool = False

        self._socket: socket.socket | None = None
        self._accept_thread: threading.Thread | None = None
        self._clients: list["ClientHandler"] = []
        self._clients_lock: threading.Lock = threading.Lock()
        self._receive_queue: list[dict] = []
        self._receive_lock: threading.Lock = threading.Lock()

    def __repr__(self) -> str:
        """返回服务器状态摘要。

        Returns:
            含地址、运行状态、客户端数的 repr 字符串。
        """
        return (
            f"GameServer({self.host}:{self.port}, "
            f"running={self.is_running}, "
            f"clients={self.client_count})"
        )

    @property
    def client_count(self) -> int:
        """当前连接的客户端数。"""
        with self._clients_lock:
            return len(self._clients)

    # ── 生命周期 ──────────────────────────────────────

    def start(self) -> None:
        """启动服务器，在后台线程开始监听。

        幂等：已在运行时调用无效果。
        """
        if self.is_running:
            return
        self._socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._socket.bind((self.host, self.port))
        self._socket.listen(5)
        self._socket.settimeout(1.0)
        self.is_running = True
        self._accept_thread = threading.Thread(
            target=self._accept_loop, name="game-server-accept", daemon=True
        )
        self._accept_thread.start()
        logger.info("GameServer 启动: %s:%d", self.host, self.port)

    def stop(self) -> None:
        """停止服务器，断开所有客户端。

        幂等：已停止时调用无效果。
        """
        if not self.is_running:
            return
        self.is_running = False
        if self._accept_thread:
            self._accept_thread.join(timeout=3.0)
        with self._clients_lock:
            clients = list(self._clients)
            self._clients.clear()
        for client in clients:
            client.close()
        if self._socket:
            self._socket.close()
            self._socket = None
        logger.info("GameServer 已停止")

    # ── 发送 ──────────────────────────────────────────

    def broadcast(self, message: dict) -> None:
        """向所有连接的客户端广播消息。

        线程安全，可从游戏线程调用。

        Args:
            message: 要广播的消息字典。
        """
        frame = encode_message(message)
        with self._clients_lock:
            for client in self._clients:
                client.send(frame)

    # ── 接收 ──────────────────────────────────────────

    def receive_all(self) -> list[dict]:
        """取出所有排队消息（消费队列）。

        从游戏线程调用，获取 Godot 发来的玩家指令。

        Returns:
            消息字典列表，无消息时返回空列表。
        """
        with self._receive_lock:
            if not self._receive_queue:
                return []
            messages = self._receive_queue
            self._receive_queue = []
            return messages

    # ── 内部 ──────────────────────────────────────────

    def _accept_loop(self) -> None:
        """接收连接循环（运行在后台线程）。"""
        while self.is_running:
            try:
                conn, addr = self._socket.accept()
                logger.info("新连接: %s:%d", addr[0], addr[1])
                handler = ClientHandler(conn, addr, self._on_message, self._on_disconnect)
                with self._clients_lock:
                    self._clients.append(handler)
                handler.start()
            except socket.timeout:
                continue
            except OSError:
                if self.is_running:
                    logger.exception("accept 错误")
                break

    def _on_message(self, message: dict) -> None:
        """客户端消息回调（从客户端线程调用）。"""
        with self._receive_lock:
            self._receive_queue.append(message)

    def _on_disconnect(self, handler: "ClientHandler") -> None:
        """客户端断开回调（从客户端线程调用）。"""
        logger.info("连接断开: %s:%d", handler.addr[0], handler.addr[1])
        with self._clients_lock:
            if handler in self._clients:
                self._clients.remove(handler)
