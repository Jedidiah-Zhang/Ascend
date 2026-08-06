"""宏观场采样坐标换算测试 — tile 米坐标 ↔ 宏观场格索引（100m/格）。

回归：sample_altitude 系列直接把 tile 世界坐标（米）当宏观场格索引，
越界返回默认 -3500 深水 → 新建世界 tile 层全深水 → 地形不可见。
"""

import pytest

from ascend.space.continent import ContinentGenerator, ContinentParams
from ascend.space.generator import WorldGenerator


@pytest.fixture(scope="module")
def small_continent():
    """小宏观场（30×20 格，3km×2km），生成快。"""
    gen = WorldGenerator(seed=2026)
    return gen.ensure_continent()


def test_sample_altitude_matches_direct_grid_index(small_continent):
    """tile 米坐标采样 = 宏观场直接索引（÷100 换算一致）。

    宏观场 1 格 = 100m = 100 tile：tile 坐标 (cx*200+100, cy*200+100)
    对应格索引 (cx*2+1, cy*2+1)——与 _select_birth_point 的判定口径一致。
    """
    cont = small_continent
    w = cont.grid_width
    for cx, cy in [(0, 0), (3, 5), (10, 8)]:
        gi = (cy * 2 + 1) * w + (cx * 2 + 1)
        expected = cont.elevation_field[gi]
        actual = cont.sample_altitude(cx * 200 + 100, cy * 200 + 100)
        assert actual == pytest.approx(expected), (
            f"chunk({cx},{cy}) 中心 tile 坐标采样应等于宏观场格索引值 "
            f"({actual} != {expected})"
        )


def test_sample_altitude_bilinear_stays_in_bounds(small_continent):
    """chunk 中心的双线性采样不越界（不返回默认深水 -3500）。"""
    cont = small_continent
    w = cont.grid_width
    for cx, cy in [(0, 0), (3, 5), (10, 8)]:
        gi = (cy * 2 + 1) * w + (cx * 2 + 1)
        expected = cont.elevation_field[gi]
        actual = cont.sample_altitude_bilinear(cx * 200 + 100, cy * 200 + 100)
        assert abs(actual - expected) < 100, (
            f"chunk({cx},{cy}) 双线性采样应在宏观场值域内（不越界 -3500）："
            f"{actual} vs {expected}"
        )


def test_tile_generation_of_land_chunk_not_all_water(small_continent):
    """陆地 chunk 的 tile 生成不得全为深水（坐标换算正确性端到端验证）。"""
    cont = small_continent
    w = cont.grid_width
    from ascend.space.tile_gen import TileGenerator
    from ascend.space.generator import WorldGenerator

    gen = WorldGenerator(seed=2026)
    tg = TileGenerator(seed=2026, continent=cont)
    picked = None
    for cy in range(cont.grid_height // 2):
        for cx in range(w // 2):
            gi = (cy * 2 + 1) * w + (cx * 2 + 1)
            if cont.land_mask[gi]:
                picked = (cx, cy)
                break
        if picked:
            break
    assert picked is not None
    chunk = gen.generate_chunk(*picked)
    grid = tg.generate_chunk_for(chunk)
    elevations = [
        grid.get_elevation(x, y)
        for y in range(grid.size) for x in range(grid.size)
    ]
    assert max(elevations) > 0, "陆地 chunk 应存在高于海平面的 tile"
    assert min(elevations) > -1000, "不应出现越界默认深水 -3500"
