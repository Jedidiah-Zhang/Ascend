"""TileGenerator — 详细地图层地形生成器。

在构造海拔（tectonic）基础上叠加微噪声，按海拔分带
+ 群系差异化生成最终 TerrainType。

算法：
  1. 构造海拔 batch → per-tile 基础海拔
  2. 高频噪声 → 微地形起伏
  3. 海拔分带 + 细节噪声 → TerrainType
"""

from .chunk import ChunkData
from .biome import get_template
from .noise import PerlinNoise
from .tectonic import tectonic_altitude_batch
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

    def generate(
        self,
        chunk: ChunkData,
        *,
        erosion_droplets: int = 0,
    ) -> TileGrid:
        """为给定分块生成详细 tile 网格。

        Args:
            chunk: 大地图层分块数据。
            erosion_droplets: 水力侵蚀水滴数。0=跳过, 1000=轻度, 5000=中度。

        Returns:
            200×200 的 TileGrid。
        """
        if chunk.biome.is_ocean:
            return self._generate_ocean(chunk)
        else:
            return self._generate_land(chunk, erosion_droplets=erosion_droplets)

    # ── 陆地生成 ────────────────────────────────────────────

    def _generate_land(
        self,
        chunk: ChunkData,
        *,
        erosion_droplets: int = 0,
    ) -> TileGrid:
        """陆地群系的 tile 生成。

        构造海拔决定宏观分带，可选水力侵蚀细化地形。

        Args:
            chunk: 大陆群系的 ChunkData。
            erosion_droplets: 侵蚀水滴数，0=跳过。

        Returns:
            TileGrid。
        """
        size = TILE_MAP_SIZE
        world_x = chunk.cx * size
        world_y = chunk.cy * size
        n = size * size

        # 1. 构造海拔 — tile 粒度，确定性
        tectonic_alts = tectonic_altitude_batch(
            world_x, world_y, size, size, self._seed)

        # 1.5. 可选水力侵蚀 — 塑造河谷和冲积地形
        if erosion_droplets > 0:
            from .erosion import hydraulic_erosion
            tectonic_alts = hydraulic_erosion(
                tectonic_alts, size, size,
                seed=self._seed,
                droplets=erosion_droplets,
            )

        # 2. 微地形噪声
        elevation = self._noise_elevation.octave_grid(
            world_x, world_y, size, size,
            frequency=_TILE_FREQ_ELEVATION,
            octaves=4, persistence=0.5, lacunarity=2.0,
        )
        detail = self._noise_detail.octave_grid(
            world_x, world_y, size, size,
            frequency=_TILE_FREQ_DETAIL,
            octaves=3, persistence=0.5, lacunarity=2.0,
        )

        # 3. 海拔分带分类
        grid = TileGrid()
        for i in range(n):
            ta = tectonic_alts[i]
            e = elevation[i]
            d = detail[i]

            # 海平面附近衰减因子：0~50m 内微噪声减弱，保持平缓
            coastal_flat = 1.0
            if ta < 50.0:
                coastal_flat = max(0.2, ta / 50.0)  # 0m→0.2, 50m→1.0

            if ta < 0.0:
                # ── 海洋 ──
                terrain = TerrainType.DEEP_WATER if ta < -200.0 else TerrainType.SHALLOW_WATER

            elif ta < 200.0:
                # ── 海岸平地（拓宽到 200m） ──
                e_flat = e * coastal_flat * 0.6  # 噪声衰减
                if e_flat < -0.5:
                    terrain = TerrainType.SHALLOW_WATER
                elif e_flat < -0.2:
                    terrain = TerrainType.MARSH
                else:
                    terrain = self._classify_flat(e_flat, d, chunk, ta)

            elif ta < 1000.0:
                # ── 丘陵 ──
                e_mod = e * coastal_flat
                if e_mod > 0.65:
                    terrain = TerrainType.ROCK
                else:
                    terrain = self._classify_flat(e_mod, d, chunk, ta)

            elif ta < 2500.0:
                # ── 山腰 ──
                if d > 0.3 or e > 0.4:
                    terrain = TerrainType.STEEP_SLOPE
                else:
                    terrain = TerrainType.ROCK

            else:
                # ── 高山 ──
                if ta > 4000.0 or d > 0.5:
                    terrain = TerrainType.MOUNTAIN_PEAK
                else:
                    terrain = TerrainType.STEEP_SLOPE

            grid._data[i] = int(terrain)

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
