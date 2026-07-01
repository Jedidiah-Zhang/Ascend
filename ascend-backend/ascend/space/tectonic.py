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

import ctypes
import math
import subprocess
from dataclasses import dataclass
from pathlib import Path

# ════════════════════════════════════════════════════════════════
# C 扩展加载（仿 noise.py 模式）
# ════════════════════════════════════════════════════════════════

_HERE = Path(__file__).resolve().parent
_SO = _HERE / "_tectonic.so"
_C = _HERE / "_tectonic.c"

if not _SO.exists() or _C.stat().st_mtime > _SO.stat().st_mtime:
    subprocess.run(
        ["gcc", "-O3", "-shared", "-fPIC", "-o", str(_SO), str(_C), "-lm"],
        check=True, cwd=str(_HERE),
    )

_LIB = ctypes.CDLL(str(_SO))
_LIB.tectonic_altitude_batch_c.argtypes = [
    ctypes.POINTER(ctypes.c_double),  # cell_cx
    ctypes.POINTER(ctypes.c_double),  # cell_cy
    ctypes.POINTER(ctypes.c_double),  # cell_elev
    ctypes.POINTER(ctypes.c_double),  # cell_drx
    ctypes.POINTER(ctypes.c_double),  # cell_dry
    ctypes.c_int,                      # n_cells
    ctypes.c_int, ctypes.c_int,        # world_x, world_y
    ctypes.c_int, ctypes.c_int,        # w, h
    ctypes.c_double,                   # sigma2
    ctypes.c_double, ctypes.c_double,  # drift_scale, uplift_scale
    ctypes.c_double,                   # sea_level_offset
    ctypes.c_double, ctypes.c_double,  # altitude_floor, altitude_ceil
    ctypes.POINTER(ctypes.c_double),   # output
]
_LIB.tectonic_altitude_batch_c.restype = None

_USE_C_EXTENSION = _SO.exists()


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
    cell_size: int = 600
    jitter_range: float = 0.30
    uplift_scale: float = 400.0
    drift_scale: float = 1.5
    elevation_min: float = -1200.0
    elevation_max: float = 1500.0
    altitude_floor: float = -500.0
    altitude_ceil: float = 6000.0

    # 海平面校准（正=降低海平面=更多陆地，负=升高海平面=更多海洋）
    sea_level_offset: float = 0.0

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

    # 3×3 邻域 — 收集所有候选单元，高斯加权
    candidates: list[tuple[float, float, float, float, float]] = []
    for dy in (-2, -1, 0, 1, 2):
        for dx in (-2, -1, 0, 1, 2):
            nx, ny = gx + dx, gy + dy
            cx, cy = _cell_center(nx, ny, cell_size, params.jitter_range, seed)
            elev = _cell_elevation(nx, ny, seed, params)
            drx, dry = _cell_drift(nx, ny, seed, params)
            d2 = (world_x - cx) ** 2 + (world_y - cy) ** 2
            candidates.append((d2, cx, cy, elev, drx, dry))

    # 高斯加权混合所有 9 个单元（平滑过渡）
    sigma = cell_size * 0.55
    sigma2 = 2.0 * sigma * sigma

    weighted_sum = 0.0
    weight_sum = 0.0
    for d2, cx, cy, elev, drx, dry in candidates:
        w = math.exp(-d2 / sigma2)
        weighted_sum += elev * w
        weight_sum += w

    altitude = weighted_sum / (weight_sum + 1e-10)

    # 收敛隆起 — 仅对最重的 2 个单元（保持尖锐的山脉边界）
    candidates.sort(key=lambda c: math.exp(-c[0] / sigma2), reverse=True)
    c1, c2 = candidates[0], candidates[1]
    d2_1, cx1, cy1, e1, drx1, dry1 = c1
    d2_2, cx2, cy2, e2, drx2, dry2 = c2

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

        d1 = math.sqrt(d2_1)
        d2 = math.sqrt(d2_2)
        denom = d1 + d2
        if denom > 1e-10:
            t = d1 / denom
            boundary_t = math.exp(-((t - 0.5) ** 2) / 0.25)
        else:
            boundary_t = 1.0

        uplift = convergence * boundary_t * params.uplift_scale
        altitude += uplift

    altitude += params.sea_level_offset
    altitude = max(params.altitude_floor, min(params.altitude_ceil, altitude))
    return altitude


# ════════════════════════════════════════════════════════════════
# 批量查询
# ════════════════════════════════════════════════════════════════

def _tectonic_batch_c(
    world_x: int, world_y: int, w: int, h: int, seed: int, params: WorldParams,
) -> list[float]:
    """C 扩展批量计算（ctypes 调用 _tectonic.so）。"""
    cell_size = params.cell_size

    # 预计算单元属性（与 Python 路径相同）
    gx_min = int(math.floor(world_x / cell_size)) - 2
    gx_max = int(math.floor((world_x + w - 1) / cell_size)) + 2
    gy_min = int(math.floor(world_y / cell_size)) - 2
    gy_max = int(math.floor((world_y + h - 1) / cell_size)) + 2

    cell_cx: list[float] = []
    cell_cy: list[float] = []
    cell_elev: list[float] = []
    cell_drx: list[float] = []
    cell_dry: list[float] = []

    for gy in range(gy_min, gy_max + 1):
        for gx in range(gx_min, gx_max + 1):
            cx, cy = _cell_center(gx, gy, cell_size, params.jitter_range, seed)
            elev = _cell_elevation(gx, gy, seed, params)
            drx, dry = _cell_drift(gx, gy, seed, params)
            cell_cx.append(cx)
            cell_cy.append(cy)
            cell_elev.append(elev)
            cell_drx.append(drx)
            cell_dry.append(dry)

    n_cells = len(cell_cx)
    sigma2 = 2.0 * (cell_size * 0.55) ** 2

    # 转换为 ctypes 数组
    arr_cx = (ctypes.c_double * n_cells)(*cell_cx)
    arr_cy = (ctypes.c_double * n_cells)(*cell_cy)
    arr_elev = (ctypes.c_double * n_cells)(*cell_elev)
    arr_drx = (ctypes.c_double * n_cells)(*cell_drx)
    arr_dry = (ctypes.c_double * n_cells)(*cell_dry)

    n_out = w * h
    arr_out = (ctypes.c_double * n_out)()

    _LIB.tectonic_altitude_batch_c(
        arr_cx, arr_cy, arr_elev, arr_drx, arr_dry,
        n_cells, world_x, world_y, w, h,
        sigma2, params.drift_scale, params.uplift_scale,
        params.sea_level_offset,
        params.altitude_floor, params.altitude_ceil,
        arr_out,
    )

    return list(arr_out)


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

    # ── C 扩展快速路径 ──
    if _USE_C_EXTENSION:
        return _tectonic_batch_c(world_x, world_y, width, height, seed, params)

    # ── 纯 Python 路径 ──
    cell_size = params.cell_size

    # ── 预计算所有可能被引用的单元属性 ──
    gx_min = int(math.floor(world_x / cell_size)) - 2
    gx_max = int(math.floor((world_x + width - 1) / cell_size)) + 2
    gy_min = int(math.floor(world_y / cell_size)) - 2
    gy_max = int(math.floor((world_y + height - 1) / cell_size)) + 2

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

    sigma = params.cell_size * 0.55
    sigma2 = 2.0 * sigma * sigma

    for ty in range(height):
        wy = world_y + ty
        for tx in range(width):
            wx = world_x + tx

            # 高斯加权混合所有候选单元
            weighted_sum = 0.0
            weight_sum = 0.0
            best_w1 = -1.0; best_i1 = 0
            best_w2 = -1.0; best_i2 = 0
            for i, (cx, cy, elev, drx, dry) in enumerate(cell_data):
                d2 = (wx - cx) ** 2 + (wy - cy) ** 2
                w = math.exp(-d2 / sigma2)
                weighted_sum += elev * w
                weight_sum += w
                if w > best_w1:
                    best_w2, best_i2 = best_w1, best_i1
                    best_w1, best_i1 = w, i
                elif w > best_w2:
                    best_w2, best_i2 = w, i

            altitude = weighted_sum / (weight_sum + 1e-10)

            # 收敛隆起 — 仅最重的 2 个单元
            cx1, cy1, e1, drx1, dry1 = cell_data[best_i1]
            cx2, cy2, e2, drx2, dry2 = cell_data[best_i2]
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
                d2_1 = (wx - cx1)**2 + (wy - cy1)**2
                d2_2 = (wx - cx2)**2 + (wy - cy2)**2
                d1 = math.sqrt(d2_1)
                d2 = math.sqrt(d2_2)
                denom = d1 + d2
                t = d1 / (denom + 1e-10)
                boundary_t = math.exp(-((t - 0.5) ** 2) / 0.25)
                uplift = convergence * boundary_t * params.uplift_scale
                altitude += uplift

            altitude += params.sea_level_offset
            altitude = max(params.altitude_floor,
                         min(params.altitude_ceil, altitude))
            result.append(altitude)

    return result


# ════════════════════════════════════════════════════════════════
# 模块文档
# ════════════════════════════════════════════════════════════════

def calibrate_ocean_ratio(
    target_ratio: float = 0.5,
    seed: int = 0,
    params: WorldParams | None = None,
    sample_size: int = 2000,
) -> float:
    """计算使海陆比接近目标的 sea_level_offset。

    扫描大样本区域，找到海拔分位数，计算需要的偏移量。

    Args:
        target_ratio: 目标海洋占比 [0, 1]。
        seed: 世界种子。
        params: 世界参数。
        sample_size: 采样边长 (tiles)。

    Returns:
        推荐设置的 sea_level_offset 值。
    """
    if params is None:
        params = PRESETS["earthlike"]

    half = sample_size // 2
    samples = tectonic_altitude_batch(-half, -half, sample_size, sample_size, seed, params=params)
    sorted_alts = sorted(samples)
    idx = int(len(sorted_alts) * target_ratio)
    current_sea_level_alt = sorted_alts[idx]
    # 要使得 current_sea_level_alt 处的海拔变为 0
    return -current_sea_level_alt


__all__ = [
    "WorldParams",
    "PRESETS",
    "tectonic_altitude",
    "tectonic_altitude_batch",
    "calibrate_ocean_ratio",
]
