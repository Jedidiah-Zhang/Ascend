"""纹理分量 — 统一天气场的无结构日常波动层。

多通道 2D Perlin 多八度噪声，波长按参数独立：
  - 温度：低频（5-10km），平滑大尺度
  - 风：中频（2-3km）
  - 降水：高频（1-2km），雨区空间结构的主来源

场沿风向漂移（wind_vector 驱动采样坐标偏移），值随时间连续。

相邻位置采样坐标接近 → 空间连续；每个通道独立 PerlinNoise 实例
（seed 去相关），多八度叠加使场值非平凡连续（无直线式梯度）。

纯查询对象，构造后只读，线程安全。
"""

import math

from ascend.config import (
    TEXTURE_WAVELENGTH_TEMP,
    TEXTURE_WAVELENGTH_WIND,
    TEXTURE_WAVELENGTH_PRECIP,
    TEXTURE_OCTAVES,
    TEXTURE_PERSISTENCE,
    TEXTURE_LACUNARITY,
    TEXTURE_DRIFT_RATE,
)
from ascend.space import PerlinNoise

# 通道标识（采样入口参数）
CH_TEMPERATURE = "temperature"
CH_WIND = "wind"
CH_PRECIP = "precipitation"

# 通道 → 波长 (m)
_DEFAULT_WAVELENGTHS: dict[str, float] = {
    CH_TEMPERATURE: TEXTURE_WAVELENGTH_TEMP,
    CH_WIND: TEXTURE_WAVELENGTH_WIND,
    CH_PRECIP: TEXTURE_WAVELENGTH_PRECIP,
}


class TextureField:
    """统一天气场的纹理分量。

    用法:
        field = TextureField(seed=42)
        val = field.sample("temperature", world_x, world_y, game_time)  # [-1, 1]
        wx, wy = field.wind_vector(game_time)
    """

    def __init__(
        self,
        seed: int = 0,
        *,
        wavelengths: dict[str, float] | None = None,
        octaves: int = TEXTURE_OCTAVES,
        persistence: float = TEXTURE_PERSISTENCE,
        lacunarity: float = TEXTURE_LACUNARITY,
        drift_rate: float = TEXTURE_DRIFT_RATE,
    ) -> None:
        """初始化纹理场。

        Args:
            seed: 噪声种子。相同种子产生相同场。
            wavelengths: 通道 → 波长 (m)。None 用 config 默认
                （温度 7.5km / 风 2.5km / 降水 1.5km）。
            octaves: 多八度层数。
            persistence: 多八度振幅衰减率。
            lacunarity: 多八度频率倍增率。
            drift_rate: 场沿风向漂移线速度（噪声单位/tick）。
        """
        self._octaves = octaves
        self._persistence = persistence
        self._lacunarity = lacunarity
        self._drift_rate = drift_rate
        wl = wavelengths or _DEFAULT_WAVELENGTHS
        # 每通道独立 PerlinNoise（seed 去相关）+ 频率（1/波长）
        self._channels: dict[str, tuple[PerlinNoise, float]] = {}
        for i, (name, wlen) in enumerate(sorted(wl.items())):
            self._channels[name] = (
                PerlinNoise(seed=seed + 900 + i),
                1.0 / wlen,
            )
        self._wind_noise = PerlinNoise(seed=seed + 1)
        # 空间风向扰动（低频双通道，波长 = 风波长）
        self._wind_dir_x = PerlinNoise(seed=seed + 2)
        self._wind_dir_y = PerlinNoise(seed + 3)

    def __repr__(self) -> str:
        channels = ",".join(self._channels)
        return f"TextureField(channels=[{channels}], octaves={self._octaves})"

    @property
    def channels(self) -> tuple[str, ...]:
        """可用通道名（元组，顺序稳定）。"""
        return tuple(self._channels)

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

    def drift_offset(
        self, game_time: int,
    ) -> tuple[float, float]:
        """当前漂移偏移（噪声坐标单位）。

        沿风向矢量线性推进：drift_rate × game_time × wind_vector(game_time)。
        风向本身随时间旋转 → 轨迹是缓转的曲线而非直线，
        气团连续移动且长期不精确重复。
        仅依赖时间——同一时刻多次采样可预计算一次复用。

        Args:
            game_time: 游戏时间（tick）。

        Returns:
            偏移 (dx, dy)（噪声坐标单位）。
        """
        wx, wy = self.wind_vector(game_time)
        d = self._drift_rate * game_time
        return (wx * d, wy * d)

    def wind_vector_at(
        self, world_x: float, world_y: float, game_time: int,
    ) -> tuple[float, float]:
        """位置风向（单位向量）：全局旋转基 + 空间向量场扰动。

        空间扰动为低频双通道噪声（波长 = 风波长），随漂移偏移同步
        移动——相邻位置风向接近（空间连续），时间上缓慢演化。

        Args:
            world_x: 世界 X 坐标（单位 m）。
            world_y: 世界 Y 坐标（单位 m）。
            game_time: 游戏时间（tick）。

        Returns:
            单位向量 (wx, wy)。
        """
        gx, gy = self.wind_vector(game_time)
        dx, dy = self.drift_offset(game_time)
        freq = self._channels[CH_WIND][1]
        nx = world_x * freq + dx
        ny = world_y * freq + dy
        px = self._wind_dir_x.sample(nx, ny)
        py = self._wind_dir_y.sample(nx, ny)
        # 扰动角 ±π/4：全局风向 + 局部偏差，归一化
        angle = math.atan2(gy, gx) + (px + py) * math.pi * 0.25
        return (math.cos(angle), math.sin(angle))

    def sample(
        self,
        channel: str,
        world_x: float,
        world_y: float,
        game_time: int,
        drift: tuple[float, float] | None = None,
    ) -> float:
        """采样指定通道在 (world_x, world_y) 的纹理值。

        Args:
            channel: 通道名（CH_TEMPERATURE / CH_WIND / CH_PRECIP）。
            world_x: 世界 X 坐标（单位 m）。
            world_y: 世界 Y 坐标（单位 m）。
            game_time: 游戏时间（tick）。
            drift: 预计算的漂移偏移（drift_offset(game_time) 结果，
                仅依赖时间——同时刻多点采样复用可省重复计算）。

        Returns:
            纹理值，范围约 [-1, 1]。

        Raises:
            KeyError: 未知通道。
        """
        noise, freq = self._channels[channel]
        if drift is None:
            drift = self.drift_offset(game_time)
        nx = world_x * freq + drift[0]
        ny = world_y * freq + drift[1]
        return noise.octave(
            nx, ny,
            self._octaves,
            persistence=self._persistence,
            lacunarity=self._lacunarity,
        )
