"""大陆数据类 — 层1 宏观场的纯数据载体。

从 continent.py 拆出：ContinentParams（生成参数）与 ContinentData
（生成结果数据 + 采样方法）不包含生成逻辑，独立成模块——
生成器（ContinentGenerator）与序列化（continent_io）都依赖本模块，
避免生成/序列化互相耦合。
"""

from array import array
from dataclasses import dataclass, field
from typing import Union

from ascend.config import CONTINENT_LAND_RATIO, CONTINENT_SAMPLE_RESOLUTION_M
from ascend.log import get_logger

from .climate import ClimateZone

logger = get_logger(__name__)


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
        subdiv_ranges: 群系细分值域 {ClimateZone: (P10, P90)}。
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
    # 写入缓存时填充；仅用于加载时漂移诊断（告警/查询），不参与
    # 缓存失效判定——每个存档的大陆在创建时定案。
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
    subdiv_ranges: dict[int, tuple[float, float]] = field(default_factory=dict)
    # chunk 级气候: {(cx, cy): (mean_temp, annual_rainfall, sea_level_temp, climate_zone_int)}
    _chunk_climate: dict = field(default_factory=dict, repr=False)

    def get_chunk_climate(
        self, cx: int, cy: int,
    ) -> tuple[float, float, float, int]:
        """查询 chunk 中心的校准后气候属性。

        Returns:
            (mean_temp, annual_rainfall, sea_level_temp, climate_zone)：
            越界（地图界限外）返回一致的极地深海默认值
            (-20, 0, -20, POLAR_TUNDRA)——地图为有界矩形，界限外
            统一视为极地深海，避免各字段自相矛盾（此前默认 zone=0
            即热带雨林，与 -20°C 温度/深海海拔矛盾）。
        """
        key = (cx, cy)
        if key in self._chunk_climate:
            return self._chunk_climate[key]
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
