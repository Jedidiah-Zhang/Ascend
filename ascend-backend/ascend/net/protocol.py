"""消息协议 — 帧格式与序列化。

帧格式: 4 字节大端无符号整数长度前缀 + JSON 体。
与 Godot 侧 scripts/autoload/connection.gd 保持一致。
"""

import json
import struct


MAX_MESSAGE_SIZE: int = 16 * 1024 * 1024  # 16 MiB


class ProtocolError(Exception):
    """协议错误（帧长度无效、JSON 解析失败等）。"""


def encode_message(message: dict) -> bytes:
    """将字典编码为带长度前缀的字节串。

    Args:
        message: 消息字典，值必须 JSON 可序列化。

    Returns:
        长度前缀 (4B) + UTF-8 JSON 体。
    """
    body = json.dumps(message, ensure_ascii=False, default=str).encode("utf-8")
    length = len(body)
    return struct.pack(">I", length) + body


def decode_message(data: bytes) -> dict:
    """从 JSON 字节串解码为字典。

    Args:
        data: UTF-8 编码的 JSON 字节串（不含长度前缀）。

    Returns:
        消息字典。

    Raises:
        ProtocolError: JSON 解析失败。
    """
    try:
        return json.loads(data.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise ProtocolError(f"JSON 解码失败: {exc}") from exc


def read_frame(buffer: bytearray) -> dict | None:
    """从缓冲区读取一帧。消费已解析的数据，保留未完成帧。

    Args:
        buffer: 接收缓冲区（会被修改：移除已消费的字节）。

    Returns:
        完整消息字典，或数据不足时返回 None。

    Raises:
        ProtocolError: 帧长度无效或超出限制。
    """
    if len(buffer) < 4:
        return None
    length: int = struct.unpack(">I", buffer[:4])[0]
    if length <= 0:
        raise ProtocolError(f"无效的消息长度: {length}")
    if length > MAX_MESSAGE_SIZE:
        raise ProtocolError(f"消息长度超出限制: {length} > {MAX_MESSAGE_SIZE}")
    if len(buffer) < 4 + length:
        return None
    body = bytes(buffer[4 : 4 + length])
    del buffer[: 4 + length]
    return decode_message(body)
