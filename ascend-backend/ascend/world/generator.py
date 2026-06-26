"""世界生成器 — 协调噪声→气候→群系→分块的完整生成流程。

支持串行和并行生成，可注入外部线程池。
每个分块的生成逻辑为纯函数链，不依赖外部可变状态。
"""

from concurrent.futures import ThreadPoolExecutor, as_completed

from ascend.log import get_logger
from .noise import PerlinNoise
from .climate import (
    ClimateZone,
    WeatherParams,
    climate_zone_from_noise,
    annual_baseline,
)
from .biome import BiomeType, BiomeTemplate, biome_from_climate, get_template
from .chunk import ChunkData

logger = get_logger(__name__)

# 默认噪声配置
# 不同参数使用不同的频率偏移，避免温度/降雨完全正相关
_PARAM_FREQUENCIES: dict[str, tuple[float, float]] = {
    "temperature": (0.003, 0.001),
    "rainfall":    (0.005, 0.002),
    "sunshine":    (0.004, 0.003),
    "altitude":    (0.006, 0.001),
    "humidity":    (0.005, 0.004),
    "wind_speed":  (0.007, 0.003),
}


class WorldGenerator:
    """世界生成器。

    封装噪声生成器、群系模板和分块生成流程。
    线程安全：除 _templates 缓存外无可变状态，缓存为只读。

    用法:
        gen = WorldGenerator(seed=42)
        chunk = gen.generate_chunk(0, 0)

        # 并行生成 9 个分块
        chunks = gen.generate_parallel([(0,0), (0,1), (1,0)], max_workers=4)
    """

    def __init__(
        self,
        seed: int = 0,
        *,
        executor: ThreadPoolExecutor | None = None,
    ) -> None:
        """初始化世界生成器。

        从 seed 派生 7 个噪声实例（1 个用于气候区 + 6 个用于气象参数），
        每个使用不同的子种子确保参数噪声相互独立。

        Args:
            seed: 世界种子。相同种子生成相同世界。
            executor: 外部线程池，None 时每次并行创建临时线程池。
        """
        self._seed = seed
        self._executor = executor

        # 气候区噪声（用于 climate_zone_from_noise）
        self._climate_noise = PerlinNoise(seed + 1000)

        # 各气象参数的独立噪声生成器
        self._param_noises: dict[str, PerlinNoise] = {}
        for i, param in enumerate(_PARAM_FREQUENCIES):
            self._param_noises[param] = PerlinNoise(seed + 2000 + i)

        # 群系模板缓存（只读）
        self._templates: dict[BiomeType, BiomeTemplate] = {}

        logger.info("WorldGenerator 就绪: seed=%d, params=%d", seed, len(self._param_noises))

    def __repr__(self) -> str:
        return f"WorldGenerator(seed={self._seed})"

    # ── 单分块同步生成 ───────────────────────────────────

    def generate_chunk(self, cx: int, cy: int) -> ChunkData:
        """同步生成一个分块（大地图层 + 气候参数）。

        不生成详细 tile 层（按需延迟生成）。

        Args:
            cx: 分块 X 坐标。
            cy: 分块 Y 坐标。

        Returns:
            完整的 ChunkData（tiles=None）。
        """
        # 1. 采样气候噪声 → 确定气候档位
        temp_noise = self._climate_noise.octave(cx, cy, octaves=3, frequency=0.003)
        rain_noise = self._climate_noise.octave(cx + 1000, cy + 1000, octaves=3, frequency=0.004)
        climate = climate_zone_from_noise(temp_noise, rain_noise)

        # 2. 采样各参数噪声 → 年均基线
        noise_values: dict[str, float] = {}
        for param, (freq_x, freq_y) in _PARAM_FREQUENCIES.items():
            noise = self._param_noises[param]
            value = noise.octave(cx, cy, octaves=4, frequency=freq_x)
            # 同一参数用偏移坐标采样以达到弱相关
            value += noise.octave(cx + 500, cy + 500, octaves=4, frequency=freq_y) * 0.3
            noise_values[param] = value

        baseline = annual_baseline(climate, noise_values)

        # 3. 次级噪声 → 群系类型
        moisture_noise = self._climate_noise.octave(cx + 2000, cy + 2000, octaves=2, frequency=0.005)
        altitude_noise = noise_values["altitude"]
        biome = biome_from_climate(climate, moisture_noise, altitude_noise)

        # 4. 组装 ChunkData
        chunk = ChunkData(
            cx=cx,
            cy=cy,
            biome=biome,
            climate_zone=climate,
            annual_baseline=baseline,
        )

        # 缓存模板引用
        if biome not in self._templates:
            self._templates[biome] = get_template(biome)

        return chunk

    # ── 并行生成 ─────────────────────────────────────────

    def generate_parallel(
        self,
        chunks: list[tuple[int, int]],
        max_workers: int = 4,
    ) -> list[ChunkData]:
        """并行生成多个分块。

        每个分块独立生成，无共享可变状态。使用线程池并发执行。

        Args:
            chunks: 要生成的分块坐标列表。
            max_workers: 最大工作线程数。若构造时注入了 executor 则忽略。

        Returns:
            ChunkData 列表，顺序与输入对应。
        """
        if not chunks:
            return []

        executor = self._executor or ThreadPoolExecutor(max_workers=max_workers)
        own_executor = self._executor is None  # 是否自己创建的 executor

        try:
            # 使用字典保持顺序
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

    # ── 群系查询 ─────────────────────────────────────────

    def get_biome(self, cx: int, cy: int) -> BiomeType:
        """快速查询分块群系（不生成完整 ChunkData）。

        Args:
            cx: 分块 X 坐标。
            cy: 分块 Y 坐标。

        Returns:
            群系类型。
        """
        temp_noise = self._climate_noise.octave(cx, cy, octaves=3, frequency=0.003)
        rain_noise = self._climate_noise.octave(cx + 1000, cy + 1000, octaves=3, frequency=0.004)
        climate = climate_zone_from_noise(temp_noise, rain_noise)

        # 需要海拔噪声来区分群系
        alt_noise = self._param_noises["altitude"]
        altitude_value = alt_noise.octave(cx, cy, octaves=4, frequency=0.006)
        moisture_noise = self._climate_noise.octave(cx + 2000, cy + 2000, octaves=2, frequency=0.005)

        return biome_from_climate(climate, moisture_noise, altitude_value)

    def get_climate(self, cx: int, cy: int) -> ClimateZone:
        """快速查询分块气候档位。

        Args:
            cx: 分块 X 坐标。
            cy: 分块 Y 坐标。

        Returns:
            气候档位。
        """
        temp_noise = self._climate_noise.octave(cx, cy, octaves=3, frequency=0.003)
        rain_noise = self._climate_noise.octave(cx + 1000, cy + 1000, octaves=3, frequency=0.004)
        return climate_zone_from_noise(temp_noise, rain_noise)
