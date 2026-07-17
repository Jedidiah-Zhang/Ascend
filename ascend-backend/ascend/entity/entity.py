"""实体数据结构 — 游戏世界中一切存在物的基类。

使用 __slots__ 消除 per-instance __dict__，data 懒分配，position 拆为独立 int 字段。
大规模实体场景下内存效率显著优于普通 dataclass。

设计原则（Issue #20）:
  - 实体类型是存在形态分类（生物/植物/物品/建筑），"被谁控制"不是类型
    —— 玩家只是 controller=PLAYER 的 CREATURE，与 NPC 唯一的区别是
    决策模块由玩家输入替代 AI。这是意识转移玩法的架构基础。
  - 组合优于继承：实体 = ID + 位置 + 组件集合。data dict 为朴素组合
    容器，组件 schema（genome/body/needs）等上游系统定型后规范化。
"""

import uuid
from enum import IntEnum
from dataclasses import dataclass, field

from ascend.config import TILE_MAP_SIZE


class EntityType(IntEnum):
    """实体存在形态枚举。

    按"是什么"分类，不按"被谁控制"分类。
    使用 int 枚举，内存和比较效率优于字符串。

    序列化约定：跨进程/持久化一律使用 ``.name`` 字符串，禁止使用
    数值——枚举成员可能增删重排（如 PLANT 插入导致 ITEM 1→2），
    数值序列化会在版本间静默错位。
    """
    CREATURE = 0
    PLANT = 1
    ITEM = 2
    STRUCTURE = 3


def split_coords(x: float, y: float) -> tuple[int, int, int, int]:
    """全局 float tile 坐标 → (chunk_x, chunk_y, tile_x, tile_y) 整数四元组。

    floor 语义：负坐标正确落入负 chunk，tile 恒在 [0, TILE_MAP_SIZE)。
    与 Entity.global_xy 互为拆分/合成。

    Args:
        x: 全局 tile X 坐标。
        y: 全局 tile Y 坐标。

    Returns:
        (chunk_x, chunk_y, tile_x, tile_y)。
    """
    gx = int(x // 1)
    gy = int(y // 1)
    cx = gx // TILE_MAP_SIZE
    cy = gy // TILE_MAP_SIZE
    return (cx, cy, gx - cx * TILE_MAP_SIZE, gy - cy * TILE_MAP_SIZE)


class Controller(IntEnum):
    """实体的决策控制者。

    NONE: 无决策模块（物品、建筑、植物等被动实体）。
    AI:   AI 决策（NPC 心智系统 / 状态机）。
    PLAYER: 玩家输入决策（可转移——意识转移即改写此标记）。
    """
    NONE = 0
    AI = 1
    PLAYER = 2


@dataclass(slots=True)
class Entity:
    """游戏世界中的一个实体。

    实体是所有存在物的基类：生物、植物、物品、建筑等。
    使用 __slots__ 存储，无 __dict__ 开销；data 为 None 时不分配字典。

    Z 轴通过 layer_id 离散化：0=地表，负数=地下层（-1=浅洞，-2=深洞）。
    每层内部是纯 2D 平面，layer_id 是枚举式的层标识而非连续坐标。
    渲染高度（tile elevation）与 layer_id 正交：同一层内 tile 仍有连续
    高度差供 2.5D 渲染抬升，但事件/寻路/碰撞只认 layer_id。

    Attributes:
        id: 实体唯一标识（UUID hex）。
        entity_type: 存在形态（EntityType 枚举）。
        chunk_x, chunk_y: 所在 chunk 坐标。
        tile_x, tile_y: chunk 内 tile 坐标，可为 None。
        born_at: 实体在虚拟世界诞生时的游戏时间（秒）。
        layer_id: 所在 Z 层（0=地表，负数=地下层），默认 0。
        controller: 决策控制者（Controller 枚举），默认 NONE。
        data: 实体附加数据，首次写入时自动创建；没有数据时为 None。
            约定键: "fx"/"fy" 为精确 float 全局 tile 坐标（可移动实体）。
    """

    entity_type: EntityType
    chunk_x: int
    chunk_y: int
    tile_x: int | None
    tile_y: int | None
    born_at: int

    layer_id: int = 0
    controller: Controller = Controller.NONE
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

    @property
    def global_xy(self) -> tuple[float, float]:
        """全局 float tile 坐标 (x, y)。

        data["fx"/"fy"] 存在时（可移动实体的精确位置）优先返回；
        否则由 chunk/tile 整数字段推导（tile 为 None 时取 chunk 原点）。

        Returns:
            (x, y) 全局 tile 坐标。
        """
        fx = self.get_data("fx")
        fy = self.get_data("fy")
        if fx is not None and fy is not None:
            return (float(fx), float(fy))
        x = float(self.chunk_x * TILE_MAP_SIZE + (self.tile_x or 0))
        y = float(self.chunk_y * TILE_MAP_SIZE + (self.tile_y or 0))
        return (x, y)

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
