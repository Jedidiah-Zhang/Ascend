"""生产实现 ↔ 声明包络的包含对拍（V5）。

对每个机制给出**手写包络转移** ``Σ: 盒 → 包络``（与生产定点实现
同构），并在随机输入盒上验证包含保持义务：

    对盒内任意采样点 x，生产输出 ∈ Σ(盒)

当前覆盖全部 26 项机制（含合成/偏移/降水/派生/天文/模板），
由 ``testbench/unit/test_enclosure_check.py`` 逐机制检查。

生产侧 = 世界声明程序（``export_world.build_program()``）。
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field

from olam.kernel.fixed import quantize
from olam.kernel.frozen_tables import TABLE_BITS
from olam.runtime import evaluate_direct

from enclosure import Interval, tanh_enclosure



def _point(value: float) -> Interval:
    return Interval.point(quantize(value, TABLE_BITS), TABLE_BITS)


def _clamp(interval: Interval, low: Interval, high: Interval) -> Interval:
    """clamp 的包络：先与界求交；区间跨界时取最近界。"""
    lo, hi = low.lo, high.hi
    return interval.clamp(lo, hi)


def _compose_sum(
    env: dict, base_name: str, pert_name: str, scale: float,
    lower_bound: float, upper_bound: float,
) -> Interval:
    total = env[base_name].add(env[pert_name].mul(_point(scale)))
    return _clamp(total, _point(lower_bound), _point(upper_bound))


def _compose_temperature(env: dict) -> Interval:
    total = (env["annual_temperature"]
             .add(env["seasonal_offset"])
             .add(env["diurnal_offset"])
             .add(env["perturbation"].mul(_point(env["__perturb_scale"]))))
    return _clamp(total, _point(env["__lower_bound"]), _point(env["__upper_bound"]))


def _compose_humidity(env: dict) -> Interval:
    total = (env["baseline"]
             .add(env["seasonal_offset"])
             .add(env["diurnal_offset"])
             .add(env["perturbation"].mul(_point(env["__perturb_scale"]))))
    return _clamp(total, _point(env["__lower_bound"]), _point(env["__upper_bound"]))


def _compose_sunshine(env: dict) -> Interval:
    total = env["daylight_hours"].add(
        env["perturbation"].mul(_point(env["__perturb_scale"])),
    )
    return _clamp(total, _point(env["__lower_bound"]), _point(env["__upper_bound"]))


def _compose_wind(env: dict) -> Interval:
    base = _clamp(
        env["baseline"].add(
            env["perturbation"].mul(_point(env["__perturb_scale"])),
        ),
        _point(env["__lower_bound"]), _point(env["__upper_bound"]),
    )
    return _clamp(base.mul(env["multiplier"]),
                  _point(env["__lower_bound"]), _point(env["__upper_bound"]))


def _diurnal_temperature_offset(env: dict) -> Interval:
    return env["amplitude"].mul(env["diurnal_phase_cos"])


def _seasonal_temperature_offset(env: dict) -> Interval:
    return env["amplitude"].mul(env["season_phase_cos"])


def _diurnal_humidity_offset(env: dict) -> Interval:
    return env["amplitude"].mul(env["diurnal_phase_cos"]).neg()


def _seasonal_humidity_offset(env: dict) -> Interval:
    sharpness = env["sharpness"]
    if sharpness.lo > 0:
        inner = tanh_enclosure(
            env["season_phase_cos"].mul(sharpness),
        )
        return env["amplitude"].mul(inner)
    if sharpness.hi <= 0:
        return env["amplitude"].mul(env["season_phase_cos"])
    # 分支不确定：两分支凸包（保守）
    sharp_positive = env["amplitude"].mul(
        tanh_enclosure(env["season_phase_cos"].mul(
            Interval.point(max(sharpness.lo, 1), TABLE_BITS),
        )),
    )
    identity = env["amplitude"].mul(env["season_phase_cos"])
    return sharp_positive.union(identity)


def _precipitation_threshold(env: dict) -> Interval:
    span = _point(env["__annual_wet"]).sub(_point(env["__annual_dry"]))
    progress = env["annual_rainfall"].sub(
        _point(env["__annual_dry"]),
    ).div(span).clamp(0, 1 << TABLE_BITS)
    return _point(env["__threshold_wet"]).add(
        _point(env["__threshold_dry"]).sub(
            _point(env["__threshold_wet"]),
        ).mul(
            Interval.point(1 << TABLE_BITS, TABLE_BITS).sub(progress),
        ),
    )


def _precipitation_intensity(env: dict) -> Interval:
    smax = _point(env["__signal_max"])
    if env["signal"].hi <= smax.lo:
        capped = env["signal"]                 # min = signal（恒不超上限）
    elif env["signal"].lo >= smax.hi:
        capped = Interval.point(smax.lo, TABLE_BITS)
    else:
        capped = env["signal"].clamp(smax.lo, smax.hi)
    excess = capped.sub(env["threshold"]).clamp(0, 1 << 62)
    branch = excess.mul(_point(env["__intensity_scale"])).mul(
        env["mean_intensity"],
    )
    if env["signal"].lo <= env["threshold"].hi:
        return branch.union(Interval.point(0, TABLE_BITS))
    return branch


def _solar_latitude_proxy(env: dict) -> Interval:
    ratio = env["sea_level_temperature"].sub(
        _point(env["__input_min"]),
    ).div(_point(env["__input_max"]).sub(_point(env["__input_min"])))
    latitude = _point(env["__output_max"]).sub(
        ratio.mul(_point(env["__output_max"]).sub(_point(env["__output_min"]))),
    )
    return _clamp(latitude, _point(env["__output_min"]),
                  _point(env["__output_max"]))


def _seasonal_temperature_amplitude(env: dict) -> Interval:
    ratio = env["annual_temperature"].sub(
        _point(env["__input_temp_min"]),
    ).div(_point(env["__input_temp_max"]).sub(_point(env["__input_temp_min"])))
    base = _point(env["__cold_endpoint"]).sub(
        ratio.mul(_point(env["__cold_endpoint"]).sub(_point(env["__hot_endpoint"]))),
    )
    rain_factor = _point(env["__rain_reference"]).sub(
        env["annual_rainfall"],
    ).div(_point(env["__rain_reference"])).clamp(
        quantize(-0.5, TABLE_BITS), quantize(1.0, TABLE_BITS),
    )
    total = base.add(rain_factor.mul(_point(env["__rain_bonus_scale"])))
    return _clamp(total, _point(env["__output_min"]),
                  _point(env["__output_max"]))


def _diurnal_temperature_amplitude(env: dict) -> Interval:
    return env["seasonal_amplitude"].mul(
        _point(env["__diurnal_to_seasonal_ratio"]),
    )


def _seasonal_humidity_amplitude(env: dict) -> Interval:
    return env["seasonal_amplitude"].mul(
        _point(env["__humidity_seasonal_scale"]),
    )


def _diurnal_humidity_amplitude(env: dict) -> Interval:
    return env["seasonal_amplitude"].mul(
        _point(env["__diurnal_to_seasonal_ratio"]),
    ).mul(_point(env["__humidity_diurnal_scale"]))


def _diurnal_phase_cos(env: dict) -> Interval:
    from olam.kernel.frozen_tables import TWO_PI_Q
    from enclosure import cos_enclosure

    angle = env["hour"].sub(_point(env["__peak_hour"])).scale_by_ratio(
        TWO_PI_Q, 24 << TABLE_BITS,
    )
    return cos_enclosure(angle)


def _sunrise(env: dict, sign: int) -> Interval:
    from olam.kernel.frozen_tables import TWO_PI_Q
    from enclosure import (
        acos_enclosure, degrees_enclosure, tan_enclosure,
    )
    from olam.kernel.fixed import round_half_even_div

    pi_over_180 = round_half_even_div(TWO_PI_Q, 360)
    latitude_rad = env["latitude"].mul(
        Interval.point(pi_over_180, TABLE_BITS),
    )
    product = tan_enclosure(latitude_rad).mul(
        tan_enclosure(env["solar_declination"]),
    )
    angle = acos_enclosure(product.neg())
    half_day = degrees_enclosure(angle).div(_point(15.0))
    base = _point(12.0)
    return base.sub(half_day) if sign < 0 else base.add(half_day)


def _sunrise_equation_box(env: dict) -> Interval:
    return _sunrise(env, -1)


def _sunset_equation_box(env: dict) -> Interval:
    return _sunrise(env, +1)


def _daylight(env: dict) -> Interval:
    return env["sunset"].sub(env["sunrise"])


def _hour_of_day(env: dict) -> Interval:
    """hour = (tick % game_day)/game_hour：整数锯齿 → 端点包络。"""
    from olam.kernel.fixed import round_half_even_div

    lo, hi = env["tick"]
    gd, gh = int(env["__game_day"]), int(env["__game_hour"])
    span = hi - lo
    if span >= gd:
        t_lo, t_hi = 0, gd - 1
    elif lo // gd == hi // gd:
        t_lo, t_hi = lo % gd, hi % gd
    else:
        t_lo, t_hi = 0, gd - 1
    scale = 1 << TABLE_BITS
    q_lo = round_half_even_div(t_lo * scale, gh)
    q_hi = round_half_even_div(t_hi * scale, gh)
    return Interval(min(q_lo, q_hi), max(q_lo, q_hi), TABLE_BITS)


def _season_phase_cos(env: dict) -> Interval:
    """cos((progress−1.5)/S·2π)，progress = season + dos/L（整数锯齿）。"""
    from enclosure import cos_enclosure
    from olam.kernel.fixed import round_half_even_div
    from olam.kernel.frozen_tables import TWO_PI_Q

    lo, hi = env["day"]
    L = int(env["__season_length_days"])
    S = int(env["__seasons_per_year"])
    span = hi - lo
    if span >= L * S:
        p_lo, p_hi = 0, L * S - 1
    elif (lo - 1) // L == (hi - 1) // L:
        p_lo = ((lo - 1) // L % S) * L + (lo - 1) % L
        p_hi = p_lo + span
    else:
        p_lo, p_hi = 0, L * S - 1
    denom = 2 * L * S
    q_lo = round_half_even_div((2 * p_lo - 3 * L) * TWO_PI_Q, denom)
    q_hi = round_half_even_div((2 * p_hi - 3 * L) * TWO_PI_Q, denom)
    angle = Interval(min(q_lo, q_hi), max(q_lo, q_hi), TABLE_BITS)
    return cos_enclosure(angle)


def _solar_declination_box(env: dict) -> Interval:
    """declination = radians(ob·sin(2π(doy − D/8)/D))，doy 整数。"""
    from enclosure import sin_enclosure
    from olam.kernel.fixed import mul, quantize, round_half_even_div
    from olam.kernel.frozen_tables import TWO_PI_Q

    lo, hi = env["day_of_year"]
    D = int(env["__days_per_year"])
    ob_q = quantize(env["__obliquity_deg"], TABLE_BITS)
    pi_over_180 = round_half_even_div(TWO_PI_Q, 360)
    a_lo = round_half_even_div((lo - D // 8) * TWO_PI_Q, D)
    a_hi = round_half_even_div((hi - D // 8) * TWO_PI_Q, D)
    angle = Interval(min(a_lo, a_hi), max(a_lo, a_hi), TABLE_BITS)
    value = sin_enclosure(angle).mul(Interval.point(ob_q, TABLE_BITS))
    return Interval(
        mul(value.lo, pi_over_180, TABLE_BITS),
        mul(value.hi, pi_over_180, TABLE_BITS),
        TABLE_BITS,
    )


def _template_range(
    env: dict, zone_arg: str, template_getter, clamp_hi: float,
) -> Interval:
    """模板查表 + 噪声线性 ramp 的包络（跨候选气候档取凸包）。"""
    from olam.generation.climate import ClimateZone, get_climate_template
    from olam.kernel.fixed import clamp as fixed_clamp, mul, quantize

    bits = TABLE_BITS

    def q(value: float) -> int:
        return quantize(value, bits)

    zones = env[zone_arg]
    noise = env.get("noise_box")
    results = []
    for zone in (zones.values if isinstance(zones, EnumBox) else tuple(zones)):
        template = get_climate_template(ClimateZone(int(zone)))
        lo, hi = template_getter(template)
        if noise is None:
            results.append(Interval.point(q(lo), bits))
            continue
        for n in (noise.lo, noise.hi):
            value = q(lo) + mul(
                mul(n + q(1.0), q(0.5), bits), q(hi) - q(lo), bits,
            )
            results.append(Interval.point(
                fixed_clamp(value, q(0.0), q(clamp_hi)), bits,
            ))
    hull = Interval.hull(results)
    # ramp 连续 → 两端点凸包即全部取值（线性单增）
    return Interval(hull.lo, hull.hi, bits)


def _baseline_humidity_box(env: dict) -> Interval:
    box = dict(env)
    box["noise_box"] = env["humidity_noise"]
    return _template_range(
        box, "climate_zone", lambda t: t.humidity_range, 100.0,
    )


def _baseline_wind_box(env: dict) -> Interval:
    box = dict(env)
    box["noise_box"] = env["wind_noise"]
    return _template_range(
        box, "climate_zone", lambda t: t.wind_speed_range, 50.0,
    )


def _mean_precip_box(env: dict) -> Interval:
    from olam.kernel.fixed import quantize

    from olam.generation.climate import ClimateZone, get_climate_template

    zones = env["climate_zone"]
    values = [
        quantize(
            get_climate_template(ClimateZone(int(z))).mean_precip_intensity,
            TABLE_BITS,
        )
        for z in (zones.values if isinstance(zones, EnumBox) else tuple(zones))
    ]
    return Interval(min(values), max(values), TABLE_BITS)


def _sharpness_box(env: dict) -> Interval:
    from olam.kernel.fixed import quantize

    from olam.generation.climate import ClimateZone, get_climate_template

    zones = env["climate_zone"]
    values = [
        quantize(
            get_climate_template(ClimateZone(int(z))).humidity_sharpness,
            TABLE_BITS,
        )
        for z in (zones.values if isinstance(zones, EnumBox) else tuple(zones))
    ]
    return Interval(min(values), max(values), TABLE_BITS)


#: 机制 → 包络构造器（参数经 env["__<形参名>"] 以点包络注入）
ENCLOSURE_BUILDERS = {
    "weather.instant.compose_temperature.v1": _compose_temperature,
    "weather.instant.compose_humidity.v1": _compose_humidity,
    "weather.instant.compose_sunshine.v1": _compose_sunshine,
    "weather.instant.compose_wind_speed.v1": _compose_wind,
    "weather.offset.derive_diurnal_temperature.v1": _diurnal_temperature_offset,
    "weather.offset.derive_seasonal_temperature.v1": _seasonal_temperature_offset,
    "weather.offset.derive_diurnal_humidity.v1": _diurnal_humidity_offset,
    "weather.offset.derive_seasonal_humidity.v1": _seasonal_humidity_offset,
    "weather.chunk.derive_precipitation_threshold.v1": _precipitation_threshold,
    "weather.instant.compose_precipitation_intensity.v1": _precipitation_intensity,
    "weather.chunk.derive_solar_latitude_proxy.v1": _solar_latitude_proxy,
    "weather.chunk.derive_seasonal_temperature_amplitude.v1": _seasonal_temperature_amplitude,
    "weather.chunk.derive_diurnal_temperature_amplitude.v1": _diurnal_temperature_amplitude,
    "weather.chunk.derive_seasonal_humidity_amplitude.v1": _seasonal_humidity_amplitude,
    "weather.chunk.derive_diurnal_humidity_amplitude.v1": _diurnal_humidity_amplitude,
    "weather.tick.derive_diurnal_phase_cos.v1": _diurnal_phase_cos,
    "weather.astronomy.derive_sunrise.v1": _sunrise_equation_box,
    "weather.astronomy.derive_sunset.v1": _sunset_equation_box,
    "weather.astronomy.derive_daylight.v1": _daylight,
    "weather.tick.derive_hour_of_day.v1": _hour_of_day,
    "weather.tick.derive_season_phase_cos.v1": _season_phase_cos,
    "weather.tick.derive_solar_declination.v1": _solar_declination_box,
    "world.gen.derive_baseline_humidity.v1": _baseline_humidity_box,
    "world.gen.derive_baseline_wind_speed.v1": _baseline_wind_box,
    "world.gen.derive_mean_precip_intensity.v1": _mean_precip_box,
    "world.gen.derive_humidity_sharpness.v1": _sharpness_box,
}

@dataclass(frozen=True, slots=True)
class EnumBox:
    """枚举/类别量的候选值盒（V5 采样与包络构造共用）。"""

    values: tuple


@dataclass
class EnclosureCheckReport:
    """包含对拍报告。"""

    checked: int = 0
    samples: int = 0
    skipped: int = 0
    uncovered: list[str] = field(default_factory=list)
    problems: list[tuple] = field(default_factory=list)
    samples_by_mechanism: dict = field(default_factory=dict)

    @property
    def passed(self) -> bool:
        return not self.uncovered and not self.problems


def _boxes_for(mechanism, program, rng: random.Random):
    """为机制的父槽位生成随机输入盒（点盒 + 随机宽度）。"""
    boxes: list[dict] = []
    for _ in range(24):
        box: dict = {}
        for parent in mechanism.parents:
            domain = program.slots[parent.slot].domain
            if domain.choices:
                box[parent.slot] = EnumBox(tuple(domain.choices))
                continue
            bounds = (
                (domain.minimum, domain.maximum)
                if domain.minimum is not None and domain.maximum is not None
                else None
            )
            if bounds is None:
                base = None
                for witness in mechanism.witnesses:
                    value = witness.inputs.get(parent.argument)
                    if value is not None:
                        base = value
                        break
                if base is None:
                    box[parent.slot] = None
                    continue
                if domain.kind == "int":
                    box[parent.slot] = (int(base), int(base) + 3)
                else:
                    width = max(1e-3, abs(float(base)) * 0.02)
                    lo = quantize(float(base) - width, TABLE_BITS)
                    hi = quantize(float(base) + width, TABLE_BITS)
                    box[parent.slot] = Interval(
                        min(lo, hi), max(lo, hi), TABLE_BITS,
                    )
                continue
            if domain.kind == "int":
                box[parent.slot] = (int(bounds[0]), int(bounds[1]))
                continue
            lo, hi = bounds
            width = max(1e-6, (hi - lo) * rng.uniform(0.0, 0.05))
            center = rng.uniform(lo, min(hi, lo + width) if hi > lo + width else hi)
            low = min(center, hi - width)
            raw_lo = quantize(low, TABLE_BITS)
            raw_hi = quantize(min(hi, low + width), TABLE_BITS)
            box[parent.slot] = Interval(min(raw_lo, raw_hi),
                                        max(raw_lo, raw_hi), TABLE_BITS)
        boxes.append(box)
    return boxes


def check_enclosures(program, *, seed: int = 20260918) -> EnclosureCheckReport:
    """逐机制做包含对拍；返回报告。"""
    report = EnclosureCheckReport()
    rng = random.Random(seed)
    params = dict(program.parameters)
    for mechanism_id, builder in ENCLOSURE_BUILDERS.items():
        report.samples_by_mechanism[mechanism_id] = 0
        mechanism = program.mechanisms.get(mechanism_id)
        if mechanism is None:
            report.uncovered.append(mechanism_id)
            continue
        report.checked += 1
        for box in _boxes_for(mechanism, program, rng):
            if any(value is None for value in box.values()):
                report.skipped += 1
                continue
            env = {parent.argument: box[parent.slot]
                   for parent in mechanism.parents}
            for parameter_id, argument in mechanism.param_arguments:
                env[f"__{argument}"] = params[parameter_id]
            try:
                envelope = builder(env)
            except (ZeroDivisionError, ValueError):
                report.skipped += 1
                continue
            for _ in range(6):
                sample = {}
                for parent in mechanism.parents:
                    interval = box[parent.slot]
                    if isinstance(interval, EnumBox):
                        sample[parent.slot] = rng.choice(interval.values)
                    elif isinstance(interval, tuple):
                        sample[parent.slot] = rng.randint(*interval)
                    else:
                        raw = rng.randint(interval.lo, interval.hi)
                        sample[parent.slot] = raw / (1 << TABLE_BITS)
                try:
                    value = evaluate_direct(
                        program, mechanism_id, sample, tick=0,
                    )
                except ValueError:
                    report.skipped += 1
                    continue
                report.samples += 1
                report.samples_by_mechanism[mechanism_id] += 1
                if not envelope.contains(quantize(value, TABLE_BITS)):
                    report.problems.append((
                        mechanism_id, dict(box), sample, value,
                        (envelope.lo, envelope.hi),
                    ))
    return report


__all__ = ["ENCLOSURE_BUILDERS", "EnclosureCheckReport", "check_enclosures"]
