"""河流网络生成 — 三级方向场 RK4 积分 + Chaikin 平滑。

数据全部来自 erode() 的输出（dem / directions / flow_acc）。

核心思路:
  1. Dijkstra 从所有海洋格反向计算"到海最低代价场" dist[]:
       cost(q) = max(0, dem[q]) + 1 - 1.5 * log(1 + flow_acc[q])
     —— 高海拔贵、高流量便宜。作为全局可达性约束，
        保证追踪终能到海、不跨分水岭。
  2. 从源头（flow_acc >= threshold 且无更高 acc 上游）沿三级方向场 RK4 积分:
       a. dem 平滑梯度 -∇z（σ 高斯模糊后）—— 山谷跟随，自然弯曲
          仅当与 dist 下降方向同向（点积>0）时启用，否则视为跨分水岭
       b. flow_acc 平滑场 +梯度 —— 指向下游高流量主流通道，
          等值线跟随真实汇水网络，平原区有自然弯曲
          必须与 dist 下降同向，否则在局部极大绕圈打转 → 退到 dist
       c. dist 场 -∇dist —— 纯几何兜底，保证到海
  3. Chaikin 切角平滑 2 轮 → 消除格子线。

设计取舍:
  - 纯 dem 梯度：山地弯曲好，但会跨分水岭、平原易断头
  - 纯 dist 追踪：全局可达，但平原 dist 退化为几何距离场 → 平行直线
  - 纯 flow_acc：平原有弯曲，但局部极大处会绕圈打转
  - 三级混合：山地 dem 弯曲，平原 flow_acc 弯曲，分水岭/兜底靠 dist

用法:
    from ascend.space.streamlines import build_river_network
    network = build_river_network(dem, filled_dem, directions, flow_acc,
                                  land_mask, w, h, threshold=500.0)
"""

import heapq
import math
from collections import deque
from dataclasses import dataclass, field


_DX = (1, -1, 0, 0, 1, -1, 1, -1)
_DY = (0, 0, 1, -1, 1, 1, -1, -1)


@dataclass(slots=True)
class RiverPoint:
    x: float
    y: float
    flow: float
    strahler: int = 1


@dataclass
class River:
    points: list[RiverPoint] = field(default_factory=list)
    source_idx: int = -1
    outlet_idx: int = -1
    parent_indices: list[int] = field(default_factory=list)


@dataclass
class RiverNetwork:
    width: int
    height: int
    rivers: list[River] = field(default_factory=list)
    node_grid: dict[int, tuple[int, int]] = field(default_factory=dict)

    def __repr__(self) -> str:
        total_pts = sum(len(r.points) for r in self.rivers)
        return (
            f"RiverNetwork({self.width}×{self.height}, "
            f"rivers={len(self.rivers)}, points={total_pts})"
        )


# ═══════════════════════════════════════════════════════════
# Dijkstra 山谷代价场
# ═══════════════════════════════════════════════════════════


def _dijkstra_to_ocean(
    dem: list[float],
    flow_acc: list[float],
    w: int, h: int,
) -> list[float]:
    """从所有海洋格反向 Dijkstra，计算每个格到海的最低累积代价。

    cost 进入格 q = max(0, dem[q]) + 1 - 0.4 * log(1 + flow_acc[q])，
    下限 0.1 保证非负。海洋格 (dem < 0) 代价 0，作为多源起点。

    Returns:
        dist[i] = 从格 i 到最近海洋格的最低累积代价；海洋格为 0。
        无路径可达的格为 inf。
    """
    n = w * h
    INF = float("inf")
    dist: list[float] = [INF] * n
    heap: list[tuple[float, int]] = []
    for i in range(n):
        if dem[i] < 0:
            dist[i] = 0.0
            heapq.heappush(heap, (0.0, i))

    while heap:
        d, i = heapq.heappop(heap)
        if d > dist[i]:
            continue
        x, y = i % w, i // w
        for k in range(8):
            nx = x + _DX[k]
            ny = y + _DY[k]
            if not (0 <= nx < w and 0 <= ny < h):
                continue
            ni = ny * w + nx
            elev_cost = max(0.0, dem[ni]) + 1.0
            flow_bonus = 1.5 * math.log1p(flow_acc[ni])
            step = elev_cost - flow_bonus
            if step < 0.1:
                step = 0.1
            nd = d + step
            if nd < dist[ni]:
                dist[ni] = nd
                heapq.heappush(heap, (nd, ni))
    return dist


# ═══════════════════════════════════════════════════════════
# 源头检测
# ═══════════════════════════════════════════════════════════


def _find_sources(
    flow_acc: list[float],
    directions: list[int],
    land_mask: list[bool],
    w: int, h: int,
    threshold: float,
) -> list[int]:
    """找河流源头：acc >= threshold 且无更高 acc 上游流入的格子。

    按流量降序排序——高流量优先追踪，支流自然汇入主流。
    """
    sources: list[int] = []
    for i in range(w * h):
        if not (flow_acc[i] >= threshold and land_mask[i] and directions[i] >= 0):
            continue
        x, y = i % w, i // w
        is_source = True
        for d in range(8):
            nx = x + _DX[d]
            ny = y + _DY[d]
            if not (0 <= nx < w and 0 <= ny < h):
                continue
            ni = ny * w + nx
            if directions[ni] < 0:
                continue
            ndx = nx + _DX[directions[ni]]
            ndy = ny + _DY[directions[ni]]
            if ndx == x and ndy == y and flow_acc[ni] >= threshold:
                is_source = False
                break
        if is_source:
            sources.append(i)
    sources.sort(key=lambda i: flow_acc[i], reverse=True)
    return sources


# ═══════════════════════════════════════════════════════════
# 场插值与平滑（连续坐标追踪的基础）
# ═══════════════════════════════════════════════════════════


def _gaussian_blur(arr: list[float], w: int, h: int,
                   sigma: float) -> list[float]:
    """可分离高斯模糊（σ 以格为单位）。

    dem 原始像素噪声会让梯度方向抖动；先平滑 σ≈2 格，
    让 -∇z 跟随宏观山谷趋势而非像素级噪声。
    """
    radius = max(1, int(3 * sigma))
    kernel = [math.exp(-0.5 * (i / sigma) ** 2) for i in range(-radius, radius + 1)]
    ks = sum(kernel)
    kernel = [k / ks for k in kernel]
    tmp = [0.0] * (w * h)
    for y in range(h):
        row = y * w
        for x in range(w):
            s = 0.0
            for i, k in enumerate(kernel):
                xi = x + i - radius
                if xi < 0:
                    xi = 0
                elif xi >= w:
                    xi = w - 1
                s += k * arr[row + xi]
            tmp[row + x] = s
    out = [0.0] * (w * h)
    for x in range(w):
        for y in range(h):
            s = 0.0
            for i, k in enumerate(kernel):
                yi = y + i - radius
                if yi < 0:
                    yi = 0
                elif yi >= h:
                    yi = h - 1
                s += k * tmp[yi * w + x]
            out[y * w + x] = s
    return out


def _bilinear(arr: list[float], x: float, y: float,
              w: int, h: int) -> float:
    """双线性插值采样，边界外按钳制到边缘格的值返回。"""
    ix, iy = int(x), int(y)
    if ix < 0:
        ix = 0
    elif ix >= w - 1:
        ix = w - 2
    if iy < 0:
        iy = 0
    elif iy >= h - 1:
        iy = h - 2
    fx, fy = x - ix, y - iy
    a = arr[iy * w + ix]
    b = arr[iy * w + ix + 1]
    c = arr[(iy + 1) * w + ix]
    d = arr[(iy + 1) * w + ix + 1]
    return ((1 - fx) * (1 - fy) * a + fx * (1 - fy) * b +
            (1 - fx) * fy * c + fx * fy * d)


def _neg_grad(x: float, y: float, arr: list[float],
              w: int, h: int, eps: float = 0.75) -> tuple[float, float]:
    """中心差分算 -∇arr（指向 arr 下降最快方向）。"""
    gx = (_bilinear(arr, x + eps, y, w, h) - _bilinear(arr, x - eps, y, w, h)) / (2 * eps)
    gy = (_bilinear(arr, x, y + eps, w, h) - _bilinear(arr, x, y - eps, w, h)) / (2 * eps)
    return -gx, -gy


def _flow_dir(x: float, y: float,
              smooth_dem: list[float],
              smooth_flow: list[float],
              dist: list[float],
              w: int, h: int,
              dem_min: float = 0.02,
              flow_min: float = 1e-3) -> tuple[float, float] | None:
    """混合方向场：dem 梯度优先，flow_acc 梯度次之，dist 兜底。

    分级策略:
      - dem 梯度强 且 与 dist 下降方向同向（点积>0）→ 跟 dem（山谷弯曲）
      - dem 梯度反向（跨分水岭）或太弱 → 跟 flow_acc 平滑场 +梯度
        （指向下游高流量主流，等值线跟随真实汇水网络，平原区有自然弯曲）
        flow_acc 方向也必须与 dist 下降同向，否则在局部极大绕圈打转 → 退 dist
      - flow_acc 也太弱 → 跟 dist（保证到海，纯几何兜底）
      - 全失效 → None（终止追踪）
    """
    gxd, gyd = _neg_grad(x, y, smooth_dem, w, h)
    md = math.hypot(gxd, gyd)
    gxf, gyf = _neg_grad(x, y, dist, w, h)
    mf = math.hypot(gxf, gyf)

    if md > dem_min and mf > 1e-6:
        if gxd * gxf + gyd * gyf > 0:
            return gxd / md, gyd / md
        # dem 跨分水岭 → 落到 flow_acc
    elif md > dem_min:
        return gxd / md, gyd / md

    # flow_acc +梯度（指向下游高流量通道）
    gfx = (_bilinear(smooth_flow, x + 0.75, y, w, h) -
           _bilinear(smooth_flow, x - 0.75, y, w, h)) / 1.5
    gfy = (_bilinear(smooth_flow, x, y + 0.75, w, h) -
           _bilinear(smooth_flow, x, y - 0.75, w, h)) / 1.5
    mfa = math.hypot(gfx, gfy)
    if mfa > flow_min and mf > 1e-6:
        # flow_acc 方向必须与 dist 下降同向，否则会在局部极大绕圈打转
        if gfx * gxf + gfy * gyf > 0:
            return gfx / mfa, gfy / mfa
        return gxf / mf, gyf / mf
    if mfa > flow_min:
        return gfx / mfa, gfy / mfa
    if mf > 1e-6:
        return gxf / mf, gyf / mf
    return None


def _rk4_step(x: float, y: float,
              smooth_dem: list[float],
              smooth_flow: list[float],
              dist: list[float],
              w: int, h: int,
              ds: float) -> tuple[float, float] | None:
    """RK4 积分一步沿 _flow_dir 方向场。"""
    k1 = _flow_dir(x, y, smooth_dem, smooth_flow, dist, w, h)
    if k1 is None:
        return None
    k2 = _flow_dir(x + 0.5 * ds * k1[0], y + 0.5 * ds * k1[1],
                   smooth_dem, smooth_flow, dist, w, h)
    if k2 is None:
        return None
    k3 = _flow_dir(x + 0.5 * ds * k2[0], y + 0.5 * ds * k2[1],
                   smooth_dem, smooth_flow, dist, w, h)
    if k3 is None:
        return None
    k4 = _flow_dir(x + ds * k3[0], y + ds * k3[1],
                   smooth_dem, smooth_flow, dist, w, h)
    if k4 is None:
        return None
    return (
        x + ds * (k1[0] + 2 * k2[0] + 2 * k3[0] + k4[0]) / 6.0,
        y + ds * (k1[1] + 2 * k2[1] + 2 * k3[1] + k4[1]) / 6.0,
    )


# ═══════════════════════════════════════════════════════════
# 单条河流追踪 + 平滑
# ═══════════════════════════════════════════════════════════


def _trace_downstream(
    src: int,
    dem: list[float],
    smooth_dem: list[float],
    smooth_flow: list[float],
    dist: list[float],
    w: int, h: int,
    *,
    max_steps: int = 4000,
    step_size: float = 0.7,
) -> list[tuple[float, float]]:
    """从源头沿混合方向场 RK4 积分追踪到海。

    返回连续坐标点列表（未做 Chaikin 平滑）。
    终止条件：进入海洋（dem<0）、越界、方向场失效、或步数耗尽。
    """
    pts: list[tuple[float, float]] = []
    x = float(src % w)
    y = float(src // w)
    for _ in range(max_steps):
        ix, iy = int(x), int(y)
        if not (0 <= ix < w and 0 <= iy < h):
            break
        pts.append((x, y))
        if dem[iy * w + ix] < 0:
            break
        step = _rk4_step(x, y, smooth_dem, smooth_flow, dist, w, h, step_size)
        if step is None:
            break
        nx, ny = step
        if math.hypot(nx - x, ny - y) < 1e-4:
            break
        x, y = nx, ny
    return pts


def _chaikin(points: list[tuple[float, float]],
             iters: int = 2) -> list[tuple[float, float]]:
    """Chaikin 切角平滑：每轮把每条边切成 1/4-3/4 两段，角点内收。

    保留首尾点，2 轮即可消除格子线感。
    """
    pts = points
    for _ in range(iters):
        if len(pts) < 3:
            break
        out = [pts[0]]
        for i in range(len(pts) - 1):
            p0, p1 = pts[i], pts[i + 1]
            out.append((0.75 * p0[0] + 0.25 * p1[0],
                        0.75 * p0[1] + 0.25 * p1[1]))
            out.append((0.25 * p0[0] + 0.75 * p1[0],
                        0.25 * p0[1] + 0.75 * p1[1]))
        out.append(pts[-1])
        pts = out
    return pts


def trace_streamline(
    sx: float, sy: float,
    dem: list[float],
    smooth_dem: list[float],
    smooth_flow: list[float],
    flow_acc: list[float],
    dist: list[float],
    w: int, h: int,
    *,
    max_steps: int = 4000,
    step_size: float = 0.7,
    chaikin_iters: int = 2,
) -> list[RiverPoint]:
    """追踪单条河流：RK4 沿混合方向场积分 + Chaikin 平滑。

    Args:
        sx, sy: 源头坐标（格单位）。
        dem: 侵蚀后海拔数组（用于判断到海）。
        smooth_dem: dem 高斯平滑后的场（驱动山谷弯曲）。
        smooth_flow: flow_acc 高斯平滑后的场（平原区指向下游主流）。
        flow_acc: 水流累积量数组（采样 RiverPoint.flow）。
        dist: _dijkstra_to_ocean 返回的到海代价场（约束+兜底）。
        w, h: 网格尺寸。
        max_steps: 最大追踪步数。
        step_size: RK4 步长（格单位）。
        chaikin_iters: Chaikin 平滑轮数。

    Returns:
        RiverPoint 列表（连续坐标，flow 已采样）。
    """
    src = int(sy) * w + int(sx)
    raw = _trace_downstream(src, dem, smooth_dem, smooth_flow, dist, w, h,
                            max_steps=max_steps, step_size=step_size)
    smooth = _chaikin(raw, chaikin_iters)
    return [
        RiverPoint(
            x=p[0],
            y=p[1],
            flow=flow_acc[max(0, min(w * h - 1, int(p[1]) * w + int(p[0])))],
        )
        for p in smooth
    ]


# ═══════════════════════════════════════════════════════════
# 汇流合并
# ═══════════════════════════════════════════════════════════


def _merge_into_existing(
    pts: list[RiverPoint],
    visited: dict[int, int],
    w: int,
    merge_radius: int,
    min_length: int,
) -> tuple[int, bool]:
    """检测流线是否经过已有河流 merge_radius 格内。

    - 前段（< min_length/2 步）碰到 → 丢弃（消除平行短支流）
    - 后段碰到 → 截断，设 outlet 汇入已有河流

    Returns:
        (outlet_idx, skip)。outlet=-1 表示独立入海，skip=True 表示丢弃。
    """
    outlet = -1
    skip = False
    early_drop = max(min_length // 2, 15)
    for pi, p in enumerate(pts):
        px, py = int(p.x), int(p.y)
        found = False
        for ndy in range(-merge_radius, merge_radius + 1):
            if found:
                break
            for ndx in range(-merge_radius, merge_radius + 1):
                if ndx == 0 and ndy == 0:
                    continue
                ni = (py + ndy) * w + (px + ndx)
                hit = visited.get(ni, -1)
                if hit != -1:
                    if pi < early_drop:
                        skip = True
                    else:
                        outlet = hit
                        del pts[pi + 1:]
                        pts.append(RiverPoint(
                            x=px + ndx + 0.0,
                            y=py + ndy + 0.0,
                            flow=p.flow,
                        ))
                    found = True
                    break
        if found:
            break
    return outlet, skip


def _commit_river(
    network: RiverNetwork,
    pts: list[RiverPoint],
    outlet: int,
    visited: dict[int, int],
    w: int,
) -> None:
    """把一条河流提交到网络，标记占用格子。"""
    river_idx = len(network.rivers)
    river = River(points=pts, source_idx=river_idx, outlet_idx=outlet)
    network.rivers.append(river)
    if outlet >= 0:
        network.rivers[outlet].parent_indices.append(river_idx)
    for p in pts:
        gi = int(p.y) * w + int(p.x)
        if gi not in visited:
            visited[gi] = river_idx
        if gi not in network.node_grid:
            network.node_grid[gi] = (river_idx, 0)


# ═══════════════════════════════════════════════════════════
# 网络构建
# ═══════════════════════════════════════════════════════════


def build_river_network(
    dem: list[float],
    filled_dem: list[float],
    directions: list[int],
    flow_acc: list[float],
    land_mask: list[bool],
    w: int, h: int,
    *,
    threshold: float = 500.0,
    min_length: int = 20,
    merge_radius: int = 2,
    chaikin_iters: int = 2,
    max_steps: int = 4000,
    step_size: float = 0.7,
    sigma: float = 1.5,
) -> RiverNetwork:
    """从水文场构建河流网络（三级方向场 RK4 + Chaikin 平滑）。

    Args:
        dem: 侵蚀后海拔数组（m），dem < 0 视为海洋。
        filled_dem: 填洼后海拔（保留接口兼容，本算法未使用）。
        directions: D8 流向数组（-1=汇点），用于源头检测。
        flow_acc: 水流累积量数组。
        land_mask: 陆地掩码（True=陆地）。
        w, h: 网格尺寸。
        threshold: 河流源头最小累积量。
        min_length: 最小河流长度（格）。
        merge_radius: 汇流检测半径（格），碰到已有河流即汇入。
        chaikin_iters: Chaikin 平滑轮数。
        max_steps: 单条河最大追踪步数。
        step_size: RK4 积分步长（格单位）。
        sigma: dem 与 flow_acc 高斯平滑 σ（格），越大越跟随宏观趋势。

    Returns:
        RiverNetwork，rivers 已按流量降序追踪，含 Strahler 分级。
    """
    network = RiverNetwork(width=w, height=h)

    dist = _dijkstra_to_ocean(dem, flow_acc, w, h)
    smooth_dem = _gaussian_blur(dem, w, h, sigma)
    smooth_flow = _gaussian_blur(flow_acc, w, h, sigma)
    sources = _find_sources(flow_acc, directions, land_mask, w, h, threshold)
    visited: dict[int, int] = {}

    for src in sources:
        if src in visited:
            continue
        raw = _trace_downstream(src, dem, smooth_dem, smooth_flow, dist, w, h,
                               max_steps=max_steps, step_size=step_size)
        if len(raw) < min_length:
            continue
        smooth = _chaikin(raw, chaikin_iters)
        pts = [
            RiverPoint(
                x=p[0],
                y=p[1],
                flow=flow_acc[max(0, min(w * h - 1, int(p[1]) * w + int(p[0])))],
            )
            for p in smooth
        ]

        outlet, skip = _merge_into_existing(pts, visited, w, merge_radius, min_length)
        if skip or len(pts) < min_length:
            continue
        _commit_river(network, pts, outlet, visited, w)

    _compute_strahler(network)
    return network


# ═══════════════════════════════════════════════════════════
# Strahler 分级
# ═══════════════════════════════════════════════════════════


def _compute_strahler(network: RiverNetwork) -> None:
    """基于 parent_indices 拓扑从叶向根传播 Strahler 分级。

    叶=1，同级子节点交汇→+1，不同级→取较大者。
    """
    n = len(network.rivers)
    if n == 0:
        return
    indegree = [len(r.parent_indices) for r in network.rivers]
    q = deque(i for i in range(n) if indegree[i] == 0)
    child_orders: list[list[int]] = [[] for _ in range(n)]
    while q:
        idx = q.popleft()
        river = network.rivers[idx]
        if not child_orders[idx]:
            base = 1
        else:
            orders = child_orders[idx]
            max_o = max(orders)
            base = max_o + 1 if sum(1 for o in orders if o >= max_o) >= 2 else max_o
        for p in river.points:
            p.strahler = base
        outlet = river.outlet_idx
        if 0 <= outlet < n:
            child_orders[outlet].append(base)
            indegree[outlet] -= 1
            if indegree[outlet] == 0:
                q.append(outlet)


# ═══════════════════════════════════════════════════════════
# 区域查询
# ═══════════════════════════════════════════════════════════


def rivers_in_region(
    network: RiverNetwork,
    x0: float, y0: float, x1: float, y1: float,
    margin: float = 100.0,
) -> list[tuple[int, list[RiverPoint]]]:
    """返回落在 [x0,y0]-[x1,y1]（含 margin 边距）内的河流段。

    Returns:
        [(river_idx, points_in_region), ...]，points_in_region 是该河流
        落在区域内的连续点子集。
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
    "RiverPoint", "River", "RiverNetwork",
    "trace_streamline", "build_river_network", "rivers_in_region",
]
