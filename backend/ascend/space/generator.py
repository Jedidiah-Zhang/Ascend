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
import threading
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

# 生成环境指纹覆盖的管线源码（相对 backend/ascend/space/）。
# 开发环境源码在场：任一文件变更 → 指纹变化 → 加载时漂移告警。
# 打包环境源码缺失：退化为 CONTINENT_GEN_VERSION（发布时递增）。
_GEN_SOURCE_FILES: tuple[str, ...] = (
    "continent.py", "hydrology.py", "streamlines.py", "climate.py", "noise.py",
    "_hydrology.c", "_streamlines.c", "_perlin.c",
)

_HERE = os.path.dirname(os.path.abspath(__file__))


def compute_gen_fingerprint() -> str:
    """当前生成环境的指纹（诊断用途，不参与缓存失效判定）。

    组成：CONTINENT_GEN_CONSTANT_NAMES 所列 config 常量值 + 生成
    管线源码内容（在场时）+ CONTINENT_GEN_VERSION（打包退位）。

    Returns:
        sha256 十六进制摘要。同环境同结果；任一常量/源码变化即变。
    """
    import hashlib

    import ascend.config as config

    h = hashlib.sha256()
    for name in config.CONTINENT_GEN_CONSTANT_NAMES:
        h.update(name.encode())
        h.update(repr(getattr(config, name)).encode())
    for rel in _GEN_SOURCE_FILES:
        path = os.path.join(_HERE, rel)
        try:
            with open(path, "rb") as f:
                h.update(f.read())
        except OSError:
            # 打包环境：源码不在包内，由 CONTINENT_GEN_VERSION 覆盖
            pass
    h.update(str(config.CONTINENT_GEN_VERSION).encode())
    return h.hexdigest()


class WorldGenerator:
    """世界生成器。

    封装噪声生成器和分块生成流程。
    线程安全：_continent 由 _continent_lock 保护的单一创建入口
    （_create_continent）惰性初始化——并发首触只生成一次，此后
    只读无锁。其余属性构造后不变。

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
        ignore_cache: bool = False,
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
            ignore_cache: True 时无视缓存强制重新生成（--regen-continent
                / continent regen 语义）。对存档世界有破坏性：玩家改动的
                chunk 数据与新场可能出现接缝不一致。

        参数归一化：None 一律落为 ContinentParams() 默认值，大陆是
        (seed, land_ratio, 尺寸) 的确定性函数，缓存校验与生成统一
        使用归一化后的参数。
        """
        self._seed = seed
        self._executor = executor
        self._continent = None  # ContinentGenerator 惰性创建
        self._continent_lock = threading.Lock()  # 单一创建入口的并发闸门
        self._continent_cache_path = continent_cache_path
        self._ignore_cache = ignore_cache
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

        供 GameEngine 在启动时主动触发（首选方式：可带阶段进度
        回调），并把 ContinentData 暴露给出生点选择 / TileGenerator。
        惰性路径（get_altitude 首触）走同一创建入口（_create_continent），
        两路径产出同一份大陆（含沙漠 moisture 动态值域与磁盘缓存）。

        Args:
            progress_cb: 可选阶段回调（ContinentGenerator.generate 的
                STAGE_* 阶段名）；缓存命中时以 STAGE_DONE 单阶段回调。

        Returns:
            ContinentData 宏观场（缓存于 self._continent）。
        """
        with self._continent_lock:
            if self._continent is None:
                self._create_continent(progress_cb)
        return self._continent

    def _create_continent(
        self, progress_cb: "Callable[[str], None] | None",
    ) -> None:
        """大陆的单一创建入口（须持 _continent_lock 调用）。

        ensure_continent 与 get_altitude 共用：磁盘缓存恢复/校验 →
        未命中则生成 + 补充沙漠档 moisture 动态值域 + 落盘缓存。
        保证任何首次触达路径产出同一份 _continent（缓存读写与
        沙漠校准不因路径而异），并发首触由锁收敛为一次生成。
        """
        # 尺寸由网格数 × cell_size 反推（序列化不存尺寸字段）：
        # 缓存必须与期望尺寸一致，避免同 seed 不同尺寸的调参结果混入
        cache_path = self._continent_cache_path
        if cache_path and not self._ignore_cache:
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
            elif (
                self._continent is not None
                and self._continent.gen_fingerprint
                and self._continent.gen_fingerprint
                != compute_gen_fingerprint()
            ):
                # 生成环境漂移：沿用缓存（世界保持创建时样貌——
                # 每个存档的大陆在创建时定案），仅告警——调参验证
                # 请新建世界或 continent regen / --regen-continent
                # 强制重建。
                logger.warning(
                    "大陆缓存生成环境与当前算法/参数不一致（世界保持"
                    "创建时样貌，派生层按当前算法解释；调参验证请"
                    "新建世界，或 continent regen 强制重建）: %s",
                    cache_path,
                )
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
        """序列化大陆宏观场到磁盘（原子写），记录生成环境指纹。"""
        data.gen_fingerprint = compute_gen_fingerprint()
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

        首触时经统一创建入口惰性生成大陆（与 ensure_continent 同构：
        缓存读写 + 沙漠 moisture 动态值域补齐，锁内单次生成）。

        Args:
            world_x: 世界 tile X。
            world_y: 世界 tile Y。

        Returns:
            海拔 (m)。
        """
        if self._continent is None:
            with self._continent_lock:
                if self._continent is None:
                    self._create_continent(None)
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
