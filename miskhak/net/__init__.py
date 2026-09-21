"""网络通信层 — Python 后端与 Godot 前端的 TCP 桥接。

通过 TCP localhost + JSON 传输游戏事件和玩家指令。
"""

from miskhak.net.server import GameServer
from miskhak.net.bridge import EventBridge
from miskhak.net.dispatcher import MessageDispatcher
from miskhak.net.protocol import encode_message, decode_message, read_frame, ProtocolError, PROTOCOL_VERSION

__all__ = [
    "GameServer",
    "EventBridge",
    "MessageDispatcher",
    "encode_message",
    "decode_message",
    "read_frame",
    "ProtocolError",
]
