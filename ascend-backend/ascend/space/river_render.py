"""Tile 级河流渲染 — 沿流线点集绘制自然蜿蜒的河道。

流线由 streamlines.py 的 RK4 积分产生,天然弯曲(弯曲度 1.5-3.0),
无需额外蜿蜒扰动。渲染只需沿点集画河道截面。

用法:
    from ascend.space.river_render import render_river_chunk
    render_river_chunk(tile_grid, world_x0, world_y0, hydrology, cont, seed=42)
"""

import math

from .terrain import TerrainType
from .tile_grid import TileGrid, TILE_MAP_SIZE


def render_river_chunk(
    tile_grid: TileGrid,
    world_x0: int, world_y0: int,
    hydrology,  # HydrologyData
    continent,  # ContinentData
    *,
    seed: int = 0,
) -> None:
    """在 chunk 内渲染所有河流（流线网络）。

    Args:
        tile_grid: 要修改的 200×200 地形网格。
        world_x0, world_y0: chunk 左上角世界坐标(tile 单位)。
        hydrology: 层1 水文数据。
        continent: 层1 大陆数据(用于 cell_size 转换)。
        seed: 随机种子(未使用,兼容签名)。
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

    流线坐标是网格单位(100m/格),需转世界坐标再转 chunk 内 tile 坐标。
    网格坐标 (gx, gy) → 世界坐标 (gx * cell_size, gy * cell_size) →
    tile 坐标 (wx - world_x0, wy - world_y0)。
    """
    from .streamlines import rivers_in_region

    size = TILE_MAP_SIZE
    cell_size = continent.cell_size

    # chunk 边界(网格坐标)
    gx0 = world_x0 / cell_size
    gy0 = world_y0 / cell_size
    gx1 = (world_x0 + size) / cell_size
    gy1 = (world_y0 + size) / cell_size
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
                wx = p.x * cell_size
                wy = p.y * cell_size
                tx = int(wx - world_x0)
                ty = int(wy - world_y0)
                width = _river_width(p.flow, max_acc)
                _fill_circle(tile_grid, tx, ty, int(width / 2) + 1, width, size)
            continue

        # 沿流线点集画河道(流线已弯曲,无需额外蜿蜒)
        for p in points:
            wx = p.x * cell_size
            wy = p.y * cell_size
            tx = int(wx - world_x0)
            ty = int(wy - world_y0)
            width = _river_width(p.flow, max_acc)
            radius = int(width / 2) + 1

            if 0 <= tx < size and 0 <= ty < size:
                _fill_circle(tile_grid, tx, ty, radius, width, size)

            # 连接相邻点(填充间隙)
            # points 是连续的,但步长可能 >1 tile,需插值填充

        # 插值填充点间间隙
        _fill_gaps(tile_grid, points, world_x0, world_y0,
                    cell_size, max_acc, size)


def _fill_gaps(
    tile_grid: TileGrid,
    points: list,
    world_x0: int, world_y0: int,
    cell_size: float,
    max_acc: float,
    size: int,
) -> None:
    """在相邻流线点间插值填充,确保河道连续无间隙。"""
    for i in range(1, len(points)):
        p0 = points[i - 1]
        p1 = points[i]

        # 世界坐标
        wx0 = p0.x * cell_size
        wy0 = p0.y * cell_size
        wx1 = p1.x * cell_size
        wy1 = p1.y * cell_size

        # tile 坐标
        tx0 = wx0 - world_x0
        ty0 = wy0 - world_y0
        tx1 = wx1 - world_x0
        ty1 = wy1 - world_y0

        dist = math.sqrt((tx1 - tx0) ** 2 + (ty1 - ty0) ** 2)
        steps = max(1, int(dist))

        width = _river_width((p0.flow + p1.flow) * 0.5, max_acc)
        radius = int(width / 2) + 1

        for s in range(steps + 1):
            t = s / steps
            tx = int(tx0 + (tx1 - tx0) * t)
            ty = int(ty0 + (ty1 - ty0) * t)
            if 0 <= tx < size and 0 <= ty < size:
                _fill_circle(tile_grid, tx, ty, radius, width, size)


# ── 河道宽度 ──────────────────────────────────────────────


def _river_width(flow: float, max_acc: float) -> float:
    """对数流量 → 河道宽度 (m)。"""
    if max_acc <= 0:
        return 2.0
    ratio = flow / max_acc
    log_ratio = math.log(1.0 + ratio * 20.0) / math.log(21.0)
    return 2.0 + 13.0 * log_ratio  # 2m~15m


def _fill_circle(
    tile_grid: TileGrid,
    cx: int, cy: int,
    radius: int,
    width: float,
    size: int,
) -> None:
    """以 (cx, cy) 为中心填充河道圆。

    深水(中心)→浅水(边缘)→沃土(岸边)自然过渡。
    """
    deep_radius = int(width / 4) if width >= 10.0 else 0

    for dy in range(-radius, radius + 1):
        for dx in range(-radius, radius + 1):
            nx, ny = cx + dx, cy + dy
            if not (0 <= nx < size and 0 <= ny < size):
                continue
            dist = math.sqrt(dx * dx + dy * dy)
            if dist > radius:
                continue
            current = tile_grid.get(nx, ny)
            if deep_radius > 0 and dist <= deep_radius:
                tile_grid.set(nx, ny, TerrainType.DEEP_WATER)
            elif dist <= radius:
                if current != TerrainType.DEEP_WATER:
                    tile_grid.set(nx, ny, TerrainType.SHALLOW_WATER)
            elif dist <= radius + 1.0 and current not in (
                TerrainType.DEEP_WATER, TerrainType.SHALLOW_WATER,
                TerrainType.MOUNTAIN_PEAK, TerrainType.STEEP_SLOPE,
            ):
                tile_grid.set(nx, ny, TerrainType.FERTILE_SOIL)
__all__ = ["render_river_chunk"]
