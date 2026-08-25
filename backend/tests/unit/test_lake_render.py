"""湖泊渲染测试 — 水面平整 + 湿地斑块的 seed 行为。

湿地斑块随机源契约：render_lake_chunk 必须掺入世界 seed
（cell_hash(wx, wy, seed)）——同 seed 确定性、不同 seed 模式不同。
"""

from types import SimpleNamespace

import pytest

from ascend.config import TILE_MAP_SIZE
from ascend.space.lake_render import render_lake_chunk
from ascend.space.terrain import TerrainType
from ascend.space.tile_grid import TileGrid

_SIZE = TILE_MAP_SIZE
_GRID_W = 200


def _make_scene():
    """构造小型湖泊场景：湖心低地 + 湖周 0-2m 湿地候选带。

    湖面 surface=100：湖心 8×8 区域 elev=99（水体），
    湖周一圈 elev=101（湿地候选，prob=0.5），其余 elev=110。
    """
    grid = TileGrid()
    macro = [110.0] * (_SIZE * _SIZE)
    cells = []
    for ty in range(96, 104):
        for tx in range(96, 104):
            macro[ty * _SIZE + tx] = 99.0
            cells.append(ty * _GRID_W + tx)
    for ty in range(95, 105):
        for tx in range(95, 105):
            if macro[ty * _SIZE + tx] == 110.0:
                macro[ty * _SIZE + tx] = 101.0
    basin = SimpleNamespace(cells=cells, surface_elev=100.0, area_km2=2.0)
    continent = SimpleNamespace(grid_width=_GRID_W)
    return grid, macro, basin, continent


def _marsh_tiles(grid) -> set:
    return {(x, y) for y in range(_SIZE) for x in range(_SIZE)
            if grid.get(x, y) == TerrainType.MARSH}


def test_lake_flattens_surface():
    grid, macro, basin, continent = _make_scene()
    render_lake_chunk(grid, 0, 0, [basin], continent, seed=0,
                      macro_elev_grid=macro)
    for ty in range(96, 104):
        for tx in range(96, 104):
            assert grid.get(tx, ty) == TerrainType.WATER


def test_wetland_deterministic_same_seed():
    g1, m1, b1, c1 = _make_scene()
    g2, m2, b2, c2 = _make_scene()
    render_lake_chunk(g1, 0, 0, [b1], c1, seed=777, macro_elev_grid=m1)
    render_lake_chunk(g2, 0, 0, [b2], c2, seed=777, macro_elev_grid=m2)
    marsh1, marsh2 = _marsh_tiles(g1), _marsh_tiles(g2)
    assert marsh1 == marsh2
    assert marsh1


def test_wetland_pattern_differs_across_seeds():
    g1, m1, b1, c1 = _make_scene()
    g2, m2, b2, c2 = _make_scene()
    render_lake_chunk(g1, 0, 0, [b1], c1, seed=777, macro_elev_grid=m1)
    render_lake_chunk(g2, 0, 0, [b2], c2, seed=778, macro_elev_grid=m2)
    assert _marsh_tiles(g1) != _marsh_tiles(g2)


def test_no_basins_returns_unchanged():
    grid = TileGrid()
    render_lake_chunk(grid, 0, 0, [], SimpleNamespace(grid_width=_GRID_W),
                      seed=1, macro_elev_grid=[110.0] * (_SIZE * _SIZE))
    for y in range(_SIZE):
        for x in range(_SIZE):
            assert grid.get(x, y) == TerrainType.GRASSLAND


@pytest.mark.parametrize("material", [
    TerrainType.ROCK, TerrainType.GRAVEL, TerrainType.PERMAFROST,
])
def test_wetland_skips_non_soil_shore(material):
    """岩岸（裸岩/砾石/冻土）不被湿地 fringe 覆盖为 MARSH。

    材质由低频场判定（issue #42 纯净化），湿地 fringe 只作用在土壤上。
    """
    grid, macro, basin, continent = _make_scene()
    # 湿地候选环（elev=101）预分类为非土壤材质
    for ty in range(95, 105):
        for tx in range(95, 105):
            if macro[ty * _SIZE + tx] == 101.0:
                grid.set(tx, ty, material)
    render_lake_chunk(grid, 0, 0, [basin], continent, seed=0,
                      macro_elev_grid=macro)
    # 岩岸保持材质，不变 MARSH
    for ty in range(95, 105):
        for tx in range(95, 105):
            if macro[ty * _SIZE + tx] == 101.0:
                assert grid.get(tx, ty) == material, \
                    f"({tx},{ty}) {material.name} 岩岸被覆盖为 MARSH"
    # 湖心仍为 WATER
    for ty in range(96, 104):
        for tx in range(96, 104):
            assert grid.get(tx, ty) == TerrainType.WATER


def test_wetland_skips_water_tiles():
    """水体 tile 不被湿地 fringe 覆盖（保持 WATER）。"""
    grid, macro, basin, continent = _make_scene()
    for ty in range(96, 104):
        for tx in range(96, 104):
            grid.set(tx, ty, TerrainType.GRASSLAND)  # 先置陆地再渲染
    render_lake_chunk(grid, 0, 0, [basin], continent, seed=0,
                      macro_elev_grid=macro)
    for ty in range(96, 104):
        for tx in range(96, 104):
            assert grid.get(tx, ty) == TerrainType.WATER