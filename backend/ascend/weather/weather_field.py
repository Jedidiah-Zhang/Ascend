"""chunk 级天气状态 — 存基线 + 上次发布的感知类别（用于感知层变化检测）。

天气参数由 WeatherEngine 解析算（baseline + 季节 + 昼夜 + 扰动，每刻连续），
本类存基线和上次发布的感知类别，用于 per-parameter 事件变化比较。

事件逻辑：属性值在感知类别（如 "cold"→"cool"）变化时发布，而非固定数值阈值。
"""


class WeatherField:
    """单个 chunk 的天气状态容器。

    线程不安全，由 WeatherEngine 单线程驱动。

    Attributes:
        chunk_x/chunk_y: chunk 坐标。
        baseline: _ChunkWeatherBaseline 实例（年均基线 + 振幅）。
        last_temp_perception/last_humidity_perception/last_wind_perception/
            last_sunshine_perception: 上次发布的感知类别标签（None=未发布过）。
        last_is_daytime: 上次的昼夜状态（None=未初始化），用于 per-chunk sunrise/sunset 检测。
        atmos_nx/atmos_ny: 预计算的大气噪声采样基坐标，per-chunk 常数。
    """

    __slots__ = ("chunk_x", "chunk_y", "baseline",
                 "last_temp_perception", "last_humidity_perception",
                 "last_wind_perception", "last_sunshine_perception",
                 "last_is_daytime",
                 "atmos_nx", "atmos_ny")

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
        self.last_temp_perception: str | None = None
        self.last_humidity_perception: str | None = None
        self.last_wind_perception: str | None = None
        self.last_sunshine_perception: str | None = None
        self.last_is_daytime: bool | None = None
        # 预计算大气噪声空间基：（chunk_center / resolution），per-chunk 常数
        inv_res = 1.0 / atmos_resolution
        self.atmos_nx = (chunk_x + 0.5) * tile_map_size * inv_res
        self.atmos_ny = (chunk_y + 0.5) * tile_map_size * inv_res

    def __repr__(self) -> str:
        """返回含 chunk 坐标与感知状态的描述。

        Returns:
            str 描述。
        """
        return (
            f"WeatherField(chunk=({self.chunk_x},{self.chunk_y}), "
            f"temp={self.last_temp_perception}, daytime={self.last_is_daytime})"
        )
