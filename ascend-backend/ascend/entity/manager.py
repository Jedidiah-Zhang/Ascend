"""实体管理器 — 实体的创建、销毁、查询和移动，发布生命周期事件。"""

from ascend.world_tree import world_tree, Event, AffectedParty
from ascend.log import get_logger
from .entity import Entity, EntityType

logger = get_logger(__name__)

world_tree.register_event_schema(
    "entity_spawned",
    required={"entity_id": str, "entity_type": str, "position": tuple},
    description="新实体生成时发布",
)
world_tree.register_event_schema(
    "entity_despawned",
    required={"entity_id": str, "entity_type": str},
    description="实体销毁时发布",
)
world_tree.register_event_schema(
    "entity_moved",
    required={"entity_id": str, "old_position": tuple, "new_position": tuple},
    description="实体位置变更时发布",
)


class EntityManager:
    """实体管理器。

    追踪所有活跃实体，维护类型和空间索引。
    实体的创建、销毁、移动均发布事件到总线。

    用法:
        mgr = EntityManager()
        npc = mgr.spawn(EntityType.NPC, 0, 0, 5, 3)
        mgr.move(npc.id, 0, 0, 6, 3)
        mgr.despawn(npc.id)
    """

    def __init__(self, world_tree_arg=None) -> None:
        """初始化空的实体管理器。

        Args:
            world_tree_arg: 世界树实例，默认使用模块级单例。
        """
        self._world_tree = (
            world_tree_arg if world_tree_arg is not None else world_tree
        )
        self._entities: dict[str, Entity] = {}
        self._type_index: dict[int, set[str]] = {}
        self._spatial_index: dict[tuple[int, int], set[str]] = {}

    def __repr__(self) -> str:
        """返回管理器状态摘要。

        Returns:
            含实体总数和类型分布的 repr 字符串。
        """
        names = {v: k for k, v in EntityType.__members__.items()}
        return (
            f"EntityManager(total={len(self._entities)}, "
            f"types={dict((names.get(k, str(k)), len(v)) for k, v in self._type_index.items())})"
        )

    # ── 生命周期 ──────────────────────────────────────────

    def spawn(
        self,
        entity_type: EntityType,
        chunk_x: int,
        chunk_y: int,
        tile_x: int | None = None,
        tile_y: int | None = None,
        *,
        data: dict | None = None,
        game_time: float = 0.0,
    ) -> Entity:
        """创建并注册一个实体，发布 entity_spawned 事件。

        Args:
            entity_type: 实体类型。
            chunk_x, chunk_y: 所在 chunk 坐标。
            tile_x, tile_y: chunk 内 tile 坐标，可为 None。
            data: 附加数据。
            game_time: 当前游戏时间。

        Returns:
            创建的实体。
        """
        entity = Entity(
            entity_type=entity_type,
            chunk_x=chunk_x,
            chunk_y=chunk_y,
            tile_x=tile_x,
            tile_y=tile_y,
            spawned_at=game_time,
            data=data,
        )
        self._entities[entity.id] = entity
        self._type_index.setdefault(entity_type, set()).add(entity.id)
        self._spatial_index.setdefault(entity.chunk, set()).add(entity.id)

        self._world_tree.publish(Event(
            timestamp=game_time,
            location=entity.position,
            initiator_type="system",
            initiator_id="entity_manager",
            affected=[AffectedParty(entity.id, "subject")],
            event_type="entity_spawned",
            data={
                "entity_id": entity.id,
                "entity_type": entity_type.name,
                "position": entity.position,
            },
        ))
        logger.debug("spawn: %s type=%s at chunk %s", entity.id, entity_type.name, entity.chunk)
        return entity

    def despawn(self, entity_id: str, *, game_time: float = 0.0) -> Entity | None:
        """移除实体，发布 entity_despawned 事件。

        Args:
            entity_id: 要移除的实体 ID。
            game_time: 当前游戏时间。

        Returns:
            被移除的实体，若不存在则返回 None。
        """
        entity = self._entities.pop(entity_id, None)
        if entity is None:
            logger.warning("despawn: 实体 %s 不存在", entity_id)
            return None

        self._type_index.get(entity.entity_type, set()).discard(entity_id)
        self._spatial_index.get(entity.chunk, set()).discard(entity_id)

        self._world_tree.publish(Event(
            timestamp=game_time,
            location=entity.position,
            initiator_type="system",
            initiator_id="entity_manager",
            affected=[AffectedParty(entity_id, "subject")],
            event_type="entity_despawned",
            data={
                "entity_id": entity_id,
                "entity_type": entity.entity_type.name,
            },
        ))
        logger.debug("despawn: %s type=%s", entity_id, entity.entity_type.name)
        return entity

    def move(
        self,
        entity_id: str,
        chunk_x: int,
        chunk_y: int,
        tile_x: int | None = None,
        tile_y: int | None = None,
        *,
        game_time: float = 0.0,
    ) -> bool:
        """移动实体到新位置，发布 entity_moved 事件。

        Args:
            entity_id: 实体 ID。
            chunk_x, chunk_y: 新 chunk 坐标。
            tile_x, tile_y: 新 tile 坐标，可为 None。
            game_time: 当前游戏时间。

        Returns:
            True 表示移动成功，False 表示实体不存在。
        """
        entity = self._entities.get(entity_id)
        if entity is None:
            logger.warning("move: 实体 %s 不存在", entity_id)
            return False

        old_chunk = entity.chunk
        old_pos = entity.position
        entity.chunk_x = chunk_x
        entity.chunk_y = chunk_y
        entity.tile_x = tile_x
        entity.tile_y = tile_y
        new_chunk = entity.chunk

        if old_chunk != new_chunk:
            self._spatial_index.get(old_chunk, set()).discard(entity_id)
            self._spatial_index.setdefault(new_chunk, set()).add(entity_id)

        self._world_tree.publish(Event(
            timestamp=game_time,
            location=entity.position,
            initiator_type="system",
            initiator_id="entity_manager",
            affected=[AffectedParty(entity_id, "subject")],
            event_type="entity_moved",
            data={
                "entity_id": entity_id,
                "old_position": old_pos,
                "new_position": entity.position,
            },
        ))
        logger.debug("move: %s %s → %s", entity_id, old_pos, entity.position)
        return True

    # ── 查询 ──────────────────────────────────────────────

    def get(self, entity_id: str) -> Entity | None:
        """按 ID 查询实体。

        Args:
            entity_id: 实体 ID。

        Returns:
            实体或 None。
        """
        return self._entities.get(entity_id)

    def by_type(self, entity_type: EntityType) -> list[Entity]:
        """按类型查询所有实体。

        Args:
            entity_type: 实体类型。

        Returns:
            该类型的所有实体列表。
        """
        ids = self._type_index.get(entity_type, set())
        return [self._entities[eid] for eid in ids if eid in self._entities]

    def in_region(
        self,
        center_chunk: tuple[int, int],
        radius: int = 1,
    ) -> list[Entity]:
        """按空间区域查询实体。

        Args:
            center_chunk: 中心 chunk 坐标。
            radius: 搜索半径（chunk 数），默认 1。

        Returns:
            区域内的实体列表。
        """
        cx, cy = center_chunk
        results: list[Entity] = []
        seen: set[str] = set()
        for dx in range(-radius, radius + 1):
            for dy in range(-radius, radius + 1):
                for eid in self._spatial_index.get((cx + dx, cy + dy), set()):
                    if eid not in seen:
                        seen.add(eid)
                        ent = self._entities.get(eid)
                        if ent:
                            results.append(ent)
        return results

    # ── 元信息 ────────────────────────────────────────────

    @property
    def count(self) -> int:
        """活跃实体总数。

        Returns:
            实体总数。
        """
        return len(self._entities)

    def type_counts(self) -> dict[str, int]:
        """各类型实体数量统计。

        Returns:
            类型名 → 数量的字典。
        """
        return {
            EntityType(k).name: len(v) for k, v in self._type_index.items()
        }
