"""chunk 级天气状态 — 存基线 + 上次发布的等级 + 活跃特征核身份。

天气参数由 WeatherEngine 解析算（baseline + 季节 + 昼夜 + 统一天气场，
每刻连续），本类存基线和上次发布的等级索引，用于 per-parameter
事件变化比较；并跟踪该 chunk 覆盖范围内的活跃特征核身份
（出现/消失 → 区域级 start/stop 事件）。

事件逻辑：属性值在等级跨越时发布（含 prev_tier），而非固定数值阈值。
"""


class WeatherField:
    """单个 chunk 的天气状态容器。

    线程不安全，由 WeatherEngine 单线程驱动。

    Attributes:
        chunk_x/chunk_y: chunk 坐标。
        baseline: _ChunkWeatherBaseline 实例（年均基线 + 振幅）。
        last_temp_tier/last_humidity_tier/last_wind_tier/
            last_sunshine_tier: 上次发布的等级索引（None=未发布过）。
        last_is_daytime: 上次的昼夜状态（None=未初始化），用于 per-chunk sunrise/sunset 检测。
        active_feature_ids: 上次跟踪到的活跃特征核 core_id 集合
            （None=未初始化，首刻静默）。
    """

    __slots__ = ("chunk_x", "chunk_y", "baseline",
                 "last_temp_tier", "last_humidity_tier",
                 "last_wind_tier", "last_sunshine_tier",
                 "last_is_daytime",
                 "active_feature_ids")

    def __init__(self, chunk_x: int, chunk_y: int, baseline) -> None:
        """初始化容器。

        Args:
            chunk_x: chunk X 坐标。
            chunk_y: chunk Y 坐标。
            baseline: _ChunkWeatherBaseline 实例。
        """
        self.chunk_x = chunk_x
        self.chunk_y = chunk_y
        self.baseline = baseline
        self.last_temp_tier: int | None = None
        self.last_humidity_tier: int | None = None
        self.last_wind_tier: int | None = None
        self.last_sunshine_tier: int | None = None
        self.last_is_daytime: bool | None = None
        self.active_feature_ids: set[str] | None = None

    def __repr__(self) -> str:
        """返回含 chunk 坐标与等级状态的描述。

        Returns:
            str 描述。
        """
        return (
            f"WeatherField(chunk=({self.chunk_x},{self.chunk_y}), "
            f"temp_tier={self.last_temp_tier}, daytime={self.last_is_daytime})"
        )
