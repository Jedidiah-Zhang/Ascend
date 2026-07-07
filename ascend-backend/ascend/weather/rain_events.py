"""降雨事件调度 — 事件式降雨模型。

降雨不是连续变化，而是事件式：一场雨持续 N 小时，间隔 M 天再来。
RainSchedule 预排未来降雨事件，到点切换下雨/停止状态。

事件内强度曲线：ramp up（前 20%）→ peak（中间 60%）→ ramp down（后 20%）。
从年降雨量推算事件频率：年降雨总小时数 = R/mean_intensity，
事件数 = 总小时数/mean_duration，间隔 = (年小时数-总小时数)/事件数。
"""

from dataclasses import dataclass

from ascend.time.constants import GAME_HOUR, GAME_YEAR


# 一年小时数 = GAME_YEAR / GAME_HOUR = 8640
_HOURS_PER_YEAR: int = GAME_YEAR // GAME_HOUR


@dataclass(slots=True)
class RainEvent:
    """一场降雨事件。

    Attributes:
        start_tick: 开始 tick。
        duration: 持续 tick 数。
        peak_intensity: 峰值强度 (mm/小时)。
    """

    start_tick: int
    duration: int
    peak_intensity: float


def intensity_at(
    event: RainEvent,
    now: int,
    ramp_up_ratio: float = 0.2,
    ramp_down_ratio: float = 0.2,
) -> float:
    """事件内强度曲线：ramp up → peak → ramp down。

    默认 20% ramp up + 60% peak + 20% ramp down；可通过 ramp_up_ratio /
    ramp_down_ratio 参数化。ramp 比例之和不能超过 1。

    Args:
        event: 降雨事件。
        now: 当前 tick。
        ramp_up_ratio: 上升段占 duration 的比例 [0, 1)。
        ramp_down_ratio: 下降段占 duration 的比例 [0, 1)。

    Returns:
        强度 (mm/小时)，事件外为 0。
    """
    elapsed = now - event.start_tick
    if elapsed < 0 or elapsed >= event.duration:
        return 0.0
    progress = elapsed / event.duration
    if progress < ramp_up_ratio:
        return event.peak_intensity * (progress / ramp_up_ratio) if ramp_up_ratio > 0 else event.peak_intensity
    sustain_end = 1.0 - ramp_down_ratio
    if progress < sustain_end:
        return event.peak_intensity
    d = 1.0 - progress
    return event.peak_intensity * (d / ramp_down_ratio) if ramp_down_ratio > 0 else event.peak_intensity


def mean_interval_hours(
    annual_rainfall: float,
    mean_intensity: float,
    mean_duration_hours: float,
) -> float:
    """年降雨量 → 平均不下雨间隔（小时）。

    年降雨总小时数 = R/mean_intensity；事件数 = 总小时数/mean_duration；
    间隔 = (年小时数 - 总小时数)/事件数。

    Args:
        annual_rainfall: 年降雨量 (mm/年)。
        mean_intensity: 平均降雨强度 (mm/小时)。
        mean_duration_hours: 平均持续时长 (小时)。

    Returns:
        平均不下雨间隔 (小时)，下限 0.5。
    """
    rain_hours = annual_rainfall / mean_intensity
    n_events = max(1.0, rain_hours / mean_duration_hours)
    return max(0.5, (_HOURS_PER_YEAR - rain_hours) / n_events)


class RainSchedule:
    """降雨调度 — 事件队列管理。

    push 须按 start_tick 升序；pop_due 检测降雨状态切换（开始/停止）；
    intensity 查询当前强度（事件外为 0）。

    线程不安全，由 WeatherEngine 单线程驱动。

    用法:
        s = RainSchedule(rng, annual_rainfall, mean_intensity, mean_duration_h)
        s.push(event)
        s.seed_current(now)
        if s.pop_due(now):       # 降雨状态切换
            ...
        intensity = s.intensity(now)
    """

    def __init__(
        self,
        rng,
        annual_rainfall: float,
        mean_intensity: float,
        mean_duration_hours: float,
        ramp_up_ratio: float = 0.2,
        ramp_down_ratio: float = 0.2,
    ) -> None:
        """初始化降雨调度。

        Args:
            rng: random.Random 实例（seed 化以保证确定性）。
            annual_rainfall: 年降雨量 (mm/年)，决定事件频率。
            mean_intensity: 平均降雨强度 (mm/小时)。
            mean_duration_hours: 平均持续时长 (小时)。
            ramp_up_ratio: 上升段占 duration 比例 [0, 1)。
            ramp_down_ratio: 下降段占 duration 比例 [0, 1)。
        """
        self._rng = rng
        self._annual = annual_rainfall
        self._mean_intensity = mean_intensity
        self._mean_duration_h = mean_duration_hours
        self._ramp_up_ratio = ramp_up_ratio
        self._ramp_down_ratio = ramp_down_ratio
        self._events: list[RainEvent] = []
        self._last_raining = False

    def __len__(self) -> int:
        return len(self._events)

    def push(self, event: RainEvent) -> None:
        """追加速度事件，start_tick 必须严格递增。

        Raises:
            ValueError: start_tick 非严格递增。
        """
        if self._events and event.start_tick <= self._events[-1].start_tick:
            raise ValueError(
                f"事件 start_tick={event.start_tick} 必须严格递增"
                f"（上一个={self._events[-1].start_tick}）"
            )
        self._events.append(event)

    def is_raining(self, now: int) -> bool:
        """当前是否在下雨（处于某事件区间内，即使 ramp up/down 端点强度为 0）。"""
        for event in self._events:
            if event.start_tick <= now < event.start_tick + event.duration:
                return True
        return False

    def intensity(self, now: int) -> float:
        """当前降雨强度 (mm/小时)，事件外为 0。"""
        for event in self._events:
            if event.start_tick <= now < event.start_tick + event.duration:
                return intensity_at(
                    event, now, self._ramp_up_ratio, self._ramp_down_ratio,
                )
        return 0.0

    def seed_current(self, now: int) -> None:
        """初始化降雨状态（用于 pop_due 基线）。"""
        self._last_raining = self.is_raining(now)

    def pop_due(self, now: int) -> bool:
        """检测降雨状态是否切换（开始/停止）。

        错过的雨（now 跳过整场事件）若状态未变化则不触发。

        Args:
            now: 当前 tick。

        Returns:
            状态切换返回 True，否则 False。
        """
        was = self._last_raining
        now_raining = self.is_raining(now)
        if was != now_raining:
            self._last_raining = now_raining
            return True
        return False

    def needs_replenish(self, threshold: int) -> bool:
        """事件数是否低于阈值（触发补算）。"""
        return len(self._events) < threshold

    def prune_before(self, tick: int) -> None:
        """移除完全结束于 tick 之前的事件。

        过期事件已通过 precipitation_start/precipitation_stop 发布到事件总线，
        不再需要保留。裁剪后 is_raining/intensity 只扫当前+未来事件。

        Args:
            tick: 裁剪时刻。end_tick <= tick 的事件被移除。
        """
        self._events = [
            e for e in self._events
            if e.start_tick + e.duration > tick
        ]

    def latest_start_tick(self) -> int | None:
        """最后一个事件的 start_tick（补算起点），空队列返回 None。"""
        return self._events[-1].start_tick if self._events else None

    def latest_end_tick(self) -> int | None:
        """最后一个事件的结束 tick（start + duration），空队列返回 None。"""
        if not self._events:
            return None
        e = self._events[-1]
        return e.start_tick + e.duration

    def generate_next(self, earliest_start: int) -> RainEvent:
        """用 rng 生成下一个降雨事件，start_tick > earliest_start。

        间隔/持续/强度在均值 0.5x-1.5x 范围内随机化。

        Args:
            earliest_start: 最早允许的开始 tick。

        Returns:
            生成的 RainEvent。
        """
        mean_interval = mean_interval_hours(
            self._annual, self._mean_intensity, self._mean_duration_h,
        ) * GAME_HOUR
        mean_duration = self._mean_duration_h * GAME_HOUR
        r = self._rng.random
        interval = int(mean_interval * (0.5 + r()))
        duration = max(GAME_HOUR // 4, int(mean_duration * (0.5 + r())))
        intensity = self._mean_intensity * (0.5 + r())
        return RainEvent(earliest_start + interval, duration, intensity)
