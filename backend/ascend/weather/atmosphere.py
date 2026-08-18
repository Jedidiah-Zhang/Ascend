"""全局大气场 — 低分辨率 Perlin 噪声 + 圆形轨道漂移模拟气团移动。

设计：
  - 主噪声场：2D Perlin，采样间距 ATMOSPHERE_RESOLUTION(2km)
  - 气团漂移：采样坐标沿半径 ATMOSPHERE_DRIFT_RADIUS 的圆轨道运动，
    弧长 = ATMOSPHERE_DRIFT_RATE × game_time（线速度 × 时间），
    轨道周期 ≈ 363.6 游戏日 ≈ 1 年，与 360 日季节年错位，跨年不精确重复。
  - 风向：由第二个低频 Perlin 缓慢旋转，仅用于天气报告字段
    （wind_dir_*），不参与扰动场采样。

相邻 chunk 采样点坐标接近 → 天气空间连续；时间漂移 → 气团移动效果。
计算 O(1)，无需邻居查询。
"""

import math

from ascend.space import PerlinNoise

from ascend.config import (
    ATMOSPHERE_RESOLUTION,
    ATMOSPHERE_DRIFT_RATE,
    ATMOSPHERE_DRIFT_RADIUS,
)


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
        drift_radius: float = ATMOSPHERE_DRIFT_RADIUS,
    ) -> None:
        """初始化大气场。

        Args:
            seed: 噪声种子。相同种子产生相同场。
            resolution: 采样间距（世界坐标单位）。越大越粗。
            drift_rate: 气团漂移线速度（噪声单位/tick）。
            drift_radius: 漂移轨道半径（噪声单位）。
                轨道周期 = 2π·drift_radius / (drift_rate·GAME_DAY) 游戏日。
        """
        self._noise = PerlinNoise(seed=seed)
        self._wind_noise = PerlinNoise(seed=seed + 1)
        self._resolution = resolution
        self._drift_rate = drift_rate
        self._drift_radius = drift_radius

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

        噪声采样坐标 = 空间坐标 / resolution + 轨道漂移偏移；
        偏移沿半径 drift_radius 的圆运动，弧长 = drift_rate × game_time，
        θ = 弧长 / 半径。t=0 时偏移为 (0,0)。

        Args:
            world_x: 世界 X 坐标（单位 m）。
            world_y: 世界 Y 坐标（单位 m）。
            game_time: 游戏时间（tick）。

        Returns:
            扰动值，范围 [-1, 1]。
        """
        theta = game_time * self._drift_rate / self._drift_radius
        nx = world_x / self._resolution + self._drift_radius * (
            math.cos(theta) - 1.0)
        ny = world_y / self._resolution + self._drift_radius * math.sin(theta)
        return self._noise.sample(nx, ny)

    def sample_raw(self, nx: float, ny: float) -> float:
        """直接噪声采样（调用方负责计算采样坐标）。

        供 WeatherEngine 在 per-chunk 循环中使用预计算的坐标偏移，
        绕过空间坐标转换。

        Args:
            nx: 噪声空间 X 坐标。
            ny: 噪声空间 Y 坐标。

        Returns:
            扰动值，范围 [-1, 1]。
        """
        return self._noise.sample(nx, ny)
