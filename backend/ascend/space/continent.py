"""大陆生成模块 — 层1 全局低分辨率大陆生成。

在世界创建时调用一次，生成 100m/采样点 的宏观场：
   - 海拔场（两层 Perlin：低频大陆轮廓 + 高频地形细节）
   - 温雨气候（C 端物理模型：纬度梯度 + 大陆度 + 雨影，校准后以 chunk 级 dict 存储）
   - 河流宽度场
   - 内陆湖泊
   - 水文数据（D8 流向、水流累积、湖盆、流线河网）

结果保存在 ContinentData 中，所有 chunk 和 tile 生成共享此数据。

用法:
    from ascend.space.continent import ContinentGenerator, ContinentParams

    gen = ContinentGenerator(seed=42)
    data = gen.generate()

    alt = data.sample_altitude(500.0, 300.0)
    is_land = data.is_land(12.5, 34.2)
"""

from array import array
import io
import struct
import zlib
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Union

from .noise import PerlinNoise
from .climate import ClimateZone, LAPSE_RATE
from ascend.config import (
    EROSION_ITERATIONS,
    LAKE_MIN_PIXELS,
    RIVER_FLOW_THRESHOLD,
    RIVER_MIN_LENGTH,
    RIVER_WIDTH_THRESHOLD,
    ELEVATION_TARGET_P99,
    ELEVATION_SCALE_FACTOR,
    CONTINENTALITY_K,
    CONTINENTALITY_D0_KM,
    RAINSHADOW_DECAY_KM,
    RAINSHADOW_SECONDARY_WEIGHT,
    RAINSHADOW_MIN_FACTOR,
    CONTINENT_BLEND_WEIGHT,
    CONTINENT_SAMPLE_RESOLUTION_M,
    TERRAIN_BLEND_WEIGHT,
    CENTER_BIAS_WEIGHT,
    CLIMATE_CALIB_RAINFALL_REF,
    CLIMATE_CALIB_TEMP_MIN,
    CLIMATE_CALIB_TEMP_MAX,
    CLIMATE_CALIB_HOT_THRESHOLD,
    CLIMATE_CALIB_COLD_RANGE,
    CLIMATE_CALIB_HOT_RAINFALL_TARGET,
    CLIMATE_CALIB_HOT_STRETCH_PARAM,
    CLIMATE_CALIB_COLD_RAINFALL_TARGET,
    CLIMATE_CALIB_COLD_STRETCH_PARAM,
)
from ascend.log import get_logger


def center_distance(dx: float, dy: float) -> float:
    """归一化坐标到矩形中心的 Chebyshev 距离，四象限对称。

    中心偏置（center bias）用「距地图中心的距离」把陆地推向中心。
    旧实现的手写分支在第三象限与负 y 轴出错（dx=0, dy=-2 时误算为 0，
    中心偏置消失）；max(abs) 恒非负且象限对称，消除该缺陷。

    Args:
        dx: 归一化 X 偏移（[-1, 1]）。
        dy: 归一化 Y 偏移（[-1, 1]）。

    Returns:
        非负距离 [0, 1]。
    """
    return max(abs(dx), abs(dy))

logger = get_logger(__name__)

# 大陆缓存格式版本：生成算法变更（侵蚀/水文/气候）或序列化格式
# 变更（如 v1 pickle → v2 显式二进制）时递增，旧缓存自动失效重新生成
# （同一 seed 的结果必须完全一致才能缓存）
CONTINENT_CACHE_VERSION: int = 3

# Knuth 乘法哈希：seed → 确定性角度 [0, 2π)（温度梯度/盛行风向共用）
def _seed_angle(seed: int) -> float:
    """seed → 确定性角度 [0, 2π)。

    Knuth 乘法哈希将任意整数种子均匀映射到角度；温度梯度方向
    与盛行风向均由此派生（各自独立调用点，同一 seed 结果相同）。
    """
    import math

    return ((seed * 2654435761) & 0xFFFFFFFF) / 0xFFFFFFFF * 2.0 * math.pi

# ── 二进制序列化（显式 schema，非 pickle） ────────────────
# 缓存随档分发（存档可分享）：pickle 反序列化可执行任意代码，
# 恶意分享存档 = 加载即 RCE；本格式只解析 struct/array 字节，
# 无代码执行面，截断/篡改数据一律拒绝（返回 None 重新生成）。
# 布局（小端）:
#   magic "ASCNT" + version u8
#   seed i64, grid_width i32, grid_height i32, cell_size f64
#   land_ratio f64
#   land_mask      u32 n + n×u8        （布尔掩码按 0/1 字节）
#   elevation      u32 n + n×f64
#   river_width    u32 n + n×f64
#   hydrology      u8 present
#     lake_basins  u32 count; each: u32 cells_n + cells_n×i32 + f64×2
#     flow_acc     u32 n + n×f64
#     directions   u32 n + n×i8
#     filled_dem   u32 n + n×f64
#     river_network u8 present
#       width i32, height i32
#       rivers u32 count; each:
#         u32 pts_n + pts_n×(f64 x, f64 y, f64 flow, i32 strahler)
#         + i32 source_idx + i32 outlet_idx + u32 pn + pn×i32
#       node_grid u32 count; each: i32 key, i32 x, i32 y
#   subdiv_ranges  u32 count; each: i32 zone, f64 p10, f64 p90
#   chunk_climate  u32 count; each: i32 cx, i32 cy, f64×3, i32 zone
# 网格字段（land_mask/elevation/river_width/flow_acc/directions/
# filled_dem）长度须 == grid_width × grid_height（防截断/篡改）。
_MAGIC: bytes = b"ASCNT"


def _w_u8(buf: io.BytesIO, v: int) -> None:
    buf.write(struct.pack("<B", v))


def _w_i32(buf: io.BytesIO, v: int) -> None:
    buf.write(struct.pack("<i", v))


def _w_i64(buf: io.BytesIO, v: int) -> None:
    buf.write(struct.pack("<q", v))


def _w_f64(buf: io.BytesIO, v: float) -> None:
    buf.write(struct.pack("<d", v))


def _w_i32_array(buf: io.BytesIO, values) -> None:
    _w_i32(buf, len(values))
    if values:
        buf.write(struct.pack(f"<{len(values)}i", *values))


def _w_i8_array(buf: io.BytesIO, values) -> None:
    _w_i32(buf, len(values))
    if values:
        buf.write(struct.pack(f"<{len(values)}b", *values))


def _w_f64_array(buf: io.BytesIO, values) -> None:
    _w_i32(buf, len(values))
    if values:
        buf.write(array("d", values).tobytes())


def _w_land_mask(buf: io.BytesIO, values) -> None:
    """布尔掩码按 0/1 字节数组写入。"""
    _w_i32(buf, len(values))
    if values:
        buf.write(bytes(1 if v else 0 for v in values))


class _Reader:
    """带边界检查的顺序读取器（截断/负长度抛 ValueError）。"""

    __slots__ = ("_buf", "_pos")

    def __init__(self, raw: bytes) -> None:
        self._buf = raw
        self._pos = 0

    def _take(self, n: int) -> bytes:
        end = self._pos + n
        if end > len(self._buf):
            raise ValueError("数据截断")
        data = self._buf[self._pos:end]
        self._pos = end
        return data

    def u8(self) -> int:
        return struct.unpack("<B", self._take(1))[0]

    def i32(self) -> int:
        return struct.unpack("<i", self._take(4))[0]

    def i64(self) -> int:
        return struct.unpack("<q", self._take(8))[0]

    def f64(self) -> float:
        return struct.unpack("<d", self._take(8))[0]

    def i32_array(self) -> list[int]:
        n = self.i32()
        if n < 0:
            raise ValueError("非法长度")
        if n == 0:
            return []
        return list(struct.unpack(f"<{n}i", self._take(4 * n)))

    def i8_array(self) -> list[int]:
        n = self.i32()
        if n < 0:
            raise ValueError("非法长度")
        if n == 0:
            return []
        return list(struct.unpack(f"<{n}b", self._take(n)))

    def f64_array(self) -> "array":
        n = self.i32()
        if n < 0:
            raise ValueError("非法长度")
        if n == 0:
            return array("d")
        return array("d", self._take(8 * n))


def serialize_continent(data: "ContinentData") -> bytes:
    """ContinentData → 压缩字节（大陆缓存落盘格式）。

    大陆宏观场是 seed 的确定性函数，生成耗时 5-30s（侵蚀+水文模拟）；
    落盘缓存后读档直接反序列化恢复，秒级完成。

    显式二进制 schema（见模块注释）：无代码执行面，随档分发安全。
    """
    buf = io.BytesIO()
    buf.write(_MAGIC)
    _w_u8(buf, CONTINENT_CACHE_VERSION)
    _w_i64(buf, int(data.seed))
    _w_i32(buf, data.grid_width)
    _w_i32(buf, data.grid_height)
    _w_f64(buf, float(data.cell_size))
    _w_f64(buf, float(data.land_ratio))
    _w_land_mask(buf, data.land_mask)
    _w_f64_array(buf, data.elevation_field)
    _w_f64_array(buf, data.river_width)
    h = data.hydrology
    _w_u8(buf, 1 if h is not None else 0)
    if h is not None:
        _w_i32(buf, len(h.lake_basins))
        for basin in h.lake_basins:
            _w_i32_array(buf, basin.cells)
            _w_f64(buf, float(basin.surface_elev))
            _w_f64(buf, float(basin.area_km2))
        _w_f64_array(buf, h.flow_acc)
        _w_i8_array(buf, h.directions)
        _w_f64_array(buf, h.filled_dem)
        net = h.river_network
        _w_u8(buf, 1 if net is not None else 0)
        if net is not None:
            _w_i32(buf, net.width)
            _w_i32(buf, net.height)
            _w_i32(buf, len(net.rivers))
            for r in net.rivers:
                _w_i32(buf, len(r.points))
                for p in r.points:
                    _w_f64(buf, float(p.x))
                    _w_f64(buf, float(p.y))
                    _w_f64(buf, float(p.flow))
                    _w_i32(buf, int(p.strahler))
                _w_i32(buf, r.source_idx)
                _w_i32(buf, r.outlet_idx)
                _w_i32_array(buf, r.parent_indices)
            _w_i32(buf, len(net.node_grid))
            for key, (nx, ny) in net.node_grid.items():
                _w_i32(buf, int(key))
                _w_i32(buf, int(nx))
                _w_i32(buf, int(ny))
    _w_i32(buf, len(data.subdiv_ranges))
    for zone, (p10, p90) in data.subdiv_ranges.items():
        _w_i32(buf, int(zone))
        _w_f64(buf, float(p10))
        _w_f64(buf, float(p90))
    _w_i32(buf, len(data._chunk_climate))
    for (cx, cy), (temp, rain, sea_temp, zone) in data._chunk_climate.items():
        _w_i32(buf, int(cx))
        _w_i32(buf, int(cy))
        _w_f64(buf, float(temp))
        _w_f64(buf, float(rain))
        _w_f64(buf, float(sea_temp))
        _w_i32(buf, int(zone))
    return zlib.compress(buf.getvalue(), 9)


def deserialize_continent(raw: bytes) -> "ContinentData | None":
    """压缩字节 → ContinentData。

    Returns:
        ContinentData；格式/版本不符、数据损坏或截断时返回 None
        （调用方据此重新生成并覆盖缓存）。
    """
    # 惰性导入避免循环依赖（hydrology/streamlines 不依赖本模块，
    # 但本模块在生成路径中才用到它们）
    from .hydrology import HydrologyData, LakeBasin
    from .streamlines import RiverNetwork, River, RiverPoint
    try:
        blob = zlib.decompress(raw)
        r = _Reader(blob)
        if r._take(len(_MAGIC)) != _MAGIC:
            return None
        if r.u8() != CONTINENT_CACHE_VERSION:
            return None
        seed = r.i64()
        width = r.i32()
        height = r.i32()
        cell_size = r.f64()
        land_ratio = r.f64()
        n = width * height
        if n <= 0:
            return None
        land_mask = [v != 0 for v in r.i8_array()]
        elevation = r.f64_array()
        river_width = r.f64_array()
        if len(land_mask) != n or len(elevation) != n or len(river_width) != n:
            return None
        hydrology = None
        if r.u8():
            basin_count = r.i32()
            if basin_count < 0:
                return None
            lake_basins = []
            for _ in range(basin_count):
                lake_basins.append(LakeBasin(
                    cells=r.i32_array(),
                    surface_elev=r.f64(),
                    area_km2=r.f64(),
                ))
            # 原数据结构：flow_acc/filled_dem 为 list，directions 为 list
            # （与 Hydrologydata 定义一致）；elevation/river_width 为 array
            flow_acc = r.f64_array().tolist()
            directions = r.i8_array()
            filled_dem = r.f64_array().tolist()
            if (
                len(flow_acc) != n
                or len(directions) != n
                or len(filled_dem) != n
            ):
                return None
            river_network = None
            if r.u8():
                net_w = r.i32()
                net_h = r.i32()
                river_count = r.i32()
                if river_count < 0:
                    return None
                rivers = []
                for _ in range(river_count):
                    pts_n = r.i32()
                    if pts_n < 0:
                        return None
                    points = []
                    for _ in range(pts_n):
                        points.append(RiverPoint(
                            x=r.f64(), y=r.f64(), flow=r.f64(),
                            strahler=r.i32(),
                        ))
                    rivers.append(River(
                        points=points,
                        source_idx=r.i32(),
                        outlet_idx=r.i32(),
                        parent_indices=r.i32_array(),
                    ))
                grid_count = r.i32()
                if grid_count < 0:
                    return None
                node_grid = {}
                for _ in range(grid_count):
                    key = r.i32()
                    node_grid[key] = (r.i32(), r.i32())
                river_network = RiverNetwork(
                    width=net_w, height=net_h,
                    rivers=rivers, node_grid=node_grid,
                )
            hydrology = HydrologyData(
                lake_basins=lake_basins, flow_acc=flow_acc,
                directions=directions, filled_dem=filled_dem,
                river_network=river_network,
            )
        subdiv_count = r.i32()
        if subdiv_count < 0:
            return None
        subdiv_ranges = {}
        for _ in range(subdiv_count):
            zone = r.i32()
            subdiv_ranges[zone] = (r.f64(), r.f64())
        climate_count = r.i32()
        if climate_count < 0:
            return None
        chunk_climate = {}
        for _ in range(climate_count):
            cx = r.i32()
            cy = r.i32()
            chunk_climate[(cx, cy)] = (r.f64(), r.f64(), r.f64(), r.i32())
        return ContinentData(
            seed=int(seed),
            grid_width=width, grid_height=height, cell_size=cell_size,
            land_ratio=land_ratio,
            land_mask=land_mask, elevation_field=elevation,
            river_width=river_width, hydrology=hydrology,
            subdiv_ranges=subdiv_ranges, _chunk_climate=chunk_climate,
        )
    except (struct.error, zlib.error, ValueError, IndexError) as exc:
        # 截断/篡改数据 → 缓存失效重新生成（有日志，便于区分真 bug）
        logger.warning("大陆缓存反序列化失败（重新生成）: %s", exc)
        return None


@dataclass
class ContinentParams:
    """大陆生成参数。

    Args:
        width_km: 大陆东西宽度 (km)。
        height_km: 大陆南北高度 (km)。
        sample_resolution: 层1采样分辨率 (m/采样点)。
        land_ratio: 目标陆地比例 [0-1]。
    """

    width_km: float = 100.0
    height_km: float = 60.0
    sample_resolution: float = 100.0
    land_ratio: float = 0.55

    def __repr__(self) -> str:
        return (
            f"ContinentParams({self.width_km:.0f}×{self.height_km:.0f}km, "
            f"res={self.sample_resolution:.0f}m, "
            f"land={self.land_ratio:.0%})"
        )


@dataclass
class ContinentData:
    """层1生成结果 — 宏观场数据。

    Attributes:
        grid_width: 网格宽度（100m/格）。
        grid_height: 网格高度（100m/格）。
        cell_size: 每格对应的世界距离 (m)，默认 100。
        seed: 生成所用种子。
        land_mask: 行优先布尔数组，True=陆地。
        elevation_field: 行优先海拔数组 (m)，100m 分辨率。
        river_width: 河流+湖泊宽度场 (m)，100m 分辨率。
        hydrology: 水文数据（流向、累积、湖盆、流线河网）。
        subdiv_ranges: 群系细分值域 {ClimateZone: (P10, P90)}。
        _chunk_climate: chunk 级气候缓存，由 generate() 末尾填充。
            通过 get_chunk_climate(cx, cy) 查询，返回
            (mean_temp, annual_rainfall, sea_level_temp, zone_int)。
    """

    grid_width: int
    grid_height: int
    cell_size: float
    seed: int
    # 生成参数快照（land_ratio）：缓存校验用——大陆是 (seed, land_ratio)
    # 的确定性函数，同 seed 不同 land_ratio 必须重新生成。
    land_ratio: float = 0.55

    land_mask: list[bool] = field(default_factory=list)
    elevation_field: Union[list[float], "array[float]"] = field(
        default_factory=lambda: array('d')
    )
    river_width: Union[list[float], "array[float]"] = field(
        default_factory=lambda: array('d')
    )
    hydrology: "HydrologyData | None" = None
    subdiv_ranges: dict[int, tuple[float, float]] = field(default_factory=dict)
    # chunk 级气候: {(cx, cy): (mean_temp, annual_rainfall, sea_level_temp, climate_zone_int)}
    _chunk_climate: dict = field(default_factory=dict, repr=False)

    def get_chunk_climate(
        self, cx: int, cy: int,
    ) -> tuple[float, float, float, int]:
        """查询 chunk 中心的校准后气候属性。

        Returns:
            (mean_temp, annual_rainfall, sea_level_temp, climate_zone)：
            越界（地图界限外）返回一致的极地深海默认值
            (-20, 0, -20, POLAR_TUNDRA)——地图为有界矩形，界限外
            统一视为极地深海，避免各字段自相矛盾（此前默认 zone=0
            即热带雨林，与 -20°C 温度/深海海拔矛盾）。
        """
        key = (cx, cy)
        if key in self._chunk_climate:
            return self._chunk_climate[key]
        logger.debug(
            "get_chunk_climate: chunk (%d,%d) 超出地图界限，返回极地深海默认",
            cx, cy,
        )
        return -20.0, 0.0, -20.0, int(ClimateZone.POLAR_TUNDRA)

    def __repr__(self) -> str:
        land = sum(1 for v in self.land_mask if v)
        total = len(self.land_mask)
        ratio = land / total if total > 0 else 0
        return (
            f"ContinentData({self.grid_width}×{self.grid_height}, "
            f"cell={self.cell_size:.0f}m, land={ratio:.1%})"
        )

    def _grid_index(self, world_x: float, world_y: float) -> int | None:
        """世界 tile 坐标（米）→ 宏观场格索引（1 格 = 分辨率米）。

        换算：格 = 米 / CONTINENT_SAMPLE_RESOLUTION_M。越界返回 None。
        """
        gx = int(world_x / CONTINENT_SAMPLE_RESOLUTION_M)
        gy = int(world_y / CONTINENT_SAMPLE_RESOLUTION_M)
        if 0 <= gx < self.grid_width and 0 <= gy < self.grid_height:
            return gy * self.grid_width + gx
        return None

    def is_land(self, world_x: float, world_y: float) -> bool:
        """查询世界坐标是否为陆地。越界返回 False。"""
        idx = self._grid_index(world_x, world_y)
        if idx is None or idx >= len(self.land_mask):
            return False
        return self.land_mask[idx]

    def sample_altitude(self, world_x: float, world_y: float) -> float:
        """从宏观海拔场采样（最近邻）。越界返回默认海洋深度。

        Args:
            world_x, world_y: 世界 tile 坐标（米；换算见 _grid_index）。
        """
        idx = self._grid_index(world_x, world_y)
        if idx is None or idx >= len(self.elevation_field):
            return -3500.0
        return self.elevation_field[idx]

    def sample_altitude_bilinear(self, world_x: float, world_y: float) -> float:
        """双线性插值采样宏观海拔，消除 100m 网格的块状伪影。

        Args:
            world_x: 世界 tile X 坐标（米）。
            world_y: 世界 tile Y 坐标（米）。

        Returns:
            插值后的海拔 (m)。越界返回默认海洋深度。
        """
        # 网格空间中的连续坐标（米 → 格：÷ 分辨率）
        gx = world_x / CONTINENT_SAMPLE_RESOLUTION_M - 0.5
        gy = world_y / CONTINENT_SAMPLE_RESOLUTION_M - 0.5

        x0 = int(gx)
        y0 = int(gy)
        x1, y1 = x0 + 1, y0 + 1

        # 越界检查
        if (x0 < 0 or x1 >= self.grid_width or
                y0 < 0 or y1 >= self.grid_height):
            return self.sample_altitude(world_x, world_y)  # 回退最近邻

        tx = gx - x0
        ty = gy - y0

        # 四个角的值
        elev = self.elevation_field
        gw = self.grid_width
        v00 = elev[y0 * gw + x0]
        v10 = elev[y0 * gw + x1]
        v01 = elev[y1 * gw + x0]
        v11 = elev[y1 * gw + x1]

        # 双线性插值
        v0 = v00 + (v10 - v00) * tx
        v1 = v01 + (v11 - v01) * tx
        return v0 + (v1 - v0) * ty

    def sample_river_width(self, world_x: float, world_y: float) -> float:
        """双线性插值采样河流宽度 (m)，消除 100m 网格块状伪影。

        Args:
            world_x: 世界 tile X 坐标。
            world_y: 世界 tile Y 坐标。

        Returns:
            插值后的河流宽度 (m)，0=无河流。越界返回 0。
        """
        if not self.river_width:
            return 0.0

        gx = world_x / CONTINENT_SAMPLE_RESOLUTION_M - 0.5
        gy = world_y / CONTINENT_SAMPLE_RESOLUTION_M - 0.5
        x0 = int(gx)
        y0 = int(gy)
        x1, y1 = x0 + 1, y0 + 1

        if (x0 < 0 or x1 >= self.grid_width or
                y0 < 0 or y1 >= self.grid_height):
            return 0.0

        tx = gx - x0
        ty = gy - y0
        rw = self.river_width
        gw = self.grid_width
        v00 = rw[y0 * gw + x0]
        v10 = rw[y0 * gw + x1]
        v01 = rw[y1 * gw + x0]
        v11 = rw[y1 * gw + x1]
        v0 = v00 + (v10 - v00) * tx
        v1 = v01 + (v11 - v01) * tx
        return v0 + (v1 - v0) * ty


class ContinentGenerator:
    """层1全局大陆生成器。

    每个 seed 独立生成一个 ContinentData。
    线程安全：generate() 创建所有临时状态，无共享可变状态。
    """

    def __init__(
        self,
        seed: int = 0,
        *,
        params: ContinentParams | None = None,
    ) -> None:
        """初始化生成器。

        Args:
            seed: 世界种子。
            params: 生成参数。
        """
        self._seed = seed
        self._params = params or ContinentParams()

    def __repr__(self) -> str:
        return f"ContinentGenerator(seed={self._seed})"

    @property
    def _grid_width(self) -> int:
        return int(self._params.width_km * 1000 / self._params.sample_resolution)

    @property
    def _grid_height(self) -> int:
        return int(self._params.height_km * 1000 / self._params.sample_resolution)

    # ── 主入口 ──────────────────────────────────────────────

    # 生成阶段名（进度广播用，前端按此显示阶段文案）
    STAGE_ELEVATION = "elevation"
    STAGE_CLIMATE = "climate"
    STAGE_EROSION = "erosion"
    STAGE_WATER = "water"
    STAGE_WIDTH = "width"
    STAGE_DONE = "done"

    def generate(
        self,
        progress_cb: "Callable[[str], None] | None" = None,
    ) -> ContinentData:
        """执行完整的层1生成管线。

        管线顺序：
          海拔 + 陆地掩码 → 海拔校准 → 气候（温度+降雨）→ 气候校准
          → 侵蚀（降雨驱动水流）→ 河流树 + 湖泊盆地提取

        校准步骤保证 8 档气候覆盖：海拔/降雨/温度场分别做保结构的
        分位数拉伸，确保值域覆盖各气候档位的判定阈值。

        Args:
            progress_cb: 可选阶段回调，每个生成阶段开始时以阶段名
                调用（STAGE_* 常量）。供前端进度条展示，缓存命中时
                不进入本方法（由调用方上报 STAGE_DONE）。

        Returns:
            ContinentData 宏观场。
        """
        def _report(stage: str) -> None:
            if progress_cb is not None:
                progress_cb(stage)

        w = self._grid_width
        h = self._grid_height

        # Step 1: 海拔 + 陆地掩码（湖泊由水文系统接管）
        _report(self.STAGE_ELEVATION)
        land_mask, elevation = self._generate_elevation(w, h)

        # Step 1b: 海拔校准 — 保证高山（≥2000m）存在
        self._ensure_elevation_range(elevation, land_mask)

        # Step 2: 气候（温度、降雨、气候带）—— 降雨在侵蚀之前生成
        _report(self.STAGE_CLIMATE)
        temp_field, rain_field, climate_field = (
            self._compute_climate(elevation, land_mask, w, h))

        # Step 2b-2e: 气候校准 + 重分类（合并为单次遍历）
        self._calibrate_climate_merged(
            elevation, temp_field, rain_field, land_mask, climate_field, w, h,
        )

        # Step 3: 侵蚀（降雨驱动水流累积）—— 提取完整水文状态
        _report(self.STAGE_EROSION)
        from .hydrology import erode, extract_lake_basins, HydrologyData
        erosion_result = erode(elevation, rain_field, w, h,
                               iterations=EROSION_ITERATIONS)

        # 用侵蚀后的海拔替换原始海拔（河流已雕刻，地形已塑形）
        elevation = erosion_result.dem

        # Step 4: 湖泊盆地提取
        _report(self.STAGE_WATER)
        lake_basins = extract_lake_basins(
            elevation, erosion_result.filled_dem, land_mask, w, h,
            min_size=LAKE_MIN_PIXELS,
        )

        # Step 4b: 流线河流网络 — RK4 沿海拔梯度场追踪自然弯曲流线
        from .streamlines import build_river_network
        river_network = build_river_network(
            elevation,
            erosion_result.directions, erosion_result.flow_acc,
            land_mask, w, h,
            threshold=RIVER_FLOW_THRESHOLD, min_length=RIVER_MIN_LENGTH,
        )

        hydrology = HydrologyData(
            lake_basins=lake_basins,
            flow_acc=erosion_result.flow_acc,
            directions=erosion_result.directions,
            filled_dem=erosion_result.filled_dem,
            river_network=river_network,
        )

        # Step 5: 河流宽度场（复用侵蚀+水文数据，避免重复计算）
        _report(self.STAGE_WIDTH)
        from .hydrology import compute_river_width
        river_width = compute_river_width(
            elevation, w, h,
            land_mask=land_mask, threshold=RIVER_WIDTH_THRESHOLD,
            directions=erosion_result.directions,
            flow_acc=erosion_result.flow_acc,
            lake_basins=lake_basins,
        )

        # Step 6: 兜底 — 保证 8 档气候覆盖（最后执行，不影响水文）
        self._inject_missing_climates(
            elevation, temp_field, rain_field, land_mask, climate_field, w, h,
        )

        # Step 7: 群系细分动态值域 — 每档内细分维度的 P10/P90
        subdiv_ranges = self._compute_subdiv_ranges(
            elevation, temp_field, rain_field, land_mask, climate_field, w, h,
        )

        # Step 8: 提取 chunk 级气候缓存（校准后值，供 tile_gen 等模块使用）
        chunk_climate: dict = {}
        for cy in range(h // 2):
            for cx in range(w // 2):
                idx = (cy * 2 + 1) * w + (cx * 2 + 1)
                alt = elevation[idx]
                temp = temp_field[idx]
                rain = rain_field[idx]
                zone = climate_field[idx]
                sea_temp = temp + alt * LAPSE_RATE / 1000.0
                chunk_climate[(cx, cy)] = (temp, rain, sea_temp, zone)

        _report(self.STAGE_DONE)
        return ContinentData(
            grid_width=w, grid_height=h,
            cell_size=self._params.sample_resolution,
            seed=self._seed,
            land_ratio=self._params.land_ratio,
            land_mask=land_mask,
            elevation_field=array('d', elevation),
            river_width=array('d', river_width),
            hydrology=hydrology,
            subdiv_ranges=subdiv_ranges,
            _chunk_climate=chunk_climate,
        )

    # ── 快速预览 ──────────────────────────────────────────

    # 预览采样分辨率 (m/格)：1000m 低分辨率缩略图（默认 100×60 网格，
    # 6000 格，秒级出图）。地形噪声场与分辨率无关（频率随采样分辨率
    # 缩放），低分辨率采样 = 同一地形的粗采样。
    PREVIEW_RESOLUTION_M: float = 1000.0

    def generate_preview(
        self, land_ratio: float,
        width_km: float | None = None, height_km: float | None = None,
    ) -> dict:
        """只生成海拔 + 陆地掩码的轻量预览（跳过气候/侵蚀/水文）。

        分位数校准保证预览陆地占比贴合 land_ratio（与真实生成同一
        校准逻辑）；海拔另做与真实生成相同的高海拔拉伸，山顶着色
        接近最终世界。未经侵蚀，预览海拔与最终世界略有偏差——
        仅作形状与占比参考的缩略图。

        低分辨率缩略图（1000m/格）：网格随尺寸缩放（60×36 → 60×36 格，
        150×90 → 150×90 格），地形变化率一致——尺寸只影响生成范围。

        Args:
            land_ratio: 目标陆地比例 [0-1]。
            width_km: 大陆东西宽度 (km)；None 用生成器参数（默认 100）。
            height_km: 大陆南北高度 (km)；None 用生成器参数（默认 60）。

        Returns:
            预览数据字典:
                {width, height, land_percent, elevation: [int 米] 行优先}
        """
        preview_params = ContinentParams(
            width_km=width_km if width_km is not None else self._params.width_km,
            height_km=height_km if height_km is not None else self._params.height_km,
            sample_resolution=self.PREVIEW_RESOLUTION_M,
            land_ratio=float(land_ratio),
        )
        gen = ContinentGenerator(seed=self._seed, params=preview_params)
        w = int(preview_params.width_km * 1000.0 / preview_params.sample_resolution)
        h = int(preview_params.height_km * 1000.0 / preview_params.sample_resolution)
        land_mask, elevation = gen._generate_elevation(w, h)
        self._ensure_elevation_range(elevation, land_mask)
        land_count = sum(1 for v in land_mask if v)
        return {
            "width": w,
            "height": h,
            "land_percent": round(land_count / max(1, w * h), 4),
            "elevation": [int(round(v)) for v in elevation],
        }

    # ── 气候覆盖校准 ──────────────────────────────────────

    @staticmethod
    def _percentile(sorted_vals: list[float], pct: float) -> float:
        """从已排序数组取分位数（线性插值）。"""
        n = len(sorted_vals)
        if n == 0:
            return 0.0
        pos = pct * (n - 1)
        lo = int(pos)
        hi = min(lo + 1, n - 1)
        frac = pos - lo
        return sorted_vals[lo] * (1 - frac) + sorted_vals[hi] * frac

    def _calibrate_climate_merged(
        self,
        elevation: list[float],
        temp: list[float],
        rain: list[float],
        land_mask: list[bool],
        climate_field: list[int],
        w: int, h: int,
    ) -> None:
        """合并气候校准 — 数据收集、排序、合并应用。

        在两次 O(N) 遍历中完成降雨/温度范围校准和气候带覆盖检查，
        对缺失气候档位通过共享排序找到最近邻区域注入种子。
        """
        from .climate import classify
        n = w * h

        # Phase 1: 一次遍历收集所有排序所需数据
        land_temps: list[float] = []
        land_rains: list[float] = []

        for i in range(n):
            if land_mask[i]:
                land_temps.append(temp[i])
                land_rains.append(rain[i])

        if not land_temps:
            return

        # Phase 2: 排序 + 计算校准参数
        land_temps.sort()
        land_rains.sort()

        # 降雨校准参数 (原 _ensure_rainfall_range)
        rain_p3 = self._percentile(land_rains, 0.03)
        rain_p10 = self._percentile(land_rains, 0.10)
        do_rain_cal = not (rain_p3 <= CLIMATE_CALIB_RAINFALL_REF or rain_p10 <= rain_p3)

        # 温度校准参数 (原 _ensure_temperature_range)
        temp_p2 = self._percentile(land_temps, 0.02)
        temp_p98 = self._percentile(land_temps, 0.98)
        do_temp_cal = (
            temp_p98 - temp_p2 >= 1.0
            and not (temp_p2 <= CLIMATE_CALIB_TEMP_MIN and temp_p98 >= CLIMATE_CALIB_TEMP_MAX)
        )

        # Phase 3: 应用降雨和温度校准，同时收集交叉校准所需数据
        # （交叉校准需要在温度校准之后收集，因为用校准后的温度分区）
        if do_rain_cal:
            rain_scale = (rain_p10 - CLIMATE_CALIB_RAINFALL_REF) / (rain_p10 - rain_p3)
        if do_temp_cal:
            temp_scale = (CLIMATE_CALIB_TEMP_MAX - CLIMATE_CALIB_TEMP_MIN) / (temp_p98 - temp_p2)
            temp_offset = CLIMATE_CALIB_TEMP_MIN - temp_p2 * temp_scale

        hot_rains: list[float] = []   # 热区(T>=20) 的降雨值
        cold_rains: list[float] = []  # 冷区(-5<=T<5) 的降雨值

        for i in range(n):
            is_land = land_mask[i]

            # 降雨校准（仅陆地）
            if is_land and do_rain_cal and rain[i] < rain_p10:
                rain[i] = max(0.0, CLIMATE_CALIB_RAINFALL_REF + (rain[i] - rain_p3) * rain_scale)

            # 温度校准（陆地+海洋统一应用，消除海陆边界跳变）
            if do_temp_cal:
                temp[i] = temp[i] * temp_scale + temp_offset

            if not is_land:
                continue

            # 收集校准后的交叉校准数据（仅陆地）
            t = temp[i]
            r = rain[i]
            if t >= CLIMATE_CALIB_HOT_THRESHOLD:
                hot_rains.append(r)
            elif CLIMATE_CALIB_COLD_RANGE[0] <= t < CLIMATE_CALIB_COLD_RANGE[1]:
                cold_rains.append(r)

        # Phase 4: 排序交叉校准数据 + 计算参数
        hot_rains.sort()
        cold_rains.sort()

        do_hot_cal = len(hot_rains) > 100
        hot_p20 = 0.0
        hot_max = 0.0
        if do_hot_cal:
            hot_p20 = hot_rains[int(len(hot_rains) * 0.20)]
            hot_max = hot_rains[-1]
            do_hot_cal = hot_max < CLIMATE_CALIB_HOT_RAINFALL_TARGET and hot_max > hot_p20

        do_cold_cal = len(cold_rains) > 100
        cold_p40 = 0.0
        cold_max = 0.0
        if do_cold_cal:
            cold_p40 = cold_rains[int(len(cold_rains) * 0.40)]
            cold_max = cold_rains[-1]
            do_cold_cal = cold_max < CLIMATE_CALIB_COLD_RAINFALL_TARGET and cold_max > cold_p40

        # Phase 5: 应用交叉校准 + 重分类（单次遍历）
        for i in range(n):
            if not land_mask[i]:
                continue

            t = temp[i]
            r = rain[i]

            # 交叉校准——使用已校准的温湿度值
            if do_hot_cal and t >= CLIMATE_CALIB_HOT_THRESHOLD and r > hot_p20:
                frac = (r - hot_p20) / (hot_max - hot_p20)
                rain[i] = CLIMATE_CALIB_HOT_STRETCH_PARAM[0] + frac * CLIMATE_CALIB_HOT_STRETCH_PARAM[1]
            elif (
                do_cold_cal
                and CLIMATE_CALIB_COLD_RANGE[0] <= t < CLIMATE_CALIB_COLD_RANGE[1]
                and r > cold_p40
            ):
                frac = (r - cold_p40) / (cold_max - cold_p40)
                rain[i] = CLIMATE_CALIB_COLD_STRETCH_PARAM[0] + frac * CLIMATE_CALIB_COLD_STRETCH_PARAM[1]

            # 重分类
            climate_field[i] = int(classify(temp[i], rain[i], elevation[i]))

    def _ensure_elevation_range(
        self, elevation: list[float], land_mask: list[bool],
    ) -> None:
        """海拔校准 — 拉伸高海拔尾部，保证陆地 P99 ≥ 2500m。

        只提升 top 10% 区域（P90 以上），低海拔不变，不影响海岸线。
        侵蚀不削平山顶，故侵蚀后仍保留 ≥2000m 的高山。
        原地修改 elevation。
        """
        land_vals = sorted(e for i, e in enumerate(elevation) if land_mask[i])
        if not land_vals:
            return
        p90 = self._percentile(land_vals, 0.90)
        p99 = self._percentile(land_vals, 0.99)
        target_p99 = ELEVATION_TARGET_P99
        if p99 >= target_p99 or p99 <= p90:
            return
        # 线性拉伸 (p90, p99] → (p90, target_p99]
        scale = (target_p99 - p90) / (p99 - p90)
        for i in range(len(elevation)):
            if land_mask[i] and elevation[i] > p90:
                elevation[i] = p90 + (elevation[i] - p90) * scale

    def _inject_missing_climates(
        self,
        elevation: list[float],
        temp: list[float],
        rain: list[float],
        land_mask: list[bool],
        climate_field: list[int],
        w: int, h: int,
    ) -> None:
        """兜底注入 — 对缺失气候档位，在最近邻区域创建最小气候种子。

        分位数拉伸解决了大部分 seed 的气候覆盖，但极端干旱/偏冷 seed
        仍可能缺失某些档位（温度-降雨空间分布天生不配合）。
        本函数在最接近目标档位阈值的陆地像素周围 3×3 区域
        直接设置参数，强制其落入目标档位。

        仅改 9 像素（0.09 km²），在 100×60km 大陆上几乎不可见，
        但保证大地图俯瞰时 8 种颜色都存在。在水文计算后执行，
        不影响河流树/湖泊/流向。
        """
        from .climate import classify
        n = w * h

        # 各档位目标参数（判定阈值中间值，确保落入该档位）
        targets = {
            0: (25.0, 2000.0, 200.0),    # 热带雨林
            1: (25.0, 1000.0, 200.0),    # 热带草原
            2: (20.0, 100.0, 200.0),     # 沙漠
            3: (15.0, 400.0, 200.0),     # 草原
            4: (12.0, 800.0, 200.0),     # 温带森林
            5: (-2.0, 500.0, 200.0),     # 亚寒带针叶林
            6: (-10.0, 300.0, 200.0),    # 极地苔原
            7: (10.0, 800.0, 2500.0),    # 高山
        }

        present = set(climate_field[i] for i in range(n) if land_mask[i])
        missing = list(set(targets.keys()) - present)

        if not missing:
            return

        # 单次扫描找到每个缺失档位的最近邻
        inv30 = 1.0 / 30.0
        inv2000 = 1.0 / 2000.0
        inv3000 = 1.0 / 3000.0
        best_i = {mz: -1 for mz in missing}
        best_d = {mz: float("inf") for mz in missing}

        for i in range(n):
            if not land_mask[i]:
                continue
            t = temp[i]
            r = rain[i]
            e = elevation[i]
            for mz in missing:
                tt, tr, ta = targets[mz]
                dt = (t - tt) * inv30
                dr = (r - tr) * inv2000
                de = (e - ta) * inv3000
                d = dt * dt + dr * dr + de * de
                if d < best_d[mz]:
                    best_d[mz] = d
                    best_i[mz] = i

        for mzone in missing:
            tt, tr, ta = targets[mzone]
            best_idx = best_i[mzone]
            if best_idx < 0:
                continue
            # 在候选位置周围 3×3 注入目标参数
            gx, gy = best_idx % w, best_idx // w
            for dy in range(-1, 2):
                for dx in range(-1, 2):
                    nx, ny = gx + dx, gy + dy
                    if 0 <= nx < w and 0 <= ny < h:
                        ni = ny * w + nx
                        if land_mask[ni]:
                            temp[ni] = tt
                            rain[ni] = tr
                            elevation[ni] = max(elevation[ni], ta)
                            climate_field[ni] = int(mzone)

    # ── 群系细分动态值域 ────────────────────────────────────

    @staticmethod
    def _compute_subdiv_ranges(
        elevation: list[float],
        temp: list[float],
        rain: list[float],
        land_mask: list[bool],
        climate_field: list[int],
        w: int, h: int,
    ) -> dict[int, tuple[float, float]]:
        """计算每气候档内细分维度的 P10/P90 值域。

        供 biome_membership 动态归一化用，使档内两子型比例均衡。
        沙漠档用 moisture 噪声细分，此处不计算（噪声值域固定 [-1,1]）。

        Returns:
            {ClimateZone_int: (P10, P90)} 每档的细分值域。
        """
        from .climate import ClimateZone
        from .biome import _SUBDIV_CONFIGS, _SUBDIV_MOISTURE

        # 按档收集细分维度值
        zone_vals: dict[int, list[float]] = {}
        for i in range(w * h):
            if not land_mask[i]:
                continue
            cz = climate_field[i]
            cfg = _SUBDIV_CONFIGS.get(ClimateZone(cz))
            if cfg is None or cfg.dimension == _SUBDIV_MOISTURE:
                continue
            if cfg.dimension == "rainfall":
                zone_vals.setdefault(cz, []).append(rain[i])
            elif cfg.dimension == "temperature":
                zone_vals.setdefault(cz, []).append(temp[i])
            elif cfg.dimension == "altitude":
                zone_vals.setdefault(cz, []).append(elevation[i])

        # P10/P90
        ranges: dict[int, tuple[float, float]] = {}
        for cz_int, vals in zone_vals.items():
            if len(vals) < 10:
                continue
            vals.sort()
            n = len(vals)
            p10 = vals[int(n * 0.10)]
            p90 = vals[int(n * 0.90)]
            if p90 - p10 < 1.0:
                # 值域过窄（档内几乎无变化），用 min/max
                p10 = vals[0]
                p90 = vals[-1]
            ranges[cz_int] = (p10, p90)

        return ranges

    # ── 海拔生成 ──────────────────────────────────────────

    def _generate_elevation(
        self, w: int, h: int,
    ) -> tuple[list[bool], list[float]]:
        """两层 Perlin 噪声 → 海拔 + 陆地。

        大陆轮廓层（低频）：决定海陆分布的大洲形状。
        地形细节层（高频）：叠加山地丘陵等局部变化。
        温和的中心倾向避免"四周陆地中间海洋"的环形分布。
        分位数校准确保陆地比例稳定在 land_ratio。
        """
        noise_terrain = PerlinNoise(self._seed + 10002)
        noise_continent = PerlinNoise(self._seed + 10003)

        terrain_freq = self._params.sample_resolution / 30000.0
        terrain_field = noise_terrain.octave_grid(
            0.5, 0.5, w, h, frequency=terrain_freq, octaves=5,
        )

        # 大陆轮廓层：绝对频率（1.5 周期 / 100km），与网格宽解耦。
        # 尺寸只改变生成范围——大尺寸下大陆轮廓自然延伸，
        # 而非把同一形状按比例缩放。
        continent_freq = self._params.sample_resolution / 100_000.0 * 1.5
        continent_field = noise_continent.octave_grid(
            0.5, 0.5, w, h, frequency=continent_freq, octaves=2,
        )

        n = w * h
        mixed = [0.0] * n
        inv_w = 1.0 / w
        inv_h = 1.0 / h

        # 用累加索引替代每像素的取模运算
        i = 0
        for y in range(h):
            dy = (y * inv_h - 0.5) * 2.0
            for x in range(w):
                dx = (x * inv_w - 0.5) * 2.0
                dist = center_distance(dx, dy)
                center = 1.0 - dist * 2.5
                if center < 0.0:
                    center = 0.0
                mixed[i] = (continent_field[i] * CONTINENT_BLEND_WEIGHT
                            + terrain_field[i] * TERRAIN_BLEND_WEIGHT
                            + center * CENTER_BIAS_WEIGHT)
                i += 1

        target = self._params.land_ratio
        sorted_vals = sorted(mixed)
        sea_idx = int(n * (1.0 - target))
        sea_idx = max(0, min(n - 1, sea_idx))
        sea_level = sorted_vals[sea_idx]

        # 列表推导 — 比 .append() 循环快
        elevation = [(m - sea_level) * ELEVATION_SCALE_FACTOR for m in mixed]
        land_mask = [e > 0 for e in elevation]
        return land_mask, elevation

    # ── 气候计算 ──────────────────────────────────────────

    def _compute_climate(
        self, elevation: list[float], land_mask: list[bool], w: int, h: int,
    ) -> tuple[list[float], list[float], list[int]]:
        """计算温度、降雨、气候带。

        温度 = 海平面纬度温度 - 海拔 × 9.0°C/km - 大陆度修正
        降雨 = 噪声 × 雨影因子（水分预算追踪）

        温度基线由 seed 决定的方向梯度给出，往某方向走持续变暖、反方向变冷。
        大陆度修正：距海越远年均温越低（海洋调节缺失，冬季降温主导年均值）。
        叠加微量噪声使气候带边界自然蜿蜒。
        """
        import math

        # seed → 随机温度梯度方向
        angle = _seed_angle(self._seed)
        gx = math.cos(angle)
        gy = math.sin(angle)

        lat_wiggle = PerlinNoise(self._seed + 99999)
        lat_wiggle_field = lat_wiggle.octave_grid(
            0.5, 0.5, w, h,
            frequency=self._params.sample_resolution / 15000.0, octaves=1,
        )

        rain_noise = PerlinNoise(self._seed + 88888)
        rain_field_raw = rain_noise.octave_grid(
            0.5, 0.5, w, h,
            frequency=self._params.sample_resolution / 25000.0, octaves=3,
        )

        rain_shadow = self._compute_rain_shadow(elevation, w, h)

        # 距海距离
        from .hydrology import _distance_to_ocean_c
        elev_arr = array('d', elevation)
        dist_to_ocean = _distance_to_ocean_c(elev_arr, w, h)

        # 气候计算（温度、降雨、气候分类）
        from .hydrology import _compute_climate_c
        lat_arr = array('d', lat_wiggle_field)
        rain_raw_arr = array('d', rain_field_raw)
        shadow_arr = array('d', rain_shadow)
        temp_field, rain_field, climate_field = _compute_climate_c(
            elev_arr, lat_arr, rain_raw_arr, shadow_arr, dist_to_ocean,
            w, h, gx, gy,
            continentality_k=CONTINENTALITY_K,
            continentality_d0=CONTINENTALITY_D0_KM,
            cell_size_km=self._params.sample_resolution / 1000.0,
        )

        # convert climate to int (from array)
        climate_field = [int(c) for c in climate_field]

        return temp_field, rain_field, climate_field

    def _compute_rain_shadow(
        self, elevation: list[float], w: int, h: int,
    ) -> list[float]:
        """雨影因子：万向盛行风 + 水分预算追踪。

        seed 决定连续风向角 [0, 2π)，主风向（80%）+ 次风向偏移 45°（20%）混合。
        使用水分预算模型：风携带水汽从海岸向内陆移动，
        地形抬升消耗水汽 → 背风面干燥。
        因子范围 [MIN_FACTOR, 1.0]，保证基础降水。
        """
        import math
        from .hydrology import _rain_shadow_omnidirectional_c

        # seed → 连续风向角（与温度梯度相同的 Knuth 乘法哈希）
        wind_angle = _seed_angle(self._seed)

        # 次风向：偏移 45°，模拟环境风切变
        secondary_angle = wind_angle + math.pi / 4.0

        elev_arr = array('d', elevation)
        factors = _rain_shadow_omnidirectional_c(
            elev_arr, w, h,
            primary_angle=wind_angle,
            secondary_angle=secondary_angle,
            secondary_weight=RAINSHADOW_SECONDARY_WEIGHT,
            decay_length_km=RAINSHADOW_DECAY_KM,   # 抬升衰减距离 (km)
            cell_size_km=self._params.sample_resolution / 1000.0,
            min_factor=RAINSHADOW_MIN_FACTOR,
        )
        return factors.tolist()


__all__ = ["ContinentParams", "ContinentData", "ContinentGenerator"]
