"""实体生命周期事件契约 — 实体生灭/移动发布的 data 结构。

data 键即 dataclass 字段，event_type 由类属性声明。
位置类字段以 tuple 声明，as_dict() 输出 JSON 安全的 list。

玩家事件契约（player_teleported）在游戏侧 ``miskhak.entity.events``。
"""

from dataclasses import dataclass
from typing import ClassVar

from miskhak.events.event import WorldEvent


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
    """实体在虚拟世界消亡（死亡/被摧毁/被拾取等）。"""

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
