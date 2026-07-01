"""事件数据结构 — 总线上流动的消息单元。

所有状态变化通过 Event 在模块间传递，各模块不直接耦合。
"""

from dataclasses import dataclass, field
from typing import Any
import uuid

from .affected import AffectedParty  # noqa: F401 — 从旧导入路径兼容


@dataclass
class Event:
    """总线上的一条事件。

    由各系统生成，事件系统只负责记录和路由，不校验 data 内容。

    Attributes:
        timestamp: 世界时间（浮点，单位：游戏秒）。
        location: 事件位置 (chunk_x, chunk_y, tile_x?, tile_y?)。
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
    timestamp: float
    location: tuple[int, int, int | None, int | None]
    initiator_type: str
    initiator_id: str
    affected: list[AffectedParty]
    event_type: str
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
