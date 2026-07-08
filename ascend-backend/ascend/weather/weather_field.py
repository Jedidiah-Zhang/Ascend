"""chunk 级天气状态 — 存基线 + 上次发布值（用于变化阈值检测）。

天气参数由 WeatherEngine 解析算（baseline + 季节 + 昼夜 + 扰动，每刻连续），
本类只存基线和上次发布的参数值，用于 per-parameter 事件阈值比较。
"""


class WeatherField:
    """单个 chunk 的天气状态容器。

    线程不安全，由 WeatherEngine 单线程驱动。

    Attributes:
        chunk_x/chunk_y: chunk 坐标。
        baseline: _ChunkWeatherBaseline 实例（年均基线 + 振幅）。
        last_temp/last_humidity/last_wind/last_sunshine: 上次发布的参数值（None=未发布过）。
        last_is_daytime: 上次的昼夜状态（None=未初始化），用于 per-chunk sunrise/sunset 检测。
    """

    __slots__ = ("chunk_x", "chunk_y", "baseline",
                 "last_temp", "last_humidity", "last_wind", "last_sunshine",
                 "last_is_daytime",
                 "_atmos_nx", "_atmos_ny")

    def __init__(self, chunk_x: int, chunk_y: int, baseline,
                 *, tile_map_size: int = 200,
                 atmos_resolution: float = 2000.0) -> None:
        """初始化容器。

        Args:
            chunk_x: chunk X 坐标。
            chunk_y: chunk Y 坐标。
            baseline: _ChunkWeatherBaseline 实例。
            tile_map_size: 每个 chunk 的 tile 数（用于坐标转换）。
            atmos_resolution: 大气噪声采样间距（m）。
        """
        self.chunk_x = chunk_x
        self.chunk_y = chunk_y
        self.baseline = baseline
        self.last_temp: float | None = None
        self.last_humidity: float | None = None
        self.last_wind: float | None = None
        self.last_sunshine: float | None = None
        self.last_is_daytime: bool | None = None
        # 预计算大气噪声空间基：（chunk_center / resolution），per-chunk 常数
        inv_res = 1.0 / atmos_resolution
        self._atmos_nx = (chunk_x + 0.5) * tile_map_size * inv_res
        self._atmos_ny = (chunk_y + 0.5) * tile_map_size * inv_res
