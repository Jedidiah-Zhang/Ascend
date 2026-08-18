"""种子派生确定性随机工具测试 — 生成层随机源单一入口契约。

承诺：位置相关 + seed 相关的确定性随机必须经 cell_hash / seed_angle，
禁止裸坐标哈希（否则所有世界模式相同）。
"""

import math

from ascend.space.randomness import cell_hash, seed_angle


class TestSeedAngle:
    def test_range(self):
        for seed in (0, 1, -1, 42, 2**31 - 1):
            assert 0.0 <= seed_angle(seed) < 2.0 * math.pi

    def test_deterministic(self):
        assert seed_angle(42) == seed_angle(42)
        assert seed_angle(0) == seed_angle(0)

    def test_seed_sensitive(self):
        angles = {seed_angle(s) for s in range(32)}
        assert len(angles) > 24

    def test_angle_distribution(self):
        # 8 个 seed 的方向应覆盖全圆（不塌缩到窄扇区）
        dirs = [
            (math.cos(seed_angle(s)), math.sin(seed_angle(s)))
            for s in range(8)
        ]
        xs = [d[0] for d in dirs]
        ys = [d[1] for d in dirs]
        assert max(xs) - min(xs) > 0.8
        assert max(ys) - min(ys) > 0.8


class TestCellHash:
    def test_range(self):
        for wx, wy, seed in [(0, 0, 0), (123, 456, 7), (-5, 10, 99), (2**20, -2**10, 2**31 - 1)]:
            assert 0.0 <= cell_hash(wx, wy, seed) < 1.0

    def test_deterministic(self):
        assert cell_hash(10, 20, 30) == cell_hash(10, 20, 30)
        assert cell_hash(-3, 7, 0) == cell_hash(-3, 7, 0)

    def test_coordinate_injective_on_small_grid(self):
        # 小范围实测无碰撞（线性混合 64→32 压缩非单射，
        # 但工程范围内（网格坐标 + seed 2^20 线性项）碰撞率为零）
        vals = {cell_hash(wx, wy, 0) for wx in range(8) for wy in range(8)}
        assert len(vals) == 64

    def test_seed_injective(self):
        # seed 项系数为奇数（可逆）→ 固定坐标下不同 seed 无碰撞
        vals = {cell_hash(5, 5, s) for s in range(16)}
        assert len(vals) == 16

    def test_seed_pattern_not_merely_offset(self):
        # 旧缺陷形态：seed 只做线性平移（模式整体位移）；
        # 混淆后相邻 seed 的同一坐标取值应均匀散布（非邻近值）
        a = [cell_hash(0, 0, s) for s in range(16)]
        gaps = [abs(a[i] - a[i + 1]) for i in range(15)]
        assert min(gaps) > 0.01
        assert max(gaps) < 0.99