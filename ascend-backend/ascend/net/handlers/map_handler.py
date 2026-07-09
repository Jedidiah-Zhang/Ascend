"""地图数据请求处理程序。

返回 ChunkData 的 JSON 可序列化表示。
"""

from ascend.log import get_logger

logger = get_logger(__name__)


def make_map_handlers(gen, tile_gen=None, birth_chunk=None):
    """为给定的 WorldGenerator 创建地图相关的请求处理程序。

    Args:
        gen: WorldGenerator 实例。
        tile_gen: TileGenerator 实例（可选），提供时支持 include_tiles。
        birth_chunk: (cx, cy) 出生区块坐标（可选），会附带在响应中。

    Returns:
        一个字典，将 request_type 字符串映射到处理函数。
    """

    def handle_get_chunks(msg: dict) -> dict:
        """处理 "get_chunks" 请求。

        预期 payload:
            chunks: [[cx1, cy1], [cx2, cy2], ...]
            可选的 force_fields: bool (默认 false) — 包含完整的气象数据
            可选的 include_tiles: bool (默认 false) — 包含 terrain_type + elevation 数组

        Returns:
            响应字典: {type: "response", request_type: "get_chunks", payload: {chunks: [...]}}
        """
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

        # 将 [[cx,cy], ...] 转换为 [(cx,cy), ...]
        coord_tuples = [(c[0], c[1]) for c in coords]

        # 批量生成
        chunks = gen.generate_parallel(coord_tuples, max_workers=8)

        result_chunks = []
        for c in chunks:
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
                    "rainfall": round(c.annual_baseline.rainfall, 1),
                })
            if include_tiles and tile_gen is not None:
                if not c.has_tiles:
                    grid = tile_gen.generate_chunk_for(c)
                    c.generate_tiles(grid)
                else:
                    grid = c.tile_grid
                entry["terrain"] = grid.to_list()
                entry["elevation"] = grid.to_elevation_list()
                entry["slope"] = grid.to_slope_list()
            result_chunks.append(entry)

        logger.debug("get_chunks: 返回 %d 个块", len(result_chunks))
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
