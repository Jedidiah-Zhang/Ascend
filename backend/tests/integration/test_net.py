"""网络层整合测试 — 验证 Python TCP Server 与 WorldTree 事件桥接。

用法:
    PYTHONPATH=backend .venv/bin/python -m pytest backend/tests/integration/test_net.py -v -s
"""

import socket
import struct
import json
import time
import threading
import pytest

from ascend.net import GameServer, EventBridge, encode_message, decode_message, read_frame, ProtocolError, PROTOCOL_VERSION
from ascend.net.client_handler import SEND_QUEUE_LIMIT
from ascend.world_tree.tree import WorldTree
from ascend.world_tree.event import Event
from ascend.log import setup_logging, get_logger

logger = get_logger(__name__)

# 避免端口冲突，使用随机端口
TEST_PORT = 19081


# ── 辅助函数 ────────────────────────────────────────────────────────


def recv_frame(sock: socket.socket, timeout: float = 2.0) -> dict | None:
    """从 socket 读取一帧消息。

    Args:
        sock: 客户端 socket。
        timeout: 读取超时秒数。

    Returns:
        消息字典，超时时返回 None。
    """
    sock.settimeout(timeout)
    # 读取 1 字节版本 + 4 字节长度前缀
    try:
        header = sock.recv(5)
    except socket.timeout:
        return None
    if len(header) < 5:
        return None
    version: int = header[0]
    length: int = struct.unpack(">I", header[1:5])[0]
    # 读取体
    body = bytearray()
    while len(body) < length:
        try:
            chunk = sock.recv(length - len(body))
        except socket.timeout:
            return None
        if not chunk:
            return None
        body.extend(chunk)
    return decode_message(bytes(body), version)


def send_frame(sock: socket.socket, message: dict) -> None:
    """通过 socket 发送一帧消息。

    Args:
        sock: 客户端 socket。
        message: 消息字典。
    """
    frame = encode_message(message)
    sock.sendall(frame)


def handshake(sock: socket.socket, server, protocol_version: int = PROTOCOL_VERSION) -> dict:
    """执行完整握手（hello → hello_ack），返回服务器响应。

    Args:
        sock: 已连接的客户端 socket。
        server: 测试服务器（提供 token）。
        protocol_version: 发送的协议版本（可传错误值测拒绝路径）。

    Returns:
        握手响应消息（hello_ack 或 error）。
    """
    send_frame(sock, {
        "type": "hello",
        "seq": 1,
        "payload": {"token": server.token, "protocol_version": protocol_version},
    })
    return recv_frame(sock)


def send_raw(sock: socket.socket, raw: bytes) -> None:
    """发送原始字节（构造畸形帧用）。"""
    sock.sendall(raw)


# ── 测试固件 ─────────────────────────────────────────────────────────


@pytest.fixture
def server():
    """创建并启动的 GameServer 固件。"""
    srv = GameServer(port=TEST_PORT)
    srv.start()
    yield srv
    srv.stop()


@pytest.fixture
def world_tree():
    """独立的 WorldTree 固件（不使用全局实例）。"""
    return WorldTree()


@pytest.fixture
def client_socket(server):
    """连接测试服务器的客户端 socket 固件。"""
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    # 等待服务器就绪
    for _ in range(10):
        try:
            sock.connect(("127.0.0.1", TEST_PORT))
            break
        except ConnectionRefusedError:
            time.sleep(0.1)
    else:
        pytest.fail("无法连接到测试服务器")
    yield sock
    sock.close()


# ── 测试 ─────────────────────────────────────────────────────────────


class TestProtocol:
    """协议层单元测试。"""

    def test_encode_decode_simple(self) -> None:
        """简单消息的编码-解码往返。"""
        msg = {"type": "event", "event_type": "test", "payload": {}}
        encoded = encode_message(msg)
        decoded = decode_message(encoded[5:], encoded[0])
        assert decoded == msg

    def test_encode_decode_unicode(self) -> None:
        """含 Unicode 消息的编解码。"""
        msg = {"type": "event", "event_type": "测试", "payload": {"文本": "中文内容"}}
        encoded = encode_message(msg)
        decoded = decode_message(encoded[5:], encoded[0])
        assert decoded == msg

    def test_encode_decode_complex(self) -> None:
        """复杂嵌套数据的编解码。"""
        msg = {
            "type": "event",
            "event_type": "entity_born",
            "payload": {
                "id": "npc_001",
                "position": [10.5, 20.3],
                "stats": {"hp": 100, "mp": 50},
                "tags": ["friendly", "trader"],
                "active": True,
                "target": None,
            },
        }
        encoded = encode_message(msg)
        decoded = decode_message(encoded[5:], encoded[0])
        assert decoded == msg

    def test_encode_unsupported_version_rejected(self) -> None:
        """不支持的编码版本应抛 ValueError。"""
        with pytest.raises(ValueError):
            encode_message({"a": 1}, version=0x99)

    def test_decode_unsupported_version_rejected(self) -> None:
        """不支持的解码版本应抛 ProtocolError。"""
        with pytest.raises(ProtocolError):
            decode_message(b"{}", version=0x99)

    def test_read_frame_complete(self) -> None:
        """完整帧读取。"""
        msg = {"type": "event", "event_type": "test", "payload": {}}
        buf = bytearray(encode_message(msg))
        result = read_frame(buf)
        assert result == msg
        assert len(buf) == 0

    def test_read_frame_partial_length(self) -> None:
        """长度前缀不完整时返回 None。"""
        buf = bytearray(b"\x01\x00\x00")
        result = read_frame(buf)
        assert result is None
        assert len(buf) == 3  # 缓冲保留

    def test_read_frame_partial_body(self) -> None:
        """消息体不完整时返回 None。"""
        msg = {"type": "event", "event_type": "test", "payload": {}}
        full = encode_message(msg)
        buf = bytearray(full[: len(full) // 2])
        result = read_frame(buf)
        assert result is None
        assert len(buf) == len(full) // 2  # 缓冲保留

    def test_read_frame_unsupported_version(self) -> None:
        """未知版本字节应抛 ProtocolError。"""
        buf = bytearray(b"\x02\x00\x00\x00\x02{}")
        with pytest.raises(ProtocolError):
            read_frame(buf)

    def test_frame_format_golden_bytes(self) -> None:
        """帧格式黄金字节 — 与前端 frame_codec.gd 的契约锁定。

        前端实现：1 字节协议版本 + 4 字节大端长度前缀 + UTF-8 JSON 体
        （见 frontend/scripts/utils/frame_codec.gd）。此测试锁定后端
        产出的逐字节格式，任何一侧修改帧格式（如迁移 MessagePack 注册
        新版本号）都必须同时修改两侧并更新本测试。
        """
        msg = {"a": 1, "b": "中文"}
        encoded = encode_message(msg)
        version, length = struct.unpack(">BI", encoded[:5])
        assert version == PROTOCOL_VERSION
        assert length == len(encoded) - 5
        body = encoded[5:]
        assert body == json.dumps(msg, ensure_ascii=False).encode("utf-8")
        # 非 JSON 可序列化值必须显式报错，不得静默降级（default=str 已移除）
        with pytest.raises(TypeError):
            encode_message({"bad": {1, 2, 3}})


class TestServer:
    """服务器功能测试。"""

    def test_server_start_stop(self, server) -> None:
        """服务器启动和停止。"""
        assert server.is_running
        assert server.client_count == 0
        assert len(server.token) == 32, "启动时应生成认证令牌"
        server.stop()
        assert not server.is_running

    def test_client_connection(self, server, client_socket) -> None:
        """客户端连接后 server 可见。"""
        time.sleep(0.2)  # 等待 accept 线程处理
        assert server.client_count == 1

    def test_client_disconnect(self, server, client_socket) -> None:
        """客户端断开后 server 更新计数。"""
        time.sleep(0.2)
        assert server.client_count == 1
        client_socket.close()
        time.sleep(0.3)
        assert server.client_count == 0


class TestHandshake:
    """握手认证测试。"""

    def test_hello_ok(self, server, client_socket) -> None:
        """正确 token + 版本 → hello_ack。"""
        resp = handshake(client_socket, server)
        assert resp is not None
        assert resp["type"] == "hello_ack"

    def test_hello_wrong_token_disconnected(self, server, client_socket) -> None:
        """错误 token → 服务器断开连接。"""
        send_frame(client_socket, {
            "type": "hello",
            "seq": 1,
            "payload": {"token": "wrong_token", "protocol_version": PROTOCOL_VERSION},
        })
        time.sleep(0.3)
        assert server.client_count == 0

    def test_hello_wrong_version_rejected(self, server, client_socket) -> None:
        """版本不兼容 → error(hello) 响应后断开。"""
        resp = handshake(client_socket, server, protocol_version=0x99)
        assert resp is not None
        assert resp["type"] == "error"
        assert resp["request_type"] == "hello"
        time.sleep(0.3)
        assert server.client_count == 0

    def test_message_before_hello_disconnects(self, server, client_socket) -> None:
        """未认证直接发普通消息 → 服务器断开。"""
        send_frame(client_socket, {"type": "request", "request_type": "ping", "seq": 1, "payload": {}})
        time.sleep(0.3)
        assert server.client_count == 0

    def test_unsupported_version_byte_disconnects(self, server, client_socket) -> None:
        """未知版本字节 → 协议错误断开。"""
        send_raw(client_socket, b"\x02\x00\x00\x00\x02{}")
        time.sleep(0.3)
        assert server.client_count == 0

    def test_hello_ack_before_events(self, server, world_tree, client_socket) -> None:
        """握手成功后事件才能到达（未握手客户端收不到广播）。"""
        bridge = EventBridge(world_tree, server)
        bridge.install()
        time.sleep(0.2)

        event = Event(
            timestamp=100,
            location=(0, 0, None, None),
            initiator_type="system",
            initiator_id="sys",
            affected=[],
            event_type="weather_change",
            data={},
        )
        world_tree.publish(event)
        # 未握手：不应收到任何帧
        assert recv_frame(client_socket, timeout=0.5) is None

        resp = handshake(client_socket, server)
        assert resp is not None and resp["type"] == "hello_ack"
        world_tree.publish(event)
        received = recv_frame(client_socket, timeout=2.0)
        assert received is not None
        assert received["event_type"] == "weather_change"


class TestSlowConsumer:
    """慢客户端（不消费数据）不应阻塞游戏线程，超限应被断开。"""

    def test_send_queue_overflow_disconnects(self, server, client_socket) -> None:
        """广播量超过发送队列上限 → 客户端被断开（游戏线程不被阻塞）。"""
        resp = handshake(client_socket, server)
        assert resp is not None and resp["type"] == "hello_ack"
        time.sleep(0.1)

        # 客户端不 recv，TCP 缓冲很快填满 → 发送队列积压 → 超限断开
        for i in range(SEND_QUEUE_LIMIT + 64):
            server.broadcast({"type": "event", "event_type": "flood", "payload": {"i": i}})

        deadline = time.monotonic() + 5.0
        while server.client_count > 0 and time.monotonic() < deadline:
            time.sleep(0.05)
        assert server.client_count == 0, "慢客户端应被断开而非无限积压"


class TestEventForwarding:
    """WorldTree 事件转发测试。"""

    def test_event_broadcast(self, server, world_tree, client_socket) -> None:
        """发布事件后客户端收到转发。"""
        bridge = EventBridge(world_tree, server)
        bridge.install()

        time.sleep(0.2)  # 等待连接就绪
        assert server.client_count == 1
        resp = handshake(client_socket, server)
        assert resp is not None and resp["type"] == "hello_ack"

        # 发布事件
        event = Event(
            timestamp=100,
            location=(0, 0, None, None),
            initiator_type="system",
            initiator_id="test_system",
            affected=[],
            event_type="weather_change",
            data={"weather": "rain"},
        )
        world_tree.publish(event)

        # 客户端应收到转发
        received = recv_frame(client_socket, timeout=2.0)
        assert received is not None
        assert received["type"] == "event"
        assert received["event_type"] == "weather_change"
        assert received["payload"]["initiator_id"] == "test_system"
        assert received["payload"]["data"]["weather"] == "rain"

    def test_multiple_events(self, server, world_tree, client_socket) -> None:
        """连续发布多个事件，按序接收。"""
        bridge = EventBridge(world_tree, server)
        bridge.install()
        time.sleep(0.2)
        resp = handshake(client_socket, server)
        assert resp is not None and resp["type"] == "hello_ack"

        for i in range(3):
            event = Event(
                timestamp=i * 10,
                location=(i, 0, None, None),
                initiator_type="system",
                initiator_id=f"sys_{i}",
                affected=[],
                event_type="test_event",
                data={"index": i},
            )
            world_tree.publish(event)

        received_count = 0
        for _ in range(3):
            msg = recv_frame(client_socket, timeout=2.0)
            if msg and msg.get("event_type") == "test_event":
                received_count += 1
        assert received_count == 3


class TestClientToServer:
    """客户端→服务器方向消息测试。"""

    def test_receive_client_message(self, server, client_socket) -> None:
        """客户端发送消息，服务器 receive_all() 可收到（含 client_id）。"""
        time.sleep(0.2)
        resp = handshake(client_socket, server)
        assert resp is not None and resp["type"] == "hello_ack"

        msg = {"type": "request", "request_type": "ping", "seq": 1, "payload": {}}
        send_frame(client_socket, msg)

        time.sleep(0.2)
        messages = server.receive_all()
        assert len(messages) == 1
        client_id, received = messages[0]
        assert received["type"] == "request"
        assert received["request_type"] == "ping"
        assert client_id == 0, "首个连接的客户端 ID 应为 0"

    def test_multiple_client_messages(self, server, client_socket) -> None:
        """多条消息一次性拉取。"""
        time.sleep(0.2)
        resp = handshake(client_socket, server)
        assert resp is not None and resp["type"] == "hello_ack"

        for i in range(5):
            send_frame(client_socket, {
                "type": "request",
                "request_type": f"move_{i}",
                "seq": i,
                "payload": {},
            })

        time.sleep(0.3)
        messages = server.receive_all()
        assert len(messages) == 5


# ── 直接运行 ─────────────────────────────────────────────────────────

if __name__ == "__main__":
    setup_logging()
    print("=== 协议测试 ===")
    TestProtocol().test_encode_decode_simple()
    TestProtocol().test_encode_decode_unicode()
    TestProtocol().test_encode_decode_complex()
    TestProtocol().test_read_frame_complete()
    TestProtocol().test_read_frame_partial_length()
    TestProtocol().test_read_frame_partial_body()
    print("协议测试通过")

    print("\n=== 服务器测试 ===")
    srv = GameServer(port=TEST_PORT)
    srv.start()
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.connect(("127.0.0.1", TEST_PORT))
    time.sleep(0.2)
    assert srv.client_count == 1
    print(f"客户端数: {srv.client_count}")

    print("\n=== 事件转发测试 ===")
    wt = WorldTree()
    bridge = EventBridge(wt, srv)
    bridge.install()

    resp = handshake(sock, srv)
    assert resp is not None and resp["type"] == "hello_ack"
    print("握手成功")

    event = Event(
        timestamp=0,
        location=(0, 0, None, None),
        initiator_type="system",
        initiator_id="test",
        affected=[],
        event_type="test",
        data={},
    )
    wt.publish(event)

    received = recv_frame(sock, timeout=2.0)
    assert received is not None
    print(f"收到事件: {received['event_type']}")

    print("\n=== 客户端→服务器测试 ===")
    send_frame(sock, {"type": "request", "request_type": "ping", "seq": 1, "payload": {}})
    time.sleep(0.2)
    msgs = srv.receive_all()
    assert len(msgs) == 1
    print(f"收到消息: {msgs[0]['request_type']}")

    sock.close()
    srv.stop()
    print("\n全部测试通过!")
