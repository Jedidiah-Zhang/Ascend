"""玩家服务 — 后端权威玩家实体（壳子版）。

玩家位置的唯一权威来源。前端本地插值仅作预测显示，
通过 player_move 上报、player_state 查询、player_teleported 事件对齐。

壳子范围：
  - move_to 无条件接受上报位置（未来在此加碰撞/速度/边界校验）
  - 单玩家、单层（layer_id=0）、无持久化

坐标约定：
  - 对外统一使用全局 float tile 坐标 (x, y)，chunk = floor(x / TILE_MAP_SIZE)
  - float 精确值存 entity.data["fx"/"fy"]；Entity 的 chunk/tile 整数字段
    为 floor 派生值，供空间索引与事件位置使用
  - entity_moved 事件仅在跨整数 tile 时发布，避免高频移动刷总线
"""

from ascend.config import TILE_MAP_SIZE
from ascend.log import get_logger
from ascend.time import WorldClock
from ascend.world_tree import world_tree as _default_wt, Event, AffectedParty

from .entity import Entity, EntityType
from .manager import EntityManager

logger = get_logger(__name__)

_default_wt.register_event_schema(
    "player_teleported",
    required={"x": float, "y": float},
    description="玩家被强制传送（终端 tp 指令等）时发布，前端据此吸附位置",
)


class PlayerService:
    """后端权威玩家实体服务。

    Usage:
        svc = PlayerService(entity_manager, clock, birth_chunk=(3, 5))
        svc.spawn()
        svc.move_to(612.4, 1000.8)      # 前端上报（壳子：直接接受）
        svc.teleport(100.0, 200.0)      # 强制传送，发布 player_teleported
        x, y = svc.position
    """

    def __init__(
        self,
        entity_manager: EntityManager,
        clock: WorldClock,
        birth_chunk: tuple[int, int],
        world_tree_arg=None,
    ) -> None:
        """初始化玩家服务。

        Args:
            entity_manager: 实体管理器（spawn/move 经由它发布生命周期事件）。
            clock: 世界时钟（事件时间戳）。
            birth_chunk: 出生 chunk 坐标，spawn 与 teleport_home 的落点。
            world_tree_arg: 可选 WorldTree 实例（测试注入隔离）。
        """
        self._manager = entity_manager
        self._clock = clock
        self._birth_chunk = birth_chunk
        self._wt = world_tree_arg if world_tree_arg is not None else _default_wt
        if world_tree_arg is not None:
            world_tree_arg.register_event_schema(
                "player_teleported",
                required={"x": float, "y": float},
                description="玩家被强制传送时发布",
            )
        self._entity: Entity | None = None

    def __repr__(self) -> str:
        """返回服务状态摘要。"""
        if self._entity is None:
            return "PlayerService(spawned=False)"
        x, y = self.position
        return f"PlayerService(id={self._entity.id[:8]}, pos=({x:.1f},{y:.1f}))"

    # ── 生命周期 ──────────────────────────────────────────

    @property
    def entity(self) -> Entity | None:
        """玩家实体（未 spawn 时为 None）。"""
        return self._entity

    @property
    def birth_position(self) -> tuple[float, float]:
        """出生点全局 tile 坐标（出生 chunk 原点，与前端约定一致）。"""
        bcx, bcy = self._birth_chunk
        return (float(bcx * TILE_MAP_SIZE), float(bcy * TILE_MAP_SIZE))

    def spawn(self) -> Entity:
        """在出生点生成玩家实体。

        幂等：已 spawn 时直接返回既有实体。

        Returns:
            玩家实体。
        """
        if self._entity is not None:
            return self._entity
        x, y = self.birth_position
        cx, cy, tx, ty = self._split_coords(x, y)
        self._entity = self._manager.spawn(
            EntityType.PLAYER, cx, cy, tx, ty,
            data={"fx": x, "fy": y},
            game_time=self._clock.time,
        )
        logger.info("玩家实体已生成: %s at (%.1f, %.1f)", self._entity.id, x, y)
        return self._entity

    # ── 位置 ──────────────────────────────────────────────

    @property
    def position(self) -> tuple[float, float]:
        """玩家当前全局 float tile 坐标。

        Returns:
            (x, y)，未 spawn 时返回出生点坐标。
        """
        if self._entity is None:
            return self.birth_position
        return (
            float(self._entity.get_data("fx", 0.0)),
            float(self._entity.get_data("fy", 0.0)),
        )

    def move_to(self, x: float, y: float) -> tuple[float, float]:
        """权威移动到指定坐标（前端 player_move 上报入口）。

        壳子实现：无条件接受。未来的碰撞/速度/边界校验统一加在此处，
        返回值即为裁决后的权威位置，前端据此纠正本地预测。

        Args:
            x: 目标全局 tile X 坐标。
            y: 目标全局 tile Y 坐标。

        Returns:
            接受后的权威位置 (x, y)。
        """
        return self._apply_position(float(x), float(y))

    def teleport(self, x: float, y: float) -> tuple[float, float]:
        """强制传送到指定坐标并发布 player_teleported 事件。

        与 move_to 的区别：传送是服务端主导的位置变更（终端指令、
        剧情等），需要显式事件通知前端吸附，而非静默接受上报。

        Args:
            x: 目标全局 tile X 坐标。
            y: 目标全局 tile Y 坐标。

        Returns:
            传送后的权威位置 (x, y)。
        """
        pos = self._apply_position(float(x), float(y))
        entity = self._entity
        self._wt.publish(Event(
            timestamp=self._clock.time,
            location=entity.position if entity else (0, 0, None, None),
            initiator_type="system",
            initiator_id="player_service",
            affected=[AffectedParty(entity.id if entity else "player", "subject")],
            event_type="player_teleported",
            data={"x": pos[0], "y": pos[1]},
        ))
        logger.info("玩家传送至 (%.1f, %.1f)", pos[0], pos[1])
        return pos

    def teleport_home(self) -> tuple[float, float]:
        """传送回出生点。

        Returns:
            出生点权威位置 (x, y)。
        """
        return self.teleport(*self.birth_position)

    # ── 内部 ──────────────────────────────────────────────

    @staticmethod
    def _split_coords(x: float, y: float) -> tuple[int, int, int, int]:
        """全局 float 坐标 → (chunk_x, chunk_y, tile_x, tile_y) 整数四元组。"""
        gx = int(x // 1)
        gy = int(y // 1)
        cx = gx // TILE_MAP_SIZE
        cy = gy // TILE_MAP_SIZE
        return (cx, cy, gx - cx * TILE_MAP_SIZE, gy - cy * TILE_MAP_SIZE)

    def _apply_position(self, x: float, y: float) -> tuple[float, float]:
        """写入权威位置：更新 float 精确值，跨 tile 时同步整数索引。

        Args:
            x: 全局 tile X 坐标。
            y: 全局 tile Y 坐标。

        Returns:
            写入后的位置 (x, y)。
        """
        if self._entity is None:
            self.spawn()
        entity = self._entity
        entity.set_data("fx", x)
        entity.set_data("fy", y)
        cx, cy, tx, ty = self._split_coords(x, y)
        if (cx, cy, tx, ty) != entity.position:
            self._manager.move(
                entity.id, cx, cy, tx, ty, game_time=self._clock.time,
            )
        return (x, y)
