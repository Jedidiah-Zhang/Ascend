"""水力侵蚀 — 粒子法模拟水流冲刷。

在海拔网格上投放水滴粒子，沿梯度下流，
侵蚀陡坡，沉积于平地，形成河谷和冲积扇。

用法:
    from ascend.space.erosion import hydraulic_erosion

    eroded = hydraulic_erosion(heightmap, w, h, seed=42, droplets=5000)
"""

import math
import random


def hydraulic_erosion(
    heightmap: list[float],
    w: int,
    h: int,
    *,
    seed: int = 0,
    droplets: int = 5000,
    inertia: float = 0.05,
    erosion_rate: float = 0.3,
    deposition_rate: float = 0.3,
    evaporation_rate: float = 0.01,
    min_slope: float = 0.01,
    capacity_factor: float = 4.0,
) -> list[float]:
    """对海拔网格执行粒子法水力侵蚀。

    算法：
      1. 在网格上随机投放 droplet_count 个水滴
      2. 每个水滴沿梯度下流，携带沉积物
      3. 不饱和时侵蚀地形，过饱和时沉积
      4. 水滴逐渐蒸发后停止

    Args:
        heightmap: 输入海拔列表，长度 w*h，行优先排列。
        w: 网格宽度。
        h: 网格高度。
        seed: 随机种子，控制水滴起始位置。
        droplets: 水滴总数。
        inertia: 惯性系数 [0, 1]，越大河道越直。
        erosion_rate: 侵蚀力 [0, 1]。
        deposition_rate: 沉积率 [0, 1]。
        evaporation_rate: 每步蒸发比例。
        min_slope: 最小坡度阈值。
        capacity_factor: 沉积物容量缩放因子。

    Returns:
        侵蚀后的海拔列表，长度 w*h。
    """
    n = w * h
    if n == 0 or droplets == 0:
        return list(heightmap)

    # 复制 heightmap 以便修改
    heights = list(heightmap)
    size = len(heights)
    rng = random.Random(seed)

    for _ in range(droplets):
        # 随机起始位置
        x = rng.random() * (w - 1)
        y = rng.random() * (h - 1)

        vx = 0.0
        vy = 0.0
        sediment = 0.0
        water = 1.0

        for _ in range(200):  # 最大生命周期
            # 整数坐标
            ix = int(x)
            iy = int(y)
            if ix < 1 or ix >= w - 1 or iy < 1 or iy >= h - 1:
                break  # 超出边界

            idx = iy * w + ix

            # 双线性采样高度和梯度
            fx = x - ix
            fy = y - iy

            # 四个角的高度
            h00 = heights[iy * w + ix]
            h10 = heights[iy * w + ix + 1]
            h01 = heights[(iy + 1) * w + ix]
            h11 = heights[(iy + 1) * w + ix + 1]

            # 梯度（双线性插值的偏导数）
            grad_x = (h10 * (1 - fy) + h11 * fy) - (h00 * (1 - fy) + h01 * fy)
            grad_y = (h01 * (1 - fx) + h11 * fx) - (h00 * (1 - fx) + h10 * fx)

            # 更新速度（惯性）
            vx = inertia * vx + (1.0 - inertia) * grad_x
            vy = inertia * vy + (1.0 - inertia) * grad_y

            speed = math.sqrt(vx * vx + vy * vy)
            if speed < min_slope:
                # 水流太慢，沉积所有沉积物后停止
                heights[idx] += sediment
                break

            # 归一化速度
            vx /= speed
            vy /= speed

            # 移动
            new_x = x + vx
            new_y = y + vy
            new_ix = int(new_x)
            new_iy = int(new_y)

            if new_ix < 0 or new_ix >= w - 1 or new_iy < 0 or new_iy >= h - 1:
                break

            new_idx = new_iy * w + new_ix
            new_fx = new_x - new_ix
            new_fy = new_y - new_iy

            # 新位置的双线性高度
            n00 = heights[new_iy * w + new_ix]
            n10 = heights[new_iy * w + new_ix + 1]
            n01 = heights[(new_iy + 1) * w + new_ix]
            n11 = heights[(new_iy + 1) * w + new_ix + 1]
            new_h = (n00 * (1 - new_fx) * (1 - new_fy) +
                     n10 * new_fx * (1 - new_fy) +
                     n01 * (1 - new_fx) * new_fy +
                     n11 * new_fx * new_fy)

            # 当前高度（双线性）
            cur_h = (h00 * (1 - fx) * (1 - fy) +
                     h10 * fx * (1 - fy) +
                     h01 * (1 - fx) * fy +
                     h11 * fx * fy)

            height_diff = cur_h - new_h

            if height_diff > 0:
                # 向下流动：侵蚀或沉积
                capacity = max(min_slope, height_diff) * speed * water * capacity_factor

                if sediment > capacity:
                    # 过饱和 → 沉积
                    deposit = (sediment - capacity) * deposition_rate
                    sediment -= deposit
                    # 沉积在当前位置（原位置和下一位置之间加权）
                    heights[idx] += deposit * (1.0 - fx) * (1.0 - fy) * 0.5
                    heights[idx + 1] += deposit * fx * (1.0 - fy) * 0.5
                    heights[idx + w] += deposit * (1.0 - fx) * fy * 0.5
                    heights[idx + w + 1] += deposit * fx * fy * 0.5
                else:
                    # 不饱和 → 侵蚀
                    erode_amount = min(
                        (capacity - sediment) * erosion_rate,
                        height_diff,
                    )
                    # 从当前位置侵蚀
                    w00 = max(0.0, (1.0 - fx) * (1.0 - fy))
                    w10 = max(0.0, fx * (1.0 - fy))
                    w01 = max(0.0, (1.0 - fx) * fy)
                    w11 = max(0.0, fx * fy)
                    w_total = w00 + w10 + w01 + w11
                    if w_total > 0:
                        heights[idx] -= erode_amount * w00 / w_total
                        heights[idx + 1] -= erode_amount * w10 / w_total
                        heights[idx + w] -= erode_amount * w01 / w_total
                        heights[idx + w + 1] -= erode_amount * w11 / w_total
                    sediment += erode_amount

            # 蒸发
            water *= (1.0 - evaporation_rate)
            if water < 0.01:
                # 水滴耗尽，沉积剩余沉积物
                heights[idx] += sediment
                break

            x, y = new_x, new_y

    return heights
