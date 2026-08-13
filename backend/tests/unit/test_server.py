"""GameServer accept 循环错误处理单元测试。

回归：瞬时 OSError（ECONNABORTED 等）不得使 accept 循环永久退出——
否则监听 socket 仍在、新连接照常完成 TCP 握手却无人 accept，服务器
"看似运行实则瘫痪"（前端连得上但永远等不到 hello_ack）。

说明：socket 实例属性只读，通过 patch.object 临时替换类方法；
单测在进程内串行执行，patch 上下文退出即恢复，不影响其他测试。
"""

import errno
import socket
import threading
import time
from unittest.mock import patch

from ascend.net.server import GameServer


def _make_listening_server() -> GameServer:
    """构造已监听但未启动 accept 循环的服务器。"""
    srv = GameServer(port=0)
    srv._socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv._socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv._socket.bind(("127.0.0.1", 0))
    srv._socket.listen(5)
    srv._socket.settimeout(1.0)
    return srv


def _start_accept_thread(srv: GameServer) -> threading.Thread:
    """手动启动 accept 线程（等价 start() 的线程启动部分）。"""
    srv.is_running = True
    thread = threading.Thread(
        target=srv._accept_loop, name="test-accept", daemon=True,
    )
    thread.start()
    return thread


class TestAcceptLoop:
    """accept 循环瞬时错误重试与致命错误退出。"""

    def test_transient_error_retries(self) -> None:
        """ECONNABORTED 后循环继续，客户端仍可正常连接。"""
        real_accept = socket.socket.accept
        state = {"calls": 0}

        def flaky(self_sock):
            state["calls"] += 1
            if state["calls"] == 1:
                raise OSError(errno.ECONNABORTED, "Connection aborted")
            return real_accept(self_sock)

        srv = _make_listening_server()
        client = None
        with patch.object(socket.socket, "accept", flaky):
            thread = _start_accept_thread(srv)
            try:
                client = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                client.settimeout(1.0)
                client.connect(("127.0.0.1", srv._socket.getsockname()[1]))

                deadline = time.monotonic() + 3.0
                while srv.client_count < 1 and time.monotonic() < deadline:
                    time.sleep(0.02)

                assert state["calls"] >= 2, "瞬时错误后应继续 accept 而非退出"
                assert srv.client_count == 1, "瞬时错误不应使服务器瘫痪"
                assert thread.is_alive(), "瞬时错误后 accept 循环应存活"
            finally:
                if client:
                    client.close()
                srv.stop()

    def test_fatal_error_stops_loop(self) -> None:
        """EBADF（socket 已关闭）→ 循环退出，等待 stop() 接管。"""
        def boom(_self_sock):
            raise OSError(errno.EBADF, "Bad file descriptor")

        srv = _make_listening_server()
        with patch.object(socket.socket, "accept", boom):
            thread = _start_accept_thread(srv)
            try:
                deadline = time.monotonic() + 2.0
                while thread.is_alive() and time.monotonic() < deadline:
                    time.sleep(0.02)
                assert not thread.is_alive(), "致命错误应退出 accept 循环"
                assert srv.is_running, "is_running 由 stop() 管理，循环退出不应改它"
            finally:
                srv.stop()
