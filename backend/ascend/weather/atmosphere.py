"""全局大气场 — 低分辨率 Perlin 噪声 + 时间漂移模拟气团移动。

设计：
  - 主噪声场：2D Perlin，采样间距 ATMOSPHERE_RESOLUTION(2km)
  - 气团漂移：噪声采样坐标沿 wind_vector 随 game_time 漂移
  - 风向：由第二个低频 Perlin 缓慢旋转，模拟季节性风向变化

相邻 chunk 采样点坐标接近 → 天气空间连续；时间漂移 → 气团移动效果。
计算 O(1)，无需邻居查询。
"""

import math

from ascend.space import PerlinNoise

from ascend.config import ATMOSPHERE_RESOLUTION, ATMOSPHERE_DRIFT_RATE


class AtmosphereField:
    """全局大气扰动场。

    纯查询对象，构造后只读。线程安全（PerlinNoise 每实例独立排列表，
    无共享可变状态）。

    用法:
        field = AtmosphereField(seed=42)
        perturb = field.sample(world_x, world_y, game_time)   # [-1, 1]
    """

    def __init__(
        self,
        seed: int = 0,
        resolution: float = ATMOSPHERE_RESOLUTION,
        drift_rate: float = ATMOSPHERE_DRIFT_RATE,
    ) -> None:
        """初始化大气场。

        Args:
            seed: 噪声种子。相同种子产生相同场。
            resolution: 采样间距（世界坐标单位）。越大越粗。
            drift_rate: 气团漂移率（世界单位/tick）。
        """
        self._noise = PerlinNoise(seed=seed)
        self._wind_noise = PerlinNoise(seed=seed + 1)
        self._resolution = resolution
        self._drift_rate = drift_rate

    def __repr__(self) -> str:
        return f"AtmosphereField(seed={self._noise}, resolution={self._resolution})"

    def wind_vector(self, game_time: int) -> tuple[float, float]:
        """当前风向（单位向量），随时间缓慢旋转。

        风向角度由低频 Perlin 噪声驱动：1 tick 对应 1e-7 噪声坐标，
        1 游戏日(172800 tick)对应 0.017 噪声坐标 → 风向缓慢变化。

        Args:
            game_time: 游戏时间（tick）。

        Returns:
            单位向量 (wx, wy)。
        """
        # +0.5 偏移避开 Perlin 整数网格零点（否则 game_time=0 时噪声恒为 0）
        angle = self._wind_noise.sample(game_time * 1e-7 + 0.5, 0.5) * math.pi
        return (math.cos(angle), math.sin(angle))

    def sample(self, world_x: float, world_y: float, game_time: int) -> float:
        """采样 (world_x, world_y) 在 game_time 时刻的大气扰动。

        噪声采样坐标 = 空间坐标 / resolution + wind_vector * drift。
        随 game_time 增大，采样坐标沿风向漂移，模拟气团移动。

        Args:
            world_x: 世界 X 坐标（单位 m）。
            world_y: 世界 Y 坐标（单位 m）。
            game_time: 游戏时间（tick）。

        Returns:
            扰动值，范围 [-1, 1]。
        """
        wx, wy = self.wind_vector(game_time)
        return self.sample_with_wind(world_x, world_y, game_time, wx, wy)

    def sample_raw(self, nx: float, ny: float) -> float:
        """直接噪声采样（调用方负责计算采样坐标）。

        供 WeatherEngine 在 per-chunk 循环中使用预计算的坐标偏移，
        绕过 wind_vector 和空间坐标转换。

        Args:
            nx: 噪声空间 X 坐标。
            ny: 噪声空间 Y 坐标。

        Returns:
            扰动值，范围 [-1, 1]。
        """
        return self._noise.sample(nx, ny)

    def sample_with_wind(
        self, world_x: float, world_y: float, game_time: int,
        wx: float, wy: float,
    ) -> float:
        """采样大气扰动，使用预计算的风向向量。

        当调用方已持有 wind_vector(now) 的结果时使用此方法，
        避免在 per-chunk 循环中重复计算相同的风向。

        Args:
            world_x: 世界 X 坐标（单位 m）。
            world_y: 世界 Y 坐标（单位 m）。
            game_time: 游戏时间（tick）。
            wx: 预计算的风向 X 分量。
            wy: 预计算的风向 Y 分量。

        Returns:
            扰动值，范围 [-1, 1]。
        """
        drift = game_time * self._drift_rate
        nx = world_x / self._resolution + wx * drift
        ny = world_y / self._resolution + wy * drift
        return self._noise.sample(nx, ny)
