"""构造海拔生成 — Voronoi 抖动网格模拟板块构造。

纯函数，无内部状态，线程安全。

用法:
    from ascend.space.tectonic import tectonic_altitude, tectonic_altitude_batch

    # 单点
    alt = tectonic_altitude(world_x=500, world_y=300, seed=42)

    # 批量（200×200 chunk）
    alts = tectonic_altitude_batch(world_x=0, world_y=0, w=200, h=200, seed=42)

    # 自定义参数
    from ascend.space.tectonic import WorldParams, PRESETS
    alts = tectonic_altitude_batch(0, 0, 200, 200, 42, params=PRESETS["mountainous"])
"""

import math
from dataclasses import dataclass


# ════════════════════════════════════════════════════════════════
# WorldParams — 可调参数
# ════════════════════════════════════════════════════════════════

@dataclass
class WorldParams:
    """世界生成参数集。

    所有参数集中管理，方便预设风格和微调。

    Attributes:
        cell_size: 构造单元大小 (tiles)。越小越碎片化。
        jitter_range: 中心抖动幅度 [0, 0.5)。越大边界越不规则，
                      必须 < 0.5 以保证单元不重叠。
        uplift_scale: 板块碰撞隆起量 (m)。越大山脉越高。
        drift_scale: 板块漂移速度。越大碰撞越频繁。
        elevation_min: 板块基础海拔下限 (m)。
        elevation_max: 板块基础海拔上限 (m)。
        altitude_floor: 最终海拔钳制下限 (m)。
        altitude_ceil: 最终海拔钳制上限 (m)。
        erosion_droplets: 水力侵蚀水滴数。
        erosion_rate: 侵蚀力 [0, 1]。
        deposition_rate: 沉积率 [0, 1]。
        evaporation_rate: 蒸发率，越小河流越长。
        erosion_iterations: 侵蚀趟数。
        micro_noise_amplitude: 微地形噪声强度。
    """

    # 构造
    cell_size: int = 400
    jitter_range: float = 0.35
    uplift_scale: float = 2000.0
    drift_scale: float = 2.0
    elevation_min: float = -1500.0
    elevation_max: float = 3500.0
    altitude_floor: float = -500.0
    altitude_ceil: float = 8000.0

    # 侵蚀
    erosion_droplets: int = 5000
    erosion_rate: float = 0.3
    deposition_rate: float = 0.3
    evaporation_rate: float = 0.01
    erosion_iterations: int = 1

    # 地表
    micro_noise_amplitude: float = 0.15

    def __post_init__(self) -> None:
        """验证参数合法性。"""
        if self.cell_size < 1:
            raise ValueError(f"cell_size 必须 >= 1，实际为 {self.cell_size}")
        if not (0.0 <= self.jitter_range < 0.5):
            raise ValueError(
                f"jitter_range 必须在 [0, 0.5)，实际为 {self.jitter_range}"
            )
        if self.altitude_floor > self.altitude_ceil:
            raise ValueError(
                f"altitude_floor ({self.altitude_floor}) 不能大于 "
                f"altitude_ceil ({self.altitude_ceil})"
            )
        if self.elevation_min > self.elevation_max:
            raise ValueError(
                f"elevation_min ({self.elevation_min}) 不能大于 "
                f"elevation_max ({self.elevation_max})"
            )

    def __repr__(self) -> str:
        return (
            f"WorldParams(cell={self.cell_size}, uplift={self.uplift_scale:.0f}, "
            f"drift={self.drift_scale:.1f})"
        )


# ── 预设风格 ─────────────────────────────────────────────────

PRESETS: dict[str, WorldParams] = {
    "earthlike": WorldParams(),

    "pangaea": WorldParams(
        cell_size=800,
        elevation_min=-500.0,
        elevation_max=4000.0,
        uplift_scale=2500.0,
    ),

    "archipelago": WorldParams(
        cell_size=250,
        elevation_min=-2000.0,
        elevation_max=1500.0,
        uplift_scale=1000.0,
        drift_scale=3.0,
    ),

    "mountainous": WorldParams(
        uplift_scale=4000.0,
        drift_scale=3.5,
        elevation_max=4000.0,
    ),

    "flat": WorldParams(
        uplift_scale=500.0,
        drift_scale=0.5,
        elevation_min=-800.0,
        elevation_max=2000.0,
    ),

    "canyon": WorldParams(
        erosion_rate=0.7,
        erosion_droplets=15000,
        evaporation_rate=0.005,
        erosion_iterations=3,
    ),

    "ocean_world": WorldParams(
        elevation_min=-3000.0,
        elevation_max=500.0,
        cell_size=300,
        drift_scale=2.5,
    ),
}


# ════════════════════════════════════════════════════════════════
# 确定性哈希
# ════════════════════════════════════════════════════════════════

def _hash_int(gx: int, gy: int, seed: int) -> int:
    """确定性伪随机整数 — 纯整数混合（无外部依赖）。

    Args:
        gx, gy: 网格单元坐标。
        seed: 世界种子。

    Returns:
        32 位伪随机整数。
    """
    h = seed ^ (gx * 374761393 + gy * 668265263)
    h = (h ^ (h >> 13)) * 1274126177
    h = h ^ (h >> 16)
    return h & 0x7FFFFFFF


def _hash_float(
    gx: int, gy: int, seed: int, lo: float, hi: float
) -> float:
    """确定性伪随机浮点数，范围 [lo, hi]。

    Args:
        gx, gy: 网格单元坐标。
        seed: 世界种子。
        lo: 下限。
        hi: 上限。

    Returns:
        [lo, hi] 范围内的浮点数。
    """
    h = _hash_int(gx, gy, seed)
    t = h / float(0x7FFFFFFF)  # [0, 1]
    return lo + t * (hi - lo)


# ════════════════════════════════════════════════════════════════
# 单元属性
# ════════════════════════════════════════════════════════════════

def _cell_center(
    gx: int, gy: int, cell_size: int, jitter_range: float, seed: int
) -> tuple[float, float]:
    """返回抖动后的单元中心坐标。

    Args:
        gx, gy: 网格坐标。
        cell_size: 单元边长。
        jitter_range: 抖动幅度（相对于 cell_size）。
        seed: 世界种子。

    Returns:
        (center_x, center_y) 世界 tile 坐标。
    """
    jx = _hash_float(gx, gy, seed + 1000, -jitter_range, jitter_range)
    jy = _hash_float(gx, gy, seed + 2000, -jitter_range, jitter_range)
    return (
        (gx + 0.5) * cell_size + jx * cell_size,
        (gy + 0.5) * cell_size + jy * cell_size,
    )


def _cell_elevation(gx: int, gy: int, seed: int, params: WorldParams) -> float:
    """返回单元的基础海拔。

    Args:
        gx, gy: 网格坐标。
        seed: 世界种子。
        params: 世界参数。

    Returns:
        基础海拔 (m)。
    """
    return _hash_float(
        gx, gy, seed + 3000,
        params.elevation_min, params.elevation_max,
    )


def _cell_drift(
    gx: int, gy: int, seed: int, params: WorldParams
) -> tuple[float, float]:
    """返回单元的板块漂移速度向量。

    Args:
        gx, gy: 网格坐标。
        seed: 世界种子。
        params: 世界参数。

    Returns:
        (drift_x, drift_y)。
    """
    dx = _hash_float(gx, gy, seed + 4000, -params.drift_scale, params.drift_scale)
    dy = _hash_float(gx, gy, seed + 5000, -params.drift_scale, params.drift_scale)
    return (dx, dy)


# ════════════════════════════════════════════════════════════════
# 单点查询
# ════════════════════════════════════════════════════════════════

def tectonic_altitude(
    world_x: float,
    world_y: float,
    seed: int,
    *,
    params: WorldParams | None = None,
) -> float:
    """在任意世界坐标处查询构造海拔。

    纯函数，确定性 — 相同 (world_x, world_y, seed, params) 永远返回相同结果。

    Args:
        world_x: 世界 tile X 坐标。
        world_y: 世界 tile Y 坐标。
        seed: 世界种子。
        params: 生成参数，默认使用 "earthlike"。

    Returns:
        海拔 (m)，范围 [altitude_floor, altitude_ceil]。
    """
    if params is None:
        params = PRESETS["earthlike"]

    cell_size = params.cell_size

    # 找到包含该点的网格单元
    gx = int(math.floor(world_x / cell_size))
    gy = int(math.floor(world_y / cell_size))

    # 3×3 邻域 — 收集所有候选单元
    candidates: list[tuple[float, float, float, float, float]] = []
    for dy in (-1, 0, 1):
        for dx in (-1, 0, 1):
            nx, ny = gx + dx, gy + dy
            cx, cy = _cell_center(nx, ny, cell_size, params.jitter_range, seed)
            elev = _cell_elevation(nx, ny, seed, params)
            drx, dry = _cell_drift(nx, ny, seed, params)
            d2 = (world_x - cx) ** 2 + (world_y - cy) ** 2
            candidates.append((d2, cx, cy, elev, drx, dry))

    # 手动追踪最近 4 个
    t1 = [float("inf"), 0, 0, 0, 0, 0]
    t2 = [float("inf"), 0, 0, 0, 0, 0]
    t3 = [float("inf"), 0, 0, 0, 0, 0]
    t4 = [float("inf"), 0, 0, 0, 0, 0]
    for (d2, cx, cy, elev, drx, dry) in candidates:
        if d2 < t1[0]:
            t4, t3, t2, t1 = t3, t2, t1, [d2, cx, cy, elev, drx, dry]
        elif d2 < t2[0]:
            t4, t3, t2 = t3, t2, [d2, cx, cy, elev, drx, dry]
        elif d2 < t3[0]:
            t4, t3 = t3, [d2, cx, cy, elev, drx, dry]
        elif d2 < t4[0]:
            t4 = [d2, cx, cy, elev, drx, dry]

    # IDW 混合基础海拔（反距离平方加权）
    weighted_sum = 0.0
    weight_sum = 0.0
    epsilon = 1e-3
    for t in (t1, t2, t3, t4):
        dist = math.sqrt(t[0])
        elev = t[3]
        w = 1.0 / (dist * dist + epsilon)
        weighted_sum += elev * w
        weight_sum += w

    altitude = weighted_sum / weight_sum

    # 收敛隆起 — 对最近 2 个单元之间计算
    d1 = math.sqrt(t1[0])
    d2 = math.sqrt(t2[0])
    denom = d1 + d2
    cx1, cy1, e1, drx1, dry1 = t1[1:]
    cx2, cy2, e2, drx2, dry2 = t2[1:]

    abx = cx2 - cx1
    aby = cy2 - cy1
    dist_ab = math.sqrt(abx * abx + aby * aby)

    if dist_ab > 1e-6:
        nx = abx / dist_ab
        ny = aby / dist_ab
        rel_vx = drx2 - drx1
        rel_vy = dry2 - dry1
        v_proj = rel_vx * nx + rel_vy * ny
        convergence = max(0.0, -v_proj) / (2.0 * params.drift_scale + 1e-6)
        convergence = min(1.0, convergence)

        if denom > 1e-10:
            t = d1 / denom
            boundary_t = 1.0 - abs(t - 0.5) * 2.0
        else:
            boundary_t = 1.0

        uplift = convergence * boundary_t * params.uplift_scale
        altitude += uplift

    altitude = max(params.altitude_floor, min(params.altitude_ceil, altitude))
    return altitude


# ════════════════════════════════════════════════════════════════
# 批量查询
# ════════════════════════════════════════════════════════════════

def tectonic_altitude_batch(
    world_x: int,
    world_y: int,
    w: int,
    h: int,
    seed: int,
    *,
    params: WorldParams | None = None,
) -> list[float]:
    """在矩形网格上批量查询构造海拔。

    对整片区域预计算单元属性，避免重复哈希，
    然后逐 tile 执行与单点查询相同的算法。

    Args:
        world_x: 区域左上角 tile X。
        world_y: 区域左上角 tile Y。
        w: 宽度 (tiles)。
        h: 高度 (tiles)。
        seed: 世界种子。
        params: 生成参数，默认使用 "earthlike"。

    Returns:
        长度为 w*h 的浮点列表，按行排列。
    """
    width = int(w)
    height = int(h)
    if width <= 0 or height <= 0:
        return []

    if params is None:
        params = PRESETS["earthlike"]

    cell_size = params.cell_size

    # ── 预计算所有可能被引用的单元属性 ──
    gx_min = int(math.floor(world_x / cell_size)) - 1
    gx_max = int(math.floor((world_x + width - 1) / cell_size)) + 1
    gy_min = int(math.floor(world_y / cell_size)) - 1
    gy_max = int(math.floor((world_y + height - 1) / cell_size)) + 1

    cells: dict[tuple[int, int], tuple[float, float, float, float, float]] = {}
    for gy in range(gy_min, gy_max + 1):
        for gx in range(gx_min, gx_max + 1):
            cx, cy = _cell_center(gx, gy, cell_size, params.jitter_range, seed)
            elev = _cell_elevation(gx, gy, seed, params)
            drx, dry = _cell_drift(gx, gy, seed, params)
            cells[(gx, gy)] = (cx, cy, elev, drx, dry)

    # ── 逐 tile 计算 ──
    result: list[float] = []
    cell_data = list(cells.values())
    n_cells = len(cell_data)

    for ty in range(height):
        wy = world_y + ty
        for tx in range(width):
            wx = world_x + tx

            # 手动追踪最近 4 个（O(12) vs 排序 O(12 log 12)，避免 40K 次排序）
            t1 = [float("inf"), 0, 0, 0, 0, 0]  # (d2, cx, cy, elev, drx, dry)
            t2 = [float("inf"), 0, 0, 0, 0, 0]
            t3 = [float("inf"), 0, 0, 0, 0, 0]
            t4 = [float("inf"), 0, 0, 0, 0, 0]
            for (cx, cy, elev, drx, dry) in cell_data:
                d2 = (wx - cx) ** 2 + (wy - cy) ** 2
                if d2 < t1[0]:
                    t4, t3, t2, t1 = t3, t2, t1, [d2, cx, cy, elev, drx, dry]
                elif d2 < t2[0]:
                    t4, t3, t2 = t3, t2, [d2, cx, cy, elev, drx, dry]
                elif d2 < t3[0]:
                    t4, t3 = t3, [d2, cx, cy, elev, drx, dry]
                elif d2 < t4[0]:
                    t4 = [d2, cx, cy, elev, drx, dry]
            top4 = (t1, t2, t3, t4)

            # IDW 混合（反距离平方加权）
            weighted_sum = 0.0
            weight_sum = 0.0
            epsilon = 1e-3
            for t in top4:
                d2 = t[0]
                elev = t[3]
                dist = math.sqrt(d2)
                w = 1.0 / (dist * dist + epsilon)
                weighted_sum += elev * w
                weight_sum += w

            altitude = weighted_sum / weight_sum

            # 收敛隆起 — 最近 2 个单元之间
            t1, t2, t3, t4 = top4
            d1 = math.sqrt(t1[0])
            d2 = math.sqrt(t2[0])
            denom = d1 + d2
            d2_0, cx1, cy1, e1, drx1, dry1 = t1
            d2_1, cx2, cy2, e2, drx2, dry2 = t2

            abx = cx2 - cx1
            aby = cy2 - cy1
            dist_ab = math.sqrt(abx * abx + aby * aby)
            if dist_ab > 1e-6:
                nx = abx / dist_ab
                ny = aby / dist_ab
                rel_vx = drx2 - drx1
                rel_vy = dry2 - dry1
                v_proj = rel_vx * nx + rel_vy * ny
                convergence = max(0.0, -v_proj) / (2.0 * params.drift_scale + 1e-6)
                convergence = min(1.0, convergence)
                t = d1 / (denom + 1e-10)
                boundary_t = 1.0 - abs(t - 0.5) * 2.0
                uplift = convergence * boundary_t * params.uplift_scale
                altitude += uplift

            altitude = max(params.altitude_floor,
                         min(params.altitude_ceil, altitude))
            result.append(altitude)

    return result


# ════════════════════════════════════════════════════════════════
# 模块文档
# ════════════════════════════════════════════════════════════════

__all__ = [
    "WorldParams",
    "PRESETS",
    "tectonic_altitude",
    "tectonic_altitude_batch",
]
