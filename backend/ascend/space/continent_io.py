"""大陆缓存 I/O — 层1 宏观场的二进制序列化。

从 continent.py 拆出：缓存随档分发（存档可分享），pickle 反序列化
可执行任意代码（恶意分享存档 = 加载即 RCE）；本模块只解析
struct/array 字节，无代码执行面，截断/篡改数据一律拒绝。

序列化布局见 CONTINENT_CACHE_VERSION 下方注释（显式二进制 schema）。
"""

import io
import struct
import zlib
from array import array

from .continent_data import ContinentData
from ascend.log import get_logger

logger = get_logger(__name__)

# 大陆缓存格式版本：仅标识当前二进制格式，不追溯历史版本——
# 格式变更时保持递增，任何版本字节不匹配的旧缓存一律
# 反序列化失败 → 重新生成。生成算法/调参变化不使缓存失效
# （每个存档的大陆在创建时定案），头部 gen_fingerprint 字段
# 仅用于加载时的漂移诊断（告警 + continent status 查询）。
CONTINENT_CACHE_VERSION: int = 1

# ── 二进制序列化（显式 schema，非 pickle） ────────────────
# 布局（小端）:
#   magic "ASCNT" + version u8
#   gen_fingerprint  u32 字节长度 + utf-8 字节（生成环境指纹，诊断用）
#   seed 32B 大端（256-bit 世界种子，0..2**256-1 全量序列化）
#   grid_width i32, grid_height i32, cell_size f64
#   land_ratio f64
#   land_mask      u32 n + n×u8        （布尔掩码按 0/1 字节）
#   elevation      u32 n + n×f64
#   river_width    u32 n + n×f64
#   water_distance u32 n + n×f64       （v2 新增，距水距离场 m，0=水体）
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


def _w_seed(buf: io.BytesIO, v: int) -> None:
    """256-bit 世界种子（0..2**256-1）→ 32 字节大端。

    `to_bytes(32)` 需 0 <= v < 2**256，与 manifest.SEED_MAX 契约一致。
    """
    buf.write(int(v).to_bytes(32, "big"))


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


def _w_str(buf: io.BytesIO, value: str) -> None:
    """utf-8 字符串写入（u32 字节长度前缀）。"""
    raw = value.encode("utf-8")
    _w_i32(buf, len(raw))
    if raw:
        buf.write(raw)


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

    def seed(self) -> int:
        """读取 32 字节大端 256-bit 世界种子。"""
        return int.from_bytes(self._take(32), "big")

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

    def string(self) -> str:
        n = self.i32()
        if n < 0:
            raise ValueError("非法长度")
        return self._take(n).decode("utf-8")


def serialize_continent(data: ContinentData) -> bytes:
    """ContinentData → 压缩字节（大陆缓存落盘格式）。

    大陆宏观场是 seed 的确定性函数，生成耗时 5-30s（侵蚀+水文模拟）；
    落盘缓存后读档直接反序列化恢复，秒级完成。

    显式二进制 schema（见模块注释）：无代码执行面，随档分发安全。
    """
    buf = io.BytesIO()
    buf.write(_MAGIC)
    _w_u8(buf, CONTINENT_CACHE_VERSION)
    _w_str(buf, data.gen_fingerprint)
    _w_seed(buf, int(data.seed))
    _w_i32(buf, data.grid_width)
    _w_i32(buf, data.grid_height)
    _w_f64(buf, float(data.cell_size))
    _w_f64(buf, float(data.land_ratio))
    _w_land_mask(buf, data.land_mask)
    _w_f64_array(buf, data.elevation_field)
    _w_f64_array(buf, data.river_width)
    _w_f64_array(buf, data.water_distance)
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
        gen_fingerprint = r.string()
        seed = r.seed()
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
        water_distance = r.f64_array()
        if (
            len(land_mask) != n
            or len(elevation) != n
            or len(river_width) != n
            or len(water_distance) != n
        ):
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
            gen_fingerprint=gen_fingerprint,
            land_mask=land_mask, elevation_field=elevation,
            river_width=river_width, water_distance=water_distance,
            hydrology=hydrology,
            subdiv_ranges=subdiv_ranges, _chunk_climate=chunk_climate,
        )
    except (struct.error, zlib.error, ValueError, IndexError) as exc:
        # 截断/篡改数据 → 缓存失效重新生成（有日志，便于区分真 bug）
        logger.warning("大陆缓存反序列化失败（重新生成）: %s", exc)
        return None


def read_continent_header(raw: bytes) -> "tuple[int, str] | None":
    """轻量读取缓存头部（格式版本 + 生成环境指纹），不解析场体。

    供 continent status 诊断命令使用：仅解压头部字节，
    不反序列化整个大陆场。

    Args:
        raw: continent.bin 原始字节（zlib 压缩）。

    Returns:
        (版本号, 指纹字符串)；格式非法/损坏/旧版本无指纹字段时
        返回 (版本, "")，magic 不符或不可解压时返回 None。
    """
    try:
        head = zlib.decompressobj().decompress(raw, 64 + 256)
    except zlib.error:
        return None
    try:
        r = _Reader(head)
        if r._take(len(_MAGIC)) != _MAGIC:
            return None
        version = r.u8()
        if version != CONTINENT_CACHE_VERSION:
            # 非当前格式（旧缓存/未来版本产物）：指纹无从解析
            return (version, "")
        return (version, r.string())
    except (struct.error, ValueError, IndexError):
        return None
