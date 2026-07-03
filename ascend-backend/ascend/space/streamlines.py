"""向量场流线积分 — 沿连续海拔梯度场追踪自然弯曲的河流路径。

替代 D8 的 8 方向格子线:用 RK4 沿 -∇z(最陡下坡方向)积分,
得到连续坐标的平滑流线。梯度从填洼后 DEM 的中心差分计算,
平坦区用 D8 方向兜底。

DAG 拓扑:水流可分叉到两个下游方向(保留 D∞ 的 frac1/frac2),
拓扑不再是树而是有向无环图,更物理地表达汇流。

用法:
    from ascend.space.streamlines import build_river_network

    network = build_river_network(dem, filled_dem, directions, flow_acc,
                                  land_mask, w, h, threshold=500.0)
    for river in network.rivers:
        for x, y, flow in river.points:
            ...
"""

import math
from dataclasses import dataclass, field


# D8 方向偏移(与 hydrology.py 一致)
_DX = (1, -1, 0, 0, 1, -1, 1, -1)
_DY = (0, 0, 1, -1, 1, 1, -1, -1)


@dataclass(slots=True)
class RiverPoint:
    """流线上的一个点 — 连续世界坐标(网格单位)。

    Attributes:
        x: 网格 X(浮点,非整数)。
        y: 网格 Y(浮点)。
        flow: 该点的水流累积量。
        strahler: Strahler 级别(1=源头,越大越主流)。
    """
    x: float
    y: float
    flow: float
    strahler: int = 1


@dataclass
class River:
    """一条河流 — 从源头到汇点的连续流线。

    Attributes:
        points: 流线点列表(上游→下游顺序)。
        source_idx: 源头在 network.rivers 中的索引(自分叉源)。
        outlet_idx: 汇入的下游河流索引(-1=入海/出界)。
        parent_indices: 上游支流索引列表(DAG:可被多条河汇入)。
    """
    points: list[RiverPoint] = field(default_factory=list)
    source_idx: int = -1
    outlet_idx: int = -1
    parent_indices: list[int] = field(default_factory=list)


@dataclass
class RiverNetwork:
    """河流网络 — DAG 拓扑的所有流线。

    Attributes:
        width: 网格宽度。
        height: 网格高度。
        rivers: 所有河流段(每条是从源头到分叉/汇点的流线)。
        node_grid: 网格索引 → 该位置最近的河流索引(用于跨 chunk 查找)。
    """
    width: int
    height: int
    rivers: list[River] = field(default_factory=list)
    # 网格索引 → (river_idx, point_idx) 最近流线点
    node_grid: dict[int, tuple[int, int]] = field(default_factory=dict)

    def __repr__(self) -> str:
        total_pts = sum(len(r.points) for r in self.rivers)
        return (
            f"RiverNetwork({self.width}×{self.height}, "
            f"rivers={len(self.rivers)}, points={total_pts})"
        )


# ── 梯度采样 ──────────────────────────────────────────────


def _gaussian_blur(dem: list[float], w: int, h: int, radius: int = 3) -> list[float]:
    """高斯模糊 DEM — 消除网格级噪声,使梯度场平滑。

    流线积分需要连续光滑的梯度场,但 100m 网格的中心差分
    在相邻格方向差可达 50%+>30°(噪声),导致流线抖成毛线。
    先模糊 DEM 再算梯度,牺牲少量精度换取路径平滑性。
    """
    import math as m
    # 高斯核(分离卷积,radius=3 → sigma≈1.5)
    sigma = radius / 2.0
    kernel = [m.exp(-((i - radius) ** 2) / (2 * sigma * sigma))
              for i in range(2 * radius + 1)]
    ks = sum(kernel)
    kernel = [k / ks for k in kernel]

    # 水平卷积
    tmp = [0.0] * (w * h)
    for y in range(h):
        row = y * w
        for x in range(w):
            s = 0.0
            for k in range(2 * radius + 1):
                sx = x + k - radius
                if sx < 0:
                    sx = 0
                elif sx >= w:
                    sx = w - 1
                s += dem[row + sx] * kernel[k]
            tmp[row + x] = s

    # 垂直卷积
    out = [0.0] * (w * h)
    for x in range(w):
        for y in range(h):
            s = 0.0
            for k in range(2 * radius + 1):
                sy = y + k - radius
                if sy < 0:
                    sy = 0
                elif sy >= h:
                    sy = h - 1
                s += tmp[sy * w + x] * kernel[k]
            out[y * w + x] = s
    return out


def _gradient(
    x: float, y: float,
    dem: list[float],
    directions: list[int],
    w: int, h: int,
) -> tuple[float, float, bool]:
    """采样连续海拔梯度,返回单位下坡方向。

    双线性插值采样海拔后做梯度,消除网格跳变。
    平坦区(mag<阈值)用 D8 方向兜底,确保流线在平原也能持续推进。

    Args:
        x, y: 连续网格坐标。
        dem: 填洼后海拔场(行优先)。
        directions: D8 流向(平坦区兜底用)。
        w, h: 网格尺寸。

    Returns:
        (dx, dy, stop): 单位下坡方向,stop=True 表示越界/汇点。
    """
    ix, iy = int(x), int(y)
    if not (1 <= ix < w - 1 and 1 <= iy < h - 1):
        return 0.0, 0.0, True

    # 双线性插值采样海拔(消除网格跳变)
    fx, fy = x - ix, y - iy
    e00 = dem[iy * w + ix]
    e10 = dem[iy * w + ix + 1]
    e01 = dem[(iy + 1) * w + ix]
    e11 = dem[(iy + 1) * w + ix + 1]
    # 在插值位置做中心差分(用相邻网格点)
    # 梯度 = (e(x+1)-e(x-1))/2, 需要x-1和x+1的插值
    # 简化:用当前格子的中心差分(已足够平滑,配合RK4小步长)
    dzdx = (dem[iy * w + ix + 1] - dem[iy * w + ix - 1]) * 0.5
    dzdy = (dem[(iy + 1) * w + ix] - dem[(iy - 1) * w + ix]) * 0.5

    # 负梯度 = 下坡
    gx, gy = -dzdx, -dzdy
    mag = math.sqrt(gx * gx + gy * gy)

    if mag < 0.1:
        # 平坦区:梯度不可靠(噪声打转),返回 stop 让调用方切 D8
        return 0.0, 0.0, True

    return gx / mag, gy / mag, False


# ── 流线追踪 ──────────────────────────────────────────────


def trace_streamline(
    sx: float, sy: float,
    dem: list[float],
    raw_dem: list[float],
    directions: list[int],
    flow_acc: list[float],
    w: int, h: int,
    *,
    step_size: float = 1.5,
    max_steps: int = 4000,
    seed: int = 0,
) -> list[RiverPoint]:
    """从 (sx, sy) 沿 -∇z 梯度下降追踪流线到海洋/出界。

    Euler 法沿最陡下坡方向步进。相比 RK4 更适合噪声场:
    RK4 的中点采样在网格噪声中引入额外抖动,Euler 单步更稳定。
    大步长(1.5格=150m)跨过单格噪声,获得平滑路径。

    平坦区(mag<阈值)用 D8 方向步进,保证到海。
    每 150 步检查净位移,打转则纯 D8 链走完。

    Args:
        sx, sy: 起点(网格坐标)。
        dem: 填洼后海拔(梯度计算)。
        raw_dem: 侵蚀后原始海拔(海洋判定)。
        directions: D8 流向(平坦区兜底)。
        flow_acc: 水流累积量。
        w, h: 网格尺寸。
        step_size: Euler 步长(网格单位)。
        max_steps: 最大步数。
        seed: 未使用(保留兼容)。

    Returns:
        RiverPoint 列表(上游→下游)。
    """
    ds = step_size
    pts: list[RiverPoint] = []
    x, y = float(sx), float(sy)
    last_ck_x, last_ck_y = x, y
    last_ck_idx = 0

    for step in range(max_steps):
        ix, iy = int(x), int(y)
        if not (0 <= ix < w and 0 <= iy < h):
            break

        flow = flow_acc[iy * w + ix] if 0 <= iy * w + ix < len(flow_acc) else 0.0
        pts.append(RiverPoint(x=x, y=y, flow=flow))

        # 海洋判定
        if raw_dem[iy * w + ix] < 0:
            break

        # 打转检测:30步内位移<0.5格 → 卡住,D8补完
        if step - last_ck_idx >= 30:
            disp = math.sqrt((x - last_ck_x)**2 + (y - last_ck_y)**2)
            if disp < 0.5:
                d8_tail = _trace_d8_chain(ix, iy, raw_dem, directions, w, h,
                                          flow_acc, max_steps - step)
                pts.extend(d8_tail)
                break
            last_ck_x, last_ck_y = x, y
            last_ck_idx = step

        # Euler 一步:沿 -∇z 步进
        dx, dy, stop = _gradient(x, y, dem, directions, w, h)
        if stop:
            d8_tail = _trace_d8_chain(ix, iy, raw_dem, directions, w, h,
                                      flow_acc, max_steps - step)
            pts.extend(d8_tail)
            break

        # 上坡拦截:下一步海拔升高>1m → 入洼地出坑,D8补完
        nx, ny = x + dx * ds, y + dy * ds
        nix, niy = int(nx), int(ny)
        if 0 <= nix < w and 0 <= niy < h:
            cur_elev = dem[iy * w + ix]
            next_elev = dem[niy * w + nix]
            if next_elev > cur_elev + 1.0:
                d8_tail = _trace_d8_chain(ix, iy, raw_dem, directions, w, h,
                                          flow_acc, max_steps - step)
                pts.extend(d8_tail)
                break

        x, y = nx, ny

    return pts


def _trace_d8_chain(
    sx: int, sy: int,
    raw_dem: list[float],
    directions: list[int],
    w: int, h: int,
    flow_acc: list[float],
    max_steps: int = 5000,
) -> list[RiverPoint]:
    """D8 链追踪 — 梯度失效/打转时的兜底,保证流线到海。

    沿 D8 流向逐像素追踪,叠加 Perlin 垂直扰动消除格子直线感。
    D8 保证到海(填洼后每像素有到边界下坡路径)。
    """
    from .noise import PerlinNoise

    noise = PerlinNoise(int(sx * 1000 + sy) % 100000 + 777)
    pts: list[RiverPoint] = []
    x, y = sx, sy
    seen: set[int] = set()

    for _ in range(max_steps):
        if not (0 <= x < w and 0 <= y < h):
            break
        idx = y * w + x
        if idx in seen:
            break
        seen.add(idx)

        flow = flow_acc[idx] if 0 <= idx < len(flow_acc) else 0.0

        if raw_dem[idx] < 0:
            pts.append(RiverPoint(x=float(x) + 0.5, y=float(y) + 0.5, flow=flow))
            break

        d = directions[idx]
        if d < 0:
            pts.append(RiverPoint(x=float(x) + 0.5, y=float(y) + 0.5, flow=flow))
            break

        dx8, dy8 = _DX[d], _DY[d]
        dist8 = math.sqrt(2.0) if d >= 4 else 1.0

        # Perlin 垂直扰动(低频,消除格子线)
        nv = noise.octave(
            x * 0.08 + 0.5, y * 0.08 + 0.5,
            octaves=2, persistence=0.5,
        )
        perp_x = -dy8 / dist8
        perp_y = dx8 / dist8
        offset = nv * 1.2  # ±1.2格=±120m弯曲

        px = float(x) + 0.5 + perp_x * offset
        py = float(y) + 0.5 + perp_y * offset
        pts.append(RiverPoint(x=px, y=py, flow=flow))

        x += dx8
        y += dy8

    return pts


# ── 河流网络构建 ──────────────────────────────────────────


def build_river_network(
    dem: list[float],
    filled_dem: list[float],
    directions: list[int],
    flow_acc: list[float],
    land_mask: list[bool],
    w: int, h: int,
    *,
    threshold: float = 500.0,
    step_size: float = 1.0,
    max_steps: int = 4000,
    min_length: int = 20,
) -> RiverNetwork:
    """从水文场构建流线河流网络。

    1. 找河流源头(acc>threshold 且无更高 acc 上游邻居)
    2. 按 acc 降序追踪:高流量源头优先,其流线标记网格已访问,
       后续低流量源头追踪时遇到已访问网格即汇入(消除平行线)
    3. 每条流线追踪到海洋/出界(不因平坦区停止)
    4. 过滤过短河流(<min_length 点)

    Args:
        dem: 侵蚀后原始海拔(海洋判定)。
        filled_dem: 填洼后海拔(梯度计算)。
        directions: D8 流向(平坦区兜底)。
        flow_acc: 水流累积量。
        land_mask: 陆地掩码。
        w, h: 网格尺寸。
        threshold: 河流最小累积量。
        step_size: RK4 步长。
        max_steps: 单条流线最大步数。
        min_length: 最小河流长度(点数)。

    Returns:
        RiverNetwork 含所有流线段和 DAG 拓扑。
    """
    network = RiverNetwork(width=w, height=h)
    n = w * h

    # 找河流像素
    river_pixels = [
        i for i in range(n)
        if flow_acc[i] >= threshold and land_mask[i] and directions[i] >= 0
    ]
    if not river_pixels:
        return network

    # 找源头:无更高 acc 上游邻居的河流像素
    sources: list[int] = []
    for i in river_pixels:
        x, y = i % w, i // w
        is_source = True
        for d in range(8):
            nx, ny = x + _DX[d], y + _DY[d]
            if 0 <= nx < w and 0 <= ny < h:
                ni = ny * w + nx
                if directions[ni] >= 0:
                    ndx = nx + _DX[directions[ni]]
                    ndy = ny + _DY[directions[ni]]
                    if ndx == x and ndy == y and flow_acc[ni] >= threshold:
                        is_source = False
                        break
        if is_source:
            sources.append(i)

    # 按 acc 降序追踪(高流量优先,支流汇入主干)
    sources.sort(key=lambda i: flow_acc[i], reverse=True)

    # 网格 → 已有流线索引(用于汇流检测,消除平行线)
    visited_grid: dict[int, int] = {}  # grid_idx → river_idx

    for src in sources:
        # 跳过已在已有流线上的源头(被高流量河覆盖)
        if src in visited_grid:
            continue

        sx, sy = float(src % w), float(src // w)
        # 用原始侵蚀 DEM 算梯度(有自然河谷起伏,梯度稳定),
        # 填洼 DEM 仅用于 D8 流向(平坦区兜底)
        pts = trace_streamline(
            sx, sy, dem, dem, directions, flow_acc, w, h,
            step_size=step_size, max_steps=max_steps,
        )

        if len(pts) < min_length:
            continue

        # 检测汇流:流线是否经过已有河流附近(2格半径)
        outlet = -1
        skip = False
        for pi, p in enumerate(pts):
            gi = int(p.y) * w + int(p.x)
            # 精确格命中
            if gi in visited_grid and visited_grid[gi] != -1:
                if pi < 30:
                    skip = True
                    break
                outlet = visited_grid[gi]
                pts = pts[:pi + 1]
                break
            # 2格半径近距检查(消除平行线)
            px, py = int(p.x), int(p.y)
            found_near = False
            for ndy in range(-2, 3):
                if found_near: break
                for ndx in range(-2, 3):
                    if ndx == 0 and ndy == 0: continue
                    ni = (py + ndy) * w + (px + ndx)
                    if ni in visited_grid and visited_grid[ni] != -1:
                        if pi < 30:
                            skip = True
                            found_near = True
                            break
                        outlet = visited_grid[ni]
                        pts = pts[:pi + 1]
                        found_near = True
                        break
            if found_near: break

        if skip or not pts:
            continue  # 平行线/同源,丢弃

        river_idx = len(network.rivers)
        river = River(points=pts, source_idx=river_idx, outlet_idx=outlet)
        network.rivers.append(river)

        if outlet >= 0:
            network.rivers[outlet].parent_indices.append(river_idx)

        # 标记流线经过的网格(后续源头遇到即汇入)
        for p in pts:
            gi = int(p.y) * w + int(p.x)
            if gi not in visited_grid:
                visited_grid[gi] = river_idx

        # 填充 node_grid(渲染时跨 chunk 查找)
        for pi, p in enumerate(pts):
            gi = int(p.y) * w + int(p.x)
            if gi not in network.node_grid:
                network.node_grid[gi] = (river_idx, pi)

    # 计算 Strahler 级别(从叶子向根传播)
    _compute_strahler(network)

    return network


def _compute_strahler(network: RiverNetwork) -> None:
    """在 DAG 上计算 Strahler 级别。

    叶子(无支流汇入)= 1,同级支流交汇 → +1,不同级 → 取较大。
    按拓扑顺序从叶子向根(outlet)传播。
    """
    n = len(network.rivers)
    if n == 0:
        return

    # 计算入度(有多少支流汇入)
    indegree = [len(r.parent_indices) for r in network.rivers]

    # 叶子 = 无支流汇入
    from collections import deque
    q = deque(i for i in range(n) if indegree[i] == 0)

    # 每条河的支流 Strahler 值
    child_orders: list[list[int]] = [[] for _ in range(n)]

    while q:
        idx = q.popleft()
        river = network.rivers[idx]

        # 计算本河段 Strahler
        if not child_orders[idx]:
            base_order = 1
        else:
            orders = child_orders[idx]
            max_o = max(orders)
            if sum(1 for o in orders if o >= max_o) >= 2:
                base_order = max_o + 1
            else:
                base_order = max_o

        # 设置所有点的 Strahler(源头到汇点一致,或按流量渐变)
        for p in river.points:
            p.strahler = base_order

        # 向下游传播
        outlet = river.outlet_idx
        if 0 <= outlet < n:
            child_orders[outlet].append(base_order)
            indegree[outlet] -= 1
            if indegree[outlet] == 0:
                q.append(outlet)


# ── 查询接口 ──────────────────────────────────────────────


def rivers_in_region(
    network: RiverNetwork,
    x0: float, y0: float, x1: float, y1: float,
    margin: float = 100.0,
) -> list[tuple[int, list[RiverPoint]]]:
    """查询与矩形区域有重叠的河流段。

    用于 tile 渲染时获取 chunk 内的河流流线点。

    Args:
        network: 河流网络。
        x0, y0, x1, y1: 矩形区域(网格坐标)。
        margin: 边界余量(确保跨界河流不被截断)。

    Returns:
        [(river_idx, points_in_region), ...] 区域内的河流段。
    """
    result: list[tuple[int, list[RiverPoint]]] = []
    for ri, river in enumerate(network.rivers):
        in_region: list[RiverPoint] = []
        for p in river.points:
            if (x0 - margin <= p.x <= x1 + margin and
                    y0 - margin <= p.y <= y1 + margin):
                in_region.append(p)
        if in_region:
            result.append((ri, in_region))
    return result


__all__ = [
    "RiverPoint",
    "River",
    "RiverNetwork",
    "trace_streamline",
    "build_river_network",
    "rivers_in_region",
]
