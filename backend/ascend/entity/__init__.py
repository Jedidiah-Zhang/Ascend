"""实体系统 — 游戏中一切存在物的基类和生命周期管理。

用法:
    from ascend.entity import Entity, EntityType, Controller, EntityManager, PlayerService

    mgr = EntityManager()
    deer = mgr.birth(EntityType.CREATURE, 0, 0, 5, 3, controller=Controller.AI)
"""

from .entity import Entity, EntityType, Controller, split_coords
from .manager import EntityManager
from .player import PlayerService

__all__ = [
    "Entity", "EntityType", "Controller", "EntityManager",
    "PlayerService", "split_coords",
]
