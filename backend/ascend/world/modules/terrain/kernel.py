"""地形状态演化内核 — 参考实现（规范）与 C 加速（逐位一致）。

C 加速为同一公式的批量实现（``_state.c``）。两者由
``tests/world/test_terrain_kernel.py`` 逐位锁定；黄金向量
（``tests/world/data/terrain_golden.json``）为冻结契约数据。

**纯函数语义**：不修改入参，返回新状态（提交由调用方负责）。

统一公式（日标定，delta × dt 缩放步长）：

    delta = precip_mm × deposit[t]                        # 沉积（降水）
          + freeze[t] × max(0, freeze_below − temp)        # 冻结
          − state × ( melt[t] × max(0, temp − melt_above)  # 温度衰减
                    + drain[t] × (1 + slope) )             # 坡度排水

每步按状态 clamp 到 [0, state_max]，最后按 ``int(v + 0.5)`` 量化回 uint8。
"""

from __future__ import annotations

import ctypes
from pathlib import Path
from typing import Mapping, Sequence

from ._cext import load_c_extension
from .data import build_param_tables, state_keys

__all__ = [
    "PARAM_TABLES",
    "STATE_KEYS",
    "evolve_accelerated",
    "evolve_reference",
]

STATE_KEYS: tuple[str, ...] = state_keys()
PARAM_TABLES = build_param_tables()
(
    _DEPOSIT, _DRAIN, _MELT, _FREEZE,
    _FREEZE_BELOW, _MELT_ABOVE, _STATE_MAX,
) = PARAM_TABLES
_N_STATES = len(STATE_KEYS)

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


def _c_arr(values: Sequence[float]) -> "ctypes.Array":
    return (ctypes.c_double * len(values))(*values)


_DEPOSIT_PTR = _c_arr(_DEPOSIT)
_DRAIN_PTR = _c_arr(_DRAIN)
_MELT_PTR = _c_arr(_MELT)
_FREEZE_PTR = _c_arr(_FREEZE)
_FREEZE_BELOW_PTR = _c_arr(_FREEZE_BELOW)
_MELT_ABOVE_PTR = _c_arr(_MELT_ABOVE)
_STATE_MAX_PTR = _c_arr(_STATE_MAX)


def _validate(
    states: Mapping[str, Sequence[int]],
    terrain: Sequence[int],
    slope: Sequence[float],
    precip: Sequence[Sequence[float]],
    temp: Sequence[float],
    cover: Sequence[float] | None,
) -> int:
    n_steps = len(temp)
    if len(precip) != _N_STATES or any(
        len(row) != n_steps for row in precip
    ):
        raise ValueError(
            f"precip 形状须为 {_N_STATES}×{n_steps}"
        )
    n = len(terrain)
    if len(slope) != n:
        raise ValueError(f"slope 长度须为 {n}")
    for key in STATE_KEYS:
        if len(states[key]) != n:
            raise ValueError(f"状态 {key} 长度须为 {n}")
    if cover is not None and len(cover) != n:
        raise ValueError(f"cover 长度须为 {n}")
    return n


def evolve_reference(
    states: Mapping[str, Sequence[int]],
    terrain: Sequence[int],
    slope: Sequence[float],
    *,
    precip: Sequence[Sequence[float]],
    temp: Sequence[float],
    dt: float = 1.0,
    cover: Sequence[float] | None = None,
) -> dict[str, list[int]]:
    """参考实现：返回新状态（不修改入参）。"""
    n_steps = len(temp)
    if n_steps < 1:
        return {key: list(states[key]) for key in STATE_KEYS}
    n = _validate(states, terrain, slope, precip, temp, cover)
    result = {key: list(states[key]) for key in STATE_KEYS}
    for index, key in enumerate(STATE_KEYS):
        arr = result[key]
        freeze_below = _FREEZE_BELOW[index]
        melt_above = _MELT_ABOVE[index]
        limit = _STATE_MAX[index]
        row_offset = index * 256
        if limit <= 0.0:
            continue
        for step in range(n_steps):
            prec = precip[index][step]
            temp_k = temp[step]
            melt_k = temp_k - melt_above if temp_k > melt_above else 0.0
            freez_k = (
                freeze_below - temp_k
                if (freeze_below > -9000.0 and temp_k < freeze_below)
                else 0.0
            )
            decay_only = prec == 0.0 and freez_k == 0.0
            for tile in range(n):
                value = float(arr[tile])
                if value == 0.0 and decay_only:
                    continue
                terrain_id = terrain[tile]
                base = row_offset + terrain_id
                factor = cover[tile] if cover is not None else 1.0
                deposit = prec * _DEPOSIT[base] * factor
                delta = (
                    deposit
                    + freez_k * _FREEZE[base]
                    - value * (
                        _MELT[base] * melt_k
                        + _DRAIN[base] * (1.0 + float(slope[tile]))
                    )
                )
                value += delta * dt
                if value < 0.0:
                    value = 0.0
                elif value > limit:
                    value = limit
                arr[tile] = int(value + 0.5)
    return result


def evolve_accelerated_into(
    states: Mapping[str, object],
    terrain: object,
    slope: object,
    *,
    precip: Sequence[Sequence[float]],
    temp: Sequence[float],
    dt: float = 1.0,
    cover: object | None = None,
) -> None:
    """C 加速**原地**路径：零拷贝映射调用方数组（生产更新点直调）。

    与 :func:`evolve_accelerated` 同一 C 内核、同一公式；入参须为可写
    缓冲（``array('B')`` / ``array('H')`` / ``array('f')``）。语义与
    参考实现逐位一致（内核对锁定），供地形更新点在帧事务影子数组上调用。
    """
    n_steps = len(temp)
    if n_steps < 1:
        return
    if len(precip) != _N_STATES or any(
        len(row) != n_steps for row in precip
    ):
        raise ValueError(f"precip 形状须为 {_N_STATES}×{n_steps}")
    n = len(terrain)  # type: ignore[arg-type]
    pointers = (ctypes.POINTER(ctypes.c_uint8) * _N_STATES)()
    views: list[object] = []
    for index, key in enumerate(STATE_KEYS):
        raw = states[key]
        if len(raw) != n:  # type: ignore[arg-type]
            raise ValueError(f"状态 {key} 长度须为 {n}")
        view = (ctypes.c_uint8 * n).from_buffer(raw)
        views.append(view)  # 保活（调用期间）
        pointers[index] = ctypes.cast(view, ctypes.POINTER(ctypes.c_uint8))
    terrain_ptr = (ctypes.c_uint16 * n).from_buffer(terrain)
    slope_ptr = (ctypes.c_float * n).from_buffer(slope)
    flat_precip = [0.0] * (_N_STATES * n_steps)
    for index in range(_N_STATES):
        for step in range(n_steps):
            flat_precip[index * n_steps + step] = precip[index][step]
    cover_ptr = None
    if cover is not None:
        if len(cover) != n:  # type: ignore[arg-type]
            raise ValueError(f"cover 长度须为 {n}")
        cover_ptr = _c_arr(list(cover))  # type: ignore[arg-type]
    _STATE.state_evolve(
        pointers,
        terrain_ptr,
        slope_ptr,
        cover_ptr,
        n,
        _N_STATES,
        n_steps,
        _c_arr(flat_precip),
        _c_arr(list(temp)),
        ctypes.c_double(dt),
        _DEPOSIT_PTR,
        _DRAIN_PTR,
        _MELT_PTR,
        _FREEZE_PTR,
        _FREEZE_BELOW_PTR,
        _MELT_ABOVE_PTR,
        _STATE_MAX_PTR,
    )


def evolve_accelerated(
    states: Mapping[str, Sequence[int]],
    terrain: Sequence[int],
    slope: Sequence[float],
    *,
    precip: Sequence[Sequence[float]],
    temp: Sequence[float],
    dt: float = 1.0,
    cover: Sequence[float] | None = None,
) -> dict[str, list[int]]:
    """C 加速实现：与参考实现逐位一致（内核对）。"""
    n_steps = len(temp)
    if n_steps < 1:
        return {key: list(states[key]) for key in STATE_KEYS}
    n = _validate(states, terrain, slope, precip, temp, cover)
    arrays = {
        key: (ctypes.c_uint8 * n)(*states[key]) for key in STATE_KEYS
    }
    pointers = (ctypes.POINTER(ctypes.c_uint8) * _N_STATES)()
    for index, key in enumerate(STATE_KEYS):
        pointers[index] = ctypes.cast(
            arrays[key], ctypes.POINTER(ctypes.c_uint8)
        )
    terrain_ptr = (ctypes.c_uint16 * n)(*terrain)
    slope_ptr = (ctypes.c_float * n)(*[float(v) for v in slope])
    flat_precip = [0.0] * (_N_STATES * n_steps)
    for index in range(_N_STATES):
        for step in range(n_steps):
            flat_precip[index * n_steps + step] = precip[index][step]
    cover_ptr = _c_arr(list(cover)) if cover is not None else None
    _STATE.state_evolve(
        pointers,
        terrain_ptr,
        slope_ptr,
        cover_ptr,
        n,
        _N_STATES,
        n_steps,
        _c_arr(flat_precip),
        _c_arr(list(temp)),
        ctypes.c_double(dt),
        _DEPOSIT_PTR,
        _DRAIN_PTR,
        _MELT_PTR,
        _FREEZE_PTR,
        _FREEZE_BELOW_PTR,
        _MELT_ABOVE_PTR,
        _STATE_MAX_PTR,
    )
    return {key: list(arrays[key]) for key in STATE_KEYS}
