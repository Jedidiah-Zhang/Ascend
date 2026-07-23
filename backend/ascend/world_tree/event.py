"""事件数据结构 — 总线上流动的消息单元。

所有状态变化通过 Event 在模块间传递，各模块不直接耦合。
"""

from dataclasses import dataclass, field
from typing import Any
import uuid

from ascend.config import TILE_MAP_SIZE

# 子格尺寸：将每个 chunk 内细分为 sub-cell 以支持更精细的空间索引
# 每个 sub-cell = SUB_CELL_SIZE × SUB_CELL_SIZE tiles（16×16 = 256 tiles）
SUB_CELL_SIZE: int = 16
SUB_CELLS: int = (TILE_MAP_SIZE + SUB_CELL_SIZE - 1) // SUB_CELL_SIZE  # 13


def spatial_key(
    layer_id: int,
    chunk_x: int,
    chunk_y: int,
    tile_x: int | None = None,
    tile_y: int | None = None,
) -> tuple[int, int, int, int, int]:
    """计算空间索引 5 元组键。

    (layer_id, chunk_x, chunk_y, sub_cx, sub_cy)
    若 tile 坐标为 None，sub 设为 0。
    """
    scx = (tile_x // SUB_CELL_SIZE) if tile_x is not None else 0
    scy = (tile_y // SUB_CELL_SIZE) if tile_y is not None else 0
    return (layer_id, chunk_x, chunk_y, scx, scy)


def sub_cell_range(
    tile_x: int,
    tile_y: int,
    sub_radius: int = 0,
) -> tuple[tuple[int, int], tuple[int, int]]:
    """给定 tile 坐标和 sub-cell 半径，返回 sub-cell 索引闭区间。

    Returns:
        ((scx_lo, scx_hi), (scy_lo, scy_hi))，均为闭区间。
    """
    csx = tile_x // SUB_CELL_SIZE
    csy = tile_y // SUB_CELL_SIZE
    scx_lo = max(0, csx - sub_radius)
    scx_hi = min(SUB_CELLS - 1, csx + sub_radius)
    scy_lo = max(0, csy - sub_radius)
    scy_hi = min(SUB_CELLS - 1, csy + sub_radius)
    return ((scx_lo, scx_hi), (scy_lo, scy_hi))


@dataclass
class Event:
    """总线上的一条事件。

    由各系统生成，事件系统只负责记录和路由，不校验 data 内容。

    Z 轴通过 layer_id 离散化（0=地表，负数=地下层）。location 保持
    层内平面坐标 (chunk_x, chunk_y, tile_x?, tile_y?)，layer_id 作为
    正交字段单独存在。空间索引 key = (layer_id, chunk_x, chunk_y, sub_cx, sub_cy)。

    Attributes:
        timestamp: 世界时间（整数，单位：tick）。
        location: 事件层内平面位置 (chunk_x, chunk_y, tile_x?, tile_y?)。
        layer_id: 所在 Z 层（0=地表，负数=地下层），默认 0。
        initiator_type: 发起方类型 "system" | "npc" | "player"。
        initiator_id: 发起方唯一标识。
        affected: 受影响方列表。
        event_type: 事件类型字符串，各系统自行注册，如 "weather_change"。
        data: 事件类型特定的附加数据，JSON 可序列化。
        caused_by: 上游因果事件 ID 列表。
        observes: 被观测的物理事件 ID（仅 observation 事件使用）。
        co_participants: 共同参与方 ID 列表。
        id: 事件唯一标识（UUID hex，自动生成）。
    """
    timestamp: int
    location: tuple[int, int, int | None, int | None]
    initiator_type: str
    initiator_id: str
    affected: list[AffectedParty]
    event_type: str

    layer_id: int = 0
    weight: int = 1
    data: dict[str, Any] = field(default_factory=dict)

    caused_by: list[str] = field(default_factory=list)
    observes: str | None = None
    co_participants: list[str] = field(default_factory=list)

    id: str = field(default_factory=lambda: uuid.uuid4().hex)

    def __hash__(self) -> int:
        """以事件 ID 作为哈希值，支持 set/dict 键。

        Returns:
            事件 ID 的哈希值。
        """
        return hash(self.id)

    def __eq__(self, other: object) -> bool:
        """基于事件 ID 判等。

        Args:
            other: 要比较的对象。

        Returns:
            同为 Event 且 ID 相同时为 True。
        """
        if not isinstance(other, Event):
            return False
        return self.id == other.id


@dataclass
class LocationFilter:
    """订阅位置过滤条件。

    用于 subscribe() 时限制回调只接收指定区域的事件。
    两层过滤：
    - chunk 级（必选）：center_chunk + radius，按 chunk 粗筛。
    - sub-cell 级（可选）：center_tile + sub_radius，按 sub-cell 精筛。
    None 表示不做位置限制，完全向后兼容。

    Attributes:
        center_chunk: 中心 chunk 坐标 (chunk_x, chunk_y)。
        radius: chunk 搜索半径，默认 0 即仅匹配自身 chunk。
        center_tile: 可选，中心 tile 坐标 (tile_x, tile_y)，开启 sub-cell 精筛。
        sub_radius: sub-cell 搜索半径，默认 0 即仅匹配自身 sub-cell。
    """

    center_chunk: tuple[int, int]
    radius: int = 0
    center_tile: tuple[int, int] | None = None
    sub_radius: int = 0

    def matches(self, event_location: tuple) -> bool:
        """判断事件位置是否在过滤范围内。

        Args:
            event_location: 事件 location 字段，格式 (chunk_x, chunk_y, tile_x?, tile_y?)。

        Returns:
            True 表示事件位置在范围内，应触发回调。
        """
        cx, cy = self.center_chunk
        ex, ey = event_location[0], event_location[1]
        if abs(ex - cx) > self.radius or abs(ey - cy) > self.radius:
            return False
        if self.center_tile is not None:
            tx: int | None = event_location[2]
            ty: int | None = event_location[3]
            if tx is None or ty is None:
                return True
            ctx, cty = self.center_tile
            esx = tx // SUB_CELL_SIZE
            esy = ty // SUB_CELL_SIZE
            csx = ctx // SUB_CELL_SIZE
            csy = cty // SUB_CELL_SIZE
            sr = self.sub_radius
            if abs(esx - csx) > sr or abs(esy - csy) > sr:
                return False
        return True
