"""TileGenerator — 层2 详细地图 tile 生成器。

对每个 200×200 chunk，从层1宏观场采样 + 叠加高频细节噪声，
按海拔带分类为 TerrainType。群系通过 TerrainBias 偏移海拔阈值，
保证 chunk 边界连续（隶属度混合）。

海拔带数据在 terrain.TERRAIN_DEFS（每地形一 AltitudeBand，含
priority 与 bias 偏移键）——加地形/调带不碰本模块。

用法:
    from ascend.space.tile_gen import TileGenerator
    from ascend.space.continent import ContinentGenerator

    continent = ContinentGenerator(seed=42).generate()
    tile_gen = TileGenerator(seed=42, continent=continent)
    grid = tile_gen.generate_chunk(cx=10, cy=5)

    # 或传入 ChunkData（推荐，复用 chunk 级气候属性）
    grid = tile_gen.generate_chunk_for(chunk_data)
"""

from .terrain import (
    TERRAIN_DEFS,
    WATER_TYPES,
    AltitudeBand,
    TerrainType,
)
from .tile_grid import TileGrid, TILE_MAP_SIZE
from .noise import PerlinNoise
from .climate import LAPSE_RATE
from .biome import TerrainBias, biome_membership, get_template


from ascend.config import (
    STEEP_GRADIENT as _STEEP_GRADIENT,
    MOISTURE_TILE_FREQUENCY as _MOISTURE_FREQ,
    TERRAIN_NOISE_FREQUENCY as _TERRAIN_NOISE_FREQ,
    TERRAIN_NOISE_AMPLITUDE as _TERRAIN_NOISE_AMP,
)


# ── 分类带（注册表派生，模块级一次性构建） ──────────────────
# 每个声明了 altitude 的地形一条带，按 priority 降序；重叠时高者胜。
# 无带命中返回 fallback 地形（SAND，全表唯一）。

_BANDS: list[tuple[AltitudeBand, TerrainType]] = sorted(
    (
        (defn.altitude, TerrainType[name])
        for name, defn in TERRAIN_DEFS.items()
        if defn.altitude is not None
    ),
    key=lambda item: item[0].priority,
    reverse=True,
)

_FALLBACK_TERRAIN: TerrainType = next(
    TerrainType[name]
    for name, defn in TERRAIN_DEFS.items()
    if defn.fallback
)


class TileGenerator:
    """详细地图地形生成器。

    从 ContinentData 层1宏观场采样宏观海拔、河流宽度，
    叠加高频细节噪声后按带分类地形。群系通过 TerrainBias
    偏移分类阈值，chunk 边界因隶属度混合而连续。
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
        # moisture 噪声（沙漠细分用，tile 级连续采样）
        self._moisture_noise = PerlinNoise(seed + 700)

    def __repr__(self) -> str:
        return f"TileGenerator(seed={self._seed})"

    # ── 主入口 ──────────────────────────────────────────────

    def generate_chunk(self, cx: int, cy: int) -> TileGrid:
        """生成一个 200×200 chunk 的详细地形。

        从 ContinentData 采样 chunk 中心气候属性计算群系隶属度，
        tile 间仅海拔和 moisture 噪声变化，保证 chunk 边界连续。

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
          1. 宏观海拔 + 细节噪声 → 基础地形分类（群系 bias 偏移）
          2. 叠加河流（蛇曲路径 + 河道雕刻）
          3. 叠加湖泊（水面平整 + 湿地，复用步骤1的宏观海拔）

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

        # 批量采样细节噪声
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

        # 预分配宏观海拔缓存（坡度计算用——避免 ±50m 细节噪声
        # 产生虚假陡坡；有湖泊时同时供湖泊渲染复用）
        macro_elev_arr = [0.0] * (size * size)

        hyd = cont.hydrology
        has_lakes = hyd is not None and hyd.lake_basins

        # chunk 中心气候（整 chunk 复用，减少 40,000 次到 1 次）
        cc_temp, cc_rain, _, _ = self._continent.get_chunk_climate(cx, cy)

        for ty in range(size):
            for tx in range(size):
                idx = ty * size + tx
                wx = world_x0 + tx
                wy = world_y0 + ty

                # 宏观海拔（双线性插值）
                macro_elev = cont.sample_altitude_bilinear(wx, wy)

                # 细节噪声（±50m，波长 200m → 自然过渡）
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

                # 地形分类（bias 偏移）
                terrain = self._classify(elev, bias)
                grid.set(tx, ty, terrain)
                grid.set_elevation(tx, ty, elev)

                # 缓存宏观海拔（坡度计算 + 湖泊渲染复用）
                macro_elev_arr[idx] = macro_elev

        # 坡度计算 + STEEP_SLOPE 重分类（基于宏观海拔的局部梯度，
        # 不含 ±50m 细节噪声——噪声纹理不应产生虚假陡坡）
        _compute_slopes(grid, source=macro_elev_arr)
        _reclassify_steep(grid)

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
        sand_delta = 0.0
        fertile_shift = 0.0
        rock_delta = 0.0
        peak_delta = 0.0
        marsh = 0.0
        for biome, weight in membership:
            b = get_template(biome).terrain_bias
            sand_delta += b.sand_cap_delta * weight
            fertile_shift += b.fertile_shift * weight
            rock_delta += b.rock_threshold_delta * weight
            peak_delta += b.peak_threshold_delta * weight
            marsh += b.marsh_tendency * weight
        return TerrainBias(
            sand_cap_delta=sand_delta,
            fertile_shift=fertile_shift,
            rock_threshold_delta=rock_delta,
            peak_threshold_delta=peak_delta,
            marsh_tendency=marsh,
        )

    # ── 地形分类 ──────────────────────────────────────────

    def _classify(
        self,
        elev: float,
        bias: TerrainBias,
    ) -> TerrainType:
        """根据海拔、群系偏移分类 tile 地形（注册表海拔带驱动）。

        水体（河流/湖泊）由后续步骤叠加覆盖——不在此处判定。
        STEEP_SLOPE 不在此处判定——由坡度计算后重分类。

        Args:
            elev: 最终海拔 (m)。
            bias: 群系偏移参数。

        Returns:
            TerrainType。
        """
        for band, terrain in _BANDS:
            lo = band.lo if band.lo is not None else float("-inf")
            hi = band.hi if band.hi is not None else float("inf")
            if band.lo_delta:
                lo += getattr(bias, band.lo_delta, 0.0)
            if band.hi_delta:
                hi += getattr(bias, band.hi_delta, 0.0)
            if lo <= elev < hi:
                return terrain
        return _FALLBACK_TERRAIN


# ── 坡度计算与陡坡重分类 ──────────────────────────────────


def _compute_slopes(grid: TileGrid, source: list[float] | None = None) -> None:
    """计算每个 tile 的最大局部梯度（m/m），存入 grid._slope。

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


def _reclassify_steep(grid: TileGrid) -> None:
    """将局部梯度超过阈值的 tile 重分类为 STEEP_SLOPE。

    仅对非水体、非豁免地形（SAND/MOUNTAIN_PEAK，注册表
    no_steep_reclass 声明）的陆地 tile 生效。
    """
    size = grid.size
    for y in range(size):
        for x in range(size):
            slope = grid.get_slope(x, y)
            if slope <= _STEEP_GRADIENT:
                continue
            terrain = grid.get(x, y)
            if terrain in WATER_TYPES:
                continue
            if TERRAIN_DEFS[terrain.name].no_steep_reclass:
                continue
            grid.set(x, y, TerrainType.STEEP_SLOPE)


__all__ = ["TileGenerator"]
