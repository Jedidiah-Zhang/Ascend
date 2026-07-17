"""实体系统 — 游戏中一切存在物的基类和生命周期管理。

用法:
    from ascend.entity import Entity, EntityType, EntityManager, PlayerService

    mgr = EntityManager()
    npc = mgr.spawn(EntityType.NPC, 0, 0, 5, 3)
"""

from .entity import Entity, EntityType
from .manager import EntityManager
from .player import PlayerService

__all__ = ["Entity", "EntityType", "EntityManager", "PlayerService"]
