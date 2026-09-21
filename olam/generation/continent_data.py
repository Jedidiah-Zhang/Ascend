"""大陆数据类 — 层1 宏观场的纯数据载体。

从 continent.py 拆出：ContinentParams（生成参数）与 ContinentData
（生成结果数据 + 采样方法）不包含生成逻辑，独立成模块——
生成器（ContinentGenerator）与序列化（continent_io）都依赖本模块，
避免生成/序列化互相耦合。

派生缓存约定（WC-3.3 / WC-9.1）：``subdiv_ranges`` 与 ``_chunk_climate``
是可由持久化宏观场重算的派生缓存。生成路径（ContinentGenerator.generate）
返回前已按当前算法构建；磁盘加载路径保留磁盘副本并**加载即信任**——
缓存与宏观场同源写入、同一算法，且 gen_fingerprint 已背书算法一致
（不一致在生成器加载路径 fail-closed）。仅当派生段缺失或显式注入重建
入口时，才按当前算法惰性重算并 memoize（重算输入不落盘、与生成值非
逐位一致，故不作为首选路径）。
"""

import threading
from array import array
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Union

from olam.constants import CONTINENT_LAND_RATIO, CONTINENT_SAMPLE_RESOLUTION_M
import logging

from olam.generation.climate import ClimateZone

logger = logging.getLogger(__name__)


@dataclass
class ContinentParams:
    """大陆生成参数。

    Args:
        width_km: 大陆东西宽度 (km)。
        height_km: 大陆南北高度 (km)。
        sample_resolution: 层1采样分辨率 (m/采样点)。
        land_ratio: 目标陆地比例 [0-1]。
    """

    width_km: float = 100.0
    height_km: float = 60.0
    sample_resolution: float = 100.0
    land_ratio: float = CONTINENT_LAND_RATIO

    def __repr__(self) -> str:
        return (
            f"ContinentParams({self.width_km:.0f}×{self.height_km:.0f}km, "
            f"res={self.sample_resolution:.0f}m, "
            f"land={self.land_ratio:.0%})"
        )


@dataclass
class ContinentData:
    """层1生成结果 — 宏观场数据。

    Attributes:
        grid_width: 网格宽度（100m/格）。
        grid_height: 网格高度（100m/格）。
        cell_size: 每格对应的世界距离 (m)，默认 100。
        seed: 生成所用种子。
        land_mask: 行优先布尔数组，True=陆地。
        elevation_field: 行优先海拔数组 (m)，100m 分辨率。
        river_width: 河流+湖泊宽度场 (m)，100m 分辨率。
        water_distance: 行优先距水距离场 (m)，0=水体本身（同分辨率）。
        hydrology: 水文数据（流向、累积、湖盆、流线河网）。
        subdiv_ranges: 群系细分值域 {ClimateZone: (P10, P90)}；
            派生缓存属性——加载路径首次访问时按当前算法重建。
        _chunk_climate: chunk 级气候缓存，由 generate() 末尾填充。
            通过 get_chunk_climate(cx, cy) 查询，返回
            (mean_temp, annual_rainfall, sea_level_temp, zone_int)。
    """

    grid_width: int
    grid_height: int
    cell_size: float
    seed: int
    # 生成参数快照（land_ratio）：缓存校验用——大陆是 (seed, land_ratio)
    # 的确定性函数，同 seed 不同 land_ratio 必须重新生成。
    land_ratio: float = CONTINENT_LAND_RATIO

    # 生成环境指纹（config 常量 + 生成管线源码摘要）：由 generator
    # 写入缓存时填充；加载时与当前指纹不一致即拒绝加载（fail-closed，
    # WC-1.2/WC-9.1，见 WorldGenerator._create_continent）。
    gen_fingerprint: str = ""

    land_mask: list[bool] = field(default_factory=list)
    elevation_field: Union[list[float], "array[float]"] = field(
        default_factory=lambda: array('d')
    )
    river_width: Union[list[float], "array[float]"] = field(
        default_factory=lambda: array('d')
    )
    # 距水距离场 (m)：每格到最近水体（海/河/湖）的距离，0 = 水体本身。
    # 多源 BFS 计算（water_distance.compute_water_distance），与海拔场
    # 同分辨率同索引；供材质分布（沙滩/冲积/湿地）与生态查询使用。
    water_distance: Union[list[float], "array[float]"] = field(
        default_factory=lambda: array('d')
    )
    hydrology: "HydrologyData | None" = None
    # 群系细分值域（派生缓存）。字段名带下划线：外部经 subdiv_ranges
    # 属性读取（首次访问触发加载路径的重建），避免消费者直接读到空/旧值。
    _subdiv_ranges: dict[int, tuple[float, float]] = field(
        default_factory=dict, repr=False,
    )
    # chunk 级气候: {(cx, cy): (mean_temp, annual_rainfall, sea_level_temp, climate_zone_int)}
    _chunk_climate: dict = field(default_factory=dict, repr=False)

    # ── 派生缓存重建（不序列化、不参与相等性）────────────────
    # 加载路径注入重建入口后置 _derived_ready=False；首次访问时调用
    # 重建器（WorldGenerator._rebuild_derived_caches）重算两个缓存。
    # 生成路径不注入，_derived_ready 保持 True（generate() 已构建）。
    _derived_rebuilder: "Callable[[ContinentData], None] | None" = field(
        default=None, repr=False, compare=False,
    )
    _derived_ready: bool = field(default=True, repr=False, compare=False)
    # 重建进行中（同线程重入判定用；跨线程由 RLock 阻塞到重建完成，
    # 不会读到半成品缓存）。
    _derived_building: bool = field(default=False, repr=False, compare=False)
    _derived_lock: threading.RLock = field(
        default_factory=threading.RLock, repr=False, compare=False,
    )

    def attach_derived_rebuilder(
        self, rebuilder: "Callable[[ContinentData], None]",
    ) -> None:
        """注入派生缓存重建入口，并标记待重建（缺派生段的兜底路径）。

        正常加载路径保留磁盘派生段并加载即信任（指纹背书）；仅当缓存
        缺派生段（手工构造/未来格式）时经此注入按当前算法重建的入口，
        首次查询在锁内重建一次（WC-9.1：无法验证时以重算兜底）。
        """
        self._derived_rebuilder = rebuilder
        self._derived_ready = False

    @property
    def subdiv_ranges(self) -> dict[int, tuple[float, float]]:
        """群系细分值域 {ClimateZone_int: (P10, P90)}（派生缓存）。

        加载路径首次访问触发按当前算法重建；生成路径直接返回已构建值。
        """
        self.ensure_derived_caches()
        return self._subdiv_ranges

    @subdiv_ranges.setter
    def subdiv_ranges(self, ranges: dict[int, tuple[float, float]]) -> None:
        self._subdiv_ranges = ranges

    def ensure_derived_caches(self) -> None:
        """派生缓存构建入口：未构建时按当前算法重建（已构建无操作）。

        供加载路径的消费者显式调用；``subdiv_ranges`` 属性与
        :meth:`get_chunk_climate` 内部自动调用。重建在 RLock 内完成：
        并发首触只有一个线程执行重建，其余阻塞到完成（不会读到
        半成品）；重建器内部回查 get_chunk_climate（沙漠档 moisture
        动态值域）由 _derived_building 判定同线程重入。
        """
        if self._derived_ready:
            return
        with self._derived_lock:
            if self._derived_ready or self._derived_building:
                return
            self._derived_building = True
            try:
                if self._derived_rebuilder is not None:
                    self._derived_rebuilder(self)
                self._derived_ready = True
            finally:
                # 重建失败：保持未就绪，下次访问重试（fail-closed，
                # 不静默沿用半成品缓存）。
                self._derived_building = False

    def get_chunk_climate(
        self, cx: int, cy: int,
    ) -> tuple[float, float, float, int]:
        """查询 chunk 中心的校准后气候属性。

        界内未命中时触发派生缓存重建（加载路径首次查询），此后命中
        缓存。越界不触发重建（地图外无须重算整场）。

        Returns:
            (mean_temp, annual_rainfall, sea_level_temp, climate_zone)：
            越界（地图界限外）返回一致的极地深海默认值
            (-20, 0, -20, POLAR_TUNDRA)——地图为有界矩形，界限外
            统一视为极地深海，避免各字段自相矛盾（zone=0 即热带
            雨林，与 -20°C 温度/深海海拔矛盾）。
        """
        key = (cx, cy)
        hit = self._chunk_climate.get(key)
        if hit is not None:
            return hit
        if 0 <= cx < self.grid_width // 2 and 0 <= cy < self.grid_height // 2:
            # 界内未命中 = 派生缓存尚未构建（加载路径）；重建后重查
            self.ensure_derived_caches()
            hit = self._chunk_climate.get(key)
            if hit is not None:
                return hit
            # 界内但无重建入口（如直接反序列化、未接入 WorldGenerator）：
            # fail-closed 返回默认，不静默使用无法验证的磁盘值。
            logger.warning(
                "get_chunk_climate: 界内 chunk (%d,%d) 派生缓存不可用，"
                "返回极地深海默认（加载路径缺重建入口？）", cx, cy,
            )
        else:
            logger.debug(
                "get_chunk_climate: chunk (%d,%d) 超出地图界限，返回极地深海默认",
                cx, cy,
            )
        return -20.0, 0.0, -20.0, int(ClimateZone.POLAR_TUNDRA)

    def __repr__(self) -> str:
        land = sum(1 for v in self.land_mask if v)
        total = len(self.land_mask)
        ratio = land / total if total > 0 else 0
        return (
            f"ContinentData({self.grid_width}×{self.grid_height}, "
            f"cell={self.cell_size:.0f}m, land={ratio:.1%})"
        )

    def _grid_index(self, world_x: float, world_y: float) -> int | None:
        """世界 tile 坐标（米）→ 宏观场格索引（1 格 = 分辨率米）。

        换算：格 = 米 / CONTINENT_SAMPLE_RESOLUTION_M。越界返回 None。
        """
        gx = int(world_x / CONTINENT_SAMPLE_RESOLUTION_M)
        gy = int(world_y / CONTINENT_SAMPLE_RESOLUTION_M)
        if 0 <= gx < self.grid_width and 0 <= gy < self.grid_height:
            return gy * self.grid_width + gx
        return None

    def is_land(self, world_x: float, world_y: float) -> bool:
        """查询世界坐标是否为陆地。越界返回 False。"""
        idx = self._grid_index(world_x, world_y)
        if idx is None or idx >= len(self.land_mask):
            return False
        return self.land_mask[idx]

    def sample_altitude(self, world_x: float, world_y: float) -> float:
        """从宏观海拔场采样（最近邻）。越界返回默认海洋深度。

        Args:
            world_x, world_y: 世界 tile 坐标（米；换算见 _grid_index）。
        """
        idx = self._grid_index(world_x, world_y)
        if idx is None or idx >= len(self.elevation_field):
            return -3500.0
        return self.elevation_field[idx]

    def sample_altitude_bilinear(self, world_x: float, world_y: float) -> float:
        """双线性插值采样宏观海拔，消除 100m 网格的块状伪影。

        Args:
            world_x: 世界 tile X 坐标（米）。
            world_y: 世界 tile Y 坐标（米）。

        Returns:
            插值后的海拔 (m)。越界返回默认海洋深度。
        """
        # 网格空间中的连续坐标（米 → 格：÷ 分辨率）
        gx = world_x / CONTINENT_SAMPLE_RESOLUTION_M - 0.5
        gy = world_y / CONTINENT_SAMPLE_RESOLUTION_M - 0.5

        x0 = int(gx)
        y0 = int(gy)
        x1, y1 = x0 + 1, y0 + 1

        # 越界检查
        if (x0 < 0 or x1 >= self.grid_width or
                y0 < 0 or y1 >= self.grid_height):
            return self.sample_altitude(world_x, world_y)  # 回退最近邻

        tx = gx - x0
        ty = gy - y0

        # 四个角的值
        elev = self.elevation_field
        gw = self.grid_width
        v00 = elev[y0 * gw + x0]
        v10 = elev[y0 * gw + x1]
        v01 = elev[y1 * gw + x0]
        v11 = elev[y1 * gw + x1]

        # 双线性插值
        v0 = v00 + (v10 - v00) * tx
        v1 = v01 + (v11 - v01) * tx
        return v0 + (v1 - v0) * ty

    def sample_river_width(self, world_x: float, world_y: float) -> float:
        """双线性插值采样河流宽度 (m)，消除 100m 网格块状伪影。

        Args:
            world_x: 世界 tile X 坐标。
            world_y: 世界 tile Y 坐标。

        Returns:
            插值后的河流宽度 (m)，0=无河流。越界返回 0。
        """
        if not self.river_width:
            return 0.0

        gx = world_x / CONTINENT_SAMPLE_RESOLUTION_M - 0.5
        gy = world_y / CONTINENT_SAMPLE_RESOLUTION_M - 0.5
        x0 = int(gx)
        y0 = int(gy)
        x1, y1 = x0 + 1, y0 + 1

        if (x0 < 0 or x1 >= self.grid_width or
                y0 < 0 or y1 >= self.grid_height):
            return 0.0

        tx = gx - x0
        ty = gy - y0
        rw = self.river_width
        gw = self.grid_width
        v00 = rw[y0 * gw + x0]
        v10 = rw[y0 * gw + x1]
        v01 = rw[y1 * gw + x0]
        v11 = rw[y1 * gw + x1]
        v0 = v00 + (v10 - v00) * tx
        v1 = v01 + (v11 - v01) * tx
        return v0 + (v1 - v0) * ty

    def sample_water_distance_bilinear(
        self, world_x: float, world_y: float,
    ) -> float:
        """双线性插值采样距水距离 (m)，消除 100m 网格的块状伪影。

        语义：0 = 水体本身；正值 = 到最近水体的平面距离。越界（地图
        界限外）返回 0——界限外统一视为海洋。距水场未生成（空）时
        返回 0（调用方应保证生成后使用）。

        Args:
            world_x: 世界 tile X 坐标（米）。
            world_y: 世界 tile Y 坐标（米）。

        Returns:
            插值后的距水距离 (m)；0 = 水体/越界/未生成。
        """
        if not self.water_distance:
            return 0.0

        # 坐标换算用 self.cell_size（本大陆实际格分辨率）而非全局常量
        # ——与 sample_altitude_bilinear 的预存模式一致，但非 100m 分辨率
        # 的大陆（测试用小尺寸）换算仍正确。
        cell = float(self.cell_size)
        gx = world_x / cell - 0.5
        gy = world_y / cell - 0.5
        x0 = int(gx)
        y0 = int(gy)
        x1, y1 = x0 + 1, y0 + 1

        if (x0 < 0 or x1 >= self.grid_width or
                y0 < 0 or y1 >= self.grid_height):
            return 0.0  # 越界视为海洋（距水 0）

        tx = gx - x0
        ty = gy - y0
        wd = self.water_distance
        gw = self.grid_width
        v00 = wd[y0 * gw + x0]
        v10 = wd[y0 * gw + x1]
        v01 = wd[y1 * gw + x0]
        v11 = wd[y1 * gw + x1]
        v0 = v00 + (v10 - v00) * tx
        v1 = v01 + (v11 - v01) * tx
        return v0 + (v1 - v0) * ty
