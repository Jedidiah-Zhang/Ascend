"""地形状态引擎 — 统一演化内核（C）+ 历史结算器 + 运行期状态引擎。

三层职责（数据算法分离；数据在 state_defs.py，存储在 TileGrid）：
  1. 统一演化内核：_state.c（逐 tile 数值循环下沉 C，与 _hydrology
     同款模式）。结算与运行期脉冲共用同一公式——不共用则两链路
     各自演化会漂移出不一致的终态。
  2. SettlementCalculator：批量历史推演（日档大步长，解析场固定
     时刻采样），预热与快进共用一套代码。
  3. TileStateEngine：三通道驱动（天气事件涂抹 / 数据直写 /
     时钟脉冲与结算）+ 统一对账出口（阈值事件 / 聚合 / 遮蔽缓存）。

本模块只写文件，不写归档 —— 持久化在 chunk_store（TileGrid
to_bytes 已含状态数组，存档自动继承）。
"""

import ctypes
from bisect import bisect_right
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import ClassVar

import threading
from ascend.config import GAME_DAY
from ascend.log import get_logger
from ascend.weather.weather_engine import precip_type_for
from ascend.world_tree import Event, WorldEvent, world_tree as _default_wt
from typing import Callable

from ._cext import load_c_extension
from .state_defs import STATE_TYPES, state_keys
from .terrain import TERRAIN_DEFS, TerrainType, terrain_by_id
from .tile_grid import TileGrid

# ── C 扩展加载（与 _hydrology.so / _streamlines.so 共用加载器） ──

_HERE = Path(__file__).resolve().parent
_STATE = load_c_extension(
    str(_HERE / "_state.c"), str(_HERE / "_state.so"),
)

_STATE.state_evolve.argtypes = [
    ctypes.POINTER(ctypes.POINTER(ctypes.c_uint8)),  # states
    ctypes.POINTER(ctypes.c_uint16),                 # terrain
    ctypes.POINTER(ctypes.c_float),                  # slope
    ctypes.POINTER(ctypes.c_double),                 # tile_cover (NULL=1.0)
    ctypes.c_int,                                    # n
    ctypes.c_int,                                    # n_states
    ctypes.c_int,                                    # n_steps
    ctypes.POINTER(ctypes.c_double),                 # step_precip
    ctypes.POINTER(ctypes.c_double),                 # step_temp
    ctypes.c_double,                                 # dt
    ctypes.POINTER(ctypes.c_double),                 # deposit
    ctypes.POINTER(ctypes.c_double),                 # drain
    ctypes.POINTER(ctypes.c_double),                 # melt
    ctypes.POINTER(ctypes.c_double),                 # freeze
    ctypes.POINTER(ctypes.c_double),                 # freeze_below
    ctypes.POINTER(ctypes.c_double),                 # melt_above
    ctypes.POINTER(ctypes.c_double),                 # state_max
]
_STATE.state_evolve.restype = None

# ── 参数表（注册表 → 256 宽地形索引表，模块级一次性构建） ──
# 不适用组合 = 系数全 0 → delta 恒 0 → 状态空转（C 内无需适用性分支）。
# 表是纯数据（矩阵定稿后不可变），构建一次全局复用。


def _build_param_tables() -> tuple[
    list[float], list[float], list[float], list[float],
    list[float], list[float], list[float],
]:
    """从 terrain.TERRAIN_DEFS + STATE_TYPES 构建内核参数表。

    Returns:
        (deposit, drain, melt, freeze, freeze_below, melt_above,
        state_max)；前四表为 n_states × 256（terrain id 索引），
        后三表为 n_states（状态级激活门限）。
    """
    n = len(STATE_TYPES)
    deposit = [0.0] * (n * 256)
    drain = [0.0] * (n * 256)
    melt = [0.0] * (n * 256)
    freeze = [0.0] * (n * 256)
    for si, key in enumerate(state_keys()):
        for name, defn in TERRAIN_DEFS.items():
            params = defn.states[key]
            if params is None:
                continue
            base = si * 256 + defn.value
            deposit[base] = params.deposit
            drain[base] = params.drain
            melt[base] = params.melt
            freeze[base] = params.freeze
    freeze_below = [
        cfg.freeze_below if cfg.freeze_below is not None else -99999.0
        for cfg in STATE_TYPES.values()
    ]
    melt_above = [cfg.melt_above or 0.0 for cfg in STATE_TYPES.values()]
    state_max = [float(cfg.bounds[1]) for cfg in STATE_TYPES.values()]
    return deposit, drain, melt, freeze, freeze_below, melt_above, state_max


_DEPOSIT, _DRAIN, _MELT, _FREEZE, _FREEZE_BELOW, _MELT_ABOVE, _STATE_MAX = (
    _build_param_tables()
)
_N_STATES = len(STATE_TYPES)
_KEYS = state_keys()


def _c_arr(values: list[float]) -> "ctypes.Array":
    """float 列表 → ctypes double 数组（跨调用保活：C 调用内同步使用）。"""
    return (ctypes.c_double * len(values))(*values)


_DEPOSIT_PTR = _c_arr(_DEPOSIT)
_DRAIN_PTR = _c_arr(_DRAIN)
_MELT_PTR = _c_arr(_MELT)
_FREEZE_PTR = _c_arr(_FREEZE)
_FREEZE_BELOW_PTR = _c_arr(_FREEZE_BELOW)
_MELT_ABOVE_PTR = _c_arr(_MELT_ABOVE)
_STATE_MAX_PTR = _c_arr(_STATE_MAX)


def state_evolve(
    grid: TileGrid,
    *,
    precip: list[list[float]],
    temp: list[float],
    dt: float = 1.0,
    tile_cover: list[float] | None = None,
) -> None:
    """统一演化内核入口（见 _state.c；公式注释同源）。

    零拷贝：状态/地形/坡度数组直接映射进 C，原地更新 grid。

    Args:
        grid: 目标 TileGrid（状态/地形/坡度数组）。
        precip: 每状态每步降水量 mm——行=状态注册序（state_keys()），
            列=步。moisture 行喂雨量、snow 行喂雪量、其余行 0。
        temp: 每步均温 (°C)。
        dt: 步长（游戏日）——结算 1.0，运行期脉冲 1/24。
        tile_cover: 每 tile 沉积倍率（None=露天 1.0；结算时无实体）。

    Raises:
        ValueError: 步数/状态数不匹配。
    """
    n_steps = len(temp)
    if n_steps < 1:
        return
    if len(precip) != _N_STATES or any(len(row) != n_steps for row in precip):
        raise ValueError(
            f"precip 形状须为 {_N_STATES}×{n_steps}，"
            f"实际 {len(precip)}×{len(precip[0]) if precip else 0}"
        )
    n = len(grid.raw_data())
    state_ptrs = (ctypes.POINTER(ctypes.c_uint8) * _N_STATES)()
    views: list = []
    for s, key in enumerate(_KEYS):
        raw = grid.state_raw(key)
        view = (ctypes.c_uint8 * len(raw)).from_buffer(raw)
        views.append(view)  # 保活（调用期间）
        state_ptrs[s] = ctypes.cast(view, ctypes.POINTER(ctypes.c_uint8))
    terrain_ptr = (ctypes.c_uint16 * n).from_buffer(grid.raw_data())
    slope_ptr = (ctypes.c_float * n).from_buffer(grid.slope_raw())
    flat_precip = [0.0] * (_N_STATES * n_steps)
    for s in range(_N_STATES):
        base = s * n_steps
        for k in range(n_steps):
            flat_precip[base + k] = precip[s][k]
    precip_ptr = _c_arr(flat_precip)
    temp_ptr = _c_arr(temp)
    cover_ptr = None
    if tile_cover is not None:
        if len(tile_cover) != n:
            raise ValueError(
                f"tile_cover 长度须为 {n}，实际 {len(tile_cover)}"
            )
        cover_ptr = _c_arr(tile_cover)
    _STATE.state_evolve(
        state_ptrs,
        terrain_ptr,
        slope_ptr,
        cover_ptr,
        n,
        _N_STATES,
        n_steps,
        precip_ptr,
        temp_ptr,
        ctypes.c_double(dt),
        _DEPOSIT_PTR,
        _DRAIN_PTR,
        _MELT_PTR,
        _FREEZE_PTR,
        _FREEZE_BELOW_PTR,
        _MELT_ABOVE_PTR,
        _STATE_MAX_PTR,
    )


class SettlementCalculator:
    """历史推演结算器 — 日档大步长，批量接口，纯函数（无副作用）。

    预热（chunk 生成/恢复后补历史）与快进（时间跳变补缺口）共用
    一套代码：逐日采样解析天气场（固定 4 点/日），喂统一演化内核。
    推演不产生事件、不更新聚合——那是 TileStateEngine 对账出口的
    职责（settle 后由引擎统一对账）。
    """

    def __init__(self, weather_engine) -> None:
        self._weather = weather_engine

    def precompute(
        self,
        chunk,
        from_day: int,
        to_day: int,
    ) -> list | None:
        """锁外预取日档天气快照（会请求 weather._query_lock）。

        与 settle_from_summaries 分离：调用方先在 tile_state 锁外
        预取，再在锁内做纯计算——**锁序约定：tile_state 锁内绝不
        接触 weather**（weather 持锁发布事件时会同步分发到 tile_state
        处理器请求其锁；反向请求即 ABBA 死锁窗口）。

        Args:
            chunk: ChunkData（取 cx/cy 采样天气）。
            from_day: 起始日（含）。
            to_day: 结束日（不含）。

        Returns:
            summaries 列表（未注册天气的日被跳过）；空区间/全跳过
            返回 None（锁内无需推演）。
        """
        if to_day <= from_day:
            return None
        summaries = []
        for day in range(from_day, to_day):
            summary = self._weather.get_day_summary(
                chunk.cx, chunk.cy, day,
            )
            if summary is None:
                continue  # chunk 天气未注册——该日空转
            summaries.append(summary)
        return summaries or None

    def settle_from_summaries(
        self,
        grid: TileGrid,
        summaries: list,
    ) -> int:
        """用预取快照推演（纯计算，不接触 weather——锁内调用）。

        Args:
            grid: 目标 TileGrid（原地更新）。
            summaries: precompute 的结果。

        Returns:
            实际推演天数（None 快照返回 0）。
        """
        if not summaries:
            return 0
        n_steps = len(summaries)
        precip = [[0.0] * n_steps for _ in range(_N_STATES)]
        idx = {key: i for i, key in enumerate(_KEYS)}
        temp = [0.0] * n_steps
        for i, summary in enumerate(summaries):
            temp[i] = summary.mean_temp
            precip[idx["moisture"]][i] = summary.rain_mm
            precip[idx["snow"]][i] = summary.snow_mm
        state_evolve(grid, precip=precip, temp=temp, dt=1.0)
        return n_steps


# ── 阈值叙事事件 ──────────────────────────────────────


@dataclass
class StateThresholdCrossed(WorldEvent):
    """状态跨阈（chunk 级 max 档位变化）——叙事/行为信号。

    雪阶梯 (15/30/50cm) 上下穿越时发布；上升发"达到"档、
    下降发"跌破"档。消费方：叙事事件（i18n 文案）、寻路缓存失效、
    生态/基因系统行为修正接口。

    Attributes:
        state: 状态名（STATE_TYPES 的键）。
        value: 触发时刻 chunk 内该状态 max 值。
        threshold: 穿越的档位界值（up=升到的档下界，down=跌破的档下界）。
        direction: "up" | "down"。
        cx/cy: chunk 坐标。
    """

    event_type: ClassVar[str] = "state_threshold_crossed"
    state: str
    value: int
    threshold: int
    direction: str
    cx: int
    cy: int


class TileStateEngine:
    """运行期地形状态引擎 — 三通道驱动 + 统一对账出口。

    三通道（同一演化内核，防漂移）：
      ① 天气信号：precipitation_start → 区域 chunk 即时涂抹（不等
         小时脉冲，视觉即时）；precipitation_stop 无沉积动作。
      ② 数据直写：涂抹直接写 TileGrid 状态数组（data, not events）。
      ③ 时钟脉冲：hour_change → 每注册 chunk 一次 dt=1/24 实时演化；
         day_change（skipped_days>0，快进/时间跳变）→ settle_gap
         补缺口（大步长日档，解析场采样）。

    统一对账出口：任何写入路径完成后 → 阈值穿越检测（per-chunk
    档位）→ 聚合缓存失效（aggregates 懒计算）。

    时序契约：register_chunk 只登记不结算；tile 生成/恢复完成后
    由装配方调 on_tiles_ready 触发 settle_to_now（保证数组就绪）。
    """

    def __init__(self, clock, weather_engine, calculator=None, wt=None) -> None:
        self._clock = clock
        self._weather = weather_engine
        self._calculator = calculator or SettlementCalculator(weather_engine)
        self._wt = wt if wt is not None else _default_wt
        self._chunks: dict[tuple[int, int], tuple[object, TileGrid]] = {}
        # chunk → 状态档位（有 thresholds 的状态；初始化 = 当前档）
        self._last_tier: dict[tuple[int, int], dict[str, int]] = {}
        # 聚合缓存（懒计算；任何写入后失效）
        self._aggregates: dict[tuple[int, int], dict] = {}
        # 遮蔽缓存失效登记（未来实体系统占位——覆盖度系数查询点）
        self._coverage_dirty: set[tuple[int, int]] = set()
        # 待发布阈值事件（写通道锁内收集，锁外 _flush_pending 发布）
        self._pending_publish: list = []
        # 写路径互斥：tick 线程（事件分发）与网络 handler 线程
        # （register/on_tiles_ready/LRU 淘汰注销）并发操作状态数组
        # 与 settled_day——RLock 全写路径覆盖（C 循环毫秒级可接受）
        #
        # 锁序约定（防 ABBA 死锁）：tile_state._lock 内绝不请求
        # weather._query_lock——天气快照一律锁外预取（precompute），
        # 锁内只做纯计算。weather 持锁发布事件时同步分发到本引擎
        # 处理器请求 _lock（weather → tile_state 单向，安全）。
        self._lock = threading.RLock()
        self._subscriptions: list[Callable] = [
            self._wt.subscribe("precipitation_start", self._on_precip_start),
            self._wt.subscribe("precipitation_stop", self._on_precip_stop),
            self._wt.subscribe("hour_change", self._on_hour_change),
            self._wt.subscribe("day_change", self._on_day_change),
        ]
        self._log = get_logger("tile_state")

    # ── 公开面 ─────────────────────────────────────────

    def register_chunk(self, chunk) -> None:
        """登记 chunk（不结算——on_tiles_ready 触发，保证数组就绪）。

        grid 从 chunk.tile_grid 取（tile 生成/恢复完成后才有网格；
        注册时可为 None，on_tiles_ready 时读当前值）。
        结算起点 = chunk.settled_day（0=全新，on_tiles_ready 从
        epoch=day 1 结算；恢复的 chunk 从持久化的结算日续算）。
        """
        grid = chunk.tile_grid
        key = (chunk.cx, chunk.cy)
        with self._lock:
            self._chunks[key] = (chunk, grid)
            self._aggregates.pop(key, None)
            # 初始档位从当前状态建立（避免首查误报穿越）
            self._last_tier[key] = self._tiers_of(grid) if grid is not None else {}

    def unregister_chunk(self, cx: int, cy: int) -> None:
        """注销 chunk（卸载/存档时）。"""
        key = (cx, cy)
        with self._lock:
            self._chunks.pop(key, None)
            self._last_tier.pop(key, None)
            self._aggregates.pop(key, None)
            self._coverage_dirty.discard(key)

    def unregister_all(self) -> None:
        """注销全部注册 chunk（引擎卸载时）。"""
        for key in list(self._chunks):
            self.unregister_chunk(*key)

    def shutdown(self) -> None:
        """停止引擎：取消事件订阅 + 注销全部 chunk。"""
        for cancel in self._subscriptions:
            cancel()
        self._subscriptions.clear()
        self.unregister_all()

    def on_tiles_ready(self, cx: int, cy: int) -> None:
        """tile 生成/恢复完成后调用：结算状态缺口到当前日。

        全新 chunk（settled_day=0）：从世界开端（day 1）结算——
        新世界 epoch=day 1（春），无史前历史。
        恢复的 chunk（settled_day>0）：从持久化结算日续算——
        防止把已结算历史重放一遍。
        """
        entry = self._chunks.get((cx, cy))
        if entry is None:
            return
        chunk, _ = entry
        grid = chunk.tile_grid
        if grid is None:
            return  # tile 尚未生成——等生成完成后再就绪
        # 锁序约定：天气快照锁外预取（会请求 weather._query_lock），
        # tile_state 锁内只做纯计算——防 ABBA 死锁（weather 持锁
        # 发布事件时同步分发到本引擎处理器，反向持锁即死锁窗口）
        now_day = self._now_day()
        from_day = chunk.settled_day
        if from_day < 1:
            from_day = 1  # 全新：从 epoch 结算
        summaries = self._calculator.precompute(chunk, from_day, now_day)
        with self._locked_write():
            if summaries:
                self._calculator.settle_from_summaries(grid, summaries)
            chunk.settled_day = now_day  # 无缺口（epoch 起）也标记已结算
            # 回写最新网格快照——运行期动态生成的 chunk 在注册时
            # tile_grid 为 None，此处才首次持有真实网格；不回写则
            # 小时脉冲通道永远跳过该 chunk（状态冻结）
            self._chunks[(cx, cy)] = (chunk, grid)
            self._reconcile(cx, cy)
        self._flush_pending()

    def settle_gap(self, cx: int, cy: int, to_day: int) -> None:
        """补结算缺口 [settled_day, to_day)——快进/时间跳变后调用。

        幂等：缺口为空（settled_day >= to_day）时无操作。
        """
        entry = self._chunks.get((cx, cy))
        if entry is None:
            return
        chunk, _ = entry
        grid = chunk.tile_grid
        if grid is None:
            return
        from_day = chunk.settled_day or 1
        if to_day <= from_day:
            return
        summaries = self._calculator.precompute(chunk, from_day, to_day)
        with self._locked_write():
            self._calculator.settle_from_summaries(grid, summaries)
            chunk.settled_day = to_day
            self._reconcile(cx, cy)

    def aggregates(self, cx: int, cy: int) -> dict:
        """chunk 级状态聚合（懒计算 + 缓存，任何写入后失效）。

        Returns:
            {"water_frozen": bool（水面任一 tile 结冰）,
             "mean_snow": int, "mean_moisture": int}；chunk 未注册
            返回 {}。
        """
        key = (cx, cy)
        with self._lock:
            cached = self._aggregates.get(key)
            if cached is not None:
                return dict(cached)
            entry = self._chunks.get(key)
            if entry is None:
                return {}
            _, grid = entry
            if grid is None:
                return {}
            terrain = grid.raw_data()
            ice = grid.state_raw("ice")
            water_tiles = {
                int(t) for t, p in _water_params().items()
            }
            frozen = any(
                ice[i] > 0 and terrain[i] in water_tiles
                for i in range(len(ice))
            )
            snow = grid.state_raw("snow")
            moist = grid.state_raw("moisture")
            agg = {
                "water_frozen": frozen,
                "mean_snow": sum(snow) // len(snow),
                "mean_moisture": sum(moist) // len(moist),
            }
            self._aggregates[key] = agg
            return dict(agg)

    def touch(self, cx: int, cy: int) -> None:
        """直写失效入口：聚合缓存 + 阈值档位失效，不演化、不推进结算日。

        契约：生态/基因系统若经 state_raw() 直写状态数组（绕过本引擎
        演化通道），写后必须 touch 以刷新聚合与阈值档位；本引擎自身
        写路径（脉冲/结算/沉积）已内含失效，无需 touch。
        注意：touch 不重算档位（无穿越检测）——直写方若跨越档位，
        下次任何写通道/on_tiles_ready 的对账会补发穿越事件。
        """
        key = (cx, cy)
        with self._lock:
            self._aggregates.pop(key, None)
            self._last_tier.pop(key, None)

    def invalidate_coverage(self, cx: int, cy: int) -> None:
        """实体遮蔽缓存失效入口（未来实体系统增删调用；当前占位）。"""
        self._coverage_dirty.add((cx, cy))

    # ── 三通道 ─────────────────────────────────────────

    def _on_precip_start(self, event: Event) -> None:
        """通道①：降水开始 → 区域 chunk 即时沉积涂抹（dt=1/24）。

        注意：本通道先沉积 1h 量，紧接着的小时脉冲再沉积一次——
        首小时降雨双计属设计近似（事件即时沉积保证降水"肉眼可见"）。
        """
        data = event.data
        ptype = data.get("precip_type", "rain")
        intensity = data.get("intensity", 0.0)
        if intensity <= 0:
            return
        # 锁序约定：天气快照锁外预取（get_weather 持 weather._query_lock）
        pending = []
        for cx, cy in data.get("chunks", ()):
            w = self._weather.get_weather(cx, cy, event.timestamp)
            temp = w.temperature if w is not None else 0.0
            pending.append(((cx, cy), temp))
        with self._locked_write():
            for (cx, cy), temp in pending:
                entry = self._chunks.get((cx, cy))
                if entry is None:
                    continue
                _, grid = entry
                if grid is None:
                    continue
                precip = [[0.0] for _ in range(_N_STATES)]
                idx = _KEY_INDEX["snow" if ptype == "snow" else "moisture"]
                # 事件即时沉积近似：intensity 是 mm/h，内核按 mm/日 标定
                # （settle 路径传日总量）——×24 换算保证沉积量与脉冲一致
                precip[idx][0] = intensity * 24
                state_evolve(grid, precip=precip, temp=[temp], dt=1 / 24)
                self._mark_written(cx, cy)
                self._advance_settled_day(cx, cy)

    def _on_precip_stop(self, event: Event) -> None:
        """通道①：降水停止——无沉积动作（衰减由小时脉冲推进）。"""

    def _on_hour_change(self, event: Event) -> None:
        """通道③：小时脉冲——每注册 chunk 一次 dt=1/24 实时演化。"""
        # 锁序约定：天气快照锁外预取（get_weather 持 weather._query_lock）
        pending = []
        for key, (_, grid) in list(self._chunks.items()):
            cx, cy = key
            if grid is None:
                continue
            w = self._weather.get_weather(cx, cy, event.timestamp)
            if w is None:
                continue
            pending.append((key, w))
        with self._locked_write():
            for key, w in pending:
                entry = self._chunks.get(key)
                if entry is None:
                    continue
                _, grid = entry
                if grid is None:
                    continue
                precip = [[0.0] for _ in range(_N_STATES)]
                if w.rainfall > 0:
                    ptype = precip_type_for(w.temperature)
                    idx = _KEY_INDEX["snow" if ptype == "snow" else "moisture"]
                    # rainfall 是 mm/h，内核按 mm/日 标定——×24 换算
                    # 日速率：24 步脉冲沉积 == 结算 1 天沉积
                    precip[idx][0] = w.rainfall * 24
                state_evolve(
                    grid, precip=precip, temp=[w.temperature], dt=1 / 24,
                )
                cx, cy = key
                self._mark_written(cx, cy)
                self._advance_settled_day(cx, cy)

    def _on_day_change(self, event: Event) -> None:
        """通道③：快进/时间跳变（skipped_days>0）→ 补结算缺口。

        缺口区间取自事件自身（day−skipped, day），不依赖 last_day
        跟踪——小时脉冲的实时推进与日档结算互不干扰。
        """
        skipped = event.data.get("skipped_days", 0)
        if skipped <= 0:
            return
        day = event.data["day"]
        # 缺口起点 = 上个结算日（日历 real_skipped = day − previous − 1，
        # 用 previous_day 最可靠；旧事件无该字段时退化 day − skipped）
        from_day = max(
            1, int(event.data.get("previous_day", day - skipped)),
        )
        # 锁序约定：快照锁外预取（weather._query_lock），锁内纯计算
        pending: list[tuple[object, TileGrid, list]] = []
        for cx, cy in list(self._chunks):
            entry = self._chunks.get((cx, cy))
            if entry is None:
                continue
            chunk, grid = entry
            if grid is None:
                continue  # tile 未就绪（注册后生成中）——等 on_tiles_ready
            base = chunk.settled_day or 1
            start = max(from_day, base)  # 缺口不含已结算区间
            if day <= start:
                continue
            summaries = self._calculator.precompute(chunk, start, day)
            if summaries:
                pending.append((chunk, grid, summaries))
        with self._locked_write():
            for chunk, grid, summaries in pending:
                self._calculator.settle_from_summaries(grid, summaries)
                chunk.settled_day = day
                self._reconcile(chunk.cx, chunk.cy)

    # ── 统一对账出口 ───────────────────────────────────

    def _mark_written(self, cx: int, cy: int) -> None:
        """任何写入后：聚合缓存失效 + 阈值穿越检测（合并同一批）。

        注意：settle 路径（on_tiles_ready/settle_gap/_on_day_change）
        复用本出口，但 settled_day 由各自显式管理——不得在此推进
        （settle 到过去日会被虚标成当前日）。脉冲路径演化后调用
        _advance_settled_day 单独推进。
        """
        key = (cx, cy)
        self._aggregates.pop(key, None)
        self._reconcile_thresholds(key)

    def _advance_settled_day(self, cx: int, cy: int) -> None:
        """脉冲演化后推进结算日到当前日。

        落盘语义：状态数组已是"当日态"，若 settled_day 仍记旧日，
        读档续算会重放已演化区间（双计）。锁由调用方（脉冲通道）
        持有。

        已知取舍（日粒度记账 vs 脉冲小时粒度）：同日快进（跨小时
        不跨日，skipped=0）不补结算缺口；读档后快进时若当日已部分
        脉冲演化，settle 从该日起点重放会双计已演化小时。窗口极
        小、误差量与 4 采样点近似同量级，属设计取舍——需要精确时
        将记账粒度降到小时。
        """
        entry = self._chunks.get((cx, cy))
        if entry is not None:
            chunk, _ = entry
            chunk.settled_day = max(chunk.settled_day, self._now_day())

    def _reconcile(self, cx: int, cy: int) -> None:
        """settle 后全量对账（阈值 + 聚合失效）。"""
        self._mark_written(cx, cy)

    def _reconcile_thresholds(self, key: tuple[int, int]) -> None:
        """阈值穿越检测：有 thresholds 的状态按 chunk max 档位对比。

        只计算档位变化并**收集**到待发布列表——发布由写通道在
        锁外统一 _flush_pending（事件发布含网络转发，不得在 _lock 内）。
        """
        entry = self._chunks.get(key)
        if entry is None:
            return
        _, grid = entry
        if grid is None:
            return
        tiers = self._last_tier.get(key, {})
        for state, cfg in STATE_TYPES.items():
            if not cfg.thresholds:
                continue
            mx = max(grid.state_raw(state))
            tier = bisect_right(cfg.thresholds, mx)
            last = tiers.get(state)
            if last is None:
                tiers[state] = tier
                continue
            if tier > last:
                self._pending_publish.append(self._threshold_event(
                    state, mx, cfg.thresholds[tier - 1], "up", key,
                ))
            elif tier < last:
                self._pending_publish.append(self._threshold_event(
                    state, mx, cfg.thresholds[tier], "down", key,
                ))
            tiers[state] = tier

    def _threshold_event(
        self, state: str, value: int, threshold: int,
        direction: str, key: tuple[int, int],
    ) -> Event:
        """构造阈值穿越事件（纯构造，无副作用——发布由 _flush_pending）。"""
        cx, cy = key
        now = self._clock.time
        return Event(
            timestamp=now,
            location=(cx, cy, None, None),
            initiator_type="system",
            initiator_id="tile_state",
            affected=[],
            event_type="state_threshold_crossed",
            data=StateThresholdCrossed(
                state=state, value=value, threshold=threshold,
                direction=direction, cx=cx, cy=cy,
            ).as_dict(),
        )

    def _flush_pending(self) -> None:
        """锁外发布收集的阈值事件（发布含网络 IO——必须在 _lock 外）。"""
        with self._lock:
            events = self._pending_publish
            self._pending_publish = []
        for ev in events:
            self._wt.publish(ev)

    @contextmanager
    def _locked_write(self):
        """写通道上下文：锁内写 + 块结束锁外统一发布。

        所有可能产生事件的写通道统一走本上下文——正常/异常路径
        都保证 _flush_pending（事件发布含网络转发，不得吞在 _lock 内）。
        """
        try:
            with self._lock:
                yield
        finally:
            self._flush_pending()

    # ── 内部工具 ───────────────────────────────────────

    def _now_day(self) -> int:
        return self._clock.time // GAME_DAY + 1

    @staticmethod
    def _tiers_of(grid: TileGrid) -> dict[str, int]:
        """当前状态档位快照（注册时初始化，防首查误报）。"""
        tiers: dict[str, int] = {}
        for state, cfg in STATE_TYPES.items():
            if cfg.thresholds:
                tiers[state] = bisect_right(
                    cfg.thresholds, max(grid.state_raw(state)),
                )
        return tiers


_WATER_PARAMS: dict = {
    terrain_by_id(ns_id): defn.states["ice"]
    for ns_id, defn in TERRAIN_DEFS.items()
    if defn.states["ice"] is not None
}


def _water_params() -> dict:
    """ice 适用地形集合（注册表驱动：ice 行有参的基底）。"""
    return _WATER_PARAMS


_KEY_INDEX = {key: i for i, key in enumerate(state_keys())}
