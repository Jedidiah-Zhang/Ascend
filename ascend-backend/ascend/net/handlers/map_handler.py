"""地图数据请求处理程序。

返回 ChunkData 的 JSON 可序列化表示。
"""

from ascend.log import get_logger

logger = get_logger(__name__)


def make_map_handlers(gen):
    """为给定的 WorldGenerator 创建地图相关的请求处理程序。

    Args:
        gen: WorldGenerator 实例。

    Returns:
        一个字典，将 request_type 字符串映射到处理函数。
    """

    def handle_get_chunks(msg: dict) -> dict:
        """处理 "get_chunks" 请求。

        预期 payload:
            chunks: [[cx1, cy1], [cx2, cy2], ...]
            可选的 force_fields: bool (默认 false) — 包含完整的气象数据

        Returns:
            响应字典: {type: "response", request_type: "get_chunks", payload: {chunks: [...]}}
        """
        payload = msg.get("payload", {})
        coords = payload.get("chunks", [])
        force_fields = payload.get("force_fields", False)

        if not coords:
            return {
                "type": "response",
                "request_type": "get_chunks",
                "payload": {"chunks": []},
            }

        logger.debug("get_chunks: 请求 %d 个块", len(coords))

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
            result_chunks.append(entry)

        logger.debug("get_chunks: 返回 %d 个块", len(result_chunks))
        return {
            "type": "response",
            "request_type": "get_chunks",
            "payload": {"chunks": result_chunks},
        }

    return {
        "get_chunks": handle_get_chunks,
    }
