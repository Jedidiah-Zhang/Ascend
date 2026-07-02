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

import math
from heapq import heappush, heappop


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
    """填平 DEM 中的局部洼地。

    使用优先队列（Planchon-Darboux 算法简化版）：
    从边界最低点出发，向内灌水，确保每个像素都有向边界的下坡路径。

    Args:
        dem: 行优先海拔数组。
        w: 宽度。
        h: 高度。

    Returns:
        填洼后的海拔数组（新列表）。
    """
    result = dem[:]
    n = w * h

    # 标记所有像素为未处理
    processed = [False] * n
    heap: list[tuple[float, int, int]] = []

    # 所有边界像素入堆
    for x in range(w):
        for y in (0, h - 1):
            idx = y * w + x
            heappush(heap, (dem[idx], x, y))
            processed[idx] = True
    for y in range(1, h - 1):
        for x in (0, w - 1):
            idx = y * w + x
            heappush(heap, (dem[idx], x, y))
            processed[idx] = True

    # 从边界向内蔓延
    while heap:
        elev, x, y = heappop(heap)
        idx = y * w + x

        for d in range(8):
            nx, ny = x + _DX[d], y + _DY[d]
            if not (0 <= nx < w and 0 <= ny < h):
                continue
            ni = ny * w + nx
            if processed[ni]:
                continue

            # 如果需要，抬高到至少比当前像素高一丁点
            spill = elev + 0.001
            if result[ni] < spill:
                result[ni] = spill

            processed[ni] = True
            heappush(heap, (result[ni], nx, ny))

    return result


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
                    # 坡度 = 海拔差 / 距离（对角线距离 √2）
                    dist = math.sqrt(2.0) if d >= 4 else 1.0
                    slope = (elev - ne) / dist
                    if slope > best_slope:
                        best_slope = slope
                        best_d = d

            directions[idx] = best_d

    return directions


# ════════════════════════════════════════════════════════════════
# 水流累积
# ════════════════════════════════════════════════════════════════


def flow_accumulation(directions: list[int], w: int, h: int) -> list[float]:
    """计算水流累积量。

    每个像素累积 = 1（自身） + 所有流入像素的累积量。
    使用拓扑排序（按入度）处理，避免递归。

    Args:
        directions: 行优先 D8 方向数组。
        w: 宽度。
        h: 高度。

    Returns:
        行优先累积量数组。
    """
    n = w * h
    acc = [1.0] * n

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
    queue: list[int] = [i for i in range(n) if indegree[i] == 0]

    while queue:
        idx = queue.pop(0)
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
# Strahler 分级
# ════════════════════════════════════════════════════════════════


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
    iterations: int = 5,
    erodibility: float = 0.01,
) -> list[float]:
    """简化水力侵蚀模型。

    每轮迭代：
      1. 计算 D8 流向 + 累积量
      2. 侵蚀量 = K × 累积量^m × 坡度^n
      3. 更新海拔

    物质从陡坡 + 高流量处侵蚀，沿流路沉积。

    Args:
        dem: 行优先海拔数组。
        rainfall: 降雨量数组（同尺寸，>0）。
        w: 宽度。
        h: 高度。
        iterations: 侵蚀迭代轮数。
        erodibility: 侵蚀系数 K。

    Returns:
        侵蚀后的海拔数组（新列表）。
    """
    result = dem[:]
    n = w * h
    m_exp = 0.5   # 流量指数
    n_exp = 1.0   # 坡度指数

    for _ in range(iterations):
        # 填洼 → D8 → 累积
        filled = fill_depressions(result, w, h)
        directions = compute_d8(filled, w, h)
        acc = flow_accumulation(directions, w, h)

        delta = [0.0] * n

        for idx in range(n):
            d = directions[idx]
            if d < 0:
                continue  # 汇点不侵蚀

            x, y = idx % w, idx // w
            nx, ny = x + _DX[d], y + _DY[d]
            if not (0 <= nx < w and 0 <= ny < h):
                continue

            ni = ny * w + nx
            slope = max(0.0, result[idx] - result[ni])
            if slope <= 0:
                continue

            # 侵蚀量 = K × flow^m × slope^n
            flow = max(1.0, acc[idx])
            rain_factor = rainfall[idx] if idx < len(rainfall) else 1.0
            erosion = erodibility * (flow ** m_exp) * (slope ** n_exp) * rain_factor

            # 限制侵蚀量（不能把山削成坑）
            max_erode = slope * 0.5
            erosion = min(erosion, max_erode)

            delta[idx] -= erosion
            delta[ni] += erosion  # 沉积在下游

        # 应用侵蚀
        for idx in range(n):
            result[idx] += delta[idx]

    return result


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
) -> list[float]:
    """洼地填水 → 湖泊检测。

    使用填洼结果：filled > original 的连通区域 = 洼地盆地。
    盆地底部低于 0 → 内陆湖；盆地底部 > 0 → 高位湖。

    Args:
        dem: 行优先原始海拔。
        land_mask: 陆地掩码。
        w: 宽度。
        h: 高度。
        min_size: 最小湖泊面积（像素）。

    Returns:
        行优先湖面海拔（0=非湖）。
    """
    n = w * h
    filled = fill_depressions(dem, w, h)
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

    return lake_surface


def compute_river_width(
    dem: list[float],
    w: int, h: int,
    *,
    land_mask: list[bool] | None = None,
    threshold: float = 30.0,
    min_width: float = 2.0,
    max_width: float = 80.0,
) -> list[float]:
    """计算河流+湖泊宽度场（层1分辨率 → 供层2 tile查询）。

    河流宽度正比于 log(累积流量)。
    湖泊宽度基于湖面积（sqrt(面积)）。

    Args:
        dem: 行优先海拔数组。
        w: 宽度。
        h: 高度。
        land_mask: 陆地掩码（用于湖泊检测）。
        threshold: 河流提取阈值。
        min_width: 最小宽度 (m)。
        max_width: 最大宽度 (m)。

    Returns:
        行优先宽度数组 (m)，非水体像素 = 0。
    """
    filled = fill_depressions(dem, w, h)
    directions = compute_d8(filled, w, h)
    acc = flow_accumulation(directions, w, h)
    rivers = extract_rivers(directions, acc, w, h, threshold=threshold)

    n = w * h
    widths: list[float] = [0.0] * n

    # 河流宽度
    river_flow: dict[int, float] = {}
    for river in rivers:
        for x, y in river:
            idx = y * w + x
            river_flow[idx] = max(river_flow.get(idx, 0.0), acc[idx])

    max_acc = max(river_flow.values()) if river_flow else 1.0
    for idx, flow in river_flow.items():
        ratio = flow / max_acc
        log_ratio = math.log(1.0 + ratio * 20.0) / math.log(21.0)
        if dem[idx] > 0:
            widths[idx] = min_width + (max_width - min_width) * log_ratio

    # 湖泊宽度 → 基于湖面积估算等效宽度
    if land_mask is not None:
        lake_surface = find_lakes(dem, land_mask, w, h, min_size=5)
        # 找湖泊连通分量
        visited = [False] * n
        for i in range(n):
            if lake_surface[i] <= 0 or visited[i]:
                continue
            # BFS 收集湖连通分量
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
                        if lake_surface[ni] > 0 and not visited[ni]:
                            visited[ni] = True
                            q.append(ni)

            # 湖宽度 = sqrt(面积) * 像素尺寸
            lake_width = math.sqrt(len(comp)) * 100.0  # 100m/px
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
        delta = [0.0] * n
        for y in range(h):
            for x in range(w):
                idx = y * w + x
                elev = result[idx]
                if elev <= 0:
                    continue  # 只侵蚀陆地

                # 向 8 邻居扩散
                total_loss = 0.0
                for d in range(8):
                    nx, ny = x + _DX[d], y + _DY[d]
                    if not (0 <= nx < w and 0 <= ny < h):
                        continue
                    ni = ny * w + nx
                    ne = result[ni]
                    diff = elev - ne
                    if diff > 0:
                        # 陡坡扩散更多
                        loss = diff * rate
                        delta[idx] -= loss
                        delta[ni] += loss
                        total_loss += loss

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
    "fill_depressions",
    "compute_d8",
    "compute_dinf",
    "flow_accumulation",
    "flow_accumulation_dinf",
    "extract_rivers",
    "extract_rivers_dinf",
    "strahler_order",
    "erode",
    "hillslope_erosion",
    "carve_rivers",
    "compute_river_width",
]
