"""距水距离场测试 — 多源 BFS（C 加速）与双线性采样接口。

覆盖 ascend/space/water_distance.py 的 compute_water_distance（C 加速）
与 ContinentData.sample_water_distance_bilinear。

纯 Python 参考实现（_reference_py）仅作为 C 输出的正确性 oracle——
生产路径走 C（见模块 docstring）。
"""

import random
from array import array
from collections import deque

import pytest

from ascend.space.continent_data import ContinentData
from ascend.space.water_distance import compute_water_distance


def _reference_py(water_mask, width: int, height: int, cell_size: float) -> array:
    """纯 Python 多源 BFS（4 邻域）参考实现（C 输出的 oracle）。

    与生产函数同语义：无水体源（全陆）抛 ValueError。
    """
    n = width * height
    dist = array("d", [0.0]) * n
    if n == 0:
        return dist
    if not any(water_mask):
        raise ValueError("water_mask 无水体源（全陆）：距水距离无定义")
    queue: deque[int] = deque()
    for i, is_water in enumerate(water_mask):
        if is_water:
            queue.append(i)
        else:
            dist[i] = -1.0
    step_m = float(cell_size)
    while queue:
        i = queue.popleft()
        x = i % width
        y = i // width
        d = dist[i] + step_m
        for j in (
            i - 1 if x > 0 else -1,
            i + 1 if x + 1 < width else -1,
            i - width if y > 0 else -1,
            i + width if y + 1 < height else -1,
        ):
            if j >= 0 and dist[j] < 0.0:
                dist[j] = d
                queue.append(j)
    return dist


class TestComputeWaterDistance:
    """多源 BFS 距离场正确性（C 加速路径）。"""

    def test_single_water_strip_manhattan(self):
        """5×5，中列水体：距离按 4 邻域步数 × cell_size 递增。"""
        w = h = 5
        mask = [False] * (w * h)
        for y in range(h):
            mask[y * w + 2] = True  # 中列 x=2 全水体
        dist = compute_water_distance(mask, w, h, cell_size=100.0)
        # 同行：x=2 是源，x=1/3 距 1 步，x=0/4 距 2 步
        assert dist[0] == 200.0       # (0,0)
        assert dist[1] == 100.0       # (1,0)
        assert dist[2] == 0.0         # (2,0) 水源
        assert dist[3] == 100.0       # (3,0)
        assert dist[4] == 200.0       # (4,0)

    def test_4neighbor_no_diagonal_shortcut(self):
        """对角不视为直达：斜邻水体需 2 步（不斜穿窄陆地）。"""
        w = h = 4
        mask = [False] * (w * h)
        mask[2 * w + 2] = True  # (2,2) 唯一水体
        dist = compute_water_distance(mask, w, h, cell_size=100.0)
        assert dist[1 * w + 1] == 200.0  # (1,1) 曼哈顿 2 步，非 141.4

    def test_multi_source_merges(self):
        """多个水源：取最近源的距离。"""
        w = h = 5
        mask = [False] * (w * h)
        mask[0] = True          # (0,0)
        mask[4 * w + 4] = True  # (4,4)
        dist = compute_water_distance(mask, w, h, cell_size=100.0)
        # 中心 (2,2)：到 (0,0) 4 步 vs (4,4) 4 步 → 400
        assert dist[2 * w + 2] == 400.0

    def test_all_land_rejected(self):
        """全陆地（无水体源）：距水无定义，fail fast 抛 ValueError。

        避免"全陆=全 0"与"水体=0"语义混淆。
        """
        with pytest.raises(ValueError, match="无水体源"):
            compute_water_distance([False] * 9, 3, 3, cell_size=100.0)

    def test_all_water_zero(self):
        """全水体：距离全 0。"""
        dist = compute_water_distance([True] * 9, 3, 3, cell_size=100.0)
        assert list(dist) == [0.0] * 9

    def test_cell_size_scaling(self):
        """距离 = 步数 × cell_size。"""
        w = h = 3
        mask = [False] * (w * h)
        mask[0] = True
        dist = compute_water_distance(mask, w, h, cell_size=250.0)
        assert dist[1] == 250.0
        assert dist[2] == 500.0

    def test_empty_grid(self):
        """空网格返回空数组。"""
        assert compute_water_distance([], 0, 0, cell_size=100.0) == array('d')

    def test_length_mismatch_rejected(self):
        """mask 长度与宽×高不符抛 ValueError。"""
        with pytest.raises(ValueError, match="water_mask 长度"):
            compute_water_distance([True] * 10, 3, 3, cell_size=100.0)

    def test_distance_increasing_away_from_source(self):
        """远离水源距离单调不减（沿海岸线扩散正确）。"""
        w = h = 6
        mask = [False] * (w * h)
        for y in range(h):
            mask[y * w + 0] = True  # 左列海岸
        dist = compute_water_distance(mask, w, h, cell_size=100.0)
        for y in range(h):
            row = [dist[y * w + x] for x in range(w)]
            assert row == [0.0, 100.0, 200.0, 300.0, 400.0, 500.0]


class TestCMatchesPythonReference:
    """C 加速输出与纯 Python 参考实现一致（随机 + 边角构型）。"""

    def test_random_grids_match(self):
        """多种子随机网格：C 与参考逐元素一致（保证至少一个水源）。"""
        for seed in range(8):
            rng = random.Random(seed)
            w, h = rng.randint(1, 12), rng.randint(1, 12)
            n = w * h
            mask = [rng.random() < 0.3 for _ in range(n)]
            if not any(mask):
                continue  # 全陆：双方都抛 ValueError，语义一致由专门测试覆盖
            cell = float(rng.choice([50.0, 100.0, 250.0]))
            expected = _reference_py(mask, w, h, cell)
            actual = compute_water_distance(mask, w, h, cell)
            assert list(actual) == list(expected), f"seed={seed} {w}×{h}"

    def test_edge_cases_match(self):
        """全水/单源角格/空图/全陆拒绝等边角与参考一致。"""
        # 有源构型：C 与参考逐元素一致
        match_cases = [
            ([True] * 9, 3, 3, 100.0),
            ([True] + [False] * 8, 3, 3, 200.0),
            ([], 0, 0, 100.0),
            ([True] * 1, 1, 1, 100.0),
        ]
        for mask, w, h, cell in match_cases:
            assert list(compute_water_distance(mask, w, h, cell)) == list(
                _reference_py(mask, w, h, cell)
            )
        # 全陆：C 与参考都抛 ValueError
        for mask, w, h in [([False] * 9, 3, 3), ([False] * 1, 1, 1)]:
            with pytest.raises(ValueError, match="无水体源"):
                compute_water_distance(mask, w, h, 100.0)
            with pytest.raises(ValueError, match="无水体源"):
                _reference_py(mask, w, h, 100.0)


def _manual_continent() -> ContinentData:
    """4×4 距水场：值 = x×100（行优先），便于精确算双线性。"""
    wd = array('d', [float(x * 100) for _ in range(4) for x in range(4)])
    return ContinentData(
        grid_width=4, grid_height=4, cell_size=100.0, seed=1,
        water_distance=wd,
    )


class TestSampleWaterDistance:
    """sample_water_distance_bilinear 采样。"""

    def test_grid_point_exact(self):
        """恰在格点采样 = 该格值。"""
        cont = _manual_continent()
        # 世界 x=50 → gx=0.0（50/100-0.5=0）
        assert cont.sample_water_distance_bilinear(50.0, 50.0) == 0.0
        # 世界 x=150 → gx=1.0 → 第 1 列 = 100
        assert cont.sample_water_distance_bilinear(150.0, 50.0) == 100.0

    def test_bilinear_midpoint(self):
        """两格中点 = 均值。"""
        cont = _manual_continent()
        # x=100 → gx=0.5，y=50 → gy=0.0：v00=0, v10=100 → 50
        assert cont.sample_water_distance_bilinear(100.0, 50.0) == pytest.approx(50.0)

    def test_out_of_bounds_zero(self):
        """越界视为海洋 → 距水 0。"""
        cont = _manual_continent()
        assert cont.sample_water_distance_bilinear(-50.0, 50.0) == 0.0
        assert cont.sample_water_distance_bilinear(1000.0, 1000.0) == 0.0

    def test_empty_field_zero(self):
        """距水场未生成（空）→ 返回 0。"""
        cont = ContinentData(grid_width=4, grid_height=4, cell_size=100.0, seed=1)
        assert cont.sample_water_distance_bilinear(100.0, 100.0) == 0.0

    def test_non_default_resolution(self):
        """cell_size≠100（如测试小尺寸大陆）时换算仍正确。

        换算用 self.cell_size 而非全局常量 CONTINENT_SAMPLE_RESOLUTION_M。
        """
        wd = array('d', [float(x * 200) for _ in range(4) for x in range(4)])
        cont = ContinentData(
            grid_width=4, grid_height=4, cell_size=200.0, seed=1,
            water_distance=wd,
        )
        # 世界 x=100 → gx=0.0（100/200-0.5）→ 第 0 列 = 0
        assert cont.sample_water_distance_bilinear(100.0, 100.0) == 0.0
        # 世界 x=200 → gx=0.5 → 列 0(0) 与列 1(200) 中点 = 100
        assert cont.sample_water_distance_bilinear(200.0, 100.0) == pytest.approx(100.0)
        # 世界 x=300 → gx=1.0 → 第 1 列 = 200
        assert cont.sample_water_distance_bilinear(300.0, 100.0) == pytest.approx(200.0)


class TestWaterDistanceIntegration:
    """端到端：小大陆生成后距水场与水系一致。"""

    def test_small_continent_water_distance(self):
        """小大陆：水体格距离 0，陆地格为正，且与 land_mask 语义一致。

        水体 = not land_mask（海）∪ river_width>0（河/湖）——
        与 continent.generate 的距水掩码判定完全一致。
        """
        from ascend.space.continent import ContinentGenerator, ContinentParams
        cont = ContinentGenerator(
            seed=7,
            params=ContinentParams(
                width_km=6.0, height_km=4.0, sample_resolution=200,
            ),
        ).generate()
        assert len(cont.water_distance) == cont.grid_width * cont.grid_height
        for i, (is_land, rw, wd) in enumerate(zip(
            cont.land_mask, cont.river_width, cont.water_distance,
        )):
            if (not is_land) or rw > 0.0:
                assert wd == 0.0, f"格 {i} 应为水体（距水 0）"
            else:
                assert wd > 0.0, f"格 {i} 陆地距水应为正"
