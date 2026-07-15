"""地图数据请求处理程序。

返回 ChunkData 的 JSON 可序列化表示。
"""

from concurrent.futures import ThreadPoolExecutor, as_completed

from ascend.config import TILE_WORKERS
from ascend.log import get_logger

logger = get_logger(__name__)


def make_map_handlers(gen, tile_gen=None, birth_chunk=None, chunk_store=None,
                      weather_engine=None):
    """为给定的 WorldGenerator 创建地图相关的请求处理程序。

    Args:
        gen: WorldGenerator 实例。
        tile_gen: TileGenerator 实例（可选），提供时支持 include_tiles。
        birth_chunk: (cx, cy) 出生区块坐标（可选），会附带在响应中。
        chunk_store: ChunkStore 实例，LRU 缓存 + SQLite 持久化。
        weather_engine: WeatherEngine 实例（可选），动态生成 chunk 时自动注册天气。

    Returns:
        一个字典，将 request_type 字符串映射到处理函数。
    """

    def _generate_tiles(chunk):
        """在独立线程中生成 chunk 的 TileGrid（原子操作）。"""
        if chunk.has_tiles:
            return
        grid = tile_gen.generate_chunk_for(chunk)
        chunk.generate_tiles(grid)

    def handle_get_chunks(msg: dict) -> dict:
        """处理 "get_chunks" 请求。"""
        payload = msg.get("payload", {})
        coords = payload.get("chunks", [])
        force_fields = payload.get("force_fields", False)
        include_tiles = payload.get("include_tiles", False)

        if not coords:
            return {
                "type": "response",
                "request_type": "get_chunks",
                "payload": {"chunks": []},
            }

        logger.debug("get_chunks: 请求 %d 个块 (include_tiles=%s)", len(coords), include_tiles)

        coord_tuples = [(c[0], c[1]) for c in coords]

        coord_to_chunk: dict[tuple[int, int], object] = {}
        missing: list[tuple[int, int]] = []

        for coord in coord_tuples:
            if chunk_store is not None:
                chunk = chunk_store.get(*coord)
                if chunk is not None:
                    coord_to_chunk[coord] = chunk
                    continue

                saved_grid = chunk_store.load_tiles(*coord)
                if saved_grid is not None:
                    chunk = gen.generate_chunk(*coord)
                    chunk.generate_tiles(saved_grid)
                    coord_to_chunk[coord] = chunk
                    chunk_store.put(chunk)
                    if weather_engine is not None:
                        weather_engine.register_chunk(
                            chunk.cx, chunk.cy, chunk.annual_baseline,
                            chunk.climate_zone, chunk.sea_level_temp,
                        )
                    continue

            missing.append(coord)

        if missing:
            new_chunks = gen.generate_parallel(missing, max_workers=8)
            for c in new_chunks:
                coord_to_chunk[(c.cx, c.cy)] = c
                if chunk_store is not None:
                    chunk_store.put(c)
                if weather_engine is not None:
                    weather_engine.register_chunk(
                        c.cx, c.cy, c.annual_baseline,
                        c.climate_zone, c.sea_level_temp,
                    )

        ordered = [coord_to_chunk[c] for c in coord_tuples]

        if include_tiles and tile_gen is not None:
            tiles_needed = [c for c in ordered if not c.has_tiles]
            if tiles_needed:
                n_workers = min(TILE_WORKERS, len(tiles_needed))
                with ThreadPoolExecutor(max_workers=n_workers) as pool:
                    futures = [
                        pool.submit(_generate_tiles, c) for c in tiles_needed
                    ]
                    for future in as_completed(futures):
                        future.result()

        result_chunks = []
        for c in ordered:
            entry = {
                "cx": c.cx,
                "cy": c.cy,
                "biome": int(c.biome),
                "climate": int(c.climate_zone),
                "passable": c.passable,
            }
            if force_fields:
                entry.update({
                    "altitude": round(c.annual_baseline.altitude, 1),
                    "temperature": round(c.annual_baseline.temperature, 1),
                    "humidity": round(c.annual_baseline.humidity, 1),
                    "rainfall": round(c.annual_baseline.rainfall, 1),
                })
            if include_tiles and tile_gen is not None:
                grid = c.tile_grid
                entry["terrain"] = grid.to_list()
                entry["elevation"] = grid.to_elevation_list()
                entry["slope"] = grid.to_slope_list()
            result_chunks.append(entry)

        logger.debug("get_chunks: 返回 %d 个块 (缓存 %d, 新生成 %d)",
                     len(result_chunks), len(coord_tuples) - len(missing), len(missing))
        response = {
            "type": "response",
            "request_type": "get_chunks",
            "payload": {"chunks": result_chunks},
        }
        if birth_chunk is not None:
            response["payload"]["birth_chunk"] = list(birth_chunk)
        return response

    return {
        "get_chunks": handle_get_chunks,
    }
