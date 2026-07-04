"""水文系统测试 — D8 流向 + 水流累积 + 河流提取 + 水力侵蚀。

测试覆盖:
  1. TestFlowDirection — D8 流向正确性（严格下坡）
  2. TestFlowAccumulation — 累积流量单调不减
  3. TestRiverExtraction — 河流网络提取 + Strahler 分级
  4. TestHydraulicErosion — 侵蚀降低河道海拔 + 质量守恒
"""

import math
import pytest

CANONICAL_SEED = 42


# ════════════════════════════════════════════════════════════════
# 辅助：构造简单 DEM 用于单测
# ════════════════════════════════════════════════════════════════

def _make_cone_dem(w: int, h: int) -> list[float]:
    """构造锥形 DEM（中心最高，边缘最低），用于验证流向正确。

    Args:
        w: 宽度。
        h: 高度。

    Returns:
        行优先海拔数组，中心=1000，边缘=0。
    """
    cx, cy = (w - 1) / 2.0, (h - 1) / 2.0
    max_dist = math.sqrt(cx * cx + cy * cy)
    dem: list[float] = []
    for y in range(h):
        for x in range(w):
            d = math.sqrt((x - cx) ** 2 + (y - cy) ** 2)
            dem.append(1000.0 * (1.0 - d / max_dist))
    return dem


def _make_slope_dem(w: int, h: int) -> list[float]:
    """构造纯斜坡 DEM（从左上到右下单调下降），用于验证流向。

    Args:
        w: 宽度。
        h: 高度。

    Returns:
        行优先海拔数组，左上最高。
    """
    dem: list[float] = []
    for y in range(h):
        for x in range(w):
            dem.append(1000.0 - (x + y) * 10.0)
    return dem


@staticmethod
def _neighbors(x: int, y: int, w: int, h: int) -> list[tuple[int, int, int]]:
    """返回 (nx, ny, dir_code) 列表，dir_code 按 D8 方向编号。"""
    dirs = [
        (1, 0, 0), (-1, 0, 1), (0, 1, 2), (0, -1, 3),
        (1, 1, 4), (-1, 1, 5), (1, -1, 6), (-1, -1, 7),
    ]
    result = []
    for dx, dy, dc in dirs:
        nx, ny = x + dx, y + dy
        if 0 <= nx < w and 0 <= ny < h:
            result.append((nx, ny, dc))
    return result


# ════════════════════════════════════════════════════════════════
# 1. TestFlowDirection
# ════════════════════════════════════════════════════════════════


class TestFlowDirection:
    """D8 流向测试 — 每个像素指向最低邻居。"""

    def test_import_hydrology(self):
        """可以导入 hydrology 模块。"""
        from ascend.space import hydrology
        assert hydrology is not None

    def test_d8_direction_exists(self):
        """compute_d8 函数可调用。"""
        from ascend.space.hydrology import compute_d8
        dem = _make_slope_dem(10, 10)
        directions = compute_d8(dem, 10, 10)
        assert len(directions) == 100
        # 方向值在 [0, 7] 或 -1（汇点）
        for d in directions:
            assert -1 <= d <= 7, f"方向 {d} 不在 [-1, 7]"

    def test_d8_always_steepest_descent(self):
        """每个像素的 D8 流向都指向 8 邻域中最低的邻居。

        对 slope DEM 验证，右下邻居应该海拔更低。
        """
        from ascend.space.hydrology import compute_d8
        w, h = 20, 20
        dem = _make_slope_dem(w, h)
        directions = compute_d8(dem, w, h)
        for y in range(h - 1):
            for x in range(w - 1):
                idx = y * w + x
                d = directions[idx]
                if d < 0:
                    continue  # 汇点
                # 解码方向
                dx = [1, -1, 0, 0, 1, -1, 1, -1][d]
                dy = [0, 0, 1, -1, 1, 1, -1, -1][d]
                nx, ny = x + dx, y + dy
                if 0 <= nx < w and 0 <= ny < h:
                    ni = ny * w + nx
                    assert dem[ni] < dem[idx], (
                        f"({x},{y}) 海拔 {dem[idx]:.1f} 流向 ({nx},{ny}) "
                        f"海拔 {dem[ni]:.1f}，但下游更高！"
                    )

    def test_no_flow_uphill_in_cone(self):
        """锥形 DEM：流向必须指向中心向外（下坡）。

        从所有点出发追踪流线，验证终点在边界。
        """
        from ascend.space.hydrology import compute_d8
        w, h = 15, 15
        dem = _make_cone_dem(w, h)
        directions = compute_d8(dem, w, h)
        for y in range(h):
            for x in range(w):
                idx = y * w + x
                d = directions[idx]
                if d < 0:
                    continue
                dx = [1, -1, 0, 0, 1, -1, 1, -1][d]
                dy = [0, 0, 1, -1, 1, 1, -1, -1][d]
                nx, ny = x + dx, y + dy
                if 0 <= nx < w and 0 <= ny < h:
                    assert dem[ny * w + nx] <= dem[idx], "流向不是下坡"

    def test_basin_sink_has_no_direction(self):
        """局部最低点（汇）的流向应为 -1。"""
        from ascend.space.hydrology import compute_d8
        # 构造中心有洼地的小 DEM
        dem = [10.0, 10.0, 10.0,
               10.0, 5.0, 10.0,    # 中心最低
               10.0, 10.0, 10.0]
        directions = compute_d8(dem, 3, 3)
        # 中心 (1,1) 应为汇点
        assert directions[1 * 3 + 1] == -1, "洼地中心应为汇点"


# ════════════════════════════════════════════════════════════════
# 2. TestFlowAccumulation
# ════════════════════════════════════════════════════════════════


class TestFlowAccumulation:
    """水流累积测试 — 单调性 + 非负 + 源头=1。"""

    def test_accumulation_non_negative(self):
        """所有累积量 >= 1。"""
        from ascend.space.hydrology import compute_d8, flow_accumulation
        w, h = 20, 20
        dem = _make_slope_dem(w, h)
        directions = compute_d8(dem, w, h)
        acc = flow_accumulation(directions, w, h)
        for a in acc:
            assert a >= 1.0

    def test_accumulation_increases_downstream(self):
        """沿流向追踪，累积量单调不减。"""
        from ascend.space.hydrology import compute_d8, flow_accumulation
        w, h = 20, 20
        dem = _make_slope_dem(w, h)
        directions = compute_d8(dem, w, h)
        acc = flow_accumulation(directions, w, h)
        for y in range(h):
            for x in range(w):
                idx = y * w + x
                d = directions[idx]
                if d < 0:
                    continue
                dx = [1, -1, 0, 0, 1, -1, 1, -1][d]
                dy = [0, 0, 1, -1, 1, 1, -1, -1][d]
                nx, ny = x + dx, y + dy
                if 0 <= nx < w and 0 <= ny < h:
                    ni = ny * w + nx
                    assert acc[ni] >= acc[idx], (
                        f"下游累积量 {acc[ni]:.1f} < 上游 {acc[idx]:.1f}"
                    )

    def test_source_cells_accumulation_one(self):
        """源头像素（无流入）的累积量 = 1.0。"""
        from ascend.space.hydrology import compute_d8, flow_accumulation
        w, h = 10, 10
        dem = _make_cone_dem(w, h)
        directions = compute_d8(dem, w, h)
        acc = flow_accumulation(directions, w, h)
        # 找源头：没有任何像素流入它的像素
        has_inflow = [False] * (w * h)
        for y in range(h):
            for x in range(w):
                idx = y * w + x
                d = directions[idx]
                if d < 0:
                    continue
                dx = [1, -1, 0, 0, 1, -1, 1, -1][d]
                dy = [0, 0, 1, -1, 1, 1, -1, -1][d]
                nx, ny = x + dx, y + dy
                if 0 <= nx < w and 0 <= ny < h:
                    has_inflow[ny * w + nx] = True
        source_count = 0
        for i in range(w * h):
            if not has_inflow[i]:
                source_count += 1
                assert acc[i] == pytest.approx(1.0), f"源头 {i} 累积量 {acc[i]} != 1"


# ════════════════════════════════════════════════════════════════
# 4. TestHydraulicErosion
# ════════════════════════════════════════════════════════════════


class TestHydraulicErosion:
    """水力侵蚀测试。"""

    def test_erosion_function_exists(self):
        """erode 函数可调用，返回 ErosionResult。"""
        from ascend.space.hydrology import erode
        dem = _make_slope_dem(10, 10)
        rainfall = [1.0] * 100
        result = erode(dem, rainfall, 10, 10, iterations=1)
        assert len(result.dem) == 100

    def test_erosion_lowers_peaks(self):
        """侵蚀后最高点降低（物质被搬运走）。"""
        from ascend.space.hydrology import erode
        dem = _make_cone_dem(20, 20)
        rainfall = [1.0] * 400
        eroded = erode(dem, rainfall, 20, 20, iterations=5)
        assert max(eroded.dem) <= max(dem), "侵蚀后最高点不应升高"

    def test_erosion_deterministic(self):
        """同输入 → 同输出。"""
        from ascend.space.hydrology import erode
        dem = _make_cone_dem(15, 15)
        rainfall = [1.0] * 225
        r1 = erode(dem, rainfall, 15, 15, iterations=3)
        r2 = erode(dem, rainfall, 15, 15, iterations=3)
        for i in range(len(r1.dem)):
            assert r1.dem[i] == pytest.approx(r2.dem[i])

    def test_erosion_no_nan(self):
        """侵蚀结果不含 NaN/Inf。"""
        from ascend.space.hydrology import erode
        dem = _make_slope_dem(10, 10)
        rainfall = [1.0] * 100
        result = erode(dem, rainfall, 10, 10, iterations=3)
        for v in result.dem:
            assert not math.isnan(v)
            assert not math.isinf(v)

    def test_erosion_changes_dem(self):
        """侵蚀后 DEM 发生变化（不是无操作），且变化合理。"""
        from ascend.space.hydrology import erode
        w, h = 20, 20
        dem = _make_cone_dem(w, h)
        rainfall = [1.0] * (w * h)
        eroded = erode(dem, rainfall, w, h, iterations=5)
        # 侵蚀后最高点降低（物质被搬运）
        assert max(eroded.dem) <= max(dem)
        # 至少有一些变化
        changes = [abs(dem[i] - eroded.dem[i]) for i in range(len(dem))]
        assert max(changes) > 0.001, "侵蚀应有可测量的海拔变化"

    def test_erosion_mass_conserved(self):
        """侵蚀+沉积总量接近 0（质量守恒）。"""
        from ascend.space.hydrology import erode
        w, h = 15, 15
        dem = _make_cone_dem(w, h)
        rainfall = [1.0] * (w * h)
        eroded = erode(dem, rainfall, w, h, iterations=3)
        total_change = sum(eroded.dem[i] - dem[i] for i in range(len(dem)))
        # 净变化应接近 0（侵蚀量 ≈ 沉积量）
        avg_change = abs(total_change) / len(dem)
        assert avg_change < 1.0, f"净质量变化 {total_change:.2f}，平均 {avg_change:.4f}m/像素"


# ════════════════════════════════════════════════════════════════
# 5. 集成测试 — 把 continent + hydrology 串起来
# ════════════════════════════════════════════════════════════════


class TestIntegration:
    """端到端：大陆生成 → 水文侵蚀。"""

    def test_continent_plus_erosion(self):
        """对 ContinentData 的海拔做水力侵蚀。"""
        from ascend.space.continent import ContinentGenerator
        from ascend.space.hydrology import compute_d8, flow_accumulation, erode

        gen = ContinentGenerator(seed=CANONICAL_SEED)
        data = gen.generate()
        w, h = data.grid_width, data.grid_height
        dem = data.elevation_field
        rainfall = [1.0] * len(dem)

        eroded = erode(dem, rainfall, w, h, iterations=2)
        assert len(eroded.dem) == len(dem)
        for v in eroded.dem:
            assert not math.isnan(v)
            assert not math.isinf(v)

    def test_rivers_from_continent(self):
        """从大陆生成 RK4 流线河流网络（实际游戏使用的河流）。"""
        from ascend.space.continent import ContinentGenerator

        gen = ContinentGenerator(seed=CANONICAL_SEED)
        data = gen.generate()
        hyd = data.hydrology
        assert hyd is not None, "水文数据应存在"
        assert hyd.river_network is not None, "河流网络应存在"
        assert len(hyd.river_network.rivers) > 0, "大陆上应能生成河流"


# ════════════════════════════════════════════════════════════════
# 6. TestDistanceToOcean — 距海距离 BFS
# ════════════════════════════════════════════════════════════════


class TestDistanceToOcean:
    """距海距离 BFS 测试。"""

    def test_import_exists(self):
        """_distance_to_ocean_c 可导入。"""
        from ascend.space.hydrology import _distance_to_ocean_c
        assert _distance_to_ocean_c is not None

    def test_ocean_distance_zero(self):
        """海洋格距离为 0。"""
        from array import array
        from ascend.space.hydrology import _distance_to_ocean_c
        w, h = 10, 10
        elev = array('d', [-100.0]) * (w * h)
        dist = _distance_to_ocean_c(elev, w, h)
        for d in dist:
            assert d == 0.0

    def test_land_increasing_from_coast(self):
        """陆地格距离随距海岸距离单调增加。"""
        from array import array
        from ascend.space.hydrology import _distance_to_ocean_c
        w, h = 10, 5
        elev = array('d', [0.0]) * (w * h)
        for y in range(h):
            for x in range(w):
                elev[y * w + x] = -10.0 if x < 2 else 100.0
        dist = _distance_to_ocean_c(elev, w, h)
        # 同列内陆方向距离应递增
        for y in range(h):
            for x in range(2, w):
                assert dist[y * w + x] >= dist[y * w + (x - 1)], (
                    f"({x},{y}) 距离 {dist[y*w+x]} < 左邻 {dist[y*w+(x-1)]}"
                )

    def test_deterministic(self):
        """同输入 → 同输出。"""
        from array import array
        from ascend.space.hydrology import _distance_to_ocean_c
        w, h = 10, 10
        elev = array('d', [0.0]) * (w * h)
        for i in range(w * h):
            elev[i] = -10.0 if (i % w) < 3 else 200.0
        d1 = _distance_to_ocean_c(elev, w, h)
        d2 = _distance_to_ocean_c(elev, w, h)
        for i in range(w * h):
            assert d1[i] == d2[i]

    def test_all_land_positive_distance(self):
        """全陆地网格中所有格距离 > 0（被海洋包围 → 距海距离递增）。"""
        from array import array
        from ascend.space.hydrology import _distance_to_ocean_c
        w, h = 8, 8
        # 边缘一圈海洋，内部陆地
        elev = array('d', [200.0]) * (w * h)
        for y in range(h):
            for x in range(w):
                if x == 0 or x == w - 1 or y == 0 or y == h - 1:
                    elev[y * w + x] = -10.0
        dist = _distance_to_ocean_c(elev, w, h)
        # 中心应距离 > 0
        center = (h // 2) * w + (w // 2)
        assert dist[center] > 0.0, f"中心距海距离应为正数，实为 {dist[center]}"


# ════════════════════════════════════════════════════════════════
# 7. TestRainShadowOmni — 万向水分预算雨影
# ════════════════════════════════════════════════════════════════


class TestRainShadowOmni:
    """万向雨影测试。"""

    def test_import_exists(self):
        """_rain_shadow_omnidirectional_c 可导入。"""
        from ascend.space.hydrology import _rain_shadow_omnidirectional_c
        assert _rain_shadow_omnidirectional_c is not None

    def test_factor_range(self):
        """所有因子在 [min_factor, 1.0] 范围内。"""
        from array import array
        from ascend.space.hydrology import _rain_shadow_omnidirectional_c
        w, h = 20, 15
        elev = array('d', [0.0]) * (w * h)
        for y in range(h):
            for x in range(w):
                elev[y * w + x] = -10.0 if x < 4 else max(0.0, (x - 4) * 50.0)
        factors = _rain_shadow_omnidirectional_c(
            elev, w, h, primary_angle=0.0, min_factor=0.15,
        )
        for f in factors:
            assert 0.15 <= f <= 1.0 + 1e-10, f"因子 {f} 超范围"

    def test_deterministic(self):
        """同输入 + 同风向角 → 同输出。"""
        from array import array
        from ascend.space.hydrology import _rain_shadow_omnidirectional_c
        w, h = 15, 10
        elev = array('d', [0.0]) * (w * h)
        for y in range(h):
            for x in range(w):
                elev[y * w + x] = -5.0 if x < 3 else (x - 3) * 100.0
        f1 = _rain_shadow_omnidirectional_c(elev, w, h, primary_angle=1.2)
        f2 = _rain_shadow_omnidirectional_c(elev, w, h, primary_angle=1.2)
        for i in range(w * h):
            assert f1[i] == pytest.approx(f2[i]), f"索引 {i}: {f1[i]} ≠ {f2[i]}"

    def test_mountain_leeward_drier(self):
        """山脉背风面比迎风面干燥（水汽预算耗尽）。

        使用缓坡山脉（50m/px），步长 2 格时每步抬升 ~100m，
        水汽逐步消耗而非瞬间耗尽，背风面仍有剩余但比迎风面少。
        """
        from array import array
        from ascend.space.hydrology import _rain_shadow_omnidirectional_c
        w, h = 50, 10
        elev = array('d', [0.0]) * (w * h)
        for y in range(h):
            for x in range(w):
                if x < 8:
                    elev[y * w + x] = -10.0  # 海洋
                elif 8 <= x < 28:
                    elev[y * w + x] = (x - 8) * 50.0  # 缓坡迎风坡 (0→1000m)
                elif 28 <= x < 33:
                    elev[y * w + x] = 1000.0  # 山顶台地
                else:
                    elev[y * w + x] = max(0.0, 1000.0 - (x - 33) * 30.0)  # 缓坡背风坡
        factors = _rain_shadow_omnidirectional_c(
            elev, w, h, primary_angle=0.0,  # 西风
            min_factor=0.15,
        )
        # 迎风坡中部（x=18）vs 背风坡中部（x=40）
        windward_factor = factors[5 * w + 18]
        leeward_factor = factors[5 * w + 40]
        assert leeward_factor < windward_factor, (
            f"背风面 {leeward_factor:.3f} 应 < 迎风面 {windward_factor:.3f}"
        )
        # 背风面仍高于最小因子（水汽未完全耗尽）
        assert leeward_factor > 0.15, (
            f"背风面 {leeward_factor:.3f} 应 > min_factor 0.15"
        )

    def test_ocean_coast_factor_near_one(self):
        """海岸线附近因子接近 1.0（水汽充足）。"""
        from array import array
        from ascend.space.hydrology import _rain_shadow_omnidirectional_c
        w, h = 20, 10
        elev = array('d', [0.0]) * (w * h)
        for y in range(h):
            for x in range(w):
                elev[y * w + x] = -10.0 if x < 5 else 100.0
        factors = _rain_shadow_omnidirectional_c(
            elev, w, h, primary_angle=0.0,
        )
        # 紧邻海岸的陆地格（x=5）因子应接近 1.0
        for y in range(h):
            assert factors[y * w + 5] > 0.9, (
                f"海岸 ({5},{y}) 因子 {factors[y*w+5]:.3f} 应 > 0.9"
            )

    def test_secondary_wind_blending(self):
        """次风向混合后因子介于两单独风向之间。"""
        from array import array
        from ascend.space.hydrology import _rain_shadow_omnidirectional_c
        import math
        w, h = 15, 10
        elev = array('d', [0.0]) * (w * h)
        for y in range(h):
            for x in range(w):
                elev[y * w + x] = -10.0 if x < 3 else max(0.0, (x - 3) * 80.0)
        f0 = _rain_shadow_omnidirectional_c(elev, w, h, primary_angle=0.0)
        f45 = _rain_shadow_omnidirectional_c(elev, w, h, primary_angle=math.pi / 4)
        f_blend = _rain_shadow_omnidirectional_c(
            elev, w, h,
            primary_angle=0.0,
            secondary_angle=math.pi / 4,
            secondary_weight=0.5,
        )
        # 混合结果应介于两纯方向之间（或等于其一）
        for i in range(w * h):
            lo = min(f0[i], f45[i])
            hi = max(f0[i], f45[i])
            assert lo - 1e-10 <= f_blend[i] <= hi + 1e-10, (
                f"混合 {f_blend[i]:.3f} 不在 [{lo:.3f}, {hi:.3f}] 内"
            )
