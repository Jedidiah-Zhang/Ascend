"""昼夜链垂直切片：昼夜链的定点/冻表实现（并行参考，尚未接生产）。

链：``tick → hour_of_day → diurnal_phase_cos → diurnal_temperature_offset``
对应生产机制：

- ``weather.tick.derive_hour_of_day.v1``：``(tick % game_day) / game_hour``
- ``weather.tick.derive_diurnal_phase_cos.v1``：
  ``cos((hour − peak_hour) / 24 · 2π)``
- ``weather.offset.derive_diurnal_temperature.v1``：``amplitude · phase``

本模块是**迁移先导**：全整数实现（定点 + 冻表），与旧 float 实现的对拍
由 ``tests/unit/test_num_diurnal.py`` 承担，偏差上界在下方声明。生产切换
（换入 ``weather/mechanisms.py``）是独立步骤：会改变世界身份，需重生成
equations.json / Lean / impl_digests —— 对拍通过后再执行。

声明偏差（各阶段，供对拍判据）：

- 小时：半偶舍入 ≤ 0.5 ulp = 2^-(bits+1)；
- 相位：冻表误差 ``tables.DECLARED_EPSILON`` + 角度量化 ≤ 3e-6；
- 温度偏移：``|amplitude|·相位误差 + 0.5 ulp``；amplitude ≤ 30 时 ≤ 1e-4。
"""

from __future__ import annotations

from .fixed import mul, round_half_even_div
from .frozen_tables import TABLE_BITS
from .tables import DECLARED_EPSILON, cos_q

__all__ = [
    "HOUR_MAX_ERROR",
    "PHASE_MAX_ERROR",
    "OFFSET_MAX_ERROR",
    "diurnal_phase_cos_q",
    "diurnal_temperature_offset_q",
    "hour_of_day_q",
]

# 与冻表同精度（多精度按 #53 顺序扩展）
HOUR_MAX_ERROR: float = 2.0 ** -(TABLE_BITS + 1)
# 相位：表误差 1e-5 + 角度量化 ≈2.5e-7 + 舍入；取 2e-5（含裕度）
PHASE_MAX_ERROR: float = 2e-5
# 温度偏移：|amplitude| ≤ 30 时 30·2e-5 + 0.5 ulp ≈ 6.1e-4；取 1e-3
OFFSET_MAX_ERROR: float = 1e-3


def hour_of_day_q(
    tick: int, game_day: int, game_hour: int,
    bits: int = TABLE_BITS,
) -> int:
    """``(tick % game_day) / game_hour`` 的 Q(bits) 表示（半偶舍入）。"""
    if game_day <= 0 or game_hour <= 0:
        raise ValueError(f"日历常量必须为正: game_day={game_day}, game_hour={game_hour}")
    t = tick % game_day
    return round_half_even_div(t * (1 << bits), game_hour)


def diurnal_phase_cos_q(
    hour_q: int, peak_hour_q: int, *, bits: int = TABLE_BITS,
) -> int:
    """``cos((hour − peak_hour)/24 · 2π)`` 的 Q(bits) 值。

    ``hour_q``/``peak_hour_q`` 为 Q(bits) 小时；角度按
    ``angle_q = (Δhour_q · 2π_q) / (24 · 2^bits)`` 归一到 Q(bits) 弧度。
    """
    from .frozen_tables import TWO_PI_Q

    numerator = (hour_q - peak_hour_q) * TWO_PI_Q
    denominator = 24 << bits
    return cos_q(round_half_even_div(numerator, denominator), bits)


def diurnal_temperature_offset_q(
    amplitude_q: int, phase_q: int, bits: int = TABLE_BITS,
) -> int:
    """``amplitude · phase`` 的 Q(bits) 乘积（定点乘法，半偶舍入）。"""
    return mul(amplitude_q, phase_q, bits)
