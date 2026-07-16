"""事件数据结构 — 总线上流动的消息单元。

所有状态变化通过 Event 在模块间传递，各模块不直接耦合。
"""

from dataclasses import dataclass, field
from typing import Any
import uuid


@dataclass
class Event:
    """总线上的一条事件。

    由各系统生成，事件系统只负责记录和路由，不校验 data 内容。

    Z 轴通过 layer_id 离散化（0=地表，负数=地下层）。location 保持
    层内平面坐标 (chunk_x, chunk_y, tile_x?, tile_y?)，layer_id 作为
    正交字段单独存在。空间索引 key = (layer_id, chunk_x, chunk_y)。

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

    用于 subscribe() 时限制回调只接收指定 chunk 区域内的事件。
    None 表示不做位置限制，完全向后兼容。

    Attributes:
        center_chunk: 中心 chunk 坐标 (chunk_x, chunk_y)。
        radius: 搜索半径（chunk 数），默认 0 即仅匹配自身 chunk。
    """
    center_chunk: tuple[int, int]
    radius: int = 0

    def matches(self, event_location: tuple) -> bool:
        """判断事件位置是否在过滤范围内。

        Args:
            event_location: 事件 location 字段，格式 (chunk_x, chunk_y, ...)。

        Returns:
            True 表示事件位置在范围内，应触发回调。
        """
        cx, cy = self.center_chunk
        ex, ey = event_location[0], event_location[1]
        return abs(ex - cx) <= self.radius and abs(ey - cy) <= self.radius
