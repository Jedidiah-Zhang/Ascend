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
          海拔 + 陆地掩码 → 气候（温度+降雨）→ 侵蚀（降雨驱动水流）
          → 河流树 + 湖泊盆地提取
        """
        w = self._grid_width
        h = self._grid_height

        # Step 1: 海拔 + 陆地掩码（湖泊由水文系统接管）
        land_mask, elevation = self._generate_elevation(w, h)

        # Step 2: 气候（温度、降雨、气候带）—— 降雨在侵蚀之前生成
        temp_field, rain_field, climate_field = (
            self._compute_climate(elevation, land_mask, w, h))

        # Step 3: 侵蚀（降雨驱动水流累积）—— 提取完整水文状态
        from .hydrology import erode, build_river_tree, extract_lake_basins, HydrologyData
        erosion_result = erode(elevation, rain_field, w, h, iterations=12)

        # 用侵蚀后的海拔替换原始海拔（河流已雕刻，地形已塑形）
        elevation = erosion_result.dem

        # Step 4: 构建结构化水文数据
        # 阈值约 0.2-0.5% of max_acc（降雨驱动下 max_acc ~200K）
        # 只保留陆地上的河流（dem > 0），过滤海底河道
        river_tree = build_river_tree(
            erosion_result.directions, erosion_result.flow_acc, w, h,
            threshold=500.0,
            land_only=True,
            dem=elevation,
            min_length=20,
        )
        lake_basins = extract_lake_basins(
            elevation, erosion_result.filled_dem, land_mask, w, h,
            min_size=5,
        )
        hydrology = HydrologyData(
            river_tree=river_tree,
            lake_basins=lake_basins,
            flow_acc=erosion_result.flow_acc,
            directions=erosion_result.directions,
            filled_dem=erosion_result.filled_dem,
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
        for i in range(n):
            x = i % w
            y = i // w

            dx = (x / w - 0.5) * 2.0
            dy = (y / h - 0.5) * 2.0
            dist = max(abs(dx), abs(dy))
            center = max(0.0, 1.0 - dist * 2.5)

            mixed[i] = continent_field[i] * 0.7 + terrain_field[i] * 0.3 + center * 0.12

        target = self._params.land_ratio
        sorted_vals = sorted(mixed)
        sea_idx = int(n * (1.0 - target))
        sea_idx = max(0, min(n - 1, sea_idx))
        sea_level = sorted_vals[sea_idx]

        elevation: list[float] = []
        for i in range(n):
            elev = (mixed[i] - sea_level) * 4000.0
            elevation.append(elev)

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
        from .climate import rainfall_from_noise, climate_zone_from_values, LAPSE_RATE

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
        LAPSE = LAPSE_RATE / 1000.0

        temp_field: list[float] = []
        rain_field: list[float] = []
        climate_field: list[int] = []

        for i in range(w * h):
            x = i % w
            y = i // w
            px = (x / w - 0.5) * 2.0
            py = (y / h - 0.5) * 2.0
            lat_n = (px * gx + py * gy) * 0.6 + lat_wiggle_field[i] * 0.15

            sea_temp = lat_n * 25.0 + 10.0
            sea_temp = max(-20.0, min(38.0, sea_temp))

            elev = elevation[i]
            temp = sea_temp - elev * LAPSE
            temp = max(-20.0, min(36.0, temp))

            rain_n = rain_field_raw[i]
            rainfall = rainfall_from_noise(rain_n) * rain_shadow[i]
            climate = climate_zone_from_values(temp, rainfall)

            temp_field.append(temp)
            rain_field.append(rainfall)
            climate_field.append(int(climate))

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
        """沿主轴扫描计算雨影因子。

        Args:
            scan_axis: 0=沿行扫描（风向东西），1=沿列扫描（风向南北）。
            windward: 0=迎风侧在起点（如西风扫描从左→右），1=迎风侧在终点。
        """
        factors: list[float] = [0.0] * (w * h)
        WINDOW = 40

        if scan_axis == 0:
            outer, inner = h, w
        else:
            outer, inner = w, h

        for o in range(outer):
            # 前缀和：沿扫描方向累加上坡量
            pref: list[float] = []
            running = 0.0

            for i in range(inner):
                if scan_axis == 0:
                    if windward == 0:
                        idx = o * w + i
                        prev_idx = o * w + (i - 1)
                        gain = elevation[prev_idx] - elevation[idx] if i > 0 else 0.0
                    else:
                        j = inner - 1 - i
                        idx = o * w + j
                        next_idx = o * w + (j + 1)
                        gain = elevation[next_idx] - elevation[idx] if j < inner - 1 else 0.0
                else:
                    if windward == 0:
                        idx = i * h + o
                        prev_idx = (i - 1) * h + o
                        gain = elevation[prev_idx] - elevation[idx] if i > 0 else 0.0
                    else:
                        j = inner - 1 - i
                        idx = j * h + o
                        next_idx = (j + 1) * h + o
                        gain = elevation[next_idx] - elevation[idx] if j < inner - 1 else 0.0

                if gain > 0:
                    running += gain
                pref.append(running)

            # 滑动窗口求和 → 雨影因子
            for i in range(inner):
                if scan_axis == 0:
                    if windward == 0:
                        idx = o * w + i
                    else:
                        idx = o * w + (inner - 1 - i)
                else:
                    if windward == 0:
                        idx = i * h + o
                    else:
                        idx = (inner - 1 - i) * h + o

                if i <= WINDOW:
                    total_uplift = pref[i]
                else:
                    total_uplift = pref[i] - pref[i - WINDOW]

                if total_uplift < 30:
                    f = 1.0
                elif total_uplift < 150:
                    f = 1.0 - (total_uplift - 30) / 120 * 0.4
                elif total_uplift < 400:
                    f = 0.6 - (total_uplift - 150) / 250 * 0.35
                else:
                    f = max(0.15, 0.25 - (total_uplift - 400) / 2000 * 0.15)
                factors[idx] = f

        return factors


__all__ = ["ContinentParams", "ContinentData", "ContinentGenerator"]
