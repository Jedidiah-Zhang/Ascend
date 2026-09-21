"""方程实现 — 天气机制的定点/预计算表求值。

导入内核 ``olam.kernel``；改动方程必须同步黄金向量
（``testbench/world/data/weather_golden.json``，冻结契约数据）。
"""
from __future__ import annotations


def _derive_latitude_equation(
    sea_level_temperature: float,
    input_min: float,
    input_max: float,
    output_min: float,
    output_max: float,
) -> float:
    """定点实现：量化 + 定点除/乘 + 定点 clamp。"""
    from olam.kernel.fixed import clamp as fixed_clamp, div, mul, quantize, to_float
    from olam.kernel.frozen_tables import TABLE_BITS

    bits = TABLE_BITS

    def q(value: float) -> int:
        return quantize(value, bits)

    ratio = div(
        q(sea_level_temperature) - q(input_min),
        q(input_max) - q(input_min), bits,
    )
    latitude = q(output_max) - mul(
        ratio, q(output_max) - q(output_min), bits,
    )
    return to_float(
        fixed_clamp(latitude, q(output_min), q(output_max)), bits,
    )


def _derive_seasonal_amplitude_equation(
    annual_temperature: float,
    annual_rainfall: float,
    input_temp_min: float,
    input_temp_max: float,
    cold_endpoint: float,
    hot_endpoint: float,
    rain_reference: float,
    rain_bonus_scale: float,
    output_min: float,
    output_max: float,
) -> float:
    """定点实现：量化 + 定点除/乘 + 两处定点 clamp。"""
    from olam.kernel.fixed import clamp as fixed_clamp, div, mul, quantize, to_float
    from olam.kernel.frozen_tables import TABLE_BITS

    bits = TABLE_BITS

    def q(value: float) -> int:
        return quantize(value, bits)

    temperature_ratio = div(
        q(annual_temperature) - q(input_temp_min),
        q(input_temp_max) - q(input_temp_min), bits,
    )
    base_amplitude = q(cold_endpoint) - mul(
        temperature_ratio, q(cold_endpoint) - q(hot_endpoint), bits,
    )
    rain_factor = fixed_clamp(
        div(q(rain_reference) - q(annual_rainfall), q(rain_reference), bits),
        q(-0.5), q(1.0),
    )
    total = base_amplitude + mul(rain_factor, q(rain_bonus_scale), bits)
    return to_float(
        fixed_clamp(total, q(output_min), q(output_max)), bits,
    )


def _diurnal_amplitude_equation(
    seasonal_amplitude: float,
    diurnal_to_seasonal_ratio: float,
) -> float:
    """定点实现：量化 + 定点乘（半偶舍入）。"""
    from olam.kernel.fixed import mul, quantize, to_float
    from olam.kernel.frozen_tables import TABLE_BITS

    bits = TABLE_BITS
    return to_float(
        mul(quantize(seasonal_amplitude, bits),
            quantize(diurnal_to_seasonal_ratio, bits), bits),
        bits,
    )


def _humidity_seasonal_amplitude_equation(
    seasonal_amplitude: float,
    humidity_seasonal_scale: float,
) -> float:
    """定点实现：量化 + 定点乘（半偶舍入）。"""
    from olam.kernel.fixed import mul, quantize, to_float
    from olam.kernel.frozen_tables import TABLE_BITS

    bits = TABLE_BITS
    return to_float(
        mul(quantize(seasonal_amplitude, bits),
            quantize(humidity_seasonal_scale, bits), bits),
        bits,
    )


def _humidity_diurnal_amplitude_equation(
    seasonal_amplitude: float,
    diurnal_to_seasonal_ratio: float,
    humidity_diurnal_scale: float,
) -> float:
    """定点实现：量化 + 定点乘链（左结合，半偶舍入）。"""
    from olam.kernel.fixed import mul, quantize, to_float
    from olam.kernel.frozen_tables import TABLE_BITS

    bits = TABLE_BITS
    value = mul(
        quantize(seasonal_amplitude, bits),
        quantize(diurnal_to_seasonal_ratio, bits), bits,
    )
    return to_float(mul(value, quantize(humidity_diurnal_scale, bits), bits), bits)


def _precip_threshold_equation(
    annual_rainfall: float,
    annual_dry: float,
    annual_wet: float,
    threshold_dry: float,
    threshold_wet: float,
) -> float:
    """定点实现：量化 + 定点除/乘 + clamp（半偶舍入）。"""
    from olam.kernel.fixed import clamp as fixed_clamp, div, mul, quantize, to_float
    from olam.kernel.frozen_tables import TABLE_BITS

    bits = TABLE_BITS
    scale = 1 << bits

    def q(value: float) -> int:
        return quantize(value, bits)

    progress = fixed_clamp(
        div(q(annual_rainfall) - q(annual_dry),
            q(annual_wet) - q(annual_dry), bits),
        0, scale,
    )
    value = q(threshold_wet) + mul(
        q(threshold_dry) - q(threshold_wet), scale - progress, bits,
    )
    return to_float(value, bits)


def _day_equation(tick: int, game_day: int) -> int:
    return tick // game_day + 1


def _day_of_year_equation(tick: int, game_day: int, days_per_year: int) -> int:
    return (tick // game_day) % days_per_year


def _hour_equation(tick: int, game_day: int, game_hour: int) -> float:
    """定点实现：整数日历除法 + Q30 半偶舍入。"""
    from olam.kernel.diurnal import hour_of_day_q
    from olam.kernel.fixed import to_float
    from olam.kernel.frozen_tables import TABLE_BITS

    return to_float(hour_of_day_q(tick, game_day, game_hour), TABLE_BITS)


def _season_equation(
    day: int,
    season_length_days: int,
    seasons_per_year: int,
) -> int:
    return (day - 1) // season_length_days % seasons_per_year


def _season_phase_cos_equation(
    day: int,
    season_length_days: int,
    seasons_per_year: int,
) -> float:
    """预计算表实现：整数日算术 + 预计算表 cos + 半偶舍入。

    progress = season + day_of_season/L；angle = (progress − 1.5)/S · 2π；
    angle_q = (2·p_num − 3·L) · TWO_PI_Q / (2·L·S)。
    """
    from olam.kernel.fixed import round_half_even_div, to_float
    from olam.kernel.frozen_tables import TABLE_BITS, TWO_PI_Q
    from olam.kernel.tables import cos_q

    season = (day - 1) // season_length_days % seasons_per_year
    day_of_season = (day - 1) % season_length_days
    p_num = season * season_length_days + day_of_season
    numerator = (2 * p_num - 3 * season_length_days) * TWO_PI_Q
    denominator = 2 * season_length_days * seasons_per_year
    return to_float(
        cos_q(round_half_even_div(numerator, denominator), TABLE_BITS),
        TABLE_BITS,
    )


def _diurnal_phase_cos_equation(hour: float, peak_hour: float) -> float:
    """预计算表实现：输入量化 + 预计算表 cos + 半偶舍入。"""
    from olam.kernel.diurnal import diurnal_phase_cos_q
    from olam.kernel.fixed import quantize, to_float
    from olam.kernel.frozen_tables import TABLE_BITS

    return to_float(
        diurnal_phase_cos_q(
            quantize(hour, TABLE_BITS), quantize(peak_hour, TABLE_BITS),
        ),
        TABLE_BITS,
    )


def _solar_declination_equation(
    day_of_year: int,
    obliquity_deg: float,
    days_per_year: int,
) -> float:
    """预计算表实现：整数日算术 + 预计算表 sin + 定点乘。

    radians(x) = x·π/180；π/180 的 Q 值由 TWO_PI_Q 整数半偶除得。
    """
    from olam.kernel.fixed import mul, quantize, round_half_even_div, to_float
    from olam.kernel.frozen_tables import TABLE_BITS, TWO_PI_Q
    from olam.kernel.tables import sin_q

    bits = TABLE_BITS
    step = day_of_year - days_per_year // 8
    angle_q = round_half_even_div(step * TWO_PI_Q, days_per_year)
    pi_over_180_q = round_half_even_div(TWO_PI_Q, 360)
    value = mul(quantize(obliquity_deg, bits), sin_q(angle_q, bits), bits)
    return to_float(mul(value, pi_over_180_q, bits), bits)


def _seasonal_temperature_offset_equation(
    amplitude: float,
    season_phase_cos: float,
) -> float:
    """定点实现：定点乘（半偶舍入）。"""
    from olam.kernel.fixed import mul, quantize, to_float
    from olam.kernel.frozen_tables import TABLE_BITS

    return to_float(
        mul(
            quantize(amplitude, TABLE_BITS),
            quantize(season_phase_cos, TABLE_BITS),
            TABLE_BITS,
        ),
        TABLE_BITS,
    )


def _diurnal_temperature_offset_equation(
    amplitude: float,
    diurnal_phase_cos: float,
) -> float:
    """定点实现：定点乘（半偶舍入）。"""
    from olam.kernel.diurnal import diurnal_temperature_offset_q
    from olam.kernel.fixed import quantize, to_float
    from olam.kernel.frozen_tables import TABLE_BITS

    return to_float(
        diurnal_temperature_offset_q(
            quantize(amplitude, TABLE_BITS),
            quantize(diurnal_phase_cos, TABLE_BITS),
        ),
        TABLE_BITS,
    )


def _seasonal_humidity_offset_equation(
    amplitude: float,
    season_phase_cos: float,
    sharpness: float,
) -> float:
    """定点/预计算表实现：sharpness>0 走预计算表 tanh，否则恒等。"""
    from olam.kernel.fixed import mul, quantize, to_float
    from olam.kernel.frozen_tables import TABLE_BITS
    from olam.kernel.tables import tanh_q

    amplitude_q = quantize(amplitude, TABLE_BITS)
    phase_q = quantize(season_phase_cos, TABLE_BITS)
    if sharpness > 0:
        inner = tanh_q(
            mul(phase_q, quantize(sharpness, TABLE_BITS), TABLE_BITS),
            TABLE_BITS,
        )
    else:
        inner = phase_q
    return to_float(mul(amplitude_q, inner, TABLE_BITS), TABLE_BITS)


def _diurnal_humidity_offset_equation(
    amplitude: float,
    diurnal_phase_cos: float,
) -> float:
    """定点实现：量化 + 定点乘 + 取负（半偶舍入）。"""
    from olam.kernel.fixed import mul, quantize, to_float
    from olam.kernel.frozen_tables import TABLE_BITS

    bits = TABLE_BITS
    return to_float(
        -mul(quantize(amplitude, bits), quantize(diurnal_phase_cos, bits), bits),
        bits,
    )


def _sunrise_equation(latitude: float, solar_declination: float) -> float:
    """预计算表实现：tan=sin/cos 表相除 + acos 预计算表 + degrees。

    注意：acos 在端点附近误差声明为 2e-3 rad（见 tables.ACOS_MAX_ERROR），
    对应日出/日落时刻误差上界 ~0.008 h。
    """
    from olam.kernel.fixed import (
        clamp as fixed_clamp, div, mul, quantize, round_half_even_div, to_float,
    )
    from olam.kernel.frozen_tables import TABLE_BITS, TWO_PI_Q
    from olam.kernel.tables import acos_q, degrees_q, tan_q

    bits = TABLE_BITS
    scale = 1 << bits

    def q(value: float) -> int:
        return quantize(value, bits)

    pi_over_180 = round_half_even_div(TWO_PI_Q, 360)
    latitude_rad = mul(q(latitude), pi_over_180, bits)
    product = mul(tan_q(latitude_rad, bits), tan_q(q(solar_declination), bits), bits)
    angle = acos_q(-fixed_clamp(product, -scale, scale), bits)
    half_day_hours = div(degrees_q(angle, bits), q(15.0), bits)
    return to_float(q(12.0) - half_day_hours, bits)


def _sunset_equation(latitude: float, solar_declination: float) -> float:
    """预计算表实现：tan=sin/cos 表相除 + acos 预计算表 + degrees。

    与 sunrise 同构（12 + 半昼长），误差声明同 tables.ACOS_MAX_ERROR。
    """
    from olam.kernel.fixed import (
        clamp as fixed_clamp, div, mul, quantize, round_half_even_div, to_float,
    )
    from olam.kernel.frozen_tables import TABLE_BITS, TWO_PI_Q
    from olam.kernel.tables import acos_q, degrees_q, tan_q

    bits = TABLE_BITS
    scale = 1 << bits

    def q(value: float) -> int:
        return quantize(value, bits)

    pi_over_180 = round_half_even_div(TWO_PI_Q, 360)
    latitude_rad = mul(q(latitude), pi_over_180, bits)
    product = mul(tan_q(latitude_rad, bits), tan_q(q(solar_declination), bits), bits)
    angle = acos_q(-fixed_clamp(product, -scale, scale), bits)
    half_day_hours = div(degrees_q(angle, bits), q(15.0), bits)
    return to_float(q(12.0) + half_day_hours, bits)


def _daylight_equation(sunrise: float, sunset: float) -> float:
    """定点实现：量化后整数相减（精确）。"""
    from olam.kernel.fixed import quantize, to_float
    from olam.kernel.frozen_tables import TABLE_BITS

    bits = TABLE_BITS
    return to_float(
        quantize(sunset, bits) - quantize(sunrise, bits), bits,
    )


def _temperature_equation(
    annual_temperature: float,
    seasonal_offset: float,
    diurnal_offset: float,
    perturbation: float,
    perturb_scale: float,
    lower_bound: float,
    upper_bound: float,
) -> float:
    """定点实现：量化 + 整数加 + 定点乘 + 定点 clamp。"""
    from olam.kernel.fixed import clamp as fixed_clamp, mul, quantize, to_float
    from olam.kernel.frozen_tables import TABLE_BITS

    bits = TABLE_BITS

    def q(value: float) -> int:
        return quantize(value, bits)

    total = (
        q(annual_temperature) + q(seasonal_offset) + q(diurnal_offset)
        + mul(q(perturbation), q(perturb_scale), bits)
    )
    return to_float(fixed_clamp(total, q(lower_bound), q(upper_bound)), bits)


def _humidity_equation(
    baseline: float,
    seasonal_offset: float,
    diurnal_offset: float,
    perturbation: float,
    perturb_scale: float,
    lower_bound: float,
    upper_bound: float,
) -> float:
    """定点实现：量化 + 整数加 + 定点乘 + 定点 clamp。"""
    from olam.kernel.fixed import clamp as fixed_clamp, mul, quantize, to_float
    from olam.kernel.frozen_tables import TABLE_BITS

    bits = TABLE_BITS

    def q(value: float) -> int:
        return quantize(value, bits)

    total = (
        q(baseline) + q(seasonal_offset) + q(diurnal_offset)
        + mul(q(perturbation), q(perturb_scale), bits)
    )
    return to_float(fixed_clamp(total, q(lower_bound), q(upper_bound)), bits)


def _wind_equation(
    baseline: float,
    perturbation: float,
    multiplier: float,
    perturb_scale: float,
    lower_bound: float,
    upper_bound: float,
) -> float:
    """定点实现：量化 + 定点乘 + 双层定点 clamp。"""
    from olam.kernel.fixed import clamp as fixed_clamp, mul, quantize, to_float
    from olam.kernel.frozen_tables import TABLE_BITS

    bits = TABLE_BITS

    def q(value: float) -> int:
        return quantize(value, bits)

    lo, hi = q(lower_bound), q(upper_bound)
    base = fixed_clamp(
        q(baseline) + mul(q(perturbation), q(perturb_scale), bits), lo, hi,
    )
    return to_float(fixed_clamp(mul(base, q(multiplier), bits), lo, hi), bits)


def _precipitation_intensity_equation(
    signal: float,
    threshold: float,
    mean_intensity: float,
    signal_max: float,
    intensity_scale: float,
) -> float:
    """定点实现：分段判据 + 定点乘（半偶舍入）。"""
    from olam.kernel.fixed import mul, quantize, to_float
    from olam.kernel.frozen_tables import TABLE_BITS

    bits = TABLE_BITS

    def q(value: float) -> int:
        return quantize(value, bits)

    signal_q = q(signal)
    threshold_q = q(threshold)
    if signal_q <= threshold_q:
        return 0.0
    excess = min(signal_q, q(signal_max)) - threshold_q
    return to_float(
        mul(mul(excess, q(intensity_scale), bits), q(mean_intensity), bits),
        bits,
    )


def _sunshine_equation(
    daylight_hours: float,
    perturbation: float,
    perturb_scale: float,
    lower_bound: float,
    upper_bound: float,
) -> float:
    """定点实现：量化 + 定点乘 + 定点 clamp。"""
    from olam.kernel.fixed import clamp as fixed_clamp, mul, quantize, to_float
    from olam.kernel.frozen_tables import TABLE_BITS

    bits = TABLE_BITS

    def q(value: float) -> int:
        return quantize(value, bits)

    total = q(daylight_hours) + mul(q(perturbation), q(perturb_scale), bits)
    return to_float(fixed_clamp(total, q(lower_bound), q(upper_bound)), bits)


def _precipitation_type_equation(instant_temperature: float) -> str:
    return "snow" if round(instant_temperature, 1) <= 0 else "rain"
