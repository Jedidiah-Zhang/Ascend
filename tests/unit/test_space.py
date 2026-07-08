"""世界生成模块单元测试。

Coverage 目标:
    space/terrain.py   — 100%  ✅
    space/tile_grid.py — 100%  ✅
    space/tile_gen.py  — 100%  ✅
    space/chunk.py     — 100%  (markers/generate_tiles)
    space/climate.py   — 100%  (物理推导)
    space/biome.py     — 100%  (群系分配 + 模板)
    space/generator.py — 100%  (WorldGenerator)
    space/noise.py     — 100%  (PerlinNoise)
    space/render.py    — 忽略 (终端调试工具, .coveragerc omit)
"""

import pytest
from ascend.space import (
    PerlinNoise,
    ClimateZone,
    ClimateTemplate,
    SeasonalityMode,
    WeatherParams,
    classify,
    annual_baseline,
    get_climate_template,
    BiomeType,
    BiomeTemplate,
    TerrainBias,
    biome_membership,
    biome_from_attrs,
    get_template,
    ChunkData,
    TILE_MAP_SIZE,
    TerrainType,
    TerrainProps,
    get_terrain_props,
    is_passable,
    is_buildable,
    movement_cost,
    fertility,
    TileGrid,
)


# ══════════════════════════════════════════════════════════
# PerlinNoise
# ══════════════════════════════════════════════════════════

class TestPerlinNoise:
    """PerlinNoise 噪声生成器测试。"""

    def test_determinism(self):
        """相同种子 + 相同坐标 → 相同噪声值。"""
        n1 = PerlinNoise(seed=42)
        n2 = PerlinNoise(seed=42)
        for x, y in [(0.0, 0.0), (1.5, 3.2), (-10.0, 20.0)]:
            assert n1.sample(x, y) == pytest.approx(n2.sample(x, y))

    def test_different_seeds(self):
        """不同种子产生不同噪声值。"""
        n1 = PerlinNoise(seed=1)
        n2 = PerlinNoise(seed=2)
        # 使用非整数坐标避免落在网格点上（网格点噪声值为 0）
        values1 = [n1.sample(i * 0.7, i * 1.3) for i in range(10)]
        values2 = [n2.sample(i * 0.7, i * 1.3) for i in range(10)]
        assert values1 != values2

    def test_range(self):
        """噪声值在合理范围内。"""
        n = PerlinNoise(seed=0)
        for i in range(100):
            v = n.sample(i * 0.73 + 0.5, i * 1.17 + 0.3)
            assert -1.5 <= v <= 1.5

    def test_octave_range(self):
        """八度叠加后仍在归一化范围。"""
        n = PerlinNoise(seed=0)
        for i in range(100):
            x = i * 0.73 + 0.1
            y = i * 1.17 + 0.2
            v = n.octave(x, y, octaves=4)
            assert -1.2 <= v <= 1.2, f"value={v} at ({x}, {y})"

    def test_smoothness(self):
        """相邻采样点之间变化不应过于剧烈。"""
        n = PerlinNoise(seed=42)
        v0 = n.sample(10.0, 10.0)
        v1 = n.sample(10.01, 10.0)
        assert abs(v1 - v0) < 0.1

    def test_thread_safety(self):
        """多线程各自创建 PerlinNoise 实例，无共享状态冲突。"""
        import threading
        results = []

        def sample_noise(seed):
            n = PerlinNoise(seed=seed)
            for i in range(50):
                n.sample(i * 0.1, i * 0.2)

        threads = [threading.Thread(target=sample_noise, args=(i,)) for i in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()


# ══════════════════════════════════════════════════════════
# Climate
# ══════════════════════════════════════════════════════════

class TestClimateZone:
    """8 档气候分类测试。"""

    def test_int_enum(self):
        """ClimateZone 是 IntEnum，值 0-7。"""
        assert ClimateZone.EQUATORIAL_RAINFOREST == 0
        assert ClimateZone.ALPINE == 7
        assert int(ClimateZone.TEMPERATE_FOREST) == 4

    def test_label(self):
        """每个气候档位有中文标签。"""
        assert ClimateZone.EQUATORIAL_RAINFOREST.label == "热带雨林"
        assert ClimateZone.POLAR_TUNDRA.label == "极地苔原"
        assert ClimateZone.ALPINE.label == "高山"

    def test_all_zones_have_templates(self):
        """8 档气候均有 ClimateTemplate。"""
        for cz in ClimateZone:
            tmpl = get_climate_template(cz)
            assert isinstance(tmpl, ClimateTemplate)
            assert tmpl.climate == cz
            assert tmpl.display_color.startswith("#")

    def test_template_has_seasonality(self):
        """模板携带季节性模式字段（供 WeatherEngine 选择湿度曲线形状）。"""
        tmpl = get_climate_template(ClimateZone.TEMPERATE_FOREST)
        assert tmpl.seasonality == SeasonalityMode.FOUR_SEASON
        tmpl2 = get_climate_template(ClimateZone.TROPICAL_SAVANNA)
        assert tmpl2.seasonality == SeasonalityMode.MONSOON

    @pytest.mark.parametrize("temp, rain, alt, expected", [
        # 高山（海拔优先，覆盖纬度气候）
        (25.0, 2000.0, 2500.0, ClimateZone.ALPINE),
        (-5.0, 100.0, 2000.0, ClimateZone.ALPINE),
        # 极地（温度 < -5 优先于干旱）
        (-10.0, 2000.0, 0.0, ClimateZone.POLAR_TUNDRA),
        (-8.0, 100.0, 0.0, ClimateZone.POLAR_TUNDRA),
        # 沙漠（降雨 < 200，温度 >= -5）
        (25.0, 100.0, 0.0, ClimateZone.DESERT),
        (15.0, 150.0, 0.0, ClimateZone.DESERT),
        # 草原（200<=R<600，温度 > 5）
        (25.0, 400.0, 0.0, ClimateZone.STEPPE),
        (10.0, 500.0, 0.0, ClimateZone.STEPPE),
        # 热带雨林（T>=20, R>=1500）
        (28.0, 2000.0, 0.0, ClimateZone.EQUATORIAL_RAINFOREST),
        (22.0, 1500.0, 0.0, ClimateZone.EQUATORIAL_RAINFOREST),
        # 热带草原（T>=20, 600<=R<1500）
        (28.0, 800.0, 0.0, ClimateZone.TROPICAL_SAVANNA),
        (22.0, 1200.0, 0.0, ClimateZone.TROPICAL_SAVANNA),
        # 温带森林（5<=T<20, R>=600）
        (15.0, 800.0, 0.0, ClimateZone.TEMPERATE_FOREST),
        (8.0, 1000.0, 0.0, ClimateZone.TEMPERATE_FOREST),
        # 亚寒带针叶林（-5<=T<5, R>=400）
        (-2.0, 600.0, 0.0, ClimateZone.SUBARCTIC_TAIGA),
        (0.0, 500.0, 0.0, ClimateZone.SUBARCTIC_TAIGA),
        # 极地苔原（-5<=T<5, R<400 — 冷干合并）
        (-2.0, 200.0, 0.0, ClimateZone.POLAR_TUNDRA),
        (4.0, 300.0, 0.0, ClimateZone.POLAR_TUNDRA),
    ])
    def test_classify(self, temp, rain, alt, expected):
        """classify 8 档判定表驱动。"""
        assert classify(temp, rain, alt) == expected

    def test_alpine_overrides_tropical(self):
        """高山海拔覆盖热带纬度气候。"""
        assert classify(30.0, 3000.0, 2500.0) == ClimateZone.ALPINE

    def test_polar_overrides_desert(self):
        """极地严寒优先于沙漠干旱判定。"""
        assert classify(-10.0, 50.0, 0.0) == ClimateZone.POLAR_TUNDRA

    def test_sea_level_temperature_range(self):
        """纬度噪声映射到合理海平面温度范围。"""
        from ascend.space.climate import sea_level_temperature
        assert sea_level_temperature(-1.0) < 0.0
        assert sea_level_temperature(1.0) > 30.0
        assert sea_level_temperature(0.0) == pytest.approx(15.0)

    def test_lapse_rate(self):
        """气温直减率：升高 1000m 应降 9.0°C（游戏性放大值）。"""
        from ascend.space.climate import apply_lapse_rate, LAPSE_RATE
        assert LAPSE_RATE == 9.0
        t0 = apply_lapse_rate(20.0, 0.0)
        t1 = apply_lapse_rate(20.0, 1000.0)
        assert t0 - t1 == pytest.approx(9.0)

    def test_high_altitude_alpine(self):
        """高海拔 → 即使赤道纬度也判高山而非热带。"""
        from ascend.space.climate import apply_lapse_rate
        t = apply_lapse_rate(35.0, 4000.0)
        assert t < 10.0
        assert classify(t, 2000.0, 4000.0) == ClimateZone.ALPINE


class TestWeatherParams:
    """气象参数测试。"""

    def test_dataclass(self):
        """创建 WeatherParams。"""
        w = WeatherParams(
            temperature=15.0, rainfall=800.0, sunshine=10.0,
            altitude=200.0, humidity=60.0, wind_speed=5.0,
        )
        assert w.temperature == 15.0
        assert w.humidity == 60.0

    def test_annual_baseline_new_api(self):
        """新 API：海拔+海平面温度+降雨+气候 → 完整参数。"""
        w = annual_baseline(
            altitude=500.0,
            sea_level_temp=20.0,
            rainfall=1000.0,
            climate=ClimateZone.TEMPERATE_FOREST,
            sunshine_noise=0.0,
            humidity_noise=0.0,
            wind_noise=0.0,
        )
        # 500m * 9.0/1000 = 4.5°C 下降
        assert w.temperature == pytest.approx(15.5)
        assert w.rainfall == 1000.0
        assert w.altitude == 500.0
        assert 45.0 <= w.humidity <= 80.0

    def test_annual_baseline_uses_template_ranges(self):
        """annual_baseline 使用 ClimateTemplate 区间。"""
        w = annual_baseline(
            altitude=0.0,
            sea_level_temp=28.0,
            rainfall=2000.0,
            climate=ClimateZone.EQUATORIAL_RAINFOREST,
            sunshine_noise=1.0,
            humidity_noise=1.0,
            wind_noise=1.0,
        )
        tmpl = get_climate_template(ClimateZone.EQUATORIAL_RAINFOREST)
        assert w.sunshine == pytest.approx(tmpl.sunshine_range[1])
        assert w.humidity == pytest.approx(tmpl.humidity_range[1])
        assert w.wind_speed == pytest.approx(tmpl.wind_speed_range[1])


# ══════════════════════════════════════════════════════════
# Biome
# ══════════════════════════════════════════════════════════

class TestBiome:
    """群系系统测试 — 16 陆地群系（8 档 × 2 子型）+ 3 海洋群系。"""

    def test_biome_type_labels(self):
        """群系类型有中文标签。"""
        assert BiomeType.TEMPERATE_DECIDUOUS_FOREST.label == "温带落叶林"
        assert BiomeType.TROPICAL_RAINFOREST.label == "热带雨林"
        assert BiomeType.SANDY_DESERT.label == "沙质沙漠"
        assert BiomeType.BOREAL_FOREST.label == "北方针叶林"
        assert BiomeType.TUNDRA.label == "苔原"
        assert BiomeType.ALPINE_MEADOW.label == "高山草甸"

    def test_land_biome_count(self):
        """16 陆地群系 + 3 海洋群系。"""
        land = [b for b in BiomeType if not b.is_ocean]
        ocean = [b for b in BiomeType if b.is_ocean]
        assert len(land) == 16
        assert len(ocean) == 3

    def test_land_biome_from_attrs(self):
        """biome_from_attrs 从连续属性映射陆地群系（含档内细分）。

        用静态默认值域（subdiv_ranges=None），中点 = (value_min+value_max)/2。
        测试值选在子型中心附近，确保明确归属。
        """
        # 热带雨林档 [1500,2200] 中点1850: R=1700→季雨林, R=2100→雨林
        assert biome_from_attrs(28.0, 1700.0, 100.0, 29.0) == BiomeType.TROPICAL_MONSOON_FOREST
        assert biome_from_attrs(28.0, 2100.0, 100.0, 29.0) == BiomeType.TROPICAL_RAINFOREST
        # 热带草原档 [600,1500] 中点1050: R=800→草原, R=1300→疏林
        assert biome_from_attrs(28.0, 800.0, 100.0, 29.0) == BiomeType.TROPICAL_SAVANNA
        assert biome_from_attrs(28.0, 1300.0, 100.0, 29.0) == BiomeType.TROPICAL_WOODLAND
        # 沙漠档 [-1,1] 中点0: moisture=-0.5→沙质, moisture=0.5→砾石
        assert biome_from_attrs(25.0, 100.0, 100.0, 26.0, moisture_noise=-0.5) == BiomeType.SANDY_DESERT
        assert biome_from_attrs(25.0, 100.0, 100.0, 26.0, moisture_noise=0.5) == BiomeType.ROCKY_DESERT
        # 草原档 [200,600] 中点400: R=300→矮草, R=500→高草
        assert biome_from_attrs(15.0, 300.0, 100.0, 16.0) == BiomeType.SHORT_GRASS_STEPPE
        assert biome_from_attrs(15.0, 500.0, 100.0, 16.0) == BiomeType.TALL_GRASS_STEPPE
        # 温带森林档 [5,20] 中点12.5: T=8→混交, T=16→落叶
        assert biome_from_attrs(8.0, 800.0, 100.0, 9.0) == BiomeType.TEMPERATE_MIXED_FOREST
        assert biome_from_attrs(16.0, 800.0, 100.0, 17.0) == BiomeType.TEMPERATE_DECIDUOUS_FOREST
        # 亚寒带针叶林档 [0,800] 中点400: alt=100→湿地, alt=700→密林
        assert biome_from_attrs(-2.0, 600.0, 100.0, 1.0) == BiomeType.BOREAL_WETLAND
        assert biome_from_attrs(-2.0, 600.0, 700.0, 1.0) == BiomeType.BOREAL_FOREST
        # 极地苔原档 [-14,0] 中点-7: T=-12→荒原, T=-6→苔原（需 T<-5 才入此档）
        assert biome_from_attrs(-12.0, 500.0, 100.0, -11.0) == BiomeType.POLAR_BARREN
        assert biome_from_attrs(-6.0, 500.0, 100.0, -5.0) == BiomeType.TUNDRA
        # 高山档 [2000,2600] 中点2300: alt=2100→草甸, alt=2500→裸岩
        assert biome_from_attrs(10.0, 800.0, 2100.0, 32.5) == BiomeType.ALPINE_MEADOW
        assert biome_from_attrs(10.0, 800.0, 2500.0, 32.5) == BiomeType.ALPINE_BARREN

    def test_biome_membership_smooth(self):
        """biome_membership 在档内边界处平滑混合。"""
        # 热带雨林档静态默认 [1500,2200], 中点 1850 → 两子型各 0.5
        m = biome_membership(28.0, 1850.0, 100.0, 29.0)
        assert len(m) == 2
        weights = [w for _, w in m]
        assert sum(weights) == pytest.approx(1.0)
        assert all(abs(w - 0.5) < 0.01 for w in weights)

        # 单一子型（远离边界，权重 <0.001 被过滤）
        m2 = biome_membership(28.0, 1550.0, 100.0, 29.0)
        assert len(m2) == 1
        assert m2[0][1] == pytest.approx(1.0)

    def test_biome_membership_ocean(self):
        """海洋返回单一群系隶属度 1.0。"""
        m = biome_membership(28.0, 2000.0, -100.0, 28.0)
        assert len(m) == 1
        assert m[0][0] == BiomeType.WARM_OCEAN
        assert m[0][1] == 1.0

    def test_all_land_biomes_have_templates(self):
        """16 种陆地群系均有模板。"""
        land_biomes = [b for b in BiomeType if not b.is_ocean]
        assert len(land_biomes) == 16
        for bt in land_biomes:
            t = get_template(bt)
            assert t.biome_type == bt
            assert len(t.creature_weights) > 0
            assert len(t.resource_weights) > 0
            assert t.terrain_bias is not None

    def test_all_biomes_have_terrain_bias(self):
        """每个群系模板都有 TerrainBias。"""
        from ascend.space import TerrainBias
        for bt in BiomeType:
            t = get_template(bt)
            assert isinstance(t.terrain_bias, TerrainBias)

    def test_template_tree_density_ordering(self):
        """雨林树密度 > 温带 > 草原 > 沙漠。"""
        rainforest = get_template(BiomeType.TROPICAL_RAINFOREST)
        temperate = get_template(BiomeType.TEMPERATE_DECIDUOUS_FOREST)
        steppe = get_template(BiomeType.TALL_GRASS_STEPPE)
        desert = get_template(BiomeType.SANDY_DESERT)
        assert rainforest.tree_density > temperate.tree_density
        assert temperate.tree_density > steppe.tree_density
        assert steppe.tree_density > desert.tree_density

    def test_template_has_creatures(self):
        """模板包含生物权重。"""
        t = get_template(BiomeType.TEMPERATE_DECIDUOUS_FOREST)
        assert "deer" in t.creature_weights
        assert "wolf" in t.creature_weights

    def test_template_has_resources(self):
        """模板包含资源权重。"""
        t = get_template(BiomeType.SANDY_DESERT)
        assert "exposed_mineral" in t.resource_weights
        assert "sand" in t.resource_weights

    def test_ocean_biomes(self):
        """海拔 <0 判定为海洋，温度决定暖/温/冷。"""
        assert biome_from_attrs(28.0, 2000.0, -100.0, 28.0) == BiomeType.WARM_OCEAN
        assert biome_from_attrs(15.0, 800.0, -50.0, 15.0) == BiomeType.TEMPERATE_OCEAN
        assert biome_from_attrs(2.0, 500.0, -200.0, 2.0) == BiomeType.COLD_OCEAN

    def test_ocean_vs_land_boundary(self):
        """海拔准确 0m 时判定为陆地而非海洋。"""
        assert biome_from_attrs(15.0, 800.0, 0.0, 16.0) == BiomeType.TEMPERATE_DECIDUOUS_FOREST
        assert biome_from_attrs(15.0, 800.0, -1.0, 16.0) == BiomeType.TEMPERATE_OCEAN

    def test_biome_is_ocean(self):
        """is_ocean 属性正确区分海陆。"""
        assert BiomeType.WARM_OCEAN.is_ocean is True
        assert BiomeType.COLD_OCEAN.is_ocean is True
        assert BiomeType.TEMPERATE_DECIDUOUS_FOREST.is_ocean is False
        assert BiomeType.SANDY_DESERT.is_ocean is False

    def test_ocean_templates_registered(self):
        """三种海洋群系均有模板。"""
        for bt in [BiomeType.WARM_OCEAN, BiomeType.TEMPERATE_OCEAN, BiomeType.COLD_OCEAN]:
            t = get_template(bt)
            assert t.water_ratio == 1.0
            assert t.tree_density == 0.0
            assert len(t.creature_weights) > 0
            assert len(t.resource_weights) > 0


# ══════════════════════════════════════════════════════════
# ChunkData
# ══════════════════════════════════════════════════════════

class TestChunkData:
    """分块数据结构测试。"""

    def test_creation(self):
        """创建 ChunkData。"""
        c = ChunkData(
            cx=0, cy=0,
            biome=BiomeType.TEMPERATE_DECIDUOUS_FOREST,
            climate_zone=ClimateZone.TEMPERATE_FOREST,
            annual_baseline=WeatherParams(15, 800, 10, 200, 60, 5),
        )
        assert c.cx == 0
        assert c.cy == 0
        assert c.chunk_key == (0, 0)
        assert c.biome == BiomeType.TEMPERATE_DECIDUOUS_FOREST
        assert c.tile_grid is None
        assert c.has_tiles is False

    def test_tiles_generation(self):
        """写入详细 tile 数据（TileGrid）。"""
        c = ChunkData(
            cx=1, cy=2,
            biome=BiomeType.SANDY_DESERT,
            climate_zone=ClimateZone.DESERT,
            annual_baseline=WeatherParams(25, 100, 12, 500, 25, 8),
        )
        grid = TileGrid()
        c.generate_tiles(grid)
        assert c.has_tiles is True
        assert c.tile_grid.size == TILE_MAP_SIZE

    def test_tiles_overwrite(self):
        """重复调用 generate_tiles 覆盖旧数据。"""
        c = ChunkData(
            cx=0, cy=0,
            biome=BiomeType.TEMPERATE_DECIDUOUS_FOREST,
            climate_zone=ClimateZone.TEMPERATE_FOREST,
            annual_baseline=WeatherParams(15, 800, 10, 200, 60, 5),
        )
        grid1 = TileGrid()
        grid1.set(50, 50, TerrainType.SAND)
        c.generate_tiles(grid1)
        assert c.tile_grid.get(50, 50) == TerrainType.SAND

        grid2 = TileGrid()
        c.generate_tiles(grid2)
        # 新 grid 覆盖后，旧数据不存在
        assert c.tile_grid is grid2
        assert c.tile_grid.get(50, 50) == TerrainType.GRASSLAND

    def test_unload_tiles(self):
        """卸载 tile 释放内存。"""
        c = ChunkData(
            cx=0, cy=0,
            biome=BiomeType.TEMPERATE_DECIDUOUS_FOREST,
            climate_zone=ClimateZone.TEMPERATE_FOREST,
            annual_baseline=WeatherParams(15, 800, 10, 200, 60, 5),
        )
        grid = TileGrid()
        c.generate_tiles(grid)
        assert c.has_tiles
        c.unload_tiles()
        assert not c.has_tiles
        assert c.tile_grid is None

    def test_markers(self):
        """标记的添加和移除。"""
        c = ChunkData(
            cx=3, cy=4,
            biome=BiomeType.SANDY_DESERT,
            climate_zone=ClimateZone.DESERT,
            annual_baseline=WeatherParams(25, 100, 12, 500, 25, 8),
        )
        c.add_marker("settlement", "一个聚落")
        c.add_marker("ruin", "古代遗址")
        assert "settlement" in c.markers
        assert c.markers["ruin"] == "古代遗址"

        c.remove_marker("ruin")
        assert "ruin" not in c.markers
        assert "settlement" in c.markers

    def test_continuous_climate_attrs(self):
        """ChunkData 携带连续气候属性字段。"""
        c = ChunkData(
            cx=0, cy=0,
            biome=BiomeType.TEMPERATE_DECIDUOUS_FOREST,
            climate_zone=ClimateZone.TEMPERATE_FOREST,
            annual_baseline=WeatherParams(15, 800, 10, 200, 60, 5),
            mean_temp=15.0,
            annual_rainfall=800.0,
            sea_level_temp=16.75,
            altitude=200.0,
        )
        assert c.mean_temp == 15.0
        assert c.annual_rainfall == 800.0
        assert c.sea_level_temp == 16.75
        assert c.altitude == 200.0


# ══════════════════════════════════════════════════════════
# WorldGenerator — 待 Voronoi 构造模块实现后恢复测试
# ══════════════════════════════════════════════════════════


# ══════════════════════════════════════════════════════════
# TerrainType
# ══════════════════════════════════════════════════════════

class TestTerrainType:
    """地形类型和属性查询测试。"""

    def test_all_types_have_props(self):
        """每个 TerrainType 都有对应的 TerrainProps。"""
        for t in TerrainType:
            props = get_terrain_props(t)
            assert isinstance(props, TerrainProps)
            assert isinstance(props.label, str)
            assert len(props.label) > 0

    def test_passable(self):
        """可行走性查询正确。"""
        assert is_passable(TerrainType.GRASSLAND) is True
        assert is_passable(TerrainType.MOUNTAIN_PEAK) is False
        assert is_passable(TerrainType.DEEP_WATER) is False
        assert is_passable(TerrainType.SHALLOW_WATER) is True

    def test_buildable(self):
        """可建造性查询正确。"""
        assert is_buildable(TerrainType.GRASSLAND) is True
        assert is_buildable(TerrainType.FERTILE_SOIL) is True
        assert is_buildable(TerrainType.MOUNTAIN_PEAK) is False
        assert is_buildable(TerrainType.ROCK) is False
        assert is_buildable(TerrainType.MARSH) is False

    def test_movement_cost(self):
        """移动消耗查询正确。"""
        assert movement_cost(TerrainType.GRASSLAND) == 1.0
        assert movement_cost(TerrainType.STEEP_SLOPE) == 2.0
        assert movement_cost(TerrainType.DEEP_WATER) == float("inf")

    def test_fertility(self):
        """肥力查询正确。"""
        assert fertility(TerrainType.FERTILE_SOIL) == 1.0
        assert fertility(TerrainType.GRASSLAND) == 0.5
        assert fertility(TerrainType.ROCK) == 0.0
        assert fertility(TerrainType.SAND) == 0.2

    def test_int_enum(self):
        """TerrainType 是 IntEnum，可直接当 int 使用。"""
        assert TerrainType.GRASSLAND == 0
        assert int(TerrainType.DEEP_WATER) == 7
        # 可存入 array
        from array import array
        a = array('H', [int(TerrainType.SAND)])
        assert a[0] == int(TerrainType.SAND)


# ══════════════════════════════════════════════════════════
# TileGrid
# ══════════════════════════════════════════════════════════

class TestTileGrid:
    """TileGrid 紧凑存储测试。

    Coverage: 正常路径 ✓  边界 ✓  错误路径 ✓  序列化 ✓
    """

    def test_create_default(self):
        """默认创建，全为 GRASSLAND。"""
        g = TileGrid()
        assert g.size == TILE_MAP_SIZE
        assert g.get(0, 0) == TerrainType.GRASSLAND
        assert g.get(199, 199) == TerrainType.GRASSLAND

    def test_set_and_get(self):
        """单点读写正确。"""
        g = TileGrid()
        g.set(10, 20, TerrainType.SAND)
        assert g.get(10, 20) == TerrainType.SAND
        # 相邻格不受影响
        assert g.get(10, 21) == TerrainType.GRASSLAND
        assert g.get(11, 20) == TerrainType.GRASSLAND

    def test_all_terrain_types(self):
        """每种地形类型都能正确存取。"""
        g = TileGrid()
        for t in TerrainType:
            g.set(t.value, t.value, t)
            assert g.get(t.value, t.value) == t

    def test_to_list(self):
        """导出为 int 列表。"""
        g = TileGrid()
        g.set(0, 0, TerrainType.DEEP_WATER)
        lst = g.to_list()
        assert len(lst) == TILE_MAP_SIZE * TILE_MAP_SIZE
        assert lst[0] == int(TerrainType.DEEP_WATER)
        assert lst[1] == int(TerrainType.GRASSLAND)
        assert all(isinstance(v, int) for v in lst)

    def test_from_list(self):
        """从 int 列表还原。"""
        g1 = TileGrid()
        g1.set(5, 5, TerrainType.ROCK)
        g1.set(100, 100, TerrainType.MARSH)

        lst = g1.to_list()
        g2 = TileGrid.from_list(lst)
        assert g2 == g1
        assert g2.get(5, 5) == TerrainType.ROCK
        assert g2.get(100, 100) == TerrainType.MARSH

    def test_from_list_wrong_size(self):
        """长度错误的列表应抛出 ValueError。"""
        with pytest.raises(ValueError):
            TileGrid.from_list([0] * 100)

    def test_create_from_list(self):
        """从正确长度的列表构造（非 array 分支）。"""
        data = [int(TerrainType.SAND)] * (TILE_MAP_SIZE * TILE_MAP_SIZE)
        g = TileGrid(data=data)
        assert g.get(0, 0) == TerrainType.SAND
        assert g.get(50, 50) == TerrainType.SAND

    def test_create_from_array(self):
        """从 array('H') 直接构造（零拷贝分支）。"""
        from array import array
        data = array('H', [int(TerrainType.ROCK)]) * (TILE_MAP_SIZE * TILE_MAP_SIZE)
        g = TileGrid(data=data)
        assert g.get(0, 0) == TerrainType.ROCK
        assert g.get(199, 199) == TerrainType.ROCK

    def test_create_from_array_wrong_size(self):
        """长度错误的 array 应抛出 ValueError。"""
        from array import array
        with pytest.raises(ValueError):
            TileGrid(data=array('H', [0] * 100))

    def test_create_from_list_wrong_size(self):
        """长度错误的列表应抛出 ValueError（__init__ 路径）。"""
        with pytest.raises(ValueError):
            TileGrid(data=[0] * 100)

    def test_get_region(self):
        """区域查询返回正确形状和值。"""
        g = TileGrid()
        g.set(5, 5, TerrainType.ROCK)
        g.set(6, 5, TerrainType.SAND)
        g.set(5, 6, TerrainType.MARSH)

        region = g.get_region(5, 5, 2, 2)
        assert len(region) == 2  # 2 行
        assert len(region[0]) == 2  # 2 列
        assert region[0][0] == TerrainType.ROCK
        assert region[0][1] == TerrainType.SAND
        assert region[1][0] == TerrainType.MARSH

    def test_get_region_edge(self):
        """区域查询在 chunk 边界处。"""
        g = TileGrid()
        g.set(0, 0, TerrainType.SAND)
        g.set(199, 199, TerrainType.ROCK)
        # 左上角
        r1 = g.get_region(0, 0, 1, 1)
        assert r1[0][0] == TerrainType.SAND
        # 右下角
        r2 = g.get_region(199, 199, 1, 1)
        assert r2[0][0] == TerrainType.ROCK

    def test_equality(self):
        """相同数据的 TileGrid 相等。"""
        g1 = TileGrid()
        g2 = TileGrid()
        assert g1 == g2

        g1.set(0, 0, TerrainType.SAND)
        assert g1 != g2

        g2.set(0, 0, TerrainType.SAND)
        assert g1 == g2

    def test_equality_different_type(self):
        """与非 TileGrid 比较返回 NotImplemented（不抛异常）。"""
        g = TileGrid()
        # 与不同类型对象比较不应崩溃
        assert g != "not a grid"
        assert g != 42
        assert g != None

    def test_repr_default(self):
        """repr 全草地网格。"""
        g = TileGrid()
        r = repr(g)
        assert "TileGrid" in r
        assert "non_grassland=0.0%" in r

    def test_repr_mixed(self):
        """repr 含非草地 tile。"""
        g = TileGrid()
        for i in range(100):
            g.set(i, 0, TerrainType.SAND)
        r = repr(g)
        assert "non_grassland=0.0%" not in r  # 不再是 0%

    def test_raw_data(self):
        """raw_data 返回底层数组引用。"""
        g = TileGrid()
        raw = g.raw_data()
        from array import array
        assert isinstance(raw, array)
        assert len(raw) == TILE_MAP_SIZE * TILE_MAP_SIZE
        # 修改底层数组会影响 TileGrid
        raw[0] = int(TerrainType.DEEP_WATER)
        assert g.get(0, 0) == TerrainType.DEEP_WATER

    def test_get_raw(self):
        """get_raw 按索引读取。"""
        g = TileGrid()
        g.set(3, 5, TerrainType.MOUNTAIN_PEAK)
        idx = 5 * TILE_MAP_SIZE + 3
        assert g.get_raw(idx) == int(TerrainType.MOUNTAIN_PEAK)


# ══════════════════════════════════════════════════════════
# TileGenerator — 层2 chunk tile 生成
# ══════════════════════════════════════════════════════════


class TestTileGenerator:
    """TileGenerator 单元测试 — 200×200 chunk 地形生成。"""

    def test_tile_gen_exists(self):
        """TileGenerator 可实例化。"""
        from ascend.space.continent import ContinentGenerator
        from ascend.space.tile_gen import TileGenerator

        cont = ContinentGenerator(seed=42).generate()
        gen = TileGenerator(seed=42, continent=cont)
        assert gen is not None
        assert repr(gen) != ""

    def test_generate_chunk_returns_tilegrid(self):
        """generate_chunk 返回 200×200 TileGrid。"""
        from ascend.space.continent import ContinentGenerator
        from ascend.space.tile_gen import TileGenerator

        cont = ContinentGenerator(seed=42).generate()
        gen = TileGenerator(seed=42, continent=cont)
        grid = gen.generate_chunk(0, 0)
        assert grid.size == 200
        assert grid.get(0, 0) is not None

    def test_ocean_chunk_all_water(self):
        """远离陆地的 chunk 全为水体。"""
        from ascend.space.continent import ContinentGenerator
        from ascend.space.tile_gen import TileGenerator
        from ascend.space.terrain import TerrainType

        cont = ContinentGenerator(seed=42).generate()
        gen = TileGenerator(seed=42, continent=cont)
        # 在海洋深处找一个 chunk（远离大陆中心）
        grid = gen.generate_chunk(300, 300)  # 深海区
        water_count = sum(
            1 for y in range(200) for x in range(200)
            if grid.get(x, y) in (TerrainType.DEEP_WATER, TerrainType.SHALLOW_WATER)
        )
        assert water_count > 39000, f"深海 chunk 水体应 >97%，实际 {water_count/400}%"

    def test_land_chunk_has_variety(self):
        """有流线河流穿过的 chunk 包含多种地形类型。"""
        from ascend.space.continent import ContinentGenerator
        from ascend.space.tile_gen import TileGenerator
        from ascend.space.terrain import TerrainType

        cont = ContinentGenerator(seed=42).generate()
        gen = TileGenerator(seed=42, continent=cont)
        # 找一个有流线河流穿过的 chunk — 必然有水体+陆地多种地形
        net = cont.hydrology.river_network
        from collections import Counter
        chunk_counts = Counter()
        for river in net.rivers:
            for p in river.points:
                cx = int(p.x * cont.cell_size / 200)
                cy = int(p.y * cont.cell_size / 200)
                chunk_counts[(cx, cy)] += 1
        best_chunk = chunk_counts.most_common(1)[0][0]

        grid = gen.generate_chunk(best_chunk[0], best_chunk[1])
        types: set[int] = set()
        for y in range(200):
            for x in range(200):
                types.add(int(grid.get(x, y)))
        assert len(types) >= 2, f"有河流的 chunk 应有 >=2 种地形，实际 {len(types)}"

    def test_chunk_deterministic(self):
        """同参数 → 同结果。"""
        from ascend.space.continent import ContinentGenerator
        from ascend.space.tile_gen import TileGenerator

        cont1 = ContinentGenerator(seed=42).generate()
        cont2 = ContinentGenerator(seed=42).generate()
        g1 = TileGenerator(seed=42, continent=cont1).generate_chunk(10, 5)
        g2 = TileGenerator(seed=42, continent=cont2).generate_chunk(10, 5)
        assert g1 == g2

    def test_chunk_boundary_continuous(self):
        """相邻 chunk 边界连续。"""
        from ascend.space.continent import ContinentGenerator
        from ascend.space.tile_gen import TileGenerator

        cont = ContinentGenerator(seed=42).generate()
        gen = TileGenerator(seed=42, continent=cont)
        left = gen.generate_chunk(10, 5)
        right = gen.generate_chunk(11, 5)
        # 左 chunk 右边缘 vs 右 chunk 左边缘
        jumps = 0
        for y in range(200):
            t1 = int(left.get(199, y))
            t2 = int(right.get(0, y))
            if abs(t1 - t2) > 2:  # 允许小跳跃（水体→陆地边界）
                jumps += 1
        assert jumps <= 20, f"chunk 边界跳变 {jumps}/200 处"

    def test_slope_distribution_reasonable(self):
        """Tile 级坡度分布合理：中位数 <15°, P90 <30°, 极少 >45°。

        在多个代表性 chunk 中采样相邻 tile 之间的海拔差，
        验证地形坡度不会过于陡峭。
        """
        import math
        from ascend.space.continent import ContinentGenerator
        from ascend.space.tile_gen import TileGenerator
        from ascend.space.tile_grid import TILE_MAP_SIZE

        cont = ContinentGenerator(seed=42).generate()
        gen = TileGenerator(seed=42, continent=cont)

        # 与 generate_chunk 保持一致的参数
        detail_freq = 0.005
        detail_amp = 50.0  # 目标振幅：±50m（不再是 ±100m）

        # 多个代表性位置
        test_chunks = [
            (50, 30, "内陆山地"),
            (30, 20, "海岸过渡"),
            (22, 16, "大陆架"),
        ]

        all_slopes: list[float] = []
        for cx, cy, _name in test_chunks:
            world_x0 = cx * TILE_MAP_SIZE
            world_y0 = cy * TILE_MAP_SIZE

            noise_field = gen._detail_noise.octave_grid(
                world_x0 + 0.5, world_y0 + 0.5,
                TILE_MAP_SIZE, TILE_MAP_SIZE,
                frequency=detail_freq, octaves=4,
            )

            # 每 2m 采样一次水平相邻 tile 的坡度
            for ty in range(0, TILE_MAP_SIZE, 2):
                row_base = ty * TILE_MAP_SIZE
                wy = world_y0 + ty
                for tx in range(0, TILE_MAP_SIZE - 1, 2):
                    wx = world_x0 + tx

                    macro0 = cont.sample_altitude_bilinear(wx, wy)
                    detail0 = noise_field[row_base + tx] * detail_amp
                    e0 = macro0 + detail0

                    macro1 = cont.sample_altitude_bilinear(wx + 1, wy)
                    detail1 = noise_field[row_base + tx + 1] * detail_amp
                    e1 = macro1 + detail1

                    all_slopes.append(abs(e1 - e0))  # m/m（1m 间距）

        assert len(all_slopes) > 5000, "样本数不足"

        all_slopes.sort()
        n = len(all_slopes)

        # 目标 1：中位数坡度 < 15°（常规地形平缓）
        p50_deg = math.degrees(math.atan(all_slopes[n // 2]))
        assert p50_deg < 15.0, (
            f"中位数坡度 {p50_deg:.1f}° 应 < 15°"
        )

        # 目标 2：P90 < 30°（大多数地形可通行）
        p90_deg = math.degrees(math.atan(all_slopes[n * 9 // 10]))
        assert p90_deg < 30.0, (
            f"P90 坡度 {p90_deg:.1f}° 应 < 30°"
        )

        # 目标 3：极少悬崖（>45° 的 tile 边缘 < 1%）
        cliff_count = sum(
            1 for s in all_slopes if math.degrees(math.atan(s)) > 45
        )
        cliff_pct = cliff_count / n * 100
        assert cliff_pct < 1.0, (
            f">45° 坡度比例 {cliff_pct:.1f}% 应 < 1%"
        )

        # 目标 4：最大坡度 < 50°（无垂直绝壁）
        max_deg = math.degrees(math.atan(all_slopes[-1]))
        assert max_deg < 50.0, (
            f"最大坡度 {max_deg:.1f}° 应 < 50°"
        )
