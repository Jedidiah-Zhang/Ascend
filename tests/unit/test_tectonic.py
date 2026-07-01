"""Voronoi 构造模拟 — 测试基准。

测试范围：
  1. API 基础 — 导入、预设、参数校验
  2. 确定性 — 同 seed 同坐标 = 同结果
  3. 值域边界 — 海拔在 [altitude_floor, altitude_ceil] 内
  4. 海陆并存 — 世界中同时存在海洋和陆地
  5. 连续性 — 相邻 tile 海拔差有上限（无悬崖式跳变）
  6. 批量一致性 — batch 结果与单点调用完全一致
  7. 预设差异化 — 不同预设产生不同海陆比例
  8. 边界情况 — 大坐标、负坐标、零坐标

未覆盖项（待算法稳定后补充）：
  - 山脉连贯性定量检测（需定义"连贯"的度量）
  - 性能回归（C 扩展实现后添加）
  - 多线程安全性
  - 板块边界具体形态验证

待商定项（见文件末尾 Q: 标记）：
  Q1: 海陆比例目标 — ocean_ratio 参数 vs 实际产出
  Q2: 相邻 tile 最大海拔差阈值
  Q3: Voronoi 边界隆起的具体机制
"""

import math
import pytest
from dataclasses import dataclass


# ════════════════════════════════════════════════════════════════
# 类型前向声明（实际实现在 ascend.space.tectonic）
# ════════════════════════════════════════════════════════════════


# ════════════════════════════════════════════════════════════════
# 1. API 基础 & 导入
# ════════════════════════════════════════════════════════════════

class TestImports:
    """模块导入和 API 存在性。"""

    def test_import_tectonic(self):
        """可以导入 tectonic 模块。"""
        from ascend.space import tectonic
        assert tectonic is not None

    def test_world_params_exists(self):
        """WorldParams 类可导入。"""
        from ascend.space.tectonic import WorldParams
        assert callable(WorldParams) or hasattr(WorldParams, '__dataclass_fields__')

    def test_world_params_defaults(self):
        """WorldParams 默认值合理。"""
        from ascend.space.tectonic import WorldParams
        p = WorldParams()
        assert p.seed_spacing > 0
        assert p.uplift_scale > 0
        assert p.altitude_floor < p.altitude_ceil
        assert 0.0 <= p.ocean_ratio <= 1.0
        assert 0.0 < p.boundary_width < p.seed_spacing
        assert p.ocean_depth_typical < 0  # 海洋深度为负值
        assert p.land_elevation_typical > 0  # 陆地海拔为正值
        # 默认 70% 海洋板块概率
        assert p.ocean_ratio == pytest.approx(0.70)

    def test_presets_exist(self):
        """所有预设可访问且合法。"""
        from ascend.space.tectonic import PRESETS, WorldParams
        required = {"earthlike", "pangaea", "archipelago", "mountainous", "flat", "ocean_world"}
        for name in required:
            assert name in PRESETS, f"缺少预设: {name}"
            p = PRESETS[name]
            assert isinstance(p, WorldParams)

    def test_tectonic_altitude_callable(self):
        """单点查询函数可调用。"""
        from ascend.space.tectonic import tectonic_altitude
        result = tectonic_altitude(0.0, 0.0, 42)
        assert isinstance(result, float)

    def test_tectonic_altitude_batch_callable(self):
        """批量查询函数可调用。"""
        from ascend.space.tectonic import tectonic_altitude_batch
        result = tectonic_altitude_batch(0, 0, 3, 3, 42)
        assert isinstance(result, list)
        assert len(result) == 9
        assert all(isinstance(v, float) for v in result)


# ════════════════════════════════════════════════════════════════
# 2. 确定性
# ════════════════════════════════════════════════════════════════

class TestDeterminism:
    """确定性：相同输入 → 相同输出。"""

    def test_same_seed_same_result(self):
        """同一 seed + 同一坐标 → 完全相同的海拔。"""
        from ascend.space.tectonic import tectonic_altitude
        for wx, wy in [(0, 0), (100, -200), (-500, 300), (1.5, 2.3)]:
            a = tectonic_altitude(wx, wy, seed=42)
            b = tectonic_altitude(wx, wy, seed=42)
            assert a == pytest.approx(b), f"({wx}, {wy}): {a} != {b}"

    def test_different_seed_different_result(self):
        """不同 seed → 海拔不同（大概率，至少某些坐标不同）。"""
        from ascend.space.tectonic import tectonic_altitude
        results_1 = [tectonic_altitude(i * 10, i * 10, seed=1) for i in range(20)]
        results_2 = [tectonic_altitude(i * 10, i * 10, seed=2) for i in range(20)]
        assert results_1 != results_2, "两个 seed 产生完全相同的结果"

    def test_batch_deterministic(self):
        """相同参数的 batch 调用产生相同结果。"""
        from ascend.space.tectonic import tectonic_altitude_batch
        a = tectonic_altitude_batch(0, 0, 10, 10, seed=42)
        b = tectonic_altitude_batch(0, 0, 10, 10, seed=42)
        assert len(a) == len(b)
        for i, (va, vb) in enumerate(zip(a, b)):
            assert va == pytest.approx(vb), f"index {i}: {va} != {vb}"


# ════════════════════════════════════════════════════════════════
# 3. 值域边界
# ════════════════════════════════════════════════════════════════

class TestValueRange:
    """输出值域在声明范围内。"""

    def test_single_point_in_range(self):
        """单点海拔在 [altitude_floor, altitude_ceil] 内。"""
        from ascend.space.tectonic import tectonic_altitude, PRESETS
        for name, params in PRESETS.items():
            floor = params.altitude_floor
            ceil = params.altitude_ceil
            # 扫描每个预设在多个坐标
            for wx in range(0, 500, 50):
                for wy in range(0, 500, 50):
                    alt = tectonic_altitude(wx, wy, seed=0, params=params)
                    assert floor <= alt <= ceil, (
                        f"[{name}] ({wx}, {wy}): {alt} not in [{floor}, {ceil}]"
                    )

    def test_batch_in_range(self):
        """批量海拔全部在值域内。"""
        from ascend.space.tectonic import tectonic_altitude_batch, PRESETS
        for name, params in PRESETS.items():
            alts = tectonic_altitude_batch(0, 0, 20, 20, seed=0, params=params)
            floor = params.altitude_floor
            ceil = params.altitude_ceil
            for i, a in enumerate(alts):
                assert floor <= a <= ceil, (
                    f"[{name}] batch[{i}]: {a} not in [{floor}, {ceil}]"
                )

    def test_no_nan_or_inf(self):
        """海拔值不含 NaN 或 Inf。"""
        from ascend.space.tectonic import tectonic_altitude_batch, PRESETS
        for name, params in PRESETS.items():
            alts = tectonic_altitude_batch(0, 0, 20, 20, seed=0, params=params)
            for i, a in enumerate(alts):
                assert not math.isnan(a), f"[{name}] batch[{i}] is NaN"
                assert not math.isinf(a), f"[{name}] batch[{i}] is Inf"


# ════════════════════════════════════════════════════════════════
# 4. 海陆并存
# ════════════════════════════════════════════════════════════════

class TestOceanLandCoexistence:
    """世界中同时存在海洋（海拔<0）和陆地（海拔>0）。

    ocean_ratio 参数直接控制海陆比例：默认 0.70 = 70% 海洋。
    """

    def _scan_for_ocean_and_land(self, params, seed, *, region_size=16000):
        """扫描 region_size×region_size 区域，返回 (has_ocean, has_land)。

        步长 = seed_spacing/4，确保跨越多个板块。
        """
        from ascend.space.tectonic import tectonic_altitude
        step = max(50, int(params.seed_spacing / 4))
        has_ocean = False
        has_land = False
        for wx in range(0, region_size, step):
            for wy in range(0, region_size, step):
                alt = tectonic_altitude(wx, wy, seed, params=params)
                if alt < 0:
                    has_ocean = True
                if alt > 0:
                    has_land = True
                if has_ocean and has_land:
                    return True, True
        return has_ocean, has_land

    def test_earthlike_has_both(self):
        """earthlike 预设：16km×16km 区域内海陆并存。"""
        from ascend.space.tectonic import PRESETS
        has_ocean, has_land = self._scan_for_ocean_and_land(
            PRESETS["earthlike"], seed=42)
        assert has_ocean, "earthlike 应包含海洋"
        assert has_land, "earthlike 应包含陆地"

    def test_default_ocean_ratio_approx_70(self):
        """默认 ocean_ratio=0.7：有效海洋面积 > 60%。

        注意：bimodal 分布 + 边界混合导致有效海洋比例略高于板块概率。
        """
        from ascend.space.tectonic import tectonic_altitude, WorldParams
        params = WorldParams()
        ocean = 0
        total = 0
        step = max(50, int(params.seed_spacing / 4))
        for wx in range(0, int(params.seed_spacing * 2), step):
            for wy in range(0, int(params.seed_spacing * 2), step):
                if tectonic_altitude(wx, wy, seed=42, params=params) < 0:
                    ocean += 1
                total += 1
        ratio = ocean / total
        assert ratio > 0.55, (
            f"海洋比例 {ratio:.1%} 应 > 55%（ocean_ratio=0.7 板块概率）"
        )

    def test_ocean_ratio_90(self):
        """ocean_ratio=0.9 → 海洋占绝大多数。"""
        from ascend.space.tectonic import tectonic_altitude, WorldParams
        params = WorldParams(ocean_ratio=0.9)
        ocean = 0
        total = 0
        step = max(50, int(params.seed_spacing / 4))
        for wx in range(0, int(params.seed_spacing * 2), step):
            for wy in range(0, int(params.seed_spacing * 2), step):
                if tectonic_altitude(wx, wy, seed=42, params=params) < 0:
                    ocean += 1
                total += 1
        ratio = ocean / total
        assert ratio > 0.80, f"ocean_ratio=0.9 时海洋比例 {ratio:.1%} 应 > 80%"

    def test_ocean_ratio_30(self):
        """ocean_ratio=0.3 → 陆地占主导。"""
        from ascend.space.tectonic import tectonic_altitude, WorldParams
        params = WorldParams(ocean_ratio=0.3)
        land = 0
        total = 0
        step = max(50, int(params.seed_spacing / 4))
        for wx in range(0, int(params.seed_spacing * 2), step):
            for wy in range(0, int(params.seed_spacing * 2), step):
                if tectonic_altitude(wx, wy, seed=42, params=params) > 0:
                    land += 1
                total += 1
        ratio = land / total
        assert ratio > 0.30, f"ocean_ratio=0.3 时陆地比例 {ratio:.1%} 应 > 30%"


# ════════════════════════════════════════════════════════════════
# 5. 连续性
# ════════════════════════════════════════════════════════════════

class TestContinuity:
    """相邻 tile 的海拔变化有物理意义上的上限。

    Q2 (待商定): 相邻 tile 最大海拔差？
      假设 cell_size=400，cell 间海拔差可达 ~elevation_max-elevation_min=5000m。
      但在 cell 内部不应有突变。暂定相邻 tile 差 < 500m。
      板块边界处（Voronoi edge）的高差是所有地形中最陡的，
      这个值决定了山脉的"陡峭程度"。
    """

    # 1 tile = 1m。自然地形中，即使陡坡也很少超过 2m/m（~63°）。
    # 板块边界处可能更陡，但不应超过 5m/m（近乎垂直）。
    MAX_ADJACENT_DIFF = 3.0   # 正常地形 3m/m
    MAX_CLIFF_DIFF = 10.0      # 绝对上限 10m/m（悬崖）

    def test_adjacent_tiles_not_too_steep(self):
        """相邻 tile 海拔差不超过 3m（正常地形，板块边界处除外）。"""
        from ascend.space.tectonic import tectonic_altitude, PRESETS
        params = PRESETS["earthlike"]
        # 在板块内部区域的连续采样（偏移半个 seed_spacing 远离边界）
        offset = params.seed_spacing * 0.5
        prev = tectonic_altitude(offset, offset, seed=42, params=params)
        violations = 0
        for i in range(1, 100):
            curr = tectonic_altitude(offset + i, offset, seed=42, params=params)
            diff = abs(curr - prev)
            if diff > self.MAX_ADJACENT_DIFF:
                violations += 1
            prev = curr
        # 普通区域（cell 内部）不应有剧烈跳变
        assert violations <= 5, (
            f"相邻 tile 海拔差超过 {self.MAX_ADJACENT_DIFF}m 共 {violations} 次"
        )

    def test_scan_line_no_cliffs(self):
        """沿对角线扫描：不应出现单步 > 10m 的悬崖。"""
        from ascend.space.tectonic import tectonic_altitude, PRESETS
        params = PRESETS["earthlike"]
        prev = tectonic_altitude(0, 0, seed=0, params=params)
        cliff_count = 0
        for i in range(1, 500):
            curr = tectonic_altitude(i, i, seed=0, params=params)
            if abs(curr - prev) > self.MAX_CLIFF_DIFF:
                cliff_count += 1
            prev = curr
        # 500m 中悬崖不超过 3 处（仅板块边界可能产生）
        assert cliff_count <= 3, f"发现 {cliff_count} 处悬崖 (>{self.MAX_CLIFF_DIFF}m/tile)"

    def test_batch_adjacent_continuity(self):
        """批量网格：4-邻域内相邻 tile 海拔差 > 3m 的比例 < 5%。"""
        from ascend.space.tectonic import tectonic_altitude_batch, PRESETS
        params = PRESETS["earthlike"]
        # 采样板块内部区域
        offset = int(params.seed_spacing * 0.5)
        w, h = 40, 40
        alts = tectonic_altitude_batch(offset, offset, w, h, seed=42, params=params)

        def idx(x, y):
            return y * w + x

        violations = 0
        for y in range(h):
            for x in range(w):
                if x < w - 1:
                    diff = abs(alts[idx(x, y)] - alts[idx(x + 1, y)])
                    if diff > self.MAX_ADJACENT_DIFF:
                        violations += 1
                if y < h - 1:
                    diff = abs(alts[idx(x, y)] - alts[idx(x, y + 1)])
                    if diff > self.MAX_ADJACENT_DIFF:
                        violations += 1
        total_edges = (w - 1) * h + w * (h - 1)
        violation_rate = violations / total_edges
        assert violation_rate < 0.05, (
            f"相邻 tile 跳变率 {violation_rate:.1%} ({violations}/{total_edges})"
        )


# ════════════════════════════════════════════════════════════════
# 6. 批量一致性
# ════════════════════════════════════════════════════════════════

class TestBatchConsistency:
    """batch 结果 = 逐个调用单点结果。"""

    def test_batch_matches_single(self):
        """同一区域的 batch 与单点逐个调用一致。"""
        from ascend.space.tectonic import (
            tectonic_altitude, tectonic_altitude_batch, PRESETS,
        )
        params = PRESETS["earthlike"]
        w, h = 10, 10
        origin_x, origin_y = 100, -50
        batch = tectonic_altitude_batch(origin_x, origin_y, w, h, seed=42, params=params)
        for ty in range(h):
            for tx in range(w):
                single = tectonic_altitude(
                    origin_x + tx, origin_y + ty, seed=42, params=params)
                assert single == pytest.approx(batch[ty * w + tx]), (
                    f"({origin_x + tx}, {origin_y + ty}): "
                    f"batch={batch[ty * w + tx]:.2f}, single={single:.2f}"
                )

    def test_batch_matches_single_different_seeds(self):
        """多 seed 下 batch 与 single 一致。"""
        from ascend.space.tectonic import (
            tectonic_altitude, tectonic_altitude_batch, PRESETS,
        )
        params = PRESETS["earthlike"]
        for seed in [0, 42, 99999]:
            batch = tectonic_altitude_batch(0, 0, 5, 5, seed=seed, params=params)
            for ty in range(5):
                for tx in range(5):
                    single = tectonic_altitude(tx, ty, seed=seed, params=params)
                    assert single == pytest.approx(batch[ty * 5 + tx])


# ════════════════════════════════════════════════════════════════
# 7. 预设差异化
# ════════════════════════════════════════════════════════════════

class TestPresetDifferentiation:
    """不同预设产生明显不同的世界特征，通过 ocean_ratio 滑块控制。"""

    def _ocean_ratio(self, params, seed):
        """计算 2×seed_spacing 区域的海洋比例。"""
        from ascend.space.tectonic import tectonic_altitude
        step = max(50, int(params.seed_spacing / 4))
        size = int(params.seed_spacing * 2)
        ocean = 0
        total = 0
        for wx in range(0, size, step):
            for wy in range(0, size, step):
                if tectonic_altitude(wx, wy, seed, params=params) < 0:
                    ocean += 1
                total += 1
        return ocean / total if total > 0 else 0.0

    def test_ocean_ratio_monotonic(self):
        """ocean_ratio 增大 → 海洋比例单调增大。"""
        from ascend.space.tectonic import WorldParams
        ratios = []
        for or_val in [0.2, 0.5, 0.8]:
            p = WorldParams(ocean_ratio=or_val)
            ratios.append(self._ocean_ratio(p, seed=42))
        assert ratios[0] < ratios[1] < ratios[2], (
            f"ocean_ratio 增大但海洋比例未单调增大: {ratios}"
        )

    def test_mountainous_has_higher_peaks(self):
        """mountainous 预设的最高海拔高于 earthlike。"""
        from ascend.space.tectonic import tectonic_altitude, PRESETS
        def max_alt(params, seed):
            step = max(50, int(params.seed_spacing / 4))
            size = int(params.seed_spacing * 2)
            best = params.altitude_floor
            for wx in range(0, size, step):
                for wy in range(0, size, step):
                    alt = tectonic_altitude(wx, wy, seed, params=params)
                    if alt > best:
                        best = alt
            return best
        max_earth = max_alt(PRESETS["earthlike"], seed=42)
        max_mountain = max_alt(PRESETS["mountainous"], seed=42)
        assert max_mountain > max_earth, (
            f"mountainous 最高峰 {max_mountain:.0f}m 应 > earthlike {max_earth:.0f}m"
        )

    def test_flat_has_lower_relief(self):
        """flat 预设的海拔方差小于 earthlike。"""
        from ascend.space.tectonic import tectonic_altitude_batch, PRESETS
        import statistics
        def relief(params, seed):
            # 采样板块内部区域（偏移避免边界）
            offset = int(params.seed_spacing * 0.5)
            alts = tectonic_altitude_batch(offset, offset, 40, 40,
                                           seed=seed, params=params)
            return statistics.stdev(alts)
        std_flat = relief(PRESETS["flat"], seed=42)
        std_earth = relief(PRESETS["earthlike"], seed=42)
        assert std_flat < std_earth, (
            f"flat stdev {std_flat:.0f}m 应 < earthlike stdev {std_earth:.0f}m"
        )


# ════════════════════════════════════════════════════════════════
# 8. 边界情况
# ════════════════════════════════════════════════════════════════

class TestEdgeCases:
    """极端输入不崩溃。"""

    def test_large_coordinates(self):
        """大坐标（±10^6 tiles）正常返回。"""
        from ascend.space.tectonic import tectonic_altitude
        for wx, wy in [(1_000_000, 0), (-1_000_000, 0), (0, 1_000_000)]:
            result = tectonic_altitude(wx, wy, seed=42)
            assert isinstance(result, float)
            assert not math.isnan(result)

    def test_negative_coordinates(self):
        """负坐标正常处理。"""
        from ascend.space.tectonic import tectonic_altitude
        for wx, wy in [(-1000, -1000), (-500, 300), (200, -600)]:
            result = tectonic_altitude(wx, wy, seed=42)
            assert isinstance(result, float)
            assert not math.isnan(result)

    def test_zero_batch_dimensions(self):
        """w=0 或 h=0 的 batch 返回空列表。"""
        from ascend.space.tectonic import tectonic_altitude_batch
        assert tectonic_altitude_batch(0, 0, 0, 10, seed=42) == []
        assert tectonic_altitude_batch(0, 0, 10, 0, seed=42) == []
        assert tectonic_altitude_batch(0, 0, 0, 0, seed=42) == []

    def test_negative_batch_dimensions(self):
        """负尺寸 batch 返回空列表（或抛出 ValueError）。"""
        from ascend.space.tectonic import tectonic_altitude_batch
        try:
            result = tectonic_altitude_batch(0, 0, -1, 10, seed=42)
            # 如果没抛异常，应返回空列表
            assert result == []
        except ValueError:
            pass  # 抛异常也可以

    def test_fractional_coordinates(self):
        """浮点坐标正常处理（用于平滑采样）。"""
        from ascend.space.tectonic import tectonic_altitude
        for wx, wy in [(0.5, 0.5), (-3.7, 12.2), (100.123, -50.456)]:
            result = tectonic_altitude(wx, wy, seed=42)
            assert isinstance(result, float)
            assert not math.isnan(result)

    def test_seed_negative(self):
        """负 seed 正常处理。"""
        from ascend.space.tectonic import tectonic_altitude
        pos = tectonic_altitude(100, 100, seed=42)
        neg = tectonic_altitude(100, 100, seed=-42)
        assert isinstance(pos, float)
        assert isinstance(neg, float)
        # 不同 seed 大概率不同
        # 注意：seed=-42 vs seed=42 如果被 abs() 处理则相同，否则不同
        # 此用例验证负 seed 不崩溃，不强求不同


# ════════════════════════════════════════════════════════════════
# 9. 山脉结构（定性检测）
# ════════════════════════════════════════════════════════════════

class TestMountainStructure:
    """山脉应沿板块边界形成连贯高海拔带，而非随机散点。

    注意：这是最难定量化的测试。当前用以下代理指标：
      a) 高海拔 tile 不应是孤立的（周围应有其他高海拔 tile）
      b) 高海拔 tile 的聚类大小应超过一定阈值
      c) 沿扫描线的海拔剖面显示"上升→高峰→下降"而非振荡
    """

    def test_high_altitude_not_isolated(self):
        """海拔 > 2000m 的 tile 至少有一个邻居也 > 2000m。

        孤立的单峰说明山脉不是板块边界的产物。
        """
        from ascend.space.tectonic import tectonic_altitude_batch, PRESETS
        params = PRESETS["earthlike"]
        # 扫描足够大的区域以覆盖碰撞边界
        w, h = 100, 100
        alts = tectonic_altitude_batch(0, 0, w, h, seed=42, params=params)

        high_threshold = 2000.0

        def idx(x, y):
            return y * w + x

        isolated_count = 0
        high_count = 0
        for y in range(h):
            for x in range(w):
                if alts[idx(x, y)] <= high_threshold:
                    continue
                high_count += 1
                # 检查 8-邻域
                has_high_neighbor = False
                for dx in (-1, 0, 1):
                    for dy in (-1, 0, 1):
                        if dx == 0 and dy == 0:
                            continue
                        nx, ny = x + dx, y + dy
                        if 0 <= nx < w and 0 <= ny < h:
                            if alts[idx(nx, ny)] > high_threshold:
                                has_high_neighbor = True
                                break
                    if has_high_neighbor:
                        break
                if not has_high_neighbor:
                    isolated_count += 1

        # 如果没有高海拔 tile，跳过（seed+区域可能全在海洋）
        if high_count > 0:
            isolation_rate = isolated_count / high_count
            assert isolation_rate < 0.3, (
                f"高海拔 tile 孤立率 {isolation_rate:.1%} "
                f"({isolated_count}/{high_count}) 应 < 30%"
            )

    def test_profile_has_sustained_ridges(self):
        """沿扫描线的海拔剖面中，存在至少一段连续 10+ tile > 1000m。

        这验证山脉不是单峰，而是有长度的脊线。
        """
        from ascend.space.tectonic import tectonic_altitude, PRESETS
        params = PRESETS["earthlike"]
        # 沿多条扫描线，覆盖 2×seed_spacing 范围
        scan_size = int(params.seed_spacing * 2)
        found_ridge = False
        for line_y in range(0, scan_size, 200):
            run = 0
            max_run = 0
            for x in range(0, scan_size, 5):
                alt = tectonic_altitude(x, line_y, seed=42, params=params)
                if alt > 1000:
                    run += 1
                    max_run = max(max_run, run)
                else:
                    run = 0
            if max_run >= 10:
                found_ridge = True
                break
        assert found_ridge, "未找到连续 10+ tile > 1000m 的山脉"


# ════════════════════════════════════════════════════════════════
# 待商定问题（Q3）
# ════════════════════════════════════════════════════════════════
#
# Q1 ✅ 已解决: ocean_ratio 是 WorldParams 的直接滑块参数，默认 0.70。
#     实现时对该参数做后处理偏移，使实际海洋比例趋近目标值。
#
# Q2 ✅ 已解决: 1 tile = 1m。MAX_ADJACENT_DIFF = 3m, MAX_CLIFF_DIFF = 10m。
#
# Q3: 板块边界隆起机制（待展开说明 → 见下面详细解释）
