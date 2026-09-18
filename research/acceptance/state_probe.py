"""WC-3 状态归属的实际落盘探针；不读取生产缓存作为恢复证据。"""

from pathlib import Path
import sqlite3
from tempfile import TemporaryDirectory


def persisted_terrain() -> tuple[dict, dict]:
    """返回实际 SQLite 目标及非默认状态的独立期望值。"""
    from ascend.space import (
        BiomeType, ChunkData, ChunkStore, ClimateZone, TileGrid, WeatherParams,
    )

    grid = TileGrid()
    expected = {}
    for key, value in (("moisture", 17), ("snow", 23), ("ice", 31)):
        grid.set_state(key, 0, 0, value)
        grid.set_state(key, grid.size - 1, grid.size - 1, value + 1)
        expected[f"world.terrain.{key}"] = list(grid.state_raw(key))
    expected["world.terrain.integrated_through"] = 123
    chunk = ChunkData(
        0, 0, next(iter(BiomeType)), ClimateZone.TEMPERATE_FOREST,
        WeatherParams(20.0, 800.0, 12.0, 100.0, 60.0, 5.0),
        tile_grid=grid, integrated_through=123,
    )
    with TemporaryDirectory(prefix="ascend-state-probe-") as directory:
        path = str(Path(directory) / "chunks.db")
        store = ChunkStore(path)
        try:
            store.put(chunk)
            store.commit_captured(store.capture_pending())
        finally:
            store.close()
        # 重新打开磁盘数据库，校验真实表/列，而非手写归属表自身。
        with sqlite3.connect(path) as db:
            columns = {row[1] for row in db.execute("PRAGMA table_info(chunk_tiles)")}
        store = ChunkStore(path)
        try:
            loaded = store.load_tiles_with_day(0, 0)
            targets = {}
            if loaded is not None:
                restored, cursor = loaded
                if "tiles" in columns:
                    targets["chunk_tiles.tiles"] = {
                        f"world.terrain.{key}": list(restored.state_raw(key))
                        for key in ("moisture", "snow", "ice")
                    }
                if "integrated_through" in columns:
                    targets["chunk_tiles.integrated_through"] = {
                        "world.terrain.integrated_through": cursor,
                    }
            return targets, expected
        finally:
            store.close()


def audit_carriers(table: dict, payload: dict, terrain: dict, expected: dict) -> list[str]:
    """逐条解析归属目标，并比较恢复值；映射存在不代表字段确实落盘。"""
    problems = []
    for slot, (carrier, path) in table.items():
        try:
            if carrier == "state.json":
                value = payload
                for part in path.split("."):
                    value = value[part]
            elif carrier == "chunks.db":
                value = terrain[path][slot]
            else:
                raise KeyError(carrier)
            if value != expected[slot]:
                problems.append(f"恢复值不一致: {slot}")
        except (KeyError, TypeError):
            problems.append(f"落盘目标不存在: {slot} -> {carrier}:{path}")
    return problems
