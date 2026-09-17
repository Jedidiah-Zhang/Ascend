"""地形状态统一演化内核 — Python 参考实现（语义规范）。

与 C 加速实现（``tile_state.state_evolve_arrays`` → ``_state.c``）完全同
公式、同运算顺序：本文件是"公式在说什么"的可执行规范，C 是它的加速
等价物。两者由 ``tests/unit/test_kernels.py`` 逐位对拍（数值内核批次将
以本实现为基准做整数/定点迁移）。

统一公式（日标定，delta × dt 缩放步长）：

    delta = precip_mm × deposit[t]                        # 沉积（降水）
          + freeze[t] × max(0, freeze_below − temp)        # 冻结
          − state × ( melt[t] × max(0, temp − melt_above)  # 温度衰减
                    + drain[t] × (1 + slope) )             # 坡度排水

每步按状态写入 clamp 到 [0, state_max]，最后按 ``int(v + 0.5)`` 量化回
uint8（C 侧 ``(uint8_t)(v + 0.5)``，v 已保证非负）。

性能说明：纯 Python 逐格循环，仅用于对拍/研究/规范，不用于生产路径。
"""

from __future__ import annotations

from array import array

from .state_defs import STATE_TYPES, build_param_tables, state_keys

(
    _DEPOSIT, _DRAIN, _MELT, _FREEZE,
    _FREEZE_BELOW, _MELT_ABOVE, _STATE_MAX,
) = build_param_tables()

_KEYS = state_keys()
_N_STATES = len(_KEYS)


def state_evolve_reference(
    states: dict[str, array],
    terrain: array,
    slope: array,
    *,
    precip: list[list[float]],
    temp: list[float],
    dt: float = 1.0,
    tile_cover: list[float] | None = None,
) -> None:
    """统一演化内核的参考实现（原地更新 ``states``）。

    参数与语义与 ``tile_state.state_evolve_arrays`` 一致：

    Args:
        states: 状态 key → array（uint8）映射（按 state_keys() 顺序处理）。
        terrain: 每 tile 地形 id（array('H')；值域 0-255，索引参数表）。
        slope: 每 tile 坡度（array('f')，排水修正 (1+slope)）。
        precip: n_states × n_steps 每步降水量（mm/日，行主序）。
        temp: n_steps 每步均温 (°C)。
        dt: 步长（游戏日；每游戏小时 = 1/24）。
        tile_cover: 每 tile 沉积倍率（None = 露天 1.0）。

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
    if tile_cover is not None and len(tile_cover) != n:
        raise ValueError(
            f"tile_cover 长度须为 {n}，实际为 {len(tile_cover)}"
        )
    for s, key in enumerate(_KEYS):
        arr = states[key]
        dep_p = _DEPOSIT
        drn_p = _DRAIN
        mlt_p = _MELT
        frz_p = _FREEZE
        fb = _FREEZE_BELOW[s]
        ma = _MELT_ABOVE[s]
        hi = _STATE_MAX[s]
        row_off = s * 256
        if hi <= 0.0:  # 未启用状态
            continue
        for k in range(n_steps):
            prec = precip[s][k]
            temp_k = temp[k]
            melt_k = temp_k - ma if temp_k > ma else 0.0
            freez_k = fb - temp_k if (fb > -9000.0 and temp_k < fb) else 0.0
            decay_only = prec == 0.0 and freez_k == 0.0
            for i in range(n):
                v = float(arr[i])
                if v == 0.0 and decay_only:
                    continue
                t = terrain[i]
                base = row_off + t
                cover = tile_cover[i] if tile_cover is not None else 1.0
                dep = prec * dep_p[base] * cover
                delta = (
                    dep
                    + freez_k * frz_p[base]
                    - v * (mlt_p[base] * melt_k
                           + drn_p[base] * (1.0 + float(slope[i])))
                )
                v += delta * dt
                if v < 0.0:
                    v = 0.0
                elif v > hi:
                    v = hi
                arr[i] = int(v + 0.5)
