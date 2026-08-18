"""协议层单元测试 — 帧编解码与格式契约（纯逻辑，不依赖真实 server）。

协议回归由日常 testmon 覆盖；server 级测试保留在集成目录（必须串行）。
"""

import json
import struct

import pytest

from ascend.net import (
    encode_message,
    decode_message,
    read_frame,
    ProtocolError,
    PROTOCOL_VERSION,
)


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