"""地形状态引擎 — 统一演化内核（C）+ 声明更新点的单一积分器。

分层（数据算法分离；数据在 state_defs.py，存储在 TileGrid）：
  1. 统一演化内核：``state_evolve``（_state.c）——逐 tile 数值循环下沉 C。
  2. TileStateEngine：唯一积分路径——按声明更新点（每游戏小时）
     直接读解析天气场，对每注册 chunk 单步积分；快进/回载按
     ``chunk.integrated_through`` 游标补齐，与逐步推进逐位一致。
     事件只作记录（阈值穿越），不驱动状态（WC-4）。

本模块只写文件，不写归档 —— 持久化在 chunk_store（TileGrid
to_bytes 已含状态数组；积分游标随 chunk_tiles 落盘）。
"""

import ctypes
import threading
from array import array
from bisect import bisect_right
from dataclasses import dataclass
from pathlib import Path
from typing import ClassVar

from ascend.config import GAME_HOUR
from ascend.log import get_logger
from ascend.weather.derive import precip_type_for
from ascend.world.runtime import FrameStateStore
from ascend.world_tree import AffectedParty, Event, WorldEvent
from ascend.world_tree import world_tree as _default_wt

from ._cext import load_c_extension
from .state_defs import STATE_TYPES, build_param_tables, state_keys
from .terrain import TerrainType
from .tile_grid import TileGrid

logger = get_logger(__name__)

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
# 表是纯数据（矩阵定稿后不可变），构建一次全局复用；Python 参考实现
# （state_reference.py）与 C 包装共享同一份表（state_defs.build_param_tables）。
_DEPOSIT, _DRAIN, _MELT, _FREEZE, _FREEZE_BELOW, _MELT_ABOVE, _STATE_MAX = (
    build_param_tables()
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
    states: dict[str, array] | None = None,
) -> None:
    """统一演化内核入口（P2-2b：生产路径经新核心 ``TerrainCore``）。

    把网格状态/地形/坡度数组交给声明式地形模块的 field 机制求值，原地
    回写状态数组（与旧 C 直调同语义）。内核（参考实现 / C 加速）在模块内
    逐位对拍；``state_evolve_arrays`` 保留为旧内核直调路径（测试/内核对，
    P2-3 删除）。

    Args:
        grid: 目标 TileGrid（地形/坡度数组；状态默认取网格数组）。
        precip: 每状态每步降水量 mm/日（行=状态注册序；moisture 喂雨、
            snow 喂雪、ice 行无沉积——模块未声明该父引用）。
        temp: 每步均温 (°C)。
        dt: 步长（游戏日）——声明更新点 = 每游戏小时（1/24）。
        tile_cover: 每 tile 沉积倍率（None=露天 1.0；当前无实体层）。
        states: 可选状态数组覆盖（帧事务影子积分传入副本）。
    """
    state_arrays = (
        {name: grid.state_raw(name) for name in _KEYS}
        if states is None else states
    )
    _terrain_core().evolve_into(
        state_arrays,
        grid.raw_data(),
        grid.slope_raw(),
        precip=precip,
        temp=temp,
        dt=dt,
        cover=tile_cover,
    )


_TERRAIN_CORE = None


def _terrain_core():
    """新核心地形适配器（进程内一次编译缓存）。"""
    global _TERRAIN_CORE
    if _TERRAIN_CORE is None:
        from ascend.world.modules.terrain.core import TerrainCore
        _TERRAIN_CORE = TerrainCore()
    return _TERRAIN_CORE


def state_evolve_arrays(
    states: dict[str, array],
    terrain: array,
    slope: array,
    *,
    precip: list[list[float]],
    temp: list[float],
    dt: float = 1.0,
    tile_cover: list[float] | None = None,
) -> None:
    """统一演化内核数组层入口（_state.c 零拷贝包装，原地更新 states）。

    Args:
        states: 状态 key → array（uint8）映射（按 state_keys() 顺序取行）。
        terrain: 每 tile 地形 id（array('H')）。
        slope: 每 tile 坡度（array('f')）。
        precip/temp/dt/tile_cover: 同 ``state_evolve``。

    Raises:
        ValueError: 步数/状态数不匹配，或 cover 长度不符。
    """
    n_steps = len(temp)
    if n_steps < 1:
        return
    if len(precip) != _N_STATES or any(len(row) != n_steps for row in precip):
        raise ValueError(
            f"precip 形状须为 {_N_STATES}×{n_steps}，"
            f"实际 {len(precip)}×{len(precip[0]) if precip else 0}"
        )
    n = len(terrain)
    state_ptrs = (ctypes.POINTER(ctypes.c_uint8) * _N_STATES)()
    views: list = []
    for s, key in enumerate(_KEYS):
        raw = states[key]
        view = (ctypes.c_uint8 * len(raw)).from_buffer(raw)
        views.append(view)  # 保活（调用期间）
        state_ptrs[s] = ctypes.cast(view, ctypes.POINTER(ctypes.c_uint8))
    terrain_ptr = (ctypes.c_uint16 * n).from_buffer(terrain)
    slope_ptr = (ctypes.c_float * n).from_buffer(slope)
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
                f"tile_cover 长度须为 {n}，实际为 {len(tile_cover)}"
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
    """声明更新点（每游戏小时）的单一积分器 + 阈值记录出口。

    调用契约：
      - ``register_chunk`` 只登记；tile 就绪后 ``on_tiles_ready``
        把状态从游标补齐到当前更新点；
      - 驱动层每帧调 ``advance(now)``，跨过更新点时对注册 chunk 逐步
        积分（直接读解析天气场；不订阅事件）；
      - 快进（clock.skip）由下一帧 ``advance`` 补齐，语义与逐步一致；
      - ``aggregates(cx, cy)`` 是状态纯函数（派生缓存，删除重算一致）。
    """

    def __init__(self, clock, weather, wt=None, store=None) -> None:
        """初始化引擎。

        Args:
            clock: WorldClock（读取当前 tick）。
            weather: WeatherEngine（解析天气场采样；``get_weather``）。
            wt: 事件发布目标 WorldTree；None = 模块级单例。
            store: 帧事务状态存储（调度器共享）；None = 引擎内建
                （独立调用时自成一帧：批内影子写入一次提交）。
        """
        self._clock = clock
        self._weather = weather
        self._wt = wt if wt is not None else _default_wt
        self._store = store if store is not None else FrameStateStore()
        # (cx, cy) → ChunkData（网格经 chunk.tile_grid 读取，允许晚就绪）
        self._chunks: dict[tuple[int, int], object] = {}
        self._aggregates_cache: dict[tuple[int, int], dict] = {}
        self._lock = threading.RLock()

    # ── 注册 ──────────────────────────────────────────────

    def register_chunk(self, chunk) -> None:
        """登记 chunk（不积分——on_tiles_ready 触发，保证数组就绪）。"""
        key = (chunk.cx, chunk.cy)
        with self._lock:
            self._chunks[key] = chunk
            self._aggregates_cache.pop(key, None)

    def unregister_chunk(self, cx: int, cy: int) -> None:
        """注销 chunk（卸载/存档时）。"""
        key = (cx, cy)
        with self._lock:
            self._chunks.pop(key, None)
            self._aggregates_cache.pop(key, None)

    def unregister_all(self) -> None:
        """注销全部注册 chunk（引擎卸载时）。"""
        with self._lock:
            self._chunks.clear()
            self._aggregates_cache.clear()

    def on_tiles_ready(self, cx: int, cy: int) -> None:
        """tile 生成/恢复完成：把状态从游标补齐到当前更新点。

        无外层帧事务时自成一帧（tile 生成线程/独立调用）。
        """
        key = (cx, cy)
        with self._lock:
            chunk = self._chunks.get(key)
        if chunk is None or chunk.tile_grid is None:
            return
        target = self._target_frame(self._clock.time)
        if self._store.transaction_open:
            self._advance_chunk(key, target)
            return
        with self._store.frame():
            self._advance_chunk(key, target)

    def advance(self, now: int) -> None:
        """驱动层每帧调用：跨过更新点时对注册 chunk 积分。

        无外层帧事务（独立调用/测试）时自成一批：本批全部 chunk 的
        影子写入一次提交（WC-7.6）。
        """
        if self._store.transaction_open:
            self._advance_due(now)
            return
        with self._store.frame():
            self._advance_due(now)

    def _advance_due(self, now: int) -> None:
        """对本批到期 chunk 排队影子积分（须在帧事务内调用）。"""
        target = self._target_frame(now)
        with self._lock:
            keys = list(self._chunks)
        for key in keys:
            chunk = self._chunks.get(key)
            if chunk is None or chunk.tile_grid is None:
                continue
            if chunk.integrated_through >= target:
                continue
            self._advance_chunk(key, target)

    def shutdown(self) -> None:
        """停止引擎：注销全部 chunk。"""
        self.unregister_all()

    # ── 聚合（派生缓存）───────────────────────────────────

    def aggregates(self, cx: int, cy: int) -> dict:
        """chunk 状态聚合视图；未注册返回 {}（派生量，可重算）。"""
        key = (cx, cy)
        with self._lock:
            chunk = self._chunks.get(key)
            if chunk is None or chunk.tile_grid is None:
                return {}
            cached = self._aggregates_cache.get(key)
            if cached is not None:
                return dict(cached)
            grid = chunk.tile_grid
            data = grid.raw_data()
            moisture = grid.state_raw("moisture")
            snow = grid.state_raw("snow")
            ice = grid.state_raw("ice")
            water = int(TerrainType.WATER)
            total = len(data)
            result = {
                "water_frozen": any(
                    data[i] == water and ice[i] > 0 for i in range(total)
                ),
                "mean_snow": sum(snow) / total,
                "mean_moisture": sum(moisture) / total,
            }
            self._aggregates_cache[key] = result
            return dict(result)

    # ── 内部：单一积分路径 ────────────────────────────────

    @staticmethod
    def _target_frame(now: int) -> int:
        """当前已能积到的最后更新点（每游戏小时）。"""
        return (now // GAME_HOUR) * GAME_HOUR

    def _advance_chunk(self, key: tuple[int, int], target: int) -> None:
        """把 chunk 状态从游标逐步积分到 target（影子写入，帧边界提交）。

        天气采样在锁外完成（锁序：tile_state 锁内不请求 weather 锁）；
        任一步天气未注册则整体不推进游标（fail-closed，待注册后重试）。
        积分写入状态数组副本，提交时才整组替换（WC-7.6：帧内写影子，
        帧边界一次性发布）；阈值记录只在提交成功后发布。
        """
        cx, cy = key
        with self._lock:
            chunk = self._chunks.get(key)
            if chunk is None or chunk.tile_grid is None:
                return
            cursor = chunk.integrated_through
            if target <= cursor:
                return
        boundaries = list(range(cursor + GAME_HOUR, target + 1, GAME_HOUR))
        samples = [self._weather.get_weather(cx, cy, b) for b in boundaries]
        if any(sample is None for sample in samples):
            return
        temps = [sample.temperature for sample in samples]
        precip = [[0.0] * len(samples) for _ in range(_N_STATES)]
        for index, sample in enumerate(samples):
            rate = sample.rainfall * 24.0  # mm/h → mm/日
            row = "snow" if precip_type_for(sample.temperature) == "snow" \
                else "moisture"
            precip[_KEYS.index(row)][index] = rate
        with self._lock:
            chunk = self._chunks.get(key)
            if chunk is None or chunk.tile_grid is None:
                return
            if chunk.integrated_through != cursor:
                return
            grid = chunk.tile_grid
            live = {name: grid.state_raw(name) for name in _KEYS}
            before = self._tier_snapshot(live)
            shadow = {
                name: array(live[name].typecode, live[name]) for name in _KEYS
            }
            state_evolve(
                grid, precip=precip, temp=temps, dt=1.0 / 24.0,
                states=shadow,
            )
        applied = False

        def _apply() -> None:
            nonlocal applied
            applied = self._commit_chunk(key, grid, shadow, target)

        self._store.stage_apply(_apply)
        self._store.stage_after_commit(
            lambda: applied and self._publish_thresholds(
                cx, cy, before, shadow, target,
            ),
        )

    def _commit_chunk(
        self,
        key: tuple[int, int],
        grid: TileGrid,
        states: dict[str, array],
        target: int,
    ) -> bool:
        """帧事务应用动作：整组替换状态数组并推进游标。

        网格已被替换（恢复/重载）或游标已被其他批次推进时跳过，
        返回是否实际提交。
        """
        with self._lock:
            chunk = self._chunks.get(key)
            if chunk is None or chunk.tile_grid is not grid:
                return False
            if chunk.integrated_through >= target:
                return False
            grid.replace_states(states)
            chunk.integrated_through = target
            chunk.mark_modified()
            self._aggregates_cache.pop(key, None)
        return True

    @staticmethod
    def _tier_snapshot(states: dict[str, array]) -> dict[str, int]:
        """档位快照（仅带 thresholds 的状态；取状态数组视图）。"""
        snapshot: dict[str, int] = {}
        for key, cfg in STATE_TYPES.items():
            if not cfg.thresholds:
                continue
            snapshot[key] = bisect_right(cfg.thresholds, max(states[key]))
        return snapshot

    def _publish_thresholds(
        self,
        cx: int,
        cy: int,
        before: dict[str, int],
        after: dict[str, array],
        timestamp: int,
    ) -> None:
        """积分前后成对比较，发布净档位变化（记录，不反馈演化）。"""
        events: list[StateThresholdCrossed] = []
        for key, cfg in STATE_TYPES.items():
            if not cfg.thresholds:
                continue
            value = max(after[key])
            new_tier = bisect_right(cfg.thresholds, value)
            old_tier = before.get(key, new_tier)
            if new_tier > old_tier:
                events.append(StateThresholdCrossed(
                    state=key, value=value,
                    threshold=cfg.thresholds[new_tier - 1],
                    direction="up", cx=cx, cy=cy,
                ))
            elif new_tier < old_tier:
                events.append(StateThresholdCrossed(
                    state=key, value=value,
                    threshold=cfg.thresholds[new_tier],
                    direction="down", cx=cx, cy=cy,
                ))
        for ev in events:
            self._wt.publish(Event(
                timestamp=timestamp,
                location=(cx, cy, None, None),
                initiator_type="system",
                initiator_id="tile_state",
                affected=[AffectedParty("world", "subject")],
                event_type=ev.event_type,
                data=ev.as_dict(),
            ))
