"""TileGenerator — 详细地图层地形生成器（骨架）。

待 Voronoi 构造模块实现后，在此接入 per-tile 海拔数据。
当前为占位实现。
"""

from .chunk import ChunkData
from .biome import get_template
from .noise import PerlinNoise
from .terrain import TerrainType
from .tile_grid import TileGrid, TILE_MAP_SIZE

# tile 层噪声频率
_TILE_FREQ_ELEVATION: float = 0.015
_TILE_FREQ_DETAIL: float = 0.04


class TileGenerator:
    """详细地图地形生成器。

    构造海拔提供宏观结构（海洋/平原/山脉），
    微噪声提供局部纹理（草地/沙地/岩石）。

    用法:
        gen = TileGenerator(seed=42)
        grid = gen.generate(chunk)
    """

    def __init__(self, seed: int) -> None:
        """初始化 tile 生成器。

        Args:
            seed: 世界种子，派生独立的噪声子种子。
        """
        self._noise_elevation = PerlinNoise(seed + 800)
        self._noise_detail = PerlinNoise(seed + 900)
        self._seed = seed

    def __repr__(self) -> str:
        return f"TileGenerator(seed={self._seed})"

    # ── 主入口 ──────────────────────────────────────────────

    def generate(self, chunk: ChunkData) -> TileGrid:
        """为给定分块生成详细 tile 网格（占位）。

        TODO: 接入 Voronoi 构造海拔 + 水力侵蚀。
        """
        if chunk.biome.is_ocean:
            return self._generate_ocean(chunk)
        else:
            return self._generate_land(chunk)

    # ── 陆地生成 ────────────────────────────────────────────

    def _generate_land(self, chunk: ChunkData) -> TileGrid:
        """陆地群系的 tile 生成（占位）。

        TODO: 接入 Voronoi 构造海拔 + 水力侵蚀，按海拔分带分类。
        """
        size = TILE_MAP_SIZE
        grid = TileGrid()
        # 占位：全部填充为草地
        for i in range(size * size):
            grid._data[i] = int(TerrainType.GRASSLAND)
        return grid

    # ── 海洋生成 ────────────────────────────────────────────

    def _generate_ocean(self, chunk: ChunkData) -> TileGrid:
        """海洋群系的 tile 生成。"""
        size = TILE_MAP_SIZE
        world_x = chunk.cx * size
        world_y = chunk.cy * size

        depth = self._noise_elevation.octave_grid(
            world_x, world_y, size, size,
            frequency=_TILE_FREQ_ELEVATION,
            octaves=3, persistence=0.5, lacunarity=2.0,
        )

        grid = TileGrid()
        n = size * size
        sorted_depth = sorted(depth)
        deep_cutoff = sorted_depth[int(n * 0.3)]

        for i in range(n):
            grid._data[i] = int(
                TerrainType.DEEP_WATER if depth[i] < deep_cutoff
                else TerrainType.SHALLOW_WATER
            )
        return grid

    # ── 平地细分 ────────────────────────────────────────────

    def _classify_flat(
        self,
        elevation: float,
        detail: float,
        chunk: ChunkData,
        base_altitude: float,
    ) -> TerrainType:
        """在平坦区域中按群系和细节噪声细分地形。

        Args:
            elevation: 微地形噪声值。
            detail: 细节噪声值。
            chunk: 所属分块。
            base_altitude: per-tile 构造海拔。

        Returns:
            细分后的 TerrainType。
        """
        biome = chunk.biome

        # 陡坡过渡带
        if elevation > 0.4:
            return TerrainType.ROCK

        # 低洼沼泽
        if elevation < -0.3 and base_altitude < 500.0:
            return TerrainType.MARSH

        # 群系差异化
        if biome.name == "ARID_SHRUBLAND":
            if detail > 0.4:
                return TerrainType.ROCK
            elif detail > -0.3:
                return TerrainType.SAND
            else:
                return TerrainType.GRASSLAND
        else:
            if detail > 0.5:
                return TerrainType.FERTILE_SOIL
            elif detail > -0.3:
                return TerrainType.GRASSLAND
            else:
                return TerrainType.SAND
