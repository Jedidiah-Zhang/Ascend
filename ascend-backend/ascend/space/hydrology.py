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
from collections import deque
from dataclasses import dataclass, field
from heapq import heappush, heappop
from pathlib import Path

# ── C 扩展加载（与 _perlin.so 相同模式） ───────────────────

_HERE = Path(__file__).resolve().parent
_HYDRO_SO = _HERE / "_hydrology.so"
_HYDRO_C = _HERE / "_hydrology.c"

if not _HYDRO_SO.exists() or _HYDRO_C.stat().st_mtime > _HYDRO_SO.stat().st_mtime:
    subprocess.run(
        ["gcc", "-O3", "-shared", "-fPIC", "-o", str(_HYDRO_SO), str(_HYDRO_C), "-lm"],
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

# hillslope_step
_HYDRO.hydrology_hillslope_step.argtypes = [
    ctypes.POINTER(ctypes.c_double),  # dem
    ctypes.c_int, ctypes.c_int,       # w, h
    ctypes.c_double,                  # rate
    ctypes.POINTER(ctypes.c_double),  # delta_out
]
_HYDRO.hydrology_hillslope_step.restype = None

# fill_depressions
_HYDRO.hydrology_fill_depressions.argtypes = [
    ctypes.POINTER(ctypes.c_double),  # dem
    ctypes.c_int, ctypes.c_int,       # w, h
    ctypes.POINTER(ctypes.c_double),  # result (out)
]
_HYDRO.hydrology_fill_depressions.restype = None


def _compute_d8_c(dem: list[float], w: int, h: int) -> list[int]:
    """C 加速 D8 流向计算。"""
    n = w * h
    dem_arr = (ctypes.c_double * n)(*dem)
    dirs_arr = (ctypes.c_int * n)()
    _HYDRO.hydrology_compute_d8(dem_arr, w, h, dirs_arr)
    return list(dirs_arr)


def _flow_accumulation_c(
    directions: list[int], w: int, h: int,
    source: list[float] | None = None,
) -> list[float]:
    """C 加速水流累积量。"""
    n = w * h
    dirs_arr = (ctypes.c_int * n)(*directions)
    acc_arr = (ctypes.c_double * n)()

    if source is not None:
        src_arr = (ctypes.c_double * n)(*source)
        _HYDRO.hydrology_flow_accumulation(dirs_arr, src_arr, w, h, acc_arr)
    else:
        _HYDRO.hydrology_flow_accumulation(dirs_arr, None, w, h, acc_arr)

    return list(acc_arr)


def _erode_step_c(
    dem: list[float], directions: list[int],
    acc: list[float], flow_source: list[float],
    w: int, h: int, erodibility: float,
) -> tuple[list[float], list[float]]:
    """C 加速单轮侵蚀 delta + 沉积。"""
    n = w * h
    dem_arr = (ctypes.c_double * n)(*dem)
    dirs_arr = (ctypes.c_int * n)(*directions)
    acc_arr = (ctypes.c_double * n)(*acc)
    src_arr = (ctypes.c_double * n)(*flow_source)
    delta_arr = (ctypes.c_double * n)()
    sed_arr = (ctypes.c_double * n)()

    _HYDRO.hydrology_erode_step(
        dem_arr, dirs_arr, acc_arr, src_arr, w, h, erodibility,
        delta_arr, sed_arr,
    )
    return list(delta_arr), list(sed_arr)


def _hillslope_step_c(
    dem: list[float], w: int, h: int, rate: float,
) -> list[float]:
    """C 加速山坡扩散 delta。"""
    n = w * h
    dem_arr = (ctypes.c_double * n)(*dem)
    delta_arr = (ctypes.c_double * n)()

    _HYDRO.hydrology_hillslope_step(dem_arr, w, h, rate, delta_arr)
    return list(delta_arr)


def _fill_depressions_c(dem: list[float], w: int, h: int) -> list[float]:
    """C 加速填洼。"""
    n = w * h
    dem_arr = (ctypes.c_double * n)(*dem)
    result_arr = (ctypes.c_double * n)()

    _HYDRO.hydrology_fill_depressions(dem_arr, w, h, result_arr)
    return list(result_arr)


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
class RiverNode:
    """河流树中的一个节点 — 河流经过的一个 100m 网格点。

    Attributes:
        x: 网格 X（整数，用于拓扑查找）。
        y: 网格 Y（整数，用于拓扑查找）。
        px: 扰动后 X（浮点，平缓地带带曲流偏移，陡坡≈grid）。
        py: 扰动后 Y（浮点，同上）。
        flow: 水流累积量。
        strahler: Strahler 级别（1=源头小溪，越大越主流）。
        children: 上游子节点在 RiverTree.nodes 中的索引。
        parent: 下游父节点索引，-1=汇入海洋/湖泊/地图外。
    """

    x: int
    y: int
    flow: float
    px: float = 0.0
    py: float = 0.0
    strahler: int = 1
    children: list[int] = field(default_factory=list)
    parent: int = -1

    def __repr__(self) -> str:
        return (
            f"RiverNode(({self.x},{self.y}), flow={self.flow:.0f}, "
            f"order={self.strahler}, parent={self.parent})"
        )


@dataclass
class RiverTree:
    """河流拓扑树 — 层1所有河流段的集合。

    根节点 = 汇入海洋/湖泊的河口（parent == -1）。
    从根向下遍历可得完整的流域汇流结构。

    Attributes:
        width: 网格宽度。
        height: 网格高度。
        nodes: 所有节点（按流量降序，根在前）。
        node_index: grid_idx → nodes 中的位置。
    """

    width: int
    height: int
    nodes: list[RiverNode] = field(default_factory=list)
    node_index: dict[int, int] = field(default_factory=dict)

    def __repr__(self) -> str:
        roots = sum(1 for n in self.nodes if n.parent < 0)
        return (
            f"RiverTree({self.width}×{self.height}, "
            f"nodes={len(self.nodes)}, roots={roots})"
        )

    def grid_idx(self, node: RiverNode) -> int:
        """节点的行优先网格索引。"""
        return node.y * self.width + node.x

    def get_node_at(self, x: int, y: int) -> RiverNode | None:
        """查询网格坐标处的河流节点，不存在返回 None。"""
        idx = y * self.width + x
        pos = self.node_index.get(idx)
        if pos is not None:
            return self.nodes[pos]
        return None


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
        river_tree: 河流拓扑（可能为 None 表示无河流）。
        lake_basins: 湖泊盆地列表。
        flow_acc: 水流累积量场（行优先）。
        directions: D8 流向场（行优先）。
        filled_dem: 填洼后海拔（行优先）。
    """

    river_tree: RiverTree | None
    lake_basins: list[LakeBasin]
    flow_acc: list[float]
    directions: list[int]
    filled_dem: list[float]

    def __repr__(self) -> str:
        rivers = self.river_tree.nodes if self.river_tree else []
        return (
            f"HydrologyData(rivers={len(rivers)}, "
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
    return _fill_depressions_c(dem, w, h)


# ════════════════════════════════════════════════════════════════
# D8 流向
# ════════════════════════════════════════════════════════════════


def compute_d8(dem: list[float], w: int, h: int) -> list[int]:
    """计算 D8 流向。

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
    n = w * h
    directions: list[int] = [-1] * n

    for y in range(h):
        for x in range(w):
            idx = y * w + x
            elev = dem[idx]
            best_d = -1
            best_slope = -float("inf")

            for d in range(8):
                nx, ny = x + _DX[d], y + _DY[d]
                if not (0 <= nx < w and 0 <= ny < h):
                    continue
                ne = dem[ny * w + nx]
                if ne < elev:
                    dist = math.sqrt(2.0) if d >= 4 else 1.0
                    slope = (elev - ne) / dist
                    if slope > best_slope:
                        best_slope = slope
                        best_d = d

            directions[idx] = best_d

    return directions


def compute_dinf(
    dem: list[float], w: int, h: int,
) -> tuple[list[float], list[int], list[float], list[int]]:
    """D-infinity (D∞) 流向 — 水流可分配到任意角度。

    在第 0-7 方向形成的 8 个三角面中找最陡下坡，
    水流按比例分配到最近的两个 D8 方向。

    Args:
        dem: 行优先海拔数组。
        w: 宽度。
        h: 高度。

    Returns:
        (angle, dir1, frac1, dir2):
          angle[i]  = 流向角度（弧度，0=东，π/2=南），汇点=-1
          dir1[i]   = 第一下游方向 (0-7)
          frac1[i]  = dir1 分得的流量比例
          dir2[i]   = 第二下游方向（或 -1 如果全部分配给 dir1）
    """
    n = w * h
    angles: list[float] = [-1.0] * n  # 汇点 = -1
    dir1s: list[int] = [-1] * n
    frac1s: list[float] = [0.0] * n
    dir2s: list[int] = [-1] * n

    # 8 方向的角度（弧度，从东开始逆时针）
    dir_angles = [0.0, math.pi, math.pi / 2, -math.pi / 2,
                  math.pi / 4, 3 * math.pi / 4, -math.pi / 4, -3 * math.pi / 4]

    for y in range(h):
        for x in range(w):
            idx = y * w + x
            elev = dem[idx]
            best_slope = -1.0
            best_angle = -1.0
            best_d1 = -1
            best_d2 = -1
            best_frac1 = 0.0

            # 检查 8 个三角面：面 d 由中心 + 方向d + 方向(d+1)%8 构成
            for d in range(8):
                d_next = (d + 1) % 8
                a1 = dir_angles[d]
                a2 = dir_angles[d_next]

                # 邻居 1
                nx1, ny1 = x + _DX[d], y + _DY[d]
                if not (0 <= nx1 < w and 0 <= ny1 < h):
                    continue
                e1 = dem[ny1 * w + nx1]
                if e1 >= elev:
                    continue  # 不下坡
                s1 = (elev - e1) / _dist(d)

                # 邻居 2
                nx2, ny2 = x + _DX[d_next], y + _DY[d_next]
                e2_valid = (0 <= nx2 < w and 0 <= ny2 < h)
                e2 = dem[ny2 * w + nx2] if e2_valid else elev
                s2 = (elev - e2) / _dist(d_next) if (e2_valid and e2 < elev) else 0.0

                if s2 > 0:
                    # 两个邻居都更低 → 三角面内插值
                    da = a2 - a1
                    if da > math.pi:
                        da -= 2 * math.pi
                    elif da < -math.pi:
                        da += 2 * math.pi
                    if abs(da) < 0.001:
                        slope = s1
                        best_angle_in_tri = a1
                    else:
                        # 在 (a1, a2) 区间内按坡度比插值
                        r = s1 / (s1 + s2) if s1 + s2 > 0 else 0.5
                        best_angle_in_tri = a1 + da * (1.0 - r)
                        slope = math.sqrt(s1 * s1 + s2 * s2 + 2 * s1 * s2 * math.cos(da))
                else:
                    # 只有邻居 1 更低
                    slope = s1
                    best_angle_in_tri = a1

                if slope > best_slope:
                    best_slope = slope
                    best_angle = best_angle_in_tri
                    best_d1, best_d2, best_frac1 = _angle_to_dirs(best_angle_in_tri)

            angles[idx] = best_angle if best_slope > 0 else -1.0
            dir1s[idx] = best_d1
            frac1s[idx] = best_frac1
            dir2s[idx] = best_d2

    return angles, dir1s, frac1s, dir2s


def _dist(d: int) -> float:
    """D8 方向 d 的步长。"""
    return math.sqrt(2.0) if d >= 4 else 1.0


def _angle_to_dirs(angle: float) -> tuple[int, int, float]:
    """将连续角度映射到最近的两个 D8 方向 + 比例。

    Args:
        angle: 弧度，0=东，π/2=南。

    Returns:
        (dir1, dir2, frac1): dir1 分得 frac1，dir2 分得 1-frac1。
    """
    # 8 方向角度（弧度）
    dir_angles = [0.0, math.pi, math.pi / 2, -math.pi / 2,
                  math.pi / 4, 3 * math.pi / 4, -math.pi / 4, -3 * math.pi / 4]
    # 归一化
    while angle > math.pi:
        angle -= 2 * math.pi
    while angle < -math.pi:
        angle += 2 * math.pi

    # 找最近的 D8 方向
    best_d = 0
    best_diff = float("inf")
    for d in range(8):
        diff = abs(angle - dir_angles[d])
        if diff > math.pi:
            diff = 2 * math.pi - diff
        if diff < best_diff:
            best_diff = diff
            best_d = d

    # 找第二近的 D8 方向（相邻方向）
    second_d = -1
    second_diff = float("inf")
    for d in range(8):
        if d == best_d:
            continue
        diff = abs(angle - dir_angles[d])
        if diff > math.pi:
            diff = 2 * math.pi - diff
        if diff < second_diff:
            second_diff = diff
            second_d = d

    # 流量比例：更接近的方向分更多
    total_diff = best_diff + second_diff
    if total_diff < 0.0001:
        return best_d, -1, 1.0
    frac1 = 1.0 - best_diff / total_diff
    return best_d, second_d, frac1


def flow_accumulation_dinf(
    dem: list[float],
    dir1s: list[int], frac1s: list[float], dir2s: list[int],
    w: int, h: int,
) -> list[float]:
    """D∞ 水流累积。

    Args:
        dem: 行优先海拔数组。
        dir1s, frac1s, dir2s: D∞ 输出。
        w, h: 网格尺寸。

    Returns:
        行优先累积量数组。
    """
    n = w * h
    acc = [1.0] * n

    # 用拓扑排序：按海拔从高到低处理
    indexed = sorted(enumerate(dem), key=lambda x: x[1], reverse=True)

    for idx, _ in indexed:
        d1 = dir1s[idx]
        if d1 < 0:
            continue
        x, y = idx % w, idx // w
        f1 = frac1s[idx]
        f2 = 1.0 - f1

        # 方向 1
        nx1, ny1 = x + _DX[d1], y + _DY[d1]
        if 0 <= nx1 < w and 0 <= ny1 < h:
            ni1 = ny1 * w + nx1
            if dem[ni1] < dem[idx]:
                acc[ni1] += acc[idx] * f1

        # 方向 2
        d2 = dir2s[idx]
        if d2 >= 0 and f2 > 0.001:
            nx2, ny2 = x + _DX[d2], y + _DY[d2]
            if 0 <= nx2 < w and 0 <= ny2 < h:
                ni2 = ny2 * w + nx2
                if dem[ni2] < dem[idx]:
                    acc[ni2] += acc[idx] * f2

    return acc


# ════════════════════════════════════════════════════════════════
# 水流累积
# ════════════════════════════════════════════════════════════════


def flow_accumulation(
    directions: list[int], w: int, h: int,
    *,
    source: list[float] | None = None,
) -> list[float]:
    """计算水流累积量。

    每个像素累积 = 自身源水量 + 所有流入像素的累积量。
    使用拓扑排序（按入度）处理，避免递归。

    Args:
        directions: 行优先 D8 方向数组。
        w: 宽度。
        h: 高度。
        source: 每像素自身贡献的水量，None=默认每像素 1.0。
                传降雨量（归一化到 ~1.0）可实现降雨驱动的水流。

    Returns:
        行优先累积量数组。
    """
    n = w * h
    acc = list(source) if source is not None else [1.0] * n

    # 计算每个像素的入度（有多少像素流入它）
    indegree = [0] * n
    for idx in range(n):
        d = directions[idx]
        if d < 0:
            continue
        nx = (idx % w) + _DX[d]
        ny = (idx // w) + _DY[d]
        if 0 <= nx < w and 0 <= ny < h:
            indegree[ny * w + nx] += 1

    # 拓扑排序：从入度为 0 的点开始
    queue: deque[int] = deque(i for i in range(n) if indegree[i] == 0)

    while queue:
        idx = queue.popleft()
        d = directions[idx]
        if d < 0:
            continue
        nx = (idx % w) + _DX[d]
        ny = (idx // w) + _DY[d]
        if 0 <= nx < w and 0 <= ny < h:
            ni = ny * w + nx
            acc[ni] += acc[idx]
            indegree[ni] -= 1
            if indegree[ni] == 0:
                queue.append(ni)

    return acc


# ════════════════════════════════════════════════════════════════
# 河流提取
# ════════════════════════════════════════════════════════════════


def extract_rivers(
    directions: list[int],
    acc: list[float],
    w: int, h: int,
    *,
    threshold: float = 10.0,
) -> list[list[tuple[int, int]]]:
    """从水流累积中提取河流网络。

    从累积量 > threshold 的源头像素出发，
    沿 D8 流向追踪到汇点或边界。

    Args:
        directions: 行优先 D8 方向数组。
        acc: 行优先水流累积量。
        w: 宽度。
        h: 高度。
        threshold: 标记为河流的最小累积量。

    Returns:
        河流列表，每条河流是 (x, y) 坐标列表。
    """
    n = w * h
    # 标记已追踪的像素
    traced = [False] * n
    rivers: list[list[tuple[int, int]]] = []

    # 找所有源头：累积量 > threshold 且其所有流入邻居都 < threshold
    for idx in range(n):
        if acc[idx] < threshold or traced[idx]:
            continue

        # 检查是否有更高的流入邻居（非源头则跳过）
        x, y = idx % w, idx // w
        has_strong_inflow = False
        for d in range(8):
            nx, ny = x + _DX[d], y + _DY[d]
            if not (0 <= nx < w and 0 <= ny < h):
                continue
            ni = ny * w + nx
            if directions[ni] >= 0:
                ndx = nx + _DX[directions[ni]]
                ndy = ny + _DY[directions[ni]]
                if ndx == x and ndy == y and acc[ni] >= threshold:
                    has_strong_inflow = True
                    break
        if has_strong_inflow:
            continue

        # 从源头追踪河流
        river, traced = _trace_river(idx, directions, traced, w, h)
        if len(river) >= 2:
            rivers.append(river)

    return rivers


def _trace_river(
    start_idx: int,
    directions: list[int],
    traced: list[bool],
    w: int, h: int,
) -> tuple[list[tuple[int, int]], list[bool]]:
    """从 start_idx 沿 D8 方向追踪河流。

    Args:
        start_idx: 起始像素索引。
        directions: D8 方向数组。
        traced: 已追踪标记。
        w, h: 网格尺寸。

    Returns:
        (河流坐标列表, 更新后的 traced)。
    """
    river: list[tuple[int, int]] = []
    idx = start_idx

    while 0 <= idx < len(directions) and not traced[idx]:
        traced[idx] = True
        x, y = idx % w, idx // w
        river.append((x, y))

        d = directions[idx]
        if d < 0:
            break
        idx = (y + _DY[d]) * w + (x + _DX[d])

    return river, traced


# ════════════════════════════════════════════════════════════════
# 河流树构建
# ════════════════════════════════════════════════════════════════


def build_river_tree(
    directions: list[int],
    acc: list[float],
    w: int, h: int,
    *,
    threshold: float = 30.0,
    land_only: bool = False,
    dem: list[float] | None = None,
    min_length: int = 5,
) -> RiverTree:
    """从 D8 流向和水流累积量构建河流拓扑树。

    从 acc > threshold 的像素出发，沿流向追踪到汇点（海洋/地图外）。
    多条支流在交汇点合并为同一节点，父子关系自动形成。

    Strahler 级别在拓扑上正确计算：
      - 无支流的源头 = 1
      - 同级交汇 → +1，不同级 → 取较大者

    Args:
        directions: D8 方向数组。
        acc: 水流累积量数组。
        w: 宽度。
        h: 高度。
        threshold: 河流最小累积量阈值。
        land_only: True=只保留陆地上（dem > 0）的河流。
        dem: 海拔数组（land_only=True 时必需）。

    Returns:
        RiverTree 包含所有河流节点和拓扑关系。
    """
    n = w * h
    tree = RiverTree(width=w, height=h)

    # 陆地过滤
    is_land: list[bool] | None = None
    if land_only and dem is not None:
        is_land = [e > 0 for e in dem]

    # 找河流像素（acc > threshold 且流向有定义）
    river_mask = [False] * n
    river_pixels: list[int] = []
    for i in range(n):
        if acc[i] >= threshold and directions[i] >= 0:
            if is_land is not None and not is_land[i]:
                continue  # 跳过海洋像素
            river_mask[i] = True
            river_pixels.append(i)

    if not river_pixels:
        return tree

    # 追踪：从每个源头（无流入的河流像素）出发
    # 先算入度（仅限河流像素之间的流入）
    indegree = [0] * n
    for i in river_pixels:
        d = directions[i]
        if d < 0:
            continue
        nx = (i % w) + _DX[d]
        ny = (i // w) + _DY[d]
        ni = ny * w + nx
        if 0 <= nx < w and 0 <= ny < h:
            indegree[ni] += 1

    # BFS：从入度为 0 的点出发，沿流向追踪并合并
    traced_to_node: dict[int, int] = {}  # grid_idx → node index in tree.nodes

    # 按入度排序，优先追踪源头
    from heapq import heappush, heappop
    heap: list[tuple[float, int]] = []  # (-acc, idx) —— 高流量优先
    for i in river_pixels:
        if indegree[i] == 0:
            heappush(heap, (-acc[i], i))

    # 如果所有像素都有入度（循环？），从最高流量处开始
    if not heap:
        max_acc_idx = max(river_pixels, key=lambda i: acc[i])
        heappush(heap, (-acc[max_acc_idx], max_acc_idx))

    visited = [False] * n

    while heap:
        _, start_idx = heappop(heap)
        if visited[start_idx]:
            continue

        # 从 start_idx 沿流向追踪
        path: list[int] = []
        idx = start_idx
        while 0 <= idx < n and river_mask[idx] and not visited[idx]:
            visited[idx] = True
            path.append(idx)

            d = directions[idx]
            if d < 0:
                break
            nx = (idx % w) + _DX[d]
            ny = (idx // w) + _DY[d]
            if 0 <= nx < w and 0 <= ny < h:
                idx = ny * w + nx
            else:
                break  # 流出地图

        if not path:
            continue

        # 将 path 中的像素注册为节点（合并已存在的节点）
        prev_node_idx = -1  # 上游节点的 tree index
        for grid_idx in reversed(path):  # 从下游向上游遍历
            if grid_idx in traced_to_node:
                # 此像素已有节点（之前其他支流创建的）→ 交汇点
                existing = traced_to_node[grid_idx]
                if prev_node_idx >= 0:
                    # 将 prev_node 链接到 existing
                    prev_node = tree.nodes[prev_node_idx]
                    prev_node.parent = existing
                    existing_node = tree.nodes[existing]
                    if prev_node_idx not in existing_node.children:
                        existing_node.children.append(prev_node_idx)
                prev_node_idx = existing
            else:
                # 新建节点
                x, y = grid_idx % w, grid_idx // w
                node = RiverNode(
                    x=x, y=y, flow=acc[grid_idx],
                    px=float(x), py=float(y),  # 初始化=网格位置
                    strahler=1,  # 稍后计算
                    parent=prev_node_idx,
                )
                node_idx = len(tree.nodes)
                tree.nodes.append(node)
                tree.node_index[grid_idx] = node_idx
                traced_to_node[grid_idx] = node_idx

                if prev_node_idx >= 0:
                    # 链接父子关系
                    tree.nodes[prev_node_idx].parent = node_idx
                    node.children.append(prev_node_idx)

                prev_node_idx = node_idx

    # 计算 Strahler 级别（拓扑：从叶子向上）
    _compute_strahler(tree)

    # 剪除海边短河（源头到入海口 < min_length 格 = 噪声）
    _prune_short_rivers(tree, min_length=min_length)

    # 初始化扰动坐标 = 网格坐标（蜿蜒在 Tile 级渲染时叠加）
    for node in tree.nodes:
        node.px = float(node.x)
        node.py = float(node.y)

    return tree


def _prune_short_rivers(tree: RiverTree, min_length: int = 5) -> None:
    """删除过短的河流支流（海边噪声）。

    从每个叶子（源头）沿 parent 向上走到根（入海口），
    总长 < min_length 的路径上的节点全部移除。
    共享节点（被其他更长路径使用）不受影响。

    Args:
        tree: 河流树（原地修改）。
        min_length: 最小河流长度（网格格数，默认 5 = 500m）。
    """
    n = len(tree.nodes)
    if n == 0:
        return

    # 找叶子：没有子节点的节点
    leaves = [i for i in range(n) if not tree.nodes[i].children]

    # 对每个叶子，计算到根（入海口）的路径长度
    # short_leaves = 需要剪除的叶子
    short_path_nodes: set[int] = set()

    for leaf_idx in leaves:
        path: list[int] = []
        idx = leaf_idx
        seen: set[int] = set()
        while idx >= 0 and idx < n and idx not in seen:
            seen.add(idx)
            path.append(idx)
            parent = tree.nodes[idx].parent
            if parent < 0 or parent >= n:
                break
            idx = parent

        if len(path) >= min_length:
            continue  # 够长，保留

        short_path_nodes.update(path)

    if not short_path_nodes:
        return

    # 检查哪些节点被长路径共享——共享节点的特征是 children 中至少有一个不在 short_path_nodes 中
    protected: set[int] = set()
    for i in short_path_nodes:
        node = tree.nodes[i]
        for child_idx in node.children:
            if child_idx < n and child_idx not in short_path_nodes:
                protected.add(i)
                break
        # 如果它的 parent 也不在 short_path_nodes 中，这个节点在长路径下游→保护
        if node.parent >= 0 and node.parent < n and node.parent not in short_path_nodes:
            protected.add(i)

    remove = short_path_nodes - protected
    if not remove:
        return

    # 重建节点列表，映射 old_idx → new_idx
    old_to_new: dict[int, int] = {}
    new_nodes: list[RiverNode] = []
    for i, node in enumerate(tree.nodes):
        if i not in remove:
            old_to_new[i] = len(new_nodes)
            new_nodes.append(node)

    # 更新 parent/children 引用
    for node in new_nodes:
        if node.parent >= 0 and node.parent in old_to_new:
            node.parent = old_to_new[node.parent]
        else:
            node.parent = -1
        new_children = []
        for c in node.children:
            if c in old_to_new:
                new_children.append(old_to_new[c])
        node.children = new_children

    tree.nodes = new_nodes
    tree.node_index = {}
    for i, node in enumerate(tree.nodes):
        tree.node_index[node.y * tree.width + node.x] = i

def _compute_strahler(tree: RiverTree) -> None:
    """在 RiverTree 上计算 Strahler 级别（拓扑正确版）。

    从源头（无子节点的叶）向河口（无父节点的根）传播：
      - 源头叶 = 1
      - 同级子节点交汇 → max_order + 1
      - 不同级 → 取较大者

    同时合并相邻的同级单链节点（简化树）。
    """
    n = len(tree.nodes)

    # 拓扑排序：从叶子（children 为空）向根（parent == -1）传播
    # indegree[i] = 有多少节点将 i 列为 child（即 i 有多少个 parent 引用）
    from collections import deque
    pending = [0] * n  # 待处理的子节点数
    for i, node in enumerate(tree.nodes):
        pending[i] = len(node.children)

    q = deque(i for i in range(n) if pending[i] == 0)

    # 暂存子节点的 Strahler 值
    child_orders: list[list[int]] = [[] for _ in range(n)]

    while q:
        idx = q.popleft()
        node = tree.nodes[idx]

        # 计算 Strahler
        if not child_orders[idx]:
            node.strahler = 1  # 源头
        else:
            orders = child_orders[idx]
            max_o = max(orders)
            # 至少两个子节点达到最高级 → +1
            if sum(1 for o in orders if o >= max_o) >= 2:
                node.strahler = max_o + 1
            else:
                node.strahler = max_o

        # 向上传播给父节点
        parent = node.parent
        if 0 <= parent < n:
            child_orders[parent].append(node.strahler)
            pending[parent] -= 1
            if pending[parent] == 0:
                q.append(parent)



def strahler_order(rivers: list[list[tuple[int, int]]]) -> list[int]:
    """计算河流的 Strahler 级别。

    规则：
      - 无支流的源头河段 = 1 级
      - 同级支流交汇 → 级别 +1
      - 不同级支流交汇 → 取较大者

    简化版：每条河流独立分级，按长度估算。

    Args:
        rivers: 河流列表。

    Returns:
        每条河流的 Strahler 级别列表。
    """
    if not rivers:
        return []

    max_len = max(len(r) for r in rivers) if rivers else 1
    orders: list[int] = []
    for river in rivers:
        # 按长度比例估算级别：短=1，长=2-4
        ratio = len(river) / max_len if max_len > 0 else 0
        if ratio < 0.1:
            order = 1
        elif ratio < 0.3:
            order = 2
        elif ratio < 0.6:
            order = 3
        else:
            order = 4
        orders.append(order)

    return orders


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
    result = dem[:]
    n = w * h

    # 归一化降雨量作为水流累积的源（全球平均 ~1000mm/yr → ~1.0）
    flow_source = [max(0.0, rainfall[i] / 1000.0) for i in range(n)]

    # 累积沉积量（正=沉积，负=侵蚀）
    sediment_net = [0.0] * n

    # 保存最后一轮的水文状态
    last_filled: list[float] = []
    last_directions: list[int] = []
    last_acc: list[float] = []

    for iteration in range(iterations):
        # 填洼（C 加速优先队列）
        filled = fill_depressions(result, w, h)

        # D8 + 累积 + 侵蚀 ← C 加速
        directions = _compute_d8_c(filled, w, h)
        acc = _flow_accumulation_c(directions, w, h, source=flow_source)

        # 保存最后一轮状态
        last_filled = filled
        last_directions = directions
        last_acc = acc

        # C 加速侵蚀 delta
        delta, _ = _erode_step_c(
            result, directions, acc, flow_source, w, h, erodibility)

        # 应用侵蚀 + 累积沉积 + 跟踪最大变化
        max_delta = 0.0
        for idx in range(n):
            result[idx] += delta[idx]
            sediment_net[idx] += delta[idx]
            abs_d = delta[idx] if delta[idx] >= 0 else -delta[idx]
            if abs_d > max_delta:
                max_delta = abs_d

        # 自适应收敛：地形变化微小时提前退出
        if iteration >= min_iterations and max_delta < tolerance:
            break

    return ErosionResult(
        dem=result,
        filled_dem=last_filled,
        flow_acc=last_acc,
        directions=last_directions,
        sediment_net=sediment_net,
    )


def extract_rivers_dinf(
    angles: list[float],
    acc: list[float],
    w: int, h: int,
    *,
    threshold: float = 50.0,
) -> list[list[tuple[int, int]]]:
    """D∞ 河流追踪：沿连续角度场追踪河流，路径自然弯曲。

    从累积量 > threshold 的源头出发，沿 D∞ 角度追踪到汇点。

    Args:
        angles: D∞ 角度数组，-1=汇点。
        acc: 水流累积量。
        w, h: 网格尺寸。
        threshold: 河流阈值。

    Returns:
        河流坐标列表。
    """
    n = w * h
    traced = [False] * n
    rivers: list[list[tuple[int, int]]] = []

    # 从高累积量像素开始追踪
    sorted_indices = sorted(
        [i for i in range(n) if acc[i] >= threshold],
        key=lambda i: acc[i], reverse=True,
    )

    for start_idx in sorted_indices:
        if traced[start_idx]:
            continue

        # 只追踪陆地上的河流（海拔 > 0）
        # 用 dem 数组判断；这里需要传入 dem
        # 暂时从 acc 推断...

        river: list[tuple[int, int]] = []
        idx = start_idx

        while 0 <= idx < n and not traced[idx]:
            traced[idx] = True
            x, y = idx % w, idx // w
            river.append((x, y))

            ang = angles[idx]
            if ang < 0:
                break  # 汇点

            # 沿角度走一步
            step = 1.0
            nx = int(x + math.cos(ang) * step + 0.5)
            ny = int(y + math.sin(ang) * step * (-1) + 0.5)
            # 注意：角度 0=东(cos=1,sin=0)，π/2=南(sin=1)，但因为 Y 向下，
            # 需要翻转 sin 的符号。实际上 angles 已经按数学约定存储。
            # 在屏幕坐标中 Y 向下，所以南 = +sin
            nx = int(x + math.cos(ang) * step + 0.5)
            ny = int(y + math.sin(ang) * step + 0.5)

            if 0 <= nx < w and 0 <= ny < h:
                idx = ny * w + nx
            else:
                break

        if len(river) >= 2:
            rivers.append(river)

    return rivers


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
        from collections import deque
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


def hillslope_erosion(
    dem: list[float],
    w: int, h: int,
    *,
    iterations: int = 20,
    rate: float = 0.1,
) -> list[float]:
    """降雨驱动的山坡侵蚀——陡坡物质向下扩散，圆滑地形。

    每轮迭代：每个像素将其一小部分海拔差分配给更低的邻居。
    效果：削峰填谷，尖峰变圆，坡面变缓。

    Args:
        dem: 行优先海拔数组。
        w: 宽度。
        h: 高度。
        iterations: 迭代轮数（越多越平滑）。
        rate: 扩散速率 [0-1]（越大越快）。

    Returns:
        侵蚀后的海拔数组。
    """
    result = dem[:]
    n = w * h

    for _ in range(iterations):
        delta = _hillslope_step_c(result, w, h, rate)
        # 应用
        for i in range(n):
            result[i] += delta[i]

    return result


def carve_rivers(
    dem: list[float],
    w: int, h: int,
    *,
    threshold: float = 30.0,
    depth_scale: float = 50.0,
    width: int = 3,
) -> list[float]:
    """沿河流网络雕刻河道。

    使用 D∞ 流向获得更自然的河道路径。

    Args:
        dem: 行优先海拔数组。
        w: 宽度。
        h: 高度。
        threshold: 河流提取阈值。
        depth_scale: 河道深度缩放系数（越大越深）。
        width: 河道半宽（像素），1=仅河床，2=含河岸。

    Returns:
        雕刻后的海拔数组（新列表）。
    """
    # 填洼 + D8 流向 + 累积
    filled = fill_depressions(dem, w, h)
    directions = compute_d8(filled, w, h)
    acc = flow_accumulation(directions, w, h)

    # D8 河流追踪
    rivers = extract_rivers(directions, acc, w, h, threshold=threshold)

    # 标记河流像素及其流量
    max_acc = max(acc) if acc else 1.0
    river_depth: dict[int, float] = {}
    for river in rivers:
        for x, y in river:
            idx = y * w + x
            # 深度 = log(流量) 缩放
            flow = acc[idx] / max_acc  # [0, 1]
            depth = math.log(1.0 + flow * 10.0) * depth_scale
            river_depth[idx] = max(river_depth.get(idx, 0.0), depth)

    # 应用雕刻（仅陆地，河床压到负海拔 → 渲染蓝色水体）
    result = dem[:]

    for idx, depth in river_depth.items():
        # 只雕刻陆地
        if result[idx] <= 0:
            continue

        x, y = idx % w, idx // w
        # 河床下切
        result[idx] -= depth

        # 降低两岸（也仅陆地）
        for dy in range(-width, width + 1):
            for dx in range(-width, width + 1):
                if dx == 0 and dy == 0:
                    continue
                d = max(abs(dx), abs(dy))
                nx, ny = x + dx, y + dy
                if 0 <= nx < w and 0 <= ny < h:
                    ni = ny * w + nx
                    if result[ni] > 0:  # 仅陆地
                        bank_factor = 0.5 / (1.0 + d)
                        result[ni] -= depth * bank_factor

    return result


__all__ = [
    "ErosionResult",
    "RiverNode",
    "RiverTree",
    "LakeBasin",
    "HydrologyData",
    "fill_depressions",
    "compute_d8",
    "compute_dinf",
    "flow_accumulation",
    "flow_accumulation_dinf",
    "extract_rivers",
    "extract_rivers_dinf",
    "build_river_tree",
    "extract_lake_basins",
    "strahler_order",
    "erode",
    "find_lakes",
    "hillslope_erosion",
    "carve_rivers",
    "compute_river_width",
]
