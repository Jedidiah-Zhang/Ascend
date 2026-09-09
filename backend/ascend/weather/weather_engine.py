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
from typing import Mapping

from ascend.config import (GAME_DAY, GAME_HOUR, TILE_MAP_SIZE)
from ascend.causal import (
    InterventionEvaluator, InterventionRecord, InterventionTable, TraceLog,
)
from ascend.log import get_logger
from ascend.space import (ClimateZone, WeatherParams,
                          get_climate_template)
from ascend.time import WorldClock
from ascend.world_tree import (AffectedParty, Event, SubscriptionScope,
                               WorldEvent)
from ascend.world_tree import world_tree as _default_wt

from .derive import (DaySummary, classify_humidity, classify_sunshine,
                     classify_temperature, classify_wind, derive_diurnal_amp,
                     derive_humidity_diurnal_amp, derive_humidity_seasonal_amp,
                     derive_latitude, derive_seasonal_amp, precip_type_for)
from .diurnal import sunrise_azimuth
from .events import (HumidityChange, PrecipitationStart, PrecipitationStop,
                     SeasonChange, Sunrise, Sunset, SunshineChange,
                     TemperatureChange, WindChange)
from .field import (CH_HUMIDITY, CH_TEMPERATURE, CH_WIND, UnifiedWeatherField)
from . import mechanisms as _mechanisms
from .region_tracker import RegionEvent, RegionTracker
from .weather_field import WeatherField

logger = get_logger(__name__)


@dataclass(slots=True)
class _ChunkWeatherBaseline:
    """chunk 的天气基线（从 annual_baseline + climate 派生，固定不变）。

    Attributes:
        altitude/sunshine/temperature/humidity/wind_speed/rainfall:
            年均基线值（rainfall 为 mm/年，降水校准输入）。
        mean_intensity: 气候带基准降雨强度 (mm/h)（来自模板的数据契约）。
        seasonal_amp: 季节温度振幅 (°C)，从年均温+年降雨连续推导（derive_seasonal_amp），
            保证气候带交界处无跳变。
        diurnal_amp: 昼夜温度振幅 (°C)，= seasonal_amp × RATIO（注册表方程）。
        humidity_seasonal_amp: 季节湿度振幅 (pp)，= seasonal_amp × SCALE（注册表方程）。
        humidity_diurnal_amp: 昼夜湿度振幅 (pp)，= seasonal_amp × RATIO × SCALE（注册表方程）。
        humidity_sharpness: 湿度季节曲线 sharpness（0=余弦，>0=tanh 阶梯；
            来自模板数据契约，季风档 2.5）。
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
    humidity_sharpness: float
    latitude: float


def _registry():
    """惰性导入全局机制注册表（打破 ascend.space ↔ ascend.weather 的 import 环）。"""
    from ascend.causal.world import ASCEND_MECHANISMS

    return ASCEND_MECHANISMS


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
        intervention_table: InterventionTable | None = None,
    ) -> None:
        """初始化天气引擎。

        Args:
            clock: 世界时钟，用于读取当前 tick。
            seed: 统一天气场种子（纹理/特征/气候代理派生）。
            world_tree_arg: 可选的 WorldTree 实例（测试注入隔离）。
            intervention_table: 干预表（干预执行器挂载点）；None = 引擎
                惰性自建（无记录时行为与直通注册表一致）。
        """
        self._clock = clock
        self._seed = seed
        self._wt = world_tree_arg if world_tree_arg is not None else _default_wt
        self._intervention_table = intervention_table
        self._intervention_eval: InterventionEvaluator | None = None
        # 研究 trace（默认关闭：未挂载 = 零开销；研究通道按需开启）
        self._trace: TraceLog | None = None
        # 查询/写入互斥：handler 线程查询（get_weather 系）与游戏线程
        # 推进（_on_minute_change / register / unregister）并发安全。
        # RLock：_publish 在锁内同步分发事件，防未来订阅者回调重入查询 API
        # （当前唯一订阅者 EventBridge 仅转发不查询，RLock 为低成本防御）。
        self._query_lock = threading.RLock()
        self._field = UnifiedWeatherField(seed=seed)
        self._fields: dict[tuple[int, int], WeatherField] = {}
        self._climates: dict[tuple[int, int], ClimateZone] = {}
        self._tracker = RegionTracker(self._field, evaluate=self.evaluate_node)
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

    @property
    def intervention_table(self) -> InterventionTable:
        """挂载的干预表（存档/研究 API 共用同一实例；未挂载时惰性自建）。"""
        _, table = self._intervention()
        return table

    @property
    def trace(self) -> TraceLog | None:
        """挂载的研究日志（None = 未开启，求值零开销）。"""
        return self._trace

    def enable_trace(self, capacity: int = 4096) -> TraceLog:
        """开启研究 trace（研究者通道；与玩法事件分库）。

        开启后 ``evaluate_node`` 的每次求值都留下一条完整记录，
        fail-closed：记录不完整即拒绝求值。重复调用返回同一实例。
        """
        if self._trace is None:
            self._trace = TraceLog(_registry(), capacity=capacity)
            self._intervention_eval = InterventionEvaluator(
                _registry(), self._intervention_table, trace=self._trace,
            )
        return self._trace

    def disable_trace(self) -> None:
        """关闭研究 trace（已记录的内容保留在日志实例上）。"""
        self._trace = None
        self._intervention_eval = None

    # ── 完整世界状态（P4）：不可重算部分 ────────────────────────

    def persist_state(self) -> dict:
        """天气侧 W_t 载荷：生效干预记录 + 注入特征核。

        可重算量（统一天气场、气候代理、自然核时间线、区域跟踪器、
        chunk 基线）一律不落盘——它们由 seed + 时钟 + 声明重建，
        漏存它们不会改变轨迹，多存它们则掩盖"状态充分性"的真问题。
        """
        return {
            "interventions": self.intervention_table.persist(),
            "feature_cores": self._field.features.persist_injected(),
        }

    def restore_state(self, payload, *, instance_loader=None) -> None:
        """从存档载荷恢复天气侧 W_t（fail-closed）。

        干预记录逐条重走登记校验（``InterventionTable.restore``），
        注入核逐条校验字段与身份（``FeatureField.restore_injected``）；
        任一条非法即抛 ValueError，不留下半成品状态。

        Args:
            payload: ``persist_state`` 输出的载荷。
            instance_loader: 可选实例装载器（读档时把被 LRU 淘汰的
                目标 chunk 拉回来再校验），见 ``InterventionTable.restore``。
        """
        if not isinstance(payload, Mapping):
            raise ValueError(
                f"天气状态载荷必须为映射: {type(payload).__name__}"
            )
        self.intervention_table.restore(
            payload.get("interventions") or [],
            instance_loader=instance_loader,
        )
        self._field.features.restore_injected(
            payload.get("feature_cores") or []
        )

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
        seasonal_amp = derive_seasonal_amp(
            baseline.temperature, baseline.rainfall,
        )
        diurnal_amp = derive_diurnal_amp(seasonal_amp)
        humidity_seasonal_amp = derive_humidity_seasonal_amp(seasonal_amp)
        humidity_diurnal_amp = derive_humidity_diurnal_amp(seasonal_amp)
        latitude = derive_latitude(sea_level_temp)
        bl = _ChunkWeatherBaseline(
            altitude=baseline.altitude,
            sunshine=baseline.sunshine,
            temperature=baseline.temperature,
            humidity=baseline.humidity,
            wind_speed=baseline.wind_speed,
            rainfall=baseline.rainfall,
            mean_intensity=tmpl.mean_precip_intensity,
            seasonal_amp=seasonal_amp,
            diurnal_amp=diurnal_amp,
            humidity_seasonal_amp=humidity_seasonal_amp,
            humidity_diurnal_amp=humidity_diurnal_amp,
            humidity_sharpness=tmpl.humidity_sharpness,
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

    def evaluate_node(
        self,
        node_id: str,
        parent_values: dict,
        *,
        frame: int,
        instance: tuple = (),
    ) -> object:
        """节点求值唯一入口（含干预覆盖）。

        引擎内所有注册表求值（tick 派生 / chunk 合成 / 降水阈值与强度）
        都经本方法，``region_tracker`` 亦以本方法为注入回调——同一节点
        在任何消费路径上只有一个求值点。``WIRED_NODES`` 声明与本方法的
        调用点由漂移巡检锁死。

        Args:
            node_id: 输出节点 ID。
            parent_values: 父值（按调用方已解析的实例与帧提供）。
            frame: 当前世界 tick。
            instance: 实例坐标（全局分量用空元组）。
        """
        evaluator, _ = self._intervention()
        return evaluator.evaluate(
            node_id, parent_values, frame=frame, instance=instance,
        )

    def has_chunk(self, cx: int, cy: int) -> bool:
        """chunk 是否已注册（干预执行器实例存在性校验用）。"""
        with self._query_lock:
            return (cx, cy) in self._fields

    def instance_exists(self, node_id: str, instance: tuple) -> bool:
        """干预实例存在性查询（注入干预表）。

        当前空间实例域均为 2 轴 chunk 坐标；轴数不符一律返回 False
        （fail-closed：干净拒绝，而非让回调抛 TypeError）。
        """
        if len(instance) != 2:
            return False
        return self.has_chunk(instance[0], instance[1])

    def _intervention(self) -> tuple[InterventionEvaluator, InterventionTable]:
        """干预执行器挂载点：覆盖感知求值器 + 干预表（惰性创建）。

        无干预表时引擎自建（无记录 → 行为与直通注册表一致），并注入
        时钟与实例存在性查询，使自建表与生产注入表行为一致。
        惰性初始化非原子：由游戏线程单线程驱动（server/dispatcher 同线程），
        首次调用仅在此线程发生，无需加锁。
        """
        if self._intervention_eval is None:
            if self._intervention_table is None:
                self._intervention_table = InterventionTable(
                    _registry(),
                    now=lambda: self._clock.time,
                    instance_exists=self.instance_exists,
                )
            self._intervention_eval = InterventionEvaluator(
                _registry(), self._intervention_table, trace=self._trace,
            )
        return self._intervention_eval, self._intervention_table

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
        """推导 tick 级共享计算上下文（经机制注册表求值，对所有 chunk 相同）。

        get_weather 查询路径与 _on_minute_change 事件路径共用，
        保证两条路径的公式永远一致。

        Args:
            now: 目标时刻（tick）。

        Returns:
            dict，含 _compute_params 需要的全部 tick 级预计算值：
            season/hour/day_of_year_val/solar_decl/season_cos/diurnal_cos。
        """
        m = _mechanisms
        day = self.evaluate_node(m.DAY, {m.CLOCK_TICK: now},
                                 frame=now, instance=())
        hour = self.evaluate_node(m.HOUR_OF_DAY, {m.CLOCK_TICK: now},
                                  frame=now, instance=())
        day_of_year_val = self.evaluate_node(
            m.DAY_OF_YEAR, {m.CLOCK_TICK: now}, frame=now, instance=(),
        )
        return {
            "season": self.evaluate_node(
                m.SEASON, {m.DAY: day}, frame=now, instance=(),
            ),
            "hour": hour,
            "day_of_year_val": day_of_year_val,
            "solar_decl": self.evaluate_node(
                m.SOLAR_DECLINATION, {m.DAY_OF_YEAR: day_of_year_val},
                frame=now, instance=(),
            ),
            "season_cos": self.evaluate_node(
                m.SEASON_PHASE_COS, {m.DAY: day}, frame=now, instance=(),
            ),
            "diurnal_cos": self.evaluate_node(
                m.DIURNAL_PHASE_COS, {m.HOUR_OF_DAY: hour},
                frame=now, instance=(),
            ),
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
        湿度 = baseline + 季节偏移（受模板 humidity_sharpness 影响）+ 昼夜偏移（逆温）+ 场扰动
        风速 = baseline + 场扰动，再 × 特征核倍率
        日照 = 天文日照时长(daylight_hours) + 场扰动
        降雨强度 = 场降水信号 + 降水越阈判定（weather.instant.
        compose_precipitation_intensity.v1 注册表方程）

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
        bl = field.baseline
        m = _mechanisms
        instance = (field.chunk_x, field.chunk_y)
        # 季节/昼夜/天文偏移 — 全部经注册表方程求值（唯一事实源，
        # 干预执行器按 (节点, chunk, tick) 覆盖）
        season_temp = self.evaluate_node(
            m.SEASONAL_TEMPERATURE_OFFSET,
            {
                m.SEASONAL_TEMPERATURE_AMPLITUDE: bl.seasonal_amp,
                m.SEASON_PHASE_COS: season_cos,
            },
            frame=now, instance=instance,
        )
        diurnal_temp = self.evaluate_node(
            m.DIURNAL_TEMPERATURE_OFFSET,
            {
                m.DIURNAL_TEMPERATURE_AMPLITUDE: bl.diurnal_amp,
                m.DIURNAL_PHASE_COS: diurnal_cos,
            },
            frame=now, instance=instance,
        )
        season_hum = self.evaluate_node(
            m.SEASONAL_HUMIDITY_OFFSET,
            {
                m.SEASONAL_HUMIDITY_AMPLITUDE: bl.humidity_seasonal_amp,
                m.SEASON_PHASE_COS: season_cos,
                m.HUMIDITY_SHARPNESS: bl.humidity_sharpness,
            },
            frame=now, instance=instance,
        )
        diurnal_hum = self.evaluate_node(
            m.DIURNAL_HUMIDITY_OFFSET,
            {
                m.DIURNAL_HUMIDITY_AMPLITUDE: bl.humidity_diurnal_amp,
                m.DIURNAL_PHASE_COS: diurnal_cos,
            },
            frame=now, instance=instance,
        )
        sr = self.evaluate_node(
            m.SUNRISE_HOUR,
            {m.SOLAR_LATITUDE_PROXY: bl.latitude,
             m.SOLAR_DECLINATION: ctx["solar_decl"]},
            frame=now, instance=instance,
        )
        ss = self.evaluate_node(
            m.SUNSET_HOUR,
            {m.SOLAR_LATITUDE_PROXY: bl.latitude,
             m.SOLAR_DECLINATION: ctx["solar_decl"]},
            frame=now, instance=instance,
        )
        # 统一天气场采样 — chunk 中心（场为解析量，任意过去时刻精确）
        wx = (field.chunk_x + 0.5) * TILE_MAP_SIZE
        wy = (field.chunk_y + 0.5) * TILE_MAP_SIZE
        # 同点多通道共享一次核收集与漂移偏移（性能语义，解析值不变）
        cores = self._field.collect_cores(wx, wy, now)
        drift = self._field.texture.drift_offset(now)
        temp_perturb = self._field.sample(CH_TEMPERATURE, wx, wy, now, cores, drift)
        hum_perturb = self._field.sample(CH_HUMIDITY, wx, wy, now, cores, drift)
        wind_perturb = self._field.sample(CH_WIND, wx, wy, now, cores, drift)
        # 即时合成（注册表方程 + 声明参数；干预执行器按 (节点, chunk, tick) 覆盖）
        temperature = self.evaluate_node(
            m.INSTANT_TEMPERATURE,
            {
                m.ANNUAL_TEMPERATURE: bl.temperature,
                m.SEASONAL_TEMPERATURE_OFFSET: season_temp,
                m.DIURNAL_TEMPERATURE_OFFSET: diurnal_temp,
                m.FIELD_TEMPERATURE_PERTURBATION: temp_perturb,
            },
            frame=now, instance=instance,
        )
        humidity = self.evaluate_node(
            m.INSTANT_HUMIDITY,
            {
                m.BASELINE_HUMIDITY: bl.humidity,
                m.SEASONAL_HUMIDITY_OFFSET: season_hum,
                m.DIURNAL_HUMIDITY_OFFSET: diurnal_hum,
                m.FIELD_HUMIDITY_PERTURBATION: hum_perturb,
            },
            frame=now, instance=instance,
        )
        wind_speed = self.evaluate_node(
            m.INSTANT_WIND_SPEED,
            {
                m.BASELINE_WIND_SPEED: bl.wind_speed,
                m.FIELD_WIND_PERTURBATION: wind_perturb,
                m.FIELD_WIND_MULTIPLIER: self._field.wind_multiplier(
                    wx, wy, now, cores, drift),
            },
            frame=now, instance=instance,
        )
        threshold = self.evaluate_node(
            m.PRECIPITATION_THRESHOLD,
            {m.ANNUAL_RAINFALL: bl.rainfall},
            frame=now, instance=instance,
        )
        intensity = self.evaluate_node(
            m.INSTANT_PRECIPITATION_INTENSITY,
            {
                m.FIELD_PRECIPITATION_SIGNAL: self._field.precip_signal(
                    wx, wy, now, cores, drift),
                m.PRECIPITATION_THRESHOLD: threshold,
                m.MEAN_PRECIP_INTENSITY: bl.mean_intensity,
            },
            frame=now, instance=instance,
        )
        daylight = self.evaluate_node(
            m.DAYLIGHT_HOURS,
            {m.SUNRISE_HOUR: sr, m.SUNSET_HOUR: ss},
            frame=now, instance=instance,
        )
        sunshine = self.evaluate_node(
            m.INSTANT_SUNSHINE,
            {
                m.DAYLIGHT_HOURS: daylight,
                m.FIELD_HUMIDITY_PERTURBATION: hum_perturb,
            },
            frame=now, instance=instance,
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

        干预执行器接线：强制控制先登记 field_feature 干预（目标/实例/
        生效帧校验 + 历史），再执行特征核注入/移除（运行时状态桥接，
        随 W_t 序列化，见 ``persist_state``）。注入核与自然核同代码路径——
        查询与事件都走场合成，无特判。
        {type}_start/stop 事件由下一次 minute_change 的核身份
        差异跟踪自动发布。

        **单一事实源 = 注入核**：no-op 判定与解除都只看核是否存在
        （``get_injected``），记录仅作校验/历史；强制核 duration=None
        与记录的"长期"语义一致，核不会先于记录过期。

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
            _, table = self._intervention()
            # 单一事实源 = 注入核本身（记录只做校验/历史/回溯），
            # 因此核自然过期后 stop 仍可解除，do clear 后核也不会成为孤儿。
            core = features.get_injected(cx, cy, type_name)
            if active:
                if core is not None and core.is_active(now):
                    return False
                table.commit(InterventionRecord(
                    target_space="field_feature",
                    target=type_name,
                    instance=(cx, cy),
                    rep="value",
                    value={"active": True},
                    frame_t0=now,
                    duration=None,
                ))
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
                    duration=None,   # 与记录同语义：强制控制长期有效
                    vel_x=vel_x, vel_y=vel_y,
                )
            else:
                if core is None:
                    return False
                features.remove_injected(cx, cy, type_name)
                table.clear("field_feature", type_name, (cx, cy))
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
        cores = self._field.features.segment_snapshot(bx, by, seg)
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
            ), fate_path=f"weather/precip/{cx}/{cy}@{now}")
        else:
            self._publish(cx, cy, now, PrecipitationStop(
                time_of_day=tod,
                chunks=region.chunks,
            ), fate_path=f"weather/precip/{cx}/{cy}@{now}")

    def _publish(
        self, cx: int, cy: int, now: int,
        ev: WorldEvent,
        *,
        fate_path: str | None = None,
    ) -> None:
        """发布天气事件。

        Args:
            cx, cy: 事件所在 chunk 坐标。
            now: 世界时间（tick）。
            ev: 事件 data 契约。
            fate_path: 随机性来源的 Loom of Fate 流身份（None = 事件
                不直接消费随机流）。供研究溯源——天气事件的值域经
                场派生链（seed + 坐标 + 时间）可完全回溯。
        """
        self._wt.publish(Event(
            timestamp=now,
            location=(cx, cy, None, None),
            initiator_type="system",
            initiator_id="weather_engine",
            affected=[AffectedParty("world", "subject")],
            event_type=ev.event_type,
            data=ev.as_dict(),
            fate_path=fate_path,
        ))
