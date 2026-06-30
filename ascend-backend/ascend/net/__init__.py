"""网络通信层 — Python 后端与 Godot 前端的 TCP 桥接。

通过 TCP localhost + JSON 传输游戏事件和玩家指令。
"""

from ascend.net.server import GameServer
from ascend.net.bridge import EventBridge
from ascend.net.protocol import encode_message, decode_message, read_frame, ProtocolError

__all__ = [
    "GameServer",
    "EventBridge",
    "encode_message",
    "decode_message",
    "read_frame",
    "ProtocolError",
]
