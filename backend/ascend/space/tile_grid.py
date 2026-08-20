"""TileGrid — 详细地图层紧凑存储结构。

使用 array('H')（uint16）存储 200×200 地形类型网格，
array('f')（float32）存储对应高度场和坡度场，
array('B')（uint8）按 STATE_TYPES 注册表存储动态状态层（湿润/覆雪/结冰）。
TerrainType 的 int 值直接存入数组，
每 chunk 80KB 地形 + 160KB 高度 + 160KB 坡度 + 3×40KB 状态。
"""

import struct
import sys
from array import array

from .terrain import TerrainType
from .state_defs import STATE_TYPES, state_keys
from ascend.config import TILE_MAP_SIZE

_TILEGRID_VERSION: int = 2
_BYTES_TERRAIN: int = TILE_MAP_SIZE * TILE_MAP_SIZE * 2
_BYTES_ELEV: int = TILE_MAP_SIZE * TILE_MAP_SIZE * 4


def _state_bytes() -> int:
    """状态段总字节数（按 STATE_TYPES 注册顺序，B=1 字节/格）。

    增删状态（bump _TILEGRID_VERSION）后此值自动跟随注册表。
    """
    total = 0
    for cfg in STATE_TYPES.values():
        total += TILE_MAP_SIZE * TILE_MAP_SIZE * array(cfg.dtype).itemsize
    return total


class TileGrid:
    """200×200 地形网格 + 高度场 + 坡度场 + 状态层，紧凑数组存储。

    地形类型用 array('H')（uint16），高度和坡度用 array('f')（float32），
    状态数组用 array('B')（uint8，按 STATE_TYPES 注册表）。
    高度场供 2.5D 渲染抬升 tile 顶面，坡度场供 isometric 渲染选择斜坡变体；
    状态层为动态叠加（湿润/覆雪/结冰），由状态引擎涂抹、随 chunk 持久化。

    线程安全：每个 TileGrid 归属单个 chunk，由该 chunk 的生成线程独占。
    """

    def __init__(
        self,
        data: array | list[int] | None = None,
        elevation: array | list[float] | None = None,
        slope: array | list[float] | None = None,
        states: dict[str, array | list[int]] | None = None,
    ) -> None:
        """初始化网格。

        若 data 为 None，地形初始化为全 GRASSLAND，高度和坡度初始化为全 0。
        elevation/slope 可选，提供时长度需与地形一致（40000）。
        states 可选：{状态 key: 长度 40000 的数组/列表}，未提供则全 0。

        Args:
            data: 地形数据，长度应为 40000 (200×200)。
            elevation: 高度数据 (m)，长度应为 40000。未提供则全 0。
            slope: 坡度数据 (m/m)，长度应为 40000。未提供则全 0。
            states: 状态数据 {key: 长度 40000 的数组/列表}，未提供则全 0。

        Raises:
            ValueError: 数据长度与 200×200 不匹配，或状态 key 未知。
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

        self._states: dict[str, array] = {}
        for key, cfg in STATE_TYPES.items():
            if states is not None and key in states:
                raw = states[key]
                if isinstance(raw, array):
                    if len(raw) != self._length:
                        raise ValueError(
                            f"状态 {key} array 长度需为 {self._length}，"
                            f"实际为 {len(raw)}"
                        )
                    if raw.typecode != cfg.dtype:
                        # 类型不符：值域超 uint8 会绕过 C 内核的
                        # c_uint8 视图直接 TypeError——显式转换拒绝歧义
                        raw = array(cfg.dtype, raw)
                    self._states[key] = raw
                else:
                    if len(raw) != self._length:
                        raise ValueError(
                            f"状态 {key} 列表长度需为 {self._length}，"
                            f"实际为 {len(raw)}"
                        )
                    self._states[key] = array(cfg.dtype, raw)
            else:
                self._states[key] = array(
                    cfg.dtype, [cfg.bounds[0]],
                ) * self._length

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

    # ── 状态层（动态叠加，注册表驱动） ──────────────────

    def get_state(self, key: str, x: int, y: int) -> int:
        """读取 (x, y) 处的状态值。

        Args:
            key: 状态名（STATE_TYPES 的键）。
            x, y: tile 坐标。

        Returns:
            状态值（语义范围见 STATE_TYPES[key].bounds）。

        Raises:
            KeyError: 未知状态 key。
        """
        return self._states[key][y * self._size + x]

    def set_state(self, key: str, x: int, y: int, value: int) -> None:
        """写入 (x, y) 处的状态值（clamp 到语义 bounds）。

        Args:
            key: 状态名（STATE_TYPES 的键）。
            x, y: tile 坐标。
            value: 状态值（自动 clamp 到 bounds）。

        Raises:
            KeyError: 未知状态 key。
        """
        lo, hi = STATE_TYPES[key].bounds
        self._states[key][y * self._size + x] = max(lo, min(int(value), hi))

    def state_raw(self, key: str) -> array:
        """返回底层状态 array 的引用（零拷贝）。

        状态引擎批量涂抹/结算直接操作该数组（与 raw_data/elevation_raw
        同模式）。调用方负责按 STATE_TYPES[key].bounds clamp。

        Args:
            key: 状态名（STATE_TYPES 的键）。

        Raises:
            KeyError: 未知状态 key。
        """
        return self._states[key]

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

    # ── 二进制序列化（网络传输 + SQLite 持久化） ──────────

    def to_bytes(self) -> bytes:
        """序列化为紧凑二进制 BLOB（显式小端）。

        格式: 4B version(LE) + 80KB terrain(uint16 LE) +
              160KB elevation(float32 LE) + 160KB slope(float32 LE) +
              状态数组段（按 STATE_TYPES 注册顺序，各 40KB uint8）。
        """
        header = struct.pack("<I", _TILEGRID_VERSION)
        if sys.byteorder != "little":
            # 大端机器上显式转小端，保证网络/持久化字节序稳定
            terrain_le = array("H", self._data)
            elevation_le = array("f", self._elevation)
            slope_le = array("f", self._slope)
            terrain_le.byteswap()
            elevation_le.byteswap()
            slope_le.byteswap()
            states_le = []
            for key in state_keys():
                arr = array(self._states[key].typecode, self._states[key])
                arr.byteswap()
                states_le.append(arr)
            return (
                header
                + terrain_le.tobytes()
                + elevation_le.tobytes()
                + slope_le.tobytes()
                + b"".join(a.tobytes() for a in states_le)
            )
        return (
            header
            + self._data.tobytes()
            + self._elevation.tobytes()
            + self._slope.tobytes()
            + b"".join(
                self._states[key].tobytes() for key in state_keys()
            )
        )

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
        expected = 4 + _BYTES_TERRAIN + _BYTES_ELEV * 2 + _state_bytes()
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
        off += _BYTES_ELEV
        states: dict[str, array] = {}
        for key in state_keys():
            cfg = STATE_TYPES[key]
            size = TILE_MAP_SIZE * TILE_MAP_SIZE * array(cfg.dtype).itemsize
            arr = array(cfg.dtype)
            arr.frombytes(data[off : off + size])
            off += size
            states[key] = arr
        if sys.byteorder != "little":
            # array.frombytes 按本机字节序读，小端契约须显式转换
            terrain.byteswap()
            elevation.byteswap()
            slope.byteswap()
            for arr in states.values():
                arr.byteswap()
        return cls(data=terrain, elevation=elevation, slope=slope, states=states)

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
        """比较两个 TileGrid 是否地形、高度、坡度与状态层均相等。"""
        if not isinstance(other, TileGrid):
            return NotImplemented
        return (
            self._data == other._data
            and self._elevation == other._elevation
            and self._slope == other._slope
            and self._states == other._states
        )
