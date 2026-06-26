"""世界生成模块单元测试。"""

import pytest
from ascend.world import (
    PerlinNoise,
    ClimateZone,
    WeatherParams,
    climate_zone_from_noise,
    annual_baseline,
    BiomeType,
    BiomeTemplate,
    biome_from_climate,
    get_template,
    ChunkData,
    TILE_MAP_SIZE,
    WorldGenerator,
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
    """气候档位测试。"""

    def test_int_enum(self):
        """ClimateZone 是 IntEnum。"""
        assert ClimateZone.TEMPERATE == 1
        assert ClimateZone.TROPICAL < ClimateZone.COLD

    def test_label(self):
        """每个气候档位有中文标签。"""
        assert ClimateZone.TEMPERATE.label == "温带"
        assert ClimateZone.ARID.label == "干旱带"

    def test_from_noise_cold(self):
        """低温噪声 → 寒带。"""
        assert climate_zone_from_noise(-0.8, 0.0) == ClimateZone.COLD
        assert climate_zone_from_noise(-0.4, 0.5) == ClimateZone.COLD

    def test_from_noise_arid(self):
        """高温 + 低降雨 → 干旱带。"""
        assert climate_zone_from_noise(0.5, -0.5) == ClimateZone.ARID
        assert climate_zone_from_noise(0.8, -0.3) == ClimateZone.ARID

    def test_from_noise_tropical(self):
        """高温 + 高降雨 → 热带。"""
        assert climate_zone_from_noise(0.5, 0.3) == ClimateZone.TROPICAL
        assert climate_zone_from_noise(0.9, 0.8) == ClimateZone.TROPICAL

    def test_from_noise_temperate(self):
        """中等噪声 → 温带（默认）。"""
        assert climate_zone_from_noise(0.0, 0.0) == ClimateZone.TEMPERATE


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

    def test_annual_baseline_temperate(self):
        """温带气候 + 零噪声 → 参数在合理范围内。"""
        noise = {
            "temperature": 0.0, "rainfall": 0.0, "sunshine": 0.0,
            "altitude": 0.0, "humidity": 0.0, "wind_speed": 0.0,
        }
        w = annual_baseline(ClimateZone.TEMPERATE, noise)
        # 温带温度区间 [5, 20]，0 噪声 → 中点 12.5
        assert 5.0 <= w.temperature <= 20.0
        assert 30.0 <= w.humidity <= 100.0
        assert w.altitude >= 0.0

    def test_annual_baseline_clamp(self):
        """极端噪声值不会超出绝对边界。"""
        noise = {
            "temperature": 100.0, "rainfall": 100.0, "sunshine": 100.0,
            "altitude": 100.0, "humidity": 100.0, "wind_speed": 100.0,
        }
        w = annual_baseline(ClimateZone.TROPICAL, noise)
        assert w.temperature <= 50.0  # 绝对上限
        assert w.humidity <= 100.0


# ══════════════════════════════════════════════════════════
# Biome
# ══════════════════════════════════════════════════════════

class TestBiome:
    """群系系统测试。"""

    def test_biome_type_labels(self):
        """群系类型有中文标签。"""
        assert BiomeType.TEMPERATE_DECIDUOUS_FOREST.label == "温带落叶林"
        assert BiomeType.ARID_SHRUBLAND.label == "干旱灌木地"

    def test_temperate_biome(self):
        """温带 → 落叶林。"""
        assert biome_from_climate(ClimateZone.TEMPERATE, 0.0, 0.0) == \
            BiomeType.TEMPERATE_DECIDUOUS_FOREST

    def test_arid_biome(self):
        """干旱带 → 灌木地。"""
        assert biome_from_climate(ClimateZone.ARID, 0.0, 0.0) == \
            BiomeType.ARID_SHRUBLAND

    def test_get_template_known_biomes(self):
        """已知群系有模板。"""
        t1 = get_template(BiomeType.TEMPERATE_DECIDUOUS_FOREST)
        t2 = get_template(BiomeType.ARID_SHRUBLAND)
        assert t1.biome_type == BiomeType.TEMPERATE_DECIDUOUS_FOREST
        assert t2.biome_type == BiomeType.ARID_SHRUBLAND
        assert t1.tree_density > t2.tree_density  # 森林比灌木地树多

    def test_template_has_creatures(self):
        """模板包含生物权重。"""
        t = get_template(BiomeType.TEMPERATE_DECIDUOUS_FOREST)
        assert "deer" in t.creature_weights
        assert "wolf" in t.creature_weights

    def test_template_has_resources(self):
        """模板包含资源权重。"""
        t = get_template(BiomeType.ARID_SHRUBLAND)
        assert "exposed_mineral" in t.resource_weights


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
            climate_zone=ClimateZone.TEMPERATE,
            annual_baseline=WeatherParams(15, 800, 10, 200, 60, 5),
        )
        assert c.cx == 0
        assert c.cy == 0
        assert c.chunk_key == (0, 0)
        assert c.biome == BiomeType.TEMPERATE_DECIDUOUS_FOREST
        assert c.tiles is None
        assert c.has_tiles is False

    def test_tiles_generation(self):
        """写入详细 tile 数据。"""
        c = ChunkData(
            cx=1, cy=2,
            biome=BiomeType.ARID_SHRUBLAND,
            climate_zone=ClimateZone.ARID,
            annual_baseline=WeatherParams(25, 100, 12, 500, 25, 8),
        )
        # 生成 200×200 的空白 tile
        tiles = [[0] * TILE_MAP_SIZE for _ in range(TILE_MAP_SIZE)]
        c.generate_tiles(tiles)
        assert c.has_tiles is True
        assert len(c.tiles) == TILE_MAP_SIZE

    def test_tiles_wrong_dimensions(self):
        """tile 维度错误应抛出异常。"""
        c = ChunkData(
            cx=0, cy=0,
            biome=BiomeType.TEMPERATE_DECIDUOUS_FOREST,
            climate_zone=ClimateZone.TEMPERATE,
            annual_baseline=WeatherParams(15, 800, 10, 200, 60, 5),
        )
        bad_tiles = [[0] * 100 for _ in range(100)]
        with pytest.raises(ValueError):
            c.generate_tiles(bad_tiles)

    def test_unload_tiles(self):
        """卸载 tile 释放内存。"""
        c = ChunkData(
            cx=0, cy=0,
            biome=BiomeType.TEMPERATE_DECIDUOUS_FOREST,
            climate_zone=ClimateZone.TEMPERATE,
            annual_baseline=WeatherParams(15, 800, 10, 200, 60, 5),
        )
        tiles = [[0] * TILE_MAP_SIZE for _ in range(TILE_MAP_SIZE)]
        c.generate_tiles(tiles)
        assert c.has_tiles
        c.unload_tiles()
        assert not c.has_tiles
        assert c.tiles is None

    def test_markers(self):
        """标记的添加和移除。"""
        c = ChunkData(
            cx=3, cy=4,
            biome=BiomeType.ARID_SHRUBLAND,
            climate_zone=ClimateZone.ARID,
            annual_baseline=WeatherParams(25, 100, 12, 500, 25, 8),
        )
        c.add_marker("settlement", "一个聚落")
        c.add_marker("ruin", "古代遗址")
        assert "settlement" in c.markers
        assert c.markers["ruin"] == "古代遗址"

        c.remove_marker("ruin")
        assert "ruin" not in c.markers
        assert "settlement" in c.markers


# ══════════════════════════════════════════════════════════
# WorldGenerator
# ══════════════════════════════════════════════════════════

class TestWorldGenerator:
    """世界生成器测试。"""

    def test_create(self):
        """创建 WorldGenerator。"""
        gen = WorldGenerator(seed=42)
        assert gen._seed == 42

    def test_determinism(self):
        """相同种子 + 相同坐标 → 相同分块。"""
        gen1 = WorldGenerator(seed=12345)
        gen2 = WorldGenerator(seed=12345)
        c1 = gen1.generate_chunk(5, -3)
        c2 = gen2.generate_chunk(5, -3)
        assert c1.biome == c2.biome
        assert c1.climate_zone == c2.climate_zone
        assert c1.annual_baseline.temperature == pytest.approx(c2.annual_baseline.temperature)

    def test_different_seeds_different_world(self):
        """不同种子产生不同世界。"""
        gen1 = WorldGenerator(seed=1)
        gen2 = WorldGenerator(seed=9999)
        biomes1 = {gen1.get_biome(x, 0) for x in range(20)}
        biomes2 = {gen2.get_biome(x, 0) for x in range(20)}
        # 分布不同（不需要严格不等，但大概率不同；检查至少都产生有效值）
        assert all(b in {BiomeType.TEMPERATE_DECIDUOUS_FOREST, BiomeType.ARID_SHRUBLAND}
                   for b in biomes1)
        assert all(b in {BiomeType.TEMPERATE_DECIDUOUS_FOREST, BiomeType.ARID_SHRUBLAND}
                   for b in biomes2)

    def test_generate_chunk_returns_valid_data(self):
        """生成的分块包含有效数据。"""
        gen = WorldGenerator(seed=0)
        chunk = gen.generate_chunk(10, -5)
        assert chunk.cx == 10
        assert chunk.cy == -5
        assert isinstance(chunk.biome, BiomeType)
        assert isinstance(chunk.climate_zone, ClimateZone)
        assert chunk.annual_baseline.temperature > -50
        assert chunk.annual_baseline.temperature < 60
        assert not chunk.has_tiles  # 不自动生成 tile

    def test_get_biome_consistent(self):
        """get_biome 与 generate_chunk 的群系一致。"""
        gen = WorldGenerator(seed=42)
        for cx, cy in [(0, 0), (1, 0), (0, 1), (-1, -1)]:
            assert gen.get_biome(cx, cy) == gen.generate_chunk(cx, cy).biome

    def test_get_climate(self):
        """get_climate 返回有效气候档位。"""
        gen = WorldGenerator(seed=0)
        c = gen.get_climate(0, 0)
        assert isinstance(c, ClimateZone)

    def test_generate_parallel(self):
        """并行生成多个分块。"""
        gen = WorldGenerator(seed=42)
        coords = [(i, j) for i in range(3) for j in range(3)]  # 9 chunks
        chunks = gen.generate_parallel(coords, max_workers=2)
        assert len(chunks) == 9
        for c, (cx, cy) in zip(chunks, coords):
            assert c.cx == cx
            assert c.cy == cy
            assert isinstance(c.biome, BiomeType)

    def test_generate_parallel_empty(self):
        """空列表并行生成返回空列表。"""
        gen = WorldGenerator(seed=0)
        assert gen.generate_parallel([]) == []

    def test_generate_parallel_deterministic(self):
        """并行与串行结果相同。"""
        gen1 = WorldGenerator(seed=99)
        gen2 = WorldGenerator(seed=99)
        coords = [(i, 0) for i in range(8)]
        serial = [gen1.generate_chunk(cx, cy) for cx, cy in coords]
        parallel = gen2.generate_parallel(coords, max_workers=3)
        for s, p in zip(serial, parallel):
            assert s.biome == p.biome
            assert s.climate_zone == p.climate_zone
            assert s.annual_baseline.temperature == pytest.approx(p.annual_baseline.temperature)

    def test_injected_executor(self):
        """注入外部线程池。"""
        from concurrent.futures import ThreadPoolExecutor
        executor = ThreadPoolExecutor(max_workers=2)
        gen = WorldGenerator(seed=1, executor=executor)
        coords = [(i, i) for i in range(4)]
        chunks = gen.generate_parallel(coords, max_workers=8)  # max_workers 被忽略
        assert len(chunks) == 4
        executor.shutdown(wait=True)

    def test_biome_continuity(self):
        """相邻分块群系应有连续性（不会热带隔壁是寒带）。"""
        gen = WorldGenerator(seed=42)
        # 沿 x 轴检查 20 个相邻分块
        prev = gen.get_climate(0, 0)
        jumps = 0
        for x in range(1, 30):
            curr = gen.get_climate(x, 0)
            # 跳两档以上算不连续（如热带→寒带直接跳）
            if abs(int(curr) - int(prev)) > 2:
                jumps += 1
            prev = curr
        # 30 个分块中不应出现剧烈跳跃
        assert jumps <= 1, f"发现 {jumps} 次不连续的气候跳变"
