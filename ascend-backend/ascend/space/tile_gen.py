"""TileGenerator — 详细地图层地形生成器。

将 ChunkData（大地图层参数）作为约束，使用高频噪声
生成 200×200 的 TileGrid，chunk 边界处自动连续。

算法：
  1. 高频噪声采样 → per-tile 相对高度
  2. 按群系水/山比率确定阈值
  3. 阈值分类 → TerrainType
  4. 群系差异化调整

当提供 get_altitude 回调时，chunk 基线海拔从常数变为
per-tile 双线性插值，消除 chunk 边界的地形跳变。
"""

from collections.abc import Callable

from .chunk import ChunkData
from .biome import get_template
from .noise import PerlinNoise
from .terrain import TerrainType
from .tile_grid import TileGrid, TILE_MAP_SIZE

# tile 层噪声频率 — 比大地图层高约 40 倍（chunk 间距 vs tile 间距）
_TILE_FREQ_ELEVATION: float = 0.015   # 地形起伏主频率（波长 ~67 tile）
_TILE_FREQ_DETAIL: float = 0.04       # 细节噪声（波长 ~25 tile）

# 陆地群系：在山地/水体区域内细分的比例
# 阈值从排序后的噪声值动态计算，确保在所有噪声分布下都有合理的细分
_WATER_DEEP_RATIO: float = 0.25     # 水体中最深的 25% 为深水
_MOUNTAIN_PEAK_RATIO: float = 0.33  # 山地中最高的 33% 为山巅


class TileGenerator:
    """详细地图地形生成器。

    使用独立的高频噪声通道，从 chunk 的群系和气候参数
    生成 200×200 的地形网格。

    用法:
        gen = TileGenerator(seed=42)
        grid = gen.generate(chunk)
    """

    def __init__(self, seed: int) -> None:
        """初始化 tile 生成器。

        Args:
            seed: 世界种子，派生独立的噪声子种子。
        """
        # 独立噪声通道 — 子种子远离 WorldGenerator 的 7 个通道
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
        get_altitude: Callable[[int, int], float] | None = None,
    ) -> TileGrid:
        """为给定分块生成详细 tile 网格。

        Args:
            chunk: 大地图层分块数据（群系、气候、海拔等）。
            get_altitude: 可选 — 接受 chunk 坐标 (cx, cy) 返回基线海拔 (m)。
                提供时，每个 tile 的基线海拔由周围 4 个 chunk 中心
                做双线性插值得到，chunk 边界处地形平滑过渡。
                未提供时使用 chunk.annual_baseline.altitude 常数（旧行为）。

        Returns:
            200×200 的 TileGrid。
        """
        if chunk.biome.is_ocean:
            return self._generate_ocean(chunk)
        else:
            return self._generate_land(chunk, get_altitude=get_altitude)

    # ── 陆地生成 ────────────────────────────────────────────

    def _generate_land(
        self,
        chunk: ChunkData,
        *,
        get_altitude: Callable[[int, int], float] | None = None,
    ) -> TileGrid:
        """陆地群系的 tile 生成。

        步骤:
          1. 采样高程噪声（batch 200×200）
          2. 按群系水/山比率动态计算阈值
          3. 阈值分类 + 细节噪声微调

        Args:
            chunk: 大陆群系的 ChunkData。
            get_altitude: 可选 — 海拔回调，用于 per-tile 双线性插值。

        Returns:
            TileGrid。
        """
        template = get_template(chunk.biome)
        size = TILE_MAP_SIZE
        world_x = chunk.cx * size
        world_y = chunk.cy * size

        # 1. 高频高程噪声 — 批量采样整个 chunk
        elevation = self._noise_elevation.octave_grid(
            world_x, world_y, size, size,
            frequency=_TILE_FREQ_ELEVATION,
            octaves=4,
            persistence=0.5,
            lacunarity=2.0,
        )

        # 2. 细节噪声 — 用于平地内细分（草地 vs 沃土 vs 沙地）
        detail = self._noise_detail.octave_grid(
            world_x, world_y, size, size,
            frequency=_TILE_FREQ_DETAIL,
            octaves=3,
            persistence=0.5,
            lacunarity=2.0,
        )

        # 3. 动态阈值 — 基于群系模板的水体和山地比率
        water_ratio = template.water_ratio
        mountain_ratio = template.mountain_ratio

        # 排序所有采样值以确定阈值
        sorted_elev = sorted(elevation)
        n = len(sorted_elev)

        water_idx = int(n * water_ratio)
        mountain_idx = int(n * (1.0 - mountain_ratio))

        water_cutoff = sorted_elev[water_idx] if water_idx > 0 else -2.0
        mountain_cutoff = sorted_elev[mountain_idx] if mountain_idx < n else 2.0

        # 水体/山地内部细分阈值 — 基于噪声实际范围动态计算
        noise_min = sorted_elev[0]
        noise_max = sorted_elev[-1]
        deep_cutoff = water_cutoff - (water_cutoff - noise_min) * _WATER_DEEP_RATIO
        peak_cutoff = mountain_cutoff + (noise_max - mountain_cutoff) * (1.0 - _MOUNTAIN_PEAK_RATIO)

        # 4. 逐 tile 分类
        grid = TileGrid()
        base_altitude = chunk.annual_baseline.altitude

        # 海拔偏置：每个 tile 独立计算（有回调时双线性插值，否则常数）
        biases = self._compute_altitude_biases(
            chunk, world_x, world_y, size, n, get_altitude)

        for i in range(n):
            e = elevation[i] + biases[i]
            d = detail[i]

            if e < water_cutoff:
                # 水体 — 按深度细分
                if e < deep_cutoff:
                    terrain = TerrainType.DEEP_WATER
                else:
                    terrain = TerrainType.SHALLOW_WATER

            elif e > mountain_cutoff:
                # 山地 — 按高度细分
                if e > peak_cutoff:
                    terrain = TerrainType.MOUNTAIN_PEAK
                else:
                    terrain = TerrainType.STEEP_SLOPE

            else:
                # 平地 — 群系内细分
                terrain = self._classify_flat(e, d, chunk, base_altitude)

            grid._data[i] = int(terrain)

        return grid

    # ── 海洋生成 ────────────────────────────────────────────

    def _generate_ocean(self, chunk: ChunkData) -> TileGrid:
        """海洋群系的 tile 生成。

        所有 tile 为水体，用噪声区分浅水和深水。

        Args:
            chunk: 海洋群系的 ChunkData。

        Returns:
            TileGrid。
        """
        size = TILE_MAP_SIZE
        world_x = chunk.cx * size
        world_y = chunk.cy * size

        # 深度噪声
        depth = self._noise_elevation.octave_grid(
            world_x, world_y, size, size,
            frequency=_TILE_FREQ_ELEVATION,
            octaves=3,
            persistence=0.5,
            lacunarity=2.0,
        )

        grid = TileGrid()
        n = size * size

        # 动态深水阈值 — 噪声最低的 30% 为深水
        sorted_depth = sorted(depth)
        deep_ocean_cutoff = sorted_depth[int(n * 0.3)]

        for i in range(n):
            d = depth[i]
            if d < deep_ocean_cutoff:
                terrain = TerrainType.DEEP_WATER
            else:
                terrain = TerrainType.SHALLOW_WATER
            grid._data[i] = int(terrain)

        return grid

    # ── 平地细分 ────────────────────────────────────────────

    def _compute_altitude_biases(
        self,
        chunk: ChunkData,
        world_x: int,
        world_y: int,
        size: int,
        n: int,
        get_altitude: Callable[[int, int], float] | None,
    ) -> list[float]:
        """为每个 tile 计算海拔偏置。

        有 get_altitude 回调时：取周围 4 个 chunk 中心做双线性插值，
        chunk 边界处海拔平滑过渡，消除地形跳变。
        无回调时：使用 chunk 自身常数海拔。

        Args:
            chunk: 当前分块。
            world_x: 分块左上角全局 tile X（= cx * 200）。
            world_y: 分块左上角全局 tile Y（= cy * 200）。
            size: 分块边长（200）。
            n: tile 总数（40000）。
            get_altitude: 海拔查询回调，或 None。

        Returns:
            长度为 n 的偏置列表，每个值在 [0, 0.5] 范围。
        """
        _ALT_BIAS_MAX = 0.5

        if get_altitude is None:
            # 常数偏置 — 旧行为
            base_altitude = chunk.annual_baseline.altitude
            bias = max(0.0, min(_ALT_BIAS_MAX,
                base_altitude / 5000.0 * _ALT_BIAS_MAX))
            return [bias] * n

        # 双线性插值 — 取 4 个周围 chunk 中心的海拔
        cx, cy = chunk.cx, chunk.cy
        a00 = get_altitude(cx, cy)
        a10 = get_altitude(cx + 1, cy)
        a01 = get_altitude(cx, cy + 1)
        a11 = get_altitude(cx + 1, cy + 1)

        biases: list[float] = []
        for i in range(n):
            tx = i % size
            ty = i // size
            # 双线性插值权重 (0..1)
            fx = tx / size
            fy = ty / size
            # 双线性插值 → per-tile 海拔
            alt = (
                a00 * (1.0 - fx) * (1.0 - fy)
                + a10 * fx * (1.0 - fy)
                + a01 * (1.0 - fx) * fy
                + a11 * fx * fy
            )
            # 海拔 → 偏置（仅陆地，负海拔钳制为 0）
            bias = max(0.0, min(_ALT_BIAS_MAX,
                alt / 5000.0 * _ALT_BIAS_MAX))
            biases.append(bias)

        return biases

    # ── 平地细分 ────────────────────────────────────────────

    def _classify_flat(
        self,
        elevation: float,
        detail: float,
        chunk: ChunkData,
        base_altitude: float,
    ) -> TerrainType:
        """在非水非山的平坦区域中，根据群系和细节噪声细分地形。

        Args:
            elevation: 该 tile 的高程噪声值。
            detail: 该 tile 的细节噪声值。
            chunk: 所属分块。
            base_altitude: 分块基线海拔。

        Returns:
            细分后的 TerrainType。
        """
        biome = chunk.biome

        # 陡坡过渡带 — 接近山地阈值但未达标的区域
        if elevation > 0.4:
            return TerrainType.ROCK

        # 低洼沼泽 — 接近水体阈值但未达标的区域
        if elevation < -0.3 and base_altitude < 500.0:
            return TerrainType.MARSH

        # 按群系区分平地类型
        if biome.name == "ARID_SHRUBLAND":
            # 干旱灌木地：以沙地为主，部分岩石
            if detail > 0.4:
                return TerrainType.ROCK
            elif detail > -0.3:
                return TerrainType.SAND
            else:
                return TerrainType.GRASSLAND

        else:
            # 温带落叶林（及其他陆地群系）：以草地为主，掺杂沃土
            if detail > 0.5:
                return TerrainType.FERTILE_SOIL
            elif detail > -0.3:
                return TerrainType.GRASSLAND
            else:
                return TerrainType.SAND
