"""TileGrid — 详细地图层紧凑存储结构。

使用 array('H')（uint16）存储 200×200 地形类型网格，
array('f')（float32）存储对应高度场和坡度场。
TerrainType 的 int 值直接存入数组，每 chunk 80KB 地形 + 160KB 高度 + 160KB 坡度。
"""

import struct
from array import array

from .terrain import TerrainType

_TILEGRID_VERSION: int = 1
_BYTES_TERRAIN: int = 40000 * 2
_BYTES_ELEV: int = 40000 * 4

# 详细地图固定尺寸
TILE_MAP_SIZE: int = 200


class TileGrid:
    """200×200 地形网格 + 高度场 + 坡度场，紧凑数组存储。

    地形类型用 array('H')（uint16），高度和坡度用 array('f')（float32）。
    高度场供 2.5D 渲染抬升 tile 顶面，坡度场供 isometric 渲染选择斜坡变体。

    线程安全：每个 TileGrid 归属单个 chunk，由该 chunk 的生成线程独占。
    """

    def __init__(
        self,
        data: array | list[int] | None = None,
        elevation: array | list[float] | None = None,
        slope: array | list[float] | None = None,
    ) -> None:
        """初始化网格。

        若 data 为 None，地形初始化为全 GRASSLAND，高度和坡度初始化为全 0。
        elevation/slope 可选，提供时长度需与地形一致（40000）。

        Args:
            data: 地形数据，长度应为 40000 (200×200)。
            elevation: 高度数据 (m)，长度应为 40000。未提供则全 0。
            slope: 坡度数据 (m/m)，长度应为 40000。未提供则全 0。

        Raises:
            ValueError: 数据长度与 200×200 不匹配。
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

        if elevation is None:
            self._elevation = array('f', [0.0]) * self._length
        elif isinstance(elevation, array):
            if len(elevation) != self._length:
                raise ValueError(
                    f"高度 array 长度需为 {self._length}，实际为 {len(elevation)}"
                )
            self._elevation = elevation
        else:
            if len(elevation) != self._length:
                raise ValueError(
                    f"高度列表长度需为 {self._length}，实际为 {len(elevation)}"
                )
            self._elevation = array('f', elevation)

        if slope is None:
            self._slope = array('f', [0.0]) * self._length
        elif isinstance(slope, array):
            if len(slope) != self._length:
                raise ValueError(
                    f"坡度 array 长度需为 {self._length}，实际为 {len(slope)}"
                )
            self._slope = slope
        else:
            if len(slope) != self._length:
                raise ValueError(
                    f"坡度列表长度需为 {self._length}，实际为 {len(slope)}"
                )
            self._slope = array('f', slope)

    def __repr__(self) -> str:
        """返回网格摘要。"""
        grassland = int(TerrainType.GRASSLAND)
        non_default = sum(1 for v in self._data if v != grassland)
        pct = non_default / self._length * 100
        return (
            f"TileGrid({self._size}×{self._size}, "
            f"non_grassland={pct:.1f}%)"
        )

    # ── 单点访问 ──────────────────────────────────────────

    def get(self, x: int, y: int) -> TerrainType:
        """读取 (x, y) 处的地形类型。"""
        return TerrainType(self._data[y * self._size + x])

    def set(self, x: int, y: int, terrain: TerrainType) -> None:
        """写入 (x, y) 处的地形类型。"""
        self._data[y * self._size + x] = int(terrain)

    def get_elevation(self, x: int, y: int) -> float:
        """读取 (x, y) 处的高度 (m)，供 2.5D 渲染和游戏逻辑查询。"""
        return self._elevation[y * self._size + x]

    def set_elevation(self, x: int, y: int, elevation: float) -> None:
        """写入 (x, y) 处的高度 (m)。"""
        self._elevation[y * self._size + x] = elevation

    def get_slope(self, x: int, y: int) -> float:
        """读取 (x, y) 处的最大坡度 (m/m)，供 isometric 渲染选择斜坡变体。"""
        return self._slope[y * self._size + x]

    def set_slope(self, x: int, y: int, slope: float) -> None:
        """写入 (x, y) 处的坡度 (m/m)。"""
        self._slope[y * self._size + x] = slope

    # ── 区域查询 ──────────────────────────────────────────

    def get_region(
        self,
        x: int,
        y: int,
        w: int,
        h: int,
    ) -> list[list[TerrainType]]:
        """读取矩形区域的地形类型。"""
        result: list[list[TerrainType]] = []
        for row in range(y, y + h):
            start = row * self._size + x
            end = start + w
            result.append([TerrainType(v) for v in self._data[start:end]])
        return result

    # ── 整体序列化 ────────────────────────────────────────

    def to_list(self) -> list[int]:
        """导出地形类型为 Python int 列表（用于 JSON 发往 Godot）。"""
        return list(self._data)

    def to_elevation_list(self) -> list[float]:
        """导出高度场为 Python float 列表（用于 2.5D 渲染发往 Godot）。"""
        return list(self._elevation)

    def to_slope_list(self) -> list[float]:
        """导出坡度场为 Python float 列表（用于 isometric 渲染发往 Godot）。"""
        return list(self._slope)

    @classmethod
    def from_list(
        cls,
        data: list[int],
        elevation: list[float] | None = None,
        slope: list[float] | None = None,
    ) -> "TileGrid":
        """从 int 列表还原。可选 elevation/slope 列表同步还原。

        Args:
            data: 长度为 40000 的 int 列表（地形）。
            elevation: 长度为 40000 的 float 列表（高度），未提供则全 0。
            slope: 长度为 40000 的 float 列表（坡度），未提供则全 0。
        """
        return cls(data=data, elevation=elevation, slope=slope)

    # ── 二进制序列化（用于 SQLite 持久化） ────────────────

    def to_bytes(self) -> bytes:
        """序列化为紧凑二进制 BLOB。

        格式: 4B version(LE) + 80KB terrain(uint16 LE) +
              160KB elevation(float32 LE) + 160KB slope(float32 LE)。
        """
        header = struct.pack("<I", _TILEGRID_VERSION)
        return header + self._data.tobytes() + self._elevation.tobytes() + self._slope.tobytes()

    @classmethod
    def from_bytes(cls, data: bytes) -> "TileGrid":
        """从 to_bytes() 输出的二进制 BLOB 反序列化。

        Args:
            data: to_bytes() 输出的字节串。

        Returns:
            重建的 TileGrid。

        Raises:
            ValueError: 版本不匹配或数据长度不正确。
        """
        if len(data) < 4:
            raise ValueError("数据过短，缺少版本头")
        version = struct.unpack("<I", data[:4])[0]
        if version != _TILEGRID_VERSION:
            raise ValueError(f"不支持 TileGrid 版本: {version}")
        expected = 4 + _BYTES_TERRAIN + _BYTES_ELEV * 2
        if len(data) != expected:
            raise ValueError(
                f"数据长度错误: 期望 {expected} 字节，实际 {len(data)}"
            )
        off = 4
        terrain = array("H")
        terrain.frombytes(data[off : off + _BYTES_TERRAIN])
        off += _BYTES_TERRAIN
        elevation = array("f")
        elevation.frombytes(data[off : off + _BYTES_ELEV])
        off += _BYTES_ELEV
        slope = array("f")
        slope.frombytes(data[off : off + _BYTES_ELEV])
        return cls(data=terrain, elevation=elevation, slope=slope)

    # ── 低级访问 ──────────────────────────────────────────

    def get_raw(self, index: int) -> int:
        """读取底层数组指定索引的地形 int 值。"""
        return self._data[index]

    def raw_data(self) -> array:
        """返回底层地形 array('H') 的引用（零拷贝）。"""
        return self._data

    def elevation_raw(self) -> array:
        """返回底层高度 array('f') 的引用（零拷贝）。"""
        return self._elevation

    def slope_raw(self) -> array:
        """返回底层坡度 array('f') 的引用（零拷贝）。"""
        return self._slope

    @property
    def size(self) -> int:
        """网格边长（200）。"""
        return self._size

    def __eq__(self, other: object) -> bool:
        """比较两个 TileGrid 是否地形、高度和坡度均相等。"""
        if not isinstance(other, TileGrid):
            return NotImplemented
        return (
            self._data == other._data
            and self._elevation == other._elevation
            and self._slope == other._slope
        )
