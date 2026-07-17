"""天气修改器调度 — 低频率事件队列，数据驱动可扩展。

对天气参数的临时修改（温度偏移、风速/降雨倍率等），事件驱动。
当前实现：寒潮、热浪、暴风雨；可扩展至干旱、雾、霜冻等。

添加新修改器只需在 WEATHER_MODIFIERS 注册表中加一行，无需修改任何逻辑代码。
当前支持两种效果类别：
  - "temperature": 温度偏移
  - "multiplier": 风速+降雨倍率

修改器由 ModifierConfig 集中定义，包含：
  - 各气候带频率、典型持续、基准强度、效果类别
  - WorldTree 事件 schema 的 required 字段
"""

from dataclasses import dataclass, field

from ascend.config import GAME_DAY, GAME_HOUR, GAME_YEAR, MODIFIER_FORECAST_DEPTH, MODIFIER_REPLENISH_THRESHOLD
from ascend.space import ClimateZone


@dataclass(slots=True)
class ModifierConfig:
    """一种天气修改器的完整静态配置。

    Attributes:
        type_name: 类型标识符（"cold_snap" / "heat_wave" / "storm"）。
        rates: 各气候带年均事件数 {ClimateZone: events_per_year}，0=永不发生。
        mean_duration: 典型持续时长均值（tick），实际在 0.5x-1.5x 随机化。
        base_intensity: 基准强度，实际在 0.5x-1.5x 随机化。
        effect: 效果类别—"temperature"（施加 temp_offset °C）或 "multiplier"（倍率）。
        start_schema: start 事件的 required 字段映射 {field_name: type}。
    """

    type_name: str
    rates: dict[ClimateZone, float]
    mean_duration: int
    base_intensity: float
    effect: str  # "temperature" | "multiplier"
    start_schema: dict[str, type] = field(default_factory=dict)


# ── 注册表：添加新类型只需在此加一行 ──────────────────────────────

WEATHER_MODIFIERS: dict[str, ModifierConfig] = {
    "cold_snap": ModifierConfig(
        type_name="cold_snap",
        rates={
            ClimateZone.EQUATORIAL_RAINFOREST: 0.0,
            ClimateZone.TROPICAL_SAVANNA: 0.0,
            ClimateZone.DESERT: 0.0,
            ClimateZone.STEPPE: 0.3,
            ClimateZone.TEMPERATE_FOREST: 1.0,
            ClimateZone.SUBARCTIC_TAIGA: 2.0,
            ClimateZone.POLAR_TUNDRA: 2.0,
            ClimateZone.ALPINE: 1.5,
        },
        mean_duration=3 * GAME_DAY,
        base_intensity=-15.0,
        effect="temperature",
        start_schema={"temperature_offset": float, "time_of_day": int},
    ),
    "heat_wave": ModifierConfig(
        type_name="heat_wave",
        rates={
            ClimateZone.EQUATORIAL_RAINFOREST: 0.0,
            ClimateZone.TROPICAL_SAVANNA: 1.0,
            ClimateZone.DESERT: 2.0,
            ClimateZone.STEPPE: 1.5,
            ClimateZone.TEMPERATE_FOREST: 0.5,
            ClimateZone.SUBARCTIC_TAIGA: 0.0,
            ClimateZone.POLAR_TUNDRA: 0.0,
            ClimateZone.ALPINE: 0.2,
        },
        mean_duration=5 * GAME_DAY,
        base_intensity=15.0,
        effect="temperature",
        start_schema={"temperature_offset": float, "time_of_day": int},
    ),
    "storm": ModifierConfig(
        type_name="storm",
        rates={
            ClimateZone.EQUATORIAL_RAINFOREST: 1.5,
            ClimateZone.TROPICAL_SAVANNA: 2.0,
            ClimateZone.DESERT: 0.1,
            ClimateZone.STEPPE: 0.5,
            ClimateZone.TEMPERATE_FOREST: 1.0,
            ClimateZone.SUBARCTIC_TAIGA: 0.5,
            ClimateZone.POLAR_TUNDRA: 0.3,
            ClimateZone.ALPINE: 1.0,
        },
        mean_duration=6 * GAME_HOUR,
        base_intensity=3.0,
        effect="multiplier",
        start_schema={"wind_multiplier": float, "rain_multiplier": float,
                      "time_of_day": int},
    ),
}

# ── 调度队列参数 ──────────────────────────────────────────────────


@dataclass(slots=True)
class ModifierEvent:
    """一场天气修改器事件。"""

    start_tick: int
    duration: int
    type_name: str
    magnitude: float

    @property
    def end_tick(self) -> int:
        return self.start_tick + self.duration


class ModifierSchedule:
    """天气修改器事件队列 — 单 chunk 单类型，由 ModifierConfig 数据驱动。

    线程不安全，由 WeatherEngine 单线程驱动。

    用法:
        config = WEATHER_MODIFIERS["cold_snap"]
        s = ModifierSchedule(rng, config, ClimateZone.TEMPERATE_FOREST)
        s.seed_current(now)
        if s.pop_due(now): ...
        offset = s.temp_offset(now)
    """

    def __init__(self, rng, config: ModifierConfig, climate: ClimateZone) -> None:
        self._rng = rng
        self._config = config
        events_per_year = config.rates.get(climate, 0.0)
        self._mean_interval = (
            GAME_YEAR / events_per_year if events_per_year > 0
            else float("inf")
        )
        self._mean_duration = config.mean_duration
        self._base_intensity = config.base_intensity
        self._events: list[ModifierEvent] = []
        self._last_active = False

    def __len__(self) -> int:
        return len(self._events)

    def __repr__(self) -> str:
        return (
            f"ModifierSchedule(type={self._config.type_name}, "
            f"interval={self._mean_interval:.0f}t, "
            f"events={len(self._events)})"
        )

    @property
    def type_name(self) -> str:
        return self._config.type_name

    def push(self, event: ModifierEvent) -> None:
        if self._events and event.start_tick <= self._events[-1].start_tick:
            raise ValueError(
                f"modifier event start_tick={event.start_tick} 必须递增"
            )
        self._events.append(event)

    def is_active(self, now: int) -> bool:
        for e in self._events:
            if e.start_tick <= now < e.end_tick:
                return True
        return False

    def _active_event(self, now: int) -> ModifierEvent | None:
        for e in self._events:
            if e.start_tick <= now < e.end_tick:
                return e
        return None

    def temp_offset(self, now: int) -> float:
        """温度偏移 (°C)。仅 effect=temperature 时有效。"""
        if self._config.effect != "temperature":
            return 0.0
        ev = self._active_event(now)
        if ev is None:
            return 0.0
        return ev.magnitude * self._base_intensity

    def wind_rain_multiplier(self, now: int) -> float:
        """风速+降雨倍率。仅 effect=multiplier 时有效，否则返回 1.0。"""
        if self._config.effect != "multiplier":
            return 1.0
        ev = self._active_event(now)
        if ev is None:
            return 1.0
        return ev.magnitude * self._base_intensity

    def start_event_data(self, now: int) -> dict:
        """构造 start 事件的 data 字典（根据 config.start_schema）。"""
        data: dict = {"time_of_day": 0}  # caller 会覆盖 time_of_day
        for field_name in self._config.start_schema:
            if field_name == "time_of_day":
                continue  # caller 填入
            if field_name == "temperature_offset":
                data[field_name] = float(self.temp_offset(now))
            elif field_name == "wind_multiplier":
                data[field_name] = float(self.wind_rain_multiplier(now))
            elif field_name == "rain_multiplier":
                data[field_name] = float(self.wind_rain_multiplier(now))
        return data

    def seed_current(self, now: int) -> None:
        self._last_active = self.is_active(now)

    def pop_due(self, now: int) -> bool:
        was = self._last_active
        now_active = self.is_active(now)
        if was != now_active:
            self._last_active = now_active
            return True
        return False

    def prune_before(self, tick: int) -> None:
        self._events = [e for e in self._events if e.end_tick > tick]

    def force_start(self, now: int, magnitude: float = 1.0) -> bool:
        """强制立即开始一场修改器事件（调试用）。

        以 mean_duration 为时长、给定 magnitude 构造事件插入队首，
        与新事件区间重叠的既有事件（含历史事件）被移除，
        保持队列 start_tick 严格递增。

        状态切换事件（{type}_start）由下一次 minute_change 的
        pop_due 检测发布，此处不直接发事件。

        Args:
            now: 当前 tick。
            magnitude: 强度系数（1.0 = 基准强度）。

        Returns:
            True=已插入强制事件；False=当前已激活（no-op）。
        """
        if self.is_active(now):
            return False
        duration = self._mean_duration
        end = now + duration
        self._events = [e for e in self._events if e.start_tick >= end]
        self._events.insert(0, ModifierEvent(
            now, duration, self._config.type_name, magnitude,
        ))
        return True

    def force_stop(self, now: int) -> bool:
        """强制立即结束当前修改器事件（调试用）。

        将覆盖 now 的活动事件截断为在 now 结束（duration 归零则移除），
        未来已排程的事件不受影响。

        Args:
            now: 当前 tick。

        Returns:
            True=已截断活动事件；False=当前未激活（no-op）。
        """
        stopped = False
        kept: list[ModifierEvent] = []
        for e in self._events:
            if e.start_tick <= now < e.end_tick:
                stopped = True
                new_duration = now - e.start_tick
                if new_duration > 0:
                    e.duration = new_duration
                    kept.append(e)
            else:
                kept.append(e)
        self._events = kept
        return stopped

    def needs_replenish(self, threshold: int) -> bool:
        """事件数是否低于阈值（触发补算）。

        事件率为 0 的调度（仅承载强制事件的动态调度）永不补算，
        避免 generate_next 对无穷间隔取整溢出。
        """
        if self._mean_interval == float("inf"):
            return False
        return len(self._events) < threshold

    def latest_end_tick(self) -> int | None:
        if not self._events:
            return None
        return self._events[-1].end_tick

    def generate_next(self, earliest_start: int) -> ModifierEvent:
        r = self._rng.random
        interval = int(self._mean_interval * (0.5 + r()))
        duration = max(
            GAME_HOUR // 2, int(self._mean_duration * (0.5 + r())),
        )
        magnitude = 0.5 + r()
        return ModifierEvent(
            earliest_start + interval, duration,
            self._config.type_name, magnitude,
        )
