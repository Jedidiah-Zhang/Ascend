"""天气引擎 — 解析算天气 + per-parameter 事件发布。

天气参数每游戏分钟解析算（baseline + 季节 + 昼夜 + 大气扰动），无快照。
每 tick 检查各参数变化超阈值 → 发对应 temperature_change/humidity_change/
wind_change；降水 RainSchedule 状态切换 → 发 precipitation_start/precipitation_stop。

降雨仍用事件调度（RainSchedule，从年降雨量推算频率/持续/强度）。

订阅 Calendar 的 minute_change 事件（而非 game_tick），分钟级更新已涵盖
所有阈值穿越（温度日变率 ~0.5°C/h，阈值 0.3°C），无需逐 tick 计算。
"""

import math
import random
from dataclasses import dataclass

from ascend.log import get_logger
from ascend.space import (
    WeatherParams, ClimateZone, SeasonalityMode, get_climate_template, clamp,
    TILE_MAP_SIZE,
)
from ascend.time import WorldClock
from ascend.time.constants import GAME_DAY
from ascend.world_tree import world_tree as _default_wt, Event, AffectedParty

from .atmosphere import AtmosphereField
from .constants import (
    ATMOSPHERE_RESOLUTION, ATMOSPHERE_DRIFT_RATE,
    RAIN_FORECAST_DEPTH, RAIN_REPLENISH_THRESHOLD,
    TEMP_PERTURB_SCALE, HUMIDITY_PERTURB_SCALE, WIND_PERTURB_SCALE,
    SUNSHINE_PERTURB_SCALE,
    DIURNAL_TO_SEASONAL_RATIO,
    HUMIDITY_DIURNAL_SCALE, HUMIDITY_SEASONAL_SCALE,
    TEMP_CHANGE_THRESHOLD, HUMIDITY_CHANGE_THRESHOLD, WIND_CHANGE_THRESHOLD,
    SUNSHINE_CHANGE_THRESHOLD,
)
from .diurnal import (
    diurnal_temp_offset, diurnal_humidity_offset,
    sunrise_hour, sunset_hour, daylight_hours, hour_of_game_time,
    _solar_declination,
)
from .events import register_weather_schemas
from .weather_modifier import ModifierSchedule, WEATHER_MODIFIERS
from .weather_modifier import MODIFIER_FORECAST_DEPTH, MODIFIER_REPLENISH_THRESHOLD
from .rain_events import RainSchedule
from .season import season_of, day_of_season, seasonal_temp_offset, seasonal_humidity_offset
from .weather_field import WeatherField

logger = get_logger(__name__)

# 物理边界（与 climate._PARAM_BOUNDS 一致）
_TEMP_BOUNDS = (-30.0, 50.0)
_HUMIDITY_BOUNDS = (0.0, 100.0)
_WIND_BOUNDS = (0.0, 50.0)
_SUNSHINE_BOUNDS = (0.0, 24.0)
_RAIN_INTENSITY_BOUNDS = (0.0, 100.0)  # mm/小时

# 纬度连续推导的物理参数
# 海平面温度范围 [-5, 35]°C → 纬度 [80, 0]°
# 海平面温度是连续场（纬度噪声推导），保证气候带交界处无纬度跳变
_LATITUDE_T_MIN: float = -5.0   # 年均温下界（极地，lat 最大）
_LATITUDE_T_MAX: float = 35.0   # 年均温上界（赤道，lat 最小）
_LATITUDE_MIN: float = 0.0      # 赤道
_LATITUDE_MAX: float = 80.0     # 极地边缘（超过 80° 极昼极夜过于极端）

# SeasonalityMode → 湿度季节曲线 sharpness（0=余弦，>0=tanh 阶梯）
_SEASONALITY_HUMIDITY_SHARPNESS: dict[SeasonalityMode, float] = {
    SeasonalityMode.NONE: 0.0,
    SeasonalityMode.MONSOON: 2.5,
    SeasonalityMode.FOUR_SEASON: 0.0,
    SeasonalityMode.POLAR: 0.0,
    SeasonalityMode.ALPINE: 0.0,
}

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


# 季节振幅连续推导的物理参数
_SEASONAL_AMP_T_MIN: float = -5.0    # 年均温下界（极地，amp 最大）
_SEASONAL_AMP_T_MAX: float = 35.0    # 年均温上界（赤道，amp 最小）
_SEASONAL_AMP_MAX: float = 28.0      # 低温端振幅
_SEASONAL_AMP_MIN: float = 2.0       # 高温端振幅
_SEASONAL_AMP_R_REF: float = 2000.0  # 降雨参考值（海洋调节，bonus=0）
_SEASONAL_AMP_R_BONUS: float = 4.0   # 干旱区大陆性修正幅度
_SEASONAL_AMP_BOUNDS: tuple[float, float] = (1.0, 30.0)


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
    """天气引擎 — 解析算天气 + per-parameter 事件发布。

    构造时订阅 minute_change（Calendar 发布，每游戏分钟一次）；
    register_chunk 注册 chunk 基线 + 降雨调度；
    每分钟解析算各参数，变化超阈值发对应事件，降水切换发 precipitation_start/stop。

    线程安全：由 GameEngine 后台单线程驱动，自身不做并发保护。

    用法:
        engine = WeatherEngine(clock, seed=42)
        engine.register_chunk(cx, cy, baseline, climate)
        engine.shutdown()
        # 天气数据通过订阅 temperature_change 等事件获取，不开放查询
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
        self._rain_schedules: dict[tuple[int, int], RainSchedule] = {}
        self._modifier_schedules: dict[tuple[int, int, str], ModifierSchedule] = {}
        self._last_season: int | None = None
        register_weather_schemas(self._wt)
        self._unsub = self._wt.subscribe("minute_change", self._on_minute_change)
        logger.debug("天气引擎初始化 seed=%d", seed)

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
            ext_seed = chunk_seed + hash(config.type_name) % 1000
            ext = ModifierSchedule(random.Random(ext_seed), config, climate)
            self._modifier_schedules[(cx, cy, config.type_name)] = ext
            self._seed_modifier(ext)
        logger.debug("注册 chunk (%d,%d) climate=%s", cx, cy, climate)

    def shutdown(self) -> None:
        """取消订阅，释放资源。"""
        self._unsub()
        logger.debug("天气引擎已关闭")

    # ── 内部：解析算 ────────────────────────────────────────────

    def _compute_params(
        self, field: WeatherField, now: int,
        day: int, season: int, dos: int, hour: float,
        *,
        wind_x: float, wind_y: float,
        drift_x: float, drift_y: float,
        solar_decl: float, day_of_year_val: int,
        season_cos: float, season_hum_monsoon: float,
        diurnal_cos: float,
        rain,
    ) -> tuple[WeatherParams, float, float]:
        """解析算 chunk 在 now 时刻的天气。

        温度 = baseline + 季节偏移 + 昼夜偏移 + 大气扰动
        湿度 = baseline + 季节偏移（受 SeasonalityMode 影响）+ 昼夜偏移（逆温）+ 大气扰动
        风速 = baseline + 大气扰动
        日照 = 天文日照时长(daylight_hours) + 大气扰动
        降雨强度 = RainSchedule.intensity(now)

        Args:
            field: chunk 天气状态（含预计算的 _atmos_nx/_atmos_ny）。
            now: 当前 tick。
            day: 游戏日（从 1 开始），由调用方预计算。
            season: 当前季节（int），由调用方预计算。
            dos: 季节内日 [0, SEASON_LENGTH_DAYS)，由调用方预计算。
            hour: 当日小时 [0, 24)，由调用方预计算。
            wind_x: 预计算的风向 X 分量（tick 级复用）。
            wind_y: 预计算的风向 Y 分量（tick 级复用）。
            drift_x: 预计算的大气漂移 X 偏移（tick 级复用）。
            drift_y: 预计算的大气漂移 Y 偏移（tick 级复用）。
            solar_decl: 预计算的太阳赤纬（弧度，tick 级复用）。
            day_of_year_val: 预计算的年内日（tick 级复用）。
            season_cos: 预计算的季节余弦基（tick 级复用）。
            season_hum_monsoon: 预计算的季风湿度曲线基（tanh(season_cos×2.5)）。
            diurnal_cos: 预计算的昼夜余弦基（tick 级复用）。
            rain: chunk 的 RainSchedule 实例（或 None），由调用方预查找。

        Returns:
            (WeatherParams, sunrise_hour, sunset_hour)。
            rainfall 字段装降雨强度 mm/小时。
        """
        bl = field.baseline
        # 季节/昼夜偏移 — 余弦基预计算（tick 级复用），只做 per-chunk amplitude 乘法
        season_temp = bl.seasonal_amp * season_cos
        diurnal_temp = bl.diurnal_amp * diurnal_cos
        sharpness = _SEASONALITY_HUMIDITY_SHARPNESS.get(bl.seasonality, 0.0)
        if sharpness > 0:
            season_hum = bl.humidity_seasonal_amp * (season_hum_monsoon
                if sharpness == 2.5 else math.tanh(season_cos * sharpness))
        else:
            season_hum = bl.humidity_seasonal_amp * season_cos
        diurnal_hum = bl.humidity_diurnal_amp * (-diurnal_cos)
        # 空间扰动 — 预计算空间基（field._atmos_nx/_ny）+ tick 级漂移偏移
        perturb = self._atmosphere.sample_raw(
            field._atmos_nx + drift_x,
            field._atmos_ny + drift_y,
        )
        # 合成并钳界
        temperature = clamp(
            bl.temperature + season_temp + diurnal_temp
            + perturb * TEMP_PERTURB_SCALE,
            *_TEMP_BOUNDS,
        )
        # 天气修改器偏移（遍历所有类型，根据 config.effect 施加不同效果）
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
        sr = sunrise_hour(day_of_year_val, bl.latitude, solar_decl=solar_decl)
        ss = sunset_hour(day_of_year_val, bl.latitude, solar_decl=solar_decl)
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

    def _replenish_rain(self, key: tuple[int, int], now: int) -> None:
        """裁剪过期 + 补算降雨事件到深度（无预算限制，rain 生成轻量）。"""
        rain = self._rain_schedules.get(key)
        if rain is None:  # pragma: no cover  (调用方遍历 _fields，rain 必存在)
            return
        rain.prune_before(now)
        for _ in range(RAIN_FORECAST_DEPTH):
            if not rain.needs_replenish(RAIN_REPLENISH_THRESHOLD):
                break
            latest_end = rain.latest_end_tick()
            earliest = latest_end if latest_end is not None else now
            rain.push(rain.generate_next(earliest))

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
        """裁剪过期 + 补算修改器事件到深度。"""
        sched = self._modifier_schedules.get(key)
        if sched is None:
            return
        sched.prune_before(now)
        for _ in range(MODIFIER_FORECAST_DEPTH):
            if not sched.needs_replenish(MODIFIER_REPLENISH_THRESHOLD):
                break
            latest_end = sched.latest_end_tick()
            earliest = latest_end if latest_end is not None else now
            sched.push(sched.generate_next(earliest))

    # ── 内部：tick 调度 ─────────────────────────────────────────

    def _on_minute_change(self, event: Event) -> None:
        """每游戏分钟：全局 season_change + per-chunk 参数事件 + per-chunk 昼夜切换。"""
        now: int = event.data["game_time"]
        day: int = event.data["day"]
        tod = now % GAME_DAY
        season = int(season_of(day))
        dos = day_of_season(day)
        hour = hour_of_game_time(now)  # 带小数小时，昼夜偏移需要精确时间
        # tick 级预计算 — 这些值对所有 chunk 相同
        day_of_year_val = (now // GAME_DAY) % 360
        wind_x, wind_y = self._atmosphere.wind_vector(now)
        solar_decl = _solar_declination(day_of_year_val)
        # 大气扰动漂移偏移 — tick 级常数
        _drift = now * ATMOSPHERE_DRIFT_RATE
        _drift_x = wind_x * _drift
        _drift_y = wind_y * _drift
        # 季节/昼夜余弦基 — phase 对所有 chunk 相同，只有 amplitude 不同
        _season_progress = season + dos / 90.0  # SEASON_LENGTH_DAYS=90
        _season_phase = (_season_progress - 1.5) / 4.0 * 2.0 * math.pi  # SEASONS_PER_YEAR=4
        _season_cos = math.cos(_season_phase)
        _season_hum_monsoon = math.tanh(_season_cos * 2.5)  # 季风阶梯化，sharpness=2.5
        _diurnal_phase = (hour - 14.0) / 24.0 * 2.0 * math.pi  # DIURNAL_PEAK_HOUR=14
        _diurnal_cos = math.cos(_diurnal_phase)
        # 全局季节事件（location=(0,0)，不 per-chunk）
        if self._last_season is not None and season != self._last_season:
            self._publish(0, 0, now, "season_change", {
                "season": season, "time_of_day": int(tod),
            })
        self._last_season = season
        # per-chunk 事件
        for (cx, cy), field in self._fields.items():
            rain = self._rain_schedules.get((cx, cy))
            params, sr, ss = self._compute_params(
                field, now, day, season, dos, hour,
                wind_x=wind_x, wind_y=wind_y,
                drift_x=_drift_x, drift_y=_drift_y,
                solar_decl=solar_decl, day_of_year_val=day_of_year_val,
                season_cos=_season_cos, season_hum_monsoon=_season_hum_monsoon,
                diurnal_cos=_diurnal_cos,
                rain=rain,
            )
            # 温度
            if (field.last_temp is None
                    or abs(params.temperature - field.last_temp)
                    >= TEMP_CHANGE_THRESHOLD):
                self._publish(cx, cy, now, "temperature_change", {
                    "temperature": float(params.temperature),
                    "season": season,
                    "time_of_day": int(tod),
                })
                field.last_temp = params.temperature
            # 湿度
            if (field.last_humidity is None
                    or abs(params.humidity - field.last_humidity)
                    >= HUMIDITY_CHANGE_THRESHOLD):
                self._publish(cx, cy, now, "humidity_change", {
                    "humidity": float(params.humidity),
                    "time_of_day": int(tod),
                })
                field.last_humidity = params.humidity
            # 风（风向使用 tick 级预计算值）
            if (field.last_wind is None
                    or abs(params.wind_speed - field.last_wind)
                    >= WIND_CHANGE_THRESHOLD):
                self._publish(cx, cy, now, "wind_change", {
                    "wind_speed": float(params.wind_speed),
                    "wind_dir_x": float(wind_x),
                    "wind_dir_y": float(wind_y),
                    "time_of_day": int(tod),
                })
                field.last_wind = params.wind_speed
            # 日照
            if (field.last_sunshine is None
                    or abs(params.sunshine - field.last_sunshine)
                    >= SUNSHINE_CHANGE_THRESHOLD):
                self._publish(cx, cy, now, "sunshine_change", {
                    "sunshine": float(params.sunshine),
                    "season": season,
                    "time_of_day": int(tod),
                })
                field.last_sunshine = params.sunshine
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
