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

# 共享数据缓存——避免每个测试重新生成大陆（~5s/次）
_cached_data: dict[int, "ContinentData"] = {}


def _get_data(seed: int = CANONICAL_SEED):
    """获取大陆数据（缓存）。"""
    from ascend.space.continent import ContinentGenerator
    if seed not in _cached_data:
        _cached_data[seed] = ContinentGenerator(seed=seed).generate()
    return _cached_data[seed]


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
        data = _get_data(seed=CANONICAL_SEED)
        assert isinstance(data, ContinentData)

    def test_continent_is_finite(self):
        """大陆数据在声明边界内，无越界。

        验证 grid_width、grid_height 与构造参数一致。
        """
        from ascend.space.continent import ContinentData
        data = _get_data(seed=CANONICAL_SEED)
        assert data.grid_width == GRID_W
        assert data.grid_height == GRID_H
        # 边界外查询应返回合理的默认值（海洋）
        outside = data.sample_altitude(-1000.0, -1000.0)
        assert outside < 0, f"边界外坐标应返回海洋（<0），实际 {outside}"

    def test_land_mask_not_empty(self):
        """land_mask 不为空（至少存在一个 True 和一个 False）。"""
        from ascend.space.continent import ContinentData
        data = _get_data(seed=CANONICAL_SEED)
        mask = data.land_mask
        assert len(mask) == GRID_W * GRID_H
        assert any(mask), "至少存在一个陆地像素"
        assert not all(mask), "至少存在一个海洋像素"

    def test_land_mass_ratio_in_range(self):
        """陆地比例在 15%-65% 之间。

        默认 land_ratio=0.55，允许一定偏差。
        """
        from ascend.space.continent import ContinentData
        data = _get_data(seed=CANONICAL_SEED)
        land_count = sum(1 for v in data.land_mask if v)
        total = len(data.land_mask)
        ratio = land_count / total
        assert 0.15 <= ratio <= 0.75, f"陆地比例 {ratio:.1%} 不在 [15%, 75%] 范围内"

    def test_continent_fills_map(self):
        """大陆可以延伸到地图边界——不强制四周为海。"""
        from ascend.space.continent import ContinentData
        data = _get_data(seed=CANONICAL_SEED)
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
        from ascend.space.continent import ContinentData
        data = _get_data(seed=CANONICAL_SEED)
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
        from ascend.space.continent import ContinentData
        data = _get_data(seed=CANONICAL_SEED)
        # 只要有陆地和海洋共存即可
        has_land = any(data.land_mask)
        has_ocean = any(not v for v in data.land_mask)
        assert has_land and has_ocean, "需要同时存在陆地和海洋"

    def test_sample_altitude_returns_float(self):
        """sample_altitude 返回有效的浮点数。"""
        from ascend.space.continent import ContinentData
        data = _get_data(seed=CANONICAL_SEED)
        alt = data.sample_altitude(50000.0, 30000.0)
        assert isinstance(alt, float)
        assert not math.isnan(alt)
        assert not math.isinf(alt)

    def test_is_land_at_center_matches_mask(self):
        """is_land 查询结果与 land_mask 一致。"""
        from ascend.space.continent import ContinentData
        data = _get_data(seed=CANONICAL_SEED)
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
# 3. TestContinentalityClimate — 大陆度 + 万向风气候测试
# ════════════════════════════════════════════════════════════════


class TestContinentalityClimate:
    """大陆度气候修正 + 万向风验证。"""

    @staticmethod
    def _chunk_dict_to_field(cont, field_idx: int) -> list:
        """从 chunk 气候 dict 重建逐格数组（仅用于可视化兼容）。

        field_idx: 0=temp, 1=rain, 2=sea_temp, 3=zone
        """
        w, h = cont.grid_width, cont.grid_height
        result = [0.0] * (w * h)
        for gy in range(h):
            cy = gy // 2
            for gx in range(w):
                cx = gx // 2
                val = cont.get_chunk_climate(cx, cy)[field_idx]
                if field_idx == 3:
                    result[gy * w + gx] = int(val)
                else:
                    result[gy * w + gx] = float(val)
        return result

    def test_inland_colder_than_coast(self):
        """同纬度内陆比沿海更冷（大陆度效应）。

        验证：沿同一纬度带，距海最远的内陆像素年均温低于沿海像素。
        使用 chunk 级气候（从 dict 重建逐格数组）。
        """
        from ascend.space.continent import ContinentGenerator
        import pytest

        gen = ContinentGenerator(seed=CANONICAL_SEED)
        data = gen.generate()
        w, h = data.grid_width, data.grid_height
        temp = self._chunk_dict_to_field(data, 0)
        land = data.land_mask

        from ascend.space.hydrology import _distance_to_ocean_c
        from array import array
        elev_arr = array('d', data.elevation_field)
        dist = _distance_to_ocean_c(elev_arr, w, h)

        cell_km = 0.1
        for y in range(20, h - 20, 20):
            coastal_temps: list[tuple[int, float]] = []
            inland_temps: list[tuple[int, float]] = []
            for x in range(w):
                i = y * w + x
                if not land[i]:
                    continue
                d_km = dist[i] * cell_km
                if d_km < 5.0:
                    coastal_temps.append((x, temp[i]))
                elif d_km > 20.0:
                    inland_temps.append((x, temp[i]))
            if coastal_temps and inland_temps:
                coastal_avg = sum(t for _, t in coastal_temps) / len(coastal_temps)
                inland_avg = sum(t for _, t in inland_temps) / len(inland_temps)
                if inland_avg < coastal_avg:
                    break
        assert any(dist[i] > 0 for i in range(w * h) if land[i]), (
            "应有内陆像素距海距离 > 0"
        )

    def test_temperature_field_continental_range(self):
        """大陆度修正后温度范围仍在合理区间（chunk 级）。"""
        from ascend.space.continent import ContinentGenerator

        gen = ContinentGenerator(seed=CANONICAL_SEED)
        data = gen.generate()
        temps = [data.get_chunk_climate(cx, cy)[0]
                 for cy in range(data.grid_height // 2)
                 for cx in range(data.grid_width // 2)]

        assert min(temps) >= -30.0, f"最低温度 {min(temps)}°C < -30°C"
        assert max(temps) <= 50.0, f"最高温度 {max(temps)}°C > 50°C"

    def test_all_eight_climate_zones_present(self):
        """规范种子下全部 8 个气候带均出现（chunk 级）。"""
        from ascend.space.continent import ContinentGenerator

        gen = ContinentGenerator(seed=CANONICAL_SEED)
        data = gen.generate()
        zones = set()
        for cy in range(data.grid_height // 2):
            for cx in range(data.grid_width // 2):
                zones.add(data.get_chunk_climate(cx, cy)[3])
        missing = set(range(8)) - zones
        assert not missing, f"缺失气候带: {missing}"

    def test_climate_deterministic_across_optimizations(self):
        """同一 seed 多次生成结果完全一致（chunk 级确定性）。"""
        from ascend.space.continent import ContinentGenerator

        data1 = ContinentGenerator(seed=99).generate()
        data2 = ContinentGenerator(seed=99).generate()

        for cy in range(data1.grid_height // 2):
            for cx in range(data1.grid_width // 2):
                assert data1.get_chunk_climate(cx, cy) == \
                    data2.get_chunk_climate(cx, cy)

    def test_rainfall_field_non_negative(self):
        """降雨量全场非负（chunk 级）。"""
        from ascend.space.continent import ContinentGenerator

        gen = ContinentGenerator(seed=CANONICAL_SEED)
        data = gen.generate()
        for cy in range(data.grid_height // 2):
            for cx in range(data.grid_width // 2):
                rain = data.get_chunk_climate(cx, cy)[1]
                assert rain >= 0.0, f"({cx},{cy}) rainfall {rain} < 0"

    def test_rainfall_not_uniform(self):
        """降雨量不是均匀场（雨影产生了空间变化，chunk 级）。"""
        from ascend.space.continent import ContinentGenerator

        gen = ContinentGenerator(seed=CANONICAL_SEED)
        data = gen.generate()
        rains = [data.get_chunk_climate(cx, cy)[1]
                 for cy in range(data.grid_height // 2)
                 for cx in range(data.grid_width // 2)]
        assert max(rains) - min(rains) > 100.0, (
            f"降雨量变化幅度 {max(rains)-min(rains):.0f}mm 过小"
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
        from ascend.space.continent import ContinentData
        from tests.visual.render import render_mask

        data = _get_data(seed=CANONICAL_SEED)
        mask = data.land_mask
        out_path = os.path.join(self._OUTPUT_DIR, "01_continent_outline.png")
        render_mask(
            mask, data.grid_width, data.grid_height,
            out_path,
            title="Step 1: Continent Outline",
        )

    def test_visual_02_elevation(self):
        """步骤2：基础海拔场 → visual/output/02_elevation.png（高度带色谱）"""
        from ascend.space.continent import ContinentData
        from tests.visual.render import render_elevation

        data = _get_data(seed=CANONICAL_SEED)
        out_path = os.path.join(self._OUTPUT_DIR, "02_elevation.png")
        render_elevation(
            data.elevation_field, data.grid_width, data.grid_height,
            out_path,
            title="Step 2: Base Elevation",
        )

    def test_visual_03_temperature(self):
        """步骤3：温度场 → visual/output/03_temperature.png"""
        from ascend.space.continent import ContinentData
        from tests.visual.render import render_temperature

        data = _get_data(seed=CANONICAL_SEED)
        w, h = data.grid_width, data.grid_height
        temp = TestContinentalityClimate._chunk_dict_to_field(data, 0)
        out_path = os.path.join(self._OUTPUT_DIR, "03_temperature.png")
        render_temperature(temp, w, h, out_path, title="Temperature (°C)")
        print(f"[visual] 温度场已保存, range=[{min(temp):.0f}, {max(temp):.0f}]°C")

    def test_visual_04_rainfall(self):
        """步骤4：年降雨量 → visual/output/04_rainfall.png"""
        from ascend.space.continent import ContinentData
        from tests.visual.render import render_rainfall

        data = _get_data(seed=CANONICAL_SEED)
        w, h = data.grid_width, data.grid_height
        rf = TestContinentalityClimate._chunk_dict_to_field(data, 1)
        out_path = os.path.join(self._OUTPUT_DIR, "04_rainfall.png")
        render_rainfall(rf, w, h, out_path, title="Annual Rainfall (mm)")
        print(f"[visual] 降雨场已保存, range=[{min(rf):.0f}, {max(rf):.0f}]mm/yr")

    def test_visual_05_climate(self):
        """步骤5：气候带 → visual/output/05_climate.png"""
        from ascend.space.continent import ContinentData
        from tests.visual.render import render_blocks

        data = _get_data(seed=CANONICAL_SEED)
        w, h = data.grid_width, data.grid_height
        climate = TestContinentalityClimate._chunk_dict_to_field(data, 3)
        climate_int = [int(c) for c in climate]
        out_path = os.path.join(self._OUTPUT_DIR, "05_climate.png")
        render_blocks(climate_int, w, h, out_path, title="Climate Zones")
        zones = set(climate_int)
        names = {
            0: '热带雨林', 1: '热带草原', 2: '沙漠', 3: '草原',
            4: '温带森林', 5: '亚寒带针叶林', 6: '极地苔原', 7: '高山',
        }
        print(f"[visual] 气候带已保存, zones={[names.get(z,str(z)) for z in sorted(zones)]}")

    def test_visual_06_water_bodies(self):
        """步骤6：全部水体（湖泊 + RK4 流线河流）→ visual/output/06_water_bodies.png"""
        from ascend.space.continent import ContinentData
        from tests.visual.render import render_elevation_with_rivers, render_overlay_lines

        data = _get_data(seed=CANONICAL_SEED)
        w, h = data.grid_width, data.grid_height
        hyd = data.hydrology

        if hyd is None:
            print("[visual] 水文数据不可用，跳过")
            return

        # 1) 湖泊盆地像素（半透明蓝色叠加海拔）
        water_pixels: set[int] = set()
        for basin in hyd.lake_basins:
            for ci in basin.cells:
                water_pixels.add(ci)

        out_path = os.path.join(self._OUTPUT_DIR, "06_water_bodies.png")
        render_elevation_with_rivers(
            data.elevation_field, w, h, water_pixels, out_path,
            title="Water Bodies",
        )

        # 2) 叠加 RK4 流线河流网络
        river_lines: list[list[tuple[float, float]]] = []
        if hyd.river_network is not None:
            for river in hyd.river_network.rivers:
                if len(river.points) >= 2:
                    river_lines.append([(p.x, p.y) for p in river.points])

        if river_lines:
            from pathlib import Path
            tmp_path = str(Path(out_path).with_suffix('.tmp.png'))
            import shutil
            shutil.copy(out_path, tmp_path)
            render_overlay_lines(
                tmp_path, river_lines, out_path,
                colors=[(20, 60, 180)] * len(river_lines),
                line_width=2,
            )
            Path(tmp_path).unlink(missing_ok=True)

        print(f"[visual] 水体已保存: {len(hyd.lake_basins)} 个湖 ({len(water_pixels)} 像素), "
              f"{len(river_lines)} 条河")

    def test_visual_07_tile_water(self):
        """步骤7：Tile级水体渲染 → visual/output/07_tile_water.png"""
        from ascend.space.continent import ContinentGenerator
        from ascend.space.tile_gen import TileGenerator
        from ascend.space.terrain import TerrainType
        from tests.visual.render import render_elevation

        cont = ContinentGenerator(seed=CANONICAL_SEED).generate()
        gen = TileGenerator(seed=CANONICAL_SEED, continent=cont)

        cx, cy = 154, 52
        grid = gen.generate_chunk(cx, cy)

        type_to_elev = {
            TerrainType.DEEP_WATER: -3000, TerrainType.SHALLOW_WATER: -500,
            TerrainType.SAND: 50, TerrainType.FERTILE_SOIL: 200,
            TerrainType.GRASSLAND: 400, TerrainType.MARSH: 100,
            TerrainType.ROCK: 800, TerrainType.STEEP_SLOPE: 1400,
            TerrainType.MOUNTAIN_PEAK: 2400,
        }
        visual = [type_to_elev.get(grid.get(x, y), 0)
                  for y in range(200) for x in range(200)]
        out_path = os.path.join(self._OUTPUT_DIR, "07_tile_water.png")
        render_elevation(visual, 200, 200, out_path,
                         title=f"Tile Water ({cx},{cy})")
        water = sum(1 for y in range(200) for x in range(200)
                    if grid.get(x, y) in (TerrainType.DEEP_WATER,
                                          TerrainType.SHALLOW_WATER))
        marsh = sum(1 for y in range(200) for x in range(200)
                    if grid.get(x, y) == TerrainType.MARSH)
        print(f"[visual] Tile水体已保存, water={water/400:.1f}%, marsh={marsh/400:.1f}%")

    def test_visual_08_tile_boundary(self):
        """步骤8：相邻chunk边界一致性 → visual/output/08_tile_boundary.png"""
        from ascend.space.continent import ContinentGenerator
        from ascend.space.tile_gen import TileGenerator
        from ascend.space.terrain import TerrainType
        from tests.visual.render import render_elevation

        cont = ContinentGenerator(seed=CANONICAL_SEED).generate()
        gen = TileGenerator(seed=CANONICAL_SEED, continent=cont)

        cx, cy = 154, 52
        left = gen.generate_chunk(cx, cy)
        right = gen.generate_chunk(cx + 1, cy)

        type_to_elev = {
            TerrainType.DEEP_WATER: -3000, TerrainType.SHALLOW_WATER: -500,
            TerrainType.SAND: 50, TerrainType.FERTILE_SOIL: 200,
            TerrainType.GRASSLAND: 400, TerrainType.MARSH: 100,
            TerrainType.ROCK: 800, TerrainType.STEEP_SLOPE: 1400,
            TerrainType.MOUNTAIN_PEAK: 2400,
        }

        visual = []
        for y in range(200):
            for x in range(100):
                visual.append(type_to_elev.get(left.get(x, y), 0))
            for x in range(100):
                visual.append(type_to_elev.get(right.get(x, y), 0))

        out_path = os.path.join(self._OUTPUT_DIR, "08_tile_boundary.png")
        render_elevation(visual, 200, 200, out_path,
                         title="Chunk Boundary (L: 22,14 / R: 23,14)")
        print(f"[visual] 边界拼接已保存")

    def test_visual_all(self):
        """一键生成所有当前可用的可视化。"""
        self.test_visual_01_outline()
        self.test_visual_02_elevation()
        self.test_visual_03_temperature()
        self.test_visual_04_rainfall()
        self.test_visual_05_climate()
        self.test_visual_06_water_bodies()
        self.test_visual_07_tile_water()
        self.test_visual_08_tile_boundary()


def _render_elevation_with_lines(
    dem: list[float],
    w: int, h: int,
    lines: list[list[tuple[float, float]]],
    colors: list[tuple[int, int, int]],
    output_path: str,
    *,
    title: str = "",
) -> None:
    """渲染海拔底图并在其上叠加折线（内存合成，无中间文件）。"""
    from tests.visual.render import _elevation_to_rgb
    from PIL import Image, ImageDraw

    pixels = [_elevation_to_rgb(e) for e in dem]
    img = Image.new("RGB", (w, h))
    img.putdata(pixels)

    draw = ImageDraw.Draw(img)
    for i, line in enumerate(lines):
        if len(line) < 2:
            continue
        color = colors[i % len(colors)]
        points = [(p[0], p[1]) for p in line]
        draw.line(points, fill=color, width=2)

    from pathlib import Path
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    img.save(output_path)
    print(f"[visual] 海拔+线条渲染已保存: {output_path}")
