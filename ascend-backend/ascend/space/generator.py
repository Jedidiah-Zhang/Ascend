"""世界生成器 — 协调噪声→海拔→温度→气候→群系的物理因果链。

生成顺序：
  1. 海拔噪声 → 海拔高度（第一性，地形）
  2. 纬度噪声 → 海平面温度
  3. 气温直减率: 实际温度 = 海平面温度 - 海拔 × LAPSE_RATE
  4. 降雨噪声 → 年降雨量
  5. 温度 + 降雨 → 气候档位
  6. 气候档位 + 次级噪声 → 群系类型

支持串行和并行生成，可注入外部线程池。
每个分块的生成逻辑为纯函数链，不依赖外部可变状态。
"""

from concurrent.futures import ThreadPoolExecutor, as_completed

from ascend.log import get_logger
from .noise import PerlinNoise
from .tile_grid import TILE_MAP_SIZE
from .continent import ContinentGenerator
from .climate import (
    ClimateZone,
    WeatherParams,
    sea_level_temperature,
    rainfall_from_noise,
    climate_zone_from_values,
    annual_baseline,
)
from .biome import BiomeType, biome_from_climate
from .chunk import ChunkData

logger = get_logger(__name__)

# 噪声频率配置
_FREQ_LATITUDE = 0.0003    # 纬度：超低频，暖/冷带宽 ~3000 chunk
_FREQ_RAINFALL = 0.004     # 降雨：低频，区域降水模式
_FREQ_DERIVED = 0.005      # 派生参数（日照/湿度/风速）：中频


class WorldGenerator:
    """世界生成器。

    封装噪声生成器和分块生成流程。
    线程安全：除 _templates 缓存外无可变状态。

    用法:
        gen = WorldGenerator(seed=42)
        chunk = gen.generate_chunk(0, 0)

        # 并行生成
        chunks = gen.generate_parallel([(0,0), (0,1), (1,0)], max_workers=4)
    """

    def __init__(
        self,
        seed: int = 0,
        *,
        executor: ThreadPoolExecutor | None = None,
    ) -> None:
        """初始化世界生成器。

        从 seed 派生 5 个独立噪声实例。

        Args:
            seed: 世界种子。相同种子生成相同世界。
            executor: 外部线程池，None 时每次并行创建临时线程池。
        """
        self._seed = seed
        self._executor = executor
        self._continent = None  # ContinentGenerator 惰性创建

        # 种子衍生相位偏移 — 确保不同 seed 的 (0,0) 采样到不同噪声值。
        # 偏移量 ~数百 chunk，相当于"种子在无限噪声空间中选择不同起点"。
        # 黄金分割共轭 0.618... 保证各通道偏移均匀分布不集中。
        import math
        phi = (math.sqrt(5.0) - 1.0) / 2.0  # 0.618...
        n_phases = 8
        seed_float = float(abs(seed) % 100000)
        self._phase = [
            ((seed_float + i * 137.5) * phi * 1000.0) % 9973.0
            for i in range(n_phases)
        ]

        # 6 个噪声通道（海拔改用构造模拟）
        self._noise_latitude = PerlinNoise(seed + 200)
        self._noise_rainfall = PerlinNoise(seed + 300)
        self._noise_sunshine = PerlinNoise(seed + 400)
        self._noise_humidity = PerlinNoise(seed + 500)
        self._noise_wind = PerlinNoise(seed + 600)
        # 次级噪声（群系细分用）
        self._noise_moisture = PerlinNoise(seed + 700)

        logger.info("WorldGenerator 就绪: seed=%d, 构造海拔 + 6 噪声通道", seed)

    def __repr__(self) -> str:
        return f"WorldGenerator(seed={self._seed})"

    # ── 海拔查询 ──────────────────────────────────────────

    def get_altitude(self, world_x: float, world_y: float) -> float:
        """查询任意世界坐标的构造海拔。

        Args:
            world_x: 世界 tile X。
            world_y: 世界 tile Y。

        Returns:
            海拔 (m)。
        """
        if self._continent is None:
            self._continent = ContinentGenerator(seed=self._seed).generate()
        return self._continent.sample_altitude(world_x, world_y)

    def _sample_altitude_at_chunk(self, cx: int, cy: int) -> float:
        """采样 chunk 中心的海拔（chunk 坐标 → tile 坐标转换）。

        Args:
            cx, cy: chunk 坐标。

        Returns:
            chunk 中心海拔 (m)。
        """
        return self.get_altitude(
            cx * TILE_MAP_SIZE + TILE_MAP_SIZE // 2,
            cy * TILE_MAP_SIZE + TILE_MAP_SIZE // 2,
        )

    # ── 物理推导 ──────────────────────────────────────────

    def _sample_latitude_temp(self, cx: int, cy: int) -> float:
        """采样纬度噪声 → 海平面温度。

        Args:
            cx, cy: 分块坐标。

        Returns:
            海平面温度 (°C)。
        """
        p = self._phase[1]
        n = self._noise_latitude.octave(cx + p, cy + p, octaves=2, frequency=_FREQ_LATITUDE)
        return sea_level_temperature(n)

    def _sample_rainfall(self, cx: int, cy: int) -> float:
        """采样降雨噪声 → 年降雨量 (mm)。

        Args:
            cx, cy: 分块坐标。

        Returns:
            年降雨量 (mm)。
        """
        p = self._phase[2]
        n = self._noise_rainfall.octave(cx + p, cy + p, octaves=4, frequency=_FREQ_RAINFALL)
        return rainfall_from_noise(n)

    def _sample_derived_noise(
        self, noise: PerlinNoise, cx: int, cy: int, phase_idx: int
    ) -> float:
        """采样派生参数噪声。

        Args:
            noise: 噪声实例。
            cx, cy: 分块坐标。
            phase_idx: 相位偏移索引。

        Returns:
            噪声值 [-1, 1]。
        """
        p = self._phase[phase_idx]
        return noise.octave(cx + p, cy + p, octaves=4, frequency=_FREQ_DERIVED)

    # ── 单分块同步生成 ───────────────────────────────────

    def generate_chunk(self, cx: int, cy: int) -> ChunkData:
        """同步生成一个分块。

        因果链：海拔 → 海平面温度 → 实际温度 → 气候 → 群系。

        不生成详细 tile 层（按需延迟生成）。

        Args:
            cx: 分块 X 坐标。
            cy: 分块 Y 坐标。

        Returns:
            完整的 ChunkData（tiles=None）。
        """
        # 1. 海拔（第一性）
        altitude = self._sample_altitude_at_chunk(cx, cy)

        # 2. 纬度 → 海平面温度
        sea_temp = self._sample_latitude_temp(cx, cy)

        # 3. 降雨
        rainfall = self._sample_rainfall(cx, cy)

        # 4. 温度 + 降雨 → 气候档位
        from .climate import apply_lapse_rate
        temperature = apply_lapse_rate(sea_temp, altitude)
        climate = climate_zone_from_values(temperature, rainfall)

        # 5. 次级噪声 → 群系（海拔优先判定海洋/陆地）
        p = self._phase[6]
        moisture = self._noise_moisture.octave(cx + p, cy + p, octaves=2, frequency=0.005)
        biome = biome_from_climate(climate, moisture, altitude, sea_temp)

        # 6. 派生参数 → 完整气象数据
        params = annual_baseline(
            altitude=altitude,
            sea_level_temp=sea_temp,
            rainfall=rainfall,
            climate=climate,
            sunshine_noise=self._sample_derived_noise(self._noise_sunshine, cx, cy, 3),
            humidity_noise=self._sample_derived_noise(self._noise_humidity, cx, cy, 4),
            wind_noise=self._sample_derived_noise(self._noise_wind, cx, cy, 5),
        )

        return ChunkData(
            cx=cx,
            cy=cy,
            biome=biome,
            climate_zone=climate,
            annual_baseline=params,
        )

    # ── 并行生成 ─────────────────────────────────────────

    def generate_parallel(
        self,
        chunks: list[tuple[int, int]],
        max_workers: int = 4,
    ) -> list[ChunkData]:
        """并行生成多个分块。

        每个分块独立生成，无共享可变状态。

        Args:
            chunks: 要生成的分块坐标列表。
            max_workers: 最大工作线程数。若构造时注入了 executor 则忽略。

        Returns:
            ChunkData 列表，顺序与输入对应。
        """
        if not chunks:
            return []

        executor = self._executor or ThreadPoolExecutor(max_workers=max_workers)
        own_executor = self._executor is None

        try:
            future_to_idx: dict = {}
            for idx, (cx, cy) in enumerate(chunks):
                future = executor.submit(self.generate_chunk, cx, cy)
                future_to_idx[future] = idx

            results: list[ChunkData | None] = [None] * len(chunks)
            for future in as_completed(future_to_idx):
                idx = future_to_idx[future]
                results[idx] = future.result()

            logger.info(
                "并行生成完成: %d 个分块, %d workers",
                len(chunks), max_workers,
            )
            return [r for r in results if r is not None]

        finally:
            if own_executor:
                executor.shutdown(wait=False)

    # ── 轻量查询 ─────────────────────────────────────────

    def get_biome(self, cx: int, cy: int) -> BiomeType:
        """快速查询分块群系（不保留中间结果）。

        Args:
            cx, cy: 分块坐标。

        Returns:
            群系类型。
        """
        altitude = self._sample_altitude_at_chunk(cx, cy)
        sea_temp = self._sample_latitude_temp(cx, cy)
        rainfall = self._sample_rainfall(cx, cy)
        from .climate import apply_lapse_rate
        temperature = apply_lapse_rate(sea_temp, altitude)
        climate = climate_zone_from_values(temperature, rainfall)
        p = self._phase[6]
        moisture = self._noise_moisture.octave(cx + p, cy + p, octaves=2, frequency=0.005)
        return biome_from_climate(climate, moisture, altitude, sea_temp)

    def get_climate(self, cx: int, cy: int) -> ClimateZone:
        """快速查询分块气候档位。

        Args:
            cx, cy: 分块坐标。

        Returns:
            气候档位。
        """
        altitude = self._sample_altitude_at_chunk(cx, cy)
        sea_temp = self._sample_latitude_temp(cx, cy)
        rainfall = self._sample_rainfall(cx, cy)
        from .climate import apply_lapse_rate
        temperature = apply_lapse_rate(sea_temp, altitude)
        return climate_zone_from_values(temperature, rainfall)
