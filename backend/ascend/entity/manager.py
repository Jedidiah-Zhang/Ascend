"""实体管理器 — 实体在虚拟世界的生灭、查询和移动，发布生命周期事件。

语义约定（Issue #20）:
  - birth/death: 实体在虚拟世界的生灭 —— 世界内因果事件，走世界树
    （entity_born / entity_died），是 NPC 未来可感知的历史事实。
  - spawn/despawn: 实体进入/离开前端渲染视野 —— 表现层概念，不在
    本模块职责内。现阶段视野=全部（前端收 born 即渲染、died 即移除）。
  - 停服等世界外操作不得调用 death()——那会向因果历史写入虚假死亡。
"""

from ascend.world_tree import world_tree, Event, AffectedParty
from ascend.world_tree.event import (
    SUB_CELL_SIZE, SUB_CELLS, spatial_key, sub_cell_range,
)
from ascend.log import get_logger
from .entity import Entity, EntityType, Controller

logger = get_logger(__name__)

world_tree.register_event_schema(
    "entity_born",
    required={"entity_id": str, "entity_type": str, "position": tuple},
    description="实体在虚拟世界诞生时发布",
)
world_tree.register_event_schema(
    "entity_died",
    required={"entity_id": str, "entity_type": str},
    description="实体在虚拟世界死亡/消亡时发布",
)
world_tree.register_event_schema(
    "entity_moved",
    required={"entity_id": str, "old_position": tuple, "new_position": tuple},
    description="实体位置变更时发布",
)


class EntityManager:
    """实体管理器。

    追踪所有活跃实体，维护类型和空间索引。
    实体的生灭、移动均发布事件到世界树。

    用法:
        mgr = EntityManager()
        deer = mgr.birth(EntityType.CREATURE, 0, 0, 5, 3)
        mgr.move(deer.id, 0, 0, 6, 3)
        mgr.death(deer.id)
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
        self._spatial_index: dict[tuple[int, int, int, int, int], set[str]] = {}

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

    # ── 生灭 ──────────────────────────────────────────────

    def birth(
        self,
        entity_type: EntityType,
        chunk_x: int,
        chunk_y: int,
        tile_x: int | None = None,
        tile_y: int | None = None,
        *,
        layer_id: int = 0,
        controller: Controller = Controller.NONE,
        data: dict | None = None,
        game_time: int = 0,
    ) -> Entity:
        """实体在虚拟世界诞生：创建并注册，发布 entity_born 事件。

        Args:
            entity_type: 存在形态。
            chunk_x, chunk_y: 所在 chunk 坐标。
            tile_x, tile_y: chunk 内 tile 坐标，可为 None。
            layer_id: 所在 Z 层（0=地表，负数=地下层），默认 0。
            controller: 决策控制者，默认 NONE（被动实体）。
            data: 附加数据。
            game_time: 当前游戏时间（tick 数）。

        Returns:
            诞生的实体。
        """
        entity = Entity(
            entity_type=entity_type,
            chunk_x=chunk_x,
            chunk_y=chunk_y,
            tile_x=tile_x,
            tile_y=tile_y,
            born_at=game_time,
            layer_id=layer_id,
            controller=controller,
            data=data,
        )
        self._entities[entity.id] = entity
        self._type_index.setdefault(entity_type, set()).add(entity.id)
        self._spatial_index.setdefault(
            spatial_key(
                entity.layer_id, entity.chunk_x, entity.chunk_y,
                entity.tile_x, entity.tile_y,
            ), set(),
        ).add(entity.id)

        gx, gy = entity.global_xy
        self._world_tree.publish(Event(
            timestamp=game_time,
            location=entity.position,
            layer_id=entity.layer_id,
            initiator_type="system",
            initiator_id="entity_manager",
            affected=[AffectedParty(entity.id, "subject")],
            event_type="entity_born",
            data={
                "entity_id": entity.id,
                "entity_type": entity_type.name,
                "controller": entity.controller.name,
                "position": entity.position,
                "layer_id": entity.layer_id,
                "x": gx,
                "y": gy,
            },
        ))
        logger.debug(
            "birth: %s type=%s controller=%s at chunk %s",
            entity.id, entity_type.name, entity.controller.name, entity.chunk,
        )
        return entity

    def death(self, entity_id: str, *, game_time: int = 0) -> Entity | None:
        """实体在虚拟世界死亡/消亡：移除并发布 entity_died 事件。

        仅用于世界内的生灭（死亡、被摧毁、被拾取等）。停服清理等
        世界外操作直接丢弃管理器即可，不得借用本方法伪造历史。

        Args:
            entity_id: 要移除的实体 ID。
            game_time: 当前游戏时间。

        Returns:
            被移除的实体，若不存在则返回 None。
        """
        entity = self._entities.pop(entity_id, None)
        if entity is None:
            logger.warning("death: 实体 %s 不存在", entity_id)
            return None

        self._discard_from_index(self._type_index, entity.entity_type, entity_id)
        self._discard_from_index(
            self._spatial_index,
            spatial_key(
                entity.layer_id, entity.chunk_x, entity.chunk_y,
                entity.tile_x, entity.tile_y,
            ),
            entity_id,
        )

        self._world_tree.publish(Event(
            timestamp=game_time,
            location=entity.position,
            layer_id=entity.layer_id,
            initiator_type="system",
            initiator_id="entity_manager",
            affected=[AffectedParty(entity_id, "subject")],
            event_type="entity_died",
            data={
                "entity_id": entity_id,
                "entity_type": entity.entity_type.name,
            },
        ))
        logger.debug("death: %s type=%s", entity_id, entity.entity_type.name)
        return entity

    def move(
        self,
        entity_id: str,
        chunk_x: int,
        chunk_y: int,
        tile_x: int | None = None,
        tile_y: int | None = None,
        *,
        game_time: int = 0,
    ) -> bool:
        """移动实体到新位置（同层内），发布 entity_moved 事件。

        跨层移动不在本方法职责内——跨层是离散动作（进入洞穴/出洞穴），
        应通过专用 transition API 实现，避免误操作。

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

        old_pos = entity.position
        old_key = spatial_key(
            entity.layer_id, entity.chunk_x, entity.chunk_y,
            old_pos[2], old_pos[3],
        )
        entity.chunk_x = chunk_x
        entity.chunk_y = chunk_y
        entity.tile_x = tile_x
        entity.tile_y = tile_y
        new_key = spatial_key(
            entity.layer_id, entity.chunk_x, entity.chunk_y,
            entity.tile_x, entity.tile_y,
        )

        if old_key != new_key:
            self._discard_from_index(self._spatial_index, old_key, entity_id)
            self._spatial_index.setdefault(new_key, set()).add(entity_id)

        gx, gy = entity.global_xy
        self._world_tree.publish(Event(
            timestamp=game_time,
            location=entity.position,
            layer_id=entity.layer_id,
            initiator_type="system",
            initiator_id="entity_manager",
            affected=[AffectedParty(entity_id, "subject")],
            event_type="entity_moved",
            data={
                "entity_id": entity_id,
                "old_position": old_pos,
                "new_position": entity.position,
                "layer_id": entity.layer_id,
                "x": gx,
                "y": gy,
            },
        ))
        logger.debug("move: %s %s → %s", entity_id, old_pos, entity.position)
        return True

    @staticmethod
    def _discard_from_index(index: dict, key, entity_id: str) -> None:
        """从索引桶移除实体 ID，桶空时删除键。

        空间索引键空间无上限（层 × chunk × sub-cell），残留空集
        在高周转下是无界内存泄漏，必须随手清理。

        Args:
            index: 目标索引字典（值为 set）。
            key: 索引键。
            entity_id: 要移除的实体 ID。
        """
        bucket = index.get(key)
        if bucket is None:
            return
        bucket.discard(entity_id)
        if not bucket:
            del index[key]

    # ── 查询 ──────────────────────────────────────────────

    def get(self, entity_id: str) -> Entity | None:
        """按 ID 查询实体。

        Args:
            entity_id: 实体 ID。

        Returns:
            实体或 None。
        """
        return self._entities.get(entity_id)

    def all_entities(self) -> list[Entity]:
        """返回所有活跃实体（快照接口等状态通道消费）。

        Returns:
            实体列表（浅拷贝，调用方可安全迭代）。
        """
        return list(self._entities.values())

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
        *,
        layer_id: int = 0,
        center_tile: tuple[int, int] | None = None,
        sub_radius: int = 0,
    ) -> list[Entity]:
        """按空间区域查询实体（限定单层）。

        chunk 级粗筛 + 可选的 sub-cell 精筛：
        - center_chunk + radius 划定 chunk 范围。
        - center_tile + sub_radius 进一步限定中心 chunk 内的 sub-cell 范围。
          周边 chunk 不受 sub-cell 限制（返回全部 sub-cell 内的实体）。

        Args:
            center_chunk: 中心 chunk 坐标 (chunk_x, chunk_y)。
            radius: 搜索半径（chunk 数），默认 1。
            layer_id: 只查询该层的实体，默认 0（地表）。
            center_tile: 可选，中心 tile 坐标 (tile_x, tile_y)，
                传入后中心 chunk 仅返回 sub_radius 范围内的 sub-cell。
            sub_radius: sub-cell 搜索半径，默认 0 即仅匹配自身 sub-cell。

        Returns:
            该层区域内的实体列表。
        """
        cx, cy = center_chunk
        results: list[Entity] = []
        seen: set[str] = set()

        if center_tile is not None:
            (scx_lo, scx_hi), (scy_lo, scy_hi) = sub_cell_range(
                center_tile[0], center_tile[1], sub_radius,
            )
            has_tile_filter = True
        else:
            has_tile_filter = False

        for dx in range(-radius, radius + 1):
            for dy in range(-radius, radius + 1):
                if dx == 0 and dy == 0 and has_tile_filter:
                    scx_iter = range(scx_lo, scx_hi + 1)
                    scy_iter = range(scy_lo, scy_hi + 1)
                else:
                    scx_iter = range(SUB_CELLS)
                    scy_iter = range(SUB_CELLS)
                for scx in scx_iter:
                    for scy in scy_iter:
                        key = (layer_id, cx + dx, cy + dy, scx, scy)
                        for eid in self._spatial_index.get(key, ()):
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
