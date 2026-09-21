"""生产实现 ↔ 独立参考的对拍巡检（verify V4）。

对每个已声明机制：

1. **覆盖门禁**：方程字符串可求值（表达式或 ``REFERENCE_IMPLS``），
   否则报"未覆盖"（fail-closed，不允许静默跳过）；
2. **对拍**：在 C1 见证上下文 + 见证点附近的随机抖动上，比较
   ``evaluate_direct``（生产求值）与 ``reference_value``（独立
   参考）。类型必须一致，浮点按声明容差，离散输出必须相等；
3. 生产因域外拒绝的采样计为跳过（不掩盖：跳过数会报告）。

用法：
    from reference_check import check_mechanisms
    report = check_mechanisms(export_world.build_program())
    report.problems  # [] 即通过
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass, field

from olam.kernel import diurnal
from olam.runtime import evaluate_direct

from mechanism_reference import reference_value, unresolved_names

# 逐机制实现包络容差：生产若为定点/预计算表实现，与 float 规范参考的差
# 必须落在该机制声明的内核误差内（容差即声明界）
_TOLERANCES: dict[str, float] = {
    "weather.tick.derive_hour_of_day.v1": diurnal.HOUR_MAX_ERROR,
    "weather.tick.derive_diurnal_phase_cos.v1": diurnal.PHASE_MAX_ERROR,
    "weather.offset.derive_diurnal_temperature.v1": diurnal.OFFSET_MAX_ERROR,
    "weather.tick.derive_season_phase_cos.v1": diurnal.PHASE_MAX_ERROR,
    "weather.offset.derive_seasonal_temperature.v1": diurnal.OFFSET_MAX_ERROR,
    "weather.offset.derive_seasonal_humidity.v1": diurnal.OFFSET_MAX_ERROR,
    "weather.chunk.derive_precipitation_threshold.v1": 1e-6,
    "weather.instant.compose_precipitation_intensity.v1": 1e-6,
    "weather.instant.compose_temperature.v1": 1e-5,
    "weather.instant.compose_humidity.v1": 1e-5,
    "weather.instant.compose_sunshine.v1": 1e-5,
    "weather.instant.compose_wind_speed.v1": 1e-5,
    "weather.tick.derive_solar_declination.v1": 1e-5,
    "weather.chunk.derive_solar_latitude_proxy.v1": 1e-5,
    "weather.chunk.derive_seasonal_temperature_amplitude.v1": 1e-5,
    "weather.chunk.derive_diurnal_temperature_amplitude.v1": 1e-5,
    "weather.chunk.derive_seasonal_humidity_amplitude.v1": 1e-5,
    "weather.chunk.derive_diurnal_humidity_amplitude.v1": 1e-5,
    "weather.offset.derive_diurnal_humidity.v1": 1e-5,
    # 日出/日落：acos 端点误差 2e-3 rad + tan 表误差，经 degrees/15 折算；
    # 取 0.05 h（≈3 分钟游戏时间）上界；昼长为两者之差 → 0.1 h
    "weather.astronomy.derive_sunrise.v1": 0.05,
    "weather.astronomy.derive_sunset.v1": 0.05,
    "weather.astronomy.derive_daylight.v1": 0.1,
    "world.gen.derive_baseline_humidity.v1": 1e-5,
    "world.gen.derive_baseline_wind_speed.v1": 1e-5,
    "world.gen.derive_humidity_sharpness.v1": 1e-5,
    "world.gen.derive_mean_precip_intensity.v1": 1e-5,
    # 空间生成定点实现：输入量化 2⁻³¹ × 放大系数 + 乘加舍入传播。
    # sst：≤ 25×2⁻³¹ + 舍入 ≈ 1.3e-8；rainfall：≤ (1+3450/2)×2⁻³¹ ≈ 2.4e-6；
    # lapse：≤ 9e-3×范围×2⁻³¹ + 除法舍入 ≈ 3e-8。决策树为离散输出
    # （阈值邻域 2⁻³¹ 才可能翻转）。
    "world.gen.derive_sea_level_temperature.v1": 1e-7,
    "world.gen.derive_annual_rainfall.v1": 5e-6,
    "world.gen.derive_annual_mean_temperature.v1": 1e-6,
}


@dataclass
class MechanismCheckReport:
    """对拍报告。"""

    mechanisms: int = 0
    samples: int = 0
    skipped: int = 0
    expression_ids: list[str] = field(default_factory=list)
    impl_ids: list[str] = field(default_factory=list)
    uncovered: list[str] = field(default_factory=list)
    problems: list[tuple] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        """无未覆盖机制且无对拍不一致。"""
        return not self.uncovered and not self.problems


def _equal(left: object, right: object, tolerance: float = 1e-9) -> bool:
    if isinstance(left, bool) or isinstance(right, bool):
        return left is right
    if isinstance(left, float) and isinstance(right, float):
        if math.isnan(left) or math.isnan(right):
            return math.isnan(left) and math.isnan(right)
        return math.isclose(left, right, rel_tol=0.0, abs_tol=tolerance)
    return left == right


def _to_slots(mechanism, arguments: dict) -> dict:
    """argument 视图 → 槽位视图（生产求值按父槽位 ID 给出）。"""
    slot_of = {parent.argument: parent.slot for parent in mechanism.parents}
    return {
        slot_of[argument]: value
        for argument, value in arguments.items()
        if argument in slot_of
    }


def _contexts(mechanism, program, rng: random.Random, samples: int):
    """对拍上下文：每条见证的输入 + 见证点附近的随机抖动。

    返回 ``(arguments, slots)`` 对：前者按 argument 名（独立参考用），
    后者按父槽位 ID（生产求值用）。
    """
    contexts: list[tuple[dict, dict]] = []
    for witness in mechanism.witnesses:
        arguments = dict(witness.inputs)
        contexts.append((arguments, _to_slots(mechanism, arguments)))
    for _ in range(samples):
        arguments: dict = {}
        for parent in mechanism.parents:
            domain = program.slots[parent.slot].domain
            choices = tuple(domain.choices)
            if choices:
                arguments[parent.argument] = rng.choice(choices)
                continue
            lo, hi = domain.minimum, domain.maximum
            if lo is not None and hi is not None:
                if domain.kind == "int":
                    arguments[parent.argument] = rng.randint(
                        math.ceil(lo), math.floor(hi),
                    )
                elif lo == hi:
                    arguments[parent.argument] = lo
                else:
                    arguments[parent.argument] = rng.uniform(lo, hi)
                continue
            # 无界输入：以该父在见证中的取值为中心抖动
            base = 0.0
            for witness in mechanism.witnesses:
                witness_value = witness.inputs.get(parent.argument)
                if isinstance(witness_value, (int, float)):
                    base = float(witness_value)
                    break
            scale = max(1.0, abs(base) * 0.5)
            jitter = rng.uniform(-3.0 * scale, 3.0 * scale)
            if domain.kind == "int":
                arguments[parent.argument] = int(base) + int(jitter)
            else:
                arguments[parent.argument] = base + jitter
        contexts.append((arguments, _to_slots(mechanism, arguments)))
    return contexts


def check_mechanisms(program, *, samples: int = 24, seed: int = 20260917):
    """逐机制对拍；返回 :class:`MechanismCheckReport`。"""
    from mechanism_reference import REFERENCE_IMPLS

    report = MechanismCheckReport()
    rng = random.Random(seed)
    for mechanism in sorted(
        program.mechanisms.values(), key=lambda item: item.id,
    ):
        report.mechanisms += 1
        missing = unresolved_names(mechanism)
        if missing:
            report.uncovered.append(
                f"{mechanism.id}: {', '.join(missing)}"
            )
            continue
        if mechanism.id in REFERENCE_IMPLS:
            report.impl_ids.append(mechanism.id)
        else:
            report.expression_ids.append(mechanism.id)
        for arguments, slots in _contexts(mechanism, program, rng, samples):
            report.samples += 1
            try:
                production = evaluate_direct(
                    program, mechanism.id, slots, tick=0,
                )
            except ValueError:
                report.skipped += 1
                continue
            reference = reference_value(
                mechanism, arguments, program.parameters,
            )
            tolerance = _TOLERANCES.get(mechanism.id, 1e-9)
            if not _equal(production, reference, tolerance):
                report.problems.append((
                    mechanism.id, dict(arguments),
                    production, reference,
                ))
    return report


__all__ = ["MechanismCheckReport", "check_mechanisms"]
