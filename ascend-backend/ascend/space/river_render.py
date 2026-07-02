"""Tile 级河流渲染 — 在 200×200 chunk 内绘制自然蜿蜒的河道。

对每个穿过 chunk 的河流，沿扰动后节点构建连续路径，叠加正弦蜿蜒，
一次性画出平滑的整条河道——无分段转角。

用法:
    from ascend.space.river_render import render_river_chunk
    render_river_chunk(tile_grid, world_x0, world_y0, river_tree, flow_acc, cell_size)
"""

import math

from .terrain import TerrainType
from .tile_grid import TileGrid, TILE_MAP_SIZE


def render_river_chunk(
    tile_grid: TileGrid,
    world_x0: int, world_y0: int,
    river_tree,  # RiverTree
    flow_acc: list[float],
    directions: list[int],
    cell_size: float,
    grid_w: int,
    *,
    seed: int = 0,
) -> None:
    """在 chunk 内渲染所有河流。

    对每条穿过 chunk 的河：
      1. 收集它在 chunk 内的所有节点，按流向排序
      2. 从最上游到最下游构建一条连续路径
      3. 在路径上叠加正弦蜿蜒
      4. 沿路径画河道截面

    Args:
        tile_grid: 要修改的 200×200 地形网格。
        world_x0, world_y0: chunk 左上角世界坐标。
        river_tree: 层1 河流拓扑树。
        flow_acc: 层1 水流累积量场。
        cell_size: 层1 采样分辨率 (m)。
        grid_w: 层1 网格宽度。
    """
    if river_tree is None or not river_tree.nodes:
        return

    size = TILE_MAP_SIZE
    world_x1 = world_x0 + size
    world_y1 = world_y0 + size
    margin = cell_size
    max_acc = max(flow_acc) if flow_acc else 1.0

    # 找到在 chunk 内（含边界 margin）的所有节点
    node_idx_in_chunk: dict[int, int] = {}  # tree_index → local_idx
    node_data: list[tuple[float, float, float]] = []  # (wx, wy, width)

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

    # 找"河流段"：在 chunk 内连通的最大节点组
    # 使用 tree 的 parent/children 关系做连通性
    groups = _group_connected(node_idx_in_chunk, river_tree)

    # 对每组，构建连续路径并渲染
    for group in groups:
        # 按流向排序路径：从最上游到最下游
        path_world = _sort_path(group, node_data, node_idx_in_chunk, river_tree)

        # 计算平均宽度（在分支前，两者都需要）
        avg_width = sum(w for _, _, w in path_world) / len(path_world)

        if len(path_world) < 2:
            # 单个节点：只画圆
            wx, wy, _ = node_data[node_idx_in_chunk[group[0]]]
            tx, ty = int(wx - world_x0), int(wy - world_y0)
            _paint_river_tile(tile_grid, tx, ty, avg_width, size)
            continue

        # Catmull-Rom 样条插值 → 天然平滑过控制点
        spacing = max(2.0, avg_width / 3.0)
        dense_path = _catmull_rom_path(path_world, spacing=spacing)

        # 画连续河道
        _paint_path(tile_grid, dense_path, world_x0, world_y0, avg_width, size)


def _river_width(flow: float, max_acc: float) -> float:
    """对数流量→河道宽度 (m)。"""
    if max_acc <= 0:
        return 2.0
    ratio = flow / max_acc
    log_ratio = math.log(1.0 + ratio * 20.0) / math.log(21.0)
    return 2.0 + 38.0 * log_ratio


def _group_connected(
    node_idx_in_chunk: dict[int, int],
    river_tree,
) -> list[list[int]]:
    """将在 chunk 内的节点按树的连通性分组。

    返回：每组是 tree.nodes 索引的列表。
    """
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
            # 父节点
            if node.parent >= 0 and node.parent in chunk_set:
                stack.append(node.parent)
            # 子节点
            for child in node.children:
                if child in chunk_set:
                    stack.append(child)
        if group:
            groups.append(group)

    return groups


def _sort_path(
    group: list[int],
    node_data: list[tuple[float, float, float]],
    node_idx_in_chunk: dict[int, int],
    river_tree,
) -> list[tuple[float, float, float]]:
    """将连通组按从上游到下游排序。

    找组内 parent=-1 或在 chunk 外的节点作为下游终点，
    然后向上游追溯。
    """
    group_set = set(group)
    chunk_node_set = set(node_idx_in_chunk.keys())

    # 找"根"：parent 不在 group 中的节点（最下游）
    roots = [ni for ni in group
             if river_tree.nodes[ni].parent < 0 or
             river_tree.nodes[ni].parent not in group_set]

    if not roots:
        roots = [group[0]]

    # 从最下游的根向上游 BFS
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

    # ordered 是从下游到上游；反转为上游→下游
    ordered.reverse()

    return [node_data[node_idx_in_chunk[ni]] for ni in ordered]


def _catmull_rom_path(
    path: list[tuple[float, float, float]],
    spacing: float = 5.0,
) -> list[tuple[float, float]]:
    """Catmull-Rom 样条插值——天然平滑通过所有控制点。

    Args:
        path: [(wx, wy, width), ...] 控制点。
        spacing: 输出点间距 (m)。

    Returns:
        [(wx, wy), ...] 平滑插值后的路径点。
    """
    pts = [(x, y) for x, y, _ in path]
    n = len(pts)
    if n < 2:
        return pts
    if n == 2:
        # 只有两个点，直接线性插值
        x0, y0 = pts[0]
        x1, y1 = pts[1]
        seg_len = math.sqrt((x1-x0)**2 + (y1-y0)**2)
        steps = max(1, int(seg_len / spacing))
        return [(x0 + (x1-x0)*i/steps, y0 + (y1-y0)*i/steps) for i in range(steps + 1)]

    # 为 Catmull-Rom 添加虚拟端点
    pts4 = [pts[0]] + pts + [pts[-1]]

    result: list[tuple[float, float]] = []
    for i in range(n - 1):
        p0, p1, p2, p3 = pts4[i], pts4[i+1], pts4[i+2], pts4[i+3]
        seg_len = math.sqrt((p2[0]-p1[0])**2 + (p2[1]-p1[1])**2)
        steps = max(1, int(seg_len / spacing))
        for j in range(steps + 1):
            t = j / steps
            # Catmull-Rom 公式
            t2 = t * t
            t3 = t2 * t
            x = 0.5 * ((2*p1[0]) +
                       (-p0[0]+p2[0]) * t +
                       (2*p0[0]-5*p1[0]+4*p2[0]-p3[0]) * t2 +
                       (-p0[0]+3*p1[0]-3*p2[0]+p3[0]) * t3)
            y = 0.5 * ((2*p1[1]) +
                       (-p0[1]+p2[1]) * t +
                       (2*p0[1]-5*p1[1]+4*p2[1]-p3[1]) * t2 +
                       (-p0[1]+3*p1[1]-3*p2[1]+p3[1]) * t3)
            result.append((x, y))
    # 最后一个点
    result.append(pts[-1])
    return result


def _paint_path(
    tile_grid: TileGrid,
    path: list[tuple[float, float]],
    world_x0: int, world_y0: int,
    width: float,
    size: int,
) -> None:
    """沿密化路径画蜿蜒河道。

    对路径上的每个点画河道截面圆。
    在直线上叠加正弦扰动模拟自然蜿蜒。

    Args:
        tile_grid: 地形网格。
        path: [(wx, wy), ...] 世界坐标路径点。
        world_x0, world_y0: chunk 偏移。
        width: 河道宽度 (m)。
        size: 网格尺寸。
    """
    if len(path) < 2:
        return

    # 蜿蜒参数
    total_len = sum(
        math.sqrt((path[i][0] - path[i-1][0])**2 + (path[i][1] - path[i-1][1])**2)
        for i in range(1, len(path))
    )
    if total_len < 2:
        return

    # 计算平均流向（确定垂直方向）
    dx = path[-1][0] - path[0][0]
    dy = path[-1][1] - path[0][1]
    main_len = math.sqrt(dx*dx + dy*dy)
    if main_len < 1:
        main_len = 1
    perp_x = -dy / main_len
    perp_y = dx / main_len

    # 蜿蜒振幅：宽度越大越蜿，波长 ~150m
    meander_amp = max(3.0, width * 0.4)
    wavelength = 150.0
    phase = (path[0][0] * 0.73 + path[0][1] * 1.17) * 0.01

    radius = int(width / 2) + 1

    for wx, wy in path:
        # 当前位置沿路径的距离（近似）
        t = math.sqrt((wx - path[0][0])**2 + (wy - path[0][1])**2)

        # 正弦偏移
        offset = meander_amp * math.sin(
            t / wavelength * 2.0 * math.pi + phase)

        cx = int(wx + perp_x * offset - world_x0)
        cy = int(wy + perp_y * offset - world_y0)

        if 0 <= cx < size and 0 <= cy < size:
            _fill_circle(tile_grid, cx, cy, radius, width, size)


def _paint_river_tile(
    tile_grid: TileGrid,
    cx: int, cy: int,
    width: float,
    size: int,
) -> None:
    """单点画河道圆。"""
    radius = int(width / 2) + 1
    _fill_circle(tile_grid, cx, cy, radius, width, size)


def _fill_circle(
    tile_grid: TileGrid,
    cx: int, cy: int,
    radius: int,
    width: float,
    size: int,
) -> None:
    """以 (cx, cy) 为中心填充河道圆。"""
    deep_radius = int(width / 4) if width >= 15.0 else 0

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
                if current not in (TerrainType.DEEP_WATER,):
                    tile_grid.set(nx, ny, TerrainType.SHALLOW_WATER)
            elif dist <= radius + 1.5 and current not in (
                TerrainType.DEEP_WATER, TerrainType.SHALLOW_WATER,
                TerrainType.MOUNTAIN_PEAK, TerrainType.STEEP_SLOPE,
            ):
                tile_grid.set(nx, ny, TerrainType.FERTILE_SOIL)


__all__ = ["render_river_chunk"]
