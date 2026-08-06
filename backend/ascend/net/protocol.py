"""消息协议 — 帧格式与序列化。

帧格式: 1 字节协议版本 + 4 字节大端长度前缀 + 体。
当前版本 0x01 = JSON；未来新增编码（如 MessagePack）注册新版本号，
解码按版本分发，前后端可渐进迁移。
与 Godot 侧 scripts/autoload/connection.gd 保持一致。
"""

import json
import struct

from ascend.config import MAX_MESSAGE_SIZE

PROTOCOL_VERSION: int = 0x01  # 当前协议版本（JSON 编码）


class ProtocolError(Exception):
    """协议错误（帧长度无效、版本不支持、JSON 解析失败等）。"""


def make_response(request_type: str, payload: dict) -> dict:
    """构造标准响应信封（全网络层单一构造点）。

    Args:
        request_type: 对应请求类型（响应回显）。
        payload: 响应载荷。

    Returns:
        响应字典。
    """
    return {"type": "response", "request_type": request_type, "payload": payload}


def make_error(request_type: str, error: str, seq: int = 0) -> dict:
    """构造错误响应信封。

    Args:
        request_type: 请求类型（"" = 未知）。
        error: 错误信息。
        seq: 请求序号（回显）。

    Returns:
        错误响应字典。
    """
    return {"type": "error", "request_type": request_type, "seq": seq, "error": error}


def encode_message(message: dict, version: int = PROTOCOL_VERSION) -> bytes:
    """将字典编码为带版本与长度前缀的字节串。

    Args:
        message: 消息字典，值必须 JSON 可序列化。
        version: 协议版本（编码方式），当前仅支持 JSON（0x01）。

    Returns:
        版本字节 (1B) + 长度前缀 (4B) + JSON 体。

    Raises:
        ValueError: version 不是受支持的协议版本。
    """
    if version != PROTOCOL_VERSION:
        raise ValueError(f"不支持的协议版本: {version:#04x}")
    # allow_nan=False：NaN/Infinity 虽能被 json 编码，但非标准 JSON，
    # 前端解码器与其它语言实现可能解析失败——异常比静默产生坏数据好
    body = json.dumps(message, ensure_ascii=False, allow_nan=False).encode("utf-8")
    length = len(body)
    return struct.pack(">BI", version, length) + body


def decode_message(data: bytes, version: int = PROTOCOL_VERSION) -> dict:
    """从字节串解码为字典（按协议版本分发编码方式）。

    Args:
        data: 编码后的字节串（不含版本与长度前缀）。
        version: 协议版本。

    Returns:
        消息字典。

    Raises:
        ProtocolError: 版本不支持或解析失败。
    """
    if version != PROTOCOL_VERSION:
        raise ProtocolError(f"不支持的协议版本: {version:#04x}")
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
        ProtocolError: 版本不支持、帧长度无效或超出限制。
    """
    if len(buffer) < 5:
        return None
    version: int = buffer[0]
    length: int = struct.unpack(">I", buffer[1:5])[0]
    if length <= 0:
        raise ProtocolError(f"无效的消息长度: {length}")
    if length > MAX_MESSAGE_SIZE:
        raise ProtocolError(f"消息长度超出限制: {length} > {MAX_MESSAGE_SIZE}")
    if len(buffer) < 5 + length:
        return None
    body = bytes(buffer[5 : 5 + length])
    result = decode_message(body, version)
    del buffer[: 5 + length]
    return result
