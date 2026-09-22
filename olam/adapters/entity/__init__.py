"""实体运行时适配 — 实体数据形态与生命周期管理。

实体是世界的存在物（生物/植物/物品/建筑），本包是运行适配层：

- ``entity``：Entity / EntityType / Controller / split_coords（数据形态）；
- ``manager``：EntityManager（三索引注册表 + 生灭/移动 API，变更经世界树
  发布 entity_born / entity_died / entity_moved 因果事件）；
- ``events``：实体生命周期事件契约。

玩家服务（PlayerService，玩家输入权威）在游戏侧 ``miskhak.entity``；
实体声明化（实例/槽位/机制）规划进 ``olam/modules``。
"""

from .entity import Entity, EntityType, Controller, split_coords
from .manager import EntityManager

__all__ = [
    "Entity", "EntityType", "Controller", "EntityManager", "split_coords",
]
