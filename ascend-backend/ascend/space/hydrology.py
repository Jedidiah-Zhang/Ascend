"""水文系统 — D8 流向 + 水流累积 + 河流提取 + 水力侵蚀。

在低分辨率 DEM 上模拟地表水流和侵蚀：
  1. 填洼（优先队列，消除局部洼地）
  2. D8 流向（每个像素指向最陡下坡邻居）
  3. 水流累积（从源头向下游累加流量）
  4. 河流提取（累积量 > 阈值的连续像素链）
  5. Strahler 河流分级
  6. 水力侵蚀（河道加深 + 山坡物质搬运）

用法:
    from ascend.space.hydrology import compute_d8, flow_accumulation, erode

    directions = compute_d8(dem, w, h)
    acc = flow_accumulation(directions, w, h)
    eroded = erode(dem, rainfall, w, h, iterations=5)
"""

import ctypes
import math
import subprocess
from array import array
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path

# ── C 扩展加载（与 _perlin.so 相同模式） ───────────────────

_HERE = Path(__file__).resolve().parent
_HYDRO_SO = _HERE / "_hydrology.so"
_HYDRO_C = _HERE / "_hydrology.c"

if not _HYDRO_SO.exists() or _HYDRO_C.stat().st_mtime > _HYDRO_SO.stat().st_mtime:
    subprocess.run(
        ["gcc", "-O3", "-march=native", "-ffast-math", "-funroll-loops",
         "-shared", "-fPIC", "-o", str(_HYDRO_SO), str(_HYDRO_C), "-lm"],
        check=True, cwd=str(_HERE),
    )

_HYDRO = ctypes.CDLL(str(_HYDRO_SO))

# compute_d8
_HYDRO.hydrology_compute_d8.argtypes = [
    ctypes.POINTER(ctypes.c_double),  # dem
    ctypes.c_int, ctypes.c_int,       # w, h
    ctypes.POINTER(ctypes.c_int),     # directions (out)
]
_HYDRO.hydrology_compute_d8.restype = None

# flow_accumulation
_HYDRO.hydrology_flow_accumulation.argtypes = [
    ctypes.POINTER(ctypes.c_int),     # directions
    ctypes.POINTER(ctypes.c_double),  # source (NULL=default 1.0)
    ctypes.c_int, ctypes.c_int,       # w, h
    ctypes.POINTER(ctypes.c_double),  # acc (out)
]
_HYDRO.hydrology_flow_accumulation.restype = None

# erode_step
_HYDRO.hydrology_erode_step.argtypes = [
    ctypes.POINTER(ctypes.c_double),  # dem
    ctypes.POINTER(ctypes.c_int),     # directions
    ctypes.POINTER(ctypes.c_double),  # acc
    ctypes.POINTER(ctypes.c_double),  # flow_source
    ctypes.c_int, ctypes.c_int,       # w, h
    ctypes.c_double,                  # erodibility
    ctypes.POINTER(ctypes.c_double),  # delta_out
    ctypes.POINTER(ctypes.c_double),  # sediment_out
]
_HYDRO.hydrology_erode_step.restype = None

# fill_depressions
_HYDRO.hydrology_fill_depressions.argtypes = [
    ctypes.POINTER(ctypes.c_double),  # dem
    ctypes.c_int, ctypes.c_int,       # w, h
    ctypes.POINTER(ctypes.c_double),  # result (out)
]
_HYDRO.hydrology_fill_depressions.restype = None

# apply_erosion
_HYDRO.hydrology_apply_erosion.argtypes = [
    ctypes.POINTER(ctypes.c_double),  # dem (in/out)
    ctypes.POINTER(ctypes.c_double),  # sediment_net (in/out)
    ctypes.POINTER(ctypes.c_double),  # delta
    ctypes.c_int,                      # n
]
_HYDRO.hydrology_apply_erosion.restype = ctypes.c_double

# gaussian_blur
_HYDRO.hydrology_gaussian_blur.argtypes = [
    ctypes.POINTER(ctypes.c_double),  # arr
    ctypes.c_int, ctypes.c_int,       # w, h
    ctypes.c_double,                  # sigma
    ctypes.POINTER(ctypes.c_double),  # result (out)
]
_HYDRO.hydrology_gaussian_blur.restype = None

# distance_to_ocean
_HYDRO.hydrology_distance_to_ocean.argtypes = [
    ctypes.POINTER(ctypes.c_double),  # elevation
    ctypes.c_int, ctypes.c_int,       # w, h
    ctypes.POINTER(ctypes.c_double),  # dist_out
]
_HYDRO.hydrology_distance_to_ocean.restype = None

# rain_shadow_omnidirectional
_HYDRO.hydrology_rain_shadow_omnidirectional.argtypes = [
    ctypes.POINTER(ctypes.c_double),  # elevation
    ctypes.c_int, ctypes.c_int,       # w, h
    ctypes.c_double,                   # primary_angle
    ctypes.c_double,                   # secondary_angle
    ctypes.c_double,                   # secondary_weight
    ctypes.c_double,                   # decay_length_km
    ctypes.c_double,                   # cell_size_km
    ctypes.c_double,                   # min_factor
    ctypes.POINTER(ctypes.c_double),  # factors (out)
]
_HYDRO.hydrology_rain_shadow_omnidirectional.restype = None

# compute_climate
_HYDRO.hydrology_compute_climate.argtypes = [
    ctypes.POINTER(ctypes.c_double),  # elevation
    ctypes.POINTER(ctypes.c_double),  # lat_wiggle
    ctypes.POINTER(ctypes.c_double),  # rain_raw
    ctypes.POINTER(ctypes.c_double),  # rain_shadow
    ctypes.POINTER(ctypes.c_double),  # dist_to_ocean (NULL=跳过大陆度)
    ctypes.c_int, ctypes.c_int,       # w, h
    ctypes.c_double,                   # gx
    ctypes.c_double,                   # gy
    ctypes.c_double,                   # continentality_k
    ctypes.c_double,                   # continentality_d0
    ctypes.c_double,                   # cell_size_km
    ctypes.POINTER(ctypes.c_double),  # temp_out
    ctypes.POINTER(ctypes.c_double),  # rain_out
    ctypes.POINTER(ctypes.c_int),     # climate_out
]
_HYDRO.hydrology_compute_climate.restype = None


def _compute_d8_c(dem: array, w: int, h: int) -> array:
    """C 加速 D8 流向计算（零拷贝 — dem 和返回值均为 array）。"""
    n = w * h
    dem_ptr = (ctypes.c_double * n).from_buffer(dem)
    dirs = array('i', [0]) * n
    dirs_ptr = (ctypes.c_int * n).from_buffer(dirs)
    _HYDRO.hydrology_compute_d8(dem_ptr, w, h, dirs_ptr)
    return dirs


def _gaussian_blur_c(arr: array, w: int, h: int, sigma: float) -> array:
    """C 加速可分离 2-pass 高斯模糊（零拷贝）。"""
    n = w * h
    arr_ptr = (ctypes.c_double * n).from_buffer(arr)
    result = array('d', [0.0]) * n
    result_ptr = (ctypes.c_double * n).from_buffer(result)
    _HYDRO.hydrology_gaussian_blur(arr_ptr, w, h, sigma, result_ptr)
    return result


def _distance_to_ocean_c(elevation: array, w: int, h: int) -> array:
    """C 加速 BFS 距海距离计算（零拷贝）。

    对每个陆地格返回其到最近海洋格的 Chebyshev 距离（格数）。
    海洋格距离为 0。

    Args:
        elevation: 海拔数组（行优先，<0=海洋）。
        w: 宽度。
        h: 高度。

    Returns:
        距海距离数组（格数，double）。
    """
    n = w * h
    elev_ptr = (ctypes.c_double * n).from_buffer(elevation)
    dist = array('d', [0.0]) * n
    dist_ptr = (ctypes.c_double * n).from_buffer(dist)
    _HYDRO.hydrology_distance_to_ocean(elev_ptr, w, h, dist_ptr)
    return dist


def _rain_shadow_omnidirectional_c(
    elevation: array, w: int, h: int,
    primary_angle: float,
    secondary_angle: float = 0.0,
    secondary_weight: float = 0.0,
    decay_length_km: float = 4.0,
    cell_size_km: float = 0.1,
    min_factor: float = 0.15,
) -> array:
    """C 加速万向抬升累积雨影因子（零拷贝）。

    支持任意风向角（弧度）。沿风向累积地形抬升量，
    指数衰减替代滑动窗口，映射到平滑分段线性雨影因子。

    Args:
        elevation: 海拔数组（行优先）。
        w: 宽度。
        h: 高度。
        primary_angle: 主风向角（弧度）。
        secondary_angle: 次风向角（弧度）。
        secondary_weight: 次风权重 [0, 1]。
        decay_length_km: 抬升指数衰减距离 (km)，≈旧滑动窗口大小。
        cell_size_km: 每格公里数。
        min_factor: 最小因子。

    Returns:
        雨影因子数组 [min_factor, 1.0]。
    """
    n = w * h
    elev_ptr = (ctypes.c_double * n).from_buffer(elevation)
    factors = array('d', [0.0]) * n
    factors_ptr = (ctypes.c_double * n).from_buffer(factors)
    _HYDRO.hydrology_rain_shadow_omnidirectional(
        elev_ptr, w, h,
        primary_angle, secondary_angle, secondary_weight,
        decay_length_km, cell_size_km, min_factor,
        factors_ptr,
    )
    return factors


def _compute_climate_c(
    elevation: array,
    lat_wiggle: array, rain_raw: array,
    rain_shadow: array,
    dist_to_ocean: array | None,
    w: int, h: int,
    gx: float, gy: float,
    continentality_k: float = 3.0,
    continentality_d0: float = 200.0,
    cell_size_km: float = 0.1,
) -> tuple[array, array, array]:
    """C 加速气候计算 — 600K 遍历下沉到 C，包含 classify() 决策树（零拷贝）。

    Args:
        elevation: 海拔数组。
        lat_wiggle: 纬度噪声数组。
        rain_raw: 降雨噪声原始值。
        rain_shadow: 雨影因子数组。
        dist_to_ocean: 距海距离数组（None=跳过大陆度修正）。
        w, h: 网格尺寸。
        gx, gy: 温度梯度方向向量。
        continentality_k: 大陆度振幅 (°C)。
        continentality_d0: 特征距离 (km)。
        cell_size_km: 每格公里数。
    """
    n = w * h
    elev_ptr = (ctypes.c_double * n).from_buffer(elevation)
    lat_ptr = (ctypes.c_double * n).from_buffer(lat_wiggle)
    rain_raw_ptr = (ctypes.c_double * n).from_buffer(rain_raw)
    shadow_ptr = (ctypes.c_double * n).from_buffer(rain_shadow)
    dist_ptr = (ctypes.c_double * n).from_buffer(dist_to_ocean) if dist_to_ocean is not None else None
    temp = array('d', [0.0]) * n
    rain = array('d', [0.0]) * n
    climate = array('i', [0]) * n
    temp_ptr = (ctypes.c_double * n).from_buffer(temp)
    rain_ptr = (ctypes.c_double * n).from_buffer(rain)
    climate_ptr = (ctypes.c_int * n).from_buffer(climate)

    _HYDRO.hydrology_compute_climate(
        elev_ptr, lat_ptr, rain_raw_ptr, shadow_ptr, dist_ptr,
        w, h, gx, gy,
        continentality_k, continentality_d0, cell_size_km,
        temp_ptr, rain_ptr, climate_ptr,
    )
    return temp, rain, climate


def _flow_accumulation_c(
    directions: array, w: int, h: int,
    source: array | None = None,
) -> array:
    """C 加速水流累积量（零拷贝）。"""
    n = w * h
    dirs_ptr = (ctypes.c_int * n).from_buffer(directions)
    acc = array('d', [0.0]) * n
    acc_ptr = (ctypes.c_double * n).from_buffer(acc)

    if source is not None:
        src_ptr = (ctypes.c_double * n).from_buffer(source)
        _HYDRO.hydrology_flow_accumulation(dirs_ptr, src_ptr, w, h, acc_ptr)
    else:
        _HYDRO.hydrology_flow_accumulation(dirs_ptr, None, w, h, acc_ptr)

    return acc


def _erode_step_c(
    dem: array, directions: array,
    acc: array, flow_source: array,
    w: int, h: int, erodibility: float,
) -> tuple[array, array]:
    """C 加速单轮侵蚀 delta + 沉积（零拷贝）。"""
    n = w * h
    dem_ptr = (ctypes.c_double * n).from_buffer(dem)
    dirs_ptr = (ctypes.c_int * n).from_buffer(directions)
    acc_ptr = (ctypes.c_double * n).from_buffer(acc)
    src_ptr = (ctypes.c_double * n).from_buffer(flow_source)
    delta = array('d', [0.0]) * n
    sed = array('d', [0.0]) * n
    delta_ptr = (ctypes.c_double * n).from_buffer(delta)
    sed_ptr = (ctypes.c_double * n).from_buffer(sed)

    _HYDRO.hydrology_erode_step(
        dem_ptr, dirs_ptr, acc_ptr, src_ptr, w, h, erodibility,
        delta_ptr, sed_ptr,
    )
    return delta, sed


def _apply_erosion_c(
    dem: array, sediment_net: array,
    delta: array, n: int,
) -> float:
    """C 加速侵蚀 delta 应用 — 通过 from_buffer 原地修改 dem 和 sediment_net。

    零拷贝：C 直接写入 array 对象的底层缓冲区，无需 Python 写回循环。
    """
    dem_ptr = (ctypes.c_double * n).from_buffer(dem)
    sed_ptr = (ctypes.c_double * n).from_buffer(sediment_net)
    delta_ptr = (ctypes.c_double * n).from_buffer(delta)
    return _HYDRO.hydrology_apply_erosion(dem_ptr, sed_ptr, delta_ptr, n)



def _fill_depressions_c(dem: array, w: int, h: int) -> array:
    """C 加速填洼（零拷贝）。

    Planchon-Darboux：海洋为边界，最小堆保证最低水位路径优先传播。
    """
    n = w * h
    dem_ptr = (ctypes.c_double * n).from_buffer(dem)
    result = array('d', [0.0]) * n
    result_ptr = (ctypes.c_double * n).from_buffer(result)
    _HYDRO.hydrology_fill_depressions(dem_ptr, w, h, result_ptr)
    return result


# ════════════════════════════════════════════════════════════════
# 水文数据结构
# ════════════════════════════════════════════════════════════════


@dataclass
class ErosionResult:
    """erode() 的完整返回 — 最终海拔 + 全部水文状态。

    将原本丢弃的流向、累积量、盆地信息打包返回，
    供下游构建河流树和湖泊盆地。

    Attributes:
        dem: 侵蚀后的海拔数组 (m)。
        filled_dem: 最后一轮填洼后海拔（用于湖泊检测）。
        flow_acc: 最后一轮水流累积量。
        directions: 最后一轮 D8 流向（-1=汇点）。
        sediment_net: 净沉积量（正=沉积，负=侵蚀）。
    """

    dem: list[float]
    filled_dem: list[float]
    flow_acc: list[float]
    directions: list[int]
    sediment_net: list[float] = field(default_factory=list)

    def __repr__(self) -> str:
        elev_range = f"{min(self.dem):.0f}~{max(self.dem):.0f}m"
        return f"ErosionResult(elev=[{elev_range}], n={len(self.dem)})"


@dataclass
class LakeBasin:
    """一个湖泊盆地 — 封闭洼地 + 溢出口决定的湖面。

    Attributes:
        cells: 盆地内像素的网格索引列表。
        surface_elev: 湖面海拔（溢出口高度，m）。
        area_km2: 湖面面积 (km²)。
    """

    cells: list[int]
    surface_elev: float
    area_km2: float = 0.0

    def __repr__(self) -> str:
        return (
            f"LakeBasin(cells={len(self.cells)}, "
            f"surface={self.surface_elev:.0f}m, "
            f"area={self.area_km2:.2f}km²)"
        )


@dataclass
class HydrologyData:
    """层1 水文数据 — 统一传递到 Tile 层的结构化水体信息。

    Attributes:
        river_network: 流线河流网络（RK4 积分,可能为 None）。
        lake_basins: 湖泊盆地列表。
        flow_acc: 水流累积量场（行优先）。
        directions: D8 流向场（行优先）。
        filled_dem: 填洼后海拔（行优先）。
    """

    lake_basins: list[LakeBasin]
    flow_acc: list[float]
    directions: list[int]
    filled_dem: list[float]
    river_network: object | None = None  # RiverNetwork(避免循环导入)

    def __repr__(self) -> str:
        network_pts = 0
        if self.river_network is not None:
            network_pts = sum(len(r.points) for r in self.river_network.rivers)
        return (
            f"HydrologyData(streamlines={network_pts}, "
            f"lakes={len(self.lake_basins)})"
        )


# D8 方向常量
_DX = [1, -1, 0, 0, 1, -1, 1, -1]
_DY = [0, 0, 1, -1, 1, 1, -1, -1]
_DIAG = [False, False, False, False, True, True, True, True]


def _dem_at(dem: list[float], w: int, h: int, x: int, y: int) -> float:
    """安全读取 DEM 值，越界返回 inf。"""
    if 0 <= x < w and 0 <= y < h:
        return dem[y * w + x]
    return float("inf")


# ════════════════════════════════════════════════════════════════
# 填洼
# ════════════════════════════════════════════════════════════════


def fill_depressions(dem: list[float], w: int, h: int) -> list[float]:
    """填平 DEM 中的局部洼地（C 加速优先队列）。

    使用优先队列（Planchon-Darboux 算法简化版）：
    从边界最低点出发，向内灌水，确保每个像素都有向边界的下坡路径。

    Args:
        dem: 行优先海拔数组。
        w: 宽度。
        h: 高度。

    Returns:
        填洼后的海拔数组（新列表）。
    """
    dem_arr = array('d', dem)
    result = _fill_depressions_c(dem_arr, w, h)
    return result.tolist()


# ════════════════════════════════════════════════════════════════
# D8 流向
# ════════════════════════════════════════════════════════════════


def compute_d8(dem: list[float], w: int, h: int) -> list[int]:
    """计算 D8 流向（C 加速）。

    每个像素指向 8 邻域中最陡下坡方向。
    方向编码：0=E, 1=W, 2=S, 3=N, 4=SE, 5=SW, 6=NE, 7=NW
    无下坡邻居 → -1（汇点/海洋）。

    Args:
        dem: 行优先海拔数组。
        w: 宽度。
        h: 高度。

    Returns:
        行优先方向数组（int）。
    """
    dem_arr = array('d', dem)
    dirs = _compute_d8_c(dem_arr, w, h)
    return dirs.tolist()

# ════════════════════════════════════════════════════════════════
# 水流累积
# ════════════════════════════════════════════════════════════════


def flow_accumulation(
    directions: list[int], w: int, h: int,
    *,
    source: list[float] | None = None,
) -> list[float]:
    """计算水流累积量（C 加速）。"""
    dirs_arr = array('i', directions)
    src_arr = array('d', source) if source is not None else None
    result = _flow_accumulation_c(dirs_arr, w, h, source=src_arr)
    return result.tolist()


# ════════════════════════════════════════════════════════════════
# 河流提取
# ════════════════════════════════════════════════════════════════


# ════════════════════════════════════════════════════════════════
# 水力侵蚀
# ════════════════════════════════════════════════════════════════


def erode(
    dem: list[float],
    rainfall: list[float],
    w: int, h: int,
    *,
    iterations: int = 20,
    erodibility: float = 0.01,
    tolerance: float = 0.05,
    min_iterations: int = 3,
) -> ErosionResult:
    """简化水力侵蚀模型 — 降雨驱动的水流累积 + 侵蚀/沉积。

    每轮迭代：
      1. 填洼（消除局部洼地，确保水流向边界）
      2. D8 流向
      3. 水流累积（降雨量作每像素水源——雨多的地方产流多）
      4. 侵蚀量 = K × 累积量^m × 坡度^n
      5. 更新海拔（沉积在下游）

    物质从陡坡 + 高流量处侵蚀，沿流路沉积在低处。
    自适应收敛：当最大海拔变化 < tolerance 时提前退出。

    Args:
        dem: 行优先海拔数组。
        rainfall: 降雨量数组（同尺寸，mm/yr，>0）。
        w: 宽度。
        h: 高度。
        iterations: 侵蚀最大迭代轮数。
        erodibility: 侵蚀系数 K。
        tolerance: 收敛容差 (m)，单轮最大变化小于此值即停止。
        min_iterations: 最少迭代轮数（收敛检测前至少运行这些轮数）。

    Returns:
        ErosionResult 包含最终海拔、流向、累积量、沉积场。
    """
    n = w * h

    # 一次性转换为 array（零拷贝 C 调用的基础）
    result = array('d', dem)
    flow_source = array('d', (max(0.0, rainfall[i] / 1000.0) for i in range(n)))
    sediment_net = array('d', [0.0]) * n

    # 保存最后一轮的水文状态（array 类型）
    last_filled: array | None = None
    last_directions: array | None = None
    last_acc: array | None = None

    for iteration in range(iterations):
        # 填洼（C 加速优先队列，零拷贝）
        filled = _fill_depressions_c(result, w, h)

        # D8 + 累积 + 侵蚀 ← C 加速，零拷贝
        directions = _compute_d8_c(filled, w, h)
        acc = _flow_accumulation_c(directions, w, h, source=flow_source)

        # 保存最后一轮状态
        last_filled = filled
        last_directions = directions
        last_acc = acc

        # C 加速侵蚀 delta（零拷贝）
        delta, _ = _erode_step_c(
            result, directions, acc, flow_source, w, h, erodibility)

        # 应用侵蚀 + 累积沉积 + 跟踪最大变化（零拷贝 — C 直接写 result 缓冲区）
        max_delta = _apply_erosion_c(result, sediment_net, delta, n)

        # 自适应收敛：地形变化微小时提前退出
        if iteration >= min_iterations and max_delta < tolerance:
            break

    return ErosionResult(
        dem=result.tolist(),
        filled_dem=last_filled.tolist() if last_filled else [],
        flow_acc=last_acc.tolist() if last_acc else [],
        directions=last_directions.tolist() if last_directions else [],
        sediment_net=sediment_net.tolist(),
    )


def find_lakes(
    dem: list[float],
    land_mask: list[bool],
    w: int, h: int,
    *,
    min_size: int = 5,
    filled_dem: list[float] | None = None,
) -> tuple[list[float], list[float]]:
    """洼地填水 → 湖泊检测。

    使用填洼结果：filled > original 的连通区域 = 洼地盆地。
    盆地底部低于 0 → 内陆湖；盆地底部 > 0 → 高位湖。

    Args:
        dem: 行优先原始海拔。
        land_mask: 陆地掩码。
        w: 宽度。
        h: 高度。
        min_size: 最小湖泊面积（像素）。
        filled_dem: 预填充的 DEM（避免重复 fill_depressions 调用）。

    Returns:
        (lake_surface, filled): 行优先湖面海拔（0=非湖）和填洼后 DEM。
    """
    n = w * h
    filled = filled_dem if filled_dem is not None else fill_depressions(dem, w, h)
    lake_surface: list[float] = [0.0] * n

    # 洼地 = 填洼后上升 > 1m 的陆地像素
    is_depression = [False] * n
    for i in range(n):
        if land_mask[i] and (filled[i] - dem[i]) > 1.0:
            is_depression[i] = True

    # 找洼地的连通分量
    visited = [False] * n
    for i in range(n):
        if not is_depression[i] or visited[i]:
            continue

        # BFS 收集连通分量
        comp: list[int] = []
        q = deque([i])
        visited[i] = True
        while q:
            ci = q.popleft()
            comp.append(ci)
            cx, cy = ci % w, ci // w
            for d in range(8):
                nx, ny = cx + _DX[d], cy + _DY[d]
                if 0 <= nx < w and 0 <= ny < h:
                    ni = ny * w + nx
                    if is_depression[ni] and not visited[ni]:
                        visited[ni] = True
                        q.append(ni)

        if len(comp) >= min_size:
            # 湖面海拔 = 填洼后的溢出口高度
            lake_elev = max(filled[ci] for ci in comp)
            for ci in comp:
                lake_surface[ci] = lake_elev

    return lake_surface, filled


def extract_lake_basins(
    dem: list[float],
    filled_dem: list[float],
    land_mask: list[bool],
    w: int, h: int,
    *,
    min_size: int = 5,
) -> list[LakeBasin]:
    """从填洼 DEM 中提取湖泊盆地列表。

    洼地 = 填洼后上升 > 1m 的陆地像素。
    BFS 找连通分量，每个分量的溢出口高程 = 湖面。

    与 find_lakes() 不同，此函数返回结构化的 LakeBasin 对象
    （含 surface_elev 和 area_km2），供 Tile 层渲染使用。

    Args:
        dem: 行优先原始海拔。
        filled_dem: 填洼后海拔（来自 fill_depressions 或 ErosionResult）。
        land_mask: 陆地掩码。
        w: 宽度。
        h: 高度。
        min_size: 最小盆地面积（像素）。

    Returns:
        LakeBasin 列表（按面积降序）。
    """
    n = w * h

    # 洼地 = 填洼后上升 > 1m 的陆地像素
    is_depression = [False] * n
    for i in range(n):
        if land_mask[i] and (filled_dem[i] - dem[i]) > 1.0:
            is_depression[i] = True

    # BFS 找连通分量
    visited = [False] * n
    basins: list[LakeBasin] = []

    for i in range(n):
        if not is_depression[i] or visited[i]:
            continue

        # 收集连通分量
        comp: list[int] = []
        q = deque([i])
        visited[i] = True
        while q:
            ci = q.popleft()
            comp.append(ci)
            cx, cy = ci % w, ci // w
            for d in range(8):
                nx, ny = cx + _DX[d], cy + _DY[d]
                if 0 <= nx < w and 0 <= ny < h:
                    ni = ny * w + nx
                    if is_depression[ni] and not visited[ni]:
                        visited[ni] = True
                        q.append(ni)

        if len(comp) < min_size:
            continue

        # 计算溢出口高程：分量边界上最低 filled 像素 = 湖面
        # 边界 = 分量中至少有一个邻居不在分量中的像素
        comp_set = set(comp)
        spill_elev = float("inf")
        for ci in comp:
            cx, cy = ci % w, ci // w
            for d in range(8):
                nx, ny = cx + _DX[d], cy + _DY[d]
                if not (0 <= nx < w and 0 <= ny < h):
                    continue
                ni = ny * w + nx
                if ni not in comp_set:
                    # 邻居不在分量中 → ci 是边界像素
                    # 溢出口高度 = filled_dem[ci]（该像素的水位）
                    spill_elev = min(spill_elev, filled_dem[ci])

        if spill_elev == float("inf"):
            # 兜底：取分量最高 filled 值
            spill_elev = max(filled_dem[ci] for ci in comp)

        # 计算面积
        cell_km = 0.1  # 100m = 0.1km
        area_km2 = len(comp) * cell_km * cell_km

        basins.append(LakeBasin(
            cells=comp,
            surface_elev=spill_elev,
            area_km2=area_km2,
        ))

    # 按面积降序
    basins.sort(key=lambda b: b.area_km2, reverse=True)
    return basins


def compute_river_width(
    dem: list[float],
    w: int, h: int,
    *,
    land_mask: list[bool] | None = None,
    threshold: float = 30.0,
    min_width: float = 2.0,
    max_width: float = 80.0,
    # 预计算水文数据 — 传入则跳过 fill_depressions + D8 + flow_accumulation
    directions: list[int] | None = None,
    flow_acc: list[float] | None = None,
    # 预提取湖泊盆地 — 传入则跳过 find_lakes + BFS
    lake_basins: list[LakeBasin] | None = None,
) -> list[float]:
    """计算河流+湖泊宽度场（层1分辨率 → 供层2 tile查询）。

    河流宽度正比于 log(累积流量)。
    湖泊宽度基于湖面积（sqrt(面积)）。

    支持传入预计算的水文数据以避免重复计算：
      - directions + flow_acc：跳过 fill_depressions + D8 + flow_accumulation
      - lake_basins：跳过 find_lakes + BFS 连通分量检测

    Args:
        dem: 行优先海拔数组。
        w: 宽度。
        h: 高度。
        land_mask: 陆地掩码（用于湖泊检测，lake_basins 未提供时必需）。
        threshold: 河流提取阈值。
        min_width: 最小宽度 (m)。
        max_width: 最大宽度 (m)。
        directions: 预计算 D8 流向（可选，与 flow_acc 配对使用）。
        flow_acc: 预计算水流累积量（可选，与 directions 配对使用）。
        lake_basins: 预提取湖泊盆地列表（可选，传入后跳过湖泊检测）。

    Returns:
        行优先宽度数组 (m)，非水体像素 = 0。
    """
    n = w * h
    widths: list[float] = [0.0] * n

    # ── 河流宽度 ──
    # 复用预计算数据或从头计算
    if directions is not None and flow_acc is not None:
        dirs = directions
        acc = flow_acc
    else:
        filled = fill_depressions(dem, w, h)
        dirs = compute_d8(filled, w, h)
        acc = flow_accumulation(dirs, w, h)

    # 直接筛选河流像素（O(n)），代替 extract_rivers 的 O(n²) 源头检测
    river_indices = [i for i in range(n)
                     if dirs[i] >= 0 and acc[i] >= threshold and dem[i] > 0]
    if river_indices:
        max_acc = max(acc[i] for i in river_indices)
        for i in river_indices:
            ratio = acc[i] / max_acc
            log_ratio = math.log(1.0 + ratio * 20.0) / math.log(21.0)
            widths[i] = min_width + (max_width - min_width) * log_ratio

    # ── 湖泊宽度 ──
    if lake_basins is not None:
        # 直接从 LakeBasin 对象计算宽度（无需 BFS）
        for basin in lake_basins:
            lake_width = math.sqrt(len(basin.cells)) * 100.0  # 100m/px
            lake_width = max(min_width, min(max_width * 3, lake_width))
            for ci in basin.cells:
                widths[ci] = lake_width
    elif land_mask is not None:
        # 兜底：从头计算湖泊
        filled = fill_depressions(dem, w, h)
        lake_surface, _ = find_lakes(dem, land_mask, w, h,
                                      min_size=5, filled_dem=filled)
        visited = [False] * n
        for i in range(n):
            if lake_surface[i] <= 0 or visited[i]:
                continue
            comp: list[int] = []
            q = deque([i])
            visited[i] = True
            while q:
                ci = q.popleft()
                comp.append(ci)
                cx, cy = ci % w, ci // w
                for d in range(8):
                    nx, ny = cx + _DX[d], cy + _DY[d]
                    if 0 <= nx < w and 0 <= ny < h:
                        ni = ny * w + nx
                        if lake_surface[ni] > 0 and not visited[ni]:
                            visited[ni] = True
                            q.append(ni)

            lake_width = math.sqrt(len(comp)) * 100.0
            lake_width = max(min_width, min(max_width * 3, lake_width))
            for ci in comp:
                widths[ci] = lake_width

    return widths


__all__ = [
    "ErosionResult",
    "LakeBasin",
    "HydrologyData",
    "fill_depressions",
    "compute_d8",
    "flow_accumulation",
    "extract_lake_basins",
    "erode",
    "find_lakes",
    "compute_river_width",
]
