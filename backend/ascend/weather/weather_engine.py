"""天气引擎 — 统一天气场解析算 + 感知层事件发布 + 查询 API。

天气参数每游戏分钟解析算（baseline + 季节 + 昼夜 + 统一天气场），
无快照。场为解析量（seed + 时间可完全重算）——任意过去时刻
精确可查，无调度窗口修剪。

降雨：场降水信号 + 气候带校准阈值判定（连续标定），
区域级事件由 RegionTracker 从加载区越阈网格连通域追踪产生。
极端天气：场特征核（寒潮/热浪/风暴/锋面），核出现/消失 →
区域级 start/stop 事件（per-chunk 覆盖范围跟踪）。

事件按等级发布（整数 tier + prev_tier，边界见 config `*_TIER_BOUNDARIES`），
仅在等级跨越边界时触发，不再按固定数值阈值。

订阅 Calendar 的 minute_change 事件（而非 game_tick），分钟级更新。
"""

import math
import threading
from dataclasses import dataclass

from ascend.log import get_logger
from ascend.space import (
    WeatherParams, ClimateZone, SeasonalityMode, get_climate_template, clamp,
)
from ascend.time import WorldClock
from ascend.world_tree import world_tree as _default_wt, Event, AffectedParty, WorldEvent, SubscriptionScope

from ascend.config import (
    TILE_MAP_SIZE,
    GAME_DAY,
    GAME_HOUR,
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
    PRECIP_SIGNAL_MAX,
    PRECIP_THRESHOLD_DRY, PRECIP_THRESHOLD_WET,
    PRECIP_ANNUAL_DRY, PRECIP_ANNUAL_WET,
    PRECIP_INTENSITY_SCALE,
    GAME_YEAR,
)

from .diurnal import (
    sunrise_hour, sunset_hour, hour_of_game_time, diurnal_phase,
    _solar_declination, sunrise_azimuth,
)
from .events import (
    HumidityChange, PrecipitationStart, PrecipitationStop, SeasonChange,
    SunshineChange, Sunrise, Sunset, TemperatureChange, WindChange,
)
from .season import season_of, season_phase, day_of_season
from .weather_field import WeatherField
from .field import (
    UnifiedWeatherField, CH_PRECIPITATION, CH_TEMPERATURE,
    CH_HUMIDITY, CH_WIND, calibrate_precip,
)
from .region_tracker import RegionTracker, RegionEvent

logger = get_logger(__name__)

# SeasonalityMode → 湿度季节曲线 sharpness（0=余弦，>0=tanh 阶梯）
_SEASONALITY_HUMIDITY_SHARPNESS: dict[SeasonalityMode, float] = {
    SeasonalityMode.NONE: 0.0,
    SeasonalityMode.MONSOON: 2.5,
    SeasonalityMode.FOUR_SEASON: 0.0,
    SeasonalityMode.POLAR: 0.0,
    SeasonalityMode.ALPINE: 0.0,
}

# 气候带 → 基准降雨强度 (mm/h)（降水信号超阈放大基准）
_MEAN_INTENSITY: dict[ClimateZone, float] = {
    ClimateZone.EQUATORIAL_RAINFOREST: 10.0,
    ClimateZone.TROPICAL_SAVANNA:      8.0,
    ClimateZone.DESERT:                2.0,
    ClimateZone.STEPPE:                4.0,
    ClimateZone.TEMPERATE_FOREST:      5.0,
    ClimateZone.SUBARCTIC_TAIGA:       3.0,
    ClimateZone.POLAR_TUNDRA:          2.0,
    ClimateZone.ALPINE:                3.0,
}


# ── 分级函数 ────────────────────────────────────────────────

def _chunk_seed(world_seed: int, cx: int, cy: int) -> int:
    """chunk 坐标 → 确定性 RNG 种子（调试特征核派生用）。

    同一世界种子 + 同一 chunk 坐标 → 同一种子，保证确定性。
    """
    return (world_seed * 1_000_003 + cx) * 1_000_003 + cy


@dataclass(frozen=True, slots=True)
class DaySummary:
    """单日解析天气摘要（地形状态结算器采样契约，见 get_day_summary）。

    Attributes:
        day: 游戏日（1-based）。
        mean_temp: 采样均温 (°C)。
        rain_mm: 当日雨量合计（mm；采样强度 × 采样间隔时长）。
        snow_mm: 当日雪量合计（mm；采样强度 × 采样间隔时长）。
    """

    day: int
    mean_temp: float
    rain_mm: float
    snow_mm: float


def precip_type_for(temperature: float) -> str:
    """降水类型判定 — 事件侧与查询侧（weather_handler）共用的单一实现。

    冰点阈值 0°C：<=0 为雪、>0 为雨。统一先 round(1) 再判定，
    保证事件广播与 UI 显示文案一致。
    """
    return "snow" if round(temperature, 1) <= 0 else "rain"


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


# ── 基线派生 ────────────────────────────────────────────────

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
            年均基线值（rainfall 为 mm/年，降水校准输入）。
        mean_intensity: 气候带基准降雨强度 (mm/h)。
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
    mean_intensity: float
    seasonal_amp: float
    diurnal_amp: float
    humidity_seasonal_amp: float
    humidity_diurnal_amp: float
    seasonality: SeasonalityMode
    latitude: float


class WeatherEngine:
    """天气引擎 — 统一天气场解析算 + 感知层事件 + 查询 API。

    构造时订阅 minute_change（Calendar 发布，每游戏分钟一次）；
    register_chunk 注册 chunk 基线（降水校准输入同时注入区域跟踪器）；
    每分钟解析算各参数，感知类别变化时发对应事件，
    降水（区域连通域）/季节/昼夜/特征核切换发离散事件。

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
            seed: 统一天气场种子（纹理/特征/气候代理派生）。
            world_tree_arg: 可选的 WorldTree 实例（测试注入隔离）。
        """
        self._clock = clock
        self._seed = seed
        self._wt = world_tree_arg if world_tree_arg is not None else _default_wt
        # 查询/写入互斥：handler 线程查询（get_weather 系）与游戏线程
        # 推进（_on_minute_change / register / unregister）并发安全。
        # RLock：_publish 在锁内同步分发事件，防未来订阅者回调重入查询 API
        # （当前唯一订阅者 EventBridge 仅转发不查询，RLock 为低成本防御）。
        self._query_lock = threading.RLock()
        self._field = UnifiedWeatherField(seed=seed)
        self._fields: dict[tuple[int, int], WeatherField] = {}
        self._climates: dict[tuple[int, int], ClimateZone] = {}
        self._tracker = RegionTracker(self._field)
        self._last_season: int | None = None
        self._scope = SubscriptionScope()
        self._scope.subscribe(self._wt, "minute_change", self._on_minute_change)
        logger.debug("天气引擎初始化 seed=%d", seed)

    @property
    def seed(self) -> int:
        """统一天气场种子（存档序列化用，与 __repr__ 展示一致）。"""
        return self._seed

    @property
    def field(self) -> UnifiedWeatherField:
        """统一天气场（调试/测试访问）。"""
        return self._field

    def __repr__(self) -> str:
        return (
            f"WeatherEngine(seed={self._seed}, "
            f"chunks={len(self._fields)})"
        )

    def register_chunk(
        self,
        cx: int,
        cy: int,
        baseline: WeatherParams,
        climate: ClimateZone,
        sea_level_temp: float,
    ) -> None:
        """注册 chunk 的天气基线（降水校准输入同时注入区域跟踪器）。

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
            mean_intensity=_MEAN_INTENSITY.get(climate, 5.0),
            seasonal_amp=seasonal_amp,
            diurnal_amp=diurnal_amp,
            humidity_seasonal_amp=humidity_seasonal_amp,
            humidity_diurnal_amp=humidity_diurnal_amp,
            seasonality=tmpl.seasonality,
            latitude=latitude,
        )
        key = (cx, cy)
        with self._query_lock:
            self._fields[key] = WeatherField(cx, cy, bl)
            self._climates[key] = climate
            self._tracker.set_chunk_baseline(
                cx, cy, baseline.rainfall, bl.mean_intensity,
            )
        logger.debug("注册 chunk (%d,%d) climate=%s", cx, cy, climate)

    def unregister_chunk(self, cx: int, cy: int) -> None:
        """注销 chunk 的天气状态（ChunkStore LRU 淘汰时由 GameEngine 调用）。

        Args:
            cx: chunk X 坐标。
            cy: chunk Y 坐标。
        """
        key = (cx, cy)
        with self._query_lock:
            self._fields.pop(key, None)
            self._climates.pop(key, None)
            self._tracker.remove_chunk(cx, cy)

    def shutdown(self) -> None:
        """取消订阅，释放资源。"""
        self._scope.close()
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
            season/hour/day_of_year_val/solar_decl/season_cos/diurnal_cos。
        """
        day = now // GAME_DAY + 1
        season = int(season_of(day))
        hour = hour_of_game_time(now)  # 带小数小时，昼夜偏移需要精确时间
        day_of_year_val = (now // GAME_DAY) % 360
        # 季节/昼夜余弦基 — phase 对所有 chunk 相同，只有 amplitude 不同
        season_cos = math.cos(season_phase(day))
        diurnal_cos = math.cos(diurnal_phase(hour))
        return {
            "season": season, "hour": hour,
            "day_of_year_val": day_of_year_val,
            "solar_decl": _solar_declination(day_of_year_val),
            "season_cos": season_cos,
            "diurnal_cos": diurnal_cos,
        }

    def _sunlight_intensity(
        self, hour: float, sr: float, ss: float, rainfall: float,
        world_x: float, world_y: float, now: int,
        hum_perturb: float | None = None,
    ) -> float:
        """计算日照强度：正弦日弧 × 降雨衰减 + 云量微调。

        Args:
            hour: 当日小时 [0, 24)。
            sr: 日出小时。
            ss: 日落小时。
            rainfall: 降雨强度 mm/h（含特征核效果），用于衰减日照。
            world_x, world_y: 采样位置（世界坐标 m，chunk 中心）。
            now: 时刻（tick）。
            hum_perturb: 湿度通道合成值（_compute_params 已采样，
                同点共享避免重复采样；None=自行采样）。

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
        # 云量微调 — 湿度通道（场合成值，含特征核），0.05x 慢速作云层效果
        if hum_perturb is None:
            hum_perturb = self._field.sample(CH_HUMIDITY, world_x, world_y, now)
        return max(0.0, min(1.0, intensity + hum_perturb * 0.05))

    def _compute_params(
        self, field: WeatherField, now: int, ctx: dict,
    ) -> tuple[WeatherParams, float, float, float]:
        """解析算 chunk 在 now 时刻的天气。

        温度 = baseline + 季节偏移 + 昼夜偏移 + 场扰动
        湿度 = baseline + 季节偏移（受 SeasonalityMode 影响）+ 昼夜偏移（逆温）+ 场扰动
        风速 = baseline + 场扰动，再 × 特征核倍率
        日照 = 天文日照时长(daylight_hours) + 场扰动
        降雨强度 = 场降水信号 + 气候带校准阈值判定（calibrate_precip）

        Args:
            field: chunk 天气状态。
            now: 当前 tick。
            ctx: _tick_context(now) 返回的 tick 级预计算上下文
                 （对所有 chunk 相同，调用方在 per-chunk 循环外算一次）。

        Returns:
            (WeatherParams, sunrise_hour, sunset_hour, hum_perturb)。
            rainfall 字段装降雨强度 mm/小时；
            hum_perturb 为湿度通道合成值（与日照云量微调共享，
            避免同点重复采样）。
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
        # 统一天气场采样 — chunk 中心（场为解析量，任意过去时刻精确）
        wx = (field.chunk_x + 0.5) * TILE_MAP_SIZE
        wy = (field.chunk_y + 0.5) * TILE_MAP_SIZE
        # 同点多通道共享一次核收集与漂移偏移（性能语义，解析值不变）
        cores = self._field.collect_cores(wx, wy, now)
        drift = self._field.texture.drift_offset(now)
        temp_perturb = self._field.sample(CH_TEMPERATURE, wx, wy, now, cores, drift)
        hum_perturb = self._field.sample(CH_HUMIDITY, wx, wy, now, cores, drift)
        wind_perturb = self._field.sample(CH_WIND, wx, wy, now, cores, drift)
        # 合成并钳界
        temperature = clamp(
            bl.temperature + season_temp + diurnal_temp
            + temp_perturb * TEMP_PERTURB_SCALE,
            *_TEMP_BOUNDS,
        )
        humidity = clamp(
            bl.humidity + season_hum + diurnal_hum
            + hum_perturb * HUMIDITY_PERTURB_SCALE,
            *_HUMIDITY_BOUNDS,
        )
        wind_speed = clamp(
            bl.wind_speed + wind_perturb * WIND_PERTURB_SCALE,
            *_WIND_BOUNDS,
        )
        wind_speed = clamp(
            wind_speed * self._field.wind_multiplier(wx, wy, now, cores, drift),
            *_WIND_BOUNDS,
        )
        # 日照：天文日照时长（用预计算赤纬，纬度不同仍需 per-chunk 算）
        sr = sunrise_hour(day_of_year_val, bl.latitude,
                          solar_decl=ctx["solar_decl"])
        ss = sunset_hour(day_of_year_val, bl.latitude,
                         solar_decl=ctx["solar_decl"])
        daylight = ss - sr
        sunshine = clamp(
            daylight + hum_perturb * SUNSHINE_PERTURB_SCALE,
            *_SUNSHINE_BOUNDS,
        )
        # 降雨：场降水信号 + 气候带校准阈值判定
        signal = self._field.precip_signal(wx, wy, now, cores, drift)
        intensity = calibrate_precip(
            signal, bl.rainfall, bl.mean_intensity,
        )
        return WeatherParams(
            temperature=temperature, rainfall=intensity, sunshine=sunshine,
            altitude=bl.altitude, humidity=humidity, wind_speed=wind_speed,
        ), sr, ss, hum_perturb

    def get_weather(self, cx: int, cy: int,
                    time: int | None = None) -> "WeatherParams | None":
        """查询任意 chunk 在当前或过去时刻的精确天气（解析算，无状态）。

        供 UI 面板、温度计、生态模拟等需要精确值的模块同步使用。
        感知层 AI 决策应订阅事件而非轮询此方法。

        场为解析量（seed + 时间可完全重算），任意过去时刻精确，
        无调度窗口修剪。

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
        with self._query_lock:
            field = self._fields.get(key)
            if field is None:
                return None
            ctx = self._tick_context(time)
            params, _, _, _ = self._compute_params(field, time, ctx)
            return params

    def get_day_summary(
        self, cx: int, cy: int, day: int,
        samples_per_day: int = 4,
    ) -> "DaySummary | None":
        """单日解析天气摘要 — 地形状态结算器的采样契约。

        每日固定采样时刻（均匀 4 点，默认）对解析场取样，按
        precip_type_for 分雨/雪合计降水量、均温取采样均值。
        场为解析量 → 任意过去日精确、确定性（同 seed 同输入同输出）。

        Args:
            cx: chunk X 坐标。
            cy: chunk Y 坐标。
            day: 游戏日（1-based；day 1 = tick [0, GAME_DAY)）。
            samples_per_day: 每日采样点数（须整除 GAME_DAY）。

        Returns:
            DaySummary；chunk 未注册返回 None（调用方跳过该日）。

        Raises:
            ValueError: 采样点数不整除 GAME_DAY 或 day < 1。
        """
        if day < 1:
            raise ValueError(f"day 须 >= 1，实际 {day}")
        if GAME_DAY % samples_per_day != 0:
            raise ValueError(
                f"samples_per_day 须整除 GAME_DAY({GAME_DAY})，"
                f"实际 {samples_per_day}"
            )
        t0 = (day - 1) * GAME_DAY
        step = GAME_DAY // samples_per_day
        step_hours = step / GAME_HOUR
        temps: list[float] = []
        rain_mm = 0.0
        snow_mm = 0.0
        with self._query_lock:
            field = self._fields.get((cx, cy))
            if field is None:
                return None
            for k in range(samples_per_day):
                tick = t0 + k * step
                ctx = self._tick_context(tick)
                params, _, _, _ = self._compute_params(field, tick, ctx)
                temps.append(params.temperature)
                if params.rainfall > 0:
                    mm = params.rainfall * step_hours
                    if precip_type_for(params.temperature) == "snow":
                        snow_mm += mm
                    else:
                        rain_mm += mm
        return DaySummary(
            day=day,
            mean_temp=sum(temps) / len(temps),
            rain_mm=rain_mm,
            snow_mm=snow_mm,
        )

    def get_weather_report(self, cx: int, cy: int) -> (
            "tuple[WeatherParams, float, float, float, float, float] | None"):
        """一次计算返回当前时刻的完整天气报告（网络 handler 专用）。

        天文与噪声只算一次，且降雨衰减自动使用含特征核效果的 rainfall，
        调用方无需穿递。

        Args:
            cx: chunk X 坐标。
            cy: chunk Y 坐标。

        Returns:
            (WeatherParams, sunrise_hour, sunset_hour, daylight_hours,
            sunshine_intensity, sun_azimuth) 或 None（chunk 未注册时）。
            sunshine_intensity 为 0~1 归一化值。
            sun_azimuth 为当日日出方位角（0~180°，从北顺时针），
            随季节渐变、日内恒定（前端光照轨道基准方位）。
        """
        key = (cx, cy)
        with self._query_lock:
            field = self._fields.get(key)
            if field is None:
                return None
            now = self._clock.time
            ctx = self._tick_context(now)
            params, sr, ss, hum_perturb = self._compute_params(field, now, ctx)
            wx = (cx + 0.5) * TILE_MAP_SIZE
            wy = (cy + 0.5) * TILE_MAP_SIZE
            intensity = self._sunlight_intensity(
                ctx["hour"], sr, ss, params.rainfall, wx, wy, now,
                hum_perturb=hum_perturb)
            az = sunrise_azimuth(
                ctx["day_of_year_val"], field.baseline.latitude,
                solar_decl=ctx["solar_decl"])
            return (params, sr, ss, ss - sr, intensity, az)

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

    # ── 公开：调试控制 API ──────────────────────────────────────

    def force_feature(
        self, cx: int, cy: int, type_name: str, active: bool,
    ) -> bool | None:
        """强制开启/关闭指定 chunk 的特征核（终端调试指令用）。

        开启时向特征场注入以 chunk 中心为核心的核（半径 = 该类
        最大半径，10 年持续），解除时移除。注入核与自然核同代码
        路径——查询与事件都走场合成，无特判。
        {type}_start/stop 事件由下一次 minute_change 的核身份
        差异跟踪自动发布。

        Args:
            cx: chunk X 坐标。
            cy: chunk Y 坐标。
            type_name: 特征类型（FEATURE_TYPES 的键）。
            active: True=激活，False=解除。

        Returns:
            True=状态已切换；False=已处于目标状态（no-op）；
            None=chunk 未注册。

        Raises:
            ValueError: type_name 不在 FEATURE_TYPES 注册表中。
        """
        from .features import FEATURE_TYPES
        if type_name not in FEATURE_TYPES:
            raise ValueError(f"未知特征类型: {type_name}")
        chunk_key = (cx, cy)
        with self._query_lock:
            if chunk_key not in self._fields:
                return None
            now = self._clock.time
            features = self._field.features
            if active:
                if features.has_injected(cx, cy, type_name):
                    return False
                cfg = FEATURE_TYPES[type_name]
                wx = (cx + 0.5) * TILE_MAP_SIZE
                wy = (cy + 0.5) * TILE_MAP_SIZE
                # front（带形）需要移动矢量；其余核静止即可
                vel_x = 0.5 if type_name == "front" else 0.0
                vel_y = 0.3 if type_name == "front" else 0.0
                features.inject_core(
                    cx, cy, type_name,
                    center_x=wx, center_y=wy,
                    radius=cfg.radius_range[1],
                    magnitude=1.0,
                    born_tick=now,
                    duration=10 * GAME_YEAR,
                    vel_x=vel_x, vel_y=vel_y,
                )
            else:
                if not features.has_injected(cx, cy, type_name):
                    return False
                features.remove_injected(cx, cy, type_name)
        logger.info(
            "强制%s特征核 %s: chunk (%d,%d)",
            "激活" if active else "解除", type_name, cx, cy,
        )
        return True

    # ── 内部：tick 调度 ─────────────────────────────────────────

    def _on_minute_change(self, event: Event) -> None:
        """每游戏分钟：全局季节 + 区域降水事件 + per-chunk 参数/昼夜/特征核。"""
        now: int = event.data["game_time"]
        tod = now % GAME_DAY
        # tick 级预计算 — 这些值对所有 chunk 相同（与查询 API 共用同一推导）
        ctx = self._tick_context(now)
        season = ctx["season"]
        hour = ctx["hour"]
        with self._query_lock:
            # 全局季节事件（location=(0,0)，不 per-chunk）
            if self._last_season is not None and season != self._last_season:
                self._publish(0, 0, now, SeasonChange(
                    season=season, time_of_day=int(tod),
                ))
            self._last_season = season
            # 区域降水事件（连通域追踪，质心所在 chunk 定位）
            for r in self._tracker.update(now):
                self._publish_region_event(r, now, tod, ctx)
            # per-chunk 事件
            for (cx, cy), field in self._fields.items():
                params, sr, ss, _ = self._compute_params(field, now, ctx)
                # 温度 — 等级变化时发布（首刻静默初始化，初始状态走查询 API）
                temp_tier = classify_temperature(params.temperature)
                if field.last_temp_tier is None:
                    field.last_temp_tier = temp_tier
                elif temp_tier != field.last_temp_tier:
                    self._publish(cx, cy, now, TemperatureChange(
                        temperature=float(params.temperature),
                        prev_tier=field.last_temp_tier,
                        tier=temp_tier,
                        season=season,
                        time_of_day=int(tod),
                    ))
                    field.last_temp_tier = temp_tier
                # 湿度 — 等级变化时发布
                hum_tier = classify_humidity(params.humidity)
                if field.last_humidity_tier is None:
                    field.last_humidity_tier = hum_tier
                elif hum_tier != field.last_humidity_tier:
                    self._publish(cx, cy, now, HumidityChange(
                        humidity=float(params.humidity),
                        prev_tier=field.last_humidity_tier,
                        tier=hum_tier,
                        time_of_day=int(tod),
                    ))
                    field.last_humidity_tier = hum_tier
                # 风 — 等级变化时发布（风向 = 场纹理风向量）
                wind_tier = classify_wind(params.wind_speed)
                if field.last_wind_tier is None:
                    field.last_wind_tier = wind_tier
                elif wind_tier != field.last_wind_tier:
                    wx = (cx + 0.5) * TILE_MAP_SIZE
                    wy = (cy + 0.5) * TILE_MAP_SIZE
                    wind_x, wind_y = self._field.texture.wind_vector_at(
                        wx, wy, now)
                    self._publish(cx, cy, now, WindChange(
                        wind_speed=float(params.wind_speed),
                        prev_tier=field.last_wind_tier,
                        tier=wind_tier,
                        wind_dir_x=float(wind_x),
                        wind_dir_y=float(wind_y),
                        time_of_day=int(tod),
                    ))
                    field.last_wind_tier = wind_tier
                # 日照 — 等级变化时发布
                sun_tier = classify_sunshine(params.sunshine)
                if field.last_sunshine_tier is None:
                    field.last_sunshine_tier = sun_tier
                elif sun_tier != field.last_sunshine_tier:
                    self._publish(cx, cy, now, SunshineChange(
                        sunshine=float(params.sunshine),
                        prev_tier=field.last_sunshine_tier,
                        tier=sun_tier,
                        season=season,
                        time_of_day=int(tod),
                    ))
                    field.last_sunshine_tier = sun_tier
                # per-chunk 昼夜切换（复用 _compute_params 返回的 sr/ss）
                is_day = sr <= hour < ss
                if (field.last_is_daytime is not None
                        and is_day != field.last_is_daytime):
                    dl = ss - sr
                    self._publish(cx, cy, now, Sunrise(
                        time_of_day=int(tod), daylight_hours=float(dl),
                    ) if is_day else Sunset(
                        time_of_day=int(tod), daylight_hours=float(dl),
                    ))
                field.last_is_daytime = is_day
                # 特征核区域事件（核出现/消失 → start/stop）
                self._sync_feature_events(cx, cy, field, now, tod)

    def _sync_feature_events(
        self, cx: int, cy: int, field: WeatherField, now: int, tod: int,
    ) -> None:
        """per-chunk 特征核身份差异 → 区域级 start/stop 事件。

        首刻静默初始化（不补发历史核事件）。

        Args:
            cx, cy: chunk 坐标。
            field: chunk 天气状态（活跃核身份缓存）。
            now: 当前时刻（tick）。
            tod: 当日 tick（time_of_day 字段）。
        """
        x0 = cx * TILE_MAP_SIZE
        y0 = cy * TILE_MAP_SIZE
        x1 = (cx + 1) * TILE_MAP_SIZE
        y1 = (cy + 1) * TILE_MAP_SIZE
        cores = self._field.features.cores_overlapping(x0, y0, x1, y1, now)
        ids = {core.core_id for core in cores}
        prev = field.active_feature_ids
        if prev is None:
            # 首刻静默：仅初始化（注入核除外——调试注入是运行时操作，
            # 应立即可见，不受历史状态静默影响）
            field.active_feature_ids = {
                cid for cid in ids if not cid.startswith("inj:")
            }
            return
        for core in cores:
            if core.core_id in prev:
                continue
            ev = self._field.features.start_event(core, now, time_of_day=tod)
            if ev is not None:
                self._publish(cx, cy, now, ev)
        for core_id in prev - ids:
            # 从当前核列表中定位已消失核的类型（重新收集成本高，
            # 用 type 前缀区分注入核；自然核从段的确定性生成重查）
            core = self._find_core(core_id, now)
            if core is None:
                continue
            ev = self._field.features.stop_event(core, time_of_day=tod)
            if ev is not None:
                self._publish(cx, cy, now, ev)
        field.active_feature_ids = ids

    def _find_core(self, core_id: str, now: int):
        """按 core_id 定位核实例（stop 事件字段派生用）。

        Args:
            core_id: 稳定标识。
            now: 当前时刻（tick）。

        Returns:
            FeatureCore 或 None（不可定位——停止事件按类型兜底）。
        """
        if core_id.startswith("inj:"):
            parts = core_id.split(":")
            core = self._field.features.get_injected(
                int(parts[1]), int(parts[2]), parts[3])
            if core is not None:
                return core
            # 已移除：返回最小核（stop_event 只读 type_name）
            from .features import FeatureCore
            return FeatureCore(
                core_id=core_id, type_name=parts[3],
                born_tick=now, duration=0,
                center_x=0.0, center_y=0.0, radius=0.0,
                magnitude=1.0, vel_x=0.0, vel_y=0.0,
            )
        # 解析核：从出生段重查（b:{bx}:{by}:{seg}:{idx}）
        parts = core_id.split(":")
        bx, by, seg, idx = int(parts[1]), int(parts[2]), int(parts[3]), int(parts[4])
        segs = self._field.features._timelines.get((bx, by))
        if segs is None:
            return None
        cores = segs.get(seg)
        if cores is None or idx >= len(cores):
            return None
        return cores[idx]

    def _publish_region_event(
        self, region: RegionEvent, now: int, tod: int,
        ctx: dict,
    ) -> None:
        """区域降水事件 → precipitation_start/stop 发布。

        Args:
            region: 区域事件（质心 chunk + 强度）。
            now: 当前时刻（tick）。
            tod: 当日 tick（time_of_day 字段）。
            ctx: _tick_context(now) 预计算结果（tick 级复用）。
        """
        cx, cy = region.center_chunk
        if region.kind == "start":
            # 降水类型：质心处温度判定（质心 chunk 未注册时缺省 rain，
            # 不臆造 0°C 判雪——连通域质心几乎必为注册 chunk）
            temp = None
            field = self._fields.get((cx, cy))
            if field is not None:
                params, _, _, _ = self._compute_params(field, now, ctx)
                temp = params.temperature
            self._publish(cx, cy, now, PrecipitationStart(
                precip_type=precip_type_for(temp) if temp is not None else "rain",
                intensity=float(region.intensity),
                time_of_day=tod,
                chunks=region.chunks,
            ))
        else:
            self._publish(cx, cy, now, PrecipitationStop(
                time_of_day=tod,
                chunks=region.chunks,
            ))

    def _publish(
        self, cx: int, cy: int, now: int,
        ev: WorldEvent,
    ) -> None:
        """发布天气事件。"""
        self._wt.publish(Event(
            timestamp=now,
            location=(cx, cy, None, None),
            initiator_type="system",
            initiator_id="weather_engine",
            affected=[AffectedParty("world", "subject")],
            event_type=ev.event_type,
            data=ev.as_dict(),
        ))