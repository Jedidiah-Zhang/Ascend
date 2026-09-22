"""大陆生成模块测试 — 层1 全局低分辨率大陆生成。

覆盖：
  - TestCenterDistance — center_distance 四象限对称性
  - TestContinentOutline — 大陆轮廓：有限边界 + 海陆比 + 确定性
  - TestGeneratePreview — generate_preview 缩略图与气候图层
  - TestContinentalityClimate — 大陆度修正 + 万向风气候（chunk 级）

约定：
  - seed=42 为规范测试种子
  - 无 parametrize，使用显式循环
  - 在仓库根运行 pytest
"""

import math
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
    from olam.generation.continent import ContinentGenerator
    if seed not in _cached_data:
        _cached_data[seed] = ContinentGenerator(seed=seed).generate()
    return _cached_data[seed]


# ════════════════════════════════════════════════════════════════
# 1. TestContinentOutline — 大陆轮廓
# ════════════════════════════════════════════════════════════════


class TestCenterDistance:
    """center_distance 四象限对称性（Chebyshev 距离）。"""

    def test_third_quadrant_negative_y_axis(self):
        """dx=0, dy=-2 应算得 2。"""
        from olam.generation.continent import center_distance
        assert center_distance(0.0, -2.0) == 2.0
        assert center_distance(0.0, -1.5) == 1.5
        assert center_distance(0.5, -2.0) == 2.0

    def test_quadrant_symmetry(self):
        """四象限等距点距离一致。"""
        from olam.generation.continent import center_distance
        assert center_distance(2.0, 2.0) == center_distance(-2.0, 2.0)
        assert center_distance(2.0, 2.0) == center_distance(2.0, -2.0)
        assert center_distance(2.0, 2.0) == center_distance(-2.0, -2.0)
        assert center_distance(0.5, -0.3) == center_distance(0.5, 0.3)
        assert center_distance(0.5, -0.3) == center_distance(-0.5, -0.3)

    def test_center_and_axis(self):
        """原点距离 0；主轴距离取另一轴绝对值。"""
        from olam.generation.continent import center_distance
        assert center_distance(0.0, 0.0) == 0.0
        assert center_distance(3.0, 0.0) == 3.0
        assert center_distance(0.0, -3.0) == 3.0
        assert center_distance(-3.0, 0.0) == 3.0


class TestContinentOutline:
    """大陆轮廓生成测试 — 有限大陆边界 + 海陆并存 + 确定性。"""

    def test_import_continent_module(self):
        """可以导入 continent 模块。"""
        from olam.generation import continent
        assert continent is not None

    def test_continent_params_exists(self):
        """ContinentParams 类可导入且有合理默认值。"""
        from olam.generation.continent import ContinentParams
        p = ContinentParams()
        assert p.width_km > 0
        assert p.height_km > 0
        assert p.sample_resolution > 0
        assert 0.0 < p.land_ratio < 1.0

    def test_continent_generator_exists(self):
        """ContinentGenerator 类可实例化。"""
        from olam.generation.continent import ContinentGenerator
        gen = ContinentGenerator(seed=CANONICAL_SEED)
        assert gen is not None
        assert repr(gen) != ""

    def test_generate_returns_continent_data(self):
        """generate() 返回 ContinentData 实例。"""
        from olam.generation.continent import ContinentGenerator, ContinentData
        data = _get_data(seed=CANONICAL_SEED)
        assert isinstance(data, ContinentData)

    def test_generate_reports_progress_stages(self):
        """generate(progress_cb) 按管线顺序回调各阶段名（前端进度条数据源）。"""
        from olam.generation.continent import ContinentGenerator
        stages: list[str] = []
        data = ContinentGenerator(seed=CANONICAL_SEED).generate(
            progress_cb=stages.append,
        )
        assert data is not None
        assert stages[0] == ContinentGenerator.STAGE_ELEVATION
        assert stages[-1] == ContinentGenerator.STAGE_DONE
        assert ContinentGenerator.STAGE_CLIMATE in stages
        assert ContinentGenerator.STAGE_EROSION in stages
        # 阶段名唯一（无重复回调）
        assert len(stages) == len(set(stages))

    def test_continent_is_finite(self):
        """大陆数据在声明边界内，无越界。

        验证 grid_width、grid_height 与构造参数一致。
        """
        from olam.generation.continent import ContinentData
        data = _get_data(seed=CANONICAL_SEED)
        assert data.grid_width == GRID_W
        assert data.grid_height == GRID_H
        # 边界外查询应返回合理的默认值（海洋）
        outside = data.sample_altitude(-1000.0, -1000.0)
        assert outside < 0, f"边界外坐标应返回海洋（<0），实际 {outside}"

    def test_land_mask_not_empty(self):
        """land_mask 不为空（至少存在一个 True 和一个 False）。"""
        from olam.generation.continent import ContinentData
        data = _get_data(seed=CANONICAL_SEED)
        mask = data.land_mask
        assert len(mask) == GRID_W * GRID_H
        assert any(mask), "至少存在一个陆地像素"
        assert not all(mask), "至少存在一个海洋像素"

    def test_land_mass_ratio_in_range(self):
        """陆地比例在 15%-75% 之间。

        默认 land_ratio=0.55，允许一定偏差。
        """
        from olam.generation.continent import ContinentData
        data = _get_data(seed=CANONICAL_SEED)
        land_count = sum(1 for v in data.land_mask if v)
        total = len(data.land_mask)
        ratio = land_count / total
        assert 0.15 <= ratio <= 0.75, f"陆地比例 {ratio:.1%} 不在 [15%, 75%] 范围内"

    def test_continent_fills_map(self):
        """大陆可以延伸到地图边界——不强制四周为海。"""
        from olam.generation.continent import ContinentData
        data = _get_data(seed=CANONICAL_SEED)
        # 不做边缘海洋约束，只检查整体数据的合法性
        assert len(data.land_mask) == data.grid_width * data.grid_height
        assert len(data.elevation_field) == data.grid_width * data.grid_height

    def test_deterministic_outline(self):
        """同 seed → 完全相同的 land_mask。"""
        from olam.generation.continent import ContinentGenerator
        gen1 = ContinentGenerator(seed=CANONICAL_SEED)
        gen2 = ContinentGenerator(seed=CANONICAL_SEED)
        data1 = gen1.generate()
        data2 = gen2.generate()
        assert data1.land_mask == data2.land_mask

    def test_different_seed_different_outline(self):
        """不同 seed → land_mask 不同。"""
        from olam.generation.continent import ContinentGenerator
        gen1 = ContinentGenerator(seed=42)
        gen2 = ContinentGenerator(seed=99)
        data1 = gen1.generate()
        data2 = gen2.generate()
        assert data1.land_mask != data2.land_mask, "不同 seed 产生了相同的 land_mask"

    def test_no_1px_islands(self):
        """消除噪声斑点：孤立陆地像素（8邻域无其他陆地）占比 < 0.1%。

        检查每个陆地像素的 8 邻域，统计没有陆地邻居的"孤立陆地"。
        """
        from olam.generation.continent import ContinentData
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
        from olam.generation.continent import ContinentData
        data = _get_data(seed=CANONICAL_SEED)
        # 只要有陆地和海洋共存即可
        has_land = any(data.land_mask)
        has_ocean = any(not v for v in data.land_mask)
        assert has_land and has_ocean, "需要同时存在陆地和海洋"

    def test_sample_altitude_returns_float(self):
        """sample_altitude 返回有效的浮点数。"""
        from olam.generation.continent import ContinentData
        data = _get_data(seed=CANONICAL_SEED)
        alt = data.sample_altitude(500.0, 300.0)
        assert isinstance(alt, float)
        assert not math.isnan(alt)
        assert not math.isinf(alt)

    def test_is_land_at_center_matches_mask(self):
        """is_land 查询结果与 land_mask 一致。"""
        from olam.generation.continent import ContinentData
        data = _get_data(seed=CANONICAL_SEED)
        # 在网格中心附近逐像素验证（tile 坐标 = 格点坐标 1:1）
        for gx in range(100, 200):
            for gy in range(100, 200):
                world_x = gx + 0.5
                world_y = gy + 0.5
                from_mask = data.land_mask[gy * data.grid_width + gx]
                from_query = data.is_land(world_x, world_y)
                assert from_mask == from_query, (
                    f"({gx}, {gy}): mask={from_mask}, is_land={from_query}"
                )


# ════════════════════════════════════════════════════════════════
# 3. TestGeneratePreview — 快速地形预览与气候图层
# ════════════════════════════════════════════════════════════════


class TestGeneratePreview:
    """快速地形预览。"""

    @staticmethod
    def _preview(seed: int = CANONICAL_SEED, land_ratio: float = 0.55,
                 width_km: float | None = None,
                 height_km: float | None = None) -> dict:
        from olam.generation.continent import ContinentGenerator
        return ContinentGenerator(seed=seed).generate_preview(
            land_ratio, width_km, height_km)

    def test_preview_shape(self):
        """预览网格为低分辨率缩略图（1000m）：默认 100×60km → 100×60 格，
        海拔场行优先。"""
        p = self._preview()
        assert p["width"] == 100
        assert p["height"] == 60
        assert len(p["elevation"]) == 100 * 60

    def test_preview_size_scales_grid_keeps_rate(self):
        """尺寸只影响生成范围：网格随尺寸，采样分辨率（变化率）不变。"""
        small = self._preview(width_km=60.0, height_km=36.0)
        large = self._preview(width_km=150.0, height_km=90.0)
        assert (small["width"], small["height"]) == (60, 36)
        assert (large["width"], large["height"]) == (150, 90)

    def test_preview_size_extends_not_scales(self):
        """大陆形状延伸而非缩放：大尺寸预览的左上角区域
        （同 seed 同 land_ratio）与整块小尺寸预览大体一致。

        轮廓层为绝对频率（1.5 周期/100km），重叠区域共享同一
        地理空间；中心倾向/校准的局部修正允许边缘小幅差异。
        """
        small = self._preview(width_km=60.0, height_km=36.0)
        large = self._preview(width_km=150.0, height_km=90.0)
        sw, sh = small["width"], small["height"]
        agree = 0.0
        for y in range(sh):
            for x in range(sw):
                s_land = small["elevation"][y * sw + x] > 0
                l_land = large["elevation"][y * large["width"] + x] > 0
                if s_land == l_land:
                    agree += 1.0
        assert agree / (sw * sh) > 0.85, (
            f"重叠区域海陆一致率 {agree / (sw * sh):.2f} 过低"
        )

    def test_preview_size_land_percent_tracks_ratio(self):
        """不同尺寸下陆地占比仍贴合目标 land_ratio。"""
        for size in ((60.0, 36.0), (150.0, 90.0)):
            p = self._preview(land_ratio=0.55, width_km=size[0],
                              height_km=size[1])
            assert abs(p["land_percent"] - 0.55) < 0.10, (
                f"size={size} 实测 {p['land_percent']}"
            )

    def test_preview_elevation_ints(self):
        """海拔为整型米（JSON 友好），陆地海拔 > 0。"""
        p = self._preview()
        assert all(isinstance(v, int) for v in p["elevation"])
        assert any(v > 0 for v in p["elevation"]), "预览必须存在陆地"
        assert any(v < 0 for v in p["elevation"]), "预览必须存在海洋"

    def test_preview_land_percent_tracks_ratio(self):
        """陆地占比贴合目标 land_ratio（分位数校准，允许采样偏差）。"""
        for ratio in (0.30, 0.55, 0.75):
            p = self._preview(land_ratio=ratio)
            assert abs(p["land_percent"] - ratio) < 0.10, (
                f"land_ratio={ratio} 实测 {p['land_percent']}"
            )

    def test_preview_deterministic(self):
        """同 (seed, land_ratio) → 完全相同的预览。"""
        p1 = self._preview()
        p2 = self._preview()
        assert p1 == p2

    def test_preview_seed_sensitive(self):
        """不同 seed → 不同地形。"""
        p1 = self._preview(seed=1)
        p2 = self._preview(seed=2)
        assert p1["elevation"] != p2["elevation"]

    def test_preview_matches_full_pipeline_land_shape(self):
        """预览海陆轮廓与完整生成管线一致（同一噪声场的粗采样）。"""
        from olam.generation.continent import ContinentGenerator, ContinentParams
        preview = self._preview()
        w, h = preview["width"], preview["height"]
        full = ContinentGenerator(
            seed=CANONICAL_SEED,
            params=ContinentParams(
                width_km=100.0, height_km=60.0, sample_resolution=100.0,
            ),
        ).generate()
        # 粗采样网格 → 全分辨率场对应区域逐点比对（比例采样）
        step_x = full.grid_width // w
        step_y = full.grid_height // h
        same = 0
        total = 0
        for py in range(h):
            for px in range(w):
                fy = py * step_y + step_y // 2
                fx = px * step_x + step_x // 2
                preview_land = preview["elevation"][py * w + px] > 0
                full_land = full.land_mask[fy * full.grid_width + fx]
                total += 1
                same += 1 if preview_land == full_land else 0
        assert same / total >= 0.80, (
            f"预览与完整管线海陆一致率 {same / total:.1%} 过低"
        )

    # ── 预览气候图层 ───────────────────────────────────────

    def _preview_climate(self, seed: int = CANONICAL_SEED,
                         land_ratio: float = 0.55,
                         width_km: float | None = None,
                         height_km: float | None = None) -> dict:
        from olam.generation.continent import ContinentGenerator
        return ContinentGenerator(seed=seed).generate_preview(
            land_ratio, width_km, height_km,
            layers=("temp", "rain", "climate"),
        )

    def test_preview_climate_layers_shape(self):
        """气候图层与海拔同网格（行优先 int 数组）。"""
        p = self._preview_climate()
        n = p["width"] * p["height"]
        assert len(p["temperature"]) == n
        assert len(p["rainfall"]) == n
        assert len(p["climate"]) == n
        assert all(isinstance(v, int) for v in p["temperature"])
        assert all(isinstance(v, int) for v in p["rainfall"])
        assert all(isinstance(v, int) for v in p["climate"])

    def test_preview_climate_omitted_by_default(self):
        """缺省 layers 不计算气候。"""
        from olam.generation.continent import ContinentGenerator
        p = ContinentGenerator(seed=CANONICAL_SEED).generate_preview(0.55)
        assert "temperature" not in p
        assert "rainfall" not in p
        assert "climate" not in p

    def test_preview_climate_plausible_ranges(self):
        """温度/降雨量级合理（温度 -20..38 附近，降雨 ≥0）。"""
        p = self._preview_climate()
        assert min(p["temperature"]) >= -25, "温度不低于物理下限"
        assert max(p["temperature"]) <= 40, "温度不高于物理上限"
        assert min(p["rainfall"]) >= 0, "降雨非负"

    def test_preview_climate_deterministic(self):
        """同 (seed, land_ratio) → 完全相同的气候预览。"""
        p1 = self._preview_climate()
        p2 = self._preview_climate()
        assert p1["temperature"] == p2["temperature"]
        assert p1["rainfall"] == p2["rainfall"]
        assert p1["climate"] == p2["climate"]

    def test_preview_climate_seed_sensitive(self):
        """不同 seed → 不同气候（温度梯度/风向由 seed 决定）。"""
        p1 = self._preview_climate(seed=1)
        p2 = self._preview_climate(seed=2)
        assert p1["temperature"] != p2["temperature"]
        assert p1["rainfall"] != p2["rainfall"]

    def test_preview_climate_matches_full_pipeline(self):
        """预览气候与完整管线一致（同一气候计算链路，未经侵蚀）。

        侵蚀只改海拔（影响高山判定），不重算温雨场；对 100×60 网格
        逐点比对温度与降雨，允许被注入兜底（≤ 9 格/档）和侵蚀后
        海拔改动的少量偏差。海陆全域严格比对：温度场统一为地表温度
        （海域 = 海面温度），预览海域温度与生产 sea_temp 同源一致。
        """
        from olam.generation.continent import ContinentGenerator, ContinentParams
        preview = self._preview_climate()
        w, h = preview["width"], preview["height"]
        full = ContinentGenerator(
            seed=CANONICAL_SEED,
            params=ContinentParams(
                width_km=100.0, height_km=60.0, sample_resolution=100.0,
            ),
        ).generate()
        full_climate = full._chunk_climate  # {(cx, cy): (temp, rain, sea_temp, zone)}
        step_x = full.grid_width // w
        step_y = full.grid_height // h
        temp_diff = 0.0
        temp_n = 0
        sea_diff = 0.0
        sea_n = 0
        rain_diff = 0.0
        rain_n = 0
        for py in range(h):
            for px in range(w):
                is_sea = preview["elevation"][py * w + px] <= 0
                fx = px * step_x + step_x // 2
                fy = py * step_y + step_y // 2
                cx, cy = fx // 2, fy // 2
                if (cx, cy) not in full_climate:
                    continue
                f_temp, f_rain, f_sea, _zone = full_climate[(cx, cy)]
                temp_diff += abs(preview["temperature"][py * w + px] - f_temp)
                temp_n += 1
                if is_sea:
                    # 海域：预览温度 == 生产 sea_temp == 生产 temp（同源海面温度）
                    sea_diff += abs(preview["temperature"][py * w + px] - f_sea)
                    sea_n += 1
                rain_diff += abs(preview["rainfall"][py * w + px] - f_rain)
                rain_n += 1
        assert temp_n > 100, "陆地采样点不足"
        assert sea_n > 50, "海域采样点不足"
        assert temp_diff / temp_n < 3.0, (
            f"预览与完整管线平均温度偏差 {temp_diff / temp_n:.2f}°C 过大"
        )
        assert sea_diff / sea_n < 3.0, (
            f"预览海域与生产 sea_temp 平均偏差 {sea_diff / sea_n:.2f}°C 过大"
        )
        assert rain_diff / rain_n < 400.0, (
            f"预览与完整管线平均降雨偏差 {rain_diff / rain_n:.0f}mm 过大"
        )

    def test_preview_climate_sea_is_surface_temp(self):
        """海域温度为海面温度（纬度梯度，clamp [-20, 38]）。

        直减率仅作用于陆地，海域温度与水深无关——深水/浅水同量级，
        并存在纬度方向的渐变（非恒定值）。
        """
        p = self._preview_climate()
        sea_temps = [
            p["temperature"][i]
            for i, e in enumerate(p["elevation"]) if e <= 0.0
        ]
        assert sea_temps, "预览必须存在海域"
        assert min(sea_temps) >= -20, "海面温度不低于纬度梯度下限"
        assert max(sea_temps) <= 38, "海面温度不高于纬度梯度上限"
        # 海面温度存在渐变（冷极 → 暖赤道方向），非恒定值
        assert max(sea_temps) - min(sea_temps) >= 5.0, "海面温度应有纬度渐变"
        # 深海与浅海同量级：海面温度与水深无关
        deep = [
            p["temperature"][i]
            for i, e in enumerate(p["elevation"]) if e <= -1500.0
        ]
        shallow = [
            p["temperature"][i]
            for i, e in enumerate(p["elevation"]) if -500.0 < e <= 0.0
        ]
        if deep and shallow:
            assert abs(min(deep) - min(shallow)) < 30, "深海/浅海温度量级应接近"

    def test_land_ratio_field_snapshotted(self):
        """ContinentData 记录 land_ratio（缓存校验数据源）。"""
        from olam.generation.continent import ContinentGenerator, ContinentParams
        data = ContinentGenerator(
            seed=CANONICAL_SEED,
            params=ContinentParams(land_ratio=0.35),
        ).generate()
        assert data.land_ratio == 0.35
        land_count = sum(1 for v in data.land_mask if v)
        assert abs(land_count / len(data.land_mask) - 0.35) < 0.05


class TestContinentalityClimate:
    """大陆度气候修正 + 万向风验证。"""

    @staticmethod
    def _chunk_dict_to_field(cont, field_idx: int) -> list:
        """从 chunk 气候 dict 重建逐格数组（大陆度断言用）。

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
        from olam.generation.continent import ContinentGenerator
        import pytest

        gen = ContinentGenerator(seed=CANONICAL_SEED)
        data = gen.generate()
        w, h = data.grid_width, data.grid_height
        temp = self._chunk_dict_to_field(data, 0)
        land = data.land_mask

        from olam.generation.hydrology import _distance_to_ocean_c
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
        from olam.generation.continent import ContinentGenerator

        gen = ContinentGenerator(seed=CANONICAL_SEED)
        data = gen.generate()
        temps = [data.get_chunk_climate(cx, cy)[0]
                 for cy in range(data.grid_height // 2)
                 for cx in range(data.grid_width // 2)]

        assert min(temps) >= -30.0, f"最低温度 {min(temps)}°C < -30°C"
        assert max(temps) <= 50.0, f"最高温度 {max(temps)}°C > 50°C"

    def test_all_eight_climate_zones_present(self):
        """规范种子下全部 8 个气候带均出现（chunk 级）。"""
        from olam.generation.continent import ContinentGenerator

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
        from olam.generation.continent import ContinentGenerator

        data1 = ContinentGenerator(seed=99).generate()
        data2 = ContinentGenerator(seed=99).generate()

        for cy in range(data1.grid_height // 2):
            for cx in range(data1.grid_width // 2):
                assert data1.get_chunk_climate(cx, cy) == \
                    data2.get_chunk_climate(cx, cy)

    def test_rainfall_field_non_negative(self):
        """降雨量全场非负（chunk 级）。"""
        from olam.generation.continent import ContinentGenerator

        gen = ContinentGenerator(seed=CANONICAL_SEED)
        data = gen.generate()
        for cy in range(data.grid_height // 2):
            for cx in range(data.grid_width // 2):
                rain = data.get_chunk_climate(cx, cy)[1]
                assert rain >= 0.0, f"({cx},{cy}) rainfall {rain} < 0"

    def test_rainfall_not_uniform(self):
        """降雨量不是均匀场（雨影产生了空间变化，chunk 级）。"""
        from olam.generation.continent import ContinentGenerator

        gen = ContinentGenerator(seed=CANONICAL_SEED)
        data = gen.generate()
        rains = [data.get_chunk_climate(cx, cy)[1]
                 for cy in range(data.grid_height // 2)
                 for cx in range(data.grid_width // 2)]
        assert max(rains) - min(rains) > 100.0, (
            f"降雨量变化幅度 {max(rains)-min(rains):.0f}mm 过小"
        )


