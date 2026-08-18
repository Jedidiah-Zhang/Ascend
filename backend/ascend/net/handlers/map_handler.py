"""地图数据请求处理程序。

返回 ChunkData 的 JSON 可序列化表示。
"""

import base64
from concurrent.futures import ThreadPoolExecutor, as_completed

from ascend.config import MAX_CHUNK_QUERY, TILE_WORKERS
from ascend.log import get_logger
from ascend.net.protocol import make_response
from ascend.net.handlers import parse_coord

logger = get_logger(__name__)

# 模块级持久化线程池（P2-04：复用而非每请求新建；线程惰性创建，
# 仅在 submit 时才派生，固定 max_workers 无实际开销）
_TILE_POOL = ThreadPoolExecutor(
    max_workers=TILE_WORKERS, thread_name_prefix="tile-gen"
)


def make_map_handlers(gen, tile_gen=None, chunk_store=None,
                      weather_engine=None):
    """为给定的 WorldGenerator 创建地图相关的请求处理程序。

    Args:
        gen: WorldGenerator 实例。
        tile_gen: TileGenerator 实例（可选），提供时支持 include_tiles。
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
        """处理 "get_chunks" 请求。

        输入防护：payload/chunks 类型校验，逐坐标校验，
        畸形坐标跳过不毁整批（与 weather_handler 一致）。
        """
        payload = msg.get("payload", {})
        coords = payload.get("chunks", []) if isinstance(payload, dict) else []
        force_fields = payload.get("force_fields", False) if isinstance(payload, dict) else False
        include_tiles = payload.get("include_tiles", False) if isinstance(payload, dict) else False

        if not isinstance(coords, list):
            logger.warning("get_chunks: chunks 非列表（%s），忽略", type(coords).__name__)
            coords = []

        coord_tuples = []
        for c in coords:
            parsed = parse_coord(c)
            if parsed is None:
                logger.warning("get_chunks: 非法坐标 %r，跳过", c)
                continue
            coord_tuples.append(parsed)

        # 请求量上限：chunk 生成（含 tile 层）同步跑在游戏线程，
        # 无上限的单条消息可冻结引擎数秒（与 weather_handler 的
        # MAX_WEATHER_QUERY_CHUNKS 同理）。
        if len(coord_tuples) > MAX_CHUNK_QUERY:
            logger.warning(
                "get_chunks: 请求 %d 个 chunk 超过上限 %d，截断",
                len(coord_tuples), MAX_CHUNK_QUERY,
            )
            coord_tuples = coord_tuples[:MAX_CHUNK_QUERY]

        if not coord_tuples:
            return make_response(
                    "get_chunks",
                    {"chunks": []},
                )

        logger.debug("get_chunks: 请求 %d 个块 (include_tiles=%s)", len(coord_tuples), include_tiles)

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
                    chunk.restore_tiles(saved_grid)
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
                futures = [
                    _TILE_POOL.submit(_generate_tiles, c) for c in tiles_needed
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
                # tile 数据二进制化：单字段 base64（版本头+uint16 LE 地形
                # +float32 LE 高程/坡度，见 TileGrid.to_bytes），比 JSON 数组
                # 省约 55% 流量；前端 Marshalls.base64_to_raw 直接解码
                grid = c.tile_grid
                entry["tiles_b64"] = (
                    base64.b64encode(grid.to_bytes()).decode("ascii")
                    if grid is not None else ""
                )
            result_chunks.append(entry)

        logger.debug("get_chunks: 返回 %d 个块 (缓存 %d, 新生成 %d)",
                     len(result_chunks), len(coord_tuples) - len(missing), len(missing))
        # include_tiles 回显：前端据此区分"字段版/完整版"响应，
        # 不再依赖 terrain 数组长度等数据形状启发式
        return make_response(
            "get_chunks",
            {"chunks": result_chunks, "include_tiles": include_tiles},
        )

    return {
        "get_chunks": handle_get_chunks,
    }
