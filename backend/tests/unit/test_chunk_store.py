"""ChunkStore 单元测试。

覆盖：LRU 命中/淘汰/访问顺序、淘汰写库（write-back，已加载全量落盘）、
on_evict 回调、脏标记不变量（dirty ⇒ 持有网格）、flush/close 持久化、
已落盘坐标集合（重访不重写）、zlib 压缩存储、SQLite roundtrip。

数据库使用 tmp_path，测试间完全隔离。
"""

import pytest

from ascend.space import BiomeType, ClimateZone, WeatherParams
from ascend.space.chunk import ChunkData
from ascend.space.chunk_store import ChunkStore
from ascend.space.tile_grid import TileGrid
from ascend.space.terrain import TerrainType


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
        """重复 put 同一对象不增加计数，仅刷新顺序。"""
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
    """SQLite write-back 持久化（已加载 chunk 全量落盘）。"""

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

    def test_T7b_clean_eviction_persists_first_load(self, db_path):
        """clean chunk 首次加载后被淘汰：写回库（否则重访仍要重生成）。"""
        store = ChunkStore(db_path, max_size=1)
        try:
            store.put(_make_chunk(0, 0, with_tiles=True))
            store.put(_make_chunk(1, 0))  # 淘汰 (0,0)，首次加载 → 写回

            assert store.contains_tiles(0, 0)
            assert store.load_tiles(0, 0) is not None
        finally:
            store.close()

    def test_T7c_clean_eviction_skips_when_already_persisted(self, db_path):
        """已落盘 clean chunk 淘汰即弃：库中已有，不重写。"""
        store = ChunkStore(db_path, max_size=2)
        try:
            store.put(_make_chunk(0, 0, with_tiles=True))
            store.flush()  # (0,0) 已落盘
            assert store._persisted_coords == {(0, 0)}

            store2 = ChunkStore(db_path, max_size=1)  # 新连接：集合从库重建
            try:
                store2.put(_make_chunk(0, 0, with_tiles=True))
                store2.put(_make_chunk(1, 0))  # 淘汰 (0,0) → 已落盘，不写
                assert store2.load_tiles(0, 0) is not None, "库中已有记录"
                assert store2._persisted_coords == {(0, 0)}, "重写不应入集合"
            finally:
                store2.close()
        finally:
            store.close()

    def test_T8_mark_dirty_requires_loaded_grid(self, db_path):
        """无 tile 网格的 chunk 不能置脏——脏 chunk 必须持有落盘数据源。"""
        store = ChunkStore(db_path, max_size=4)
        try:
            store.put(_make_chunk(0, 0, with_tiles=False))
            with pytest.raises(ValueError):
                store.mark_dirty(0, 0)
        finally:
            store.close()

    def test_T8b_clean_eviction_without_tiles_not_persisted(self, db_path):
        """clean 且无 tile 的 chunk 淘汰时不写库。"""
        store = ChunkStore(db_path, max_size=1)
        try:
            store.put(_make_chunk(0, 0, with_tiles=False))
            store.put(_make_chunk(1, 0))  # 淘汰 (0,0)
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

    def test_T10_flush_persists_all_loaded(self, db_path):
        """flush 落盘所有已加载 chunk（clean 首次加载 + dirty），返回写入数。"""
        store = ChunkStore(db_path, max_size=4)
        try:
            store.put(_make_chunk(0, 0, with_tiles=True))
            store.put(_make_chunk(1, 0, with_tiles=True))
            store.mark_dirty(1, 0)
            assert store.flush() == 2, "clean 首次加载与 dirty 都落盘"
            assert store.contains_tiles(0, 0), "clean 落盘"
            assert store.contains_tiles(1, 0), "dirty 落盘"
            assert store.get(1, 0).dirty is False, "落盘后脏标记清除"
            assert store.flush() == 0, "全部已落盘后返回 0"
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

    def test_T11b_restored_chunk_player_change_survives_eviction(self, db_path):
        """恢复的 chunk 保持 clean；玩家改动经 mark_dirty 后淘汰必落盘。

        恢复 = 内容与库中一致，不置脏（无冗余重写）；改动必须走
        mark_dirty 统一入口，脏 chunk 淘汰时写回、跨重启保留。
        """
        # 初始：写入一行（模拟已加载落盘）
        store = ChunkStore(db_path, max_size=4)
        c = _make_chunk(0, 0, with_tiles=True)
        store.put(c)
        store.flush()
        store.close()

        # 重新打开：恢复网格（不置脏）→ 玩家改动 → 淘汰 → 修改必须跨重启存活
        store2 = ChunkStore(db_path, max_size=1)
        try:
            grid = store2.load_tiles(0, 0)
            assert grid is not None
            restored = _make_chunk(0, 0)
            restored.restore_tiles(grid)
            assert restored.dirty is False, "恢复的 chunk 不置脏（库中已有）"
            restored.tile_grid.set(10, 10, TerrainType.SAND)
            store2.put(restored)
            store2.mark_dirty(0, 0)  # 玩家改动统一入口
            store2.put(_make_chunk(1, 0))  # 淘汰 (0,0) → dirty 落盘
        finally:
            store2.close()

        store3 = ChunkStore(db_path, max_size=4)
        try:
            grid = store3.load_tiles(0, 0)
            assert grid is not None
            assert grid.get(10, 10) == TerrainType.SAND, "修改跨重启持续保留"
        finally:
            store3.close()

    def test_T12_mark_dirty_idempotent_and_flush_clears(self, db_path):
        """mark_dirty 重复调用无副作用，flush 后脏标记被清除。"""
        store = ChunkStore(db_path, max_size=4)
        try:
            store.put(_make_chunk(0, 0, with_tiles=True))
            store.mark_dirty(0, 0)
            store.mark_dirty(0, 0)
            assert store.get(0, 0).dirty is True
            store.flush()
            assert store.contains_tiles(0, 0)
            assert store.get(0, 0).dirty is False
        finally:
            store.close()

    def test_T13_dirty_eviction_write_committed_immediately(self, db_path):
        """dirty 淘汰写入立即 commit：第二个独立连接可见（崩溃安全）。"""
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

    def test_T14_mark_dirty_requires_cached_chunk(self, db_path):
        """不在缓存中的坐标不能置脏。"""
        store = ChunkStore(db_path, max_size=4)
        try:
            with pytest.raises(ValueError):
                store.mark_dirty(9, 9)
        finally:
            store.close()

    def test_T15_flush_persists_and_reports_count(self, db_path):
        """flush 落盘待落盘 chunk 并返回写入数（脉搏保存入口语义）。"""
        store = ChunkStore(db_path, max_size=4)
        try:
            store.put(_make_chunk(0, 0, with_tiles=True))
            store.put(_make_chunk(1, 0, with_tiles=True))
            store.mark_dirty(1, 0)
            assert store.flush() == 2, "clean 首次加载 + dirty 都写入"
            assert store.contains_tiles(0, 0)
            assert store.contains_tiles(1, 0)
            assert store.get(1, 0).dirty is False
            assert store.flush() == 0, "全部已落盘时返回 0"
        finally:
            store.close()

    def test_T19_clean_chunk_roundtrip_across_reopen(self, db_path):
        """clean chunk 落盘后重开：可直接恢复，免重新生成。"""
        chunk = _make_chunk(3, 4, with_tiles=True)
        original = chunk.tile_grid.to_bytes()

        store = ChunkStore(db_path, max_size=4)
        store.put(chunk)
        assert store.flush() == 1, "首次加载落盘"
        store.close()

        store2 = ChunkStore(db_path, max_size=4)
        try:
            grid = store2.load_tiles(3, 4)
            assert grid is not None, "重开即可恢复（免重生成）"
            assert grid.to_bytes() == original, "内容一致（确定性）"
        finally:
            store2.close()

    def test_T20_reexplored_chunk_not_rewritten(self, db_path):
        """重访 chunk（淘汰后重新生成）不重写：已落盘坐标集合命中。"""
        chunk = _make_chunk(0, 0, with_tiles=True)
        store = ChunkStore(db_path, max_size=1)
        try:
            store.put(chunk)
            assert store.flush() == 1

            # 模拟重访：新 chunk 对象（重新生成），同一坐标
            store.put(_make_chunk(5, 5, with_tiles=True))  # 淘汰 (0,0)：已落盘，不写
            assert store.flush() == 1, "仅新加载的 (5,5) 写入"

            store.put(_make_chunk(0, 0, with_tiles=True))  # 重访 (0,0)
            assert store.flush() == 0, "重访内容不变，不重写"
            assert store.contains_tiles(0, 0), "库中仍有记录"
        finally:
            store.close()

    def test_T21_blob_compressed_and_legacy_readable(self, db_path):
        """存储 BLOB 为 zlib 压缩；旧版明文格式仍可读取（兼容）。"""
        import sqlite3
        import zlib

        chunk = _make_chunk(0, 0, with_tiles=True)
        raw = chunk.tile_grid.to_bytes()

        store = ChunkStore(db_path, max_size=4)
        store.put(chunk)
        store.flush()
        store.close()

        # 库中 BLOB 带压缩前缀且明显小于明文
        con = sqlite3.connect(db_path)
        try:
            blob = bytes(con.execute(
                "SELECT tiles FROM chunk_tiles WHERE cx = 0 AND cy = 0"
            ).fetchone()[0])
        finally:
            con.close()
        assert blob[:2] == b"ZC", "压缩前缀"
        assert len(blob) < len(raw) / 2, "压缩后应显著小于明文"
        assert zlib.decompress(blob[2:]) == raw

        # 旧版明文（无前缀、无 settled_day 列）仍可读：先删列建旧表
        con = sqlite3.connect(db_path)
        con.execute("DROP TABLE chunk_tiles")
        con.execute(
            "CREATE TABLE chunk_tiles (cx INTEGER, cy INTEGER, "
            "tiles BLOB NOT NULL, PRIMARY KEY (cx, cy))"
        )
        con.execute(
            "INSERT OR REPLACE INTO chunk_tiles VALUES (9, 9, ?)",
            (sqlite3.Binary(raw),),
        )
        con.commit()
        con.close()
        store2 = ChunkStore(db_path, max_size=4)
        try:
            grid = store2.load_tiles(9, 9)
            assert grid is not None and grid.to_bytes() == raw, "旧明文兼容"
            assert store2._persisted_coords >= {(9, 9)}, "旧行进入已落盘集合"
            saved = store2.load_tiles_with_day(9, 9)
            assert saved is not None and saved[1] == 0, "旧库补列默认 0"
        finally:
            store2.close()

    def test_T22_persisted_set_rebuilt_from_db(self, db_path):
        """启动时已落盘坐标集合从库重建（崩溃后集合与库一致）。"""
        store = ChunkStore(db_path, max_size=4)
        store.put(_make_chunk(0, 0, with_tiles=True))
        store.put(_make_chunk(1, 0, with_tiles=True))
        store.flush()
        store.close()

        store2 = ChunkStore(db_path, max_size=4)
        try:
            assert store2._persisted_coords == {(0, 0), (1, 0)}, "集合从库重建"
        finally:
            store2.close()

    def test_T16_put_refuses_to_replace_dirty_chunk(self, db_path):
        """不能替换未落盘的脏 chunk（否则其脏状态被静默丢弃）。"""
        store = ChunkStore(db_path, max_size=4)
        try:
            a = _make_chunk(0, 0, with_tiles=True)
            store.put(a)
            store.mark_dirty(0, 0)
            with pytest.raises(ValueError):
                store.put(_make_chunk(0, 0, with_tiles=True))
            store.put(a)  # 同一对象重复 put 允许（仅刷新顺序）
            assert store.get(0, 0) is a
        finally:
            store.close()

    def test_T17_put_rejects_dirty_chunk_without_grid(self, db_path):
        """缓存边界守卫：脏但无网格的 chunk 不得进入缓存。"""
        store = ChunkStore(db_path, max_size=4)
        try:
            c = _make_chunk(0, 0, with_tiles=False)
            c.dirty = True  # 直接构造非法状态，验证边界拦截
            with pytest.raises(ValueError):
                store.put(c)
        finally:
            store.close()

    def test_T18_dirty_chunk_cannot_unload_then_evicts_safely(self, db_path):
        """脏 chunk 拒绝卸载网格 → 淘汰时数据源必然在场，改动落盘。

        锁定不变量 end-to-end：置脏后 unload_tiles 返回 False，
        随后淘汰仍能持久化网格。
        """
        store = ChunkStore(db_path, max_size=1)
        try:
            chunk = _make_chunk(0, 0, with_tiles=True)
            store.put(chunk)
            store.mark_dirty(0, 0)
            assert chunk.unload_tiles() is False, "脏 chunk 拒绝卸载"
            assert chunk.tile_grid is not None
            store.put(_make_chunk(1, 0))  # 淘汰 (0,0) → 写库
            assert store.contains_tiles(0, 0)
        finally:
            store.close()


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
