"""旧注册表方程实现（P1 逐位移植；生成后即为源码）。

本文件由一次性移植生成器从旧机制注册表提取；导入已改写为新内核
（``ascend.world.kernel``）。改动方程必须同步黄金向量
（``backend/tests/world/data/weather_golden.json`` 由旧实现生成，
P2 后作为冻结契约保留）。
"""
from __future__ import annotations
from ascend.space.climate import ClimateZone, get_climate_template


def _sea_level_temperature_equation(latitude_noise: float) -> float:
    """定点实现（issue #52）：Q30 乘加 + clamp [-20, 38]。"""
    from .gen_fixed import sea_level_temperature

    return sea_level_temperature(latitude_noise)


def _rainfall_equation(rainfall_noise: float) -> float:
    """定点实现（issue #52）：min + (n+1)/2*(max-min) 的 Q30 定点等价。"""
    from .gen_fixed import rainfall_from_noise

    return rainfall_from_noise(rainfall_noise)


def _lapse_rate_equation(
    sea_level_temperature: float,
    altitude: float,
) -> float:
    """定点实现（issue #52）：陆地直减率 + clamp [-20, 36]；海域恒等。"""
    from .gen_fixed import apply_lapse_rate

    return apply_lapse_rate(sea_level_temperature, altitude)


def _climate_zone_equation(
    mean_temperature: float,
    annual_rainfall: float,
    altitude: float,
) -> int:
    """定点实现（issue #52）：量化域整数决策树（判定顺序同 C）。"""
    from .gen_fixed import classify_climate

    return classify_climate(mean_temperature, annual_rainfall, altitude)


def _biome_equation(
    mean_temperature: float,
    annual_rainfall: float,
    altitude: float,
    sea_level_temperature: float,
    moisture_noise: float,
) -> int:
    """群系主隶属（issue #52 定点实现；静态细分值域）。"""
    from .gen_fixed import biome_from_attrs

    return biome_from_attrs(
        mean_temperature, annual_rainfall, altitude, sea_level_temperature,
        moisture_noise,
    )


def _baseline_humidity_equation(
    climate_zone: int,
    humidity_noise: float,
) -> float:
    """定点实现（issue #53 P4）：模板/噪声量化 + 定点乘加 + 定点 clamp。"""
    from ascend.world.kernel.fixed import clamp as fixed_clamp, mul, quantize, to_float
    from ascend.world.kernel.frozen_tables import TABLE_BITS

    template = get_climate_template(ClimateZone(climate_zone))
    lo, hi = template.humidity_range
    bits = TABLE_BITS

    def q(value: float) -> int:
        return quantize(value, bits)

    value = q(lo) + mul(
        mul(q(humidity_noise) + q(1.0), q(0.5), bits),
        q(hi) - q(lo), bits,
    )
    return to_float(
        fixed_clamp(value, q(0.0), q(100.0)), bits,
    )


def _baseline_wind_speed_equation(
    climate_zone: int,
    wind_noise: float,
) -> float:
    """定点实现（issue #53 P4）：模板/噪声量化 + 定点乘加 + 定点 clamp。"""
    from ascend.world.kernel.fixed import clamp as fixed_clamp, mul, quantize, to_float
    from ascend.world.kernel.frozen_tables import TABLE_BITS

    template = get_climate_template(ClimateZone(climate_zone))
    lo, hi = template.wind_speed_range
    bits = TABLE_BITS

    def q(value: float) -> int:
        return quantize(value, bits)

    value = q(lo) + mul(
        mul(q(wind_noise) + q(1.0), q(0.5), bits),
        q(hi) - q(lo), bits,
    )
    return to_float(
        fixed_clamp(value, q(0.0), q(50.0)), bits,
    )


def _mean_precip_intensity_equation(climate_zone: int) -> float:
    """定点实现（issue #53 P4）：模板值量化往返（数据契约）。"""
    from ascend.world.kernel.fixed import quantize, to_float
    from ascend.world.kernel.frozen_tables import TABLE_BITS

    value = get_climate_template(
        ClimateZone(climate_zone),
    ).mean_precip_intensity
    return to_float(quantize(value, TABLE_BITS), TABLE_BITS)


def _humidity_sharpness_equation(climate_zone: int) -> float:
    """定点实现（issue #53 P4）：模板值量化往返（数据契约）。"""
    from ascend.world.kernel.fixed import quantize, to_float
    from ascend.world.kernel.frozen_tables import TABLE_BITS

    value = get_climate_template(
        ClimateZone(climate_zone),
    ).humidity_sharpness
    return to_float(quantize(value, TABLE_BITS), TABLE_BITS)
