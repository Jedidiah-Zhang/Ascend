"""Tile 级河流渲染 — 沿流线点集绘制自然蜿蜒的河道。

流线由 streamlines.py 的 RK4 积分产生,天然弯曲(弯曲度 1.5-3.0),
无需额外蜿蜒扰动。渲染只需沿点集画河道截面。

优先使用 RiverNetwork(流线),回退到 RiverTree(D8 节点)兼容旧管线。

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
    """在 chunk 内渲染所有河流。

    优先使用流线网络(RiverNetwork),回退到 D8 河流树(RiverTree)。

    Args:
        tile_grid: 要修改的 200×200 地形网格。
        world_x0, world_y0: chunk 左上角世界坐标(tile 单位)。
        hydrology: 层1 水文数据。
        continent: 层1 大陆数据(用于 cell_size 转换)。
        seed: 随机种子(流线模式未使用,兼容签名)。
    """
    if hydrology is None:
        return

    # 优先:流线网络
    if hydrology.river_network is not None:
        _render_streamlines(
            tile_grid, world_x0, world_y0,
            hydrology.river_network, continent,
        )
        return

    # 回退:D8 河流树(旧管线)
    if hydrology.river_tree is not None and hydrology.river_tree.nodes:
        _render_tree_legacy(
            tile_grid, world_x0, world_y0,
            hydrology.river_tree, hydrology.flow_acc,
            hydrology.directions, continent.cell_size,
            continent.grid_width, seed=seed,
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
    """对数流量→河道宽度 (m)。

    相比旧版缩减最大宽度(40m→15m),避免河道过宽。
    """
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


# ── 旧管线兼容(D8 河流树)──────────────────────────────────


def _render_tree_legacy(
    tile_grid: TileGrid,
    world_x0: int, world_y0: int,
    river_tree,
    flow_acc: list[float],
    directions: list[int],
    cell_size: float,
    grid_w: int,
    *,
    seed: int = 0,
) -> None:
    """旧 D8 河流树渲染(兼容回退路径)。

    保留原有 Catmull-Rom + 正弦蜿蜒逻辑,仅在流线网络不可用时使用。
    """
    size = TILE_MAP_SIZE
    world_x1 = world_x0 + size
    world_y1 = world_y0 + size
    margin = cell_size
    max_acc = max(flow_acc) if flow_acc else 1.0

    node_idx_in_chunk: dict[int, int] = {}
    node_data: list[tuple[float, float, float]] = []

    for i, node in enumerate(river_tree.nodes):
        wx = node.px * cell_size + cell_size / 2
        wy = node.py * cell_size + cell_size / 2
        if world_x0 - margin <= wx < world_x1 + margin and \
           world_y0 - margin <= wy < world_y1 + margin:
            node_idx_in_chunk[i] = len(node_data)
            width = _river_width(node.flow, max_acc)
            node_data.append((wx, wy, width))

    if not node_data:
        return

    groups = _group_connected(node_idx_in_chunk, river_tree)

    for group in groups:
        path_world = _sort_path(group, node_data, node_idx_in_chunk, river_tree)
        avg_width = sum(w for _, _, w in path_world) / len(path_world)

        if len(path_world) < 2:
            wx, wy, _ = node_data[node_idx_in_chunk[group[0]]]
            tx, ty = int(wx - world_x0), int(wy - world_y0)
            _fill_circle(tile_grid, tx, ty, int(avg_width / 2) + 1, avg_width, size)
            continue

        spacing = max(2.0, avg_width / 3.0)
        dense_path = _catmull_rom_path(path_world, spacing=spacing)
        _paint_path_legacy(tile_grid, dense_path, world_x0, world_y0, avg_width, size)


def _group_connected(node_idx_in_chunk: dict[int, int], river_tree) -> list[list[int]]:
    chunk_set = set(node_idx_in_chunk.keys())
    visited: set[int] = set()
    groups: list[list[int]] = []
    for start in chunk_set:
        if start in visited:
            continue
        group: list[int] = []
        stack = [start]
        while stack:
            ni = stack.pop()
            if ni in visited or ni not in chunk_set:
                continue
            visited.add(ni)
            group.append(ni)
            node = river_tree.nodes[ni]
            if node.parent >= 0 and node.parent in chunk_set:
                stack.append(node.parent)
            for child in node.children:
                if child in chunk_set:
                    stack.append(child)
        if group:
            groups.append(group)
    return groups


def _sort_path(group, node_data, node_idx_in_chunk, river_tree):
    group_set = set(group)
    roots = [ni for ni in group
             if river_tree.nodes[ni].parent < 0 or
             river_tree.nodes[ni].parent not in group_set]
    if not roots:
        roots = [group[0]]
    ordered: list[int] = []
    visited: set[int] = set()
    stack = list(roots)
    while stack:
        ni = stack.pop()
        if ni in visited or ni not in group_set:
            continue
        visited.add(ni)
        ordered.append(ni)
        for child in river_tree.nodes[ni].children:
            if child in group_set:
                stack.append(child)
    ordered.reverse()
    return [node_data[node_idx_in_chunk[ni]] for ni in ordered]


def _catmull_rom_path(path, spacing=5.0):
    pts = [(x, y) for x, y, _ in path]
    n = len(pts)
    if n < 2:
        return pts
    if n == 2:
        x0, y0 = pts[0]
        x1, y1 = pts[1]
        seg_len = math.sqrt((x1 - x0) ** 2 + (y1 - y0) ** 2)
        steps = max(1, int(seg_len / spacing))
        return [(x0 + (x1 - x0) * i / steps, y0 + (y1 - y0) * i / steps)
                for i in range(steps + 1)]
    pts4 = [pts[0]] + pts + [pts[-1]]
    result: list[tuple[float, float]] = []
    for i in range(n - 1):
        p0, p1, p2, p3 = pts4[i], pts4[i + 1], pts4[i + 2], pts4[i + 3]
        seg_len = math.sqrt((p2[0] - p1[0]) ** 2 + (p2[1] - p1[1]) ** 2)
        steps = max(1, int(seg_len / spacing))
        for j in range(steps + 1):
            t = j / steps
            t2, t3 = t * t, t2 * t
            x = 0.5 * ((2 * p1[0]) + (-p0[0] + p2[0]) * t +
                       (2 * p0[0] - 5 * p1[0] + 4 * p2[0] - p3[0]) * t2 +
                       (-p0[0] + 3 * p1[0] - 3 * p2[0] + p3[0]) * t3)
            y = 0.5 * ((2 * p1[1]) + (-p0[1] + p2[1]) * t +
                       (2 * p0[1] - 5 * p1[1] + 4 * p2[1] - p3[1]) * t2 +
                       (-p0[1] + 3 * p1[1] - 3 * p2[1] + p3[1]) * t3)
            result.append((x, y))
    result.append(pts[-1])
    return result


def _paint_path_legacy(tile_grid, path, world_x0, world_y0, width, size):
    if len(path) < 2:
        return
    total_len = sum(
        math.sqrt((path[i][0] - path[i - 1][0]) ** 2 +
                  (path[i][1] - path[i - 1][1]) ** 2)
        for i in range(1, len(path))
    )
    if total_len < 2:
        return
    dx = path[-1][0] - path[0][0]
    dy = path[-1][1] - path[0][1]
    main_len = math.sqrt(dx * dx + dy * dy) or 1
    perp_x, perp_y = -dy / main_len, dx / main_len
    meander_amp = max(3.0, width * 0.4)
    wavelength = 150.0
    phase = (path[0][0] * 0.73 + path[0][1] * 1.17) * 0.01
    radius = int(width / 2) + 1
    for wx, wy in path:
        t = math.sqrt((wx - path[0][0]) ** 2 + (wy - path[0][1]) ** 2)
        offset = meander_amp * math.sin(t / wavelength * 2.0 * math.pi + phase)
        cx = int(wx + perp_x * offset - world_x0)
        cy = int(wy + perp_y * offset - world_y0)
        if 0 <= cx < size and 0 <= cy < size:
            _fill_circle(tile_grid, cx, cy, radius, width, size)


__all__ = ["render_river_chunk"]
