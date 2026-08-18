"""实体事件契约 — 实体生灭/移动/传送发布的 data 结构。

data 键即 dataclass 字段，event_type 由类属性声明（不再重复写字符串）。
位置类字段以 tuple 声明，as_dict() 输出 JSON 安全的 list。
"""

from dataclasses import dataclass
from typing import ClassVar

from ascend.world_tree.event import WorldEvent


@dataclass
class EntityBorn(WorldEvent):
    """实体在虚拟世界诞生。position 为 (chunk_x, chunk_y, tile_x?, tile_y?)。"""

    event_type: ClassVar[str] = "entity_born"
    entity_id: str
    entity_type: str
    controller: str
    position: tuple
    layer_id: int
    x: int
    y: int


@dataclass
class EntityDied(WorldEvent):
    event_type: ClassVar[str] = "entity_died"
    entity_id: str
    entity_type: str


@dataclass
class EntityMoved(WorldEvent):
    """实体位置变更。old_position/new_position 为 (chunk_x, chunk_y, tile_x?, tile_y?)。"""

    event_type: ClassVar[str] = "entity_moved"
    entity_id: str
    old_position: tuple
    new_position: tuple
    layer_id: int
    x: int
    y: int


@dataclass
class PlayerTeleported(WorldEvent):
    """玩家被强制传送（终端 tp 指令等），前端据此吸附位置。"""

    event_type: ClassVar[str] = "player_teleported"
    x: float
    y: float
