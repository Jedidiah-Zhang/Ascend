"""TileGenerator — 层2 详细地图 tile 生成器。

对每个 200×200 chunk，从层1宏观场采样 + 叠加高频细节噪声，
按海拔带分类为 TerrainType。群系通过 TerrainBias 偏移海拔阈值，
保证 chunk 边界连续（隶属度混合）。

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
    BASE_SAND_CAP as _BASE_SAND_CAP,
    BASE_FERTILE_LO as _BASE_FERTILE_LO,
    BASE_FERTILE_HI as _BASE_FERTILE_HI,
    BASE_GRASSLAND_CAP as _BASE_GRASSLAND_CAP,
    BASE_ROCK_THRESHOLD as _BASE_ROCK_THRESHOLD,
    BASE_PEAK_THRESHOLD as _BASE_PEAK_THRESHOLD,
    STEEP_GRADIENT as _STEEP_GRADIENT,
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
        return self._generate(cx, cy, chunk=None)

    def generate_chunk_for(self, chunk) -> TileGrid:
        """为已生成的 ChunkData 生成详细地形（推荐入口）。

        复用 chunk 级气候属性，与 chunk.biome 保持一致。
        tile 级仍重新采样连续场算隶属度（保证边界连续）。

        Args:
            chunk: ChunkData（大地图层数据）。

        Returns:
            200×200 TileGrid。
        """
        return self._generate(chunk.cx, chunk.cy, chunk=chunk)

    def _generate(self, cx: int, cy: int, chunk) -> TileGrid:
        """内部生成逻辑。

        管线：
          1. 宏观海拔 + 细节噪声 → 基础地形分类（群系 bias 偏移）
          2. 叠加河流（蛇曲路径 + 河道雕刻）
          3. 叠加湖泊（水面平整 + 湿地，复用步骤1的宏观海拔）

        Args:
            cx, cy: chunk 坐标。
            chunk: 可选 ChunkData（当前未直接使用，tile 级重采样保证连续）。

        Returns:
            200×200 TileGrid。
        """
        size = TILE_MAP_SIZE
        world_x0 = cx * size
        world_y0 = cy * size

        grid = TileGrid()
        cont = self._continent
        detail_freq = 0.005

        # 批量采样细节噪声
        noise_field = self._detail_noise.octave_grid(
            world_x0 + 0.5, world_y0 + 0.5, size, size,
            frequency=detail_freq, octaves=4,
        )

        # 批量采样 moisture 噪声
        moisture_field = self._moisture_noise.octave_grid(
            world_x0 + 0.5, world_y0 + 0.5, size, size,
            frequency=0.005, octaves=2,
        )

        # 预分配宏观海拔缓存（仅在有湖泊时用于后续复用）
        hyd = cont.hydrology
        has_lakes = hyd is not None and hyd.lake_basins
        macro_cache = [0.0] * (size * size) if has_lakes else None

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
                detail = noise_field[idx] * 50.0
                elev = macro_elev + detail

                # 海平面温度 = chunk 中心基线 + tile 海拔递减
                sea_temp = cc_temp + macro_elev * LAPSE_RATE / 1000.0
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

                # 缓存宏观海拔（供湖泊渲染复用）
                if macro_cache is not None:
                    macro_cache[idx] = macro_elev

        # 坡度计算 + STEEP_SLOPE 重分类（基于局部梯度而非绝对海拔）
        _compute_slopes(grid)
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
                    seed=self._seed,
                )
            if has_lakes:
                from .lake_render import render_lake_chunk
                render_lake_chunk(
                    grid, world_x0, world_y0,
                    hyd.lake_basins, cont,
                    macro_elev_grid=macro_cache,
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
        """根据海拔、群系偏移分类 tile 地形。

        水体（河流/湖泊）由后续步骤叠加覆盖——不在此处判定。
        STEEP_SLOPE 不在此处判定——由坡度计算后重分类。

        Args:
            elev: 最终海拔 (m)。
            bias: 群系偏移参数。

        Returns:
            TerrainType。
        """
        # 海洋（海拔 < 0 = 水体）
        if elev < -100:
            return TerrainType.DEEP_WATER
        if elev < 0:
            return TerrainType.SHALLOW_WATER

        # 应用群系偏移后的阈值
        sand_cap = _BASE_SAND_CAP + bias.sand_cap_delta
        fertile_lo = _BASE_FERTILE_LO + bias.fertile_shift
        fertile_hi = _BASE_FERTILE_HI + bias.fertile_shift
        grassland_cap = _BASE_GRASSLAND_CAP
        rock_threshold = _BASE_ROCK_THRESHOLD + bias.rock_threshold_delta
        peak_threshold = _BASE_PEAK_THRESHOLD + bias.peak_threshold_delta

        # 海滩/海岸
        if elev < sand_cap:
            return TerrainType.SAND

        # 按海拔带分类（偏移后）
        if elev > peak_threshold:
            return TerrainType.MOUNTAIN_PEAK
        if elev > rock_threshold:
            return TerrainType.ROCK
        if fertile_lo <= elev <= fertile_hi:
            return TerrainType.FERTILE_SOIL
        if elev > grassland_cap:
            return TerrainType.ROCK
        if elev > sand_cap:
            return TerrainType.GRASSLAND

        return TerrainType.SAND


# ── 坡度计算与陡坡重分类 ──────────────────────────────────


def _compute_slopes(grid: TileGrid) -> None:
    """计算每个 tile 的最大局部梯度（m/m），存入 grid._slope。

    对每个 tile，比较其高程与 8 邻域（chunk 内）的高程差，
    取最大值作为该 tile 的坡度。边界 tile 仅考虑 chunk 内的邻居。
    """
    size = grid.size
    directions = [(-1, -1), (0, -1), (1, -1), (-1, 0),
                  (1, 0), (-1, 1), (0, 1), (1, 1)]

    for y in range(size):
        for x in range(size):
            elev = grid.get_elevation(x, y)
            max_delta = 0.0
            for dx, dy in directions:
                nx, ny = x + dx, y + dy
                if 0 <= nx < size and 0 <= ny < size:
                    delta = abs(elev - grid.get_elevation(nx, ny))
                    if delta > max_delta:
                        max_delta = delta
            grid.set_slope(x, y, max_delta)


_WATER_TYPES = frozenset({TerrainType.DEEP_WATER, TerrainType.SHALLOW_WATER})


def _reclassify_steep(grid: TileGrid) -> None:
    """将局部梯度超过阈值的 tile 重分类为 STEEP_SLOPE。

    仅对非水体、非沙滩、非山巅的陆地 tile 生效。
    山巅（MOUNTAIN_PEAK）保留最高优先级，不降级为陡坡。
    """
    size = grid.size
    for y in range(size):
        for x in range(size):
            slope = grid.get_slope(x, y)
            if slope <= _STEEP_GRADIENT:
                continue
            terrain = grid.get(x, y)
            if terrain in _WATER_TYPES:
                continue
            if terrain in (TerrainType.SAND, TerrainType.MOUNTAIN_PEAK):
                continue
            grid.set(x, y, TerrainType.STEEP_SLOPE)


__all__ = ["TileGenerator"]
