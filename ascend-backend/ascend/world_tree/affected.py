"""事件的受影响方数据结构。"""

from dataclasses import dataclass


@dataclass
class AffectedParty:
    """事件的受影响方。

    Attributes:
        entity_id: 受影响实体的唯一标识。
        role: 受影响角色，取值为 "subject" | "witness" | "recipient"。
    """
    entity_id: str
    role: str
