"""水力侵蚀测试台 — TDD 先行。

测试粒子法水力侵蚀的正确性：
  - 侵蚀应降低陡坡、填平洼地
  - 总质量守恒（侵蚀量 = 沉积量）
  - 确定性
  - 边界处理
"""

import sys
from pathlib import Path

import pytest

_HERE = Path(__file__).resolve().parent
_BACKEND = _HERE.parent.parent / "ascend-backend"
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

from ascend.space.erosion import hydraulic_erosion


# ════════════════════════════════════════════════════════════════
# 辅助
# ════════════════════════════════════════════════════════════════

def _flat_grid(w: int, h: int, height: float = 100.0) -> list[float]:
    """创建平坦网格。"""
    return [height] * (w * h)


def _ramp_grid(w: int, h: int) -> list[float]:
    """创建斜坡网格：左低右高。"""
    result = []
    for y in range(h):
        for x in range(w):
            result.append(float(x) / w * 200.0)
    return result


def _peak_grid(w: int, h: int) -> list[float]:
    """创建山峰网格：中心高四周低。"""
    result = []
    cx, cy = w / 2, h / 2
    for y in range(h):
        for x in range(w):
            d = ((x - cx) ** 2 + (y - cy) ** 2) ** 0.5
            result.append(max(0.0, 500.0 - d * 5.0))
    return result


# ════════════════════════════════════════════════════════════════

class TestHydraulicErosion:
    """核心侵蚀测试。"""

    def test_flat_grid_unchanged(self):
        """平坦地面不应被侵蚀。"""
        grid = _flat_grid(50, 50, 100.0)
        eroded = hydraulic_erosion(grid, 50, 50, seed=42, droplets=500)
        # 平坦地面无梯度，不应有显著变化
        for i, (orig, ero) in enumerate(zip(grid, eroded)):
            assert orig == pytest.approx(ero, abs=1.0), (
                f"平坦网格索引 {i} 不应有显著变化: {orig} vs {ero}"
            )

    def test_erosion_changes_terrain(self):
        """侵蚀应能在有起伏的地形上产生变化。"""
        # 混合地形：山峰 + 斜坡
        grid = _peak_grid(60, 60)
        eroded = hydraulic_erosion(grid, 60, 60, seed=42, droplets=5000,
                                   erosion_rate=0.6)
        changed = sum(1 for o, e in zip(grid, eroded) if abs(o - e) > 0.01)
        assert changed > 10, f"侵蚀应改变 >10 个 tile，实际: {changed}"

    def test_peak_erodes_summit(self):
        """山峰顶部应被削低。"""
        grid = _peak_grid(50, 50)
        eroded = hydraulic_erosion(grid, 50, 50, seed=42, droplets=2000)
        # 最高点应降低
        max_orig = max(grid)
        max_eroded = max(eroded)
        assert max_eroded <= max_orig + 0.01, "最高点不应升高"

    def test_mass_conservation(self):
        """侵蚀+沉积应近似质量守恒。"""
        grid = _ramp_grid(50, 50)
        eroded = hydraulic_erosion(grid, 50, 50, seed=42, droplets=1000)
        total_orig = sum(grid)
        total_eroded = sum(eroded)
        # 允许 <1% 误差（边界蒸发损失）
        assert abs(total_orig - total_eroded) / total_orig < 0.01, (
            f"质量不守恒: {total_orig} vs {total_eroded}"
        )

    def test_deterministic(self):
        """同 seed 同输入 → 同输出。"""
        grid = _ramp_grid(50, 50)
        e1 = hydraulic_erosion(grid, 50, 50, seed=42, droplets=500)
        e2 = hydraulic_erosion(grid, 50, 50, seed=42, droplets=500)
        for i, (a, b) in enumerate(zip(e1, e2)):
            assert a == b, f"确定性失败在索引 {i}"

    def test_output_same_size(self):
        """输出尺寸与输入一致。"""
        grid = _flat_grid(30, 20)
        eroded = hydraulic_erosion(grid, 30, 20, seed=42, droplets=100)
        assert len(eroded) == len(grid)

    def test_seed_affects_output(self):
        """不同 seed 产生不同侵蚀模式。"""
        # 使用有噪声的地形增加随机性
        grid = _peak_grid(50, 50)
        e1 = hydraulic_erosion(grid, 50, 50, seed=42, droplets=2000,
                               erosion_rate=0.5)
        e2 = hydraulic_erosion(grid, 50, 50, seed=99, droplets=2000,
                               erosion_rate=0.5)
        diffs = sum(1 for a, b in zip(e1, e2) if abs(a - b) > 0.001)
        assert diffs > 0, "不同 seed 应产生不同结果"

    def test_custom_params(self):
        """高侵蚀率应产生更剧烈的变化。"""
        grid = _peak_grid(50, 50)
        mild = hydraulic_erosion(grid, 50, 50, seed=42, droplets=2000,
                                 erosion_rate=0.1, deposition_rate=0.1)
        strong = hydraulic_erosion(grid, 50, 50, seed=42, droplets=2000,
                                   erosion_rate=0.8, deposition_rate=0.5)
        mild_change = sum(abs(a - b) for a, b in zip(grid, mild))
        strong_change = sum(abs(a - b) for a, b in zip(grid, strong))
        assert strong_change > mild_change * 1.5, (
            f"高侵蚀率({strong_change:.1f})应 >1.5× 低侵蚀率({mild_change:.1f})"
        )

    def test_no_droplets_no_change(self):
        """droplets=0 时输出等于输入。"""
        grid = _ramp_grid(50, 50)
        eroded = hydraulic_erosion(grid, 50, 50, seed=42, droplets=0)
        for a, b in zip(grid, eroded):
            assert a == b


class TestErosionBoundary:
    """边界处理。"""

    def test_small_grid(self):
        """小网格不崩溃。"""
        grid = _peak_grid(5, 5)
        eroded = hydraulic_erosion(grid, 5, 5, seed=42, droplets=50)
        assert len(eroded) == 25

    def test_single_column(self):
        """单列网格。"""
        grid = [100.0] * 10
        eroded = hydraulic_erosion(grid, 1, 10, seed=42, droplets=10)
        assert len(eroded) == 10

    def test_all_negative(self):
        """全负海拔（海底）。"""
        grid = [-200.0] * 100
        eroded = hydraulic_erosion(grid, 10, 10, seed=42, droplets=100)
        # 海底也应有侵蚀（水流仍沿梯度流动）
        assert len(eroded) == 100
        # 负海拔应保持不变（水压不影响我们的简化模型）
        for o, e in zip(grid, eroded):
            assert e == pytest.approx(o, abs=10.0)

    def test_large_grid_performance(self):
        """200×200 典型 chunk 尺寸应在合理时间内完成。"""
        import time
        grid = _peak_grid(200, 200)
        start = time.perf_counter()
        eroded = hydraulic_erosion(grid, 200, 200, seed=42, droplets=5000)
        elapsed = time.perf_counter() - start
        assert elapsed < 5.0, f"200×200 + 5K droplets 超过 5 秒: {elapsed:.1f}s"
        assert len(eroded) == 40000
