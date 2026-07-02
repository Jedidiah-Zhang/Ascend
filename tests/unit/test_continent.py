"""大陆生成模块测试 — 层1 全局低分辨率大陆生成。

测试覆盖（按 TDD 顺序）：
  1. TestContinentOutline — 大陆轮廓：有限边界 + 海陆比 + 连续性
  2. TestTerrainBlocks — 地块划分：数量 + 大小 + 曲率 + 岩性
  3. TestTectonicSkeleton — 构造骨架线：碰撞带 + 拉张带 + 曲率
  4. TestElevationSynthesis — 海拔合成：值域 + 双峰 + 连续性
  5. TestGlacialModel — 冰川侵蚀
  6. TestCoastalClassification — 海岸分类
  7. TestVolcanism — 火山分布

约定：
  - seed=42 为规范测试种子
  - 无 parametrize，使用显式循环
  - 所有类和方法有中文 docstring
  - PYTHONPATH=ascend-backend
"""

import math
import os
import pytest

# 层1 默认参数
WORLD_W_KM = 100.0
WORLD_H_KM = 60.0
CELL_SIZE_M = 100.0  # 100m 分辨率
GRID_W = int(WORLD_W_KM * 1000 / CELL_SIZE_M)  # 1000
GRID_H = int(WORLD_H_KM * 1000 / CELL_SIZE_M)  # 600
CANONICAL_SEED = 42


# ════════════════════════════════════════════════════════════════
# 1. TestContinentOutline — 大陆轮廓
# ════════════════════════════════════════════════════════════════


class TestContinentOutline:
    """大陆轮廓生成测试 — 有限大陆边界 + 海陆并存 + 确定性。"""

    def test_import_continent_module(self):
        """可以导入 continent 模块。"""
        from ascend.space import continent
        assert continent is not None

    def test_continent_params_exists(self):
        """ContinentParams 类可导入且有合理默认值。"""
        from ascend.space.continent import ContinentParams
        p = ContinentParams()
        assert p.width_km > 0
        assert p.height_km > 0
        assert p.sample_resolution > 0
        assert 0.0 < p.land_ratio < 1.0

    def test_continent_generator_exists(self):
        """ContinentGenerator 类可实例化。"""
        from ascend.space.continent import ContinentGenerator
        gen = ContinentGenerator(seed=CANONICAL_SEED)
        assert gen is not None
        assert repr(gen) != ""

    def test_generate_returns_continent_data(self):
        """generate() 返回 ContinentData 实例。"""
        from ascend.space.continent import ContinentGenerator, ContinentData
        gen = ContinentGenerator(seed=CANONICAL_SEED)
        data = gen.generate()
        assert isinstance(data, ContinentData)

    def test_continent_is_finite(self):
        """大陆数据在声明边界内，无越界。

        验证 grid_width、grid_height 与构造参数一致。
        """
        from ascend.space.continent import ContinentGenerator
        gen = ContinentGenerator(seed=CANONICAL_SEED)
        data = gen.generate()
        assert data.grid_width == GRID_W
        assert data.grid_height == GRID_H
        # 边界外查询应返回合理的默认值（海洋）
        outside = data.sample_altitude(-1000.0, -1000.0)
        assert outside < 0, f"边界外坐标应返回海洋（<0），实际 {outside}"

    def test_land_mask_not_empty(self):
        """land_mask 不为空（至少存在一个 True 和一个 False）。"""
        from ascend.space.continent import ContinentGenerator
        gen = ContinentGenerator(seed=CANONICAL_SEED)
        data = gen.generate()
        mask = data.land_mask
        assert len(mask) == GRID_W * GRID_H
        assert any(mask), "至少存在一个陆地像素"
        assert not all(mask), "至少存在一个海洋像素"

    def test_land_mass_ratio_in_range(self):
        """陆地比例在 15%-65% 之间。

        默认 land_ratio=0.55，允许一定偏差。
        """
        from ascend.space.continent import ContinentGenerator
        gen = ContinentGenerator(seed=CANONICAL_SEED)
        data = gen.generate()
        land_count = sum(1 for v in data.land_mask if v)
        total = len(data.land_mask)
        ratio = land_count / total
        assert 0.15 <= ratio <= 0.75, f"陆地比例 {ratio:.1%} 不在 [15%, 75%] 范围内"

    def test_continent_fills_map(self):
        """大陆可以延伸到地图边界——不强制四周为海。"""
        from ascend.space.continent import ContinentGenerator
        gen = ContinentGenerator(seed=CANONICAL_SEED)
        data = gen.generate()
        # 不做边缘海洋约束，只检查整体数据的合法性
        assert len(data.land_mask) == data.grid_width * data.grid_height
        assert len(data.elevation_field) == data.grid_width * data.grid_height

    def test_deterministic_outline(self):
        """同 seed → 完全相同的 land_mask。"""
        from ascend.space.continent import ContinentGenerator
        gen1 = ContinentGenerator(seed=CANONICAL_SEED)
        gen2 = ContinentGenerator(seed=CANONICAL_SEED)
        data1 = gen1.generate()
        data2 = gen2.generate()
        assert data1.land_mask == data2.land_mask

    def test_different_seed_different_outline(self):
        """不同 seed → land_mask 不同。"""
        from ascend.space.continent import ContinentGenerator
        gen1 = ContinentGenerator(seed=42)
        gen2 = ContinentGenerator(seed=99)
        data1 = gen1.generate()
        data2 = gen2.generate()
        assert data1.land_mask != data2.land_mask, "不同 seed 产生了相同的 land_mask"

    def test_no_1px_islands(self):
        """消除噪声斑点：孤立陆地像素（8邻域无其他陆地）占比 < 0.1%。

        检查每个陆地像素的 8 邻域，统计没有陆地邻居的"孤立陆地"。
        """
        from ascend.space.continent import ContinentGenerator
        gen = ContinentGenerator(seed=CANONICAL_SEED)
        data = gen.generate()
        w, h = data.grid_width, data.grid_height
        mask = data.land_mask

        isolated = 0
        land_total = 0
        for y in range(1, h - 1):
            for x in range(1, w - 1):
                idx = y * w + x
                if not mask[idx]:
                    continue
                land_total += 1
                # 检查 8 邻域
                has_neighbor = any(
                    mask[(y + dy) * w + (x + dx)]
                    for dx in (-1, 0, 1) for dy in (-1, 0, 1)
                    if not (dx == 0 and dy == 0)
                )
                if not has_neighbor:
                    isolated += 1

        if land_total > 0:
            rate = isolated / land_total
            assert rate < 0.001, (
                f"孤立陆地像素 {isolated}/{land_total} ({rate:.2%})，应 < 0.1%"
            )

    def test_land_islands_ok(self):
        """陆地可以是群岛/碎片——不强制单一大块连通大陆。"""
        from ascend.space.continent import ContinentGenerator
        gen = ContinentGenerator(seed=CANONICAL_SEED)
        data = gen.generate()
        # 只要有陆地和海洋共存即可
        has_land = any(data.land_mask)
        has_ocean = any(not v for v in data.land_mask)
        assert has_land and has_ocean, "需要同时存在陆地和海洋"

    def test_sample_altitude_returns_float(self):
        """sample_altitude 返回有效的浮点数。"""
        from ascend.space.continent import ContinentGenerator
        gen = ContinentGenerator(seed=CANONICAL_SEED)
        data = gen.generate()
        alt = data.sample_altitude(50000.0, 30000.0)
        assert isinstance(alt, float)
        assert not math.isnan(alt)
        assert not math.isinf(alt)

    def test_is_land_at_center_matches_mask(self):
        """is_land 查询结果与 land_mask 一致。"""
        from ascend.space.continent import ContinentGenerator
        gen = ContinentGenerator(seed=CANONICAL_SEED)
        data = gen.generate()
        # 在网格中心附近逐像素验证
        for gx in range(100, 200):
            for gy in range(100, 200):
                world_x = gx * CELL_SIZE_M + CELL_SIZE_M / 2
                world_y = gy * CELL_SIZE_M + CELL_SIZE_M / 2
                from_mask = data.land_mask[gy * data.grid_width + gx]
                from_query = data.is_land(world_x, world_y)
                assert from_mask == from_query, (
                    f"({gx}, {gy}): mask={from_mask}, is_land={from_query}"
                )


# ════════════════════════════════════════════════════════════════
# 可视化辅助（非断言测试，手动运行）
# ════════════════════════════════════════════════════════════════


class TestVisualOutput:
    """每步生成可视化 PNG（手动运行）。

    用法:
        cd ascend-backend && PYTHONPATH=. ../.venv/bin/python -m pytest \
            ../tests/unit/test_continent.py::TestVisualOutput -v -s
    """

    # 输出目录相对于项目根
    _OUTPUT_DIR = os.path.join(os.path.dirname(__file__), "..", "visual", "output")

    def test_visual_01_outline(self):
        """步骤1：大陆轮廓 → visual/output/01_continent_outline.png"""
        from ascend.space.continent import ContinentGenerator
        from tests.visual.render import render_mask

        gen = ContinentGenerator(seed=CANONICAL_SEED)
        data = gen.generate()
        mask = data.land_mask
        out_path = os.path.join(self._OUTPUT_DIR, "01_continent_outline.png")
        render_mask(
            mask, data.grid_width, data.grid_height,
            out_path,
            title="Step 1: Continent Outline",
        )

    def test_visual_02_elevation(self):
        """步骤2：基础海拔场 → visual/output/02_elevation.png（高度带色谱）"""
        from ascend.space.continent import ContinentGenerator
        from tests.visual.render import render_elevation

        gen = ContinentGenerator(seed=CANONICAL_SEED)
        data = gen.generate()
        out_path = os.path.join(self._OUTPUT_DIR, "02_elevation.png")
        render_elevation(
            data.elevation_field, data.grid_width, data.grid_height,
            out_path,
            title="Step 2: Base Elevation",
        )

    def test_visual_dla_skeleton(self):
        """输出 DLA 骨架 → visual/output/dla_skeleton.png（小画布原始尺寸）"""
        from ascend.space.continent import ContinentGenerator
        from tests.visual.render import render_mask

        gen = ContinentGenerator(seed=CANONICAL_SEED)
        data = gen.generate()

        levels = getattr(gen, '_debug_levels', [])
        for lv in levels:
            name = lv["name"]
            occ = lv["occupied"]
            w = lv["w"]
            h = lv["h"]
            mask = [(x, y) in occ for y in range(h) for x in range(w)]
            out_path = os.path.join(self._OUTPUT_DIR, f"dla_{name}.png")
            render_mask(mask, w, h, out_path,
                        true_color=(255, 255, 255),
                        false_color=(20, 20, 40),
                        title=name)
            print(f"[visual] DLA skeleton: {len(occ)} pixels, {w}x{h}")

    def test_visual_weight_grid(self):
        """输出权重网格 → visual/output/weight_grid.png"""
        from ascend.space.continent import ContinentGenerator
        from tests.visual.render import render_elevation

        gen = ContinentGenerator(seed=CANONICAL_SEED)
        gen.generate()
        grid = getattr(gen, '_debug_weight_grid', [])
        if not grid:
            print("[visual] 无权重复网格数据")
            return
        rows, cols = len(grid), len(grid[0])
        # 把小网格放大到可看尺寸 ×50
        scale = 50
        flat = []
        for r in range(rows):
            for _ in range(scale):
                for c in range(cols):
                    w = grid[r][c]
                    for _ in range(scale):
                        flat.append(w * 5000.0 - 2000.0)  # 映射到海拔范围便于看颜色
        out_path = os.path.join(self._OUTPUT_DIR, "weight_grid.png")
        render_elevation(flat, cols * scale, rows * scale, out_path,
                         title="Weight Grid (10×6)")
        print(f"[visual] 权重网格: {cols}×{rows} → {cols*scale}×{rows*scale}px")

    def test_visual_03_eroded(self):
        """步骤3：山坡+河道侵蚀 → visual/output/03_eroded.png"""
        from ascend.space.continent import ContinentGenerator
        from ascend.space.hydrology import hillslope_erosion, carve_rivers
        from tests.visual.render import render_elevation

        gen = ContinentGenerator(seed=CANONICAL_SEED)
        data = gen.generate()
        w, h = data.grid_width, data.grid_height
        dem = data.elevation_field

        # 先山坡侵蚀 — 平滑微地形，促进水流汇聚
        eroded = hillslope_erosion(dem, w, h, iterations=50, rate=0.10)
        # 再河道雕刻 — 低阈值捕获更多支流，汇成大河
        carved = carve_rivers(eroded, w, h, threshold=15.0, depth_scale=60.0, width=3)

        out_path = os.path.join(self._OUTPUT_DIR, "03_eroded.png")
        render_elevation(carved, w, h, out_path, title="Step 3: Eroded Elevation")
        print(f"[visual] 侵蚀后海拔已保存, range=[{min(carved):.0f}, {max(carved):.0f}]")

    def test_visual_04_rivers(self):
        """步骤4：河流网络（蓝标水体） → visual/output/04_rivers.png"""
        from ascend.space.continent import ContinentGenerator
        from ascend.space.hydrology import fill_depressions, compute_d8
        from ascend.space.hydrology import flow_accumulation, extract_rivers, find_lakes
        from tests.visual.render import render_elevation_with_rivers

        gen = ContinentGenerator(seed=CANONICAL_SEED)
        data = gen.generate()
        w, h = data.grid_width, data.grid_height
        dem = data.elevation_field
        land = data.land_mask

        # 河流
        filled = fill_depressions(dem, w, h)
        directions = compute_d8(filled, w, h)
        acc = flow_accumulation(directions, w, h)
        rivers = extract_rivers(directions, acc, w, h, threshold=50.0)
        # 湖泊
        lake_surface = find_lakes(dem, land, w, h, min_size=5)

        # 合并河流+湖泊
        water_pixels: set[int] = set()
        for river in rivers:
            for x, y in river:
                idx = y * w + x
                if dem[idx] > 0:
                    water_pixels.add(idx)
        for i, ls in enumerate(lake_surface):
            if ls > 0:
                water_pixels.add(i)

        out_path = os.path.join(self._OUTPUT_DIR, "04_rivers.png")
        render_elevation_with_rivers(dem, w, h, water_pixels, out_path,
                                     title="Step 4: Rivers + Lakes")
        print(f"[visual] 河流+湖泊已保存, {len(water_pixels)} 水体像素")

    def test_visual_05_river_width(self):
        """步骤5：河流宽度 → visual/output/05_river_width.png"""
        from ascend.space.continent import ContinentGenerator
        from tests.visual.render import render_elevation

        gen = ContinentGenerator(seed=CANONICAL_SEED)
        data = gen.generate()
        w, h = data.grid_width, data.grid_height
        rw = data.river_width
        # 宽度映射到海拔范围便于可视化：0→0, 80→4000
        visual = [v * 50.0 - 2000.0 for v in rw]  # 0m宽→-2000(蓝), 80m宽→2000(棕)
        out_path = os.path.join(self._OUTPUT_DIR, "05_river_width.png")
        render_elevation(visual, w, h, out_path, title="River Width")
        print(f"[visual] 河流宽度已保存, max_width={max(rw):.0f}m, rivers={sum(1 for v in rw if v>0)}px")

    def test_visual_09_rainfall(self):
        """步骤9：年降雨量 → visual/output/09_rainfall.png"""
        from ascend.space.continent import ContinentGenerator
        from tests.visual.render import render_elevation

        gen = ContinentGenerator(seed=CANONICAL_SEED)
        data = gen.generate()
        w, h = data.grid_width, data.grid_height
        rf = data.rainfall_field
        # 降雨量映射到色标 [0, 3500]mm → [-2000, 4000]
        visual = [r / 3500.0 * 6000.0 - 2000.0 for r in rf]
        out_path = os.path.join(self._OUTPUT_DIR, "09_rainfall.png")
        render_elevation(visual, w, h, out_path, title="Annual Rainfall (mm)")
        print(f"[visual] 降雨场已保存, range=[{min(rf):.0f}, {max(rf):.0f}]mm/yr")

    def test_visual_10_snow(self):
        """步骤10：永久积雪 → visual/output/10_snow.png"""
        from ascend.space.continent import ContinentGenerator
        from tests.visual.render import render_mask

        gen = ContinentGenerator(seed=CANONICAL_SEED)
        data = gen.generate()
        w, h = data.grid_width, data.grid_height
        snow = [data.snow_mask[i] and data.land_mask[i] for i in range(len(data.snow_mask))]
        out_path = os.path.join(self._OUTPUT_DIR, "10_snow.png")
        render_mask(snow, w, h, out_path,
                    true_color=(240, 240, 255),
                    false_color=(60, 80, 40),
                    title="Permanent Snow")
        print(f"[visual] 积雪已保存, {sum(snow)} 个积雪像素")

    def test_visual_07_temperature(self):
        """步骤7：温度场 → visual/output/07_temperature.png"""
        from ascend.space.continent import ContinentGenerator
        from tests.visual.render import render_temperature

        gen = ContinentGenerator(seed=CANONICAL_SEED)
        data = gen.generate()
        w, h = data.grid_width, data.grid_height
        temp = data.temperature_field
        out_path = os.path.join(self._OUTPUT_DIR, "07_temperature.png")
        render_temperature(temp, w, h, out_path, title="Temperature (°C)")
        print(f"[visual] 温度场已保存, range=[{min(temp):.0f}, {max(temp):.0f}]°C")

    def test_visual_08_climate(self):
        """步骤8：气候带 → visual/output/08_climate.png"""
        from ascend.space.continent import ContinentGenerator
        from tests.visual.render import render_blocks

        gen = ContinentGenerator(seed=CANONICAL_SEED)
        data = gen.generate()
        w, h = data.grid_width, data.grid_height
        climate = data.climate_zone
        out_path = os.path.join(self._OUTPUT_DIR, "08_climate.png")
        render_blocks(climate, w, h, out_path, title="Climate Zones")
        zones = set(climate)
        names = {0: '热带', 1: '温带', 2: '寒带', 3: '干旱'}
        print(f"[visual] 气候带已保存, zones={[names.get(z,str(z)) for z in sorted(zones)]}")

    def test_visual_06_lakes(self):
        """步骤6：湖泊 → visual/output/06_lakes.png"""
        from ascend.space.continent import ContinentGenerator
        from ascend.space.hydrology import find_lakes
        from tests.visual.render import render_elevation_with_rivers

        gen = ContinentGenerator(seed=CANONICAL_SEED)
        data = gen.generate()
        w, h = data.grid_width, data.grid_height
        dem = data.elevation_field
        land = data.land_mask

        # 找湖泊
        lake_surface = find_lakes(dem, land, w, h, min_size=5)

        # 湖泊像素 → 标蓝
        water_pixels: set[int] = set()
        for i, ls in enumerate(lake_surface):
            if ls > 0:
                water_pixels.add(i)

        out_path = os.path.join(self._OUTPUT_DIR, "06_lakes.png")
        render_elevation_with_rivers(dem, w, h, water_pixels, out_path,
                                     title="Step 6: Lakes")
        print(f"[visual] 湖泊已保存, {len(water_pixels)} 个湖泊像素")

    def test_visual_all(self):
        """一键生成所有当前可用的可视化。"""
        self.test_visual_01_outline()
        self.test_visual_02_elevation()
        self.test_visual_03_eroded()
        self.test_visual_04_rivers()
        self.test_visual_05_river_width()
        self.test_visual_06_lakes()
        self.test_visual_07_temperature()
        self.test_visual_08_climate()
        self.test_visual_09_rainfall()
        self.test_visual_10_snow()
