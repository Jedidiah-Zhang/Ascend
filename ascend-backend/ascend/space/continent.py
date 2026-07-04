"""大陆生成模块 — 层1 全局低分辨率大陆生成。

在世界创建时调用一次，生成低分辨率（100m/采样点）宏观场：
   - 海拔场（两层 Perlin：低频大陆轮廓 + 高频地形细节）
  - 温度场（纬度渐变 + 海拔降温）
  - 降雨场（噪声 + 雨影效应）
  - 气候带（热/温/寒/干）
  - 积雪场（年均温 < 0°C）
  - 河流宽度场
  - 内陆湖泊

结果保存在 ContinentData 中，所有 chunk 和 tile 生成共享此数据。

用法:
    from ascend.space.continent import ContinentGenerator, ContinentParams

    gen = ContinentGenerator(seed=42)
    data = gen.generate()

    alt = data.sample_altitude(50000.0, 30000.0)
    is_land = data.is_land(1200.5, 3400.2)
"""

from array import array
from dataclasses import dataclass, field
from typing import Union, Sequence

from .noise import PerlinNoise


@dataclass
class ContinentParams:
    """大陆生成参数。

    Args:
        width_km: 大陆东西宽度 (km)。
        height_km: 大陆南北高度 (km)。
        sample_resolution: 层1采样分辨率 (m/采样点)。
        land_ratio: 目标陆地比例 [0-1]。
    """

    width_km: float = 100.0
    height_km: float = 60.0
    sample_resolution: float = 100.0
    land_ratio: float = 0.55

    def __repr__(self) -> str:
        return (
            f"ContinentParams({self.width_km:.0f}×{self.height_km:.0f}km, "
            f"res={self.sample_resolution:.0f}m, "
            f"land={self.land_ratio:.0%})"
        )


@dataclass
class ContinentData:
    """层1生成结果 — 宏观场数据，不可变。

    Attributes:
        grid_width: 网格宽度（采样点数）。
        grid_height: 网格高度（采样点数）。
        cell_size: 每个采样点的世界距离 (m)。
        seed: 生成所用的种子。
        land_mask: 行优先布尔数组，True=陆地。
        elevation_field: 行优先海拔数组 (m)。
        temperature_field: 年均温基线 (°C)。
        rainfall_field: 年降雨量基线 (mm)。
        snow_mask: 永久积雪（年均温 < 0°C）。
        climate_zone: 气候带编码 (0=热带 1=温带 2=寒带 3=干旱)。
        river_width: 河流+湖泊宽度场 (m)。
    """

    grid_width: int
    grid_height: int
    cell_size: float
    seed: int

    land_mask: list[bool] = field(default_factory=list)
    elevation_field: Union[list[float], "array[float]"] = field(default_factory=lambda: array('d'))
    temperature_field: Union[list[float], "array[float]"] = field(default_factory=lambda: array('d'))
    rainfall_field: Union[list[float], "array[float]"] = field(default_factory=lambda: array('d'))
    climate_zone: list[int] = field(default_factory=list)
    river_width: Union[list[float], "array[float]"] = field(default_factory=lambda: array('d'))
    hydrology: "HydrologyData | None" = None

    def __repr__(self) -> str:
        land = sum(1 for v in self.land_mask if v)
        total = len(self.land_mask)
        ratio = land / total if total > 0 else 0
        return (
            f"ContinentData({self.grid_width}×{self.grid_height}, "
            f"cell={self.cell_size:.0f}m, land={ratio:.1%})"
        )

    def _grid_index(self, world_x: float, world_y: float) -> int | None:
        """世界坐标 → 网格索引。越界返回 None。"""
        gx = int(world_x / self.cell_size)
        gy = int(world_y / self.cell_size)
        if 0 <= gx < self.grid_width and 0 <= gy < self.grid_height:
            return gy * self.grid_width + gx
        return None

    def is_land(self, world_x: float, world_y: float) -> bool:
        """查询世界坐标是否为陆地。越界返回 False。"""
        idx = self._grid_index(world_x, world_y)
        if idx is None or idx >= len(self.land_mask):
            return False
        return self.land_mask[idx]

    def sample_altitude(self, world_x: float, world_y: float) -> float:
        """从宏观海拔场采样（最近邻）。越界返回默认海洋深度。"""
        idx = self._grid_index(world_x, world_y)
        if idx is None or idx >= len(self.elevation_field):
            return -3500.0
        return self.elevation_field[idx]

    def sample_altitude_bilinear(self, world_x: float, world_y: float) -> float:
        """双线性插值采样宏观海拔，消除 100m 网格的块状伪影。

        Args:
            world_x: 世界 tile X 坐标。
            world_y: 世界 tile Y 坐标。

        Returns:
            插值后的海拔 (m)。越界返回默认海洋深度。
        """
        # 网格空间中的连续坐标（以 cell_size 为单位）
        gx = world_x / self.cell_size - 0.5
        gy = world_y / self.cell_size - 0.5

        x0 = int(gx)
        y0 = int(gy)
        x1, y1 = x0 + 1, y0 + 1

        # 越界检查
        if (x0 < 0 or x1 >= self.grid_width or
                y0 < 0 or y1 >= self.grid_height):
            return self.sample_altitude(world_x, world_y)  # 回退最近邻

        tx = gx - x0
        ty = gy - y0

        # 四个角的值
        elev = self.elevation_field
        gw = self.grid_width
        v00 = elev[y0 * gw + x0]
        v10 = elev[y0 * gw + x1]
        v01 = elev[y1 * gw + x0]
        v11 = elev[y1 * gw + x1]

        # 双线性插值
        v0 = v00 + (v10 - v00) * tx
        v1 = v01 + (v11 - v01) * tx
        return v0 + (v1 - v0) * ty

    def sample_river_width(self, world_x: float, world_y: float) -> float:
        """双线性插值采样河流宽度 (m)，消除 100m 网格块状伪影。

        Args:
            world_x: 世界 tile X 坐标。
            world_y: 世界 tile Y 坐标。

        Returns:
            插值后的河流宽度 (m)，0=无河流。越界返回 0。
        """
        if not self.river_width:
            return 0.0

        gx = world_x / self.cell_size - 0.5
        gy = world_y / self.cell_size - 0.5
        x0 = int(gx)
        y0 = int(gy)
        x1, y1 = x0 + 1, y0 + 1

        if (x0 < 0 or x1 >= self.grid_width or
                y0 < 0 or y1 >= self.grid_height):
            return 0.0

        tx = gx - x0
        ty = gy - y0
        rw = self.river_width
        gw = self.grid_width
        v00 = rw[y0 * gw + x0]
        v10 = rw[y0 * gw + x1]
        v01 = rw[y1 * gw + x0]
        v11 = rw[y1 * gw + x1]
        v0 = v00 + (v10 - v00) * tx
        v1 = v01 + (v11 - v01) * tx
        return v0 + (v1 - v0) * ty


class ContinentGenerator:
    """层1全局大陆生成器。

    每个 seed 独立生成一个 ContinentData。
    线程安全：generate() 创建所有临时状态，无共享可变状态。
    """

    def __init__(
        self,
        seed: int = 0,
        *,
        params: ContinentParams | None = None,
    ) -> None:
        """初始化生成器。

        Args:
            seed: 世界种子。
            params: 生成参数。
        """
        self._seed = seed
        self._params = params or ContinentParams()

    def __repr__(self) -> str:
        return f"ContinentGenerator(seed={self._seed})"

    @property
    def _grid_width(self) -> int:
        return int(self._params.width_km * 1000 / self._params.sample_resolution)

    @property
    def _grid_height(self) -> int:
        return int(self._params.height_km * 1000 / self._params.sample_resolution)

    # ── 主入口 ──────────────────────────────────────────────

    def generate(self) -> ContinentData:
        """执行完整的层1生成管线。

        管线顺序：
          海拔 + 陆地掩码 → 海拔校准 → 气候（温度+降雨）→ 气候校准
          → 侵蚀（降雨驱动水流）→ 河流树 + 湖泊盆地提取

        校准步骤保证 8 档气候覆盖：海拔/降雨/温度场分别做保结构的
        分位数拉伸，确保值域覆盖各气候档位的判定阈值。
        """
        w = self._grid_width
        h = self._grid_height

        # Step 1: 海拔 + 陆地掩码（湖泊由水文系统接管）
        land_mask, elevation = self._generate_elevation(w, h)

        # Step 1b: 海拔校准 — 保证高山（≥2000m）存在
        self._ensure_elevation_range(elevation, land_mask)

        # Step 2: 气候（温度、降雨、气候带）—— 降雨在侵蚀之前生成
        temp_field, rain_field, climate_field = (
            self._compute_climate(elevation, land_mask, w, h))

        # Step 2b-2e: 气候校准 + 重分类（合并为单次遍历）
        self._calibrate_climate_merged(
            elevation, temp_field, rain_field, land_mask, climate_field, w, h,
        )

        # Step 3: 侵蚀（降雨驱动水流累积）—— 提取完整水文状态
        from .hydrology import erode, extract_lake_basins, HydrologyData
        erosion_result = erode(elevation, rain_field, w, h, iterations=10)

        # 用侵蚀后的海拔替换原始海拔（河流已雕刻，地形已塑形）
        elevation = erosion_result.dem

        # Step 4: 湖泊盆地提取
        lake_basins = extract_lake_basins(
            elevation, erosion_result.filled_dem, land_mask, w, h,
            min_size=5,
        )

        # Step 4b: 流线河流网络 — RK4 沿海拔梯度场追踪自然弯曲流线
        from .streamlines import build_river_network
        river_network = build_river_network(
            elevation, erosion_result.filled_dem,
            erosion_result.directions, erosion_result.flow_acc,
            land_mask, w, h,
            threshold=500.0, min_length=20,
        )

        hydrology = HydrologyData(
            lake_basins=lake_basins,
            flow_acc=erosion_result.flow_acc,
            directions=erosion_result.directions,
            filled_dem=erosion_result.filled_dem,
            river_network=river_network,
        )

        # Step 5: 河流宽度场（复用侵蚀+水文数据，避免重复计算）
        from .hydrology import compute_river_width
        river_width = compute_river_width(
            elevation, w, h,
            land_mask=land_mask, threshold=20.0,
            directions=erosion_result.directions,
            flow_acc=erosion_result.flow_acc,
            lake_basins=lake_basins,
        )

        # Step 6: 兜底 — 保证 8 档气候覆盖（最后执行，不影响水文）
        self._inject_missing_climates(
            elevation, temp_field, rain_field, land_mask, climate_field, w, h,
        )

        return ContinentData(
            grid_width=w, grid_height=h,
            cell_size=self._params.sample_resolution,
            seed=self._seed,
            land_mask=land_mask,
            elevation_field=array('d', elevation),
            temperature_field=array('d', temp_field),
            rainfall_field=array('d', rain_field),
            climate_zone=climate_field,
            river_width=array('d', river_width),
            hydrology=hydrology,
        )

    # ── 气候覆盖校准 ──────────────────────────────────────

    @staticmethod
    def _percentile(sorted_vals: list[float], pct: float) -> float:
        """从已排序数组取分位数（线性插值）。"""
        n = len(sorted_vals)
        if n == 0:
            return 0.0
        pos = pct * (n - 1)
        lo = int(pos)
        hi = min(lo + 1, n - 1)
        frac = pos - lo
        return sorted_vals[lo] * (1 - frac) + sorted_vals[hi] * frac

    def _calibrate_climate_merged(
        self,
        elevation: list[float],
        temp: list[float],
        rain: list[float],
        land_mask: list[bool],
        climate_field: list[int],
        w: int, h: int,
    ) -> None:
        """合并气候校准 — 单次数据收集 + 合并应用循环。

        将原来的四次独立 O(N) 遍历（_ensure_rainfall_range、
        _ensure_temperature_range、_ensure_rainfall_temperature_coverage、
        重算气候带）合并为两次 O(N) 遍历 + 共享排序，
        消除 3 次冗余的 600K 元素扫描。
        """
        from .climate import classify
        n = w * h

        # Phase 1: 一次遍历收集所有排序所需数据
        land_temps: list[float] = []
        land_rains: list[float] = []

        for i in range(n):
            if land_mask[i]:
                land_temps.append(temp[i])
                land_rains.append(rain[i])

        if not land_temps:
            return

        # Phase 2: 排序 + 计算校准参数
        land_temps.sort()
        land_rains.sort()

        # 降雨校准参数 (原 _ensure_rainfall_range)
        rain_p3 = self._percentile(land_rains, 0.03)
        rain_p10 = self._percentile(land_rains, 0.10)
        do_rain_cal = not (rain_p3 <= 100.0 or rain_p10 <= rain_p3)

        # 温度校准参数 (原 _ensure_temperature_range)
        temp_p2 = self._percentile(land_temps, 0.02)
        temp_p98 = self._percentile(land_temps, 0.98)
        do_temp_cal = (
            temp_p98 - temp_p2 >= 1.0
            and not (temp_p2 <= -12.0 and temp_p98 >= 30.0)
        )

        # Phase 3: 应用降雨和温度校准，同时收集交叉校准所需数据
        # （交叉校准需要在温度校准之后收集，因为用校准后的温度分区）
        if do_rain_cal:
            rain_scale = (rain_p10 - 100.0) / (rain_p10 - rain_p3)
        if do_temp_cal:
            temp_scale = (30.0 - (-12.0)) / (temp_p98 - temp_p2)
            temp_offset = -12.0 - temp_p2 * temp_scale

        hot_rains: list[float] = []   # 热区(T>=20) 的降雨值
        cold_rains: list[float] = []  # 冷区(-5<=T<5) 的降雨值

        for i in range(n):
            if not land_mask[i]:
                continue

            # 降雨校准（必须在温度校准之前）
            if do_rain_cal and rain[i] < rain_p10:
                rain[i] = max(0.0, 100.0 + (rain[i] - rain_p3) * rain_scale)

            # 温度校准
            if do_temp_cal:
                temp[i] = temp[i] * temp_scale + temp_offset

            # 收集校准后的交叉校准数据
            t = temp[i]
            r = rain[i]
            if t >= 20.0:
                hot_rains.append(r)
            elif -5.0 <= t < 5.0:
                cold_rains.append(r)

        # Phase 4: 排序交叉校准数据 + 计算参数
        hot_rains.sort()
        cold_rains.sort()

        do_hot_cal = len(hot_rains) > 100
        hot_p20 = 0.0
        hot_max = 0.0
        if do_hot_cal:
            hot_p20 = hot_rains[int(len(hot_rains) * 0.20)]
            hot_max = hot_rains[-1]
            do_hot_cal = hot_max < 1500.0 and hot_max > hot_p20

        do_cold_cal = len(cold_rains) > 100
        cold_p40 = 0.0
        cold_max = 0.0
        if do_cold_cal:
            cold_p40 = cold_rains[int(len(cold_rains) * 0.40)]
            cold_max = cold_rains[-1]
            do_cold_cal = cold_max < 500.0 and cold_max > cold_p40

        # Phase 5: 应用交叉校准 + 重分类（单次遍历）
        for i in range(n):
            if not land_mask[i]:
                continue

            t = temp[i]
            r = rain[i]

            # 交叉校准——使用已校准的温湿度值
            if do_hot_cal and t >= 20.0 and r > hot_p20:
                frac = (r - hot_p20) / (hot_max - hot_p20)
                rain[i] = 200.0 + frac * 1600.0
            elif do_cold_cal and -5.0 <= t < 5.0 and r > cold_p40:
                frac = (r - cold_p40) / (cold_max - cold_p40)
                rain[i] = 300.0 + frac * 300.0

            # 重分类
            climate_field[i] = int(classify(temp[i], rain[i], elevation[i]))

    def _ensure_elevation_range(
        self, elevation: list[float], land_mask: list[bool],
    ) -> None:
        """海拔校准 — 拉伸高海拔尾部，保证陆地 P99 ≥ 2500m。

        只提升 top 10% 区域（P90 以上），低海拔不变，不影响海岸线。
        侵蚀不削平山顶，故侵蚀后仍保留 ≥2000m 的高山。
        原地修改 elevation。
        """
        land_vals = sorted(e for i, e in enumerate(elevation) if land_mask[i])
        if not land_vals:
            return
        p90 = self._percentile(land_vals, 0.90)
        p99 = self._percentile(land_vals, 0.99)
        target_p99 = 2500.0
        if p99 >= target_p99 or p99 <= p90:
            return
        # 线性拉伸 (p90, p99] → (p90, target_p99]
        scale = (target_p99 - p90) / (p99 - p90)
        for i in range(len(elevation)):
            if land_mask[i] and elevation[i] > p90:
                elevation[i] = p90 + (elevation[i] - p90) * scale

    def _inject_missing_climates(
        self,
        elevation: list[float],
        temp: list[float],
        rain: list[float],
        land_mask: list[bool],
        climate_field: list[int],
        w: int, h: int,
    ) -> None:
        """兜底注入 — 对缺失气候档位，在最近邻区域创建最小气候种子。

        分位数拉伸解决了大部分 seed 的气候覆盖，但极端干旱/偏冷 seed
        仍可能缺失某些档位（温度-降雨空间分布天生不配合）。
        本函数在最接近目标档位阈值的陆地像素周围 3×3 区域
        直接设置参数，强制其落入目标档位。

        仅改 9 像素（0.09 km²），在 100×60km 大陆上几乎不可见，
        但保证大地图俯瞰时 8 种颜色都存在。在水文计算后执行，
        不影响河流树/湖泊/流向。
        """
        from .climate import classify
        n = w * h

        # 各档位目标参数（判定阈值中间值，确保落入该档位）
        targets = {
            0: (25.0, 2000.0, 200.0),    # 热带雨林
            1: (25.0, 1000.0, 200.0),    # 热带草原
            2: (20.0, 100.0, 200.0),     # 沙漠
            3: (15.0, 400.0, 200.0),     # 草原
            4: (12.0, 800.0, 200.0),     # 温带森林
            5: (-2.0, 500.0, 200.0),     # 亚寒带针叶林
            6: (-10.0, 300.0, 200.0),    # 极地苔原
            7: (10.0, 800.0, 2500.0),    # 高山
        }

        present = set(climate_field[i] for i in range(n) if land_mask[i])
        missing = list(set(targets.keys()) - present)

        if not missing:
            return

        # 单次扫描替代 N × |missing| 次独立扫描
        inv30 = 1.0 / 30.0
        inv2000 = 1.0 / 2000.0
        inv3000 = 1.0 / 3000.0
        best_i = {mz: -1 for mz in missing}
        best_d = {mz: float("inf") for mz in missing}

        for i in range(n):
            if not land_mask[i]:
                continue
            t = temp[i]
            r = rain[i]
            e = elevation[i]
            for mz in missing:
                tt, tr, ta = targets[mz]
                dt = (t - tt) * inv30
                dr = (r - tr) * inv2000
                de = (e - ta) * inv3000
                d = dt * dt + dr * dr + de * de
                if d < best_d[mz]:
                    best_d[mz] = d
                    best_i[mz] = i

        for mzone in missing:
            tt, tr, ta = targets[mzone]
            best_idx = best_i[mzone]
            if best_idx < 0:
                continue
            # 在候选位置周围 3×3 注入目标参数
            gx, gy = best_idx % w, best_idx // w
            for dy in range(-1, 2):
                for dx in range(-1, 2):
                    nx, ny = gx + dx, gy + dy
                    if 0 <= nx < w and 0 <= ny < h:
                        ni = ny * w + nx
                        if land_mask[ni]:
                            temp[ni] = tt
                            rain[ni] = tr
                            elevation[ni] = max(elevation[ni], ta)
                            climate_field[ni] = int(mzone)

    # ── 海拔生成 ──────────────────────────────────────────

    def _generate_elevation(
        self, w: int, h: int,
    ) -> tuple[list[bool], list[float]]:
        """两层 Perlin 噪声 → 海拔 + 陆地。

        大陆轮廓层（低频）：决定海陆分布的大洲形状。
        地形细节层（高频）：叠加山地丘陵等局部变化。
        温和的中心倾向避免"四周陆地中间海洋"的环形分布。
        分位数校准确保陆地比例稳定在 land_ratio。
        """
        noise_terrain = PerlinNoise(self._seed + 10002)
        noise_continent = PerlinNoise(self._seed + 10003)

        terrain_freq = self._params.sample_resolution / 30000.0
        terrain_field = noise_terrain.octave_grid(
            0.5, 0.5, w, h, frequency=terrain_freq, octaves=5,
        )

        continent_freq = 1.5 / w
        continent_field = noise_continent.octave_grid(
            0.5, 0.5, w, h, frequency=continent_freq, octaves=2,
        )

        n = w * h
        mixed = [0.0] * n
        inv_w = 1.0 / w
        inv_h = 1.0 / h

        # 嵌套循环避免每像素 % 和 // 取模运算
        i = 0
        for y in range(h):
            dy = (y * inv_h - 0.5) * 2.0
            for x in range(w):
                dx = (x * inv_w - 0.5) * 2.0
                dist = -dx if dx < -dy else (dy if dy > dx else dx)
                if dist < 0:
                    dist = -dist
                center = 1.0 - dist * 2.5
                if center < 0.0:
                    center = 0.0
                mixed[i] = continent_field[i] * 0.7 + terrain_field[i] * 0.3 + center * 0.12
                i += 1

        target = self._params.land_ratio
        sorted_vals = sorted(mixed)
        sea_idx = int(n * (1.0 - target))
        sea_idx = max(0, min(n - 1, sea_idx))
        sea_level = sorted_vals[sea_idx]

        # 列表推导 — 比 .append() 循环快
        elevation = [(m - sea_level) * 4000.0 for m in mixed]
        land_mask = [e > 0 for e in elevation]
        return land_mask, elevation

    # ── 气候计算 ──────────────────────────────────────────

    def _compute_climate(
        self, elevation: list[float], land_mask: list[bool], w: int, h: int,
    ) -> tuple[list[float], list[float], list[int]]:
        """计算温度、降雨、气候带。

        温度 = 海平面纬度温度 - 海拔 × 6.5°C/km
        降雨 = 噪声 × 雨影因子

        温度基线由 seed 决定的方向梯度给出，往某方向走持续变暖、反方向变冷。
        叠加微量噪声使气候带边界自然蜿蜒。
        """
        import math

        # seed → 随机温度梯度方向
        angle = ((self._seed * 2654435761) & 0xFFFFFFFF) / 0xFFFFFFFF * 2.0 * math.pi
        gx = math.cos(angle)
        gy = math.sin(angle)

        lat_wiggle = PerlinNoise(self._seed + 99999)
        lat_wiggle_field = lat_wiggle.octave_grid(
            0.5, 0.5, w, h,
            frequency=self._params.sample_resolution / 15000.0, octaves=1,
        )

        rain_noise = PerlinNoise(self._seed + 88888)
        rain_field_raw = rain_noise.octave_grid(
            0.5, 0.5, w, h,
            frequency=self._params.sample_resolution / 25000.0, octaves=3,
        )

        rain_shadow = self._compute_rain_shadow(elevation, w, h)

        # climate computation → C (single pass over 600K cells, 零拷贝)
        from .hydrology import _compute_climate_c
        elev_arr = array('d', elevation)
        lat_arr = array('d', lat_wiggle_field)
        rain_raw_arr = array('d', rain_field_raw)
        shadow_arr = array('d', rain_shadow)
        temp_field, rain_field, climate_field = _compute_climate_c(
            elev_arr, lat_arr, rain_raw_arr, shadow_arr,
            w, h, gx, gy,
        )

        # convert climate to int (from array)
        climate_field = [int(c) for c in climate_field]

        return temp_field, rain_field, climate_field

    def _compute_rain_shadow(
        self, elevation: list[float], w: int, h: int,
    ) -> list[float]:
        """雨影因子：山脉背风面降雨锐减，盛行风向由 seed 决定。

        支持四个盛行风向（西、东、南、北）。风向按 seed 确定。
        滑动窗口前缀扫描：从迎风侧向背风侧累加上坡量，
        累积爬升越多 → 背风侧降雨衰减越大。
        """
        # seed 决定盛行风向: 0=西风, 1=东风, 2=南风, 3=北风
        wind = (self._seed % 37 * 13) % 4

        if wind == 0:
            return self._rain_shadow_westerly(elevation, w, h)
        elif wind == 1:
            return self._rain_shadow_easterly(elevation, w, h)
        elif wind == 2:
            return self._rain_shadow_southerly(elevation, w, h)
        else:
            return self._rain_shadow_northerly(elevation, w, h)

    def _rain_shadow_westerly(
        self, elevation: list[float], w: int, h: int,
    ) -> list[float]:
        """盛行西风：西侧迎风多雨，东侧背风干旱。
        扫描每行从左到右，累加上坡量。"""
        return self._rain_shadow_along_axis(
            elevation, w, h, scan_axis=0, windward=0,
        )

    def _rain_shadow_easterly(
        self, elevation: list[float], w: int, h: int,
    ) -> list[float]:
        """盛行东风：东侧迎风多雨，西侧背风干旱。
        扫描每行从右到左，累加上坡量。"""
        return self._rain_shadow_along_axis(
            elevation, w, h, scan_axis=0, windward=1,
        )

    def _rain_shadow_southerly(
        self, elevation: list[float], w: int, h: int,
    ) -> list[float]:
        """盛行南风：南侧迎风多雨，北侧背风干旱。
        扫描每列从下到上，累加上坡量。"""
        return self._rain_shadow_along_axis(
            elevation, w, h, scan_axis=1, windward=0,
        )

    def _rain_shadow_northerly(
        self, elevation: list[float], w: int, h: int,
    ) -> list[float]:
        """盛行北风：北侧迎风多雨，南侧背风干旱。
        扫描每列从上到下，累加上坡量。"""
        return self._rain_shadow_along_axis(
            elevation, w, h, scan_axis=1, windward=1,
        )

    @staticmethod
    def _rain_shadow_along_axis(
        elevation: list[float], w: int, h: int,
        scan_axis: int, windward: int,
    ) -> list[float]:
        """沿主轴扫描计算雨影因子 — C 加速。

        Args:
            scan_axis: 0=沿行扫描（风向东西），1=沿列扫描（风向南北）。
            windward: 0=迎风侧在起点（如西风扫描从左→右），1=迎风侧在终点。
        """
        from .hydrology import _rain_shadow_c
        elev_arr = array('d', elevation)
        result = _rain_shadow_c(elev_arr, w, h, scan_axis, windward)
        return result.tolist()


__all__ = ["ContinentParams", "ContinentData", "ContinentGenerator"]
