"""玩家事件契约 — 玩家传送发布的 data 结构。

实体生命周期事件契约（entity_born / entity_died / entity_moved）在
``olam.adapters.entity.events``；本文件只保留玩家侧事件。

data 键即 dataclass 字段，event_type 由类属性声明。
位置类字段以 tuple 声明，as_dict() 输出 JSON 安全的 list。
"""

from dataclasses import dataclass
from typing import ClassVar

from miskhak.events.event import WorldEvent


@dataclass
class PlayerTeleported(WorldEvent):
    """玩家被强制传送（终端 tp 指令等），前端据此吸附位置。"""

    event_type: ClassVar[str] = "player_teleported"
    x: float
    y: float
