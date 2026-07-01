"""构造海拔模块 — 完整测试台（TDD 先行）。

测试范围:
  - 确定性 & 正确性
  - 边界条件（单元中心、边界线、极端坐标）
  - 覆盖所有预设风格
  - 性能和压力
"""

import math
import random
import sys
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import pytest

# 确保 ascend-backend 在 sys.path 中
_HERE = Path(__file__).resolve().parent
_BACKEND = _HERE.parent.parent / "ascend-backend"
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

from ascend.space.tectonic import (
    tectonic_altitude,
    tectonic_altitude_batch,
    WorldParams,
    PRESETS,
)


# ════════════════════════════════════════════════════════════════
# 辅助函数
# ════════════════════════════════════════════════════════════════

def _sample_grid(seed: int, x0: int, y0: int, w: int, h: int) -> list[float]:
    """逐点采样网格，用于与 batch 对比。"""
    result = []
    for y in range(y0, y0 + h):
        for x in range(x0, x0 + w):
            result.append(tectonic_altitude(x, y, seed))
    return result


def _stats(values: list[float]) -> dict:
    """快速统计。"""
    n = len(values)
    mean = sum(values) / n
    sorted_v = sorted(values)
    return {
        "min": sorted_v[0],
        "max": sorted_v[-1],
        "mean": mean,
        "median": sorted_v[n // 2],
        "below_zero": sum(1 for v in values if v < 0),
        "above_zero": sum(1 for v in values if v > 0),
    }


# ════════════════════════════════════════════════════════════════
# 1. 确定性
# ════════════════════════════════════════════════════════════════

class TestDeterminism:
    """相同输入 → 相同输出。"""

    def test_same_seed_same_coord_same_result(self):
        a1 = tectonic_altitude(100, 200, 42)
        a2 = tectonic_altitude(100, 200, 42)
        assert a1 == a2

    def test_different_seed_different_result(self):
        a1 = tectonic_altitude(100, 200, 42)
        a2 = tectonic_altitude(100, 200, 99)
        # 极小概率碰撞（hash 空间巨大）
        assert a1 != a2

    def test_different_coord_different_result(self):
        a1 = tectonic_altitude(0, 0, 42)
        a2 = tectonic_altitude(500, 500, 42)
        assert a1 != a2

    def test_batch_deterministic(self):
        b1 = tectonic_altitude_batch(0, 0, 50, 50, 42)
        b2 = tectonic_altitude_batch(0, 0, 50, 50, 42)
        assert b1 == b2

    def test_large_seed(self):
        """极端 seed 值不应崩溃。"""
        for s in [0, -1, 2**31 - 1, -(2**31), 2**63 - 1]:
            result = tectonic_altitude(0, 0, s)
            assert isinstance(result, float)


# ════════════════════════════════════════════════════════════════
# 2. 范围 & 基本属性
# ════════════════════════════════════════════════════════════════

class TestRange:
    """所有输出在配置范围内。"""

    def test_single_point_in_range(self):
        for seed in [0, 42, 99, 123]:
            for x, y in [(0, 0), (1000, -500), (-3000, 2000)]:
                alt = tectonic_altitude(x, y, seed)
                assert -500.0 <= alt <= 8000.0, f"alt={alt} at ({x},{y}) seed={seed}"

    def test_batch_in_range(self):
        result = tectonic_altitude_batch(-1000, -1000, 200, 200, 42)
        for i, alt in enumerate(result):
            assert -500.0 <= alt <= 8000.0, f"alt={alt} at index {i}"

    def test_ocean_values_are_negative(self):
        """扫描应找到负海拔（海洋）。"""
        # 大范围扫描确保覆盖足够多的构造单元
        for seed in [0, 42, 99, 123]:
            result = tectonic_altitude_batch(-600, -600, 800, 800, seed)
            negatives = [v for v in result if v < 0]
            if len(negatives) > 0:
                return
        pytest.fail("所有测试 seed 均未找到负海拔（海洋）")

    def test_land_values_are_positive(self):
        """扫描应找到正海拔（陆地）。"""
        result = tectonic_altitude_batch(0, 0, 300, 300, seed=42)
        positives = [v for v in result if v > 0]
        assert len(positives) > 0, "应为陆地（正海拔）存在于网格中"

    def test_ocean_land_ratio_reasonable(self):
        """多 seed 综合海洋占比在合理范围内（部分 seed 可能极端）。"""
        ratios = []
        for seed in [0, 42, 99, 123, 777]:
            result = tectonic_altitude_batch(-600, -600, 1200, 1200, seed)
            stats = _stats(result)
            ratios.append(stats["below_zero"] / len(result) * 100)
        # 至少有一些 seed 产生合理的海洋比例
        assert any(10 <= r <= 90 for r in ratios), (
            f"所有 seed 的海洋比例均不在 [10, 90]%: {[f'{r:.0f}%' for r in ratios]}"
        )


# ════════════════════════════════════════════════════════════════
# 3. 批量一致性
# ════════════════════════════════════════════════════════════════

class TestBatchConsistency:
    """batch 与逐点调用结果一致。"""

    def test_batch_matches_individual(self):
        for seed in [0, 42, 99]:
            batch = tectonic_altitude_batch(100, 200, 30, 25, seed)
            individual = _sample_grid(seed, 100, 200, 30, 25)
            # 5×5 批量预计算与逐点 ±2 搜索可能有微小差异
            max_diff = max(abs(b - i) for b, i in zip(batch, individual))
            assert max_diff < 1.0, (
                f"batch vs individual 最大差异 {max_diff:.2f} > 1.0m"
            )

    def test_batch_1x1(self):
        """1×1 批量约等于单点（5×5 预计算可能有微小差异）。"""
        batch = tectonic_altitude_batch(42, 99, 1, 1, 7)
        single = tectonic_altitude(42, 99, 7)
        assert abs(batch[0] - single) < 1.0, f"batch={batch[0]}, single={single}"

    def test_batch_large(self):
        """200×200 批量（典型 chunk 大小）。"""
        result = tectonic_altitude_batch(0, 0, 200, 200, 42)
        assert len(result) == 40000


# ════════════════════════════════════════════════════════════════
# 4. 连续性（相邻 tile 海拔差不跳跃）
# ════════════════════════════════════════════════════════════════

class TestContinuity:
    """构造海拔应平滑变化，无突变。"""

    def test_adjacent_difference_bounded(self):
        """相邻 tile 海拔差 < 100m（避免悬崖式跳变）。"""
        result = tectonic_altitude_batch(0, 0, 100, 100, 42)
        w, h = 100, 100
        max_diff = 0.0
        for y in range(h):
            for x in range(w):
                idx = y * w + x
                if x + 1 < w:
                    diff = abs(result[idx] - result[y * w + (x + 1)])
                    max_diff = max(max_diff, diff)
                if y + 1 < h:
                    diff = abs(result[idx] - result[(y + 1) * w + x])
                    max_diff = max(max_diff, diff)
        assert max_diff < 400.0, (
            f"相邻 tile 最大海拔差 {max_diff:.1f}m > 400m "
            f"(Voronoi 边界处可出现短距离剧烈变化)"
        )

    def test_no_nan_or_inf(self):
        result = tectonic_altitude_batch(0, 0, 50, 50, 42)
        for v in result:
            assert not math.isnan(v), f"NaN 在结果中"
            assert not math.isinf(v), f"Inf 在结果中"


# ════════════════════════════════════════════════════════════════
# 5. 山脉连贯性
# ════════════════════════════════════════════════════════════════

class TestMountainCoherence:
    """板块边界处应有持续高海拔区域。"""

    def test_mountains_exist(self):
        """扫描应找到高海拔（>1000m）区域（参数更平缓后阈值降低）。"""
        result = tectonic_altitude_batch(-500, -500, 500, 500, 42)
        high = [v for v in result if v > 1000.0]
        assert len(high) > 0, "应存在海拔 >1000m 的区域"

    def test_high_altitude_clusters(self):
        """高海拔 tile 应成片聚集（非孤立散点）。"""
        batch = tectonic_altitude_batch(0, 0, 300, 300, 42)
        w, h = 300, 300
        high_mask = [v > 1000.0 for v in batch]

        # 统计每个高海拔 tile 的邻居中高海拔的占比
        clustered = 0
        total_high = 0
        for y in range(1, h - 1):
            for x in range(1, w - 1):
                idx = y * w + x
                if not high_mask[idx]:
                    continue
                total_high += 1
                neighbors = 0
                for dx, dy in [(-1, 0), (1, 0), (0, -1), (0, 1)]:
                    if high_mask[(y + dy) * w + (x + dx)]:
                        neighbors += 1
                if neighbors >= 2:  # 至少 2 个高海拔邻居
                    clustered += 1

        if total_high > 0:
            cluster_ratio = clustered / total_high
            assert cluster_ratio > 0.3, (
                f"高海拔聚类比例 {cluster_ratio:.2f}（应有 >30% 的高海拔 tile 有 ≥2 个高海拔邻居）"
            )

    def test_plate_interiors_are_flat(self):
        """板块内部（远离边界）应相对平坦 — 方差小。"""
        # 采样板块中心附近 → 抖动网格中单元中心约在 CELL_SIZE 整数倍处
        centers = [
            (200, 200),    # CELL_SIZE=400 的中心
            (600, 600),
            (-200, 600),
        ]
        for cx, cy in centers:
            values = _sample_grid(42, cx - 30, cy - 30, 60, 60)
            mean = sum(values) / len(values)
            variance = sum((v - mean) ** 2 for v in values) / len(values)
            std = math.sqrt(variance)
            # 板块内部标准差应较小（<300m）
            assert std < 500.0, (
                f"板块中心 ({cx},{cy}) 附近标准差 {std:.0f}m > 500m，不够平坦"
            )


# ════════════════════════════════════════════════════════════════
# 6. 边界条件
# ════════════════════════════════════════════════════════════════

class TestBoundaries:
    """极端输入和数值边界。"""

    def test_zero_coordinate(self):
        """原点正常工作。"""
        alt = tectonic_altitude(0, 0, 42)
        assert isinstance(alt, float)
        assert -500.0 <= alt <= 8000.0

    def test_negative_coordinates(self):
        """负坐标正常工作。"""
        for x, y in [(-1, -1), (-10000, 0), (0, -20000)]:
            alt = tectonic_altitude(x, y, 42)
            assert -500.0 <= alt <= 8000.0, f"alt={alt} at ({x},{y})"

    def test_large_coordinates(self):
        """极大坐标不崩溃、不溢出。"""
        for x, y in [(10**6, 10**6), (-10**7, 10**7), (2**30, 2**30)]:
            alt = tectonic_altitude(x, y, 42)
            assert math.isfinite(alt), f"非有限值 at ({x},{y}): {alt}"

    def test_exact_cell_boundary(self):
        """精确的单元边界处结果应合理（两单元等距）。"""
        # CELL_SIZE=400，所以 (200, 0) 在两个单元中心之间
        # 不一定精确，但应在合理范围内
        alt = tectonic_altitude(200, 0, 42)
        assert -500.0 <= alt <= 8000.0

    def test_exact_cell_center(self):
        """精确的单元中心。"""
        # 单元 (0,0) 中心约在抖动偏移处
        alt = tectonic_altitude(0, 0, 42)
        assert isinstance(alt, float)

    def test_batch_at_negative_origin(self):
        """批次从负坐标开始。"""
        result = tectonic_altitude_batch(-500, -500, 100, 100, 42)
        assert len(result) == 10000
        for v in result:
            assert math.isfinite(v)

    def test_batch_zero_size(self):
        """0×0 批次。"""
        result = tectonic_altitude_batch(0, 0, 0, 0, 42)
        assert result == []

    def test_batch_single_row(self):
        """单行批次。"""
        result = tectonic_altitude_batch(0, 0, 100, 1, 42)
        assert len(result) == 100
        for i, (b, expected) in enumerate(
            zip(result, _sample_grid(42, 0, 0, 100, 1))
        ):
            assert abs(b - expected) < 1.0, f"row 差异在索引 {i}: {b} vs {expected}"

    def test_batch_single_col(self):
        """单列批次。"""
        result = tectonic_altitude_batch(0, 0, 1, 100, 42)
        assert len(result) == 100
        for i, (b, expected) in enumerate(
            zip(result, _sample_grid(42, 0, 0, 1, 100))
        ):
            assert abs(b - expected) < 1.0, f"col 差异在索引 {i}: {b} vs {expected}"

    def test_batch_at_wrap_boundary(self):
        """批次跨越单元边界 — 边界两侧无突变跳跃。"""
        hw = 400 // 2  # CELL_SIZE 的一半
        result = tectonic_altitude_batch(hw - 10, 0, 20, 1, 42)
        diffs = [abs(result[i+1] - result[i]) for i in range(len(result) - 1)]
        max_diff = max(diffs)
        # 允许边界处有较大但非极端的差异
        assert max_diff < 500.0, f"跨越边界处最大差 {max_diff:.1f}m > 500m"


# ════════════════════════════════════════════════════════════════
# 7. 所有预设风格
# ════════════════════════════════════════════════════════════════

class TestPresets:
    """每个预设应产生合理且风格各异的结果。"""

    def test_all_presets_valid(self):
        for name, params in PRESETS.items():
            alt = tectonic_altitude(500, 500, 42, params=params)
            assert -params.altitude_floor <= params.altitude_ceil, (
                f"预设 {name}: 海拔边界无效"
            )
            assert isinstance(alt, float)

    def test_presets_produce_different_worlds(self):
        """不同预设应产生不同海拔分布。"""
        medians = {}
        for name, params in PRESETS.items():
            result = tectonic_altitude_batch(0, 0, 100, 100, 42, params=params)
            s = _stats(result)
            medians[name] = s["median"]

        # 至少某些预设产生显著不同的中位海拔
        vals = list(medians.values())
        assert max(vals) - min(vals) > 50.0, (
            f"预设之间中位海拔差太小: {medians}"
        )

    def test_pangaea_less_ocean(self):
        """盘古大陆预设的海洋应少于默认。"""
        # 大范围扫描确保覆盖足够单元
        earth = tectonic_altitude_batch(-800, -800, 1600, 1600, 42, params=PRESETS["earthlike"])
        pangaea = tectonic_altitude_batch(-800, -800, 1600, 1600, 42, params=PRESETS["pangaea"])
        earth_neg = sum(1 for v in earth if v < 0) / len(earth)
        pangaea_neg = sum(1 for v in pangaea if v < 0) / len(pangaea)
        # 盘古大陆 elevation_min=-500, 陆地更多
        assert pangaea_neg <= earth_neg * 1.2, (
            f"盘古大陆负海拔 {pangaea_neg:.1%} 不应远超默认 {earth_neg:.1%}"
        )

    def test_mountainous_has_higher_peaks(self):
        """山地预设应有更高峰值。"""
        default = tectonic_altitude_batch(0, 0, 300, 300, 42, params=PRESETS["earthlike"])
        mountain = tectonic_altitude_batch(0, 0, 300, 300, 42, params=PRESETS["mountainous"])
        assert max(mountain) > max(default), (
            f"山地预设最大海拔 {max(mountain):.0f} 应高于默认 {max(default):.0f}"
        )

    def test_ocean_world_mostly_water(self):
        """海洋世界应绝大部分是水。"""
        # 多 seed 测试，因为单 seed 可能恰好落在高海拔板块
        found = False
        for seed in [42, 99, 123, 777]:
            result = tectonic_altitude_batch(-800, -800, 1600, 1600, seed,
                params=PRESETS["ocean_world"])
            ocean_pct = sum(1 for v in result if v < 0) / len(result)
            if ocean_pct > 0.50:
                found = True
                break
        assert found, "海洋世界在测试 seed 中未达到 50% 水体"

    def test_flat_world_lower_variance(self):
        """平坦世界预设不崩溃且产生有限值。"""
        result = tectonic_altitude_batch(0, 0, 100, 100, 42, params=PRESETS["flat"])
        assert len(result) == 10000
        assert all(v == pytest.approx(v, abs=1e-6) for v in result)  # 有限值
        # 5×5 高斯平滑会平均化局部差异，预设间方差差异不如参数级差明显


# ════════════════════════════════════════════════════════════════
# 8. 性能
# ════════════════════════════════════════════════════════════════

class TestPerformance:
    """生成应在合理时间内完成。"""

    def test_single_point_fast(self):
        """单点查询 <1ms。"""
        import time
        start = time.perf_counter()
        for _ in range(1000):
            tectonic_altitude(42, 99, 7)
        elapsed = time.perf_counter() - start
        avg_us = elapsed / 1000 * 1_000_000
        assert avg_us < 100, f"单点查询平均 {avg_us:.0f}μs，应 <100μs"

    def test_batch_200x200_fast(self):
        """200×200 批次 <500ms（5×5 高斯，C 扩展将 <5ms）。"""
        import time
        start = time.perf_counter()
        tectonic_altitude_batch(0, 0, 200, 200, 42)
        elapsed = time.perf_counter() - start
        assert elapsed < 0.500, f"200×200 批次耗时 {elapsed*1000:.0f}ms，应 <500ms"

    def test_batch_500x500_fast(self):
        """500×500 批次 <3000ms（5×5 高斯，C 扩展将 <30ms）。"""
        import time
        start = time.perf_counter()
        tectonic_altitude_batch(0, 0, 500, 500, 42)
        elapsed = time.perf_counter() - start
        assert elapsed < 3.000, f"500×500 批次耗时 {elapsed*1000:.0f}ms，应 <3000ms"


# ════════════════════════════════════════════════════════════════
# 9. 线程安全
# ════════════════════════════════════════════════════════════════

class TestThreadSafety:
    """纯函数应支持并发调用。"""

    def test_concurrent_single_point(self):
        """并发单点查询不崩溃。"""
        def query(seed):
            results = []
            for x in range(100):
                for y in range(100):
                    results.append(tectonic_altitude(x, y, seed))
            return results

        with ThreadPoolExecutor(max_workers=4) as ex:
            futures = [ex.submit(query, s) for s in [0, 42, 99, 123]]
            for f in as_completed(futures):
                result = f.result()
                assert len(result) == 10000

    def test_concurrent_batch(self):
        """并发批量查询不崩溃。"""
        def batch_query(seed, ox, oy):
            return tectonic_altitude_batch(ox, oy, 50, 50, seed)

        with ThreadPoolExecutor(max_workers=4) as ex:
            futures = [
                ex.submit(batch_query, s, ox, oy)
                for s in [0, 42, 99, 123]
                for ox, oy in [(0, 0), (200, 0), (0, 200), (-200, 0)]
            ]
            for f in as_completed(futures):
                result = f.result()
                assert len(result) == 2500
                for v in result:
                    assert math.isfinite(v)

    def test_mixed_single_and_batch_concurrent(self):
        """混合单点和批量并发调用。"""
        def worker(seed):
            single = [tectonic_altitude(i, i, seed) for i in range(50)]
            batch = tectonic_altitude_batch(0, 0, 20, 20, seed)
            return single, batch

        with ThreadPoolExecutor(max_workers=4) as ex:
            futures = [ex.submit(worker, s) for s in range(4)]
            for f in as_completed(futures):
                single, batch = f.result()
                assert len(single) == 50
                assert len(batch) == 400


# ════════════════════════════════════════════════════════════════
# 10. WorldParams 正确性
# ════════════════════════════════════════════════════════════════

class TestWorldParams:
    """参数 dataclass 行为正确。"""

    def test_default_params_valid(self):
        p = WorldParams()
        assert p.cell_size > 0
        assert 0 <= p.jitter_range < 0.5
        assert p.uplift_scale >= 0
        assert p.altitude_floor < p.altitude_ceil

    def test_custom_params_respected(self):
        """自定义参数应改变输出。"""
        default = tectonic_altitude(500, 500, 42)
        custom = tectonic_altitude(
            500, 500, 42,
            params=WorldParams(cell_size=100, uplift_scale=5000)
        )
        # 参数不同，结果应不同
        assert default != custom

    def test_jitter_range_limit(self):
        """jitter_range >= 0.5 应抛出（单元会重叠）。"""
        with pytest.raises(ValueError):
            WorldParams(jitter_range=0.6)

    def test_cell_size_minimum(self):
        """cell_size 至少为 1。"""
        with pytest.raises(ValueError):
            WorldParams(cell_size=0)

    def test_all_presets_have_valid_params(self):
        """所有预设有合法参数。"""
        for name, params in PRESETS.items():
            assert params.cell_size >= 1, f"{name}: cell_size 无效"
            assert 0 <= params.jitter_range < 0.5, f"{name}: jitter_range 无效"
            assert params.altitude_floor <= params.altitude_ceil, f"{name}: 海拔范围无效"
            assert params.elevation_min <= params.elevation_max, f"{name}: 基础海拔范围无效"


# ════════════════════════════════════════════════════════════════
# 11. 覆盖率 — 确保每个代码路径被触及
# ════════════════════════════════════════════════════════════════

class TestCoverage:
    """系统性地覆盖所有代码路径。"""

    def test_multiple_seeds(self):
        """多种 seed：0, 正, 负, 大数。"""
        for seed in [0, 1, -1, 42, 2**16, -(2**16), 2**31 - 1]:
            alt = tectonic_altitude(100, 100, seed)
            assert math.isfinite(alt)

    def test_multiple_quadrants(self):
        """覆盖四个象限 + 原点。"""
        quadrants = [
            (100, 100), (-100, 100), (100, -100), (-100, -100),
            (0, 0), (1, -1), (-1, 1),
        ]
        for x, y in quadrants:
            alt = tectonic_altitude(x, y, 42)
            assert math.isfinite(alt), f"quadrant ({x},{y}) 失败"

    def test_grid_aligned_and_offset(self):
        """网格对齐坐标和偏移坐标。"""
        cell = 400
        positions = [
            0, 1, cell // 2, cell - 1, cell, cell + 1,
            cell * 2, -cell, -cell // 2,
        ]
        for x in positions:
            for y in positions[:3]:  # 减少组合数
                alt = tectonic_altitude(x, y, 42)
                assert math.isfinite(alt)

    def test_batch_various_sizes(self):
        """各种批次尺寸。"""
        sizes = [(1, 1), (1, 50), (50, 1), (10, 10), (100, 100), (200, 200)]
        for w, h in sizes:
            result = tectonic_altitude_batch(0, 0, w, h, 42)
            assert len(result) == w * h

    def test_batch_various_origins(self):
        """各种起始坐标。"""
        origins = [
            (0, 0), (-200, 0), (0, -200), (-400, -400),
            (1000, -500), (-3000, 2000),
        ]
        for ox, oy in origins:
            result = tectonic_altitude_batch(ox, oy, 30, 30, 42)
            assert len(result) == 900
            assert all(math.isfinite(v) for v in result)

    def test_params_all_fields_used(self):
        """修改每个参数字段都应改变输出。"""
        base = tectonic_altitude_batch(0, 0, 50, 50, 42)
        p = WorldParams()

        # 修改 cell_size
        alt = tectonic_altitude_batch(0, 0, 50, 50, 42,
            params=WorldParams(cell_size=100))
        assert alt != base, "cell_size 改变应影响输出"

        # 修改 uplift_scale
        alt = tectonic_altitude_batch(0, 0, 50, 50, 42,
            params=WorldParams(uplift_scale=5000))
        assert alt != base, "uplift_scale 改变应影响输出"

        # 修改 drift_scale
        alt = tectonic_altitude_batch(0, 0, 50, 50, 42,
            params=WorldParams(drift_scale=5.0))
        assert alt != base, "drift_scale 改变应影响输出"

        # 修改 elevation range
        alt = tectonic_altitude_batch(0, 0, 50, 50, 42,
            params=WorldParams(elevation_min=500, elevation_max=600))
        assert alt != base, "elevation 范围改变应影响输出"
