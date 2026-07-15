"""客户端连接处理器 — 每个 TCP 连接一个实例。

运行接收线程，持续读取并解析帧。
"""

import socket
import threading
from collections.abc import Callable

from ascend.log import get_logger
from ascend.net.protocol import read_frame, ProtocolError

logger = get_logger(__name__)


class ClientHandler:
    """单个客户端连接处理器。

    运行接收线程，持续读取并解析帧。
    """

    def __init__(
        self,
        sock: socket.socket,
        addr: tuple[str, int],
        on_message: Callable[[dict], None],
        on_disconnect: Callable[["ClientHandler"], None],
    ) -> None:
        """初始化客户端处理器。

        Args:
            sock: 已 accept 的客户端 socket。
            addr: 客户端地址 (host, port)。
            on_message: 收到完整消息时的回调。
            on_disconnect: 连接断开时的回调。
        """
        self.sock: socket.socket = sock
        self.addr: tuple[str, int] = addr
        self._on_message: Callable[[dict], None] = on_message
        self._on_disconnect: Callable[["ClientHandler"], None] = on_disconnect
        self._recv_thread: threading.Thread | None = None
        self._running: bool = False
        self._send_lock: threading.Lock = threading.Lock()

    def __repr__(self) -> str:
        """返回客户端地址。

        Returns:
            含地址和运行状态的 repr 字符串。
        """
        return f"ClientHandler({self.addr[0]}:{self.addr[1]}, running={self._running})"

    def start(self) -> None:
        """启动接收线程。"""
        self._running = True
        self.sock.settimeout(1.0)
        self._recv_thread = threading.Thread(
            target=self._recv_loop,
            name=f"game-client-{self.addr[1]}",
            daemon=True,
        )
        self._recv_thread.start()

    def close(self) -> None:
        """关闭连接并等待线程结束。"""
        self._running = False
        if self._recv_thread:
            self._recv_thread.join(timeout=2.0)
        try:
            self.sock.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        self.sock.close()

    def send(self, frame: bytes) -> None:
        """发送一帧数据（线程安全）。

        Args:
            frame: 已编码的消息帧。
        """
        with self._send_lock:
            try:
                self.sock.sendall(frame)
            except OSError as exc:
                logger.error("发送失败 %s:%d: %s", self.addr[0], self.addr[1], exc)
                self._running = False

    def _recv_loop(self) -> None:
        """接收循环（运行在客户端线程）。"""
        buffer = bytearray()
        while self._running:
            try:
                data = self.sock.recv(4096)
                if not data:
                    break
                buffer.extend(data)
                while True:
                    message = read_frame(buffer)
                    if message is None:
                        break
                    self._on_message(message)
            except socket.timeout:
                continue
            except ProtocolError as exc:
                logger.error("协议错误 %s:%d: %s", self.addr[0], self.addr[1], exc)
                break
            except OSError as exc:
                if self._running:
                    logger.error("接收错误 %s:%d: %s", self.addr[0], self.addr[1], exc)
                break
        self._running = False
        self._on_disconnect(self)
