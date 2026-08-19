"""统一天气场 — 特征 + 纹理双分量合成，1km 网格 C1 插值 + tile 级噪声。

下游（WeatherEngine / 地形状态引擎）只认 sample(x, y, t) / sample_grid，
不感知分量存在——防止未来又拆回两个系统。

合成路径：
    纹理分量（多 octave Perlin，波长按参数独立）
        + 特征分量（结构化核：锋面/风暴核/寒潮/热浪，高斯 falloff）
        → 1km 网格点采样 → Catmull-Rom Hermite 双三次插值（C1）
        → tile 级确定性噪声叠加（5-20m 波长，打散折痕）
        → 合成值

解析量：场 seed + 时间可完全重算，不存任何状态（线程安全，只读）。

用法:
    field = UnifiedWeatherField(seed=42)
    val = field.sample("precip", world_x, world_y, t)  # 降水信号
    vals = field.sample_grid("temperature", x0, y0, w, h, t)  # 批量
"""

import math

from ascend.config import (
    WEATHER_FIELD_GRID_SIZE,
    WEATHER_FIELD_TILE_NOISE_WAVELENGTH,
    WEATHER_FIELD_TILE_NOISE_SCALE,
    TEMP_PERTURB_SCALE,
    PRECIP_SIGNAL_MAX,
    PRECIP_THRESHOLD_DRY, PRECIP_THRESHOLD_WET,
    PRECIP_ANNUAL_DRY, PRECIP_ANNUAL_WET,
    PRECIP_INTENSITY_SCALE,
)
from ascend.space import PerlinNoise, clamp

from .atmosphere import (
    TextureField, CH_TEMPERATURE, CH_WIND, CH_PRECIP,
)
from .features import (
    FeatureField, ClimateProxy,
)

# 通道标识（对外统一）
CH_PRECIPITATION = "precip"       # 降水信号（0 ~ PRECIP_SIGNAL_MAX）
CH_TEMPERATURE = "temperature"    # 温度扰动（归一化，× TEMP_PERTURB_SCALE = °C）
CH_HUMIDITY = "humidity"          # 湿度扰动（归一化 [-1, 1]）
CH_WIND = "wind"                  # 风扰动（归一化 [-1, 1]）


# ── 降水校准（场信号 → 降雨强度）────────────────────────────

def precip_threshold(annual_rainfall: float) -> float:
    """年降雨量 → 降水越阈水平（连续标定，无气候带边界跳变）。

    干旱气候带（~50mm/年）→ PRECIP_THRESHOLD_DRY（高阈，难下雨）；
    湿润气候带（~3500mm/年）→ PRECIP_THRESHOLD_WET（低阈，常下雨）。
    中间线性插值，钳制在 [WET, DRY]。

    Args:
        annual_rainfall: 年降雨量 (mm/年)。

    Returns:
        越阈水平（信号 > 阈值 → 下雨）。
    """
    t = (annual_rainfall - PRECIP_ANNUAL_DRY) / (
        PRECIP_ANNUAL_WET - PRECIP_ANNUAL_DRY)
    t = clamp(t, 0.0, 1.0)
    return PRECIP_THRESHOLD_WET + (
        PRECIP_THRESHOLD_DRY - PRECIP_THRESHOLD_WET) * (1.0 - t)


def calibrate_precip(
    signal: float,
    annual_rainfall: float,
    mean_intensity: float = 5.0,
    threshold: float | None = None,
) -> float:
    """降水信号 → 降雨强度 (mm/h)。

    信号 ≤ 阈值 → 0（不下雨）；超阈部分 × 强度放大系数 × 气候带
    基准强度。信号钳制在 PRECIP_SIGNAL_MAX（防校准溢出）。

    Args:
        signal: 场降水信号。
        annual_rainfall: 年降雨量 (mm/年)（阈值推导输入）。
        mean_intensity: 气候带基准降雨强度 (mm/h)。
        threshold: 预计算越阈水平（复用避免重复推导）。

    Returns:
        降雨强度 (mm/h)，≥0。
    """
    if threshold is None:
        threshold = precip_threshold(annual_rainfall)
    if signal <= threshold:
        return 0.0
    excess = min(signal, PRECIP_SIGNAL_MAX) - threshold
    return excess * PRECIP_INTENSITY_SCALE * mean_intensity


class UnifiedWeatherField:
    """统一天气场 — 双分量合成 + 空间连续插值。

    纯查询对象，构造后只读，线程安全（所有下游只读缓存与噪声表）。

    用法:
        field = UnifiedWeatherField(seed=42)
        field.sample("temperature", x, y, t)
        field.sample_grid("precip", x0, y0, 8, 8, t)
    """

    def __init__(
        self,
        seed: int = 0,
        *,
        grid_size: float = WEATHER_FIELD_GRID_SIZE,
        tile_noise_wavelength: float = WEATHER_FIELD_TILE_NOISE_WAVELENGTH,
        tile_noise_scale: float = WEATHER_FIELD_TILE_NOISE_SCALE,
        texture: TextureField | None = None,
        features: FeatureField | None = None,
        climate_proxy: ClimateProxy | None = None,
    ) -> None:
        """初始化统一天气场。

        Args:
            seed: 世界种子（纹理/特征/代理场派生）。
            grid_size: 采样网格间距 (m)，默认 1km（≈5 chunk）。
            tile_noise_wavelength: tile 级确定性噪声波长 (m)。
            tile_noise_scale: tile 级噪声幅度（归一化值）。
            texture: 纹理分量（None 时按 seed 内部创建）。
            features: 特征分量（None 时按 seed 内部创建）。
            climate_proxy: 气候代理（None 时内部创建）。
        """
        self._seed = seed
        self._grid_size = grid_size
        self._tile_noise_scale = tile_noise_scale
        self._texture = texture or TextureField(seed=seed)
        self._proxy = climate_proxy or ClimateProxy(seed=seed)
        self._features = features or FeatureField(
            seed=seed, climate_proxy=self._proxy,
        )
        # tile 级噪声（独立通道，极高频单八度）
        self._tile_noise = PerlinNoise(seed + 920)
        self._tile_freq = 1.0 / tile_noise_wavelength

    def __repr__(self) -> str:
        return (
            f"UnifiedWeatherField(seed={self._seed}, "
            f"grid={self._grid_size:.0f}m)"
        )

    @property
    def seed(self) -> int:
        return self._seed

    @property
    def texture(self) -> TextureField:
        """纹理分量（诊断/测试访问）。"""
        return self._texture

    @property
    def features(self) -> FeatureField:
        """特征分量（诊断/测试访问）。"""
        return self._features

    # ── 插值 ───────────────────────────────────────────────

    @staticmethod
    def _hermite(p0: float, p1: float, p2: float, p3: float, t: float) -> float:
        """Catmull-Rom Hermite 基（一维，C1 连续）。

        Args:
            p0..p3: 四个相邻采样点。
            t: 区间内参数 [0, 1]。

        Returns:
            插值结果。
        """
        a = -0.5 * p0 + 1.5 * p1 - 1.5 * p2 + 0.5 * p3
        b = p0 - 2.5 * p1 + 2.0 * p2 - 0.5 * p3
        c = -0.5 * p0 + 0.5 * p2
        return ((a * t + b) * t + c) * t + p1

    @staticmethod
    def _bicubic(rows: list[list[float]], tx: float, ty: float) -> float:
        """双三次 Hermite 插值（4×4 采样点，C1 连续）。

        Args:
            rows: 4 行 × 4 列的采样点（行 = y 方向）。
            tx: x 方向区间参数 [0, 1]。
            ty: y 方向区间参数 [0, 1]。

        Returns:
            插值结果。
        """
        cols = [
            UnifiedWeatherField._hermite(
                rows[0][i], rows[1][i], rows[2][i], rows[3][i], ty,
            )
            for i in range(4)
        ]
        return UnifiedWeatherField._hermite(cols[0], cols[1], cols[2], cols[3], tx)

    # ── 网格点采样（各通道合成值）───────────────────────────

    def _grid_point(
        self, channel: str, gx: float, gy: float, t: int,
        cores: list, drift: tuple[float, float],
    ) -> float:
        """单个 1km 网格点的通道合成值（纹理 + 特征）。

        Args:
            channel: 通道标识。
            gx, gy: 网格坐标（世界坐标 / grid_size）。
            t: 时刻（tick）。
            cores: 预收集的特征核列表（网格点附近，跨点复用）。
            drift: 预计算的漂移偏移（仅依赖 t，跨点复用）。

        Returns:
            合成值（通道语义见模块 docstring）。
        """
        wx = gx * self._grid_size
        wy = gy * self._grid_size
        if channel == CH_TEMPERATURE:
            tex = self._texture.sample(CH_TEMPERATURE, wx, wy, t, drift)
            off = self._features.temperature_offset_at(cores, wx, wy, t)
            return tex + off / TEMP_PERTURB_SCALE
        if channel == CH_HUMIDITY:
            # 湿度用降水纹理通道（云量相关）
            return self._texture.sample(CH_PRECIP, wx, wy, t, drift)
        if channel == CH_WIND:
            return self._texture.sample(CH_WIND, wx, wy, t, drift)
        if channel == CH_PRECIPITATION:
            # 降水信号 = 纹理 [0,1] + 特征降水提升（风暴核/锋面）
            tex = self._texture.sample(CH_PRECIP, wx, wy, t, drift)
            signal = (tex + 1.0) * 0.5
            signal += self._features.precip_boost_at(cores, wx, wy, t)
            return signal
        raise KeyError(f"未知通道: {channel}")

    def collect_cores(
        self, x: float, y: float, t: int,
    ) -> list:
        """收集采样点附近（覆盖 4×4 网格邻域）的特征核。

        网格邻域 = 3km × 3km（4×4 点 × 1km），一次收集跨多通道/
        多采样点复用（性能语义：同点不同通道共享一次收集）。

        Args:
            x, y: 采样中心（世界坐标 m）。
            t: 时刻（tick）。

        Returns:
            候选核列表。
        """
        # 邻域外扩：4×4 插值邻域（±2 网格）基础上按核完整 falloff 范围
        # （2×最大半径）外扩——cores_overlapping 相交判定按核半径，
        # 仅靠插值邻域会截断核边缘贡献（gauss ~2.8%）
        return self._features.cores_overlapping(
            x - self._grid_size * 2.0, y - self._grid_size * 2.0,
            x + self._grid_size * 2.0, y + self._grid_size * 2.0, t,
            margin=self._features.max_radius * 2.0,
        )

    # ── 对外采样 ───────────────────────────────────────────

    def sample(
        self, channel: str, x: float, y: float, t: int,
        cores: list | None = None, drift: tuple[float, float] | None = None,
    ) -> float:
        """采样任意位置的通道合成值（C1 插值 + tile 级噪声）。

        Args:
            channel: 通道标识（CH_TEMPERATURE / CH_HUMIDITY /
                CH_WIND / CH_PRECIPITATION）。
            x: 世界 X 坐标 (m)。
            y: 世界 Y 坐标 (m)。
            t: 时刻（tick）。
            cores: 预收集的特征核（collect_cores 结果，同点
                多通道采样共享——性能语义，跳过重新收集）。
            drift: 预计算的漂移偏移（texture.drift_offset(t) 结果，
                同时刻多点采样共享）。

        Returns:
            合成值（通道语义见模块 docstring）。

        Raises:
            KeyError: 未知通道。
        """
        if cores is None:
            cores = self.collect_cores(x, y, t)
        if drift is None:
            drift = self._texture.drift_offset(t)
        gxf = x / self._grid_size
        gyf = y / self._grid_size
        i0 = math.floor(gxf) - 1
        j0 = math.floor(gyf) - 1
        rows: list[list[float]] = []
        for j in range(4):
            row = [
                self._grid_point(
                    channel, i0 + i, j0 + j, t, cores, drift,
                )
                for i in range(4)
            ]
            rows.append(row)
        value = self._bicubic(rows, gxf - (i0 + 1), gyf - (j0 + 1))
        # tile 级确定性噪声（打散折痕，掩网格线）
        if self._tile_noise_scale > 0:
            n = self._tile_noise.sample(x * self._tile_freq, y * self._tile_freq)
            value += n * self._tile_noise_scale
        return value

    def sample_grid(
        self, channel: str, x0: float, y0: float, w: int, h: int, t: int,
        drift: tuple[float, float] | None = None,
    ) -> list[float]:
        """批量采样矩形区域的通道合成值（一次核收集）。

        供 #37 解析结算器等栅格消费者使用——批量与单点共享同一
        合成路径，仅核收集复用（性能语义独立）。

        Args:
            channel: 通道标识。
            x0, y0: 区域左上角（世界坐标 m）。
            w, h: 采样点数（按 grid_size 步进）。
            t: 时刻（tick）。
            drift: 预计算的漂移偏移（同时刻多点采样共享）。

        Returns:
            长度为 w*h 的列表，按行排列（每点 = 区域中心）。
        """
        # 区域外扩（插值邻域 + 特征核完整 falloff 范围）收集核
        margin = self._grid_size + self._features.max_radius * 2.0
        cores = self._features.cores_overlapping(
            x0, y0,
            x0 + w * self._grid_size, y0 + h * self._grid_size,
            t, margin=margin,
        )
        if drift is None:
            drift = self._texture.drift_offset(t)
        out: list[float] = []
        for j in range(h):
            for i in range(w):
                x = x0 + (i + 0.5) * self._grid_size
                y = y0 + (j + 0.5) * self._grid_size
                gxf = x / self._grid_size
                gyf = y / self._grid_size
                i0 = math.floor(gxf) - 1
                j0 = math.floor(gyf) - 1
                rows = [
                    [self._grid_point(channel, i0 + ii, j0 + jj, t, cores, drift)
                     for ii in range(4)]
                    for jj in range(4)
                ]
                value = self._bicubic(
                    rows, gxf - (i0 + 1), gyf - (j0 + 1),
                )
                if self._tile_noise_scale > 0:
                    n = self._tile_noise.sample(
                        x * self._tile_freq, y * self._tile_freq,
                    )
                    value += n * self._tile_noise_scale
                out.append(value)
        return out

    # ── 便捷通道（语义化命名）──────────────────────────────

    def precip_signal(
        self, x: float, y: float, t: int,
        cores: list | None = None, drift: tuple[float, float] | None = None,
    ) -> float:
        """降水信号（合成值，供阈值判定）。

        Args:
            x, y: 世界坐标 (m)。
            t: 时刻（tick）。
            cores: 预收集的特征核（同点多通道采样共享）。
            drift: 预计算的漂移偏移。

        Returns:
            降水信号（0 ~ PRECIP_SIGNAL_MAX，越阈见校准函数）。
        """
        return self.sample(CH_PRECIPITATION, x, y, t, cores, drift)

    def wind_multiplier(
        self, x: float, y: float, t: int,
        cores: list | None = None, drift: tuple[float, float] | None = None,
    ) -> float:
        """风速+降雨倍率（特征分量 multiplier 类核叠乘）。

        Args:
            x, y: 世界坐标 (m)。
            t: 时刻（tick）。
            cores: 预收集的特征核（同点多通道采样共享）。
            drift: 预计算的漂移偏移（纹理采样共享，无核时无开销）。

        Returns:
            倍率（≥1.0）。
        """
        if cores is None:
            cores = self.collect_cores(x, y, t)
        return self._features.multiplier_at(cores, x, y, t)