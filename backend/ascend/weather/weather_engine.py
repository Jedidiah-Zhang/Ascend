"""天气引擎 — 解析算天气 + 感知层事件发布 + 查询 API。

天气参数每游戏分钟解析算（baseline + 季节 + 昼夜 + 大气扰动），无快照。
事件按感知类别发布（温度 "cold"/"cool"、湿度 "dry"/"comfortable" 等），
仅在类别跨越边界时触发，不再按固定数值阈值。

查询 API：get_weather(cx, cy, time) 返回任意位置当前/过去时刻的精确天气，
供 UI 面板、生态模拟等需要精确值的模块同步使用。

降雨/季节/昼夜/极端天气保持离散事件调度不变。

订阅 Calendar 的 minute_change 事件（而非 game_tick），分钟级更新。
"""

import math
import random
import zlib
from dataclasses import dataclass

from ascend.log import get_logger
from ascend.space import (
    WeatherParams, ClimateZone, SeasonalityMode, get_climate_template, clamp,
)
from ascend.time import WorldClock
from ascend.world_tree import world_tree as _default_wt, Event, AffectedParty

from ascend.config import (
    TILE_MAP_SIZE,
    GAME_DAY,
    ATMOSPHERE_RESOLUTION, ATMOSPHERE_DRIFT_RATE,
    RAIN_FORECAST_DEPTH, RAIN_REPLENISH_THRESHOLD,
    MODIFIER_FORECAST_DEPTH, MODIFIER_REPLENISH_THRESHOLD,
    TEMP_PERTURB_SCALE, HUMIDITY_PERTURB_SCALE, WIND_PERTURB_SCALE,
    SUNSHINE_PERTURB_SCALE,
    DIURNAL_TO_SEASONAL_RATIO,
    HUMIDITY_DIURNAL_SCALE, HUMIDITY_SEASONAL_SCALE,
    TEMP_TIER_BOUNDARIES,
    HUMIDITY_TIER_BOUNDARIES,
    WIND_TIER_BOUNDARIES,
    SUNSHINE_TIER_BOUNDARIES,
    SUNLIGHT_INTENSITY_TIER_BOUNDARIES,
    TEMP_BOUNDS as _TEMP_BOUNDS,
    HUMIDITY_BOUNDS as _HUMIDITY_BOUNDS,
    WIND_BOUNDS as _WIND_BOUNDS,
    SUNSHINE_BOUNDS as _SUNSHINE_BOUNDS,
    RAIN_INTENSITY_BOUNDS as _RAIN_INTENSITY_BOUNDS,
    LATITUDE_T_MIN as _LATITUDE_T_MIN,
    LATITUDE_T_MAX as _LATITUDE_T_MAX,
    LATITUDE_MIN as _LATITUDE_MIN,
    LATITUDE_MAX as _LATITUDE_MAX,
    SEASONAL_AMP_T_MIN as _SEASONAL_AMP_T_MIN,
    SEASONAL_AMP_T_MAX as _SEASONAL_AMP_T_MAX,
    SEASONAL_AMP_MAX as _SEASONAL_AMP_MAX,
    SEASONAL_AMP_MIN as _SEASONAL_AMP_MIN,
    SEASONAL_AMP_R_REF as _SEASONAL_AMP_R_REF,
    SEASONAL_AMP_R_BONUS as _SEASONAL_AMP_R_BONUS,
    SEASONAL_AMP_BOUNDS as _SEASONAL_AMP_BOUNDS,
)

from .atmosphere import AtmosphereField
from .diurnal import (
    sunrise_hour, sunset_hour, hour_of_game_time,
    _solar_declination,
)
from .events import register_weather_schemas
from .weather_modifier import ModifierSchedule, WEATHER_MODIFIERS
from .rain_events import RainSchedule
from .season import season_of, day_of_season
from .weather_field import WeatherField

logger = get_logger(__name__)

# SeasonalityMode → 湿度季节曲线 sharpness（0=余弦，>0=tanh 阶梯）
_SEASONALITY_HUMIDITY_SHARPNESS: dict[SeasonalityMode, float] = {
    SeasonalityMode.NONE: 0.0,
    SeasonalityMode.MONSOON: 2.5,
    SeasonalityMode.FOUR_SEASON: 0.0,
    SeasonalityMode.POLAR: 0.0,
    SeasonalityMode.ALPINE: 0.0,
}

# ── 分级函数 ────────────────────────────────────────────────

def _classify(value: float, boundaries: tuple[float, ...]) -> int:
    """按阈值返回等级索引（0-based）。

    Args:
        value: 待分类的数值。
        boundaries: 阈值升序元组。

    Returns:
        int，value < boundaries[i] 的最小 i，或在边界外返回 len(boundaries)。
    """
    for i, limit in enumerate(boundaries):
        if value < limit:
            return i
    return len(boundaries)


def classify_temperature(temp: float,
                         boundaries: tuple[float, ...]
                         = TEMP_TIER_BOUNDARIES) -> int:
    """温度 → 等级索引。

    Args:
        temp: 温度 (°C)。
        boundaries: 可选自定义阈值，默认用全局配置。
                    用于不同物种/场景的分级调整。

    Returns:
        int，等级索引（0=最冷，len(boundaries)=最热）。
    """
    return _classify(temp, boundaries)


def classify_humidity(hum: float,
                      boundaries: tuple[float, ...]
                      = HUMIDITY_TIER_BOUNDARIES) -> int:
    """湿度 → 等级索引。

    Args:
        hum: 相对湿度 (%)。
        boundaries: 可选自定义阈值，默认用全局配置。

    Returns:
        int，等级索引（0=最干燥，len(boundaries)=最潮湿）。
    """
    return _classify(hum, boundaries)


def classify_wind(speed: float,
                  boundaries: tuple[float, ...]
                  = WIND_TIER_BOUNDARIES) -> int:
    """风速 → 等级索引。

    Args:
        speed: 风速 (m/s)。
        boundaries: 可选自定义阈值，默认用全局配置。

    Returns:
        int，等级索引（0=无风，len(boundaries)=最大风力）。
    """
    return _classify(speed, boundaries)


def classify_sunshine(sun: float,
                      boundaries: tuple[float, ...]
                      = SUNSHINE_TIER_BOUNDARIES) -> int:
    """日照时长 → 等级索引。

    Args:
        sun: 日照时长 (小时/天)。
        boundaries: 可选自定义阈值，默认用全局配置。

    Returns:
        int，等级索引（0=最短，len(boundaries)=最长）。
    """
    return _classify(sun, boundaries)


def classify_sunlight_intensity(intensity: float,
                                boundaries: tuple[float, ...]
                                = SUNLIGHT_INTENSITY_TIER_BOUNDARIES) -> int:
    """日照强度 (0~1) → 等级索引。

    Args:
        intensity: 归一化日照强度，0=黑夜 1=正午烈日。
        boundaries: 可选自定义阈值，默认用全局配置。

    Returns:
        int，等级索引（0=最暗，len(boundaries)=最亮）。
    """
    return _classify(intensity, boundaries)


# 降雨事件档位：climate → (mean_intensity mm/h, mean_duration h,
#                             ramp_up_ratio, ramp_down_ratio)
_RAIN_PROFILE: dict[ClimateZone, tuple[float, float, float, float]] = {
    ClimateZone.EQUATORIAL_RAINFOREST: (10.0, 2.0, 0.15, 0.15),
    ClimateZone.TROPICAL_SAVANNA:       (8.0, 1.5, 0.2,  0.2),
    ClimateZone.DESERT:                 (2.0, 0.5, 0.35, 0.35),
    ClimateZone.STEPPE:                 (4.0, 1.5, 0.25, 0.25),
    ClimateZone.TEMPERATE_FOREST:       (5.0, 2.0, 0.2,  0.2),
    ClimateZone.SUBARCTIC_TAIGA:        (3.0, 1.5, 0.2,  0.25),
    ClimateZone.POLAR_TUNDRA:           (2.0, 1.0, 0.25, 0.3),
    ClimateZone.ALPINE:                 (3.0, 1.0, 0.25, 0.25),
}

def _derive_seasonal_amp(temperature: float, rainfall: float) -> float:
    """从年均温 + 年降雨连续推导季节温度振幅 (°C)。

    年均温越低 → 振幅越大（极地 ~28, 赤道 ~2）；
    干旱区（低降雨）大陆性气候 → 振幅偏大（+最多 4°C）；
    高降雨区海洋调节 → 振幅偏小（-最多 2°C）。

    保证空间连续：相邻 chunk 的 baseline 温度/降雨接近 →
    seasonal_amp 接近，无气候带边界跳变。

    Args:
        temperature: 年均温度 (°C)。
        rainfall: 年降雨量 (mm/年)。

    Returns:
        季节温度振幅 (°C)，钳制在 [1, 30]。
    """
    t_ratio = (temperature - _SEASONAL_AMP_T_MIN) / (
        _SEASONAL_AMP_T_MAX - _SEASONAL_AMP_T_MIN
    )
    base_amp = _SEASONAL_AMP_MAX - t_ratio * (
        _SEASONAL_AMP_MAX - _SEASONAL_AMP_MIN
    )
    rain_factor = clamp(
        (_SEASONAL_AMP_R_REF - rainfall) / _SEASONAL_AMP_R_REF,
        -0.5, 1.0,
    )
    rain_bonus = rain_factor * _SEASONAL_AMP_R_BONUS
    return clamp(base_amp + rain_bonus, *_SEASONAL_AMP_BOUNDS)


def _derive_latitude(sea_level_temp: float) -> float:
    """从海平面温度连续推导纬度 (°)。

    海平面温度是连续场（纬度噪声推导），不受海拔/气候档位离散判定影响，
    保证气候带交界处纬度连续 → 日照季节振幅 + 日出/日落时刻无跳变。

    线性映射：sea_temp=-5（极地）→ lat=80，sea_temp=35（赤道）→ lat=0。

    Args:
        sea_level_temp: 海平面年均温度 (°C)。

    Returns:
        纬度 (°)，范围 [0, 80]。
    """
    t_ratio = (sea_level_temp - _LATITUDE_T_MIN) / (
        _LATITUDE_T_MAX - _LATITUDE_T_MIN
    )
    lat = _LATITUDE_MAX - t_ratio * (_LATITUDE_MAX - _LATITUDE_MIN)
    return clamp(lat, _LATITUDE_MIN, _LATITUDE_MAX)


@dataclass(slots=True)
class _ChunkWeatherBaseline:
    """chunk 的天气基线（从 annual_baseline + climate 派生，固定不变）。

    Attributes:
        altitude/sunshine/temperature/humidity/wind_speed/rainfall:
            年均基线值（rainfall 为 mm/年，用于推算降雨事件频率）。
        seasonal_amp: 季节温度振幅 (°C)，从年均温+年降雨连续推导（_derive_seasonal_amp），
            保证气候带交界处无跳变。
        diurnal_amp: 昼夜温度振幅 (°C)，= seasonal_amp × DIURNAL_TO_SEASONAL_RATIO。
        humidity_seasonal_amp: 季节湿度振幅 (pp)，= seasonal_amp × HUMIDITY_SEASONAL_SCALE。
        humidity_diurnal_amp: 昼夜湿度振幅 (pp)，= diurnal_amp × HUMIDITY_DIURNAL_SCALE。
        seasonality: 季节性模式（决定湿度曲线形状 — 余弦 vs 季风阶梯）。
        latitude: 纬度 (°)，用于日出/日落时间计算 + 日照时长计算。
    """

    altitude: float
    sunshine: float
    temperature: float
    humidity: float
    wind_speed: float
    rainfall: float
    seasonal_amp: float
    diurnal_amp: float
    humidity_seasonal_amp: float
    humidity_diurnal_amp: float
    seasonality: SeasonalityMode
    latitude: float


class WeatherEngine:
    """天气引擎 — 解析算天气 + 感知层事件 + 查询 API。

    构造时订阅 minute_change（Calendar 发布，每游戏分钟一次）；
    register_chunk 注册 chunk 基线 + 降雨调度；
    每分钟解析算各参数，感知类别变化时发对应事件，
    降水/季节/昼夜/极端天气切换发离散事件。

    线程安全：由 GameEngine 后台单线程驱动，自身不做并发保护。

    用法:
        engine = WeatherEngine(clock, seed=42)
        engine.register_chunk(cx, cy, baseline, climate, sea_level_temp)
        # 事件：感知通知（AI 决策、行为变化）
        #   订阅 temperature_change / humidity_change / wind_change 等
        # API 查询：精确值（UI 面板、生态模拟）
        wp = engine.get_weather(cx, cy)      # 当前时刻
        wp = engine.get_weather(cx, cy, t)   # 当前/过去时刻（未来抛 ValueError）
        engine.shutdown()
    """

    def __init__(
        self,
        clock: WorldClock,
        *,
        seed: int = 0,
        world_tree_arg=None,
    ) -> None:
        """初始化天气引擎。

        Args:
            clock: 世界时钟，用于读取当前 tick。
            seed: 大气场噪声种子（也用于派生各 chunk 降雨 rng 种子）。
            world_tree_arg: 可选的 WorldTree 实例（测试注入隔离）。
        """
        self._clock = clock
        self._seed = seed
        self._wt = world_tree_arg if world_tree_arg is not None else _default_wt
        self._atmosphere = AtmosphereField(seed=seed)
        self._fields: dict[tuple[int, int], WeatherField] = {}
        self._climates: dict[tuple[int, int], ClimateZone] = {}
        self._rain_schedules: dict[tuple[int, int], RainSchedule] = {}
        self._modifier_schedules: dict[tuple[int, int, str], ModifierSchedule] = {}
        self._last_season: int | None = None
        register_weather_schemas(self._wt)
        self._unsub = self._wt.subscribe("minute_change", self._on_minute_change)
        logger.debug("天气引擎初始化 seed=%d", seed)

    def __repr__(self) -> str:
        return (
            f"WeatherEngine(seed={self._seed}, "
            f"chunks={len(self._fields)}, "
            f"rain_schedules={len(self._rain_schedules)})"
        )

    def register_chunk(
        self,
        cx: int,
        cy: int,
        baseline: WeatherParams,
        climate: ClimateZone,
        sea_level_temp: float,
    ) -> None:
        """注册 chunk 的天气基线 + 降雨调度。

        Args:
            cx: chunk X 坐标。
            cy: chunk Y 坐标。
            baseline: chunk 年均气象基线（来自 ChunkData.annual_baseline）。
            climate: chunk 气候档位（取季节性模式 + 降雨档位）。
            sea_level_temp: chunk 海平面年均温度 (°C)（来自 ChunkData.sea_level_temp），
                用于连续推导纬度（日照季节振幅 + 日出/日落）。
        """
        tmpl = get_climate_template(climate)
        seasonal_amp = _derive_seasonal_amp(
            baseline.temperature, baseline.rainfall,
        )
        diurnal_amp = seasonal_amp * DIURNAL_TO_SEASONAL_RATIO
        humidity_seasonal_amp = seasonal_amp * HUMIDITY_SEASONAL_SCALE
        humidity_diurnal_amp = diurnal_amp * HUMIDITY_DIURNAL_SCALE
        latitude = _derive_latitude(sea_level_temp)
        bl = _ChunkWeatherBaseline(
            altitude=baseline.altitude,
            sunshine=baseline.sunshine,
            temperature=baseline.temperature,
            humidity=baseline.humidity,
            wind_speed=baseline.wind_speed,
            rainfall=baseline.rainfall,
            seasonal_amp=seasonal_amp,
            diurnal_amp=diurnal_amp,
            humidity_seasonal_amp=humidity_seasonal_amp,
            humidity_diurnal_amp=humidity_diurnal_amp,
            seasonality=tmpl.seasonality,
            latitude=latitude,
        )
        key = (cx, cy)
        self._fields[key] = WeatherField(
            cx, cy, bl,
            tile_map_size=TILE_MAP_SIZE,
            atmos_resolution=ATMOSPHERE_RESOLUTION,
        )
        self._climates[key] = climate
        # 降雨调度（chunk 坐标派生 rng 种子，保证确定性）
        mean_intensity, mean_duration_h, ramp_up, ramp_down = _RAIN_PROFILE.get(
            climate, (5.0, 2.0, 0.2, 0.2),
        )
        chunk_seed = (self._seed * 1_000_003 + cx) * 1_000_003 + cy
        rain = RainSchedule(
            random.Random(chunk_seed),
            baseline.rainfall, mean_intensity, mean_duration_h,
            ramp_up_ratio=ramp_up, ramp_down_ratio=ramp_down,
        )
        self._rain_schedules[key] = rain
        self._seed_rain(rain)
        # 天气修改器调度（每类型独立队列，仅可能发生的气候带创建）
        for config in WEATHER_MODIFIERS.values():
            if config.rates.get(climate, 0.0) <= 0:
                continue
            ext_seed = chunk_seed + zlib.crc32(config.type_name.encode()) % 1000
            ext = ModifierSchedule(random.Random(ext_seed), config, climate)
            self._modifier_schedules[(cx, cy, config.type_name)] = ext
            self._seed_modifier(ext)
        logger.debug("注册 chunk (%d,%d) climate=%s", cx, cy, climate)

    def unregister_chunk(self, cx: int, cy: int) -> None:
        """注销 chunk 的天气状态（ChunkStore LRU 淘汰时由 GameEngine 调用）。

        Args:
            cx: chunk X 坐标。
            cy: chunk Y 坐标。
        """
        key = (cx, cy)
        self._fields.pop(key, None)
        self._climates.pop(key, None)
        self._rain_schedules.pop(key, None)
        for mk in list(self._modifier_schedules.keys()):
            if mk[:2] == (cx, cy):
                del self._modifier_schedules[mk]

    def shutdown(self) -> None:
        """取消订阅，释放资源。"""
        self._unsub()
        logger.debug("天气引擎已关闭")

    # ── 公开：查询 API ──────────────────────────────────────────

    def _validate_time(self, time: "int | None") -> int:
        """校验并解析查询时刻。

        Args:
            time: 目标时刻（tick），None=当前时刻。

        Returns:
            解析后的时刻（int）。

        Raises:
            ValueError: time 为未来时刻（> 当前时钟）。
        """
        now = self._clock.time
        if time is None:
            return now
        if time > now:
            raise ValueError(f"不允许查询未来时刻: time={time} > now={now}")
        return time

    def _tick_context(self, now: int) -> dict:
        """推导 tick 级共享计算上下文（对所有 chunk 相同）。

        get_weather 查询路径与 _on_minute_change 事件路径共用，
        保证两条路径的公式永远一致。

        Args:
            now: 目标时刻（tick）。

        Returns:
            dict，含 _compute_params 需要的全部 tick 级预计算值：
            day/season/dos/hour/day_of_year_val/wind_x/wind_y/
            drift_x/drift_y/solar_decl/season_cos/diurnal_cos。
        """
        day = now // GAME_DAY + 1
        season = int(season_of(day))
        dos = day_of_season(day)
        hour = hour_of_game_time(now)  # 带小数小时，昼夜偏移需要精确时间
        day_of_year_val = (now // GAME_DAY) % 360
        wind_x, wind_y = self._atmosphere.wind_vector(now)
        # 大气扰动漂移偏移 — tick 级常数
        drift = now * ATMOSPHERE_DRIFT_RATE
        # 季节/昼夜余弦基 — phase 对所有 chunk 相同，只有 amplitude 不同
        season_progress = season + dos / 90.0  # SEASON_LENGTH_DAYS=90
        season_phase = (season_progress - 1.5) / 4.0 * 2.0 * math.pi  # SEASONS_PER_YEAR=4
        season_cos = math.cos(season_phase)
        diurnal_phase = (hour - 14.0) / 24.0 * 2.0 * math.pi  # DIURNAL_PEAK_HOUR=14
        return {
            "day": day, "season": season, "dos": dos, "hour": hour,
            "day_of_year_val": day_of_year_val,
            "wind_x": wind_x, "wind_y": wind_y,
            "drift_x": wind_x * drift, "drift_y": wind_y * drift,
            "solar_decl": _solar_declination(day_of_year_val),
            "season_cos": season_cos,
            "diurnal_cos": math.cos(diurnal_phase),
        }

    def _sunlight_intensity(self, field: WeatherField, hour: float,
                            sr: float, ss: float, rainfall: float,
                            drift_x: float, drift_y: float) -> float:
        """计算日照强度：正弦日弧 × 降雨衰减 + 大气噪声微调。

        Args:
            field: chunk 天气状态（含预计算的 atmos_nx/atmos_ny）。
            hour: 当日小时 [0, 24)。
            sr: 日出小时。
            ss: 日落小时。
            rainfall: 降雨强度 mm/h（含修改器效果），用于衰减日照。
            drift_x: 大气漂移 X 偏移（与 _compute_params 同源，随风向）。
            drift_y: 大气漂移 Y 偏移。

        Returns:
            float，日照强度 [0, 1]，0=黑夜 1=正午烈日。
        """
        daylight = ss - sr
        if not (sr <= hour < ss and daylight > 0):
            return 0.0
        progress = (hour - sr) / daylight
        intensity = math.sin(progress * math.pi)
        # 降雨衰减：雨越大光越暗，暴雨覆盖 80% 日照
        if rainfall > 0:
            rain_factor = min(rainfall / 30.0, 1.0) * 0.8
            intensity *= (1.0 - rain_factor)
        # 大气扰动微调 — 与 _compute_params 同一风向漂移，0.1x 慢速作云层效果
        w_p = self._atmosphere.sample_raw(
            field.atmos_nx + drift_x * 0.1,
            field.atmos_ny + drift_y * 0.1,
        )
        return max(0.0, min(1.0, intensity + w_p * 0.05))

    def get_weather(self, cx: int, cy: int,
                    time: int | None = None) -> "WeatherParams | None":
        """查询任意 chunk 在当前或过去时刻的精确天气（解析算，无状态）。

        供 UI 面板、温度计、生态模拟等需要精确值的模块同步使用。
        感知层 AI 决策应订阅事件而非轮询此方法。

        注意：降雨与极端天气修改器效果仅对调度保留窗口内的时刻精确；
        更久远的历史时刻的降雨事件已被修剪，rainfall 返回 0。

        Args:
            cx: chunk X 坐标。
            cy: chunk Y 坐标。
            time: 目标时刻（tick），None=当前时刻。仅允许当前或过去。

        Returns:
            WeatherParams 或 None（chunk 未注册时）。

        Raises:
            ValueError: time 为未来时刻。
        """
        time = self._validate_time(time)
        key = (cx, cy)
        field = self._fields.get(key)
        if field is None:
            return None
        ctx = self._tick_context(time)
        params, _, _ = self._compute_params(
            field, time, ctx, self._rain_schedules.get(key))
        return params

    def get_weather_report(self, cx: int, cy: int) -> (
            "tuple[WeatherParams, float, float, float, float] | None"):
        """一次计算返回当前时刻的完整天气报告（网络 handler 专用）。

        相比分别调用 get_weather + get_daylight_info，天文与噪声只算一次，
        且降雨衰减自动使用含修改器效果的 rainfall，调用方无需穿递。

        Args:
            cx: chunk X 坐标。
            cy: chunk Y 坐标。

        Returns:
            (WeatherParams, sunrise_hour, sunset_hour, daylight_hours,
            sunshine_intensity) 或 None（chunk 未注册时）。
            sunshine_intensity 为 0~1 归一化值。
        """
        key = (cx, cy)
        field = self._fields.get(key)
        if field is None:
            return None
        now = self._clock.time
        ctx = self._tick_context(now)
        params, sr, ss = self._compute_params(
            field, now, ctx, self._rain_schedules.get(key))
        intensity = self._sunlight_intensity(
            field, ctx["hour"], sr, ss, params.rainfall,
            ctx["drift_x"], ctx["drift_y"])
        return (params, sr, ss, ss - sr, intensity)

    def get_tiers(self, cx: int, cy: int,
                  time: int | None = None) -> dict[str, int] | None:
        """查询任意 chunk 在当前或过去时刻的等级索引。

        便捷方法，返回 {"temperature": 3, "humidity": 1, ...}。

        Args:
            cx: chunk X 坐标。
            cy: chunk Y 坐标。
            time: 目标时刻（tick），None=当前时刻。仅允许当前或过去。

        Returns:
            dict 或 None（chunk 未注册时）。

        Raises:
            ValueError: time 为未来时刻。
        """
        params = self.get_weather(cx, cy, time)
        if params is None:
            return None
        return {
            "temperature": classify_temperature(params.temperature),
            "humidity": classify_humidity(params.humidity),
            "wind": classify_wind(params.wind_speed),
            "sunshine": classify_sunshine(params.sunshine),
        }

    def get_daylight_info(self, cx: int, cy: int,
                          time: int | None = None,
                          rainfall: float = 0.0
                          ) -> tuple[float, float, float, float] | None:
        """查询任意 chunk 在当前或过去时刻的日出日落 + 日照强度。

        Args:
            cx: chunk X 坐标。
            cy: chunk Y 坐标。
            time: 目标时刻（tick），None=当前时刻。仅允许当前或过去。
            rainfall: 当前降雨强度 mm/h（含修改器效果），用于衰减日照。
                      由调用方通过 get_weather() 获取后传入；网络 handler
                      应改用 get_weather_report()，无需手工穿递。

        Returns:
            (sunrise_hour, sunset_hour, daylight_hours, sunshine_intensity)
            或 None（chunk 未注册时）。
            sunshine_intensity 为 0~1 归一化值，0=黑夜 1=正午烈日。

        Raises:
            ValueError: time 为未来时刻。
        """
        time = self._validate_time(time)
        key = (cx, cy)
        field = self._fields.get(key)
        if field is None:
            return None
        ctx = self._tick_context(time)
        lat = field.baseline.latitude
        sr = sunrise_hour(ctx["day_of_year_val"], lat,
                          solar_decl=ctx["solar_decl"])
        ss = sunset_hour(ctx["day_of_year_val"], lat,
                         solar_decl=ctx["solar_decl"])
        intensity = self._sunlight_intensity(
            field, ctx["hour"], sr, ss, rainfall,
            ctx["drift_x"], ctx["drift_y"])
        return (sr, ss, ss - sr, intensity)

    # ── 公开：调试控制 API ──────────────────────────────────────

    def set_rain(self, cx: int, cy: int, active: bool) -> bool | None:
        """强制开启/关闭指定 chunk 的降雨（终端调试指令用）。

        开启时以均值强度插入立即生效的降雨事件，关闭时截断当前事件。
        precipitation_start/stop 事件由下一次 minute_change 自动发布。

        Args:
            cx: chunk X 坐标。
            cy: chunk Y 坐标。
            active: True=开始下雨，False=停止下雨。

        Returns:
            True=状态已切换；False=已处于目标状态（no-op）；
            None=chunk 未注册。
        """
        rain = self._rain_schedules.get((cx, cy))
        if rain is None:
            return None
        now = self._clock.time
        changed = rain.force_start(now) if active else rain.force_stop(now)
        if changed:
            logger.info(
                "强制%s降雨: chunk (%d,%d)",
                "开启" if active else "关闭", cx, cy,
            )
        return changed

    def set_modifier(
        self, cx: int, cy: int, type_name: str, active: bool,
    ) -> bool | None:
        """强制开启/关闭指定 chunk 的天气修改器（终端调试指令用）。

        气候带天然不出现该修改器的 chunk（无调度）在开启时动态创建
        仅承载强制事件的调度（事件率 0，永不自然补算）。
        {type}_start/stop 事件由下一次 minute_change 自动发布。

        Args:
            cx: chunk X 坐标。
            cy: chunk Y 坐标。
            type_name: 修改器类型（WEATHER_MODIFIERS 的键）。
            active: True=激活，False=解除。

        Returns:
            True=状态已切换；False=已处于目标状态（no-op）；
            None=chunk 未注册。

        Raises:
            ValueError: type_name 不在 WEATHER_MODIFIERS 注册表中。
        """
        if type_name not in WEATHER_MODIFIERS:
            raise ValueError(f"未知天气修改器类型: {type_name}")
        chunk_key = (cx, cy)
        if chunk_key not in self._fields:
            return None
        now = self._clock.time
        key = (cx, cy, type_name)
        sched = self._modifier_schedules.get(key)
        if sched is None:
            if not active:
                return False
            config = WEATHER_MODIFIERS[type_name]
            chunk_seed = (self._seed * 1_000_003 + cx) * 1_000_003 + cy
            ext_seed = chunk_seed + zlib.crc32(config.type_name.encode()) % 1000
            sched = ModifierSchedule(
                random.Random(ext_seed), config, self._climates.get(chunk_key),
            )
            sched.seed_current(now)
            self._modifier_schedules[key] = sched
        changed = sched.force_start(now) if active else sched.force_stop(now)
        if changed:
            logger.info(
                "强制%s修改器 %s: chunk (%d,%d)",
                "激活" if active else "解除", type_name, cx, cy,
            )
        return changed

    # ── 内部：解析算 ────────────────────────────────────────────

    def _compute_params(
        self, field: WeatherField, now: int, ctx: dict, rain,
    ) -> tuple[WeatherParams, float, float]:
        """解析算 chunk 在 now 时刻的天气。

        温度 = baseline + 季节偏移 + 昼夜偏移 + 大气扰动
        湿度 = baseline + 季节偏移（受 SeasonalityMode 影响）+ 昼夜偏移（逆温）+ 大气扰动
        风速 = baseline + 大气扰动
        日照 = 天文日照时长(daylight_hours) + 大气扰动
        降雨强度 = RainSchedule.intensity(now)

        Args:
            field: chunk 天气状态（含预计算的 atmos_nx/atmos_ny）。
            now: 当前 tick。
            ctx: _tick_context(now) 返回的 tick 级预计算上下文
                 （对所有 chunk 相同，调用方在 per-chunk 循环外算一次）。
            rain: chunk 的 RainSchedule 实例（或 None），由调用方预查找。

        Returns:
            (WeatherParams, sunrise_hour, sunset_hour)。
            rainfall 字段装降雨强度 mm/小时。
        """
        season_cos = ctx["season_cos"]
        diurnal_cos = ctx["diurnal_cos"]
        day_of_year_val = ctx["day_of_year_val"]
        bl = field.baseline
        # 季节/昼夜偏移 — 余弦基预计算（tick 级复用），只做 per-chunk amplitude 乘法
        season_temp = bl.seasonal_amp * season_cos
        diurnal_temp = bl.diurnal_amp * diurnal_cos
        sharpness = _SEASONALITY_HUMIDITY_SHARPNESS.get(bl.seasonality, 0.0)
        if sharpness > 0:
            season_hum = bl.humidity_seasonal_amp * math.tanh(season_cos * sharpness)
        else:
            season_hum = bl.humidity_seasonal_amp * season_cos
        diurnal_hum = bl.humidity_diurnal_amp * (-diurnal_cos)
        # 空间扰动 — 预计算空间基（field.atmos_nx/_ny）+ tick 级漂移偏移
        perturb = self._atmosphere.sample_raw(
            field.atmos_nx + ctx["drift_x"],
            field.atmos_ny + ctx["drift_y"],
        )
        # 合成并钳界
        temperature = clamp(
            bl.temperature + season_temp + diurnal_temp
            + perturb * TEMP_PERTURB_SCALE,
            *_TEMP_BOUNDS,
        )
        # 天气修改器偏移（遍历所有类型，根据 config.effect 施加不同效果）
        #
        # 叠加语义：
        #   - temperature 类偏移相加。cold_snap 与 heat_wave 在部分气候带
        #     （STEPPE/TEMPERATE_FOREST/ALPINE）都可能触发，同时激活时
        #     相互抵消（-15 + 15 ≈ 0），视为"异常天气对冲"，可接受；
        #   - multiplier 类倍率相乘。当前仅 storm 一种，且单个
        #     ModifierSchedule 的 _active_event 只取首个活动事件，
        #     不存在同类型倍率自乘。若未来新增 multiplier 类型，
        #     需重新评估乘法叠加上限。
        cx, cy = field.chunk_x, field.chunk_y
        temp_extra = 0.0
        wind_mult = 1.0
        rain_mult = 1.0
        for config in WEATHER_MODIFIERS.values():
            sched = self._modifier_schedules.get((cx, cy, config.type_name))
            if sched is None:
                continue
            if config.effect == "temperature":
                temp_extra += sched.temp_offset(now)
            elif config.effect == "multiplier":
                m = sched.wind_rain_multiplier(now)
                wind_mult *= m
                rain_mult *= m
        temperature = clamp(
            temperature + temp_extra, *_TEMP_BOUNDS,
        )
        humidity = clamp(
            bl.humidity + season_hum + diurnal_hum
            + perturb * HUMIDITY_PERTURB_SCALE,
            *_HUMIDITY_BOUNDS,
        )
        wind_speed = clamp(
            bl.wind_speed + perturb * WIND_PERTURB_SCALE, *_WIND_BOUNDS,
        )
        wind_speed = clamp(wind_speed * wind_mult, *_WIND_BOUNDS)
        # 日照：天文日照时长（用预计算赤纬，纬度不同仍需 per-chunk 算）
        sr = sunrise_hour(day_of_year_val, bl.latitude,
                          solar_decl=ctx["solar_decl"])
        ss = sunset_hour(day_of_year_val, bl.latitude,
                         solar_decl=ctx["solar_decl"])
        daylight = ss - sr
        sunshine = clamp(
            daylight + perturb * SUNSHINE_PERTURB_SCALE,
            *_SUNSHINE_BOUNDS,
        )
        intensity = (
            clamp(rain.intensity(now) * rain_mult, *_RAIN_INTENSITY_BOUNDS)
            if rain is not None else 0.0
        )
        return WeatherParams(
            temperature=temperature, rainfall=intensity, sunshine=sunshine,
            altitude=bl.altitude, humidity=humidity, wind_speed=wind_speed,
        ), sr, ss

    def _seed_rain(self, rain: RainSchedule) -> None:
        """注册时预排 RAIN_FORECAST_DEPTH 个未来降雨事件并 seed_current。"""
        now = self._clock.time
        latest_end = now
        for _ in range(RAIN_FORECAST_DEPTH):
            event = rain.generate_next(latest_end)
            rain.push(event)
            latest_end = event.start_tick + event.duration
        rain.seed_current(now)

    def _replenish_schedule(self, schedule, now: int,
                            depth: int, threshold: int) -> None:
        """裁剪过期事件 + 补算到指定深度。

        schedule 需提供 prune_before / needs_replenish /
        latest_end_tick / generate_next / push 接口。
        """
        schedule.prune_before(now)
        for _ in range(depth):
            if not schedule.needs_replenish(threshold):
                break
            latest_end = schedule.latest_end_tick()
            earliest = latest_end if latest_end is not None else now
            schedule.push(schedule.generate_next(earliest))

    def _replenish_rain(self, key: tuple[int, int], now: int) -> None:
        """裁剪过期 + 补算降雨事件。"""
        rain = self._rain_schedules.get(key)
        if rain is not None:
            self._replenish_schedule(
                rain, now, RAIN_FORECAST_DEPTH, RAIN_REPLENISH_THRESHOLD,
            )

    def _seed_modifier(self, schedule: ModifierSchedule) -> None:
        """注册时预排 MODIFIER_FORECAST_DEPTH 个未来修改器事件并 seed_current。"""
        now = self._clock.time
        latest_end = now
        for _ in range(MODIFIER_FORECAST_DEPTH):
            event = schedule.generate_next(latest_end)
            schedule.push(event)
            latest_end = event.end_tick
        schedule.seed_current(now)

    def _replenish_modifier(self, key: tuple[int, int, str], now: int) -> None:
        """裁剪过期 + 补算修改器事件。"""
        sched = self._modifier_schedules.get(key)
        if sched is not None:
            self._replenish_schedule(
                sched, now, MODIFIER_FORECAST_DEPTH, MODIFIER_REPLENISH_THRESHOLD,
            )

    # ── 内部：tick 调度 ─────────────────────────────────────────

    def _on_minute_change(self, event: Event) -> None:
        """每游戏分钟：全局 season_change + per-chunk 参数事件 + per-chunk 昼夜切换。"""
        now: int = event.data["game_time"]
        tod = now % GAME_DAY
        # tick 级预计算 — 这些值对所有 chunk 相同（与查询 API 共用同一推导）
        ctx = self._tick_context(now)
        season = ctx["season"]
        hour = ctx["hour"]
        wind_x = ctx["wind_x"]
        wind_y = ctx["wind_y"]
        # 全局季节事件（location=(0,0)，不 per-chunk）
        if self._last_season is not None and season != self._last_season:
            self._publish(0, 0, now, "season_change", {
                "season": season, "time_of_day": int(tod),
            })
        self._last_season = season
        # per-chunk 事件
        for (cx, cy), field in self._fields.items():
            rain = self._rain_schedules.get((cx, cy))
            params, sr, ss = self._compute_params(field, now, ctx, rain)
            # 温度 — 等级变化时发布（首刻静默初始化，初始状态走查询 API）
            temp_tier = classify_temperature(params.temperature)
            if field.last_temp_tier is None:
                field.last_temp_tier = temp_tier
            elif temp_tier != field.last_temp_tier:
                self._publish(cx, cy, now, "temperature_change", {
                    "temperature": float(params.temperature),
                    "prev_tier": field.last_temp_tier,
                    "tier": temp_tier,
                    "season": season,
                    "time_of_day": int(tod),
                })
                field.last_temp_tier = temp_tier
            # 湿度 — 等级变化时发布
            hum_tier = classify_humidity(params.humidity)
            if field.last_humidity_tier is None:
                field.last_humidity_tier = hum_tier
            elif hum_tier != field.last_humidity_tier:
                self._publish(cx, cy, now, "humidity_change", {
                    "humidity": float(params.humidity),
                    "prev_tier": field.last_humidity_tier,
                    "tier": hum_tier,
                    "time_of_day": int(tod),
                })
                field.last_humidity_tier = hum_tier
            # 风 — 等级变化时发布（风向使用 tick 级预计算值）
            wind_tier = classify_wind(params.wind_speed)
            if field.last_wind_tier is None:
                field.last_wind_tier = wind_tier
            elif wind_tier != field.last_wind_tier:
                self._publish(cx, cy, now, "wind_change", {
                    "wind_speed": float(params.wind_speed),
                    "prev_tier": field.last_wind_tier,
                    "tier": wind_tier,
                    "wind_dir_x": float(wind_x),
                    "wind_dir_y": float(wind_y),
                    "time_of_day": int(tod),
                })
                field.last_wind_tier = wind_tier
            # 日照 — 等级变化时发布
            sun_tier = classify_sunshine(params.sunshine)
            if field.last_sunshine_tier is None:
                field.last_sunshine_tier = sun_tier
            elif sun_tier != field.last_sunshine_tier:
                self._publish(cx, cy, now, "sunshine_change", {
                    "sunshine": float(params.sunshine),
                    "prev_tier": field.last_sunshine_tier,
                    "tier": sun_tier,
                    "season": season,
                    "time_of_day": int(tod),
                })
                field.last_sunshine_tier = sun_tier
            # per-chunk 昼夜切换（复用 _compute_params 返回的 sr/ss）
            is_day = sr <= hour < ss
            if (field.last_is_daytime is not None
                    and is_day != field.last_is_daytime):
                dl = ss - sr
                self._publish(cx, cy, now, "sunrise" if is_day else "sunset", {
                    "time_of_day": int(tod),
                    "daylight_hours": float(dl),
                })
            field.last_is_daytime = is_day
            # 降水（rain 已在循环顶部预查找）
            if rain is not None and rain.pop_due(now):
                if rain.is_raining(now):
                    precip_type = "snow" if params.temperature <= 0 else "rain"
                    self._publish(cx, cy, now, "precipitation_start", {
                        "precip_type": precip_type,
                        "intensity": float(params.rainfall),
                        "time_of_day": int(tod),
                    })
                else:
                    self._publish(cx, cy, now, "precipitation_stop", {
                        "time_of_day": int(tod),
                    })
            # 补算降水（先裁剪过期事件）
            self._replenish_rain((cx, cy), now)
            # 天气修改器事件（遍历 WEATHER_MODIFIERS 注册表）
            for config in WEATHER_MODIFIERS.values():
                sched = self._modifier_schedules.get((cx, cy, config.type_name))
                if sched is None:
                    continue
                if sched.pop_due(now):
                    if sched.is_active(now):
                        data = sched.start_event_data(now)
                        data["time_of_day"] = int(tod)
                        self._publish(cx, cy, now, f"{config.type_name}_start", data)
                    else:
                        self._publish(cx, cy, now, f"{config.type_name}_stop",
                                      {"time_of_day": int(tod)})
                self._replenish_modifier((cx, cy, config.type_name), now)

    def _publish(
        self, cx: int, cy: int, now: int,
        event_type: str, data: dict,
    ) -> None:
        """发布天气事件。"""
        self._wt.publish(Event(
            timestamp=now,
            location=(cx, cy, None, None),
            initiator_type="system",
            initiator_id="weather_engine",
            affected=[AffectedParty("world", "subject")],
            event_type=event_type,
            data=data,
        ))
