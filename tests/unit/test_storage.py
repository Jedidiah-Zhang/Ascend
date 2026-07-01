"""WorldStore 持久化测试台 — TDD 先行。

测试 SQLite 持久化层：
  - 保存/加载 chunk 数据
  - 确定性（save→load 一致）
  - 不存在的 chunk 返回 None
  - 覆盖保存
  - 内存缓存命中
  - 跨实例持久化
  - 数据完整性
"""

import sys
import tempfile
from array import array
from pathlib import Path

import pytest

_HERE = Path(__file__).resolve().parent
_BACKEND = _HERE.parent.parent / "ascend-backend"
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

from ascend.space import WorldGenerator, TileGenerator, ChunkData, TileGrid
from ascend.space.storage import WorldStore


# ════════════════════════════════════════════════════════════════
# Fixtures
# ════════════════════════════════════════════════════════════════

@pytest.fixture
def tmp_db():
    """临时数据库路径，测试后自动清理。"""
    with tempfile.TemporaryDirectory() as d:
        yield Path(d) / "test_world.db"


@pytest.fixture
def sample_chunk():
    """生成一个示例 chunk + tile_grid 用于测试。"""
    gen = WorldGenerator(seed=42)
    chunk = gen.generate_chunk(0, 0)
    tile_gen = TileGenerator(seed=42)
    grid = tile_gen.generate(chunk)
    chunk.generate_tiles(grid)
    return chunk


# ════════════════════════════════════════════════════════════════

class TestWorldStoreBasic:
    """基本 CRUD 操作。"""

    def test_create_store(self, tmp_db):
        """创建存储实例不崩溃。"""
        store = WorldStore(seed=42, path=str(tmp_db))
        assert store is not None
        store.close()

    def test_save_and_has(self, tmp_db, sample_chunk):
        """保存后 has_chunk 返回 True。"""
        store = WorldStore(seed=42, path=str(tmp_db))
        assert not store.has_chunk(0, 0)
        store.save_chunk(sample_chunk)
        assert store.has_chunk(0, 0)
        store.close()

    def test_load_returns_same_data(self, tmp_db, sample_chunk):
        """加载的数据与保存一致。"""
        store = WorldStore(seed=42, path=str(tmp_db))
        store.save_chunk(sample_chunk)

        loaded = store.load_chunk(0, 0)
        assert loaded is not None
        assert loaded.cx == sample_chunk.cx
        assert loaded.cy == sample_chunk.cy
        assert loaded.biome == sample_chunk.biome
        assert loaded.climate_zone == sample_chunk.climate_zone
        assert loaded.annual_baseline.altitude == pytest.approx(
            sample_chunk.annual_baseline.altitude)
        assert loaded.has_tiles
        assert loaded.tile_grid == sample_chunk.tile_grid
        store.close()

    def test_load_nonexistent(self, tmp_db):
        """未保存的 chunk 返回 None。"""
        store = WorldStore(seed=42, path=str(tmp_db))
        assert store.load_chunk(0, 0) is None
        assert store.load_chunk(999, 999) is None
        store.close()

    def test_overwrite(self, tmp_db, sample_chunk):
        """覆盖保存应更新数据。"""
        store = WorldStore(seed=42, path=str(tmp_db))
        store.save_chunk(sample_chunk)

        # 修改 markers 后重新保存
        sample_chunk.add_marker("test", "updated")
        store.save_chunk(sample_chunk)

        loaded = store.load_chunk(0, 0)
        assert loaded is not None
        assert "test" in loaded.markers
        assert loaded.markers["test"] == "updated"
        store.close()

    def test_delete(self, tmp_db, sample_chunk):
        """删除后 has_chunk 返回 False。"""
        store = WorldStore(seed=42, path=str(tmp_db))
        store.save_chunk(sample_chunk)
        assert store.has_chunk(0, 0)

        store.delete_chunk(0, 0)
        assert not store.has_chunk(0, 0)
        assert store.load_chunk(0, 0) is None
        store.close()

    def test_multiple_chunks(self, tmp_db):
        """保存多个不同 chunk。"""
        store = WorldStore(seed=42, path=str(tmp_db))
        gen = WorldGenerator(seed=42)
        tile_gen = TileGenerator(seed=42)

        for cx, cy in [(0, 0), (0, 1), (1, 0), (-1, -1)]:
            chunk = gen.generate_chunk(cx, cy)
            grid = tile_gen.generate(chunk)
            chunk.generate_tiles(grid)
            store.save_chunk(chunk)

        for cx, cy in [(0, 0), (0, 1), (1, 0), (-1, -1)]:
            assert store.has_chunk(cx, cy)
            loaded = store.load_chunk(cx, cy)
            assert loaded.cx == cx
            assert loaded.cy == cy

        # 未保存的不存在
        assert not store.has_chunk(5, 5)
        store.close()


class TestWorldStorePersistence:
    """跨实例持久化。"""

    def test_data_survives_reopen(self, tmp_db, sample_chunk):
        """关闭存储后重新打开，数据仍在。"""
        store1 = WorldStore(seed=42, path=str(tmp_db))
        store1.save_chunk(sample_chunk)
        store1.close()

        store2 = WorldStore(seed=42, path=str(tmp_db))
        assert store2.has_chunk(0, 0)
        loaded = store2.load_chunk(0, 0)
        assert loaded is not None
        assert loaded.cx == 0
        store2.close()

    def test_different_seeds_independent(self, tmp_db, sample_chunk):
        """不同 seed 的数据互相独立。"""
        store_a = WorldStore(seed=42, path=str(tmp_db))
        store_b = WorldStore(seed=99, path=str(tmp_db))

        store_a.save_chunk(sample_chunk)  # seed=42, chunk (0,0)

        assert not store_b.has_chunk(0, 0)  # seed=99 看不到 seed=42 的数据
        store_a.close()
        store_b.close()

    def test_same_path_different_seeds_isolated(self, tmp_db):
        """同一数据库文件内不同 seed 的数据隔离。"""
        # 注意: seed 是表的主键一部分，所以天然隔离
        store = WorldStore(seed=42, path=str(tmp_db))
        gen = WorldGenerator(seed=42)
        tile_gen = TileGenerator(seed=42)
        chunk = gen.generate_chunk(0, 0)
        grid = tile_gen.generate(chunk)
        chunk.generate_tiles(grid)
        store.save_chunk(chunk)
        store.close()

        # 用不同 seed 打开同一数据库
        store2 = WorldStore(seed=99, path=str(tmp_db))
        assert not store2.has_chunk(0, 0)
        store2.close()


class TestWorldStoreCache:
    """内存缓存行为。"""

    def test_cache_hit(self, tmp_db, sample_chunk):
        """二次加载应从内存缓存命中（更快）。"""
        store = WorldStore(seed=42, path=str(tmp_db), cache_size=10)
        store.save_chunk(sample_chunk)

        # 第一次加载（从 DB）
        c1 = store.load_chunk(0, 0)
        # 第二次加载（应命中缓存）
        c2 = store.load_chunk(0, 0)

        assert c1.cx == c2.cx
        assert c1.tile_grid == c2.tile_grid
        store.close()

    def test_cache_eviction(self, tmp_db):
        """缓存满后旧条目被淘汰。"""
        store = WorldStore(seed=42, path=str(tmp_db), cache_size=3)
        gen = WorldGenerator(seed=42)
        tile_gen = TileGenerator(seed=42)

        # 保存 5 个 chunk
        for i in range(5):
            chunk = gen.generate_chunk(i, 0)
            grid = tile_gen.generate(chunk)
            chunk.generate_tiles(grid)
            store.save_chunk(chunk)

        # 全部能从 DB 加载
        for i in range(5):
            assert store.load_chunk(i, 0) is not None
        store.close()

    def test_auto_create_dir(self, tmp_db):
        """路径不存在时自动创建。"""
        deep_path = tmp_db / "deep" / "nested" / "world.db"
        store = WorldStore(seed=42, path=str(deep_path))
        assert deep_path.parent.exists()
        store.close()

    def test_close_idempotent(self, tmp_db):
        """多次 close 不崩溃。"""
        store = WorldStore(seed=42, path=str(tmp_db))
        store.close()
        store.close()  # 第二次


class TestWorldStoreDataIntegrity:
    """数据完整性验证。"""

    def test_tiles_array_preserved(self, tmp_db):
        """TileGrid 的 array('H') 数据完整保存和恢复。"""
        store = WorldStore(seed=42, path=str(tmp_db))
        gen = WorldGenerator(seed=42)
        tile_gen = TileGenerator(seed=42)
        chunk = gen.generate_chunk(0, 0)
        grid = tile_gen.generate(chunk)
        chunk.generate_tiles(grid)

        store.save_chunk(chunk)
        loaded = store.load_chunk(0, 0)

        # 逐 tile 验证
        for y in range(200):
            for x in range(200):
                assert loaded.tile_grid.get(x, y) == grid.get(x, y)
        store.close()

    def test_markers_preserved(self, tmp_db):
        """标记数据完整保存。"""
        store = WorldStore(seed=42, path=str(tmp_db))
        gen = WorldGenerator(seed=42)
        tile_gen = TileGenerator(seed=42)
        chunk = gen.generate_chunk(0, 0)
        grid = tile_gen.generate(chunk)
        chunk.generate_tiles(grid)
        chunk.add_marker("spawn", "玩家出生点")
        chunk.add_marker("cave_01", "矿洞入口")

        store.save_chunk(chunk)
        loaded = store.load_chunk(0, 0)

        assert loaded.markers == {"spawn": "玩家出生点", "cave_01": "矿洞入口"}
        store.close()

    def test_no_tiles_chunk(self, tmp_db):
        """无 tile 数据的 chunk 也能保存和加载。"""
        store = WorldStore(seed=42, path=str(tmp_db))
        gen = WorldGenerator(seed=42)
        chunk = gen.generate_chunk(0, 0)
        # 不生成 tile

        store.save_chunk(chunk)
        loaded = store.load_chunk(0, 0)

        assert loaded.cx == 0
        assert loaded.cy == 0
        assert not loaded.has_tiles
        store.close()


class TestWorldStoreEdgeCases:
    """边界条件。"""

    def test_large_coordinates(self, tmp_db):
        """大坐标 chunk 正常保存。"""
        store = WorldStore(seed=42, path=str(tmp_db))
        gen = WorldGenerator(seed=42)
        tile_gen = TileGenerator(seed=42)

        for cx, cy in [(10**6, 10**6), (-10**6, -10**6), (0, 2**30)]:
            chunk = gen.generate_chunk(cx, cy)
            grid = tile_gen.generate(chunk)
            chunk.generate_tiles(grid)
            store.save_chunk(chunk)
            assert store.has_chunk(cx, cy)

        store.close()

    def test_save_same_chunk_twice_no_error(self, tmp_db, sample_chunk):
        """重复保存同一 chunk 不报错。"""
        store = WorldStore(seed=42, path=str(tmp_db))
        store.save_chunk(sample_chunk)
        store.save_chunk(sample_chunk)  # 不应崩溃
        store.close()

    def test_delete_nonexistent(self, tmp_db):
        """删除不存在的 chunk 不报错。"""
        store = WorldStore(seed=42, path=str(tmp_db))
        store.delete_chunk(999, 999)  # 不应崩溃
        store.close()
