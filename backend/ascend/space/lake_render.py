"""Tile 级湖泊渲染 — 在 200×200 chunk 内平整湖面并生成湿地过渡。

层1 提供湖泊盆地（cell 列表 + 湖面高程），层2 负责细化：
  1. 确定 chunk 内哪些 tile 属于湖泊（水面以下 → 水体）
  2. 水面以上的边缘地带 → 湿地过渡（MARSH）
  3. 根据湖面面积决定水深（大湖中心 = DEEP_WATER，边缘 = SHALLOW_WATER）

所有 chunk 共享同一个湖面高程（来自 LakeBasin.surface_elev），
保证跨 chunk 水面平坦连续。

用法:
    from ascend.space.lake_render import render_lake_chunk

    render_lake_chunk(tile_grid, world_x0, world_y0, lake_basins,
                      continent, seed)
"""

from ascend.config import LAKE_DEEP_AREA_KM2, LAKE_DEEP_DEPTH_M, LAKE_WETLAND_DEPTH_MAX
from .randomness import cell_hash
from .terrain import TerrainType
from .tile_grid import TileGrid, TILE_MAP_SIZE


def render_lake_chunk(
    tile_grid: TileGrid,
    world_x0: int, world_y0: int,
    lake_basins: list,  # list[LakeBasin]
    continent,  # ContinentData (for bilinear elevation sampling)
    seed: int,
    *,
    macro_elev_grid: list[float] | None = None,
) -> None:
    """在 chunk 内渲染湖泊：水面平整 + 边缘湿地。

    对于每个与 chunk 有重叠的湖泊盆地：
      1. 遍历 chunk 内所有 tile，检查其宏观海拔
      2. 海拔 < 湖面 → 水体（浅水/深水取决于深度）
      3. 海拔在湖面以上 0-2m → MARSH（湿地）
      4. 海拔接近湖面 → 自然湖岸线

    Args:
        tile_grid: 要修改的 200×200 地形网格。
        world_x0: chunk 左上角世界 X 坐标。
        world_y0: chunk 左上角世界 Y 坐标。
        lake_basins: 层1 湖泊盆地列表（LakeBasin 对象）。
        continent: ContinentData（用于海拔采样，macro_elev_grid 提供时可省略）。
        seed: 世界种子（湿地斑块随机源，保证不同世界湿地模式不同）。
        macro_elev_grid: 预计算的 chunk 宏观海拔网格（200×200 行优先），
                         提供后跳过逐 tile 双线性插值。
    """
    if not lake_basins:
        return

    size = TILE_MAP_SIZE

    # chunk 边界（tile 坐标，1 tile = 1 格点）
    chunk_x1 = world_x0 + size
    chunk_y1 = world_y0 + size

    # 检查哪些湖泊与此 chunk 有重叠
    for basin in lake_basins:
        surface = basin.surface_elev

        # 检查盆地是否有任何像素在此 chunk 内
        overlaps = False
        for ci in basin.cells:
            cx = ci % continent.grid_width + 0.5
            cy = ci // continent.grid_width + 0.5
            if world_x0 - 1 <= cx < chunk_x1 + 1 and \
               world_y0 - 1 <= cy < chunk_y1 + 1:
                overlaps = True
                break

        if not overlaps:
            continue

        # 在 chunk 内渲染此湖泊
        _flatten_lake_surface(tile_grid, world_x0, world_y0, surface,
                              basin.area_km2, basin.cells, continent,
                              macro_elev_grid)
        _generate_wetland_fringe(tile_grid, world_x0, world_y0, surface,
                                 basin.cells, continent, seed,
                                 macro_elev_grid)


def _basin_tiles(
    cells: list[int], gw: int, world_x0: int, world_y0: int, size: int,
) -> set[tuple[int, int]]:
    """湖盆地格点 cells → chunk 内局部 tile 坐标集合。

    cells 是格点索引（1 tile = 1 格点），映射到 chunk 内 (tx, ty)。
    只保留落在本 chunk 范围内的 tile。
    """
    tiles: set[tuple[int, int]] = set()
    for ci in cells:
        gx = ci % gw
        gy = ci // gw
        tx = gx - world_x0
        ty = gy - world_y0
        if 0 <= tx < size and 0 <= ty < size:
            tiles.add((tx, ty))
    return tiles


def _flatten_lake_surface(
    tile_grid: TileGrid,
    world_x0: int, world_y0: int,
    surface_elev: float,
    area_km2: float,
    cells: list[int],
    continent,
    macro_elev_grid: list[float] | None = None,
) -> None:
    """将湖面以下的 tile 标记为水体（仅限湖盆地 cells 覆盖范围）。

    深度判定：
      - 大湖（>1km²）中央 → DEEP_WATER
      - 小湖 / 边缘 → SHALLOW_WATER

    只处理湖盆地格点覆盖的 tile——湖面只淹没湖盆地所在区域，
    不受周边更高湖面影响（如高山湖不会淹没整片低地 chunk）。

    Args:
        tile_grid: 地形网格。
        world_x0, world_y0: chunk 左上角世界 tile 坐标。
        surface_elev: 湖面海拔 (m)。
        area_km2: 湖面面积 (km²)。
        cells: 湖盆地格点索引列表。
        continent: ContinentData（macro_elev_grid 提供时可省略）。
        macro_elev_grid: 预计算宏观海拔网格（行优先，200×200）。
    """
    size = tile_grid.size
    has_deep_zone = area_km2 > LAKE_DEEP_AREA_KM2
    gw = continent.grid_width

    for tx, ty in _basin_tiles(cells, gw, world_x0, world_y0, size):
        # 获取宏观海拔（优先使用预计算网格）
        if macro_elev_grid is not None:
            macro_elev = macro_elev_grid[ty * size + tx]
        else:
            wx = world_x0 + tx
            wy = world_y0 + ty
            macro_elev = continent.sample_altitude_bilinear(wx, wy)

        if macro_elev >= surface_elev:
            continue  # 高于湖面，不处理

        # 水面以下 → 水体
        depth = surface_elev - macro_elev

        if depth > LAKE_DEEP_DEPTH_M and has_deep_zone:
            tile_grid.set(tx, ty, TerrainType.DEEP_WATER)
        else:
            current = tile_grid.get(tx, ty)
            if current != TerrainType.DEEP_WATER:
                tile_grid.set(tx, ty, TerrainType.SHALLOW_WATER)


def _generate_wetland_fringe(
    tile_grid: TileGrid,
    world_x0: int, world_y0: int,
    surface_elev: float,
    cells: list[int],
    continent,
    seed: int,
    macro_elev_grid: list[float] | None = None,
) -> None:
    """在湖面边缘生成湿地（MARSH）。

    湖面以上 0-2m 的平坦区域 → 沼泽湿地。
    模拟自然湖泊周围的季节性淹没区。
    只处理湖盆地 cells 及其 1 格邻域——岸线湿地只在湖周出现。

    Args:
        tile_grid: 地形网格。
        world_x0, world_y0: chunk 左上角世界 tile 坐标。
        surface_elev: 湖面海拔 (m)。
        cells: 湖盆地格点索引列表。
        continent: ContinentData（macro_elev_grid 提供时可省略）。
        seed: 世界种子（湿地斑块随机源）。
        macro_elev_grid: 预计算宏观海拔网格（行优先，200×200）。
    """
    size = tile_grid.size
    gw = continent.grid_width

    basin = _basin_tiles(cells, gw, world_x0, world_y0, size)
    fringe: set[tuple[int, int]] = set(basin)
    for tx, ty in basin:
        for nx in (tx - 1, tx, tx + 1):
            for ny in (ty - 1, ty, ty + 1):
                if 0 <= nx < size and 0 <= ny < size:
                    fringe.add((nx, ny))

    for tx, ty in fringe:
            if macro_elev_grid is not None:
                macro_elev = macro_elev_grid[ty * size + tx]
            else:
                wx = world_x0 + tx
                wy = world_y0 + ty
                macro_elev = continent.sample_altitude_bilinear(wx, wy)

            # 湿地 = 湖面以上 0-2m
            wetland_depth = macro_elev - surface_elev
            if not (0.0 < wetland_depth <= LAKE_WETLAND_DEPTH_MAX):
                continue

            # 只有非水体、非山地 tile 可以变为湿地
            current = tile_grid.get(tx, ty)
            if current in (TerrainType.DEEP_WATER, TerrainType.SHALLOW_WATER,
                           TerrainType.MOUNTAIN_PEAK, TerrainType.STEEP_SLOPE):
                continue

            # 越接近湖面，越大概率是湿地（概率 = 1 - wetland_depth/2）
            prob_threshold = 1.0 - wetland_depth / LAKE_WETLAND_DEPTH_MAX
            wx = world_x0 + tx
            wy = world_y0 + ty
            hash_val = cell_hash(wx, wy, seed)

            if hash_val < prob_threshold:
                tile_grid.set(tx, ty, TerrainType.MARSH)


__all__ = ["render_lake_chunk"]
