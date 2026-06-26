"""世界时钟 — 驱动游戏时间推进，通过事件总线发布 game_minute。"""

from ascend.bus import bus, Event, AffectedParty
from ascend.log import get_logger
from .mode import TimeMode, GAME_SECONDS_PER_REAL_SECOND

logger = get_logger(__name__)


class WorldClock:
    """世界时钟。

    追踪游戏时间，按当前模式推进并发布 game_minute 事件。
    所有需要时间驱动的模块订阅 game_minute 事件来更新自身状态。

    用法:
        clock = WorldClock()
        clock.tick(0.016)  # 约 60fps，传入真实秒数
        clock.fast_forward(TimeMode.SLEEP, game_hours=8)
    """

    def __init__(self, epoch: float | None = None) -> None:
        """初始化世界时钟。

        Args:
            epoch: 世界起始时间（游戏秒），默认 6:00（第 1 天清晨）。
        """
        if epoch is None:
            from .mode import GAME_HOUR
            epoch = 6 * GAME_HOUR
        self._time: float = epoch
        self._mode: TimeMode = TimeMode.REALTIME
        self._tick_count: int = 0
        self._last_published: float = -60.0  # 确保首次 tick 即发布

    @property
    def time(self) -> float:
        """当前世界时间（游戏秒）。"""
        return self._time

    @property
    def mode(self) -> TimeMode:
        """当前时间模式。"""
        return self._mode

    @property
    def tick_count(self) -> int:
        """累计 tick 次数。"""
        return self._tick_count

    # ── 推进 ──────────────────────────────────────────

    def tick(self, real_dt: float = 0.0) -> Event | None:
        """推进一帧。

        实时模式下步长 = real_dt × 比率。其他模式取固定步长。
        仅当游戏时间推进 >= 1 分钟时才发布 game_minute 事件，避免事件洪水。

        Args:
            real_dt: 上一帧的真实耗时（秒）。仅在 REALTIME 模式下使用。

        Returns:
            发布的 game_minute 事件，若未达到发布阈值则返回 None。
        """
        if self._mode is TimeMode.REALTIME:
            step = real_dt * GAME_SECONDS_PER_REAL_SECOND
        else:
            step = self._mode.step_seconds

        self._time = round(self._time + step, 1)
        self._tick_count += 1

        # 节流：至少间隔 1 游戏分钟才发布
        if self._time - self._last_published < 60:
            return None

        self._last_published = self._time
        event = Event(
            timestamp=self._time,
            location=(0, 0, None, None),
            initiator_type="system",
            initiator_id="world_clock",
            affected=[AffectedParty("world", "subject")],
            event_type="game_minute",
            data={
                "step": step,
                "mode": self._mode.key,
                "tick_count": self._tick_count,
                "game_time": self._time,
            },
        )
        bus.publish(event)
        return event

    def fast_forward(self, target_time: float, mode: TimeMode | None = None) -> list[Event]:
        """快进到目标时间。

        以指定模式的步长连续 tick，直到到达 target_time。
        不传 mode 时使用当前模式；若当前为 REALTIME 则自动切换为 SLEEP
        （REALTIME 依赖真实时间推进，不适合快进）。

        Args:
            target_time: 目标世界时间（游戏秒），必须大于当前时间。
            mode: 快进使用的时间模式。None 表示自动选择。

        Returns:
            快进期间发布的所有 game_minute 事件列表。

        Raises:
            ValueError: 目标时间在过去。
        """
        if target_time <= self._time:
            raise ValueError(f"目标时间 {target_time} 必须在当前时间 {self._time} 之后")

        previous_mode = self._mode
        if mode is not None:
            self._mode = mode
        elif self._mode is TimeMode.REALTIME:
            # REALTIME 模式下 tick() 依赖 real_dt 推进，不适合快进
            self._mode = TimeMode.SLEEP

        ff_mode = self._mode

        events: list[Event] = []
        while self._time < target_time:
            event = self.tick()
            events.append(event)

        self._mode = previous_mode
        logger.info("快进完成: → %.0f (%d ticks, mode=%s)", self._time, len(events), ff_mode.key)
        return events

    def skip_to(self, target_time: float) -> Event:
        """直接跳到目标时间，不产生中间事件。

        仅在时间戳连续不重要时使用（如长跳）。

        Args:
            target_time: 目标世界时间（游戏秒）。

        Returns:
            发布的时间跳转事件。
        """
        if target_time <= self._time:
            raise ValueError(f"目标时间 {target_time} 必须在当前时间 {self._time} 之后")

        skipped = target_time - self._time
        self._time = round(target_time, 1)
        self._tick_count += 1

        event = Event(
            timestamp=self._time,
            location=(0, 0, None, None),
            initiator_type="system",
            initiator_id="world_clock",
            affected=[AffectedParty("world", "subject")],
            event_type="time_skip",
            data={
                "skipped": skipped,
                "game_time": self._time,
                "mode": self._mode.key,
                "tick_count": self._tick_count,
            },
        )
        bus.publish(event)
        logger.info("跳转: %.0f → %.0f (跳过 %.0fs)", self._time - skipped, self._time, skipped)
        return event

    # ── 模式切换 ──────────────────────────────────────

    def set_mode(self, mode: TimeMode) -> None:
        """切换时间模式。

        Args:
            mode: 新的时间模式。
        """
        old = self._mode
        self._mode = mode
        logger.debug("模式切换: %s → %s", old.key, mode.key)

    # ── 时间查询 ──────────────────────────────────────

    def game_days(self) -> float:
        """当前经过的游戏天数。

        Returns:
            游戏天内的小数表示。
        """
        from .mode import GAME_DAY
        return self._time / GAME_DAY

    def game_years(self) -> float:
        """当前经过的游戏年数。

        Returns:
            游戏年（360 天）内的小数表示。
        """
        from .mode import GAME_YEAR
        return self._time / GAME_YEAR

    def __repr__(self) -> str:
        return (
            f"WorldClock(time={self._time:.0f}s, "
            f"day={self.game_days():.1f}, "
            f"mode={self._mode.key}, "
            f"ticks={self._tick_count})"
        )
