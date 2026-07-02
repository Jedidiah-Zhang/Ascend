"""TileGenerator — 层2 详细地图 tile 生成器。

对每个 200×200 chunk，从层1宏观场采样 + 叠加高频细节噪声，
按海拔带分类为 TerrainType。

用法:
    from ascend.space.tile_gen import TileGenerator
    from ascend.space.continent import ContinentGenerator

    continent = ContinentGenerator(seed=42).generate()
    tile_gen = TileGenerator(seed=42, continent=continent)
    grid = tile_gen.generate_chunk(cx=10, cy=5)
"""

from .terrain import TerrainType
from .tile_grid import TileGrid, TILE_MAP_SIZE
from .noise import PerlinNoise


class TileGenerator:
    """详细地图地形生成器。

    从 ContinentData 层1宏观场采样宏观海拔、河流宽度，
    叠加高频细节噪声后按带分类地形。
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

    def __repr__(self) -> str:
        return f"TileGenerator(seed={self._seed})"

    # ── 主入口 ──────────────────────────────────────────────

    def generate_chunk(self, cx: int, cy: int) -> TileGrid:
        """生成一个 200×200 chunk 的详细地形。

        管线：
          1. 宏观海拔采样 + 细节噪声 → 基础地形分类
          2. 叠加河流（蛇曲路径 + 河道雕刻）
          3. 叠加湖泊（水面平整 + 湿地）

        Args:
            cx: chunk X 坐标。
            cy: chunk Y 坐标。

        Returns:
            200×200 TileGrid。
        """
        size = TILE_MAP_SIZE
        world_x0 = cx * size
        world_y0 = cy * size

        grid = TileGrid()
        cont = self._continent
        detail_freq = 0.005  # 波长 ~200m，大面积连续地貌

        # 批量采样细节噪声——一次 C 调用替代 40K 次 ctypes 跨越
        # +0.5 偏移避免采样在整数网格点（Perlin 噪声在整数点恒为 0）
        # 低频低幅：宏观海拔主导地形，噪声仅添加 ±50m 微妙起伏
        noise_field = self._detail_noise.octave_grid(
            world_x0 + 0.5, world_y0 + 0.5, size, size,
            frequency=detail_freq, octaves=4,
        )

        for ty in range(size):
            for tx in range(size):
                wx = world_x0 + tx
                wy = world_y0 + ty

                # 1. 宏观海拔（双线性插值，消除 100m 网格块状伪影）
                macro_elev = cont.sample_altitude_bilinear(wx, wy)

                # 2. 细节噪声（±50m，波长 200m → 自然过渡，不过度碎化）
                detail = noise_field[ty * size + tx] * 50.0
                elev = macro_elev + detail

                # 3. 地形分类（海拔 + 温度 + 积雪，水体由后续步骤叠加）
                terrain = self._classify(elev, wx, wy)

                grid.set(tx, ty, terrain)

        # 4. 叠加水体（河流 + 湖泊）—— 覆盖在基础地形之上
        if cont.hydrology is not None:
            hyd = cont.hydrology
            if hyd.river_tree is not None and hyd.river_tree.nodes:
                from .river_render import render_river_chunk
                render_river_chunk(
                    grid, world_x0, world_y0,
                    hyd.river_tree,
                    hyd.flow_acc, hyd.directions,
                    cont.cell_size, cont.grid_width,
                    seed=self._seed,
                )
            if hyd.lake_basins:
                from .lake_render import render_lake_chunk
                render_lake_chunk(
                    grid, world_x0, world_y0,
                    hyd.lake_basins, cont,
                )

        return grid

    # ── 地形分类 ──────────────────────────────────────────

    def _classify(
        self,
        elev: float,
        wx: float, wy: float,
    ) -> TerrainType:
        """根据海拔、积雪等条件分类 tile 基础地形。

        水体（河流/湖泊）由后续步骤叠加覆盖——不在此处判定。

        Args:
            elev: 最终海拔 (m)。
            wx, wy: 世界坐标。

        Returns:
            TerrainType。
        """
        # 海洋（海拔 < 0 = 水体）
        if elev < -100:
            return TerrainType.DEEP_WATER
        if elev < 0:
            return TerrainType.SHALLOW_WATER

        # 海滩/海岸
        if elev < 10:
            return TerrainType.SAND

        # 积雪（温度 < 0°C 且海拔 > 800m → 雪顶）
        cont = self._continent
        if cont.temperature_field:
            gx = int(wx / cont.cell_size)
            gy = int(wy / cont.cell_size)
            if 0 <= gx < cont.grid_width and 0 <= gy < cont.grid_height:
                t = cont.temperature_field[gy * cont.grid_width + gx]
                if t < 0 and elev > 800:
                    return TerrainType.MOUNTAIN_PEAK

        # 按海拔带分类（窄带 → 噪声能跨越多带）
        if elev > 2000:
            return TerrainType.MOUNTAIN_PEAK
        if elev > 1200:
            return TerrainType.STEEP_SLOPE
        if elev > 600:
            return TerrainType.ROCK
        if elev > 300:
            return TerrainType.GRASSLAND
        if elev > 100:
            return TerrainType.FERTILE_SOIL
        if elev > 20:
            return TerrainType.GRASSLAND

        return TerrainType.SAND


__all__ = ["TileGenerator"]
