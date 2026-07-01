"""Voronoi 构造模拟 — 板块漂移 + 边界隆起/沉降。

核心思路：
  世界被随机撒点划分为不规则 Voronoi 单元（板块）。
  每个板块有：基础海拔、漂移向量。

  查询点 P 时：
    1. 空间哈希定位 → 搜邻域找最近板块 A 和次近板块 B
    2. 基础海拔 = A 的海拔（板块内部平坦 + 微起伏噪声）
    3. 边界检测 = P 接近 A-B 中线 → smoothstep 过渡
    4. drift_A · drift_B → 碰撞(+隆起) 或 分离(-沉降)
    5. 板块内部叠加 FBM 噪声 → 丘陵

特性：
  - 板块形状 = 不规则多边形（随机种子 Voronoi）
  - 碰撞边界 = 连贯山脉（离海岸线远的内陆）
  - 分离边界 = 裂谷/海岭
  - 板块内部 = 微起伏丘陵，不平坦
  - ocean_ratio 滑块直接控制海陆比例

用法:
    from ascend.space.tectonic import tectonic_altitude, WorldParams

    alt = tectonic_altitude(0, 0, 42)
    alts = tectonic_altitude_batch(0, 0, 200, 200, 42)
"""

import math
import random as _random
from dataclasses import dataclass, field

from .noise import PerlinNoise


# ════════════════════════════════════════════════════════════════
# WorldParams
# ════════════════════════════════════════════════════════════════

@dataclass
class WorldParams:
    """世界生成参数。

    Args:
        seed_spacing: 板块种子平均间距 (m)。越小→板块越多→地形越碎。
        ocean_ratio: 目标海洋比例 [0-1]。默认 0.70。
        uplift_scale: 碰撞边界最大隆起量 (m)。叠加在板块基础海拔之上。
        subsidence_scale: 分离边界最大沉降量 (m)。
        drift_scale: 板块漂移速度量级，影响碰撞/分离强度。
        ocean_depth_typical: 海洋板块典型深度 (m)。
        ocean_depth_range: 海洋深度变化范围 (m)。
        land_elevation_typical: 大陆板块典型海拔 (m)。
        land_elevation_range: 大陆海拔变化范围 (m)。
        altitude_floor: 世界最低海拔 (m)。
        altitude_ceil: 世界最高海拔 (m)。
        boundary_width: 板块边界过渡带宽度 (m)。越大越平滑。
        interior_roughness: 板块内部微起伏幅度 (m)。
    """

    seed_spacing: float = 8000.0     # 板块间距 ~8km
    ocean_ratio: float = 0.70         # 70% 海洋

    # 碰撞/分离
    uplift_scale: float = 3000.0      # 碰撞隆起
    subsidence_scale: float = 1000.0  # 分离沉降
    drift_scale: float = 1500.0       # 漂移速度

    # 板块海拔分布（bimodal: 海洋深 + 大陆低）
    ocean_depth_typical: float = -3500.0   # 典型洋底深度
    ocean_depth_range: float = 2000.0      # 洋底变化 ±2000m
    land_elevation_typical: float = 400.0  # 典型陆地海拔
    land_elevation_range: float = 600.0    # 陆地变化 ±600m

    # 绝对边界
    altitude_floor: float = -8000.0   # 最深海沟
    altitude_ceil: float = 8000.0     # 最高山峰（极少数）

    # 细节
    boundary_width: float = 300.0     # 边界过渡带
    interior_roughness: float = 150.0  # 内部起伏

    def __repr__(self) -> str:
        return (
            f"WorldParams(spacing={self.seed_spacing:.0f}m, "
            f"ocean={self.ocean_ratio:.0%}, "
            f"uplift={self.uplift_scale:.0f}m)"
        )


PRESETS: dict[str, WorldParams] = {
    "earthlike": WorldParams(),
    "pangaea": WorldParams(
        ocean_ratio=0.30, seed_spacing=12000.0,
    ),
    "archipelago": WorldParams(
        ocean_ratio=0.80, seed_spacing=4000.0,
        uplift_scale=1000.0,
    ),
    "mountainous": WorldParams(
        uplift_scale=4000.0, seed_spacing=6000.0,
        altitude_ceil=10000.0,
    ),
    "flat": WorldParams(
        uplift_scale=500.0, interior_roughness=50.0,
        subsidence_scale=200.0,
    ),
    "ocean_world": WorldParams(
        ocean_ratio=0.92, seed_spacing=5000.0,
    ),
}


# ════════════════════════════════════════════════════════════════
# 确定性伪随机
# ════════════════════════════════════════════════════════════════

def _hash2d(x: int, y: int, seed: int) -> int:
    """2D 确定性哈希 → [0, 2^31)。"""
    h = seed
    h = (h * 0x9E3779B9) & 0xFFFFFFFF
    h ^= (x * 0x85EBCA77) & 0xFFFFFFFF
    h ^= (y * 0xC2B2AE35) & 0xFFFFFFFF
    h = (h ^ (h >> 16)) & 0xFFFFFFFF
    h = (h * 0x85EBCA6B) & 0xFFFFFFFF
    h = (h ^ (h >> 13)) & 0xFFFFFFFF
    return h & 0x7FFFFFFF


def _hash_to_float(x: int, y: int, seed: int) -> float:
    """确定性哈希 → [0, 1)。"""
    return _hash2d(x, y, seed) / 2147483648.0


def _hash_to_range(x: int, y: int, seed: int, lo: float, hi: float) -> float:
    """确定性哈希 → [lo, hi)。"""
    return lo + _hash_to_float(x, y, seed) * (hi - lo)


# ════════════════════════════════════════════════════════════════
# 板块种子：每个 bin 内有 0-1 个种子，确定性
# ════════════════════════════════════════════════════════════════

# 空间哈希 bin 大小 = seed_spacing 的一半
# 这样每个板块占据 ~2×2 bin，5×5 搜索覆盖足够多的种子


@dataclass
class _PlateSeed:
    """一个板块种子。"""
    x: float          # 世界坐标 X
    y: float          # 世界坐标 Y
    elevation: float  # 基础海拔 (m)
    drift_x: float    # 漂移向量分量
    drift_y: float


def _collect_seeds(
    wx: float, wy: float, seed: int, params: WorldParams,
) -> list[_PlateSeed]:
    """收集查询点附近的板块种子（5×5 bin 邻域）。

    Args:
        wx, wy: 世界坐标。
        seed: 世界种子。
        params: 世界参数。

    Returns:
        邻域内所有板块种子列表。
    """
    bin_size = params.seed_spacing * 0.5
    cx = int(math.floor(wx / bin_size))
    cy = int(math.floor(wy / bin_size))

    seeds: list[_PlateSeed] = []
    for dx in range(-2, 3):
        for dy in range(-2, 3):
            bx = cx + dx
            by = cy + dy
            seeds.extend(_seeds_in_bin(bx, by, seed, params))

    return seeds


def _seeds_in_bin(
    bx: int, by: int, seed: int, params: WorldParams,
) -> list[_PlateSeed]:
    """返回一个 bin 内的板块种子（0 或 1 个）。

    用哈希判断这个 bin 是否有种子，以及种子的精确位置和属性。
    """
    # 是否有种子：hash[0,1) > 0.3 → 大多数 bin 有种子
    has_seed = _hash_to_float(bx, by, seed + 10000) > 0.3
    if not has_seed:
        return []

    bin_size = params.seed_spacing * 0.5

    # 种子在 bin 内的随机位置
    jx = _hash_to_float(bx, by, seed + 20000) * bin_size
    jy = _hash_to_float(bx, by, seed + 30000) * bin_size

    world_x = bx * bin_size + jx
    world_y = by * bin_size + jy

    # 板块类型：ocean_ratio 的概率为海洋板块
    is_ocean = _hash_to_float(bx, by, seed + 40000) < params.ocean_ratio

    # 海拔分布 — bimodal，贴合现实
    # 用平方映射使分布向典型值聚集（而非均匀）
    t = _hash_to_float(bx, by, seed + 41000)
    t_shaped = 2.0 * t - 1.0          # [-1, 1]
    t_shaped = t_shaped * t_shaped * t_shaped  # 立方，向 0 聚集
    t_shaped = t_shaped * 0.5 + 0.5   # [0, 1] 向 0.5 聚集

    if is_ocean:
        # 海洋板块：集中在 deep ocean
        half = params.ocean_depth_range
        elevation = params.ocean_depth_typical + (t_shaped - 0.5) * 2.0 * half
        # 限制不浅于 -200m
        if elevation > -200.0:
            elevation = -200.0
    else:
        # 大陆板块：集中在低海拔平原
        half = params.land_elevation_range
        elevation = params.land_elevation_typical + (t_shaped - 0.5) * 2.0 * half

    # 漂移向量（角度随机，量级 × drift_scale）
    angle = _hash_to_float(bx, by, seed + 50000) * 2.0 * math.pi
    mag = (_hash_to_float(bx, by, seed + 60000) * 0.5 + 0.5) * params.drift_scale
    drift_x = math.cos(angle) * mag
    drift_y = math.sin(angle) * mag

    return [_PlateSeed(x=world_x, y=world_y, elevation=elevation,
                        drift_x=drift_x, drift_y=drift_y)]


# ════════════════════════════════════════════════════════════════
# 海拔计算
# ════════════════════════════════════════════════════════════════

def _compute_boundary_factor(
    dist_a: float, dist_b: float, params: WorldParams,
) -> float:
    """计算边界因子：0=板块内部, 1=正对边界中线。

    用 smoothstep 在板块边界带做平滑过渡。
    """
    if dist_a < 1.0:
        return 0.0
    # 相对距离差：dist_a 和 dist_b 越接近 → 越接近边界
    total = dist_a + dist_b
    if total < 1.0:
        return 1.0
    diff = abs(dist_a - dist_b)
    # smoothstep: diff ∈ [0, boundary_width] → [1, 0]
    if diff >= params.boundary_width:
        return 0.0
    t = diff / params.boundary_width
    # smoothstep: 1 - (3t² - 2t³)
    return 1.0 - t * t * (3.0 - 2.0 * t)


def _compute_convergence(a: _PlateSeed, b: _PlateSeed) -> float:
    """计算两个板块的碰撞/分离程度。

    正值 = 相向（碰撞），负值 = 相背（分离）。

    从 A 指向 B 的单位向量，与两板块 drift 的投影差。
    """
    dx = b.x - a.x
    dy = b.y - a.y
    dist = math.hypot(dx, dy)
    if dist < 1.0:
        return 0.0
    ux = dx / dist
    uy = dy / dist
    # A 向 B 方向的投影，B 向 -A 方向的投影
    proj_a = a.drift_x * ux + a.drift_y * uy
    proj_b = -(b.drift_x * ux + b.drift_y * uy)
    return proj_a + proj_b  # 正值 = 碰撞


# ════════════════════════════════════════════════════════════════
# 核心 API
# ════════════════════════════════════════════════════════════════

def tectonic_altitude(
    world_x: float, world_y: float, seed: int,
    *, params: WorldParams | None = None,
) -> float:
    """查询任意世界坐标的构造海拔。

    Args:
        world_x: 世界 X 坐标 (tile 空间, 1 tile = 1m)。
        world_y: 世界 Y 坐标 (tile 空间, 1 tile = 1m)。
        seed: 世界种子。
        params: 世界参数，默认 earthlike。

    Returns:
        海拔 (m)。
    """
    if params is None:
        params = PRESETS["earthlike"]

    seeds = _collect_seeds(world_x, world_y, seed, params)

    if not seeds:
        # 极端情况：无种子 → 深海
        return params.altitude_floor

    # 找到最近的两个种子
    best_a: _PlateSeed | None = None
    best_b: _PlateSeed | None = None
    dist_a = float("inf")
    dist_b = float("inf")

    for s in seeds:
        d = math.hypot(world_x - s.x, world_y - s.y)
        if d < dist_a:
            dist_b = dist_a
            best_b = best_a
            dist_a = d
            best_a = s
        elif d < dist_b:
            dist_b = d
            best_b = s

    if best_a is None:
        return params.altitude_floor

    # 板块内部海拔 — 距离加权混合（避免边界处突变）
    if best_b is not None and dist_b < float("inf"):
        eps = 0.01  # 防除零
        w_a = 1.0 / (dist_a + eps)
        w_b = 1.0 / (dist_b + eps)
        total_w = w_a + w_b
        base_elevation = (w_a * best_a.elevation + w_b * best_b.elevation) / total_w

        # 边界因子（距离越接近 → 越在边界上）
        boundary_factor = _compute_boundary_factor(dist_a, dist_b, params)
        convergence = _compute_convergence(best_a, best_b)
    else:
        base_elevation = best_a.elevation
        boundary_factor = 0.0
        convergence = 0.0

    # 边界处叠加隆起或沉降
    # convergence 归一化到 [-1, 1] 量级
    norm_conv = convergence / max(1.0, params.drift_scale)
    if norm_conv > 0:
        boundary_effect = boundary_factor * norm_conv * params.uplift_scale
    else:
        boundary_effect = boundary_factor * norm_conv * params.subsidence_scale

    altitude = base_elevation + boundary_effect

    # 内部微起伏（板块内部不平坦）
    # 使用 PerlinNoise 保证平滑，波长 ~500m 避免 1m 步长陡变
    noise = PerlinNoise(seed + 70000)
    micro = noise.octave(
        world_x * 0.002, world_y * 0.002,  # 波长 ~500m
        octaves=3, frequency=1.0,
    )
    # 边界处减弱微起伏（边界效应已经占主导）
    internal_factor = 1.0 - boundary_factor * 0.7
    altitude += micro * params.interior_roughness * internal_factor

    return max(params.altitude_floor, min(params.altitude_ceil, altitude))


def tectonic_altitude_batch(
    world_x: int, world_y: int, w: int, h: int, seed: int,
    *, params: WorldParams | None = None,
) -> list[float]:
    """矩形区域批量查询海拔。

    Args:
        world_x: 区域左上角世界 X 坐标。
        world_y: 区域左上角世界 Y 坐标。
        w: 宽度 (tiles)。
        h: 高度 (tiles)。
        seed: 世界种子。
        params: 世界参数，默认 earthlike。

    Returns:
        长度为 w*h 的海拔列表，行优先。
    """
    if w <= 0 or h <= 0:
        return []

    if params is None:
        params = PRESETS["earthlike"]

    results: list[float] = []
    for ty in range(h):
        wy = world_y + ty
        for tx in range(w):
            wx = world_x + tx
            results.append(tectonic_altitude(wx, wy, seed, params=params))

    return results


__all__ = [
    "WorldParams", "PRESETS",
    "tectonic_altitude", "tectonic_altitude_batch",
]
