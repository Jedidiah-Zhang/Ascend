"""特征分量 — 统一天气场的结构化特征层。

锋面 / 风暴核 / 寒潮 / 热浪——有中心、半径、强度、移动矢量的
结构化特征核，falloff 几何上平滑。极端天气频率统计按气候带
（FeatureConfig.rates，数据驱动可扩展）。

生成：空间块（FEATURE_BLOCK_SIZE）内按段（1 游戏年）确定性派生——
核属性全部由 (块坐标, 段索引) 派生的 RNG 决定，任意 (x, y, t) 可重算，
解析量不存（存档只存 seed + 时钟）。

气候带判定：低频气候代理场（ClimateProxy，纯噪声近似），
特征频率统计在统计层面正确，个别位置偏差可接受（文档注记）。

线程安全：缓存读写经内部锁；PerlinNoise 只读。
"""

import math
import random
import threading
from dataclasses import dataclass
from typing import Mapping

from ascend.config import (
    GAME_DAY,
    GAME_HOUR,
    GAME_YEAR,
    FEATURE_BLOCK_SIZE,
    FEATURE_MAX_RADIUS,
    CLIMATE_PROXY_TEMP_WAVELENGTH,
    CLIMATE_PROXY_RAIN_WAVELENGTH,
    CLIMATE_PROXY_OCTAVES,
)
from ascend.data import load_content, split_ns_id
from ascend.log import get_logger
from ascend.space import PerlinNoise, ClimateZone, classify

from .events import (
    ColdSnapStart, ColdSnapStop, HeatWaveStart, HeatWaveStop,
    StormStart, StormStop,
)

logger = get_logger(__name__)

# 特征类型标识
T_FRONT = "front"
T_STORM = "storm"
T_COLD_SNAP = "cold_snap"
T_HEAT_WAVE = "heat_wave"

# 效果类别
EFFECT_TEMPERATURE = "temperature"  # 温度偏移（累加）
EFFECT_MULTIPLIER = "multiplier"    # 风速+降雨倍率（叠乘）
EFFECT_PRECIP = "precip"            # 降水信号提升（锋面雨带）

# 时间包络（核生命周期内强度形状）
_ENV_RAMP = 0.15  # 前后 ramp 占 duration 比例


@dataclass(slots=True)
class FeatureConfig:
    """一种特征核的静态配置。

    Attributes:
        type_name: 类型标识（"front"/"storm"/"cold_snap"/"heat_wave"）。
        effect: 效果类别 — temperature（temp_offset °C）/
            multiplier（风速+降雨倍率）/ precip（降水信号提升）。
        rates: 各气候带年均事件数 {ClimateZone: events_per_year}，0=永不发生。
        mean_duration: 典型持续时长均值（tick），实际 0.5x-1.5x 随机化。
        base_intensity: 基准强度（温度偏移 °C / 倍率 / 降水信号峰值）。
        radius_range: 影响半径范围 (min, max) m，实际 0.5x-1.5x 随机化。
        speed_range: 移动速度范围 (min, max) m/tick，实际 0.5x-1.5x 随机化。
        precip_boost: 该类型对降水信号的峰值贡献（0=不贡献降水）。
        start_event_cls / stop_event_cls: 区域事件类（字段即 data 契约）。
    """

    type_name: str
    effect: str
    rates: dict[ClimateZone, float]
    mean_duration: int
    base_intensity: float
    radius_range: tuple[float, float]
    speed_range: tuple[float, float]
    precip_boost: float = 0.0
    start_event_cls: type = None
    stop_event_cls: type = None


# ── 注册表：数据驱动（data/weather.json），新增特征类型 = 数据加一项 ──
# 数据键 = 命名空间 id（ascend:cold_snap 等）；运行时 type_name = local 部分
# （"cold_snap"，代码/事件/终端指令的字面标识）。事件类经代码侧映射。

_EVENT_CLASSES: dict[str, type] = {
    "cold_snap_start": ColdSnapStart,
    "cold_snap_stop": ColdSnapStop,
    "heat_wave_start": HeatWaveStart,
    "heat_wave_stop": HeatWaveStop,
    "storm_start": StormStart,
    "storm_stop": StormStop,
}

_ALLOWED_EFFECTS = frozenset({EFFECT_TEMPERATURE, EFFECT_MULTIPLIER, EFFECT_PRECIP})


def _parse_duration(raw: Mapping) -> int:
    """mean_duration：{"days": n} / {"hours": n} → tick。"""
    if not isinstance(raw, Mapping):
        raise ValueError(f"mean_duration 必须是对象，got {raw!r}")
    if "days" in raw:
        return int(raw["days"]) * GAME_DAY
    if "hours" in raw:
        return int(raw["hours"]) * GAME_HOUR
    raise ValueError(f"mean_duration 需含 days 或 hours，got {raw!r}")


def _parse_rates(raw: Mapping) -> dict[ClimateZone, float]:
    """rates：命名空间 id 键 → ClimateZone；未知键报错，缺省档填 0.0。"""
    if not isinstance(raw, Mapping):
        raise ValueError(f"rates 必须是对象，got {raw!r}")
    rates: dict[ClimateZone, float] = {
        ClimateZone[split_ns_id(str(k))[1].upper()]: float(v)
        for k, v in raw.items()
    }
    for zone in ClimateZone:
        rates.setdefault(zone, 0.0)  # 未声明的气候档 = 永不发生
    return rates


def _parse_event_class(raw: object, field: str) -> type | None:
    """事件类：字符串 id → 类（经 _EVENT_CLASSES）；null → None。"""
    if raw is None:
        return None
    if raw not in _EVENT_CLASSES:
        raise ValueError(f"未知事件类 {raw!r}（{field}）")
    return _EVENT_CLASSES[raw]


def _parse_feature_config(ns_id: str, raw: Mapping) -> FeatureConfig:
    """单行 JSON → FeatureConfig（type_name = local 部分，非空校验）。"""
    ns, local = split_ns_id(ns_id)
    if not local:
        raise ValueError(f"注册表键 local 部分为空: {ns_id!r}")
    effect = str(raw.get("effect", ""))
    if effect not in _ALLOWED_EFFECTS:
        raise ValueError(f"{ns_id}: 非法 effect {effect!r}")
    events = raw.get("events", {})
    return FeatureConfig(
        type_name=local,
        effect=effect,
        rates=_parse_rates(raw["rates"]),
        mean_duration=_parse_duration(raw.get("mean_duration", {"days": 1})),
        base_intensity=float(raw["base_intensity"]),
        radius_range=tuple(float(x) for x in raw["radius_range"]),
        speed_range=tuple(float(x) for x in raw["speed_range"]),
        precip_boost=float(raw.get("precip_boost", 0.0)),
        start_event_cls=_parse_event_class(
            events.get("start") if isinstance(events, Mapping) else None, "start",
        ),
        stop_event_cls=_parse_event_class(
            events.get("stop") if isinstance(events, Mapping) else None, "stop",
        ),
    )


def _build_feature_types(doc: Mapping) -> dict[str, FeatureConfig]:
    """data/weather.json → FEATURE_TYPES。校验 import 期 fail fast。"""
    raw_map = doc.get("features")
    if not isinstance(raw_map, Mapping) or not raw_map:
        raise ValueError("data/weather.json: 缺少 features 注册表")
    configs: dict[str, FeatureConfig] = {}
    for ns_id, raw in raw_map.items():
        cfg = _parse_feature_config(ns_id, raw)
        if cfg.type_name in configs:
            raise ValueError(f"特征 local 名重复（运行时 type_name 须唯一）: {cfg.type_name}")
        configs[cfg.type_name] = cfg
    return configs


FEATURE_TYPES: dict[str, FeatureConfig] = _build_feature_types(
    load_content("weather")
)


@dataclass(slots=True)
class FeatureCore:
    """一个活跃特征核（确定性派生的不可变数据）。

    Attributes:
        core_id: 稳定标识（解析核 = "b:{bx}:{by}:{seg}:{idx}"，
            注入核 = "inj:{cx}:{cy}:{type}"），事件身份跟踪用。
        type_name: 特征类型。
        born_tick: 出生 tick。
        duration: 持续 tick 数。
        center_x/center_y: 出生时中心（世界坐标 m）。
        radius: 影响半径 (m)。
        magnitude: 强度系数（0.5-1.5，相对基准强度）。
        vel_x/vel_y: 移动矢量（m/tick）。
        no_ramp: 跳过生命周期 ramp（注入核专用，调试立即满强度）。
    """

    core_id: str
    type_name: str
    born_tick: int
    duration: int
    center_x: float
    center_y: float
    radius: float
    magnitude: float
    vel_x: float
    vel_y: float
    no_ramp: bool = False

    @property
    def end_tick(self) -> int:
        return self.born_tick + self.duration

    def center_at(self, t: int) -> tuple[float, float]:
        """核中心在 t 时刻的位置（沿移动矢量线性推进）。"""
        dt = t - self.born_tick
        return (self.center_x + self.vel_x * dt,
                self.center_y + self.vel_y * dt)


def _block_of(x: float, y: float) -> tuple[int, int]:
    """世界坐标 → 空间块坐标（floor 语义，负坐标正确）。"""
    return (math.floor(x / FEATURE_BLOCK_SIZE),
            math.floor(y / FEATURE_BLOCK_SIZE))


def _block_seed(world_seed: int, bx: int, by: int) -> int:
    """块坐标 → 确定性种子。"""
    return (world_seed * 1_000_003 + bx) * 1_000_003 + by


def _segment_seed(block_seed: int, seg_idx: int) -> int:
    """块种子 + 段索引 → 独立 RNG 种子（段间去相关）。"""
    return (block_seed * 1_000_003 + seg_idx) * 1_000_003 + 0x9E3779B9


class ClimateProxy:
    """低频气候代理场 — 纯噪声近似气候档位。

    特征生成频率与降水校准需要"任意位置的气候带"，但不允许依赖
    chunk 数据（场是解析量，独立于加载状态）。用低频温度/降雨
    噪声近似判定气候档位（海拔忽略——代理无法表达构造地形，
    高山核频率偏差可接受，见 features.py 模块注记）。

    用法:
        proxy = ClimateProxy(seed=42)
        zone = proxy.zone_at(world_x, world_y)  # → ClimateZone
    """

    def __init__(
        self,
        seed: int = 0,
        *,
        temp_wavelength: float = CLIMATE_PROXY_TEMP_WAVELENGTH,
        rain_wavelength: float = CLIMATE_PROXY_RAIN_WAVELENGTH,
        octaves: int = CLIMATE_PROXY_OCTAVES,
    ) -> None:
        """初始化气候代理。

        Args:
            seed: 噪声种子。
            temp_wavelength: 温度代理波长 (m)。
            rain_wavelength: 降雨代理波长 (m)。
            octaves: 多八度层数。
        """
        self._temp = PerlinNoise(seed + 910)
        self._rain = PerlinNoise(seed + 911)
        self._temp_freq = 1.0 / temp_wavelength
        self._rain_freq = 1.0 / rain_wavelength
        self._octaves = octaves

    def zone_at(self, world_x: float, world_y: float) -> ClimateZone:
        """查询 (world_x, world_y) 的近似气候档位。

        Args:
            world_x: 世界 X 坐标 (m)。
            world_y: 世界 Y 坐标 (m)。

        Returns:
            近似 ClimateZone（无海拔判定，ALPINE 不产生）。
        """
        t_n = self._temp.octave(
            world_x * self._temp_freq, world_y * self._temp_freq,
            self._octaves,
        )
        r_n = self._rain.octave(
            world_x * self._rain_freq, world_y * self._rain_freq,
            self._octaves,
        )
        # 海平面温度 -5~35°C，年降雨 50~3500mm
        temp = -5.0 + (t_n + 1.0) * 0.5 * 40.0
        rainfall = 50.0 + (r_n + 1.0) * 0.5 * 3450.0
        return classify(temp, rainfall, 0.0)


class FeatureField:
    """特征分量 — 结构化特征核的空间场。

    采样（temperature_offset / precip_boost / multiplier）与区域事件
    （cores_overlapping）共用同一核时间线。

    线程安全：时间线缓存经内部锁；核为不可变数据，查询侧只读。

    用法:
        field = FeatureField(seed=42)
        off = field.sample_temperature_offset(x, y, t)  # °C，累加
        boost = field.sample_precip_boost(x, y, t)      # 降水信号增量
        mult = field.sample_multiplier(x, y, t)         # ≥1，叠乘
    """

    def __init__(
        self,
        seed: int = 0,
        *,
        climate_proxy: ClimateProxy | None = None,
        block_size: float = FEATURE_BLOCK_SIZE,
        max_radius: float = FEATURE_MAX_RADIUS,
        seed_override: bool = False,
    ) -> None:
        """初始化特征场。

        Args:
            seed: 世界种子。
            climate_proxy: 气候代理（None 时内部创建）。
            block_size: 空间块边长 (m)。
            max_radius: 特征核半径上限 (m)（邻块查询范围推导）。
            seed_override: 保留参数（兼容注入自定义 seed 的场景）。
        """
        self._seed = seed
        self._proxy = climate_proxy or ClimateProxy(seed=seed)
        self._block_size = block_size
        self._max_radius = max_radius
        # 块 → 段索引 → 核列表（按 born_tick 有序）
        self._timelines: dict[tuple[int, int], dict[int, list[FeatureCore]]] = {}
        # 注入核（调试 API force 用，键 = (cx, cy, type_name)）
        self._injected: dict[tuple[int, int, str], FeatureCore] = {}
        self._lock = threading.Lock()

    @property
    def max_radius(self) -> float:
        """特征核半径上限 (m)。

        邻块查询（cores_overlapping 块遍历外扩）与采样收集外扩
        （field.py 按完整 falloff 范围推算）的推导基准。
        """
        return self._max_radius

    def __repr__(self) -> str:
        return (
            f"FeatureField(seed={self._seed}, "
            f"blocks={len(self._timelines)}, "
            f"types={len(FEATURE_TYPES)})"
        )

    # ── 时间线生成 ──────────────────────────────────────────

    def _block_zone(self, bx: int, by: int) -> ClimateZone:
        """块中心的气候代理档位。"""
        cx = (bx + 0.5) * self._block_size
        cy = (by + 0.5) * self._block_size
        return self._proxy.zone_at(cx, cy)

    def _segment(self, bx: int, by: int, seg_idx: int) -> list[FeatureCore]:
        """取块段核列表（惰性生成 + 缓存，确定性）。

        段 = 1 游戏年。段内核从段起点以随机间隔派生，
        RNG 种子 = (块种子, 段索引)，段间互不依赖 → 任意段 O(1) 生成。

        Args:
            bx, by: 空间块坐标。
            seg_idx: 段索引（非负）。

        Returns:
            核列表（按 born_tick 升序）。
        """
        seg_idx = max(0, seg_idx)
        key = (bx, by)
        with self._lock:
            segs = self._timelines.get(key)
            if segs is None:
                segs = {}
                self._timelines[key] = segs
            existing = segs.get(seg_idx)
            if existing is not None:
                return existing
            # 锁内生成：并发请求同一段时串行化，避免双份（确定性幂等）
            cores = self._gen_segment(bx, by, seg_idx)
            segs[seg_idx] = cores
            return cores

    def _gen_segment(self, bx: int, by: int, seg_idx: int) -> list[FeatureCore]:
        """确定性生成一个块段内的全部特征核。

        Args:
            bx, by: 空间块坐标。
            seg_idx: 段索引。

        Returns:
            按 born_tick 升序的核列表。
        """
        zone = self._block_zone(bx, by)
        total_rate = sum(
            cfg.rates.get(zone, 0.0) for cfg in FEATURE_TYPES.values()
        )
        if total_rate <= 0:
            return []
        rng = random.Random(_segment_seed(_block_seed(self._seed, bx, by), seg_idx))
        seg_start = seg_idx * GAME_YEAR
        cores: list[FeatureCore] = []
        t = seg_start
        # 段首核延时（0~1 个平均间隔），避免跨段衔接过密
        mean_interval = GAME_YEAR / total_rate
        t += int(mean_interval * rng.random())
        while t < seg_start + GAME_YEAR:
            type_name = self._weighted_type(rng, zone)
            cfg = FEATURE_TYPES[type_name]
            duration = max(
                3600, int(cfg.mean_duration * (0.5 + rng.random())),
            )
            # 核中心：块内均匀（含半径外扩余量）
            margin = min(self._max_radius, cfg.radius_range[1])
            cx = bx * self._block_size + rng.uniform(-margin,
                                                     self._block_size + margin)
            cy = by * self._block_size + rng.uniform(-margin,
                                                     self._block_size + margin)
            radius = cfg.radius_range[0] + rng.random() * (
                cfg.radius_range[1] - cfg.radius_range[0])
            magnitude = 0.5 + rng.random()
            speed = cfg.speed_range[0] + rng.random() * (
                cfg.speed_range[1] - cfg.speed_range[0])
            angle = rng.uniform(0.0, 2.0 * math.pi)
            cores.append(FeatureCore(
                core_id="",
                type_name=type_name,
                born_tick=t,
                duration=duration,
                center_x=cx,
                center_y=cy,
                radius=radius,
                magnitude=magnitude,
                vel_x=math.cos(angle) * speed,
                vel_y=math.sin(angle) * speed,
            ))
            t += int(mean_interval * (0.5 + rng.random() * 1.0))
        # 稳定 core_id（段内序号），供事件身份跟踪（跨 tick 匹配）
        for idx, core in enumerate(cores):
            core.core_id = f"b:{bx}:{by}:{seg_idx}:{idx}"
        return cores

    @staticmethod
    def _weighted_type(rng: random.Random, zone: ClimateZone) -> str:
        """按块气候带的 rates 权重抽取特征类型。"""
        items = [
            (name, cfg.rates.get(zone, 0.0))
            for name, cfg in FEATURE_TYPES.items()
        ]
        total = sum(w for _, w in items)
        r = rng.random() * total
        for name, w in items:
            r -= w
            if r <= 0:
                return name
        return items[-1][0]

    # ── 注入核（调试 API）────────────────────────────────────

    def inject_core(
        self, cx: int, cy: int, type_name: str,
        *, center_x: float, center_y: float, radius: float,
        magnitude: float = 1.0, born_tick: int, duration: int,
        vel_x: float = 0.0, vel_y: float = 0.0,
    ) -> FeatureCore:
        """注入一个特征核（终端调试指令用，与自然核同代码路径）。

        注入核是运行时状态（非解析量，存档序列化不保存）；
        同 (cx, cy, type_name) 重复注入覆盖旧核。

        Args:
            cx, cy: 关联 chunk 坐标（身份键 + 事件 location）。
            type_name: 特征类型（FEATURE_TYPES 的键）。
            center_x, center_y: 核中心（世界坐标 m）。
            radius: 影响半径 (m)。
            magnitude: 强度系数（默认 1.0）。
            born_tick: 出生 tick。
            duration: 持续 tick 数。
            vel_x, vel_y: 移动矢量（m/tick，默认静止）。

        Returns:
            注入的 FeatureCore。

        Raises:
            ValueError: type_name 不在 FEATURE_TYPES 注册表中。
        """
        if type_name not in FEATURE_TYPES:
            raise ValueError(f"未知特征类型: {type_name}")
        core = FeatureCore(
            core_id=f"inj:{cx}:{cy}:{type_name}",
            type_name=type_name,
            born_tick=born_tick,
            duration=duration,
            center_x=center_x,
            center_y=center_y,
            radius=radius,
            magnitude=magnitude,
            vel_x=vel_x,
            vel_y=vel_y,
            no_ramp=True,
        )
        with self._lock:
            self._injected[(cx, cy, type_name)] = core
        return core

    def remove_injected(self, cx: int, cy: int, type_name: str) -> bool:
        """移除注入核（解除调试指令）。

        Args:
            cx, cy: 关联 chunk 坐标。
            type_name: 特征类型。

        Returns:
            True=已移除；False=不存在。
        """
        with self._lock:
            return self._injected.pop((cx, cy, type_name), None) is not None

    def has_injected(self, cx: int, cy: int, type_name: str) -> bool:
        """查询注入核是否存在（force_feature no-op 判定用）。

        Args:
            cx, cy: 关联 chunk 坐标。
            type_name: 特征类型。

        Returns:
            True=该注入核已存在。
        """
        with self._lock:
            return (cx, cy, type_name) in self._injected

    def get_injected(
        self, cx: int, cy: int, type_name: str,
    ) -> "FeatureCore | None":
        """取注入核（stop 事件字段派生用）。

        Args:
            cx, cy: 关联 chunk 坐标。
            type_name: 特征类型。

        Returns:
            注入核或 None（不存在）。
        """
        with self._lock:
            return self._injected.get((cx, cy, type_name))

    def _active_injected(self, t: int) -> list[FeatureCore]:
        """活跃注入核（按出生 tick 升序，供查询侧合并）。"""
        return [
            core for core in self._injected.values()
            if core.born_tick <= t < core.end_tick
        ]

    def _cores_near(
        self, x: float, y: float, t: int,
    ) -> list[FeatureCore]:
        """收集可能覆盖 (x, y) 的活跃核（3×3 邻块 + 半径裁剪）。

        跨段：核持续时间远小于 1 段（1 年），只需检查当前段与
        上一段末尾出生的核（duration ≤ 5 天，不可能跨 2 段）。
        注入核（调试 API）无条件合并。

        Args:
            x, y: 世界坐标 (m)。
            t: 时刻（tick）。

        Returns:
            与点距离 ≤ 半径的活跃核列表。
        """
        out: list[FeatureCore] = self._active_injected(t)
        bx, by = _block_of(x, y)
        seg_idx = max(0, t // GAME_YEAR)
        segs = (seg_idx,) if seg_idx == 0 else (seg_idx - 1, seg_idx)
        radius_limit = self._max_radius * 2.0
        for dbx in (-1, 0, 1):
            for dby in (-1, 0, 1):
                for seg in segs:
                    for core in self._segment(bx + dbx, by + dby, seg):
                        if not (core.born_tick <= t < core.end_tick):
                            continue
                        cx, cy = core.center_at(t)
                        if (cx - x) ** 2 + (cy - y) ** 2 <= radius_limit ** 2:
                            out.append(core)
        return out

    # ── 采样（合成值）───────────────────────────────────────

    @staticmethod
    def _gauss(d: float, radius: float) -> float:
        """高斯 falloff：d=0 → 1.0，d=radius → ~0.135，d=2r → ~0。"""
        return math.exp(-2.0 * (d / max(1.0, radius)) ** 2)

    def _envelope(self, core: FeatureCore, t: int) -> float:
        """生命周期强度包络：ramp 进出 + 中间恒定。

        Args:
            core: 特征核。
            t: 时刻。

        Returns:
            强度系数 [0, 1]。
        """
        if core.no_ramp:
            return 1.0
        elapsed = t - core.born_tick
        ramp = int(core.duration * _ENV_RAMP)
        if elapsed < ramp:
            return elapsed / max(1, ramp)
        tail = core.duration - ramp
        if elapsed > tail:
            return max(0.0, (core.duration - elapsed) / max(1, ramp))
        return 1.0

    def sample_temperature_offset(self, x: float, y: float, t: int) -> float:
        """温度偏移 (°C)：temperature 类核高斯 falloff 累加。

        Args:
            x, y: 世界坐标 (m)。
            t: 时刻（tick）。

        Returns:
            总温度偏移 (°C)，寒潮负、热浪正，可叠加抵消。
        """
        cores = self._cores_near(x, y, t)
        return self.temperature_offset_at(cores, x, y, t)

    def temperature_offset_at(
        self, cores: list[FeatureCore], x: float, y: float, t: int,
    ) -> float:
        """预收集核列表的温度偏移（网格采样复用收集结果）。

        Args:
            cores: 预收集的候选核。
            x, y: 世界坐标 (m)。
            t: 时刻（tick）。

        Returns:
            总温度偏移 (°C)。
        """
        total = 0.0
        for core in cores:
            cfg = FEATURE_TYPES[core.type_name]
            if cfg.effect != EFFECT_TEMPERATURE:
                continue
            cx, cy = core.center_at(t)
            d = math.hypot(cx - x, cy - y)
            if d > core.radius * 2:
                continue
            total += (cfg.base_intensity * core.magnitude
                      * self._gauss(d, core.radius) * self._envelope(core, t))
        return total

    def sample_multiplier(self, x: float, y: float, t: int) -> float:
        """风速+降雨倍率：multiplier 类核叠乘。

        Args:
            x, y: 世界坐标 (m)。
            t: 时刻（tick）。

        Returns:
            倍率（≥1.0，无活跃核时为 1.0）。
        """
        cores = self._cores_near(x, y, t)
        return self.multiplier_at(cores, x, y, t)

    def multiplier_at(
        self, cores: list[FeatureCore], x: float, y: float, t: int,
    ) -> float:
        """预收集核列表的倍率（网格采样复用收集结果）。

        Args:
            cores: 预收集的候选核。
            x, y: 世界坐标 (m)。
            t: 时刻（tick）。

        Returns:
            倍率（≥1.0）。
        """
        mult = 1.0
        for core in cores:
            cfg = FEATURE_TYPES[core.type_name]
            if cfg.effect != EFFECT_MULTIPLIER:
                continue
            cx, cy = core.center_at(t)
            d = math.hypot(cx - x, cy - y)
            if d > core.radius * 2:
                continue
            mult *= (1.0 + (cfg.base_intensity - 1.0) * core.magnitude
                     * self._gauss(d, core.radius) * self._envelope(core, t))
        return mult

    def sample_precip_boost(self, x: float, y: float, t: int) -> float:
        """降水信号提升：风暴核（高斯）+ 锋面（带形）贡献叠加。

        Args:
            x, y: 世界坐标 (m)。
            t: 时刻（tick）。

        Returns:
            降水信号增量（≥0）。
        """
        cores = self._cores_near(x, y, t)
        return self.precip_boost_at(cores, x, y, t)

    def precip_boost_at(
        self, cores: list[FeatureCore], x: float, y: float, t: int,
    ) -> float:
        """预收集核列表的降水提升（网格采样复用收集结果）。

        Args:
            cores: 预收集的候选核。
            x, y: 世界坐标 (m)。
            t: 时刻（tick）。

        Returns:
            降水信号增量（≥0）。
        """
        total = 0.0
        for core in cores:
            cfg = FEATURE_TYPES[core.type_name]
            if cfg.precip_boost <= 0:
                continue
            cx, cy = core.center_at(t)
            if core.type_name == T_FRONT:
                # 带形：沿移动方向延伸，垂直方向高斯 falloff
                # 点到过中心、方向为移动矢量的直线的距离
                vx, vy = core.vel_x, core.vel_y
                vlen = math.hypot(vx, vy)
                if vlen < 1e-9:
                    continue
                # 投影点到线的距离
                px, py = x - cx, y - cy
                dist = abs(px * vy - py * vx) / vlen
                if dist > core.radius * 2:
                    continue
                falloff = self._gauss(dist, core.radius * 0.5)
            else:
                d = math.hypot(cx - x, cy - y)
                if d > core.radius * 2:
                    continue
                falloff = self._gauss(d, core.radius)
            total += (cfg.precip_boost * core.magnitude
                      * falloff * self._envelope(core, t))
        return total

    # ── 区域事件查询 ─────────────────────────────────────────

    def cores_overlapping(
        self, x0: float, y0: float, x1: float, y1: float, t: int,
        margin: float = 0.0,
    ) -> list[FeatureCore]:
        """与矩形区域相交的活跃核（区域级 start/stop 事件判定用）。

        遍历区域覆盖的空间块（含外扩 margin = 最大半径）。
        相交判定按核半径（覆盖范围），falloff 强度范围不参与——
        采样侧如需完整 falloff 覆盖（2×半径）请用 margin 参数外扩矩形。

        Args:
            x0, y0, x1, y1: 区域包围盒（世界坐标 m，任意角序）。
            t: 时刻（tick）。
            margin: 矩形外扩距离（m，默认 0）。查询矩形四周外扩后
                再做相交判定——等价于调用方自行扩大矩形，但把意图
                显式化（如采样收集按完整 falloff 范围外扩）。

        Returns:
            活跃且与（外扩后）区域相交的核列表。
        """
        if margin > 0:
            x0, x1 = min(x0, x1) - margin, max(x0, x1) + margin
            y0, y1 = min(y0, y1) - margin, max(y0, y1) + margin
        else:
            x0, x1 = min(x0, x1), max(x0, x1)
            y0, y1 = min(y0, y1), max(y0, y1)
        bx0, by0 = _block_of(x0 - self._max_radius,
                             y0 - self._max_radius)
        bx1, by1 = _block_of(x1 + self._max_radius,
                             y1 + self._max_radius)
        seg_idx = max(0, t // GAME_YEAR)
        out: list[FeatureCore] = []
        for core in self._active_injected(t):
            cx, cy = core.center_at(t)
            near_x = max(x0, min(x1, cx))
            near_y = max(y0, min(y1, cy))
            if (cx - near_x) ** 2 + (cy - near_y) ** 2 <= core.radius ** 2:
                out.append(core)
        for bx in range(bx0, bx1 + 1):
            for by in range(by0, by1 + 1):
                for seg in (seg_idx, seg_idx - 1 if seg_idx > 0 else None):
                    if seg is None:
                        continue
                    for core in self._segment(bx, by, seg):
                        if not (core.born_tick <= t < core.end_tick):
                            continue
                        cx, cy = core.center_at(t)
                        # 圆与矩形相交（包围盒外扩半径做粗判定）
                        near_x = max(min(x0, x1), min(max(x0, x1), cx))
                        near_y = max(min(y0, y1), min(max(y0, y1), cy))
                        if (cx - near_x) ** 2 + (cy - near_y) ** 2 <= core.radius ** 2:
                            out.append(core)
        return out

    def start_event(self, core: FeatureCore, now: int, time_of_day: int):
        """构造特征核 start 事件（区域级，字段按事件类驱动）。

        front 类型无事件类（None）→ 返回 None，调用方跳过。
        """
        cfg = FEATURE_TYPES[core.type_name]
        cls = cfg.start_event_cls
        if cls is None:
            return None
        kwargs: dict = {"time_of_day": time_of_day}
        for f in getattr(cls, "__dataclass_fields__", {}):
            if f == "time_of_day":
                continue
            if f == "temperature_offset":
                kwargs[f] = float(
                    cfg.base_intensity * core.magnitude)
            elif f == "wind_multiplier":
                kwargs[f] = float(cfg.base_intensity * core.magnitude)
            elif f == "rain_multiplier":
                kwargs[f] = float(cfg.base_intensity * core.magnitude)
        return cls(**kwargs)

    def stop_event(self, core: FeatureCore, time_of_day: int):
        """构造特征核 stop 事件（front 无事件类 → None）。"""
        cls = FEATURE_TYPES[core.type_name].stop_event_cls
        if cls is None:
            return None
        return cls(time_of_day=time_of_day)
