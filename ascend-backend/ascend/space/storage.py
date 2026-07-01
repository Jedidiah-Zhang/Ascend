"""WorldStore — Chunk 持久化存储（SQLite + 内存 LRU 缓存）。

每个 seed 一个数据库文件，chunk 以 (seed, cx, cy) 为主键。
TileGrid 的 array('H') 以 BLOB 形式存储。

用法:
    store = WorldStore(seed=42, path="data/worlds/")
    store.save_chunk(chunk)  # chunk 必须已有 tile_grid
    loaded = store.load_chunk(cx, cy)
    store.close()
"""

import json
import sqlite3
import threading
from array import array
from collections import OrderedDict
from pathlib import Path

from .chunk import ChunkData
from .climate import ClimateZone, WeatherParams
from .biome import BiomeType
from .tile_grid import TileGrid, TILE_MAP_SIZE


class WorldStore:
    """Chunk 持久化存储。

    使用 SQLite 单文件存储，内存 LRU 缓存加速热数据访问。
    线程安全：所有数据库操作在锁保护下进行。

    Attributes:
        seed: 世界种子。
        path: 数据库文件路径。
    """

    def __init__(
        self,
        seed: int,
        path: str = "data/worlds/",
        *,
        cache_size: int = 256,
    ) -> None:
        """打开或创建持久化存储。

        Args:
            seed: 世界种子。不同 seed 的数据互相隔离。
            path: 数据库文件路径。若为目录则自动创建 <seed>.db 文件。
            cache_size: 内存 LRU 缓存容量（chunk 数）。
        """
        self._seed = seed
        self._cache_size = cache_size
        self._lock = threading.Lock()

        p = Path(path)
        if p.suffix != ".db":
            # 目录 → 自动生成文件名
            p.mkdir(parents=True, exist_ok=True)
            db_path = p / f"world_{seed}.db"
        else:
            p.parent.mkdir(parents=True, exist_ok=True)
            db_path = p

        self._db_path = str(db_path)
        self._conn = sqlite3.connect(self._db_path, check_same_thread=False)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA synchronous=NORMAL")
        self._conn.execute(
            """CREATE TABLE IF NOT EXISTS chunks (
                seed INTEGER NOT NULL,
                cx INTEGER NOT NULL,
                cy INTEGER NOT NULL,
                biome INTEGER,
                climate_zone INTEGER,
                altitude REAL,
                temperature REAL,
                rainfall REAL,
                tiles BLOB,
                markers TEXT,
                generated_at TEXT DEFAULT (datetime('now')),
                PRIMARY KEY (seed, cx, cy)
            )"""
        )
        self._conn.commit()

        # 内存 LRU 缓存
        self._cache: OrderedDict[tuple[int, int], ChunkData] = OrderedDict()

    def __repr__(self) -> str:
        return (
            f"WorldStore(seed={self._seed}, path={self._db_path!r}, "
            f"cache={len(self._cache)}/{self._cache_size})"
        )

    # ── 公共 API ────────────────────────────────────────────

    def has_chunk(self, cx: int, cy: int) -> bool:
        """检查 chunk 是否已保存。

        Args:
            cx, cy: chunk 坐标。

        Returns:
            True 如果已保存。
        """
        with self._lock:
            key = (cx, cy)
            if key in self._cache:
                return True
            row = self._conn.execute(
                "SELECT 1 FROM chunks WHERE seed=? AND cx=? AND cy=?",
                (self._seed, cx, cy),
            ).fetchone()
            return row is not None

    def load_chunk(self, cx: int, cy: int) -> ChunkData | None:
        """加载已保存的 chunk。

        先在内存缓存中查找，未命中则查询 SQLite。

        Args:
            cx, cy: chunk 坐标。

        Returns:
            ChunkData 或 None（如果未保存）。
        """
        with self._lock:
            key = (cx, cy)

            # 内存缓存命中
            if key in self._cache:
                self._cache.move_to_end(key)
                return self._cache[key]

            # 查询 SQLite
            row = self._conn.execute(
                "SELECT biome, climate_zone, altitude, temperature, "
                "rainfall, tiles, markers "
                "FROM chunks WHERE seed=? AND cx=? AND cy=?",
                (self._seed, cx, cy),
            ).fetchone()

            if row is None:
                return None

            chunk = self._row_to_chunk(cx, cy, row)

            # 加入缓存
            self._cache[key] = chunk
            self._cache.move_to_end(key)
            if len(self._cache) > self._cache_size:
                self._cache.popitem(last=False)

            return chunk

    def save_chunk(self, chunk: ChunkData) -> None:
        """保存 chunk 到数据库。

        若 chunk 有 tile_grid，tile 数据以 BLOB 存储。

        Args:
            chunk: 要保存的 ChunkData。
        """
        cx, cy = chunk.cx, chunk.cy
        w = chunk.annual_baseline

        tiles_blob = None
        if chunk.has_tiles and chunk.tile_grid is not None:
            tiles_blob = chunk.tile_grid.raw_data().tobytes()

        markers_json = json.dumps(chunk.markers, ensure_ascii=False) if chunk.markers else None

        with self._lock:
            self._conn.execute(
                """INSERT OR REPLACE INTO chunks
                   (seed, cx, cy, biome, climate_zone, altitude,
                    temperature, rainfall, tiles, markers)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    self._seed, cx, cy,
                    int(chunk.biome), int(chunk.climate_zone),
                    w.altitude, w.temperature, w.rainfall,
                    tiles_blob, markers_json,
                ),
            )
            self._conn.commit()

            # 更新缓存
            key = (cx, cy)
            self._cache[key] = chunk
            self._cache.move_to_end(key)
            if len(self._cache) > self._cache_size:
                self._cache.popitem(last=False)

    def delete_chunk(self, cx: int, cy: int) -> None:
        """删除 chunk 数据。

        Args:
            cx, cy: chunk 坐标。
        """
        with self._lock:
            self._conn.execute(
                "DELETE FROM chunks WHERE seed=? AND cx=? AND cy=?",
                (self._seed, cx, cy),
            )
            self._conn.commit()
            self._cache.pop((cx, cy), None)

    def close(self) -> None:
        """关闭数据库连接。"""
        with self._lock:
            self._cache.clear()
            self._conn.close()

    # ── 内部辅助 ────────────────────────────────────────────

    def _row_to_chunk(
        self, cx: int, cy: int, row: tuple
    ) -> ChunkData:
        """将数据库行转换为 ChunkData。

        Args:
            cx, cy: chunk 坐标。
            row: (biome, climate_zone, altitude, temperature, rainfall, tiles, markers)。

        Returns:
            ChunkData 实例。
        """
        biome = BiomeType(row[0])
        climate = ClimateZone(row[1])

        # 重建简化的 WeatherParams
        weather = WeatherParams(
            temperature=row[3],
            rainfall=row[4],
            sunshine=0.0,
            altitude=row[2],
            humidity=0.0,
            wind_speed=0.0,
        )

        chunk = ChunkData(
            cx=cx,
            cy=cy,
            biome=biome,
            climate_zone=climate,
            annual_baseline=weather,
        )

        # 恢复标记
        if row[6]:
            chunk.markers = json.loads(row[6])

        # 恢复 tile 数据
        if row[5]:
            n = TILE_MAP_SIZE * TILE_MAP_SIZE
            raw = row[5]
            if len(raw) == n * 2:  # array('H') = 2 bytes per tile
                data = array('H')
                data.frombytes(raw)
                chunk.tile_grid = TileGrid(data=data)

        return chunk
