"""ChunkStore — 分块数据 LRU 缓存 + SQLite 持久化。

职责:
  1. LRU 内存缓存 ChunkData（有界地图可控容量）
  2. 所有 chunk 淘汰时自动写入 SQLite（避免重新生成 tile_grid）
  3. dirty chunk 的持久化是强制的（玩家修改不可再生），clean chunk 是缓存优化
  4. 支持从 SQLite 恢复已被淘汰的 chunk 的 TileGrid
  5. flush() 在正常退出时保存所有缓存中的 chunk

淘汰策略（write-back on eviction）：
  所有 chunk 淘汰时写入 SQLite，无论 dirty 与否。
  下次请求时从 SQLite 加载 tile_grid + 重新生成宏层（宏层生成很快）。
  正常退出时 flush() 强制写入所有未淘汰的缓存 chunk。
  SQLite WAL 模式保证写入中途崩溃不会损坏数据库。
"""

import os
import sqlite3
import threading
from collections import OrderedDict
from collections.abc import Callable

from ascend.config import (
    CHUNK_STORE_DB_PATH as _DEFAULT_DB_PATH,
    CHUNK_STORE_MAX_SIZE as _DEFAULT_MAX_SIZE,
    SQLITE_JOURNAL_MODE,
    SQLITE_SYNCHRONOUS,
    SQLITE_MMAP_SIZE,
    SQLITE_CACHE_SIZE,
)
from ascend.log import get_logger
from .chunk import ChunkData
from .tile_grid import TileGrid

logger = get_logger(__name__)


class ChunkStore:
    """分块数据缓存与持久化存储。

    所有 chunk 的 TileGrid 在淘汰时自动写入 SQLite，
    下次访问时从 SQLite 加载，避免重新生成 tile（昂贵操作）。
    dirty 标记用于区分玩家修改（必须保留）和纯缓存（可丢失）。

    用法:
        store = ChunkStore("save/chunks.db", max_size=49)

        chunk = store.get(cx, cy)
        store.put(chunk)

        saved_grid = store.load_tiles(cx, cy)
        if store.contains_tiles(cx, cy):
            ...

        store.mark_dirty(cx, cy)          # 标记为玩家修改

        for key, chunk in store.items():
            ...
        for chunk in store.values():
            ...

        store.flush()
        store.close()
    """

    def __init__(self, db_path: str = _DEFAULT_DB_PATH, max_size: int = _DEFAULT_MAX_SIZE,
                 on_evict: Callable[[int, int], None] | None = None) -> None:
        self._max_size = max_size
        self._on_evict = on_evict
        self._cache: OrderedDict[tuple[int, int], ChunkData] = OrderedDict()
        self._dirty: set[tuple[int, int]] = set()
        self._lock = threading.RLock()

        os.makedirs(os.path.dirname(db_path) or ".", exist_ok=True)
        self._db = sqlite3.connect(db_path, check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        self._db.execute(f"PRAGMA journal_mode={SQLITE_JOURNAL_MODE}")
        self._db.execute(f"PRAGMA synchronous={SQLITE_SYNCHRONOUS}")
        self._db.execute(f"PRAGMA mmap_size={SQLITE_MMAP_SIZE}")
        self._db.execute(f"PRAGMA cache_size={SQLITE_CACHE_SIZE}")
        self._db.execute(
            "CREATE TABLE IF NOT EXISTS chunk_tiles ("
            "cx INTEGER, cy INTEGER, "
            "tiles BLOB NOT NULL, "
            "PRIMARY KEY (cx, cy))"
        )
        self._db.execute("CREATE INDEX IF NOT EXISTS idx_chunk_tiles_coord ON chunk_tiles(cx, cy)")
        logger.info("ChunkStore 就绪: %s max_size=%d", db_path, max_size)

    def __repr__(self) -> str:
        return (
            f"ChunkStore(cached={len(self._cache)}/{self._max_size}, "
            f"dirty={len(self._dirty)})"
        )

    # ── 缓存查询 ────────────────────────────────────────

    def get(self, cx: int, cy: int) -> ChunkData | None:
        """从缓存中获取 chunk，命中时移到 LRU 末尾。

        Args:
            cx, cy: chunk 坐标。

        Returns:
            命中的 ChunkData，未命中返回 None。
        """
        key = (cx, cy)
        with self._lock:
            if key in self._cache:
                self._cache.move_to_end(key)
                return self._cache[key]
        return None

    def put(self, chunk: ChunkData) -> None:
        """将 chunk 放入缓存，触发 LRU 淘汰。

        若 chunk 已在缓存中，移到末尾（更新访问时间）。
        淘汰的 chunk 自动写入 SQLite。

        Args:
            chunk: 要缓存的 ChunkData。
        """
        key = chunk.chunk_key
        with self._lock:
            if key in self._cache:
                self._cache.move_to_end(key)
                return
            self._evict_if_needed()
            self._cache[key] = chunk

    def __contains__(self, key: tuple[int, int]) -> bool:
        with self._lock:
            return key in self._cache

    def __len__(self) -> int:
        with self._lock:
            return len(self._cache)

    def items(self):
        with self._lock:
            return list(self._cache.items())

    def values(self):
        with self._lock:
            return list(self._cache.values())

    def keys(self):
        with self._lock:
            return list(self._cache.keys())

    # ── 脏标记 ──────────────────────────────────────────

    def mark_dirty(self, cx: int, cy: int) -> None:
        """标记 chunk 为已修改，淘汰时写入 SQLite。

        幂等：重复标记无副作用。

        Args:
            cx, cy: chunk 坐标。
        """
        with self._lock:
            self._dirty.add((cx, cy))

    # ── SQLite 持久化 ───────────────────────────────────

    def load_tiles(self, cx: int, cy: int) -> TileGrid | None:
        """从 SQLite 加载已持久化的 TileGrid。

        Args:
            cx, cy: chunk 坐标。

        Returns:
            反序列化的 TileGrid，无记录返回 None。
        """
        with self._lock:
            row = self._db.execute(
                "SELECT tiles FROM chunk_tiles WHERE cx = ? AND cy = ?", (cx, cy)
            ).fetchone()
        if row is None:
            return None
        return TileGrid.from_bytes(bytes(row["tiles"]))

    def contains_tiles(self, cx: int, cy: int) -> bool:
        """检查 SQLite 中是否有已持久化的 tile 数据。

        Args:
            cx, cy: chunk 坐标。

        Returns:
            True 表示该 chunk 的 TileGrid 已持久化在 SQLite 中。
        """
        with self._lock:
            row = self._db.execute(
                "SELECT 1 FROM chunk_tiles WHERE cx = ? AND cy = ?", (cx, cy)
            ).fetchone()
        return row is not None

    def _save_tiles(self, cx: int, cy: int, grid: TileGrid) -> None:
        """将单个 chunk 的 TileGrid 写入 SQLite（INSERT OR REPLACE）。"""
        blob = grid.to_bytes()
        self._db.execute(
            "INSERT OR REPLACE INTO chunk_tiles VALUES (?, ?, ?)",
            (cx, cy, sqlite3.Binary(blob)),
        )

    def flush(self) -> None:
        """将所有缓存 chunk 的 TileGrid 写回 SQLite。

        正常退出时调用，确保未淘汰的 chunk 持久化。
        """
        with self._lock:
            count = 0
            for key, chunk in self._cache.items():
                if chunk.tile_grid is not None:
                    self._save_tiles(*key, chunk.tile_grid)
                    count += 1
            self._dirty.clear()
            if count:
                logger.info("已 flush %d 个 chunk", count)

    def close(self) -> None:
        """关闭 ChunkStore，先 flush 再关闭数据库。"""
        self.flush()
        self._db.close()
        logger.info("ChunkStore 已关闭")

    # ── 内部 ────────────────────────────────────────────

    def _evict_if_needed(self) -> None:
        """淘汰 LRU 头部（最久未访问）直到缓存不超限。

        淘汰的 chunk 自动写入 SQLite（dirty 和 clean 都保存）。
        """
        while len(self._cache) >= self._max_size:
            key, chunk = self._cache.popitem(last=False)
            if chunk.tile_grid is not None:
                self._save_tiles(*key, chunk.tile_grid)
            self._dirty.discard(key)
            if self._on_evict:
                self._on_evict(*key)
