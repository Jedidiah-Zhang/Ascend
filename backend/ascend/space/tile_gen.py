"""TileGenerator — 层2 详细地图 tile 生成器。

对每个 200×200 chunk，从层1宏观场采样 + 叠加高频细节噪声（仅海拔
存储），按 issue #42 层次分类判定地表材质。群系通过 TerrainBias
偏移分类阈值，chunk 边界因隶属度混合而连续。

分类输入全为**低频连续场**（宏观海拔 / 面内坡度 / 距水距离 / 气候 /
湿度），细节噪声**退出分类**——±50m 噪声不再产生窄带碎斑（SAND
0–10m 旧带）与虚假陡坡。材质在连续场上逐 tile 判定，相邻 tile
输入连续 → 区域自然连续。

层次判定（顺序即优先级）：
  1. 宏观海拔 < 0            → WATER（海洋；河/湖由 render 后叠加）
  2. 海拔 > 岩线(bias) 或 坡度>裸岩 → ROCK
  3. 寒带(temp<冻土线bias)：近水积水  → MARSH
  4. 寒带非沼泽              → PERMAFROST
  5. 干旱(rain<干旱阈值bias)：高/中海拔 → GRAVEL
  6. 干旱低海拔              → SAND
  7. 距水 < 沙滩带           → SAND
  8. 距水 < 冲积带 且 低海拔  → FERTILE_SOIL
  9. 距水 < 湿地带 且 湿度高  → MARSH
  10. 默认                   → GRASS

用法:
    from ascend.space.tile_gen import TileGenerator
    from ascend.space.continent import ContinentGenerator

    continent = ContinentGenerator(seed=42).generate()
    tile_gen = TileGenerator(seed=42, continent=continent)
    grid = tile_gen.generate_chunk(cx=10, cy=5)

    # 或传入 ChunkData（推荐，复用 chunk 级气候属性）
    grid = tile_gen.generate_chunk_for(chunk_data)
"""

from .terrain import TerrainType
from .tile_grid import TileGrid, TILE_MAP_SIZE
from .noise import PerlinNoise
from .climate import LAPSE_RATE
from .biome import TerrainBias, biome_membership, get_template


from ascend.config import (
    MOISTURE_TILE_FREQUENCY as _MOISTURE_FREQ,
    TERRAIN_NOISE_FREQUENCY as _TERRAIN_NOISE_FREQ,
    TERRAIN_NOISE_AMPLITUDE as _TERRAIN_NOISE_AMP,
    ROCK_LINE_ELEV as _ROCK_LINE_ELEV,
    BARE_ROCK_SLOPE as _BARE_ROCK_SLOPE,
    GRAVEL_ALT_BAND as _GRAVEL_ALT_BAND,
    FERTILE_LOW_ELEV as _FERTILE_LOW_ELEV,
    WETLAND_BAND_M as _WETLAND_BAND_M,
)


class TileGenerator:
    """详细地图地形生成器。

    从 ContinentData 层1宏观场采样宏观海拔、距水距离、河流宽度，
    叠加高频细节噪声（仅海拔存储）后按层次规则分类材质。群系通过
    TerrainBias 偏移分类阈值，chunk 边界因隶属度混合而连续。
    线程安全：每个实例持有独立 PerlinNoise，无共享可变状态。
    """

    def __init__(
        self,
        seed: int,
        continent,  # ContinentData
    ) -> None:
        """初始化 tile 生成器。

        Args:
            seed: 世界种子。
            continent: 层1宏观场数据。
        """
        self._seed = seed
        self._continent = continent
        self._detail_noise = PerlinNoise(seed + 80000)
        # moisture 噪声（沙漠细分/沼泽判定用，tile 级连续采样）
        self._moisture_noise = PerlinNoise(seed + 700)

    def __repr__(self) -> str:
        return f"TileGenerator(seed={self._seed})"

    # ── 主入口 ──────────────────────────────────────────────

    def generate_chunk(self, cx: int, cy: int) -> TileGrid:
        """生成一个 200×200 chunk 的详细地形。

        从 ContinentData 采样 chunk 中心气候属性计算群系隶属度，
        tile 间仅海拔/距水/湿度等连续场变化，保证 chunk 边界连续。

        Args:
            cx: chunk X 坐标。
            cy: chunk Y 坐标。

        Returns:
            200×200 TileGrid。
        """
        return self._generate(cx, cy)

    def generate_chunk_for(self, chunk) -> TileGrid:
        """为已生成的 ChunkData 生成详细地形（推荐入口）。

        复用 chunk 级气候属性，与 chunk.biome 保持一致。
        tile 级仍重新采样连续场算隶属度（保证边界连续）。

        Args:
            chunk: ChunkData（大地图层数据）。

        Returns:
            200×200 TileGrid。
        """
        return self._generate(chunk.cx, chunk.cy)

    def _generate(self, cx: int, cy: int) -> TileGrid:
        """内部生成逻辑。

        管线：
          1. 采样宏观海拔场（分类/坡度输入，不含细节噪声）
          2. 坡度（基于宏观海拔）
          3. 逐 tile 层次分类（低频场输入） + 存储海拔（含细节噪声）
          4. 叠加河流/湖泊（render 覆盖为 WATER）

        Args:
            cx, cy: chunk 坐标。

        Returns:
            200×200 TileGrid。
        """
        size = TILE_MAP_SIZE
        world_x0 = cx * size
        world_y0 = cy * size

        grid = TileGrid()
        cont = self._continent

        # 批量采样细节噪声（仅海拔存储用）
        noise_field = self._detail_noise.octave_grid(
            world_x0 + 0.5, world_y0 + 0.5, size, size,
            frequency=_TERRAIN_NOISE_FREQ, octaves=4,
        )

        # 批量采样 moisture 噪声 — 世界坐标场，与 chunk 级
        # （generator._sample_moisture_at_chunk）同一频率同一八度数，
        # 保证 chunk 标签与 tile 隶属度一致
        moisture_field = self._moisture_noise.octave_grid(
            world_x0 + 0.5, world_y0 + 0.5, size, size,
            frequency=_MOISTURE_FREQ, octaves=4,
        )

        # Pass 0: 宏观海拔场（分类/坡度输入；不含 ±50m 细节噪声——
        # 噪声纹理不应产生虚假陡坡/裸岩；湖泊渲染复用）
        macro_elev_arr = [0.0] * (size * size)
        for ty in range(size):
            for tx in range(size):
                idx = ty * size + tx
                macro_elev_arr[idx] = cont.sample_altitude_bilinear(
                    world_x0 + tx, world_y0 + ty,
                )

        # 坡度（基于宏观海拔）
        _compute_slopes(grid, source=macro_elev_arr)
        slope_arr = grid.slope_raw()

        hyd = cont.hydrology
        has_lakes = hyd is not None and hyd.lake_basins

        # chunk 中心气候（整 chunk 复用）
        cc_temp, cc_rain, _, _ = self._continent.get_chunk_climate(cx, cy)

        # Pass 1: 逐 tile 层次分类 + 海拔存储
        for ty in range(size):
            for tx in range(size):
                idx = ty * size + tx
                wx = world_x0 + tx
                wy = world_y0 + ty

                macro_elev = macro_elev_arr[idx]
                slope = slope_arr[idx]
                water_dist = cont.sample_water_distance_bilinear(wx, wy)

                # 细节噪声（±50m，仅海拔存储——渲染崖壁/阴影/等高线）
                detail = noise_field[idx] * _TERRAIN_NOISE_AMP
                elev = macro_elev + detail

                # 海平面温度 = chunk 中心基线 + tile 海拔递减（直减率仅
                # 作用于陆地；海域 tile 直接取 chunk 中心海面温度）
                sea_temp = cc_temp + max(0.0, macro_elev) * LAPSE_RATE / 1000.0
                moisture = moisture_field[idx]

                # 群系隶属度 → 混合 TerrainBias（温度降雨用 chunk 中心值）
                bias = self._compute_bias(
                    cc_temp, cc_rain, macro_elev, sea_temp, moisture,
                    subdiv_ranges=cont.subdiv_ranges,
                )

                # 层次分类（低频连续场输入）
                terrain = self._classify(
                    macro_elev, slope, water_dist,
                    cc_temp, cc_rain, moisture, bias,
                )
                grid.set(tx, ty, terrain)
                grid.set_elevation(tx, ty, elev)

        # 叠加水体（河流 + 湖泊）
        if hyd is not None:
            has_rivers = (
                hyd.river_network is not None and hyd.river_network.rivers
            )
            if has_rivers:
                from .river_render import render_river_chunk
                render_river_chunk(
                    grid, world_x0, world_y0,
                    hyd, cont,
                )
            if has_lakes:
                from .lake_render import render_lake_chunk
                render_lake_chunk(
                    grid, world_x0, world_y0,
                    hyd.lake_basins, cont, self._seed,
                    macro_elev_grid=macro_elev_arr,
                )

        return grid

    # ── 群系偏移计算 ────────────────────────────────────────

    @staticmethod
    def _compute_bias(
        temp: float,
        rain: float,
        altitude: float,
        sea_temp: float,
        moisture: float,
        subdiv_ranges: dict[int, tuple[float, float]] | None = None,
    ) -> TerrainBias:
        """根据 tile 级气候属性算群系隶属度，混合 TerrainBias。

        数值字段加权平均；chunk 边界因连续场 → 隶属度连续 → bias 连续。

        Args:
            temp: tile 年均温。
            rain: tile 年降雨。
            altitude: tile 海拔（用宏观海拔，非细节噪声后的）。
            sea_temp: tile 海平面温度。
            moisture: tile moisture 噪声。
            subdiv_ranges: 动态值域（来自 ContinentData）。

        Returns:
            混合后的 TerrainBias。
        """
        membership = biome_membership(
            temp, rain, altitude, sea_temp, moisture,
            subdiv_ranges=subdiv_ranges,
        )
        if len(membership) == 1:
            return get_template(membership[0][0]).terrain_bias

        # 加权混合数值字段
        rock_line_delta = 0.0
        arid_rainfall = 0.0
        beach_band = 0.0
        alluvial_band = 0.0
        marsh = 0.0
        permafrost = 0.0
        for biome, weight in membership:
            b = get_template(biome).terrain_bias
            rock_line_delta += b.rock_line_delta * weight
            arid_rainfall += b.arid_rainfall_mm * weight
            beach_band += b.beach_band_m * weight
            alluvial_band += b.alluvial_band_m * weight
            marsh += b.marsh_tendency * weight
            permafrost += b.permafrost_temp_c * weight
        return TerrainBias(
            rock_line_delta=rock_line_delta,
            arid_rainfall_mm=arid_rainfall,
            beach_band_m=beach_band,
            alluvial_band_m=alluvial_band,
            marsh_tendency=marsh,
            permafrost_temp_c=permafrost,
        )

    # ── 材质分类（层次判定，低频连续场输入） ───────────────

    def _classify(
        self,
        macro_elev: float,
        slope: float,
        water_dist: float,
        temp: float,
        rain: float,
        moisture: float,
        bias: TerrainBias,
    ) -> TerrainType:
        """按层次规则判定 tile 地表材质（issue #42）。

        输入全为低频连续场（宏观海拔/坡度/距水/气候/湿度），无细节
        噪声——相邻 tile 输入连续 → 材质区域连续，根治碎斑。
        河/湖由后续 render 步骤覆盖为 WATER（不在此判定）。

        Args:
            macro_elev: 宏观海拔 (m)。
            slope: 面内最大坡度 (m/m，基于宏观海拔)。
            water_dist: 距最近水体距离 (m)。
            temp: tile 年均温 (°C)。
            rain: tile 年降雨 (mm)。
            moisture: tile 湿度噪声 [-1, 1]。
            bias: 群系偏移参数。

        Returns:
            TerrainType。
        """
        # 1. 海洋
        if macro_elev < 0.0:
            return TerrainType.WATER
        # 2. 岩线 / 裸岩坡度 → ROCK
        rock_line = _ROCK_LINE_ELEV + bias.rock_line_delta
        if macro_elev > rock_line or slope > _BARE_ROCK_SLOPE:
            return TerrainType.ROCK
        # 3-4. 寒带（年均温 < 冻土线）：低洼积水 → 沼泽，否则冻土
        if temp < bias.permafrost_temp_c:
            # 寒带积水带取冲积带 ×2：冻土区融化水洼/湿地范围比温带冲积带
            # 更宽（季节冻融漫溢）；×2 为经验系数，visual 调参期校准。
            if (
                water_dist < bias.alluvial_band_m * 2.0
                and (moisture > 0.0 or bias.marsh_tendency > 0.3)
            ):
                return TerrainType.MARSH
            return TerrainType.PERMAFROST
        # 5-6. 干旱（年降雨 < 干旱阈值）：高/中海拔砾石，低海拔沙
        if rain < bias.arid_rainfall_mm:
            if macro_elev >= _GRAVEL_ALT_BAND[0]:
                return TerrainType.GRAVEL
            return TerrainType.SAND
        # 7. 沙滩带
        if water_dist < bias.beach_band_m:
            return TerrainType.SAND
        # 8. 冲积带（低海拔沃土）
        if water_dist < bias.alluvial_band_m and macro_elev < _FERTILE_LOW_ELEV:
            return TerrainType.FERTILE_SOIL
        # 9. 湿地带（湿度高 → 沼泽）
        if water_dist < _WETLAND_BAND_M and moisture + bias.marsh_tendency > 0.3:
            return TerrainType.MARSH
        # 10. 默认
        return TerrainType.GRASSLAND


# ── 坡度计算 ──────────────────────────────────────────────


def _compute_slopes(grid: TileGrid, source: list[float] | None = None) -> None:
    """计算每个 tile 的最大局部梯度 (m/m)，存入 grid._slope。

    对每个 tile，比较其高程与 8 邻域（chunk 内）的高程差，
    除以邻域距离（1 tile = 100m，对角 141.4m）得真实斜率，
    取最大值作为该 tile 的坡度。边界 tile 仅考虑 chunk 内的邻居。

    Args:
        grid: 目标 TileGrid。
        source: 可选高程源数组（如宏观海拔）。为 None 时用 grid 自身
            高程（含细节噪声）。坡度应反映地形而非噪声纹理，生成
            管线传宏观海拔数组。
    """
    size = grid.size
    directions = [(-1, -1, 141.4), (0, -1, 100.0), (1, -1, 141.4),
                  (-1, 0, 100.0), (1, 0, 100.0), (-1, 1, 141.4),
                  (0, 1, 100.0), (1, 1, 141.4)]

    for y in range(size):
        for x in range(size):
            elev = (
                source[y * size + x] if source is not None
                else grid.get_elevation(x, y)
            )
            max_slope = 0.0
            for dx, dy, dist in directions:
                nx, ny = x + dx, y + dy
                if 0 <= nx < size and 0 <= ny < size:
                    ne = (
                        source[ny * size + nx] if source is not None
                        else grid.get_elevation(nx, ny)
                    )
                    slope = abs(elev - ne) / dist
                    if slope > max_slope:
                        max_slope = slope
            grid.set_slope(x, y, max_slope)


__all__ = ["TileGenerator"]
