"""chunk 气候基线查询（观察层用；独立文件避免扰动声明方程源码版本）。

任意坐标可查，不依赖 chunk 是否加载：大陆气候为 seed 确定的静态量，
越界返回大陆缓存的极地深海默认（与 ``ContinentData.get_chunk_climate``
一致）。供区域降水观测按域取阈值/强度校准输入。

线程语义：缓存由 tick 线程单写；跨线程只读场景走调用方加锁。
"""

from __future__ import annotations

from olam.generation.climate import ClimateZone, get_climate_template


class ChunkClimateLookup:
    """chunk 气候基线查询（纯派生 + 静态缓存；观察层用）。

    任意坐标可查，不依赖 chunk 是否加载：大陆气候为 seed 确定的静态量，
    越界返回大陆缓存的极地深海默认（与 ``ContinentData.get_chunk_climate``
    一致）。供区域降水观测按域取阈值/强度校准输入。

    线程语义：缓存由 tick 线程单写；跨线程只读场景走调用方加锁。
    """

    def __init__(self, continent) -> None:
        self._continent = continent
        self._cache: dict[tuple[int, int], tuple[float, float]] = {}

    def __repr__(self) -> str:
        return f"ChunkClimateLookup(cached={len(self._cache)})"

    def baseline(self, cx: int, cy: int) -> tuple[float, float]:
        """(年降雨量 mm/年, 基准降雨强度 mm/h)；任意坐标可查。"""
        key = (cx, cy)
        hit = self._cache.get(key)
        if hit is not None:
            return hit
        _, annual_rainfall, _, zone_int = (
            self._continent.get_chunk_climate(cx, cy)
        )
        template = get_climate_template(ClimateZone(zone_int))
        result = (float(annual_rainfall), float(template.mean_precip_intensity))
        self._cache[key] = result
        return result
