"""ChunkStore — 分块数据 LRU 缓存 + SQLite 持久化。

职责:
  1. LRU 内存缓存 ChunkData（有界地图可控容量）
  2. **已加载 chunk 全量落盘**——首次加载的 chunk（含确定性生成的
     clean chunk）在保存脉搏时写入 SQLite，避免重访/读档时重新生成
     （~1s/chunk）；重访的 chunk 靠 _persisted_coords 集合识别，
     内容不变则不重写
  3. 玩家改动（dirty chunk）落盘是强制的（玩家修改不可再生）
  4. 从 SQLite 恢复已持久化的改动 chunk
  5. flush() 在正常退出与快照前保存所有待落盘 chunk

脏标记位于 ChunkData.dirty（数据自带状态，无平行标记结构），
不变量：**dirty ⇒ 持有 tile_grid**。置脏入口（mark_dirty）要求
网格在场，覆盖/卸载入口（ChunkData.generate_tiles / unload_tiles）
拒绝脏 chunk——脏 chunk 在任何时刻都保有落盘所需的数据源。

落盘判定（已落盘坐标集合）：
   需落盘 = 持有网格 and (dirty or 坐标不在 _persisted_coords)
_persisted_coords 只增不删（库中行无删除路径）、启动时从库重建，
与库必然一致；落盘后 dirty 清除、坐标入集合。

淘汰策略（write-back on eviction）：
   待落盘 chunk 在淘汰时写库提交（脉搏之间的安全网）；
   已落盘 clean chunk 淘汰即弃（库中已有，重访直接恢复）。
   SQLite WAL 模式保证写入中途崩溃不会损坏数据库。

存储格式：TileGrid BLOB 经 zlib 压缩，前缀区分压缩/旧版明文
（"ZC" = zlib，无前缀 = 旧库明文），读取自动兼容。
"""

import os
import sqlite3
import threading
import time as _real_time
import zlib
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

# 存储 BLOB 前缀：压缩（"ZC" + zlib 数据）/ 旧版明文（无前缀），
# 读取按前缀自动分流，旧库兼容。
_BLOB_ZLIB: bytes = b"ZC"


class ChunkStore:
    """分块数据缓存与持久化存储。

    持久化策略：
      - 已加载 chunk 全量落盘（含确定性 clean chunk，免重访重生成）；
        _persisted_coords（单调集合，启动时从库重建）记录已落盘坐标，
        内容不变不重写；
      - SQLite 中的行 = 已加载 chunk（含玩家改动），经 load_tiles
        读取、ChunkData.restore_tiles 恢复。

    用法:
        store = ChunkStore("save/chunks.db", max_size=49)

        chunk = store.get(cx, cy)
        store.put(chunk)

        saved_grid = store.load_tiles(cx, cy)
        if store.contains_tiles(cx, cy):
            ...

        store.mark_dirty(cx, cy)          # 标记缓存中的 chunk 为玩家修改

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
            "settled_day INTEGER NOT NULL DEFAULT 0, "
            "PRIMARY KEY (cx, cy))"
        )
# 旧库无 settled_day 列（TileGrid v1 时期建表）：就地补列（旧行默认
# 0 = 未结算）。注意：v1 期 BLOB 无状态段，from_bytes 按版本拒绝——
# 旧存档的 chunk_tiles 数据无法解码（设计取舍：无向后兼容，见
# tile_grid._TILEGRID_VERSION 文档），settled_day=0 仅对 v2 数据生效。
        cols = {
            r["name"]
            for r in self._db.execute("PRAGMA table_info(chunk_tiles)").fetchall()
        }
        if "settled_day" not in cols:
            self._db.execute(
                "ALTER TABLE chunk_tiles ADD COLUMN "
                "settled_day INTEGER NOT NULL DEFAULT 0"
            )
        self._db.execute("CREATE INDEX IF NOT EXISTS idx_chunk_tiles_coord ON chunk_tiles(cx, cy)")
        # 已落盘坐标集合：只增不删（库中行无删除路径）、启动时从库
        # 重建，与库必然一致。落盘判定 = dirty or 坐标不在集合。
        rows = self._db.execute(
            "SELECT cx, cy FROM chunk_tiles"
        ).fetchall()
        self._persisted_coords: set[tuple[int, int]] = {(r[0], r[1]) for r in rows}
        logger.info(
            "ChunkStore 就绪: %s max_size=%d 已落盘=%d",
            db_path, max_size, len(self._persisted_coords),
        )

    def __repr__(self) -> str:
        dirty = sum(1 for chunk in self._cache.values() if chunk.dirty)
        return (
            f"ChunkStore(cached={len(self._cache)}/{self._max_size}, "
            f"dirty={dirty})"
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

        若同一对象已在缓存中：仅移到末尾（重复 put 刷新顺序）。
        若坐标已被另一脏 chunk 占用：拒绝替换——替换会静默丢弃
        其未落盘的脏状态。

        Args:
            chunk: 要缓存的 ChunkData。

        Raises:
            ValueError: chunk 为脏但无网格，或目标坐标被另一
                未落盘的脏 chunk 占用。
        """
        if chunk.dirty and chunk.tile_grid is None:
            raise ValueError(
                f"脏 chunk 必须持有网格: ({chunk.cx}, {chunk.cy})"
            )
        key = chunk.chunk_key
        with self._lock:
            if key in self._cache:
                existing = self._cache[key]
                if existing.dirty and existing is not chunk:
                    raise ValueError(
                        f"不能替换未落盘的脏 chunk: ({chunk.cx}, {chunk.cy})"
                    )
                self._cache[key] = chunk
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
        """标记缓存中的 chunk 为已修改（玩家改动）。

        只允许标记持有网格的缓存 chunk：脏 chunk 的网格是淘汰/
        退出时落盘的数据源，无网格则无从落盘。

        幂等：重复标记无副作用。

        Args:
            cx, cy: chunk 坐标。

        Raises:
            ValueError: chunk 不在缓存中，或尚未生成 tile 网格。
        """
        with self._lock:
            chunk = self._cache.get((cx, cy))
            if chunk is None:
                raise ValueError(f"chunk 不在缓存中: ({cx}, {cy})")
            if chunk.tile_grid is None:
                raise ValueError(f"chunk 尚未生成 tile 网格: ({cx}, {cy})")
            chunk.dirty = True

    # ── SQLite 持久化 ───────────────────────────────────

    def load_tiles_with_day(self, cx: int, cy: int) -> "tuple[TileGrid, int] | None":
        """从 SQLite 加载已持久化的 TileGrid 及其状态结算日。

        Args:
            cx, cy: chunk 坐标。

        Returns:
            (TileGrid, settled_day)；无记录返回 None。settled_day=0
            表示旧库/未结算记录（状态全 0，按全新处理）。
        """
        with self._lock:
            row = self._db.execute(
                "SELECT tiles, settled_day FROM chunk_tiles "
                "WHERE cx = ? AND cy = ?", (cx, cy),
            ).fetchone()
        if row is None:
            return None
        blob = bytes(row["tiles"])
        if blob[:2] == _BLOB_ZLIB:
            blob = zlib.decompress(blob[2:])
        try:
            grid = TileGrid.from_bytes(blob)
        except ValueError as exc:
            # v1 存档（无状态段）加载失败是硬性不兼容——给出明确
            # 中文提示而非让上层崩溃/泛化"处理失败"
            raise RuntimeError(
                f"chunk ({cx},{cy}) 数据无法解码（{exc}）——"
                f"旧版存档与当前版本不兼容，无法加载该区块"
            ) from exc
        return grid, int(row["settled_day"])

    def load_tiles(self, cx: int, cy: int) -> TileGrid | None:
        """从 SQLite 加载已持久化的 TileGrid。

        库中行 = 已加载 chunk（含玩家改动）。BLOB 为 zlib 压缩格式，
        旧版明文（无前缀）自动兼容读取。调用方应以
        ChunkData.restore_tiles 恢复网格。需要结算日的调用方用
        load_tiles_with_day。

        Args:
            cx, cy: chunk 坐标。

        Returns:
            反序列化的 TileGrid，无记录返回 None。
        """
        loaded = self.load_tiles_with_day(cx, cy)
        return loaded[0] if loaded is not None else None

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

    def _save_tiles(
        self, cx: int, cy: int, grid: TileGrid, settled_day: int = 0,
    ) -> None:
        """将单个 chunk 的 TileGrid 写入 SQLite（zlib 压缩，INSERT OR REPLACE）。"""
        blob = _BLOB_ZLIB + zlib.compress(grid.to_bytes(), zlib.Z_DEFAULT_COMPRESSION)
        self._db.execute(
            "INSERT OR REPLACE INTO chunk_tiles VALUES (?, ?, ?, ?)",
            (cx, cy, sqlite3.Binary(blob), int(settled_day)),
        )

    def _persist(self, chunk: ChunkData) -> None:
        """将待落盘 chunk 的网格写回 SQLite 并更新状态。

        调用方须持有 _lock。脏标记不变量（dirty ⇒ 持有网格）
        保证网格在场；违反时抛错而非静默跳过——脏数据不可丢失。

        落盘后：脏标记清除、坐标记入 _persisted_coords（重访不再重写）。

        Raises:
            RuntimeError: 脏 chunk 无网格（不变量被破坏）。
        """
        grid = chunk.tile_grid
        if grid is None:
            raise RuntimeError(
                f"脏 chunk 无网格（不变量破坏）: ({chunk.cx}, {chunk.cy})"
            )
        self._save_tiles(
            chunk.cx, chunk.cy, grid, settled_day=chunk.settled_day,
        )
        chunk.dirty = False
        self._persisted_coords.add((chunk.cx, chunk.cy))

    def _flush_persistable(self) -> int:
        """落盘缓存中所有待落盘 chunk（dirty 或首次加载）并更新状态。

        无网格的 chunk（详细层未生成）无可落盘数据，跳过。

        Returns:
            实际写入的 chunk 数。
        """
        with self._lock:
            count = 0
            for chunk in self._cache.values():
                if chunk.tile_grid is None:
                    continue
                if chunk.dirty or (chunk.cx, chunk.cy) not in self._persisted_coords:
                    self._persist(chunk)
                    count += 1
            return count

    def flush(self) -> int:
        """将缓存中所有待落盘 chunk 写回 SQLite 并提交。

        待落盘 = 玩家改动（dirty）或首次加载（坐标不在已落盘集合）。
        已落盘 chunk 跳过（重访内容不变不重写）。正常退出、保存
        脉搏与快照前调用，确保已加载 chunk 与玩家改动持久化。

        Returns:
            实际写入的 chunk 数。
        """
        count = self._flush_persistable()
        if count:
            with self._lock:
                self._db.commit()
            logger.info("已 flush %d 个 chunk", count)
        return count

    def checkpoint(self) -> None:
        """WAL 强制写回主库（快照打包前调用，保证文件副本完整）。

        WAL 模式下提交的数据可能仍留在 -wal 文件，直接拷贝 .db
        会丢失这些数据；checkpoint 后 .db 即为完整一致快照。
        """
        for attempt in range(3):
            with self._lock:
                rows = self._db.execute(
                    "PRAGMA wal_checkpoint(FULL)"
                ).fetchall()
            if not rows or rows[0][0] == 0:
                return
            # busy=1：其它连接（如 mmap 读）占用了 WAL 锁，稍后重试
            logger.warning(
                "WAL checkpoint 未完全执行 (attempt=%d, busy=%d): %s",
                attempt + 1, rows[0][0], rows[0],
            )
            _real_time.sleep(0.01)
        logger.error(
            "WAL checkpoint 连续失败，快照可能缺失未写回数据: %s", rows[0],
        )

    def verify(self) -> None:
        """校验数据库完整性（PRAGMA integrity_check，设计文档承诺）。

        读档时调用：数据库是明文 SQLite，防篡改靠完整性校验——
        损坏/被外部工具改写时拒绝加载。

        Raises:
            ValueError: 完整性校验失败（数据库损坏或被篡改）。
        """
        try:
            with self._lock:
                rows = self._db.execute("PRAGMA integrity_check").fetchall()
        except sqlite3.DatabaseError as exc:
            logger.error("ChunkStore 完整性校验失败: %s", exc)
            raise ValueError(
                "存档 chunk 数据库损坏或被篡改，拒绝加载"
            ) from exc
        if not rows or rows[0][0] != "ok":
            logger.error("ChunkStore 完整性校验失败: %s", rows[:5])
            raise ValueError("存档 chunk 数据库损坏或被篡改，拒绝加载")

    def close(self) -> None:
        """关闭 ChunkStore，先 flush 再关闭数据库。"""
        self.flush()
        self._db.close()
        logger.info("ChunkStore 已关闭")

    # ── 内部 ────────────────────────────────────────────

    def _evict_if_needed(self) -> None:
        """淘汰 LRU 头部（最久未访问）直到缓存不超限。

        待落盘 chunk（dirty 或首次加载未落盘）淘汰前先持久化——
        dirty 不可再生、首次加载的 clean chunk 不落盘则重访仍要
        重新生成；已落盘 clean chunk 淘汰即弃（库中已有，重访
        直接恢复）。写入提交后才算安全，跨进程重启不丢失。
        """
        wrote = False
        while len(self._cache) >= self._max_size:
            key, chunk = self._cache.popitem(last=False)
            if chunk.tile_grid is not None and (
                chunk.dirty or key not in self._persisted_coords
            ):
                self._persist(chunk)
                wrote = True
            if self._on_evict:
                self._on_evict(*key)
        if wrote:
            self._db.commit()
