"""TileGrid — 详细地图层紧凑存储结构。

使用 array('H')（uint16）存储 200×200 地形类型网格。
TerrainType 的 int 值直接存入数组，每 chunk 仅 80KB。
"""

from array import array

from .terrain import TerrainType

# 详细地图固定尺寸
TILE_MAP_SIZE: int = 200


class TileGrid:
    """200×200 地形网格，紧凑数组存储。

    使用 Python 标准库 array('H')（无符号 short），
    比 list[list[int]] 节省约 13 倍内存（80KB vs 1.1MB）。

    线程安全：每个 TileGrid 归属单个 chunk，由该 chunk 的生成线程独占。
    """

    def __init__(self, data: array | list[int] | None = None) -> None:
        """初始化网格。

        若 data 为 None，初始化为全 GRASSLAND。
        若 data 为 list，转换为 array('H')。
        若 data 为 array('H')，直接引用（需确保长度正确）。

        Args:
            data: 初始数据，长度应为 40000 (200×200)。
        """
        self._size: int = TILE_MAP_SIZE
        self._length: int = self._size * self._size

        if data is None:
            self._data = array('H', [int(TerrainType.GRASSLAND)]) * self._length
        elif isinstance(data, array):
            if len(data) != self._length:
                raise ValueError(
                    f"array 长度需为 {self._length}，实际为 {len(data)}"
                )
            self._data = data
        else:
            if len(data) != self._length:
                raise ValueError(
                    f"列表长度需为 {self._length}，实际为 {len(data)}"
                )
            self._data = array('H', data)

    def __repr__(self) -> str:
        """返回网格摘要。

        Returns:
            含尺寸和非默认 tile 占比的 repr 字符串。
        """
        grassland = int(TerrainType.GRASSLAND)
        non_default = sum(1 for v in self._data if v != grassland)
        pct = non_default / self._length * 100
        return (
            f"TileGrid({self._size}×{self._size}, "
            f"non_grassland={pct:.1f}%)"
        )

    # ── 单点访问 ──────────────────────────────────────────

    def get(self, x: int, y: int) -> TerrainType:
        """读取 (x, y) 处的地形类型。

        Args:
            x: tile X 坐标 [0, 199]。
            y: tile Y 坐标 [0, 199]。

        Returns:
            TerrainType 枚举值。
        """
        return TerrainType(self._data[y * self._size + x])

    def set(self, x: int, y: int, terrain: TerrainType) -> None:
        """写入 (x, y) 处的地形类型。

        Args:
            x: tile X 坐标 [0, 199]。
            y: tile Y 坐标 [0, 199]。
            terrain: 地形类型。
        """
        self._data[y * self._size + x] = int(terrain)

    # ── 区域查询 ──────────────────────────────────────────

    def get_region(
        self,
        x: int,
        y: int,
        w: int,
        h: int,
    ) -> list[list[TerrainType]]:
        """读取矩形区域的地形类型。

        返回 Python 列表，便于调用方遍历和序列化。
        对于高性能场景，使用 get_raw() 直接访问底层数组。

        Args:
            x: 区域左上角 X。
            y: 区域左上角 Y。
            w: 区域宽度。
            h: 区域高度。

        Returns:
            二维 TerrainType 列表，形状 (h, w)。
        """
        result: list[list[TerrainType]] = []
        for row in range(y, y + h):
            start = row * self._size + x
            end = start + w
            result.append([TerrainType(v) for v in self._data[start:end]])
        return result

    # ── 整体序列化 ────────────────────────────────────────

    def to_list(self) -> list[int]:
        """导出为 Python int 列表（用于 JSON 序列化发往 Godot）。

        Returns:
            长度为 40000 的 int 列表。
        """
        return list(self._data)

    @classmethod
    def from_list(cls, data: list[int]) -> "TileGrid":
        """从 int 列表还原（用于从 Godot 接收或存档加载）。

        Args:
            data: 长度为 40000 的 int 列表。

        Returns:
            TileGrid 实例。
        """
        return cls(data=data)

    # ── 低级访问 ──────────────────────────────────────────

    def get_raw(self, index: int) -> int:
        """读取底层数组指定索引的 int 值。

        用于批量扫描等需要高性能的场景。

        Args:
            index: 底层数组索引（y * 200 + x）。

        Returns:
            TerrainType 的 int 值。
        """
        return self._data[index]

    def raw_data(self) -> array:
        """返回底层 array('H') 的引用（零拷贝）。

        Returns:
            底层 uint16 数组。
        """
        return self._data

    @property
    def size(self) -> int:
        """网格边长（200）。"""
        return self._size

    def __eq__(self, other: object) -> bool:
        """比较两个 TileGrid 是否数据相等。"""
        if not isinstance(other, TileGrid):
            return NotImplemented
        return self._data == other._data
