"""昼夜链定点/冻表对拍：定点/冻表实现 ↔ 旧 float 实现。

对拍判据（声明偏差，见 ascend/world/kernel/diurnal.py）：

- 小时：≤ 0.5 ulp；
- 相位：≤ PHASE_MAX_ERROR；
- 温度偏移：≤ OFFSET_MAX_ERROR。

全链在整日范围与多组振幅上采样，登记实测最大偏差；任何超界即红。
"""

from __future__ import annotations

import math
import random

from ascend.config import DIURNAL_PEAK_HOUR, GAME_DAY, GAME_HOUR
from ascend.world.kernel import diurnal
from ascend.world.kernel.fixed import quantize, to_float
from ascend.world.kernel.frozen_tables import TABLE_BITS


def _old_hour(tick: int) -> float:
    return (tick % GAME_DAY) / GAME_HOUR


def _old_phase(hour: float) -> float:
    return math.cos((hour - DIURNAL_PEAK_HOUR) / 24 * 2 * math.pi)


def _old_offset(amplitude: float, phase: float) -> float:
    return amplitude * phase


def test_vertical_slice_parity_and_bounds():
    """全链对拍：三阶段实测偏差 ≤ 声明界（登记实测最大值）。"""
    peak_q = quantize(float(DIURNAL_PEAK_HOUR), TABLE_BITS)
    rng = random.Random(20260917)

    worst_hour = worst_phase = worst_offset = 0.0
    # 覆盖：整日步进（含跨日、负 tick）+ 随机多日 + 振幅网格
    ticks = [t for t in range(0, GAME_DAY + GAME_HOUR, max(1, GAME_HOUR))]
    ticks += [rng.randint(-3 * GAME_DAY, 5 * GAME_DAY) for _ in range(600)]
    amplitudes = [0.0, 1.0, 5.0, 12.5, 30.0]

    for tick in ticks:
        hour_old = _old_hour(tick)
        hour_q = diurnal.hour_of_day_q(tick, GAME_DAY, GAME_HOUR)
        hour_new = to_float(hour_q, TABLE_BITS)
        worst_hour = max(worst_hour, abs(hour_new - hour_old))

        phase_old = _old_phase(hour_old)
        phase_q = diurnal.diurnal_phase_cos_q(hour_q, peak_q)
        phase_new = to_float(phase_q, TABLE_BITS)
        worst_phase = max(worst_phase, abs(phase_new - phase_old))

        for amplitude in amplitudes:
            amp_q = quantize(amplitude, TABLE_BITS)
            offset_q = diurnal.diurnal_temperature_offset_q(amp_q, phase_q)
            offset_new = to_float(offset_q, TABLE_BITS)
            offset_old = _old_offset(amplitude, phase_old)
            worst_offset = max(
                worst_offset, abs(offset_new - offset_old),
            )

    assert worst_hour <= diurnal.HOUR_MAX_ERROR, (
        f"hour 偏差 {worst_hour:.3g} 超出 {diurnal.HOUR_MAX_ERROR:.3g}"
    )
    assert worst_phase <= diurnal.PHASE_MAX_ERROR, (
        f"phase 偏差 {worst_phase:.3g} 超出 {diurnal.PHASE_MAX_ERROR:.3g}"
    )
    assert worst_offset <= diurnal.OFFSET_MAX_ERROR, (
        f"offset 偏差 {worst_offset:.3g} 超出 {diurnal.OFFSET_MAX_ERROR:.3g}"
    )


def test_determinism_and_no_libm_in_query_path(monkeypatch):
    """查询路径零浮点：把 math.cos 替换为抛错桩，结果不变。"""
    def _boom(*_args, **_kwargs):
        raise AssertionError("定点查询路径不得调用 math.cos")

    monkeypatch.setattr(math, "cos", _boom)
    peak_q = quantize(float(DIURNAL_PEAK_HOUR), TABLE_BITS)
    hour_q = diurnal.hour_of_day_q(12345, GAME_DAY, GAME_HOUR)
    phase_q = diurnal.diurnal_phase_cos_q(hour_q, peak_q)
    amp_q = quantize(8.0, TABLE_BITS)
    value = diurnal.diurnal_temperature_offset_q(amp_q, phase_q)
    assert value == diurnal.diurnal_temperature_offset_q(amp_q, phase_q)
    assert abs(to_float(value, TABLE_BITS)) <= 8.0 + 1e-6


def test_hour_matches_quantized_float_exactly():
    """小时阶段：整数日历除法的定点值 == 旧值量化（半偶同规则）。"""
    for tick in range(0, 2 * GAME_DAY + GAME_HOUR, 3):
        expected = quantize(_old_hour(tick), TABLE_BITS)
        assert diurnal.hour_of_day_q(tick, GAME_DAY, GAME_HOUR) == expected


def test_invalid_calendar_rejected():
    import pytest

    with pytest.raises(ValueError, match="日历常量"):
        diurnal.hour_of_day_q(1, 0, GAME_HOUR)
    with pytest.raises(ValueError, match="日历常量"):
        diurnal.hour_of_day_q(1, GAME_DAY, -1)
