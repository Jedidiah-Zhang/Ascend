"""Tile 级河流渲染 — 沿流线点集绘制自然蜿蜒的河道。

流线由 streamlines.py 的 RK4 积分产生,天然弯曲(弯曲度 1.5-3.0),
无需额外蜿蜒扰动。渲染只需沿点集画河道截面。

用法:
    from ascend.space.river_render import render_river_chunk
    render_river_chunk(tile_grid, world_x0, world_y0, hydrology, cont)
"""

import math

from ascend.config import RIVER_WIDTH_MIN, RIVER_WIDTH_MAX
from .terrain import TerrainType
from .tile_grid import TileGrid, TILE_MAP_SIZE
from .hydrology import river_width_log


def render_river_chunk(
    tile_grid: TileGrid,
    world_x0: int, world_y0: int,
    hydrology,  # HydrologyData
    continent,  # ContinentData
) -> None:
    """在 chunk 内渲染所有河流（流线网络）。

    Args:
        tile_grid: 要修改的 200×200 地形网格。
        world_x0, world_y0: chunk 左上角世界坐标(tile 单位)。
        hydrology: 层1 水文数据。
        continent: 层1 大陆数据(用于 cell_size 转换)。
    """
    if hydrology is None or hydrology.river_network is None:
        return

    _render_streamlines(
        tile_grid, world_x0, world_y0,
        hydrology.river_network, continent,
    )


# ── 流线渲染 ──────────────────────────────────────────────


def _render_streamlines(
    tile_grid: TileGrid,
    world_x0: int, world_y0: int,
    network,  # RiverNetwork
    continent,
) -> None:
    """沿流线点集渲染河道。

    流线坐标、世界坐标、chunk 内 tile 坐标三者 1:1（1 tile = 100m = 1 格点），
    无需单位换算。
    """
    from .streamlines import rivers_in_region

    size = TILE_MAP_SIZE

    # chunk 边界（tile 坐标）
    gx0 = world_x0
    gy0 = world_y0
    gx1 = world_x0 + size
    gy1 = world_y0 + size
    margin = 2.0  # 网格单位余量

    # 获取区域内河流段
    region_rivers = rivers_in_region(network, gx0, gy0, gx1, gy1, margin=margin)

    if not region_rivers:
        return

    max_acc = max(
        (p.flow for _, pts in region_rivers for p in pts),
        default=1.0,
    )

    for _, points in region_rivers:
        if len(points) < 2:
            # 单点:画圆
            if points:
                p = points[0]
                wx = p.x
                wy = p.y
                tx = int(wx - world_x0)
                ty = int(wy - world_y0)
                width = _river_width(continent, wx, wy, p.flow, max_acc)
                _fill_circle(tile_grid, tx, ty, _river_radius(width), size)
            continue

        # 沿流线点集画河道(流线已弯曲,无需额外蜿蜒)
        for p in points:
            wx = p.x
            wy = p.y
            tx = int(wx - world_x0)
            ty = int(wy - world_y0)
            width = _river_width(continent, wx, wy, p.flow, max_acc)
            radius = _river_radius(width)

            if 0 <= tx < size and 0 <= ty < size:
                _fill_circle(tile_grid, tx, ty, radius, size)

            # 连接相邻点(填充间隙)
            # points 是连续的,但步长可能 >1 tile,需插值填充

        # 插值填充点间间隙
        _fill_gaps(tile_grid, points, world_x0, world_y0,
                    continent, max_acc, size)


def _fill_gaps(
    tile_grid: TileGrid,
    points: list,
    world_x0: int, world_y0: int,
    continent,
    max_acc: float,
    size: int,
) -> None:
    """在相邻流线点间插值填充,确保河道连续无间隙。"""
    for i in range(1, len(points)):
        p0 = points[i - 1]
        p1 = points[i]

        # tile 坐标（流线点已是 tile/格点坐标）
        wx0 = p0.x
        wy0 = p0.y
        wx1 = p1.x
        wy1 = p1.y

        # tile 坐标
        tx0 = wx0 - world_x0
        ty0 = wy0 - world_y0
        tx1 = wx1 - world_x0
        ty1 = wy1 - world_y0

        dist = math.sqrt((tx1 - tx0) ** 2 + (ty1 - ty0) ** 2)
        steps = max(1, int(dist))

        width = _river_width(
            continent, (wx0 + wx1) * 0.5, (wy0 + wy1) * 0.5,
            (p0.flow + p1.flow) * 0.5, max_acc,
        )
        radius = _river_radius(width)

        for s in range(steps + 1):
            t = s / steps
            tx = int(tx0 + (tx1 - tx0) * t)
            ty = int(ty0 + (ty1 - ty0) * t)
            if 0 <= tx < size and 0 <= ty < size:
                _fill_circle(tile_grid, tx, ty, radius, size)


# ── 河道宽度 ──────────────────────────────────────────────


def _river_width(continent, wx: float, wy: float,
                 flow: float, max_acc: float) -> float:
    """河道宽度 (m)。

    优先采样层1河流宽度场（continent.sample_river_width）——与
    hydrology.compute_river_width 同源、与出生点避让同源、跨 chunk
    连续（消除按 chunk 局部 max 归一化的边界接缝）。
    场外点（插值为 0，如流线与宽度场的边界差）回退到对数流量公式，
    范围取 config 的 RIVER_WIDTH_MIN/MAX（2m~80m，与层1一致）。
    """
    field_w = continent.sample_river_width(wx, wy)
    if field_w > 0.0:
        return field_w
    if max_acc <= 0:
        return RIVER_WIDTH_MIN
    ratio = flow / max_acc
    return RIVER_WIDTH_MIN + (RIVER_WIDTH_MAX - RIVER_WIDTH_MIN) * river_width_log(ratio)


def _river_radius(width: float) -> int:
    """河道宽度 (m) → 渲染半径 (tile，1 tile = 100m)。

    宽 40m 河 → 1 tile 水；80m 河 → 2 tile。深浅分级由 depth 场
    派生（issue #42 单 WATER，无深浅枚举）。
    """
    return max(1, int(width / 50 + 0.5))


def _fill_circle(
    tile_grid: TileGrid,
    cx: int, cy: int,
    radius: int,
    size: int,
) -> None:
    """以 (cx, cy) 为中心填充河道圆。

    半径内 → WATER；河岸外侧一环（radius < dist ≤ radius+1）→ 沃土
    （窄河的低频距水场可能捕捉不到岸带，render 兜底；湖岸沃土过渡
    由分类的距水带处理）。
    """
    for dy in range(-radius, radius + 1):
        for dx in range(-radius, radius + 1):
            nx, ny = cx + dx, cy + dy
            if not (0 <= nx < size and 0 <= ny < size):
                continue
            dist = math.sqrt(dx * dx + dy * dy)
            if dist > radius + 1.0:
                continue
            if dist <= radius:
                tile_grid.set(nx, ny, TerrainType.WATER)
            else:  # radius < dist <= radius + 1.0 — 河岸沃土
                if tile_grid.get(nx, ny) != TerrainType.WATER:
                    tile_grid.set(nx, ny, TerrainType.FERTILE_SOIL)
__all__ = ["render_river_chunk"]
