"""世界生成器 — 从大陆场派生 ChunkData，协调群系和气象参数。

生成顺序：
  1. 大陆海拔场 → chunk 中心海拔（最近邻）
  2. 大陆气候缓存 → chunk 中心温雨 + 气候档位（C 模型：纬度梯度 + 大陆度 + 雨影）
  3. chunk 中心温雨 + moisture 噪声 → 群系（两子型隶属度混合）
  4. 群系 + 风/湿噪声 → 气象参数基线

支持串行和并行生成，可注入外部线程池。
每个分块的生成逻辑为纯函数链，不依赖外部可变状态。
"""

import os
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed

from ascend.log import get_logger
from .noise import PerlinNoise
from .tile_grid import TILE_MAP_SIZE
from .continent import (
    ContinentGenerator,
    ContinentParams,
    serialize_continent,
    deserialize_continent,
)
from .climate import (
    ClimateZone,
    annual_baseline,
)
from .biome import BiomeType, biome_from_attrs
from .chunk import ChunkData

logger = get_logger(__name__)

from ascend.config import (
    NOISE_FREQ_DERIVED as _FREQ_DERIVED,
    MOISTURE_TILE_FREQUENCY as _MOISTURE_FREQ,
)


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
        continent_cache_path: str | None = None,
        land_ratio: float | None = None,
        width_km: float | None = None,
        height_km: float | None = None,
    ) -> None:
        """初始化世界生成器。

        从 seed 派生独立噪声实例（湿度、风力、moisture 细分）。

        Args:
            seed: 世界种子。相同种子生成相同世界。
            executor: 外部线程池，None 时每次并行创建临时线程池。
            continent_cache_path: 大陆宏观场缓存文件路径（None 不落盘）。
                由 GameEngine 传入存档内的 continent.bin——大陆是
                (seed, land_ratio, 尺寸) 的确定性函数，缓存随档分发，
                保证换机后首次加载也秒开。
            land_ratio: 目标陆地比例 [0-1]；None 用默认 0.55
                （创建世界调参时由存档 gen_params 传入）。
            width_km: 大陆东西宽度 (km)；None 用默认 100
                （创建世界调参的地图尺寸，随档 gen_params 定案）。
            height_km: 大陆南北高度 (km)；None 用默认 60。

        参数归一化：None 一律落为 ContinentParams() 默认值，大陆是
        (seed, land_ratio, 尺寸) 的确定性函数，缓存校验与生成统一
        使用归一化后的参数。
        """
        self._seed = seed
        self._executor = executor
        self._continent = None  # ContinentGenerator 惰性创建
        self._continent_cache_path = continent_cache_path
        default_params = ContinentParams()
        self._land_ratio = (
            land_ratio if land_ratio is not None else default_params.land_ratio)
        self._width_km = (
            width_km if width_km is not None else default_params.width_km)
        self._height_km = (
            height_km if height_km is not None else default_params.height_km)
        self._params = ContinentParams(
            land_ratio=self._land_ratio,
            width_km=self._width_km,
            height_km=self._height_km,
        )

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

        # 噪声通道（温雨改用大陆场 C 模型，海拔改用构造模拟）
        self._noise_humidity = PerlinNoise(seed + 500)
        self._noise_wind = PerlinNoise(seed + 600)
        # 次级噪声（群系细分用）
        self._noise_moisture = PerlinNoise(seed + 700)

        logger.info("WorldGenerator 就绪: seed=%d, 3 噪声通道", seed)

    def __repr__(self) -> str:
        return f"WorldGenerator(seed={self._seed})"

    # ── 海拔查询 ──────────────────────────────────────────

    def ensure_continent(
        self, progress_cb: "Callable[[str], None] | None" = None,
    ) -> "ContinentData":
        """主动生成并缓存宏观大陆数据，返回 ContinentData。

        默认 get_altitude 是惰性生成，首次调用才跑（侵蚀慢）。
        本方法强制预生成，供 GameEngine 在启动时主动触发，
        并把 ContinentData 暴露给出生点选择 / TileGenerator。

        磁盘缓存（<world_id>/continent.bin，由 GameEngine 传入路径）：
        大陆宏观场是 seed 的确定性函数，生成耗时 5-30s；缓存随档分发，
        命中直接反序列化恢复（秒级），缓存失效/损坏时重新生成并覆盖。

        生成后补充沙漠档的 moisture 噪声动态值域（continent 生成时
        无 moisture 噪声实例，此处补算）。

        Args:
            progress_cb: 可选阶段回调（ContinentGenerator.generate 的
                STAGE_* 阶段名）；缓存命中时以 STAGE_DONE 单阶段回调。

        Returns:
            ContinentData 宏观场（缓存于 self._continent）。
        """
        if self._continent is None:
            # 尺寸由网格数 × cell_size 反推（序列化不存尺寸字段）：
            # 缓存必须与期望尺寸一致，避免同 seed 不同尺寸的调参结果混入
            cache_path = self._continent_cache_path
            if cache_path:
                self._continent = self._load_continent_cache(cache_path)
                if (
                    self._continent is not None
                    and (
                        self._continent.seed != self._seed
                        or self._continent.land_ratio != self._land_ratio
                        or abs(
                            self._continent.grid_width
                            * self._continent.cell_size / 1000.0
                            - self._width_km
                        ) > 1e-6
                        or abs(
                            self._continent.grid_height
                            * self._continent.cell_size / 1000.0
                            - self._height_km
                        ) > 1e-6
                    )
                ):
                    # 缓存属于其它种子/参数（如手动拷贝错档、缓存损坏、
                    # 同 seed 不同 land_ratio/尺寸的调参结果混入）：
                    # 视为未命中，重新生成并覆盖
                    logger.warning(
                        "大陆缓存参数不符（缓存=seed %d land %.3f "
                        "%.1fx%.1fkm，期望=seed %d land %.3f %.1fx%.1fkm），"
                        "重新生成: %s",
                        self._continent.seed, self._continent.land_ratio,
                        self._continent.grid_width
                        * self._continent.cell_size / 1000.0,
                        self._continent.grid_height
                        * self._continent.cell_size / 1000.0,
                        self._seed, self._land_ratio,
                        self._width_km, self._height_km, cache_path,
                    )
                    self._continent = None
            if self._continent is None:
                self._continent = ContinentGenerator(
                    seed=self._seed, params=self._params,
                ).generate(progress_cb=progress_cb)
                self._supplement_moisture_range()
                if cache_path:
                    self._save_continent_cache(cache_path, self._continent)
                logger.info("大陆生成完成: %s", self._continent)
            else:
                if progress_cb is not None:
                    progress_cb(ContinentGenerator.STAGE_DONE)
                logger.info("大陆从缓存恢复: %s", self._continent)
        return self._continent

    # ── 大陆磁盘缓存 ──────────────────────────────────────

    def _load_continent_cache(self, path: str) -> "ContinentData | None":
        """从磁盘恢复大陆宏观场；无缓存/损坏/版本不符返回 None。"""
        if not os.path.isfile(path):
            return None
        try:
            with open(path, "rb") as f:
                data = deserialize_continent(f.read())
        except OSError:
            return None
        if data is None:
            logger.warning("大陆缓存失效（版本或数据损坏），将重新生成: %s", path)
        return data

    def _save_continent_cache(self, path: str, data: "ContinentData") -> None:
        """序列化大陆宏观场到磁盘（原子写，生成算法变更时覆盖）。"""
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = path + ".tmp"
        with open(tmp, "wb") as f:
            f.write(serialize_continent(data))
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
        logger.info("大陆缓存已写入: %s", path)

    def _supplement_moisture_range(self) -> None:
        """补充沙漠档 moisture 噪声的动态值域。

        遍历所有 chunk，取气候档为 DESERT 的 chunk 采样 moisture 噪声。
        """
        cont = self._continent
        if cont is None:
            return
        w, h = cont.grid_width, cont.grid_height
        moisture_vals: list[float] = []
        for cy in range(h // 2):
            for cx in range(w // 2):
                idx = (cy * 2 + 1) * w + (cx * 2 + 1)
                if not cont.land_mask[idx]:
                    continue
                _, _, _, zone = cont.get_chunk_climate(cx, cy)
                if zone != int(ClimateZone.DESERT):
                    continue
                moisture = self._sample_moisture_at_chunk(cx, cy)
                moisture_vals.append(moisture)
        if len(moisture_vals) < 10:
            return
        moisture_vals.sort()
        n = len(moisture_vals)
        p10 = moisture_vals[int(n * 0.10)]
        p90 = moisture_vals[int(n * 0.90)]
        if p90 - p10 < 0.1:
            p10 = moisture_vals[0]
            p90 = moisture_vals[-1]
        cont.subdiv_ranges[int(ClimateZone.DESERT)] = (p10, p90)

    def get_altitude(self, world_x: float, world_y: float) -> float:
        """查询任意世界坐标的构造海拔。

        Args:
            world_x: 世界 tile X。
            world_y: 世界 tile Y。

        Returns:
            海拔 (m)。
        """
        if self._continent is None:
            self._continent = ContinentGenerator(
                seed=self._seed, params=self._params,
            ).generate()
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

    def _sample_moisture_at_chunk(self, cx: int, cy: int) -> float:
        """采样 chunk 中心 moisture（世界坐标场，与 tile 层同一噪声场）。

        chunk 级群系标签与 tile 级隶属度必须来自同一噪声场：tile 层
        （tile_gen）在世界 tile 坐标采样 MOISTURE_TILE_FREQUENCY 场，
        此处取 chunk 中心世界坐标采样同场，保证 chunk 标签与中心 tile
        的群系细分一致。不带相位偏移（相位仅用于 chunk 级独用通道的
        种子去相关；moisture 跨两级使用，必须去掉）。

        Args:
            cx, cy: 分块坐标。

        Returns:
            噪声值 [-1, 1]。
        """
        return self._noise_moisture.octave(
            cx * TILE_MAP_SIZE + TILE_MAP_SIZE // 2,
            cy * TILE_MAP_SIZE + TILE_MAP_SIZE // 2,
            octaves=4, frequency=_MOISTURE_FREQ,
        )

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

        # 2. 气候 — 从大陆校准后 C 模型获取（纬度梯度+大陆度+雨影）
        temperature, rainfall, sea_temp, zone = (
            self._continent.get_chunk_climate(cx, cy)
        )
        from .climate import ClimateZone as _CZ
        climate = _CZ(zone)

        # 3. 群系 — 从连续属性 + moisture 噪声映射（档内细分，边界自然渐变）
        moisture = self._sample_moisture_at_chunk(cx, cy)
        biome = biome_from_attrs(
            temperature, rainfall, altitude, sea_temp,
            moisture_noise=moisture, subdiv_ranges=self._continent.subdiv_ranges,
        )

        # 6. 派生参数 → 完整气象数据
        params = annual_baseline(
            altitude=altitude,
            sea_level_temp=sea_temp,
            rainfall=rainfall,
            climate=climate,
            humidity_noise=self._sample_derived_noise(self._noise_humidity, cx, cy, 4),
            wind_noise=self._sample_derived_noise(self._noise_wind, cx, cy, 5),
        )

        return ChunkData(
            cx=cx,
            cy=cy,
            biome=biome,
            climate_zone=climate,
            annual_baseline=params,
            mean_temp=temperature,
            annual_rainfall=rainfall,
            sea_level_temp=sea_temp,
            altitude=altitude,
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
        temperature, rainfall, sea_temp, _zone = (
            self._continent.get_chunk_climate(cx, cy)
        )
        moisture = self._sample_moisture_at_chunk(cx, cy)
        return biome_from_attrs(
            temperature, rainfall, altitude, sea_temp,
            moisture_noise=moisture,
            subdiv_ranges=self._continent.subdiv_ranges if self._continent else None,
        )

    def get_climate(self, cx: int, cy: int) -> ClimateZone:
        """快速查询分块气候档位。

        Args:
            cx, cy: 分块坐标。

        Returns:
            气候档位。
        """
        _, _, _, zone = self._continent.get_chunk_climate(cx, cy)
        return ClimateZone(zone)
