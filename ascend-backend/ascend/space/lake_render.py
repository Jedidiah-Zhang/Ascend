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
                       dem_macro, cell_size)
"""

import math

from .terrain import TerrainType
from .tile_grid import TileGrid, TILE_MAP_SIZE


def render_lake_chunk(
    tile_grid: TileGrid,
    world_x0: int, world_y0: int,
    lake_basins: list,  # list[LakeBasin]
    continent,  # ContinentData (for bilinear elevation sampling)
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
        continent: ContinentData（用于海拔采样）。
    """
    if not lake_basins:
        return

    size = TILE_MAP_SIZE
    cell_size = continent.cell_size

    # chunk 边界（世界坐标）
    chunk_x1 = world_x0 + size
    chunk_y1 = world_y0 + size

    # 检查哪些湖泊与此 chunk 有重叠
    for basin in lake_basins:
        surface = basin.surface_elev

        # 检查盆地是否有任何像素在此 chunk 内
        overlaps = False
        for ci in basin.cells:
            cx = (ci % continent.grid_width) * cell_size + cell_size / 2
            cy = (ci // continent.grid_width) * cell_size + cell_size / 2
            if world_x0 - cell_size <= cx < chunk_x1 + cell_size and \
               world_y0 - cell_size <= cy < chunk_y1 + cell_size:
                overlaps = True
                break

        if not overlaps:
            continue

        # 在 chunk 内渲染此湖泊
        _flatten_lake_surface(tile_grid, world_x0, world_y0, surface,
                              basin.area_km2, continent)
        _generate_wetland_fringe(tile_grid, world_x0, world_y0, surface,
                                 continent)


def _flatten_lake_surface(
    tile_grid: TileGrid,
    world_x0: int, world_y0: int,
    surface_elev: float,
    area_km2: float,
    continent,
) -> None:
    """将湖面以下的 tile 标记为水体。

    深度判定：
      - 大湖（>1km²）中央 → DEEP_WATER
      - 小湖 / 边缘 → SHALLOW_WATER

    Args:
        tile_grid: 地形网格。
        world_x0, world_y0: chunk 世界坐标。
        surface_elev: 湖面海拔 (m)。
        area_km2: 湖面面积 (km²)。
        continent: ContinentData。
    """
    size = tile_grid.size
    # 大湖（>1km²）允许深水区
    has_deep_zone = area_km2 > 1.0

    for ty in range(size):
        wy = world_y0 + ty
        for tx in range(size):
            wx = world_x0 + tx

            # 获取宏观海拔（湖面判定使用宏观场，避免细节噪声干扰）
            macro_elev = continent.sample_altitude_bilinear(wx, wy)

            if macro_elev >= surface_elev:
                continue  # 高于湖面，不处理

            # 水面以下 → 水体
            depth = surface_elev - macro_elev

            # 计算到湖岸的估计距离（基于水深推断）
            # 浅水（0-3m）→ SHALLOW_WATER
            # 深水（>3m）→ DEEP_WATER（仅限大湖）
            if depth > 3.0 and has_deep_zone:
                tile_grid.set(tx, ty, TerrainType.DEEP_WATER)
            else:
                # 避免覆盖已经设置的深水
                current = tile_grid.get(tx, ty)
                if current != TerrainType.DEEP_WATER:
                    tile_grid.set(tx, ty, TerrainType.SHALLOW_WATER)


def _generate_wetland_fringe(
    tile_grid: TileGrid,
    world_x0: int, world_y0: int,
    surface_elev: float,
    continent,
) -> None:
    """在湖面边缘生成湿地（MARSH）。

    湖面以上 0-2m 的平坦区域 → 沼泽湿地。
    模拟自然湖泊周围的季节性淹没区。

    Args:
        tile_grid: 地形网格。
        world_x0, world_y0: chunk 世界坐标。
        surface_elev: 湖面海拔 (m)。
        continent: ContinentData。
    """
    size = tile_grid.size

    for ty in range(size):
        wy = world_y0 + ty
        for tx in range(size):
            wx = world_x0 + tx

            macro_elev = continent.sample_altitude_bilinear(wx, wy)

            # 湿地 = 湖面以上 0-2m
            wetland_depth = macro_elev - surface_elev
            if not (0.0 < wetland_depth <= 2.0):
                continue

            # 只有非水体、非山地 tile 可以变为湿地
            current = tile_grid.get(tx, ty)
            if current in (TerrainType.DEEP_WATER, TerrainType.SHALLOW_WATER,
                           TerrainType.MOUNTAIN_PEAK, TerrainType.STEEP_SLOPE):
                continue

            # 越接近湖面，越大概率是湿地（概率 = 1 - wetland_depth/2）
            # 使用确定性判定：基于坐标 hash 的伪随机
            prob_threshold = 1.0 - wetland_depth / 2.0  # depth=0 → 100%, depth=2 → 0%
            hash_val = ((wx * 2654435761 + wy * 1597334677) & 0xFFFFFFFF) / 0xFFFFFFFF

            if hash_val < prob_threshold:
                tile_grid.set(tx, ty, TerrainType.MARSH)


__all__ = ["render_lake_chunk"]
