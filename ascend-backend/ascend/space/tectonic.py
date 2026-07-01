"""构造海拔 — 多尺度分形噪声 + 大陆蒙版。

简洁方案：
  1. 超低频 FBM → 大陆/海洋蒙版
  2. 中低频山脊噪声 → 连绵山脉
  3. 中高频 FBM → 地形细节
  4. Sigmoid 海岸过渡

用法:
    alt = tectonic_altitude(0, 0, 42)
    alts = tectonic_altitude_batch(0, 0, 200, 200, 42)
"""

import ctypes
import math
import subprocess
from dataclasses import dataclass
from pathlib import Path

from .noise import PerlinNoise


# ════════════════════════════════════════════════════════════════
# WorldParams
# ════════════════════════════════════════════════════════════════

@dataclass
class WorldParams:
    """世界生成参数。"""

    continent_scale: float = 500000.0    # 大陆波长 (~2500 chunks)
    ocean_ratio: float = 0.45            # 海洋比例
    mountain_scale: float = 30000.0      # 山脊间距 (tiles)
    mountain_height: float = 2500.0      # 山脊最大高度 (m)
    detail_scale: float = 800.0          # 丘陵起伏间距 (tiles)
    detail_height: float = 300.0         # 丘陵起伏幅度 (m)
    altitude_floor: float = -800.0       # 最深海沟
    altitude_ceil: float = 6000.0        # 最高山峰
    coastal_sharpness: float = 500.0     # 海岸过渡锐度 (越小越锐)

    def __repr__(self) -> str:
        return (
            f"WorldParams(ocean={self.ocean_ratio:.0%}, "
            f"mountain={self.mountain_height:.0f}m)"
        )


PRESETS: dict[str, WorldParams] = {
    "earthlike": WorldParams(),
    "pangaea": WorldParams(ocean_ratio=0.25, continent_scale=1200000.0),
    "archipelago": WorldParams(ocean_ratio=0.75, continent_scale=400000.0,
                               mountain_scale=15000.0),
    "mountainous": WorldParams(mountain_height=4500.0, mountain_scale=20000.0),
    "flat": WorldParams(mountain_height=500.0, detail_height=150.0),
    "ocean_world": WorldParams(ocean_ratio=0.90),
}


# ════════════════════════════════════════════════════════════════
# 噪声组合
# ════════════════════════════════════════════════════════════════

def _fbm(x: float, y: float, noise: PerlinNoise,
         octaves: int = 4, freq: float = 1.0) -> float:
    """分形布朗运动。"""
    total = 0.0
    amp = 1.0
    total_amp = 0.0
    for _ in range(octaves):
        total += noise.sample(x * freq, y * freq) * amp
        total_amp += amp
        freq *= 2.0
        amp *= 0.5
    return total / total_amp


def _ridge(x: float, y: float, noise: PerlinNoise,
           octaves: int = 3, freq: float = 1.0) -> float:
    """山脊噪声: (1 - |fbm|)², 产生线性脊线。"""
    v = _fbm(x, y, noise, octaves, freq)
    r = 1.0 - abs(v)
    return r * r


# ════════════════════════════════════════════════════════════════
# 核心 API
# ════════════════════════════════════════════════════════════════

def tectonic_altitude(
    world_x: float, world_y: float, seed: int,
    *, params: WorldParams | None = None,
) -> float:
    """单点查询构造海拔。"""
    if params is None:
        params = PRESETS["earthlike"]

    n1 = PerlinNoise(seed)
    n2 = PerlinNoise(seed + 1000)
    n3 = PerlinNoise(seed + 2000)

    continent = _fbm(world_x, world_y, n1, 3, 1.0 / params.continent_scale)
    ridge = _ridge(world_x, world_y, n2, 3, 1.0 / params.mountain_scale)
    detail = _fbm(world_x, world_y, n3, 4, 1.0 / params.detail_scale)

    # 大陆蒙版值 [-1,1] → 映射到偏移后的 [-2500,2500]
    # sea_threshold 决定海陆分界
    sea_threshold = params.ocean_ratio * 2.0 - 1.0  # ocean_ratio=0.5→0, 0.3→-0.4
    base = (continent - sea_threshold) * 2500.0

    # 海岸 Sigmoid 过渡
    sea = 1.0 / (1.0 + math.exp(-base / params.coastal_sharpness))
    alt = base * sea - 500.0 * (1.0 - sea)

    # 山脉仅在陆地上
    land = 1.0 if alt > -200 else 0.0
    alt += ridge * params.mountain_height * land
    alt += detail * params.detail_height

    return max(params.altitude_floor, min(params.altitude_ceil, alt))


def tectonic_altitude_batch(
    world_x: int, world_y: int, w: int, h: int, seed: int,
    *, params: WorldParams | None = None,
) -> list[float]:
    """矩形区域批量查询。"""
    if w <= 0 or h <= 0:
        return []
    if params is None:
        params = PRESETS["earthlike"]

    n1 = PerlinNoise(seed)
    n2 = PerlinNoise(seed + 1000)
    n3 = PerlinNoise(seed + 2000)

    cf = 1.0 / params.continent_scale
    mf = 1.0 / params.mountain_scale
    df = 1.0 / params.detail_scale
    sh = params.coastal_sharpness
    mh = params.mountain_height
    dh = params.detail_height
    sea_thr = params.ocean_ratio * 2.0 - 1.0
    floor_ = params.altitude_floor
    ceil_ = params.altitude_ceil

    result: list[float] = []
    for ty in range(h):
        wy = world_y + ty
        for tx in range(w):
            wx = world_x + tx

            c = _fbm(wx, wy, n1, 3, cf)
            r = _ridge(wx, wy, n2, 3, mf)
            d = _fbm(wx, wy, n3, 4, df)

            base = (c - sea_thr) * 2500.0
            sea = 1.0 / (1.0 + math.exp(-base / sh))
            alt = base * sea - 500.0 * (1.0 - sea)
            land = 1.0 if alt > -200 else 0.0
            alt += r * mh * land + d * dh

            result.append(max(floor_, min(ceil_, alt)))

    return result


__all__ = [
    "WorldParams", "PRESETS",
    "tectonic_altitude", "tectonic_altitude_batch",
]
