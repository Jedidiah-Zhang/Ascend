"""地图预览处理程序 — 创建世界流程的地形预览（Issue #8）。

语义：调参步骤的只读查询（无副作用）——给定种子与大陆占比，
返回低分辨率地形缩略图（海拔场 + 实测陆地占比），前端按高度着色。

     map_preview {payload: {seed, land_ratio}} → {payload: {width,
                height, land_percent, elevation: [int 米] 行优先}}

预览走 ContinentGenerator.generate_preview：只算海拔 + 陆地掩码
（跳过气候/侵蚀/水文），采样分辨率 1000m（默认 100×60 网格），
秒级返回。地形噪声场与分辨率无关，预览为同一地形的粗采样缩略图
（海拔未经侵蚀，与最终世界略有偏差，仅作参考）。
"""

import math

from ascend.log import get_logger
from ascend.net.protocol import make_response
from ascend.save.manifest import SEED_MAX, SIZE_KM_MIN, SIZE_KM_MAX, LAND_RATIO_MAX
from ascend.space.continent import ContinentGenerator

logger = get_logger(__name__)


def _parse_preview_payload(msg: dict) -> tuple[int, float, float | None, float | None]:
    """解析并校验预览请求参数。

    参数范围与存档契约单源（manifest.SEED_MAX / LAND_RATIO_MAX /
    SIZE_KM_MIN / SIZE_KM_MAX）——预览与 save_create 校验一致。

    Returns:
        (seed, land_ratio, width_km, height_km)——尺寸缺省时为 None，
        由生成器默认参数兜底（100×60）。

    Raises:
        ValueError: 参数缺失或越界。
    """
    payload = msg.get("payload", {})
    if not isinstance(payload, dict):
        raise ValueError("payload 必须为对象")
    seed = int(payload.get("seed", 0) or 0)
    if not (1 <= seed <= SEED_MAX):
        raise ValueError(f"seed 越界: {seed}")
    land_ratio = float(payload.get("land_ratio", 0.55))
    if not math.isfinite(land_ratio) or not (0.0 < land_ratio <= LAND_RATIO_MAX):
        raise ValueError(f"land_ratio 越界: {land_ratio}")

    def _km(key: str) -> float | None:
        if key not in payload or payload[key] is None:
            return None
        value = float(payload[key])
        if not math.isfinite(value) or not (SIZE_KM_MIN <= value <= SIZE_KM_MAX):
            raise ValueError(f"{key} 越界: {value}")
        return value

    return seed, land_ratio, _km("width_km"), _km("height_km")


def make_preview_handlers():
    """创建地图预览请求处理程序。

    Returns:
        {request_type: handler} 映射。
    """

    def handle_map_preview(msg: dict) -> dict:
        """生成地形预览（种子 + 大陆占比 + 尺寸 → 海拔缩略图）。"""
        seed, land_ratio, width_km, height_km = _parse_preview_payload(msg)
        preview = ContinentGenerator(seed=seed).generate_preview(
            land_ratio, width_km, height_km)
        preview["seed"] = seed
        preview["land_ratio"] = land_ratio
        if width_km is not None:
            preview["width_km"] = width_km
        if height_km is not None:
            preview["height_km"] = height_km
        logger.debug(
            "地图预览完成: seed=%d land=%.2f size=%s",
            seed, land_ratio,
            f"{width_km}x{height_km}" if width_km else "默认",
        )
        return make_response("map_preview", preview)

    return {"map_preview": handle_map_preview}
