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
    ctypes.c_double, ctypes.c_double,  # faults_per_region, uplift_scale
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
        faults_per_region: 板块漂移速度。越大碰撞越频繁。
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
    # 大尺度（海陆框架）
    cell_size: int = 400000         # ~2000 chunks
    jitter_range: float = 0.25
    blend_sigma: float = 0.015      # 交界带宽度 (×cell_size, ~30 chunks)
    elevation_min: float = -3000.0  # 深海盆地
    elevation_max: float = 3000.0   # 高原台地
    # 中尺度（地形细节）
    detail_cell_size: int = 600     # ~3 chunks
    detail_jitter: float = 0.35
    detail_amplitude: float = 400.0 # 中尺度起伏幅度 (m)
    # 断层山脉
    uplift_scale: float = 2000.0
    faults_per_region: int = 5
    drift_scale: float = 1.5  # 兼容 C 扩展
    fault_length: float = 0.6
    fault_width: float = 0.02
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
            f"drift={self.faults_per_region:.1f})"
        )


# ── 预设风格 ─────────────────────────────────────────────────

PRESETS: dict[str, WorldParams] = {
    "earthlike": WorldParams(),

    "pangaea": WorldParams(
        cell_size=1000, elevation_min=-500.0, elevation_max=2500.0,
        uplift_scale=600.0, jitter_range=0.20,
    ),

    "archipelago": WorldParams(
        cell_size=350, elevation_min=-1800.0, elevation_max=1000.0,
        uplift_scale=300.0, faults_per_region=3, jitter_range=0.35,
        detail_cell_size=300,
    ),

    "mountainous": WorldParams(
        uplift_scale=2500.0, faults_per_region=8, elevation_max=3000.0,
        cell_size=300000, detail_cell_size=400,
    ),

    "flat": WorldParams(
        uplift_scale=100.0, faults_per_region=1, jitter_range=0.20,
        elevation_min=-600.0, elevation_max=1200.0, cell_size=600000,
        detail_amplitude=100.0,
    ),

    "canyon": WorldParams(
        erosion_rate=0.7, erosion_droplets=15000,
        evaporation_rate=0.005, erosion_iterations=3,
    ),

    "ocean_world": WorldParams(
        elevation_min=-2500.0, elevation_max=300.0, cell_size=400,
        faults_per_region=2.0, jitter_range=0.30,
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
    dx = _hash_float(gx, gy, seed + 4000, -params.faults_per_region, params.faults_per_region)
    dy = _hash_float(gx, gy, seed + 5000, -params.faults_per_region, params.faults_per_region)
    return (dx, dy)


# ════════════════════════════════════════════════════════════════
# 断层线系统 — 连绵山脉
# ════════════════════════════════════════════════════════════════

_FAULT_REGION_FACTOR = 2.0  # 断层区域 = cell_size × factor


def _region_faults(
    rx: int, ry: int, seed: int, params: WorldParams
) -> list[tuple[float, float, float, float, float]]:
    """生成区域内确定性断层线列表。

    Args:
        rx, ry: 断层区域坐标。
        seed: 世界种子。
        params: 世界参数。

    Returns:
        (fx, fy, angle, length, strength) 列表。
        fx, fy: 断层中点（世界 tile 坐标）。
        angle: 断层走向 (弧度)。
        length: 断层半长 (tiles)。
        strength: 隆起强度 (m)。
    """
    region_size = params.cell_size * _FAULT_REGION_FACTOR
    n = params.faults_per_region

    # 用 hash 决定该区域是否有断层（约 60% 区域有）
    has_faults = _hash_float(rx, ry, seed + 6000, 0, 1) < 0.85
    if not has_faults:
        return []

    faults = []
    for fi in range(n):
        # 断层中点（区域内随机位置）
        fx = (rx + _hash_float(rx, ry, seed + 7000 + fi * 10, -0.3, 0.3)) * region_size
        fy = (ry + _hash_float(rx, ry, seed + 8000 + fi * 10, -0.3, 0.3)) * region_size

        # 走向角度
        angle = _hash_float(rx, ry, seed + 9000 + fi * 10, 0, math.pi)

        # 长度
        halflen = _hash_float(rx, ry, seed + 10000 + fi * 10, 0.3, params.fault_length) * region_size

        # 强度
        strength = _hash_float(rx, ry, seed + 11000 + fi * 10, 0.3, 1.0) * params.uplift_scale

        faults.append((fx, fy, angle, halflen, strength))

    return faults


def _fault_uplift(
    world_x: float, world_y: float, seed: int, params: WorldParams
) -> float:
    """累加附近所有断层线的隆起贡献。

    每条断层是一个线段，沿线添加高斯剖面隆起：
      uplift = strength × exp(-d_perp² / (2 × width²))
    线段两端逐渐衰减。

    Args:
        world_x, world_y: 查询点。
        seed: 世界种子。
        params: 世界参数。

    Returns:
        总隆起量 (m)。
    """
    region_size = params.cell_size * _FAULT_REGION_FACTOR
    width = params.fault_width * params.cell_size  # 高斯 sigma
    width2 = 2.0 * width * width

    gx = int(math.floor(world_x / region_size))
    gy = int(math.floor(world_y / region_size))

    total_uplift = 0.0

    # 检查 3×3 邻域
    for dy in (-1, 0, 1):
        for dx in (-1, 0, 1):
            rx, ry = gx + dx, gy + dy
            for (fx, fy, angle, halflen, strength) in _region_faults(rx, ry, seed, params):
                # 点到断层的垂直距离
                dpx = world_x - fx
                dpy = world_y - fy

                # 沿断层方向投影
                along = math.cos(angle) * dpx + math.sin(angle) * dpy
                if abs(along) > halflen:
                    continue  # 超出断层长度

                # 垂直距离
                perp = -math.sin(angle) * dpx + math.cos(angle) * dpy

                # 高斯剖面
                ridge = math.exp(-perp * perp / width2)

                # 端点渐变
                end_taper = 1.0 - abs(along) / halflen
                ridge *= end_taper

                total_uplift += strength * ridge

    return total_uplift


def _detail_elevation(
    world_x: float, world_y: float, seed: int, params: WorldParams
) -> float:
    """中尺度地形细节：小单元 Voronoi，零中心。"""
    d_cell = params.detail_cell_size
    sigma = d_cell * 0.45
    sigma2 = 2.0 * sigma * sigma

    gx = int(math.floor(world_x / d_cell))
    gy = int(math.floor(world_y / d_cell))

    w_sum = 0.0
    e_sum = 0.0
    for dy in (-2, -1, 0, 1, 2):
        for dx in (-2, -1, 0, 1, 2):
            nx, ny = gx + dx, gy + dy
            cx, cy = _cell_center(nx, ny, d_cell, params.detail_jitter, seed + 100000)
            elev = _hash_float(nx, ny, seed + 103000, -1.0, 1.0)
            d2 = (world_x - cx) ** 2 + (world_y - cy) ** 2
            w = math.exp(-d2 / sigma2)
            e_sum += elev * w
            w_sum += w

    return (e_sum / (w_sum + 1e-10)) * params.detail_amplitude


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
    sigma = cell_size * params.blend_sigma
    sigma2 = 2.0 * sigma * sigma

    weighted_sum = 0.0
    weight_sum = 0.0
    for d2, cx, cy, elev, drx, dry in candidates:
        w = math.exp(-d2 / sigma2)
        weighted_sum += elev * w
        weight_sum += w

    altitude = weighted_sum / (weight_sum + 1e-10)

    # 中尺度地形细节
    altitude += _detail_elevation(world_x, world_y, seed, params)

    # 断层线隆起 — 连绵山脉
    altitude += _fault_uplift(world_x, world_y, seed, params)

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
    sigma2 = 2.0 * (cell_size * params.blend_sigma) ** 2

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
        sigma2, 0.0, 0.0,          # drift_scale, uplift_scale (C skip)
        0.0,                        # sea_level_offset (Python handles)
        params.altitude_floor, params.altitude_ceil,
        arr_out,
    )

    result = list(arr_out)
    # Python 后处理：细节 + 断层 + offset
    for i in range(w * h):
        wx = world_x + (i % w)
        wy = world_y + (i // w)
        result[i] += _detail_elevation(wx, wy, seed, params)
        result[i] += _fault_uplift(wx, wy, seed, params)
        result[i] += params.sea_level_offset
        r = result[i]
        if r < params.altitude_floor: r = params.altitude_floor
        elif r > params.altitude_ceil: r = params.altitude_ceil
        result[i] = r
    return result


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

            # 中尺度细节 + 断层线隆起
            altitude += _detail_elevation(wx, wy, seed, params)
            altitude += _fault_uplift(wx, wy, seed, params)

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
    sample_size: int = 0,  # 0 = 自动按 cell_size 计算
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

    if sample_size <= 0:
        sample_size = params.cell_size * 5  # 覆盖 ~25 个单元
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
