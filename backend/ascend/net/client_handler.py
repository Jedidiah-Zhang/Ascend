"""客户端连接处理器 — 每个 TCP 连接一个实例。

每连接两个线程：
  - 接收线程: 持续读取并解析帧；未认证时仅放行 hello 握手帧。
  - 发送线程: 从发送队列取帧 sendall——游戏线程调用 send() 仅入队，
    绝不阻塞（慢客户端由发送队列上限断开，不再冻结游戏线程）。

认证：连接建立后须在 AUTH_TIMEOUT 秒内完成 hello 握手（token +
协议版本），否则断开。
"""

import secrets
import socket
import threading
import time
from collections.abc import Callable

from ascend.log import get_logger
from ascend.net.protocol import (
    read_frame,
    encode_message,
    make_error,
    ProtocolError,
    PROTOCOL_VERSION,
)
from ascend.space.tile_grid import _TILEGRID_VERSION

logger = get_logger(__name__)

AUTH_TIMEOUT: float = 30.0  # 未认证连接的最大存活时间（秒）
SEND_QUEUE_LIMIT: int = 1024  # 发送队列上限（帧），超限 = 客户端消费太慢
RECV_CHUNK_SIZE: int = 4096  # 单次 recv 缓冲大小（字节）


class ClientHandler:
    """单个客户端连接处理器。

    线程安全：send() 可从任意线程调用（仅入队）。
    """

    def __init__(
        self,
        sock: socket.socket,
        addr: tuple[str, int],
        on_message: Callable[["ClientHandler", dict], None],
        on_disconnect: Callable[["ClientHandler"], None],
        token: str,
    ) -> None:
        """初始化客户端处理器。

        Args:
            sock: 已 accept 的客户端 socket。
            addr: 客户端地址 (host, port)。
            on_message: 收到完整消息时的回调 (handler, message)。
            on_disconnect: 连接断开时的回调。
            token: 服务器认证令牌（hello 校验用）。
        """
        self.sock: socket.socket = sock
        self.addr: tuple[int, int] = addr
        self.client_id: int = -1
        self.token: str = token
        self.verified: bool = False
        self._on_message: Callable[[ClientHandler, dict], None] = on_message
        self._on_disconnect: Callable[["ClientHandler"], None] = on_disconnect
        self._running: bool = False
        self._connected_at: float = 0.0
        self._send_lock: threading.Lock = threading.Lock()
        self._send_queue: list[bytes] = []
        self._send_cond: threading.Condition = threading.Condition()
        self._recv_thread: threading.Thread | None = None
        self._send_thread: threading.Thread | None = None

    def __repr__(self) -> str:
        """返回客户端地址。

        Returns:
            含地址和运行状态的 repr 字符串。
        """
        return f"ClientHandler({self.addr[0]}:{self.addr[1]}, running={self._running}, verified={self.verified})"

    def start(self) -> None:
        """启动接收与发送线程。"""
        self._running = True
        self._connected_at = time.monotonic()
        self.sock.settimeout(1.0)
        self._recv_thread = threading.Thread(
            target=self._recv_loop,
            name=f"game-client-{self.addr[1]}-recv",
            daemon=True,
        )
        self._send_thread = threading.Thread(
            target=self._send_loop,
            name=f"game-client-{self.addr[1]}-send",
            daemon=True,
        )
        self._recv_thread.start()
        self._send_thread.start()

    def close(self) -> None:
        """关闭连接并等待线程结束。"""
        self._running = False
        with self._send_cond:
            self._send_cond.notify_all()
        self._close_socket()
        if self._recv_thread:
            self._recv_thread.join(timeout=2.0)
        if self._send_thread:
            self._send_thread.join(timeout=2.0)

    def send(self, frame: bytes) -> bool:
        """发送一帧数据（仅入队，从不阻塞）。

        Args:
            frame: 已编码的消息帧。

        Returns:
            False 表示发送队列已满（客户端消费过慢），调用方应断开。
        """
        with self._send_cond:
            if len(self._send_queue) >= SEND_QUEUE_LIMIT:
                logger.error(
                    "发送队列超限 %d，断开 %s:%d",
                    SEND_QUEUE_LIMIT, self.addr[0], self.addr[1],
                )
                self.request_close()
                return False
            self._send_queue.append(frame)
            self._send_cond.notify()
        return True

    # ── 内部 ──────────────────────────────────────────────

    def _close_socket(self) -> None:
        """关闭底层 socket（幂等，可从任意线程调用，不 join 线程）。

        断开路径（recv/send 线程自退出）必须用它而非 close()——
        close() 会 join 自身线程，从线程内调用抛 RuntimeError 死锁。
        """
        try:
            self.sock.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        try:
            self.sock.close()
        except OSError:
            pass

    def request_close(self) -> None:
        """请求断开：置停止标志并关闭 socket 使各线程退出。

        线程安全的异步断开，可在 recv/send 线程内调用（不 join 自身）。
        """
        self._running = False
        with self._send_cond:
            self._send_cond.notify_all()
        self._close_socket()

    def _send_loop(self) -> None:
        """发送循环（运行在发送线程）：取帧 sendall，失败主动断开。"""
        while self._running:
            with self._send_cond:
                while self._running and not self._send_queue:
                    self._send_cond.wait(timeout=0.5)
                if not self._running:
                    break
                frames = self._send_queue
                self._send_queue = []
            for frame in frames:
                try:
                    self.sock.sendall(frame)
                except OSError as exc:
                    logger.error("发送失败 %s:%d: %s", self.addr[0], self.addr[1], exc)
                    self._running = False
                    self._close_socket()
                    self._on_disconnect(self)
                    return

    def _recv_loop(self) -> None:
        """接收循环（运行在接收线程）。"""
        buffer = bytearray()
        while self._running:
            # 认证超时兜底：未握手连接的存活时间受限
            if not self.verified and time.monotonic() - self._connected_at > AUTH_TIMEOUT:
                logger.warning("握手超时，断开 %s:%d", self.addr[0], self.addr[1])
                break
            try:
                data = self.sock.recv(RECV_CHUNK_SIZE)
                if not data:
                    break
                buffer.extend(data)
                while True:
                    message = read_frame(buffer)
                    if message is None:
                        break
                    if not self._handle_incoming(message):
                        self._running = False
                        break
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
        self._close_socket()
        self._on_disconnect(self)

    def _handle_incoming(self, message: dict) -> bool:
        """处理一帧消息。返回 False 表示应断开连接。"""
        if not self.verified:
            return self._handle_hello(message)
        self._on_message(self, message)
        return True

    def _handle_hello(self, message: dict) -> bool:
        """处理握手帧：校验 token 与协议版本。

        Returns:
            False 表示握手失败，应断开。
        """
        if message.get("type") != "hello":
            logger.warning(
                "未认证消息被拒 %s:%d: type=%s",
                self.addr[0], self.addr[1], message.get("type"),
            )
            return False
        payload = message.get("payload", {})
        token = str(payload.get("token", ""))
        if not secrets.compare_digest(token, self.token):
            logger.warning("握手失败（token 错误）: %s:%d", self.addr[0], self.addr[1])
            return False
        version = payload.get("protocol_version")
        if version != PROTOCOL_VERSION:
            logger.warning(
                "协议版本不兼容 %s:%d: %s（当前 %#04x）",
                self.addr[0], self.addr[1], version, PROTOCOL_VERSION,
            )
            # 握手路径单帧直接 sendall（断开在即，不经过发送队列）
            try:
                self.sock.sendall(encode_message(make_error(
                    "hello", f"协议版本不兼容: {version}", seq=0,
                )))
            except OSError:
                pass
            return False
        # tile 数据 BLOB 版本协商：客户端上报其支持的版本，低于服务端
        # 数据格式版本 = 无法解码（BLOB 布局随版本演化），握手即拒绝
        client_blob = int(payload.get("tile_blob_version", 0) or 0)
        if client_blob < _TILEGRID_VERSION:
            logger.warning(
                "BLOB 版本不兼容 %s:%d: 客户端 %d < 服务端 %d",
                self.addr[0], self.addr[1], client_blob, _TILEGRID_VERSION,
            )
            try:
                self.sock.sendall(encode_message(make_error(
                    "hello",
                    f"tile 数据版本不兼容: 客户端 {client_blob} < 服务端 {_TILEGRID_VERSION}",
                    seq=0,
                )))
            except OSError:
                pass
            return False
        self.verified = True
        self.send(encode_message({
            "type": "hello_ack",
            "payload": {"blob_version": _TILEGRID_VERSION},
        }))
        logger.info("握手成功: %s:%d", self.addr[0], self.addr[1])
        return True
