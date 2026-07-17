"""实体数据结构 — 游戏世界中一切存在物的基类。

使用 __slots__ 消除 per-instance __dict__，data 懒分配，position 拆为独立 int 字段。
大规模实体场景下内存效率显著优于普通 dataclass。
"""

import uuid
from enum import IntEnum
from dataclasses import dataclass, field


class EntityType(IntEnum):
    """实体类型枚举。

    使用 int 枚举，内存和比较效率优于字符串。
    """
    NPC = 0
    ITEM = 1
    STRUCTURE = 2
    PLAYER = 3


@dataclass(slots=True)
class Entity:
    """游戏世界中的一个实体。

    实体是所有存在物的基类：NPC、物品、建筑、玩家等。
    使用 __slots__ 存储，无 __dict__ 开销；data 为 None 时不分配字典。

    Z 轴通过 layer_id 离散化：0=地表，负数=地下层（-1=浅洞，-2=深洞）。
    每层内部是纯 2D 平面，layer_id 是枚举式的层标识而非连续坐标。
    渲染高度（tile elevation）与 layer_id 正交：同一层内 tile 仍有连续
    高度差供 2.5D 渲染抬升，但事件/寻路/碰撞只认 layer_id。

    Attributes:
        id: 实体唯一标识（UUID hex）。
        entity_type: 实体类型（EntityType 枚举）。
        chunk_x, chunk_y: 所在 chunk 坐标。
        tile_x, tile_y: chunk 内 tile 坐标，可为 None。
        spawned_at: 实体被创建时的游戏时间（秒）。
        layer_id: 所在 Z 层（0=地表，负数=地下层），默认 0。
        data: 实体附加数据，首次写入时自动创建；没有数据时为 None。
    """

    entity_type: EntityType
    chunk_x: int
    chunk_y: int
    tile_x: int | None
    tile_y: int | None
    spawned_at: int

    layer_id: int = 0
    id: str = field(default_factory=lambda: uuid.uuid4().hex)
    data: dict | None = None

    @property
    def chunk(self) -> tuple[int, int]:
        """实体所在 chunk 坐标（层内平面）。"""
        return (self.chunk_x, self.chunk_y)

    @property
    def position(self) -> tuple[int, int, int | None, int | None]:
        """层内平面位置 (chunk_x, chunk_y, tile_x, tile_y)。

        不含 layer_id——层是正交维度，用 layer_id 字段单独表达。
        这样 location = position 时平面语义自洽。
        """
        return (self.chunk_x, self.chunk_y, self.tile_x, self.tile_y)

    def set_data(self, key: str, value: object) -> None:
        """写入附加数据，若 data 为 None 则懒创建字典。

        Args:
            key: 键。
            value: 值。
        """
        if self.data is None:
            self.data = {}
        self.data[key] = value

    def get_data(self, key: str, default: object = None) -> object:
        """读取附加数据，安全处理 data 为 None 的情况。

        Args:
            key: 键。
            default: 键不存在或 data 为 None 时的默认值。

        Returns:
            对应的值或 default。
        """
        if self.data is None:
            return default
        return self.data.get(key, default)

    def __hash__(self) -> int:
        """以实体 ID 作为哈希值。

        Returns:
            实体 ID 的哈希值。
        """
        return hash(self.id)
