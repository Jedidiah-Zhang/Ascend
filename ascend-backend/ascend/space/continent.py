"""大陆生成模块 — 层1 全局低分辨率大陆生成。

在世界创建时调用一次，生成低分辨率（100m/采样点）宏观场：
  - 海拔场（5 octave Perlin 噪声 + 10×6 权重网格）
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

import math
from dataclasses import dataclass, field

from .noise import PerlinNoise


@dataclass
class ContinentParams:
    """大陆生成参数。

    Args:
        width_km: 大陆东西宽度 (km)。
        height_km: 大陆南北高度 (km)。
        sample_resolution: 层1采样分辨率 (m/采样点)。
        land_ratio: 目标陆地比例 [0-1]。
        weight_cols: 权重区域列数。
        weight_rows: 权重区域行数。
    """

    width_km: float = 100.0
    height_km: float = 60.0
    sample_resolution: float = 100.0
    land_ratio: float = 0.55
    weight_cols: int = 10
    weight_rows: int = 6

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
    elevation_field: list[float] = field(default_factory=list)
    temperature_field: list[float] = field(default_factory=list)
    rainfall_field: list[float] = field(default_factory=list)
    snow_mask: list[bool] = field(default_factory=list)
    climate_zone: list[int] = field(default_factory=list)
    river_width: list[float] = field(default_factory=list)

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
        """执行完整的层1生成管线。"""
        w = self._grid_width
        h = self._grid_height

        # Step 1: 海拔 + 陆地掩码
        land_mask, elevation = self._generate_elevation(w, h)

        # Step 2: 气候（温度、降雨、积雪、气候带）
        temp_field, rain_field, snow_mask, climate_field = (
            self._compute_climate(elevation, land_mask, w, h))

        # Step 3: 河流宽度场
        from .hydrology import compute_river_width
        river_width = compute_river_width(elevation, w, h,
                                          land_mask=land_mask, threshold=20.0)

        return ContinentData(
            grid_width=w, grid_height=h,
            cell_size=self._params.sample_resolution,
            seed=self._seed,
            land_mask=land_mask,
            elevation_field=elevation,
            temperature_field=temp_field,
            rainfall_field=rain_field,
            snow_mask=snow_mask,
            climate_zone=climate_field,
            river_width=river_width,
        )

    # ── 海拔生成 ──────────────────────────────────────────

    def _generate_elevation(
        self, w: int, h: int,
    ) -> tuple[list[bool], list[float]]:
        """5 octave Perlin + 10×6 权重网格 → 海拔 + 陆地。"""
        noise = PerlinNoise(self._seed + 10002)
        freq = 1.0 / 30000.0  # 波长 30km
        weight_grid = self._generate_weight_grid(
            self._params.weight_cols, self._params.weight_rows)
        cell_w = w / self._params.weight_cols
        cell_h = h / self._params.weight_rows

        elevation: list[float] = []
        for y in range(h):
            for x in range(w):
                wx = (x + 0.5) * self._params.sample_resolution
                wy = (y + 0.5) * self._params.sample_resolution
                val = noise.octave(wx * freq, wy * freq, octaves=5)

                gx = x / cell_w - 0.5
                gy = y / cell_h - 0.5
                weight = self._sample_weight(weight_grid,
                                              self._params.weight_cols,
                                              self._params.weight_rows, gx, gy)
                sea_level = (0.5 - weight) * 2.0
                adjusted = val - sea_level

                if adjusted > 0:
                    elevation.append(adjusted * 4000.0)
                else:
                    elevation.append(adjusted * 4000.0)

        land_mask = [e > 0 for e in elevation]
        land_mask, elevation = self._create_lakes(land_mask, elevation, w, h)
        return land_mask, elevation

    def _generate_weight_grid(self, cols: int, rows: int) -> list[list[float]]:
        """10×6 随机权重网格 + 边缘衰减。"""
        import random
        rng = random.Random(self._seed + 7777777)

        grid: list[list[float]] = []
        for r in range(rows):
            row: list[float] = []
            nr = r / (rows - 1) if rows > 1 else 0.5
            for c in range(cols):
                nc = c / (cols - 1) if cols > 1 else 0.5
                w = rng.gauss(0.55, 0.12)
                w = max(0.25, min(0.82, w))

                dist_to_edge = min(nc, nr, 1.0 - nc, 1.0 - nr)
                if dist_to_edge < 0.15:
                    t = dist_to_edge / 0.15
                    edge_factor = 0.75 + 0.25 * t * t * (3.0 - 2.0 * t)
                else:
                    edge_factor = 1.0
                w *= edge_factor
                row.append(w)
            grid.append(row)
        return grid

    @staticmethod
    def _sample_weight(
        grid: list[list[float]], cols: int, rows: int,
        gx: float, gy: float,
    ) -> float:
        """双线性插值采样权重网格。"""
        gx = max(0.0, min(cols - 1.001, gx))
        gy = max(0.0, min(rows - 1.001, gy))
        x0, y0 = int(gx), int(gy)
        x1, y1 = min(x0 + 1, cols - 1), min(y0 + 1, rows - 1)
        tx, ty = gx - x0, gy - y0
        w00, w10 = grid[y0][x0], grid[y0][x1]
        w01, w11 = grid[y1][x0], grid[y1][x1]
        w0 = w00 + (w10 - w00) * tx
        w1 = w01 + (w11 - w01) * tx
        return w0 + (w1 - w0) * ty

    @staticmethod
    def _create_lakes(
        land_mask: list[bool], elevation: list[float], w: int, h: int,
    ) -> tuple[list[bool], list[float]]:
        """内陆湖：不与地图边缘相连的海洋连通分量。"""
        ocean = [i for i, v in enumerate(land_mask) if not v]
        if not ocean:
            return land_mask, elevation

        visited: set[int] = set()
        for start_idx in ocean:
            if start_idx in visited:
                continue
            component: list[int] = []
            queue = [start_idx]
            visited.add(start_idx)
            touches_edge = False
            while queue:
                ci = queue.pop(0)
                component.append(ci)
                cx, cy = ci % w, ci // w
                if cx == 0 or cx == w - 1 or cy == 0 or cy == h - 1:
                    touches_edge = True
                for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                    nx, ny = cx + dx, cy + dy
                    if 0 <= nx < w and 0 <= ny < h:
                        ni = ny * w + nx
                        if ni not in visited and not land_mask[ni]:
                            visited.add(ni)
                            queue.append(ni)
            if not touches_edge:
                depth = max(-200.0, -5.0 - len(component) * 0.05)
                for ci in component:
                    land_mask[ci] = False
                    elevation[ci] = depth
        return land_mask, elevation

    # ── 气候计算 ──────────────────────────────────────────

    def _compute_climate(
        self, elevation: list[float], land_mask: list[bool], w: int, h: int,
    ) -> tuple[list[float], list[float], list[bool], list[int]]:
        """计算温度、降雨、积雪、气候带。

        温度 = 海平面纬度温度 - 海拔 × 6.5°C/km
        降雨 = 噪声 × 雨影因子
        积雪 = 年均温 < 0°C
        """
        from .climate import rainfall_from_noise, climate_zone_from_values

        lat_noise = PerlinNoise(self._seed + 99999)
        lat_freq = 1.0 / 40000.0
        rain_noise = PerlinNoise(self._seed + 88888)
        rain_freq = 1.0 / 25000.0
        rain_shadow = self._compute_rain_shadow(elevation, w, h)
        LAPSE = 6.5 / 1000.0

        temp_field: list[float] = []
        rain_field: list[float] = []
        snow_mask: list[bool] = []
        climate_field: list[int] = []

        for y in range(h):
            for x in range(w):
                wx = (x + 0.5) * self._params.sample_resolution
                wy = (y + 0.5) * self._params.sample_resolution
                idx = y * w + x

                lat_n = lat_noise.octave(wx * lat_freq, wy * lat_freq, octaves=1)
                sea_temp = lat_n * 25.0 + 10.0
                sea_temp = max(-20.0, min(38.0, sea_temp))

                elev = elevation[idx]
                temp = sea_temp - elev * LAPSE
                temp = max(-20.0, min(36.0, temp))

                rain_n = rain_noise.octave(wx * rain_freq, wy * rain_freq, octaves=3)
                rainfall = rainfall_from_noise(rain_n) * rain_shadow[idx]
                climate = climate_zone_from_values(temp, rainfall)

                temp_field.append(temp)
                rain_field.append(rainfall)
                snow_mask.append(temp < 0.0)
                climate_field.append(int(climate))

        return temp_field, rain_field, snow_mask, climate_field

    def _compute_rain_shadow(
        self, elevation: list[float], w: int, h: int,
    ) -> list[float]:
        """雨影因子：盛行西风，山脉背风面降雨锐减。"""
        trace_steps = 20
        factors: list[float] = []
        for y in range(h):
            for x in range(w):
                idx = y * w + x
                total_uplift = 0.0
                prev_elev = elevation[idx]
                for step in range(1, trace_steps + 1):
                    nx = x - step
                    if nx < 0:
                        break
                    ni = y * w + nx
                    uplift = elevation[ni] - prev_elev
                    if uplift > 0:
                        total_uplift += uplift
                    prev_elev = elevation[ni]

                if total_uplift < 50:
                    f = 1.0
                elif total_uplift < 200:
                    f = 1.0 - (total_uplift - 50) / 150 * 0.3
                elif total_uplift < 500:
                    f = 0.7 - (total_uplift - 200) / 300 * 0.3
                else:
                    f = max(0.3, 0.4 - (total_uplift - 500) / 2000 * 0.2)
                factors.append(f)
        return factors


__all__ = ["ContinentParams", "ContinentData", "ContinentGenerator"]
