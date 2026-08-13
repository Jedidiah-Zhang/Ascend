"""ChunkStore 单元测试。

覆盖：LRU 命中/淘汰/访问顺序、淘汰写库（write-back，仅 dirty 落盘）、
on_evict 回调、脏标记、flush/close 持久化、SQLite roundtrip。

数据库使用 tmp_path，测试间完全隔离。
"""

import pytest

from ascend.space import BiomeType, ClimateZone, WeatherParams
from ascend.space.chunk import ChunkData
from ascend.space.chunk_store import ChunkStore
from ascend.space.tile_grid import TileGrid


def _make_chunk(cx: int, cy: int, with_tiles: bool = False) -> ChunkData:
    """构造最小可用 ChunkData。"""
    chunk = ChunkData(
        cx=cx, cy=cy,
        biome=BiomeType.TEMPERATE_MIXED_FOREST,
        climate_zone=ClimateZone.TEMPERATE_FOREST,
        annual_baseline=WeatherParams(15.0, 800.0, 12.0, 100.0, 60.0, 5.0),
    )
    if with_tiles:
        chunk.generate_tiles(TileGrid())
    return chunk


@pytest.fixture
def db_path(tmp_path):
    return str(tmp_path / "chunks.db")


class TestChunkStoreCache:
    """LRU 缓存行为。"""

    def test_T1_put_get_hit(self, db_path):
        """put 后 get 命中同一对象。"""
        store = ChunkStore(db_path, max_size=4)
        try:
            chunk = _make_chunk(1, 2)
            store.put(chunk)
            assert store.get(1, 2) is chunk
            assert (1, 2) in store
            assert len(store) == 1
        finally:
            store.close()

    def test_T2_get_miss_returns_none(self, db_path):
        """未缓存的坐标返回 None。"""
        store = ChunkStore(db_path, max_size=4)
        try:
            assert store.get(9, 9) is None
        finally:
            store.close()

    def test_T3_lru_evicts_oldest(self, db_path):
        """超过 max_size 时淘汰最久未访问的 chunk。"""
        store = ChunkStore(db_path, max_size=2)
        try:
            store.put(_make_chunk(0, 0))
            store.put(_make_chunk(1, 0))
            store.put(_make_chunk(2, 0))  # 触发淘汰 (0,0)
            assert store.get(0, 0) is None
            assert store.get(1, 0) is not None
            assert store.get(2, 0) is not None
        finally:
            store.close()

    def test_T4_get_refreshes_lru_order(self, db_path):
        """get 将 chunk 移到 LRU 末尾，改变淘汰顺序。"""
        store = ChunkStore(db_path, max_size=2)
        try:
            store.put(_make_chunk(0, 0))
            store.put(_make_chunk(1, 0))
            store.get(0, 0)  # 刷新 (0,0)
            store.put(_make_chunk(2, 0))  # 应淘汰 (1,0)
            assert store.get(0, 0) is not None
            assert store.get(1, 0) is None
        finally:
            store.close()

    def test_T5_put_duplicate_moves_to_end(self, db_path):
        """重复 put 同一 chunk 不增加计数，仅刷新顺序。"""
        store = ChunkStore(db_path, max_size=2)
        try:
            a = _make_chunk(0, 0)
            store.put(a)
            store.put(_make_chunk(1, 0))
            store.put(a)  # 刷新 (0,0)
            store.put(_make_chunk(2, 0))  # 应淘汰 (1,0)
            assert len(store) == 2
            assert store.get(0, 0) is a
            assert store.get(1, 0) is None
        finally:
            store.close()

    def test_T6_iteration_helpers(self, db_path):
        """keys/values/items 返回快照列表。"""
        store = ChunkStore(db_path, max_size=4)
        try:
            store.put(_make_chunk(0, 0))
            store.put(_make_chunk(1, 0))
            assert sorted(store.keys()) == [(0, 0), (1, 0)]
            assert len(store.values()) == 2
            assert dict(store.items())[(0, 0)].cx == 0
        finally:
            store.close()


class TestChunkStorePersistence:
    """SQLite write-back 持久化（仅玩家改动落盘）。"""

    def test_T7_dirty_eviction_persists_tiles(self, db_path):
        """带 tile 的 dirty chunk 被淘汰时写入 SQLite，可 load 回来。"""
        store = ChunkStore(db_path, max_size=1)
        try:
            store.put(_make_chunk(0, 0, with_tiles=True))
            store.mark_dirty(0, 0)
            store.put(_make_chunk(1, 0))  # 淘汰 (0,0)

            assert store.contains_tiles(0, 0)
            grid = store.load_tiles(0, 0)
            assert grid is not None
        finally:
            store.close()

    def test_T7b_clean_eviction_not_persisted(self, db_path):
        """clean（确定性生成）chunk 淘汰即弃，不写库（无落盘价值）。

        回归：v2 策略——clean tile 是 seed 的纯函数，可再生，
        淘汰后重访重新生成，不占磁盘与存档体积。
        """
        store = ChunkStore(db_path, max_size=1)
        try:
            store.put(_make_chunk(0, 0, with_tiles=True))
            store.put(_make_chunk(1, 0))  # 淘汰 (0,0)，clean → 不写库

            assert not store.contains_tiles(0, 0)
            assert store.load_tiles(0, 0) is None
        finally:
            store.close()

    def test_T8_eviction_without_tiles_not_persisted(self, db_path):
        """无 tile 的 chunk 淘汰时不写库。"""
        store = ChunkStore(db_path, max_size=1)
        try:
            store.put(_make_chunk(0, 0, with_tiles=False))
            store.mark_dirty(0, 0)
            store.put(_make_chunk(1, 0))
            assert not store.contains_tiles(0, 0)
            assert store.load_tiles(0, 0) is None
        finally:
            store.close()

    def test_T9_eviction_fires_on_evict_callback(self, db_path):
        """淘汰触发 on_evict(cx, cy) 回调。"""
        evicted: list[tuple[int, int]] = []
        store = ChunkStore(db_path, max_size=1,
                           on_evict=lambda cx, cy: evicted.append((cx, cy)))
        try:
            store.put(_make_chunk(0, 0))
            store.put(_make_chunk(1, 0))
            assert evicted == [(0, 0)]
        finally:
            store.close()

    def test_T10_flush_persists_dirty_only(self, db_path):
        """flush 只写 dirty chunk；clean 缓存不落盘。"""
        store = ChunkStore(db_path, max_size=4)
        try:
            store.put(_make_chunk(0, 0, with_tiles=True))
            store.put(_make_chunk(1, 0, with_tiles=True))
            store.mark_dirty(1, 0)
            store.flush()
            assert not store.contains_tiles(0, 0), "clean 不落盘"
            assert store.contains_tiles(1, 0), "dirty 落盘"
        finally:
            store.close()

    def test_T11_dirty_tiles_roundtrip_across_reopen(self, db_path):
        """close 后重新打开 store，dirty tile 数据可恢复且内容一致。"""
        chunk = _make_chunk(3, 4, with_tiles=True)
        original = chunk.tile_grid.to_bytes()

        store = ChunkStore(db_path, max_size=4)
        store.put(chunk)
        store.mark_dirty(3, 4)
        store.close()  # close 内部 flush（仅 dirty）

        store2 = ChunkStore(db_path, max_size=4)
        try:
            grid = store2.load_tiles(3, 4)
            assert grid is not None
            assert grid.to_bytes() == original
        finally:
            store2.close()

    def test_T11b_loaded_row_keeps_dirty_mark(self, db_path):
        """从库中加载的行视为玩家改动，继续保留 dirty 标记。

        回归：库中行 = 修改（确定性 tile 不落盘），加载后若失去
        dirty 标记，后续淘汰/退出会把修改当 clean 丢弃。
        """
        store = ChunkStore(db_path, max_size=4)
        store.put(_make_chunk(0, 0, with_tiles=True))
        store.mark_dirty(0, 0)
        store.close()

        store2 = ChunkStore(db_path, max_size=4)
        try:
            grid = store2.load_tiles(0, 0)
            assert grid is not None
            assert (0, 0) in store2._dirty, "加载的行应保留 dirty 标记"
            store2.put(_make_chunk(0, 0, with_tiles=True))
            store2.close()  # dirty 标记保留 → 再次落盘
        except Exception:
            store2.close()
            raise

        store3 = ChunkStore(db_path, max_size=4)
        try:
            assert store3.contains_tiles(0, 0), "改动跨重启持续保留"
        finally:
            store3.close()

    def test_T12_mark_dirty_idempotent(self, db_path):
        """mark_dirty 重复调用无副作用，淘汰后脏标记被清除。"""
        store = ChunkStore(db_path, max_size=1)
        try:
            store.put(_make_chunk(0, 0, with_tiles=True))
            store.mark_dirty(0, 0)
            store.mark_dirty(0, 0)
            store.put(_make_chunk(1, 0))  # 淘汰 (0,0)
            assert store.contains_tiles(0, 0)
            assert (0, 0) not in store._dirty
        finally:
            store.close()

    def test_T13_dirty_eviction_write_committed_immediately(self, db_path):
        """dirty 淘汰写入立即 commit：第二个独立连接可见（崩溃安全）。

        未提交的写入仅同连接可见，进程崩溃即丢失。
        此测试用第二个 ChunkStore（独立 SQLite 连接）验证已提交。
        """
        store = ChunkStore(db_path, max_size=1)
        store2 = None
        try:
            store.put(_make_chunk(0, 0, with_tiles=True))
            store.mark_dirty(0, 0)
            store.put(_make_chunk(1, 0))  # 淘汰 (0,0) → 写库 + commit

            store2 = ChunkStore(db_path, max_size=4)
            assert store2.contains_tiles(0, 0)
            assert store2.load_tiles(0, 0) is not None
        finally:
            store.close()
            if store2 is not None:
                store2.close()


class TestVerify:
    """数据库完整性校验（读档防篡改）。"""

    def test_verify_passes_on_clean_db(self, db_path):
        """正常数据库校验通过。"""
        store = ChunkStore(db_path)
        try:
            store.verify()
        finally:
            store.close()

    def test_checkpoint_completes_clean_db(self, db_path):
        """WAL checkpoint 在正常数据库上完整执行（busy=0 静默通过）。"""
        store = ChunkStore(db_path)
        try:
            store.checkpoint()
            # 幂等：重复调用不报错
            store.checkpoint()
        finally:
            store.close()

    def test_verify_rejects_corrupted_db(self, db_path):
        """结构性损坏的数据库校验失败（拒绝加载）。"""
        import os
        import sqlite3
        con = sqlite3.connect(db_path)
        con.execute("CREATE TABLE t (id INTEGER PRIMARY KEY, v TEXT)")
        con.execute("INSERT INTO t VALUES (1, 'hello')")
        con.commit()
        con.close()
        # 破坏数据页的页类型字节（页结构损坏，integrity_check 可检测；
        # 记录内字节翻转无页校验和，不在本校验范围内）
        size = os.path.getsize(db_path)
        with open(db_path, "r+b") as f:
            f.seek(size // 2 & ~0xFFF)  # 对齐到页首
            f.write(b"\x00")            # 页类型 0x0D(表叶) → 0x00 非法
        store = ChunkStore(db_path)
        try:
            with pytest.raises(ValueError):
                store.verify()
        finally:
            store.close()
