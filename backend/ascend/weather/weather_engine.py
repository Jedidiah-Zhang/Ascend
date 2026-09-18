"""天气引擎 — 统一天气场解析算 + 感知层事件发布 + 查询 API。

天气参数每游戏分钟解析算（baseline + 季节 + 昼夜 + 统一天气场），
无快照。场为解析量（seed + 时间可完全重算）——任意过去时刻
精确可查，无调度窗口修剪。

降雨：场降水信号 + 气候带校准阈值判定（连续标定），
区域级事件由 RegionTracker 在观察者声明的域内做越阈连通域派生
（纯函数：当前帧 vs 上一分钟；与加载集无关）。
极端天气：场特征核（寒潮/热浪/风暴/锋面），核出现/消失 →
区域级 start/stop 事件（per-chunk 覆盖范围跟踪）。

事件按等级发布（整数 tier + prev_tier，边界见 config `*_TIER_BOUNDARIES`），
仅在等级跨越边界时触发，不再按固定数值阈值。

"""

import math
import threading
from dataclasses import dataclass
from typing import Mapping

from ascend.config import (GAME_DAY, GAME_HOUR, TILE_MAP_SIZE)
from ascend.causal import (
    InterventionEvaluator, InterventionTimeline,
    PlannedIntervention, TraceLog,
)
from ascend.log import get_logger
from ascend.space import (ClimateZone, WeatherParams,
                          get_climate_template)
from ascend.time import WorldClock
from ascend.world_tree import (AffectedParty, Event, WorldEvent)
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
from .features import FEATURE_TYPES
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

    由帧调度器按声明更新点驱动（每游戏分钟一次，非事件订阅）；
    register_chunk 注册 chunk 解析算基线；
    每分钟解析算各参数，感知类别变化时发对应事件，
    降水（观察者域内连通域，纯函数派生）/季节/昼夜/特征核切换发离散事件。

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
        intervention_table: InterventionTimeline | None = None,
        climate_lookup=None,
        region_domain=None,
        world_program=None,
        wave_parallel: bool = False,
        state_store=None,
    ) -> None:
        """初始化天气引擎。

        Args:
            clock: 世界时钟，用于读取当前 tick。
            seed: 统一天气场种子（纹理/特征/气候代理派生）。
            world_tree_arg: 可选的 WorldTree 实例（测试注入隔离）。
            intervention_table: 干预表（干预执行器挂载点）；None = 引擎
                惰性自建（无记录时行为与直通注册表一致）。
            climate_lookup: 区域观测的气候基线查询
                ``(cx, cy) -> (年降雨量, 基准强度)``（纯派生；None = 不接
                区域观测，未随生产装配时零事件）。
            region_domain: 区域观测的域提供者 ``() -> chunk 坐标序列``
                （观察者声明，如玩家窗口）；None = 不产区域事件。
            world_program: 世界程序（求值计划的唯一事实源）；None = 惰性取
                进程内编译缓存（测试/独立使用）。
            wave_parallel: 同波并发求值（结果与串行逐位一致；挂载研究
                trace 时自动降级串行，保证记录顺序确定）。
            state_store: 共享帧事务存储（``runtime.state_store``）；注入后
                ``advance`` 的事件发布与观察缓存经 ``stage_after_commit``
                挂入帧事务（回滚的帧不留事件、不推进缓存）；None = 立即
                生效（测试/独立使用）。
        """
        self._clock = clock
        self._seed = seed
        self._wt = world_tree_arg if world_tree_arg is not None else _default_wt
        self._intervention_table = intervention_table
        self._intervention_eval: InterventionEvaluator | None = None
        # 研究 trace（默认关闭：未挂载 = 零开销；研究通道按需开启）
        self._trace: TraceLog | None = None
        # 查询/写入互斥：handler 线程查询（get_weather 系）与游戏线程
        # 推进（advance / register / unregister）并发安全。
        # RLock：事件发布在锁内同步分发（记录/观测），防未来订阅者
        # 回调重入查询 API（当前订阅者仅转发与展示，RLock 为低成本防御）。
        self._query_lock = threading.RLock()
        self._field = UnifiedWeatherField(seed=seed)
        self._fields: dict[tuple[int, int], WeatherField] = {}
        self._tracker = RegionTracker(
            self._field,
            evaluate=self.evaluate_node,
            climate_baseline=climate_lookup,
        )
        self._region_domain = region_domain
        self._world_program = world_program
        self._wave_parallel = wave_parallel
        self._state_store = state_store
        self._last_season: int | None = None
        # 上一帧观察域（域移动语义：前后帧各用当时的域比较；帧事务内推
        # 进——回滚的帧不推进）。
        self._last_region_domain: tuple[tuple[int, int], ...] | None = None
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
    def intervention_table(self) -> InterventionTimeline:
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
            # 经 _intervention() 取表：它会按需建表（无注入表的引擎路径），
            # 直接读 self._intervention_table 会跳过建表分支，导致该引擎
            # 此后永久没有干预表（属性返回 None、commit 抛 AttributeError）。
            _, table = self._intervention()
            self._trace = TraceLog(_registry(), capacity=capacity)
            self._intervention_eval = InterventionEvaluator(
                _registry(), table, trace=self._trace,
            )
        return self._trace

    def disable_trace(self) -> None:
        """关闭研究 trace（已记录的内容保留在日志实例上）。"""
        self._trace = None
        self._intervention_eval = None

    # ── 完整世界状态（P4）：不可重算部分 ────────────────────────

    def persist_state(self) -> dict:
        """天气侧 W_t 载荷：干预时间线（计划 + 已发生记录）。

        可重算量（统一天气场、气候代理、自然核时间线、区域跟踪器、
        chunk 基线）一律不落盘——它们由 seed + 时钟 + 声明重建，
        漏存它们不会改变轨迹，多存它们则掩盖"状态充分性"的真问题。

        **注入特征核不作为状态**（#51 / WC-6.5）：它是外部输入
        （``field_feature`` 计划）的时间线投影，读档由
        :meth:`restore_state` 按生效计划重建。
        """
        return {
            "interventions": self.intervention_table.persist(),
        }

    def restore_state(self, payload, *, instance_loader=None) -> None:
        """从存档载荷恢复天气侧 W_t（fail-closed）。

        干预时间线全量重走登记校验（``InterventionTimeline.restore``）；
        注入核不来自载荷，而由**时间线投影**重建
        （:meth:`_project_injected_features`，WC-6.5）：读档时先清空
        注入核，再按当前帧生效的 ``field_feature`` 计划注入。任一条
        非法即抛 ValueError，不留下半成品状态。

        Args:
            payload: ``persist_state`` 输出的载荷。
            instance_loader: 可选实例装载器（读档时把被 LRU 淘汰的
                目标 chunk 拉回来再校验），见 ``InterventionTimeline.restore``。
        """
        if not isinstance(payload, Mapping):
            raise ValueError(
                f"天气状态载荷必须为映射: {type(payload).__name__}"
            )
        self.intervention_table.restore(
            payload.get("interventions") or {},
            instance_loader=instance_loader,
            replace=True,
        )
        self._project_injected_features()

    def _project_injected_features(self) -> int:
        """按时间线投影注入核（WC-6.5 / #51）：外部输入投影，不入状态。

        投影 = 整体替换：先校验全部生效计划，再清空注入核并逐条注入
        （校验先于改动——失败不留半成品状态）。撤销已固化为
        ``stop_frame``，失效计划天然被 ``active_plans`` 排除。

        Returns:
            注入的核数。

        Raises:
            ValueError: 计划缺核规格或特征类型未注册（fail-closed）。
        """
        now = self._clock.time
        entries = []
        for entry in self.intervention_table.active_plans(now):
            if entry.target_space != "field_feature":
                continue
            value = entry.value
            if not isinstance(value, Mapping) or not value.get("active"):
                continue
            if entry.target not in FEATURE_TYPES:
                raise ValueError(
                    f"未注册的特征类型（读档投影）: {entry.target!r}"
                )
            spec = value.get("spec")
            if not isinstance(spec, Mapping):
                raise ValueError(
                    f"特征核计划缺少核规格（读档投影）: {entry.target}"
                )
            missing = [
                name for name in (
                    "center_x", "center_y", "radius", "magnitude",
                    "born_tick", "duration", "vel_x", "vel_y",
                ) if name not in spec
            ]
            if missing:
                raise ValueError(
                    f"特征核规格缺少字段（读档投影）: {entry.target} {missing}"
                )
            entries.append((entry, spec))

        features = self._field.features
        features.clear_injected()
        for entry, spec in entries:
            features.inject_core(
                entry.instance[0], entry.instance[1], entry.target,
                center_x=float(spec["center_x"]),
                center_y=float(spec["center_y"]),
                radius=float(spec["radius"]),
                magnitude=float(spec["magnitude"]),
                born_tick=int(spec["born_tick"]),
                duration=(
                    None if spec["duration"] is None
                    else int(spec["duration"])
                ),
                vel_x=float(spec["vel_x"]),
                vel_y=float(spec["vel_y"]),
            )
        return len(entries)

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
        """注册 chunk 的天气基线（解析算基线；不改变观察者域）。

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
        logger.debug("注册 chunk (%d,%d) climate=%s", cx, cy, climate)

    def unregister_chunk(self, cx: int, cy: int) -> None:
        """注销 chunk 的天气状态（ChunkStore LRU 淘汰时由 GameEngine 调用）。

        观察者域与气候基线不受加载集影响：注销不产生区域事件。

        Args:
            cx: chunk X 坐标。
            cy: chunk Y 坐标。
        """
        key = (cx, cy)
        with self._query_lock:
            self._fields.pop(key, None)

    def shutdown(self) -> None:
        """关闭引擎（无订阅；保留接口供生命周期统一调用）。"""
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

    def _intervention(self) -> tuple[InterventionEvaluator, InterventionTimeline]:
        """干预执行器挂载点：覆盖感知求值器 + 干预表（惰性创建）。

        无干预表时引擎自建（无记录 → 行为与直通注册表一致），并注入
        时钟与实例存在性查询，使自建表与生产注入表行为一致。
        惰性初始化非原子：由游戏线程单线程驱动（server/dispatcher 同线程），
        首次调用仅在此线程发生，无需加锁。
        """
        if self._intervention_eval is None:
            if self._intervention_table is None:
                self._intervention_table = InterventionTimeline(
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
            hum_perturb: 湿度通道合成值（波次求值已采样，
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

    # ── 机制图执行（生产唯一求值路径：世界程序波次计划）─────────

    def _mechanism_program(self):
        """世界程序（注入优先；未注入时取进程内编译缓存）。"""
        if self._world_program is None:
            from ascend.causal.program import get_default_program
            self._world_program = get_default_program()
        return self._world_program

    def _boundary_values(
        self, now: int, fields: dict[tuple[int, int], WeatherField],
    ) -> "tuple[dict[tuple[str, tuple], object], dict[tuple[int, int], float]]":
        """为波次执行器准备边界输入（非机制声明的输入节点 + 时钟）。

        边界 = 声明图之外/之前的输入：chunk 基线（气候静态量）与场采样
        （扰动/倍率/信号）。同点五通道共享一次核收集与漂移偏移。全部
        实例值预计算 → 并行求值无需共享缓存；缺值即 KeyError（fail-closed）。

        Returns:
            (boundary, hum_perturb)：``{(节点, 实例): 值}`` 与各实例湿度
            通道合成值（日照云量复用，避免重复采样）。
        """
        m = _mechanisms
        boundary: dict[tuple[str, tuple], object] = {(m.CLOCK_TICK, ()): now}
        hum_perturb: dict[tuple[int, int], float] = {}
        baseline_nodes = (
            (m.ANNUAL_TEMPERATURE, "temperature"),
            (m.BASELINE_HUMIDITY, "humidity"),
            (m.BASELINE_WIND_SPEED, "wind_speed"),
            (m.ANNUAL_RAINFALL, "rainfall"),
            (m.MEAN_PRECIP_INTENSITY, "mean_intensity"),
            (m.SOLAR_LATITUDE_PROXY, "latitude"),
            (m.SEASONAL_TEMPERATURE_AMPLITUDE, "seasonal_amp"),
            (m.DIURNAL_TEMPERATURE_AMPLITUDE, "diurnal_amp"),
            (m.SEASONAL_HUMIDITY_AMPLITUDE, "humidity_seasonal_amp"),
            (m.DIURNAL_HUMIDITY_AMPLITUDE, "humidity_diurnal_amp"),
            (m.HUMIDITY_SHARPNESS, "humidity_sharpness"),
        )
        for key, field in fields.items():
            baseline = field.baseline
            for node_id, attr in baseline_nodes:
                boundary[(node_id, key)] = getattr(baseline, attr)
            wx = (key[0] + 0.5) * TILE_MAP_SIZE
            wy = (key[1] + 0.5) * TILE_MAP_SIZE
            cores = self._field.collect_cores(wx, wy, now)
            drift = self._field.texture.drift_offset(now)
            humidity = self._field.sample(CH_HUMIDITY, wx, wy, now, cores, drift)
            hum_perturb[key] = humidity
            boundary[(m.FIELD_TEMPERATURE_PERTURBATION, key)] = (
                self._field.sample(CH_TEMPERATURE, wx, wy, now, cores, drift)
            )
            boundary[(m.FIELD_HUMIDITY_PERTURBATION, key)] = humidity
            boundary[(m.FIELD_WIND_PERTURBATION, key)] = self._field.sample(
                CH_WIND, wx, wy, now, cores, drift,
            )
            boundary[(m.FIELD_WIND_MULTIPLIER, key)] = (
                self._field.wind_multiplier(wx, wy, now, cores, drift)
            )
            boundary[(m.FIELD_PRECIPITATION_SIGNAL, key)] = (
                self._field.precip_signal(wx, wy, now, cores, drift)
            )
        return boundary, hum_perturb

    def _evaluate(
        self, now: int, fields: dict[tuple[int, int], WeatherField],
    ) -> "tuple[dict[tuple[str, tuple], object], dict[tuple[int, int], float]]":
        """按世界程序波次计划求值全部 wired 节点（生产唯一执行路径）。

        查询路径与驱动路径共用：同一时刻同一输入下，结果与节点顺序无关
        （波次由微步/依赖声明导出，编译期静态校验）。``wave_parallel``
        开启时同波并发（结果逐位一致）；挂载研究 trace 时强制串行
        （记录顺序确定性优先）。

        Args:
            now: 目标时刻（tick）。
            fields: 参与求值的 chunk 快照（key → WeatherField）。

        Returns:
            (values, hum_perturb)：``{(节点, 实例): 值}`` 与湿度合成值。

        Raises:
            KeyError: 边界值/父值缺失（声明漂移，fail-closed）。
        """
        from ascend.runtime import execute_waves

        boundary, hum_perturb = self._boundary_values(now, fields)
        parallel = self._wave_parallel and self._trace is None
        if parallel:
            self._intervention()  # 预初始化挂载点（避免并发首建）
        values = execute_waves(
            self._mechanism_program(), _registry(), frame=now,
            evaluate=self.evaluate_node,
            provide=lambda node_id, instance: boundary[(node_id, instance)],
            instances=list(fields),
            parallel=parallel,
        )
        return values, hum_perturb

    def _params_from_values(
        self,
        field: WeatherField,
        values: dict[tuple[str, tuple], object],
        hum_perturb: float,
    ) -> "tuple[WeatherParams, float, float, float]":
        """从波次求值结果取出单个 chunk 的参数与天文读数。

        Returns:
            (WeatherParams, sunrise_hour, sunset_hour, hum_perturb)。
            rainfall 字段装降雨强度 mm/小时。
        """
        m = _mechanisms
        instance = (field.chunk_x, field.chunk_y)
        params = WeatherParams(
            temperature=values[(m.INSTANT_TEMPERATURE, instance)],
            rainfall=values[(m.INSTANT_PRECIPITATION_INTENSITY, instance)],
            sunshine=values[(m.INSTANT_SUNSHINE, instance)],
            altitude=field.baseline.altitude,
            humidity=values[(m.INSTANT_HUMIDITY, instance)],
            wind_speed=values[(m.INSTANT_WIND_SPEED, instance)],
        )
        return (
            params,
            values[(m.SUNRISE_HOUR, instance)],
            values[(m.SUNSET_HOUR, instance)],
            hum_perturb,
        )

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
        values, hum = self._evaluate(time, {key: field})
        params, _, _, _ = self._params_from_values(
            field, values, hum.get(key, 0.0),
        )
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
        key = (cx, cy)
        with self._query_lock:
            field = self._fields.get(key)
        if field is None:
            return None
        for k in range(samples_per_day):
            tick = t0 + k * step
            values, hum = self._evaluate(tick, {key: field})
            params, _, _, _ = self._params_from_values(
                field, values, hum.get(key, 0.0),
            )
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
        values, hum = self._evaluate(now, {key: field})
        params, sr, ss, hum_perturb = self._params_from_values(
            field, values, hum.get(key, 0.0),
        )
        wx = (cx + 0.5) * TILE_MAP_SIZE
        wy = (cy + 0.5) * TILE_MAP_SIZE
        intensity = self._sunlight_intensity(
            values[(_mechanisms.HOUR_OF_DAY, ())], sr, ss,
            params.rainfall, wx, wy, now, hum_perturb=hum_perturb)
        az = sunrise_azimuth(
            values[(_mechanisms.DAY_OF_YEAR, ())], field.baseline.latitude,
            solar_decl=values[(_mechanisms.SOLAR_DECLINATION, ())])
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

        干预接线：强制控制先登记 field_feature 计划条目（目标/实例/
        生效帧校验 + 历史），再执行特征核注入/移除（运行时状态桥接，
        随 W_t 序列化，见 ``persist_state``）。注入核与自然核同代码路径——
        查询与事件都走场合成，无特判。
        {type}_start/stop 事件由下一次更新点推进时的核身份
        差异跟踪自动发布。

        **单一事实源 = 注入核**：no-op 判定与解除都只看核是否存在
        （``get_injected``），计划条目仅作校验/历史；强制核常驻与条目的
        stop_frame=None（长期）语义一致，核不会先于条目失效。

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
                cfg = FEATURE_TYPES[type_name]
                wx = (cx + 0.5) * TILE_MAP_SIZE
                wy = (cy + 0.5) * TILE_MAP_SIZE
                # front（带形）需要移动矢量；其余核静止即可。
                # 核规格进计划值（读档投影的事实源，WC-6.5 / #51）：
                # 恢复不依赖 data/weather.json 的当前配置，避免配置漂移
                # 悄悄改写旧世界的注入核。
                spec = {
                    "center_x": wx,
                    "center_y": wy,
                    "radius": cfg.radius_range[1],
                    "magnitude": 1.0,
                    "born_tick": now,
                    "duration": None,   # 与计划同语义：强制控制长期有效
                    "vel_x": 0.5 if type_name == "front" else 0.0,
                    "vel_y": 0.3 if type_name == "front" else 0.0,
                }
                table.plan(PlannedIntervention(
                    target_space="field_feature",
                    target=type_name,
                    instance=(cx, cy),
                    value={"active": True, "spec": spec},
                    start_frame=now,
                    stop_frame=None,
                    source="feature",
                ))
                features.inject_core(
                    cx, cy, type_name,
                    center_x=spec["center_x"], center_y=spec["center_y"],
                    radius=spec["radius"], magnitude=spec["magnitude"],
                    born_tick=spec["born_tick"], duration=spec["duration"],
                    vel_x=spec["vel_x"], vel_y=spec["vel_y"],
                )
            else:
                if core is None:
                    return False
                features.remove_injected(cx, cy, type_name)
                table.revoke(
                    "field_feature", type_name, (cx, cy), at_frame=now,
                )
        logger.info(
            "强制%s特征核 %s: chunk (%d,%d)",
            "激活" if active else "解除", type_name, cx, cy,
        )
        return True

    # ── 驱动入口：声明更新点（每游戏分钟）──────────────────────

    def advance(self, now: int) -> None:
        """每游戏分钟：全局季节 + 区域降水事件 + per-chunk 参数/昼夜/特征核。

        由 FrameScheduler 按声明更新点调用（驱动层信号，非世界树订阅）。

        帧事务语义（WC-7.6）：事件发布与观察缓存（季节/等级/昼夜/核集合/
        观察域）在本帧提交成功后才生效——注入 ``state_store`` 时经
        ``stage_after_commit`` 挂入帧事务，回滚的帧不留事件、不推进缓存，
        下一帧以未推进的缓存重算（重试不丢事件）；未注入时立即生效。
        """
        tod = now % GAME_DAY
        with self._query_lock:
            fields = dict(self._fields)
        # 世界程序波次计划：全部 wired 节点一次求值（唯一执行路径；
        # wave_parallel 时同波并发，结果逐位一致）
        values, hum_perturb = self._evaluate(now, fields)

        def _commit_records() -> None:
            """帧提交成功后：按原顺序发布本帧事件并推进观察缓存。"""
            season = values[(_mechanisms.SEASON, ())]
            hour = values[(_mechanisms.HOUR_OF_DAY, ())]
            with self._query_lock:
                # 全局季节事件（location=(0,0)，不 per-chunk）
                if self._last_season is not None and season != self._last_season:
                    self._publish(0, 0, now, SeasonChange(
                        season=season, time_of_day=int(tod),
                    ))
                self._last_season = season
                # 区域降水事件（观察者域内的连通域，纯函数派生；域未声明
                # 则不产区域事件）。域移动语义：前后帧各用当时的域比较，
                # 走入既有雨带记为 start（"雨来了"）；上一帧域随帧事务推进。
                if self._region_domain is not None:
                    domain = tuple(self._region_domain())
                    previous_domain = (
                        domain if self._last_region_domain is None
                        else self._last_region_domain
                    )
                    for r in self._tracker.observe(
                        now, domain, previous_domain=previous_domain,
                    ):
                        self._publish_region_event(r, now, tod, values, fields)
                    self._last_region_domain = domain
                # per-chunk 事件
                for (cx, cy), field in fields.items():
                    params, sr, ss, _ = self._params_from_values(
                        field, values, hum_perturb.get((cx, cy), 0.0),
                    )
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
                    # per-chunk 昼夜切换（复用波次求值返回的 sr/ss）
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

        # 帧事务未注入：立即生效（测试/独立使用）；注入：提交后执行
        if self._state_store is None:
            _commit_records()
        else:
            self._state_store.stage_after_commit(_commit_records)

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
        values: dict, fields: dict,
    ) -> None:
        """区域降水事件 → precipitation_start/stop 发布。

        Args:
            region: 区域事件（质心 chunk + 强度）。
            now: 当前时刻（tick）。
            tod: 当日 tick（time_of_day 字段）。
            values: 本帧波次求值结果 ``{(节点, 实例): 值}``。
            fields: 本帧参与求值的 chunk 快照。
        """
        cx, cy = region.center_chunk
        if region.kind == "start":
            # 降水类型：质心处温度判定（质心 chunk 未注册时缺省 rain，
            # 不臆造 0°C 判雪——连通域质心几乎必为注册 chunk）
            temp = None
            if (cx, cy) in fields:
                temp = values.get((_mechanisms.INSTANT_TEMPERATURE, (cx, cy)))
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
