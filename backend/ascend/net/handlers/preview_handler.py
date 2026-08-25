"""地图预览处理程序 — 创建世界流程的地形预览（Issue #8）。

语义：调参步骤的只读查询（无副作用）——给定种子与大陆占比，
返回低分辨率地形缩略图（海拔场 + 实测陆地占比），前端按高度着色。
可选 layers 请求气候图层（温度/降雨/气候带），一次响应携带全部。

     map_preview {payload: {seed, land_ratio, layers?}} → {payload: {
                width, height, land_percent, elevation: [int 米],
                temperature?: [int °C], rainfall?: [int mm/年],
                climate?: [int 0-7]} 行优先}

温度/降雨为海陆全域场：temperature 海域为海面温度（C 端纬度梯度场
clamp [-20, 38]，直减率仅作用于陆地，与生产管线 sea_temp 同源一致）；
rainfall 为噪声场全域值。climate 档位仅陆地有意义（海域未校准，前端
忽略海域）。

预览走 ContinentGenerator.generate_preview：只算海拔 + 陆地掩码，
layers 请求时补算气候（跳过侵蚀/水文），采样分辨率 1000m
（默认 100×60 网格），秒级返回。地形噪声场与分辨率无关，预览为
同一地形的粗采样缩略图（海拔未经侵蚀，与最终世界略有偏差，仅作
参考；气候值与最终世界一致）。
"""

import math
import random

from ascend.config import CONTINENT_LAND_RATIO
from ascend.log import get_logger
from ascend.net.protocol import make_response
from ascend.save.manifest import (
    LAND_RATIO_MAX, SEED_MAX, SIZE_KM_MAX, SIZE_KM_MIN,
    parse_seed, seed_to_hex,
)
from ascend.space.continent import ContinentGenerator

logger = get_logger(__name__)

# 可请求的气候预览图层
PREVIEW_LAYERS = ("temp", "rain", "climate")


def _parse_preview_payload(msg: dict) -> tuple[int, float, float | None, float | None, tuple[str, ...]]:
    """解析并校验预览请求参数。

    参数范围与存档契约单源（manifest.SEED_MAX / LAND_RATIO_MAX /
    SIZE_KM_MIN / SIZE_KM_MAX）——预览与 save_create 校验一致。
    seed 为协议层 hex 字符串（manifest.parse_seed 契约）；
    "" / "0" = 随机占位（本处随机定案并随响应回传）。

    Returns:
        (seed, land_ratio, width_km, height_km, layers)——尺寸缺省时为
        None，由生成器默认参数兜底（100×60）；layers 为去重后的
        合法图层名元组，缺省/为空 = 仅海拔。

    Raises:
        ValueError: 参数缺失或越界，或 layers 含非法图层名。
    """
    payload = msg.get("payload", {})
    if not isinstance(payload, dict):
        raise ValueError("payload 必须为对象")
    seed = parse_seed(payload.get("seed", ""))
    if seed == 0:
        # 随机占位：预览即定案（种子唯一随机源 = 后端，与命运织机
        # "随机性全部可追溯"一致），响应回传 hex 种子供创建复用。
        seed = random.randint(1, SEED_MAX)
    land_ratio = float(payload.get("land_ratio", CONTINENT_LAND_RATIO))
    if not math.isfinite(land_ratio) or not (0.0 < land_ratio <= LAND_RATIO_MAX):
        raise ValueError(f"land_ratio 越界: {land_ratio}")

    def _km(key: str) -> float | None:
        if key not in payload or payload[key] is None:
            return None
        value = float(payload[key])
        if not math.isfinite(value) or not (SIZE_KM_MIN <= value <= SIZE_KM_MAX):
            raise ValueError(f"{key} 越界: {value}")
        return value

    layers_raw = payload.get("layers", [])
    if not isinstance(layers_raw, list):
        raise ValueError("layers 必须为数组")
    layers = tuple(sorted({str(v) for v in layers_raw} - {""}))
    for name in layers:
        if name not in PREVIEW_LAYERS:
            raise ValueError(f"layers 含非法图层: {name}")

    return seed, land_ratio, _km("width_km"), _km("height_km"), layers


def make_preview_handlers():
    """创建地图预览请求处理程序。

    Returns:
        {request_type: handler} 映射。
    """

    def handle_map_preview(msg: dict) -> dict:
        """生成地形预览（种子 + 大陆占比 + 尺寸 + 可选气候图层）。"""
        seed, land_ratio, width_km, height_km, layers = _parse_preview_payload(msg)
        preview = ContinentGenerator(seed=seed).generate_preview(
            land_ratio, width_km, height_km, layers=layers)
        # 协议层 seed 一律 hex 字符串（Godot JSON 仅 int64，256-bit 直传丢失）
        preview["seed"] = seed_to_hex(seed)
        preview["land_ratio"] = land_ratio
        if width_km is not None:
            preview["width_km"] = width_km
        if height_km is not None:
            preview["height_km"] = height_km
        logger.debug(
            "地图预览完成: seed=%d land=%.2f size=%s layers=%s",
            seed, land_ratio,
            f"{width_km}x{height_km}" if width_km else "默认",
            ",".join(layers) if layers else "-",
        )
        return make_response("map_preview", preview)

    return {"map_preview": handle_map_preview}
