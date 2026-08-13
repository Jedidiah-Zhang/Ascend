"""ClientHandler 断开路径资源清理单元测试。

回归：recv/send 线程自退出路径必须显式关闭 socket，不得依赖 GC 兜底
（此前仅 close()/shutdown 显式处理，断开路径的 socket 要靠循环 GC
回收 handler 对象才关闭，FD 释放时机不确定）。
"""

import socket
import time

from ascend.net.client_handler import ClientHandler


def _make_handler(server_sock: socket.socket) -> ClientHandler:
    """构造并启动 handler（socketpair 服务器端）。"""
    handler = ClientHandler(
        server_sock,
        ("127.0.0.1", 0),
        on_message=lambda h, m: None,
        on_disconnect=lambda h: None,
        token="test-token",
    )
    handler.start()
    return handler


def _wait_closed(handler: ClientHandler, timeout: float = 3.0) -> None:
    """等待 handler 关闭底层 socket（fileno() == -1）。"""
    deadline = time.monotonic() + timeout
    while handler.sock.fileno() != -1 and time.monotonic() < deadline:
        time.sleep(0.01)


class TestDisconnectCleanup:
    """断开路径必须显式关闭 socket。"""

    def test_recv_eof_closes_socket(self) -> None:
        """客户端正常关闭（EOF）→ 接收线程退出并关闭 socket。"""
        server_sock, client_sock = socket.socketpair()
        handler = _make_handler(server_sock)
        try:
            client_sock.close()
            _wait_closed(handler)
            assert handler.sock.fileno() == -1, "EOF 断开后 socket 应被显式关闭"
            assert not handler._recv_thread.is_alive(), "接收线程应已退出"
        finally:
            client_sock.close()
            handler.close()

    def test_send_failure_closes_socket(self) -> None:
        """对端只读端关闭 → 发送线程失败退出并关闭 socket。

        触发机制：对端 shutdown(SHUT_RD) 后 sendall 积压超过内核发送缓冲，
        阻塞直至 socket 1s 超时（TimeoutError，同为 OSError 子类；对端
        完全关闭时则为 EPIPE）。断言行为与触发路径无关：发送线程失败后
        socket 必须显式关闭。
        """
        server_sock, client_sock = socket.socketpair()
        handler = _make_handler(server_sock)
        try:
            # 对端不再读：发送缓冲填满后 sendall 失败（纯 close 会被
            # recv 线程的 EOF 路径抢先关 socket，测不到发送失败路径）
            client_sock.shutdown(socket.SHUT_RD)
            # 积压量远超内核缓冲（Linux 默认 ~208KB；1000×4KB=4MB，仍低于
            # SEND_QUEUE_LIMIT=1024 帧的队列上限，不触发 send() 的主动断开）
            for _ in range(1000):
                handler.send(b"x" * 4096)
            _wait_closed(handler)
            assert handler.sock.fileno() == -1, "发送失败后 socket 应被显式关闭"
            assert not handler._send_thread.is_alive(), "发送线程应已退出"
        finally:
            client_sock.close()
            handler.close()
