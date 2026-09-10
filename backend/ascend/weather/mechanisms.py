"""天气机制声明 — 天气模块全部公式的生产单一事实源。

本模块只产出声明片段（节点/参数/机制元组），注册表组装在
``ascend.causal.world``。生产求值点（derive.py / weather_engine.py）
经注册表 ``evaluate`` 执行这些方程。
"""

from __future__ import annotations

import hashlib
import json
import math

from ascend.causal import (
    AccessPolicy,
    DependencyWitness,
    InstanceDomain,
    MathMetadata,
    MechanismSpec,
    NodeSpec,
    ParameterBinding,
    ParameterSpec,
    ParentSpec,
    StateOwnership,
    UpdateContract,
    ValueDomain,
)
from ascend.causal.microsteps import (
    WEATHER_CHUNK_DERIVED_A,
    WEATHER_CHUNK_DERIVED_B,
    WEATHER_FRAME_INPUT,
    WEATHER_INSTANT_COMPOSITE,
    WEATHER_INSTANT_OFFSET,
    WEATHER_INSTANT_READOUT,
    WEATHER_INSTANT_TICK_DERIVED,
    WEATHER_INSTANT_TICK_INPUT,
)
from ascend.config import (
    DIURNAL_PEAK_HOUR,
    DIURNAL_TO_SEASONAL_RATIO,
    GAME_DAY,
    GAME_HOUR,
    HUMIDITY_BOUNDS,
    HUMIDITY_DIURNAL_SCALE,
    HUMIDITY_PERTURB_SCALE,
    HUMIDITY_SEASONAL_SCALE,
    LATITUDE_MAX,
    LATITUDE_MIN,
    LATITUDE_T_MAX,
    LATITUDE_T_MIN,
    OBLIQUITY_DEG,
    PRECIP_ANNUAL_DRY,
    PRECIP_ANNUAL_WET,
    PRECIP_INTENSITY_SCALE,
    PRECIP_SIGNAL_MAX,
    PRECIP_THRESHOLD_DRY,
    PRECIP_THRESHOLD_WET,
    SEASON_LENGTH_DAYS,
    SEASONAL_AMP_BOUNDS,
    SEASONAL_AMP_MAX,
    SEASONAL_AMP_MIN,
    SEASONAL_AMP_R_BONUS,
    SEASONAL_AMP_R_REF,
    SEASONAL_AMP_T_MAX,
    SEASONAL_AMP_T_MIN,
    SEASONS_PER_YEAR,
    SUNSHINE_BOUNDS,
    SUNSHINE_PERTURB_SCALE,
    TEMP_BOUNDS,
    TEMP_PERTURB_SCALE,
    WIND_BOUNDS,
    WIND_PERTURB_SCALE,
)
from ascend.mathutil import clamp

# ── 节点 ID ─────────────────────────────────────────────────────

CLOCK_TICK = "world.clock.tick"
ANNUAL_TEMPERATURE = "weather.chunk.annual_mean_temperature_c"
ANNUAL_RAINFALL = "weather.chunk.annual_rainfall_mm_per_year"
SEA_LEVEL_TEMPERATURE = "weather.chunk.sea_level_temperature_c"
SOLAR_LATITUDE_PROXY = "weather.chunk.solar_latitude_proxy_deg"
SEASONAL_TEMPERATURE_AMPLITUDE = (
    "weather.chunk.seasonal_temperature_amplitude_c"
)
DIURNAL_TEMPERATURE_AMPLITUDE = (
    "weather.chunk.diurnal_temperature_amplitude_c"
)
SEASONAL_HUMIDITY_AMPLITUDE = "weather.chunk.seasonal_humidity_amplitude_pp"
DIURNAL_HUMIDITY_AMPLITUDE = "weather.chunk.diurnal_humidity_amplitude_pp"
PRECIPITATION_THRESHOLD = "weather.chunk.precipitation_threshold"
BASELINE_HUMIDITY = "weather.chunk.baseline_humidity_percent"
BASELINE_WIND_SPEED = "weather.chunk.baseline_wind_speed_mps"
MEAN_PRECIP_INTENSITY = "weather.chunk.mean_precip_intensity_mm_per_hour"
HUMIDITY_SHARPNESS = "weather.chunk.humidity_sharpness"

FIELD_TEMPERATURE_PERTURBATION = "weather.field.temperature_perturbation"
FIELD_HUMIDITY_PERTURBATION = "weather.field.humidity_perturbation"
FIELD_WIND_PERTURBATION = "weather.field.wind_perturbation"
FIELD_PRECIPITATION_SIGNAL = "weather.field.precipitation_signal"
FIELD_WIND_MULTIPLIER = "weather.field.wind_multiplier"

DAY = "weather.tick.day"
DAY_OF_YEAR = "weather.tick.day_of_year"
HOUR_OF_DAY = "weather.tick.hour_of_day"
SEASON = "weather.tick.season"
SEASON_PHASE_COS = "weather.tick.season_phase_cos"
DIURNAL_PHASE_COS = "weather.tick.diurnal_phase_cos"
SOLAR_DECLINATION = "weather.tick.solar_declination_rad"

SEASONAL_TEMPERATURE_OFFSET = "weather.offset.seasonal_temperature_c"
DIURNAL_TEMPERATURE_OFFSET = "weather.offset.diurnal_temperature_c"
SEASONAL_HUMIDITY_OFFSET = "weather.offset.seasonal_humidity_pp"
DIURNAL_HUMIDITY_OFFSET = "weather.offset.diurnal_humidity_pp"
SUNRISE_HOUR = "weather.astronomy.sunrise_hour"
SUNSET_HOUR = "weather.astronomy.sunset_hour"
DAYLIGHT_HOURS = "weather.astronomy.daylight_hours"

INSTANT_TEMPERATURE = "weather.instant.temperature_c"
INSTANT_HUMIDITY = "weather.instant.relative_humidity_percent"
INSTANT_WIND_SPEED = "weather.instant.wind_speed_mps"
INSTANT_SUNSHINE = "weather.instant.sunshine_hours_per_day"
INSTANT_PRECIPITATION_INTENSITY = (
    "weather.instant.precipitation_intensity_mm_per_hour"
)
INSTANT_PRECIPITATION_TYPE = "weather.instant.precipitation_type"

# ── 参数 ID ─────────────────────────────────────────────────────

_P_LAT_T_MIN = "weather.parameter.latitude.input_min_c"
_P_LAT_T_MAX = "weather.parameter.latitude.input_max_c"
_P_LAT_MIN = "weather.parameter.latitude.output_min_deg"
_P_LAT_MAX = "weather.parameter.latitude.output_max_deg"
_P_AMP_T_MIN = "weather.parameter.seasonal_amplitude.input_min_c"
_P_AMP_T_MAX = "weather.parameter.seasonal_amplitude.input_max_c"
_P_AMP_MAX = "weather.parameter.seasonal_amplitude.cold_endpoint_c"
_P_AMP_MIN = "weather.parameter.seasonal_amplitude.hot_endpoint_c"
_P_AMP_R_REF = "weather.parameter.seasonal_amplitude.rain_reference_mm_per_year"
_P_AMP_R_BONUS = "weather.parameter.seasonal_amplitude.rain_bonus_c"
_P_AMP_BOUND_MIN = "weather.parameter.seasonal_amplitude.output_min_c"
_P_AMP_BOUND_MAX = "weather.parameter.seasonal_amplitude.output_max_c"

_P_GAME_DAY = "world.parameter.game_day_ticks"
_P_GAME_HOUR = "world.parameter.game_hour_ticks"
_P_DAYS_PER_YEAR = "world.parameter.days_per_year"
_P_SEASON_LENGTH_DAYS = "world.parameter.season_length_days"
_P_SEASONS_PER_YEAR = "world.parameter.seasons_per_year"
_P_DIURNAL_PEAK_HOUR = "world.parameter.diurnal_peak_hour"
_P_OBLIQUITY_DEG = "world.parameter.obliquity_deg"
_P_DIURNAL_TO_SEASONAL_RATIO = "world.parameter.diurnal_to_seasonal_ratio"
_P_HUMIDITY_DIURNAL_SCALE = "world.parameter.humidity_diurnal_scale"
_P_HUMIDITY_SEASONAL_SCALE = "world.parameter.humidity_seasonal_scale"
_P_TEMP_PERTURB_SCALE = "world.parameter.temp_perturb_scale_c"
_P_HUMIDITY_PERTURB_SCALE = "world.parameter.humidity_perturb_scale_pp"
_P_WIND_PERTURB_SCALE = "world.parameter.wind_perturb_scale_mps"
_P_SUNSHINE_PERTURB_SCALE = "world.parameter.sunshine_perturb_scale_h"
_P_TEMP_BOUND_LO = "world.parameter.temp_bound_min_c"
_P_TEMP_BOUND_HI = "world.parameter.temp_bound_max_c"
_P_HUMIDITY_BOUND_LO = "world.parameter.humidity_bound_min_pp"
_P_HUMIDITY_BOUND_HI = "world.parameter.humidity_bound_max_pp"
_P_WIND_BOUND_LO = "world.parameter.wind_bound_min_mps"
_P_WIND_BOUND_HI = "world.parameter.wind_bound_max_mps"
_P_SUNSHINE_BOUND_LO = "world.parameter.sunshine_bound_min_h"
_P_SUNSHINE_BOUND_HI = "world.parameter.sunshine_bound_max_h"
_P_PRECIP_SIGNAL_MAX = "world.parameter.precip_signal_max"
_P_PRECIP_INTENSITY_SCALE = "world.parameter.precip_intensity_scale"
_P_PRECIP_ANNUAL_DRY = "world.parameter.precip_annual_dry_mm"
_P_PRECIP_ANNUAL_WET = "world.parameter.precip_annual_wet_mm"
_P_PRECIP_THRESHOLD_DRY = "world.parameter.precip_threshold_dry"
_P_PRECIP_THRESHOLD_WET = "world.parameter.precip_threshold_wet"

# ── 构建助手 ───────────────────────────────────────────────────

_CHUNK_INSTANCE = InstanceDomain(
    kind="spatial_field",
    axes=("chunk_x", "chunk_y"),
    creation="chunk_registration",
    destruction="chunk_unregistration",
)
_GLOBAL_INSTANCE = InstanceDomain(
    kind="global_singleton",
    axes=(),
    creation="world_initialization",
    destruction="world_teardown",
)
_ACCESS = AccessPolicy(
    interventions=("node", "persistent", "mechanism"),
    research_trace=True,
    observation_protocols=("research.full.v1", "agent.weather.v1"),
)
# 边界/读出分量只可观测、不可干预（§7 读出分量保护，声明期即生效）
_ACCESS_OBSERVED = AccessPolicy(
    interventions=(),
    research_trace=True,
    observation_protocols=("research.full.v1", "agent.weather.v1"),
)


def _access_for(role: str, origin: str) -> AccessPolicy:
    """按角色/来源派生干预权限声明（唯一派生处）。"""
    if role == "readout" or origin == "slice_boundary":
        return _ACCESS_OBSERVED
    return _ACCESS


def _parameter_version(parameter_id: str, value: float, source: str) -> str:
    payload = json.dumps(
        {"id": parameter_id, "value": value, "source": source,
         "source_version": 1},
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(payload).hexdigest()


def _parameter(
    parameter_id: str,
    value: float,
    unit: str,
    bounds: tuple[float, float],
    source_key: str,
) -> ParameterSpec:
    """声明环境参数；source_key 溯源到真实数据/代码来源。

    Args:
        source_key: 数据键形如 ``TEMP_PERTURB_SCALE``（data/world.json#
            weather.* 分区）；代码常量以 ``code:`` 前缀、派生量以
            ``expr:`` 前缀标注（无对应数据文件键）。
    """
    if source_key.startswith("code:"):
        source = f"code-only:{source_key[5:]}（ascend.config 代码常量）"
    elif source_key.startswith("expr:"):
        source = f"derived:{source_key[5:]}"
    else:
        source = f"data/world.json#weather.{source_key}"
    return ParameterSpec(
        parameter_id=parameter_id,
        value_type="float",
        unit=unit,
        bounds=bounds,
        value=value,
        version=_parameter_version(parameter_id, value, source),
        intervention_allowed=True,
        source=source,
    )


def _node(
    node_id: str,
    *,
    role: str,
    origin: str,
    kind: str,
    unit: str,
    bounds: tuple[float, float] | None,
    choices: tuple[object, ...],
    quantization: str,
    in_world_state: bool,
    reconstruction: str,
    schedule: str,
    microstep: str,
    writer: str,
    error_budget: float,
    valid_domain: str,
    global_singleton: bool = False,
) -> NodeSpec:
    return NodeSpec(
        node_id=node_id,
        role=role,
        origin=origin,
        instance_domain=_GLOBAL_INSTANCE if global_singleton else _CHUNK_INSTANCE,
        value=ValueDomain(
            kind=kind,
            unit=unit,
            bounds=bounds,
            choices=choices,
            missing="forbidden",
            quantization=quantization,
        ),
        state=StateOwnership(
            in_world_state=in_world_state,
            reconstruction=reconstruction,
        ),
        update=UpdateContract(
            schedule=schedule,
            microstep=microstep,
            when_not_updated=(
                "retain_previous_value"
                if in_world_state
                else "recompute_on_demand"
            ),
            writer=writer,
            merge_rule="single_writer",
        ),
        access=_access_for(role, origin),
        math=MathMetadata(
            error_budget=error_budget,
            metric="absolute_difference" if kind == "float" else "discrete",
            valid_domain=valid_domain,
        ),
    )


def _parent(
    parent: str,
    argument: str,
    source_microstep: str,
    lipschitz: float,
    valid_domain: str,
    analysis_role: str,
) -> ParentSpec:
    return ParentSpec(
        parent=parent,
        argument=argument,
        lag=0,
        source_microstep=source_microstep,
        spatial_offsets=((0, 0),),
        entity_relation="same_chunk",
        aggregation="identity",
        broadcast="same_instance",
        boundary_operator="identity_same_chunk",
        guard="always",
        lipschitz=lipschitz,
        metric="absolute_difference",
        valid_domain=valid_domain,
        analysis_role=analysis_role,
    )


# ── 方程实现（唯一事实源）───────────────────────────────────────


def _derive_latitude_equation(
    sea_level_temperature: float,
    input_min: float,
    input_max: float,
    output_min: float,
    output_max: float,
) -> float:
    ratio = (sea_level_temperature - input_min) / (input_max - input_min)
    latitude = output_max - ratio * (output_max - output_min)
    return clamp(latitude, output_min, output_max)


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
    temperature_ratio = (annual_temperature - input_temp_min) / (
        input_temp_max - input_temp_min
    )
    base_amplitude = cold_endpoint - temperature_ratio * (
        cold_endpoint - hot_endpoint
    )
    rain_factor = clamp(
        (rain_reference - annual_rainfall) / rain_reference,
        -0.5,
        1.0,
    )
    return clamp(
        base_amplitude + rain_factor * rain_bonus_scale,
        output_min,
        output_max,
    )


def _diurnal_amplitude_equation(
    seasonal_amplitude: float,
    diurnal_to_seasonal_ratio: float,
) -> float:
    return seasonal_amplitude * diurnal_to_seasonal_ratio


def _humidity_seasonal_amplitude_equation(
    seasonal_amplitude: float,
    humidity_seasonal_scale: float,
) -> float:
    return seasonal_amplitude * humidity_seasonal_scale


def _humidity_diurnal_amplitude_equation(
    seasonal_amplitude: float,
    diurnal_to_seasonal_ratio: float,
    humidity_diurnal_scale: float,
) -> float:
    return (
        seasonal_amplitude
        * diurnal_to_seasonal_ratio
        * humidity_diurnal_scale
    )


def _precip_threshold_equation(
    annual_rainfall: float,
    annual_dry: float,
    annual_wet: float,
    threshold_dry: float,
    threshold_wet: float,
) -> float:
    progress = (annual_rainfall - annual_dry) / (annual_wet - annual_dry)
    progress = clamp(progress, 0.0, 1.0)
    return threshold_wet + (threshold_dry - threshold_wet) * (1.0 - progress)


def _day_equation(tick: int, game_day: int) -> int:
    return tick // game_day + 1


def _day_of_year_equation(tick: int, game_day: int, days_per_year: int) -> int:
    return (tick // game_day) % days_per_year


def _hour_equation(tick: int, game_day: int, game_hour: int) -> float:
    return (tick % game_day) / game_hour


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
    season = (day - 1) // season_length_days % seasons_per_year
    day_of_season = (day - 1) % season_length_days
    progress = season + day_of_season / season_length_days
    return math.cos((progress - 1.5) / seasons_per_year * 2.0 * math.pi)


def _diurnal_phase_cos_equation(hour: float, peak_hour: float) -> float:
    return math.cos((hour - peak_hour) / 24.0 * 2.0 * math.pi)


def _solar_declination_equation(
    day_of_year: int,
    obliquity_deg: float,
    days_per_year: int,
) -> float:
    return math.radians(
        obliquity_deg
        * math.sin(2.0 * math.pi * (day_of_year - days_per_year // 8) / days_per_year)
    )


def _seasonal_temperature_offset_equation(
    amplitude: float,
    season_phase_cos: float,
) -> float:
    return amplitude * season_phase_cos


def _diurnal_temperature_offset_equation(
    amplitude: float,
    diurnal_phase_cos: float,
) -> float:
    return amplitude * diurnal_phase_cos


def _seasonal_humidity_offset_equation(
    amplitude: float,
    season_phase_cos: float,
    sharpness: float,
) -> float:
    if sharpness > 0:
        return amplitude * math.tanh(season_phase_cos * sharpness)
    return amplitude * season_phase_cos


def _diurnal_humidity_offset_equation(
    amplitude: float,
    diurnal_phase_cos: float,
) -> float:
    return amplitude * (-diurnal_phase_cos)


def _sunrise_equation(latitude: float, solar_declination: float) -> float:
    lat = math.radians(latitude)
    tan_product = max(-1.0, min(1.0, math.tan(lat) * math.tan(solar_declination)))
    half_day_deg = math.degrees(math.acos(-tan_product))
    return 12.0 - half_day_deg / 15.0


def _sunset_equation(latitude: float, solar_declination: float) -> float:
    lat = math.radians(latitude)
    tan_product = max(-1.0, min(1.0, math.tan(lat) * math.tan(solar_declination)))
    half_day_deg = math.degrees(math.acos(-tan_product))
    return 12.0 + half_day_deg / 15.0


def _daylight_equation(sunrise: float, sunset: float) -> float:
    return sunset - sunrise


def _temperature_equation(
    annual_temperature: float,
    seasonal_offset: float,
    diurnal_offset: float,
    perturbation: float,
    perturb_scale: float,
    lower_bound: float,
    upper_bound: float,
) -> float:
    return clamp(
        annual_temperature + seasonal_offset + diurnal_offset
        + perturbation * perturb_scale,
        lower_bound,
        upper_bound,
    )


def _humidity_equation(
    baseline: float,
    seasonal_offset: float,
    diurnal_offset: float,
    perturbation: float,
    perturb_scale: float,
    lower_bound: float,
    upper_bound: float,
) -> float:
    return clamp(
        baseline + seasonal_offset + diurnal_offset
        + perturbation * perturb_scale,
        lower_bound,
        upper_bound,
    )


def _wind_equation(
    baseline: float,
    perturbation: float,
    multiplier: float,
    perturb_scale: float,
    lower_bound: float,
    upper_bound: float,
) -> float:
    base = clamp(baseline + perturbation * perturb_scale, lower_bound, upper_bound)
    return clamp(base * multiplier, lower_bound, upper_bound)


def _precipitation_intensity_equation(
    signal: float,
    threshold: float,
    mean_intensity: float,
    signal_max: float,
    intensity_scale: float,
) -> float:
    if signal <= threshold:
        return 0.0
    excess = min(signal, signal_max) - threshold
    return excess * intensity_scale * mean_intensity


def _sunshine_equation(
    daylight_hours: float,
    perturbation: float,
    perturb_scale: float,
    lower_bound: float,
    upper_bound: float,
) -> float:
    return clamp(
        daylight_hours + perturbation * perturb_scale,
        lower_bound,
        upper_bound,
    )


def _precipitation_type_equation(instant_temperature: float) -> str:
    return "snow" if round(instant_temperature, 1) <= 0 else "rain"


# ── 节点 ────────────────────────────────────────────────────────


def _chunk_boundary(
    node_id: str,
    *,
    kind: str,
    unit: str,
    bounds: tuple[float, float] | None,
    quantization: str,
    writer: str,
    reconstruction: str,
    in_world_state: bool,
    error_budget: float,
    valid_domain: str,
    microstep: str,
) -> NodeSpec:
    return _node(
        node_id,
        role="persistent_state",
        origin="slice_boundary",
        kind=kind,
        unit=unit,
        bounds=bounds,
        choices=(),
        quantization=quantization,
        in_world_state=in_world_state,
        reconstruction=reconstruction,
        schedule="provided_by_external_writer",
        microstep=microstep,
        writer=writer,
        error_budget=error_budget,
        valid_domain=valid_domain,
    )


_NODES = (
    # ── 世界时钟与统一天气场（切片边界输入）──────────────
    _node(
        CLOCK_TICK,
        role="persistent_state",
        origin="slice_boundary",
        kind="integer",
        unit="tick",
        bounds=None,
        choices=(),
        quantization="exact_integer",
        in_world_state=True,
        reconstruction="not_applicable:world_clock_state",
        schedule="on_clock_advance",
        microstep=WEATHER_FRAME_INPUT,
        writer="time.world_clock",
        error_budget=0.0,
        valid_domain="nonnegative_integer_tick",
        global_singleton=True,
    ),
    _chunk_boundary(
        FIELD_TEMPERATURE_PERTURBATION,
        kind="float",
        unit="dimensionless",
        bounds=(-20.0, 20.0),
        quantization="ieee754_binary64",
        writer="outside_slice:unified_weather_field.temperature_channel",
        reconstruction="outside_slice:unified_weather_field.temperature_channel",
        in_world_state=False,
        error_budget=0.05,
        valid_domain="unified_field_channel_composite",
        microstep=WEATHER_FRAME_INPUT,
    ),
    _chunk_boundary(
        FIELD_HUMIDITY_PERTURBATION,
        kind="float",
        unit="dimensionless",
        bounds=(-2.0, 2.0),
        quantization="ieee754_binary64",
        writer="outside_slice:unified_weather_field.humidity_channel",
        reconstruction="outside_slice:unified_weather_field.humidity_channel",
        in_world_state=False,
        error_budget=0.05,
        valid_domain="unified_field_channel_composite",
        microstep=WEATHER_FRAME_INPUT,
    ),
    _chunk_boundary(
        FIELD_WIND_PERTURBATION,
        kind="float",
        unit="dimensionless",
        bounds=(-2.0, 2.0),
        quantization="ieee754_binary64",
        writer="outside_slice:unified_weather_field.wind_channel",
        reconstruction="outside_slice:unified_weather_field.wind_channel",
        in_world_state=False,
        error_budget=0.05,
        valid_domain="unified_field_channel_composite",
        microstep=WEATHER_FRAME_INPUT,
    ),
    _chunk_boundary(
        FIELD_PRECIPITATION_SIGNAL,
        kind="float",
        unit="dimensionless",
        bounds=(0.0, 10.0),
        quantization="ieee754_binary64",
        writer="outside_slice:unified_weather_field.precipitation_channel",
        reconstruction="outside_slice:unified_weather_field.precipitation_channel",
        in_world_state=False,
        error_budget=0.05,
        valid_domain="unified_field_channel_composite",
        microstep=WEATHER_FRAME_INPUT,
    ),
    _chunk_boundary(
        FIELD_WIND_MULTIPLIER,
        kind="float",
        unit="dimensionless",
        bounds=(1.0, 100.0),
        quantization="ieee754_binary64",
        writer="outside_slice:unified_weather_field.feature_multiplier",
        reconstruction="outside_slice:unified_weather_field.feature_multiplier",
        in_world_state=False,
        error_budget=0.0,
        valid_domain="at_least_one_multiplier",
        microstep=WEATHER_FRAME_INPUT,
    ),
    # ── chunk 派生（a 层）────────────────────────────────
    _node(
        SOLAR_LATITUDE_PROXY,
        role="mechanism_state",
        origin="mechanism",
        kind="float",
        unit="degree_north",
        bounds=(LATITUDE_MIN, LATITUDE_MAX),
        choices=(),
        quantization="ieee754_binary64",
        in_world_state=False,
        reconstruction="weather.chunk.derive_solar_latitude_proxy.v1",
        schedule="on_chunk_registration",
        microstep=WEATHER_CHUNK_DERIVED_A,
        writer="weather.chunk.derive_solar_latitude_proxy.v1",
        error_budget=0.5,
        valid_domain="closed_interval_0_80_degrees",
    ),
    _node(
        SEASONAL_TEMPERATURE_AMPLITUDE,
        role="mechanism_state",
        origin="mechanism",
        kind="float",
        unit="degC",
        bounds=SEASONAL_AMP_BOUNDS,
        choices=(),
        quantization="ieee754_binary64",
        in_world_state=False,
        reconstruction="weather.chunk.derive_seasonal_temperature_amplitude.v1",
        schedule="on_chunk_registration",
        microstep=WEATHER_CHUNK_DERIVED_A,
        writer="weather.chunk.derive_seasonal_temperature_amplitude.v1",
        error_budget=0.1,
        valid_domain="closed_interval_1_30_degC",
    ),
    _node(
        PRECIPITATION_THRESHOLD,
        role="mechanism_state",
        origin="mechanism",
        kind="float",
        unit="dimensionless",
        bounds=(PRECIP_THRESHOLD_WET, PRECIP_THRESHOLD_DRY),
        choices=(),
        quantization="ieee754_binary64",
        in_world_state=False,
        reconstruction="weather.chunk.derive_precipitation_threshold.v1",
        schedule="on_chunk_registration",
        microstep=WEATHER_CHUNK_DERIVED_A,
        writer="weather.chunk.derive_precipitation_threshold.v1",
        error_budget=0.01,
        valid_domain="closed_interval_wet_to_dry",
    ),
    # ── chunk 派生（b 层）────────────────────────────────
    _node(
        DIURNAL_TEMPERATURE_AMPLITUDE,
        role="mechanism_state",
        origin="mechanism",
        kind="float",
        unit="degC",
        bounds=(0.0, 20.0),
        choices=(),
        quantization="ieee754_binary64",
        in_world_state=False,
        reconstruction="weather.chunk.derive_diurnal_temperature_amplitude.v1",
        schedule="on_chunk_registration",
        microstep=WEATHER_CHUNK_DERIVED_B,
        writer="weather.chunk.derive_diurnal_temperature_amplitude.v1",
        error_budget=0.1,
        valid_domain="nonnegative_diurnal_amplitude",
    ),
    _node(
        SEASONAL_HUMIDITY_AMPLITUDE,
        role="mechanism_state",
        origin="mechanism",
        kind="float",
        unit="pp",
        bounds=(0.0, 20.0),
        choices=(),
        quantization="ieee754_binary64",
        in_world_state=False,
        reconstruction="weather.chunk.derive_seasonal_humidity_amplitude.v1",
        schedule="on_chunk_registration",
        microstep=WEATHER_CHUNK_DERIVED_B,
        writer="weather.chunk.derive_seasonal_humidity_amplitude.v1",
        error_budget=0.1,
        valid_domain="nonnegative_humidity_amplitude",
    ),
    _node(
        DIURNAL_HUMIDITY_AMPLITUDE,
        role="mechanism_state",
        origin="mechanism",
        kind="float",
        unit="pp",
        bounds=(0.0, 20.0),
        choices=(),
        quantization="ieee754_binary64",
        in_world_state=False,
        reconstruction="weather.chunk.derive_diurnal_humidity_amplitude.v1",
        schedule="on_chunk_registration",
        microstep=WEATHER_CHUNK_DERIVED_B,
        writer="weather.chunk.derive_diurnal_humidity_amplitude.v1",
        error_budget=0.1,
        valid_domain="nonnegative_humidity_amplitude",
    ),
    # ── tick 上下文（输入层）────────────────────────────
    _node(
        DAY,
        role="mechanism_state",
        origin="mechanism",
        kind="integer",
        unit="game_day",
        bounds=None,
        choices=(),
        quantization="exact_integer",
        in_world_state=False,
        reconstruction="weather.tick.derive_day.v1",
        schedule="on_weather_evaluation",
        microstep=WEATHER_INSTANT_TICK_INPUT,
        writer="weather.tick.derive_day.v1",
        error_budget=0.0,
        valid_domain="positive_game_day",
        global_singleton=True,
    ),
    _node(
        DAY_OF_YEAR,
        role="mechanism_state",
        origin="mechanism",
        kind="integer",
        unit="game_day",
        bounds=(0, 360),
        choices=(),
        quantization="exact_integer",
        in_world_state=False,
        reconstruction="weather.tick.derive_day_of_year.v1",
        schedule="on_weather_evaluation",
        microstep=WEATHER_INSTANT_TICK_INPUT,
        writer="weather.tick.derive_day_of_year.v1",
        error_budget=0.0,
        valid_domain="zero_based_day_of_year",
        global_singleton=True,
    ),
    _node(
        HOUR_OF_DAY,
        role="mechanism_state",
        origin="mechanism",
        kind="float",
        unit="hour",
        bounds=(0.0, 24.0),
        choices=(),
        quantization="ieee754_binary64",
        in_world_state=False,
        reconstruction="weather.tick.derive_hour_of_day.v1",
        schedule="on_weather_evaluation",
        microstep=WEATHER_INSTANT_TICK_INPUT,
        writer="weather.tick.derive_hour_of_day.v1",
        error_budget=0.0,
        valid_domain="half_open_interval_0_24_hours",
        global_singleton=True,
    ),
    # ── tick 上下文（派生层）────────────────────────────
    _node(
        SEASON,
        role="mechanism_state",
        origin="mechanism",
        kind="enum",
        unit="season_index",
        bounds=None,
        choices=(0, 1, 2, 3),
        quantization="exact_enum",
        in_world_state=False,
        reconstruction="weather.tick.derive_season.v1",
        schedule="on_weather_evaluation",
        microstep=WEATHER_INSTANT_TICK_DERIVED,
        writer="weather.tick.derive_season.v1",
        error_budget=0.0,
        valid_domain="spring_summer_autumn_winter",
        global_singleton=True,
    ),
    _node(
        SEASON_PHASE_COS,
        role="mechanism_state",
        origin="mechanism",
        kind="float",
        unit="dimensionless",
        bounds=(-1.0, 1.0),
        choices=(),
        quantization="ieee754_binary64",
        in_world_state=False,
        reconstruction="weather.tick.derive_season_phase_cos.v1",
        schedule="on_weather_evaluation",
        microstep=WEATHER_INSTANT_TICK_DERIVED,
        writer="weather.tick.derive_season_phase_cos.v1",
        error_budget=0.0,
        valid_domain="cosine_range",
        global_singleton=True,
    ),
    _node(
        DIURNAL_PHASE_COS,
        role="mechanism_state",
        origin="mechanism",
        kind="float",
        unit="dimensionless",
        bounds=(-1.0, 1.0),
        choices=(),
        quantization="ieee754_binary64",
        in_world_state=False,
        reconstruction="weather.tick.derive_diurnal_phase_cos.v1",
        schedule="on_weather_evaluation",
        microstep=WEATHER_INSTANT_TICK_DERIVED,
        writer="weather.tick.derive_diurnal_phase_cos.v1",
        error_budget=0.0,
        valid_domain="cosine_range",
        global_singleton=True,
    ),
    _node(
        SOLAR_DECLINATION,
        role="mechanism_state",
        origin="mechanism",
        kind="float",
        unit="radian",
        bounds=(-0.5, 0.5),
        choices=(),
        quantization="ieee754_binary64",
        in_world_state=False,
        reconstruction="weather.tick.derive_solar_declination.v1",
        schedule="on_weather_evaluation",
        microstep=WEATHER_INSTANT_TICK_DERIVED,
        writer="weather.tick.derive_solar_declination.v1",
        error_budget=0.0,
        valid_domain="solar_declination_range",
        global_singleton=True,
    ),
    # ── 偏移与天文（offset 层）──────────────────────────
    _node(
        SEASONAL_TEMPERATURE_OFFSET,
        role="mechanism_state",
        origin="mechanism",
        kind="float",
        unit="degC",
        bounds=(-30.0, 30.0),
        choices=(),
        quantization="ieee754_binary64",
        in_world_state=False,
        reconstruction="weather.offset.derive_seasonal_temperature.v1",
        schedule="on_weather_evaluation",
        microstep=WEATHER_INSTANT_OFFSET,
        writer="weather.offset.derive_seasonal_temperature.v1",
        error_budget=0.1,
        valid_domain="bounded_by_seasonal_amplitude",
    ),
    _node(
        DIURNAL_TEMPERATURE_OFFSET,
        role="mechanism_state",
        origin="mechanism",
        kind="float",
        unit="degC",
        bounds=(-20.0, 20.0),
        choices=(),
        quantization="ieee754_binary64",
        in_world_state=False,
        reconstruction="weather.offset.derive_diurnal_temperature.v1",
        schedule="on_weather_evaluation",
        microstep=WEATHER_INSTANT_OFFSET,
        writer="weather.offset.derive_diurnal_temperature.v1",
        error_budget=0.1,
        valid_domain="bounded_by_diurnal_amplitude",
    ),
    _node(
        SEASONAL_HUMIDITY_OFFSET,
        role="mechanism_state",
        origin="mechanism",
        kind="float",
        unit="pp",
        bounds=(-20.0, 20.0),
        choices=(),
        quantization="ieee754_binary64",
        in_world_state=False,
        reconstruction="weather.offset.derive_seasonal_humidity.v1",
        schedule="on_weather_evaluation",
        microstep=WEATHER_INSTANT_OFFSET,
        writer="weather.offset.derive_seasonal_humidity.v1",
        error_budget=0.1,
        valid_domain="bounded_by_humidity_amplitude",
    ),
    _node(
        DIURNAL_HUMIDITY_OFFSET,
        role="mechanism_state",
        origin="mechanism",
        kind="float",
        unit="pp",
        bounds=(-20.0, 20.0),
        choices=(),
        quantization="ieee754_binary64",
        in_world_state=False,
        reconstruction="weather.offset.derive_diurnal_humidity.v1",
        schedule="on_weather_evaluation",
        microstep=WEATHER_INSTANT_OFFSET,
        writer="weather.offset.derive_diurnal_humidity.v1",
        error_budget=0.1,
        valid_domain="bounded_by_humidity_amplitude",
    ),
    _node(
        SUNRISE_HOUR,
        role="mechanism_state",
        origin="mechanism",
        kind="float",
        unit="hour",
        bounds=(0.0, 12.0),
        choices=(),
        quantization="ieee754_binary64",
        in_world_state=False,
        reconstruction="weather.astronomy.derive_sunrise.v1",
        schedule="on_weather_evaluation",
        microstep=WEATHER_INSTANT_OFFSET,
        writer="weather.astronomy.derive_sunrise.v1",
        error_budget=0.05,
        valid_domain="half_open_interval_0_12_hours",
    ),
    _node(
        SUNSET_HOUR,
        role="mechanism_state",
        origin="mechanism",
        kind="float",
        unit="hour",
        bounds=(12.0, 24.0),
        choices=(),
        quantization="ieee754_binary64",
        in_world_state=False,
        reconstruction="weather.astronomy.derive_sunset.v1",
        schedule="on_weather_evaluation",
        microstep=WEATHER_INSTANT_OFFSET,
        writer="weather.astronomy.derive_sunset.v1",
        error_budget=0.05,
        valid_domain="half_open_interval_12_24_hours",
    ),
    # ── 合成层 ──────────────────────────────────────────
    _node(
        DAYLIGHT_HOURS,
        role="mechanism_state",
        origin="mechanism",
        kind="float",
        unit="hour",
        bounds=(0.0, 24.0),
        choices=(),
        quantization="ieee754_binary64",
        in_world_state=False,
        reconstruction="weather.astronomy.derive_daylight.v1",
        schedule="on_weather_evaluation",
        microstep=WEATHER_INSTANT_COMPOSITE,
        writer="weather.astronomy.derive_daylight.v1",
        error_budget=0.05,
        valid_domain="closed_interval_0_24_hours",
    ),
    _node(
        INSTANT_TEMPERATURE,
        role="mechanism_state",
        origin="mechanism",
        kind="float",
        unit="degC",
        bounds=TEMP_BOUNDS,
        choices=(),
        quantization="ieee754_binary64",
        in_world_state=False,
        reconstruction="weather.instant.compose_temperature.v1",
        schedule="on_weather_evaluation",
        microstep=WEATHER_INSTANT_COMPOSITE,
        writer="weather.instant.compose_temperature.v1",
        error_budget=0.05,
        valid_domain="finite_temperature_within_declared_bounds",
    ),
    _node(
        INSTANT_HUMIDITY,
        role="mechanism_state",
        origin="mechanism",
        kind="float",
        unit="percent",
        bounds=HUMIDITY_BOUNDS,
        choices=(),
        quantization="ieee754_binary64",
        in_world_state=False,
        reconstruction="weather.instant.compose_humidity.v1",
        schedule="on_weather_evaluation",
        microstep=WEATHER_INSTANT_COMPOSITE,
        writer="weather.instant.compose_humidity.v1",
        error_budget=1.0,
        valid_domain="closed_interval_0_100_percent",
    ),
    _node(
        INSTANT_WIND_SPEED,
        role="mechanism_state",
        origin="mechanism",
        kind="float",
        unit="mps",
        bounds=WIND_BOUNDS,
        choices=(),
        quantization="ieee754_binary64",
        in_world_state=False,
        reconstruction="weather.instant.compose_wind_speed.v1",
        schedule="on_weather_evaluation",
        microstep=WEATHER_INSTANT_COMPOSITE,
        writer="weather.instant.compose_wind_speed.v1",
        error_budget=0.5,
        valid_domain="closed_interval_0_50_mps",
    ),
    _node(
        INSTANT_PRECIPITATION_INTENSITY,
        role="mechanism_state",
        origin="mechanism",
        kind="float",
        unit="mm_per_hour",
        bounds=(0.0, 100.0),
        choices=(),
        quantization="ieee754_binary64",
        in_world_state=False,
        reconstruction="weather.instant.compose_precipitation_intensity.v1",
        schedule="on_weather_evaluation",
        microstep=WEATHER_INSTANT_COMPOSITE,
        writer="weather.instant.compose_precipitation_intensity.v1",
        error_budget=1.0,
        valid_domain="nonnegative_precipitation_intensity",
    ),
    # ── 读出层 ──────────────────────────────────────────
    _node(
        INSTANT_SUNSHINE,
        role="mechanism_state",
        origin="mechanism",
        kind="float",
        unit="hour_per_day",
        bounds=SUNSHINE_BOUNDS,
        choices=(),
        quantization="ieee754_binary64",
        in_world_state=False,
        reconstruction="weather.instant.compose_sunshine.v1",
        schedule="on_weather_evaluation",
        microstep=WEATHER_INSTANT_READOUT,
        writer="weather.instant.compose_sunshine.v1",
        error_budget=0.05,
        valid_domain="closed_interval_0_24_hours_per_day",
    ),
    _node(
        INSTANT_PRECIPITATION_TYPE,
        role="mechanism_state",
        origin="mechanism",
        kind="enum",
        unit="category",
        bounds=None,
        choices=("snow", "rain"),
        quantization="round_half_even_to_0.1_degC_then_threshold_at_0",
        in_world_state=False,
        reconstruction="weather.instant.classify_precipitation_type.v1",
        schedule="on_weather_evaluation",
        microstep=WEATHER_INSTANT_READOUT,
        writer="weather.instant.classify_precipitation_type.v1",
        error_budget=0.0,
        valid_domain="snow_or_rain",
    ),
)

# ── 参数 ────────────────────────────────────────────────────────

_PLACEHOLDER_BOUNDS = (-1e12, 1e12)


def _u(parameter_id, value, unit, source_key):
    """无物理界参数（tick 数/维度数等正整数）。"""
    return _parameter(parameter_id, value, unit, (0.0, 1e12), source_key)


_PARAMETERS = (
    _parameter(_P_LAT_T_MIN, LATITUDE_T_MIN, "degC", (-273.15, 100.0), "LATITUDE_T_MIN"),
    _parameter(_P_LAT_T_MAX, LATITUDE_T_MAX, "degC", (-273.15, 100.0), "LATITUDE_T_MAX"),
    _parameter(_P_LAT_MIN, LATITUDE_MIN, "degree_north", (0.0, 90.0), "LATITUDE_MIN"),
    _parameter(_P_LAT_MAX, LATITUDE_MAX, "degree_north", (0.0, 90.0), "LATITUDE_MAX"),
    _parameter(_P_AMP_T_MIN, SEASONAL_AMP_T_MIN, "degC", (-273.15, 100.0), "SEASONAL_AMP_T_MIN"),
    _parameter(_P_AMP_T_MAX, SEASONAL_AMP_T_MAX, "degC", (-273.15, 100.0), "SEASONAL_AMP_T_MAX"),
    _parameter(_P_AMP_MAX, SEASONAL_AMP_MAX, "degC", (0.0, 100.0), "SEASONAL_AMP_MAX"),
    _parameter(_P_AMP_MIN, SEASONAL_AMP_MIN, "degC", (0.0, 100.0), "SEASONAL_AMP_MIN"),
    _parameter(_P_AMP_R_REF, SEASONAL_AMP_R_REF, "mm_per_year", (1.0, 100000.0), "SEASONAL_AMP_R_REF"),
    _parameter(_P_AMP_R_BONUS, SEASONAL_AMP_R_BONUS, "degC", (0.0, 100.0), "SEASONAL_AMP_R_BONUS"),
    _parameter(_P_AMP_BOUND_MIN, SEASONAL_AMP_BOUNDS[0], "degC", (0.0, 100.0), "SEASONAL_AMP_BOUNDS[0]"),
    _parameter(_P_AMP_BOUND_MAX, SEASONAL_AMP_BOUNDS[1], "degC", (0.0, 100.0), "SEASONAL_AMP_BOUNDS[1]"),
    _u(_P_GAME_DAY, GAME_DAY, "tick", "code:ascend.config.GAME_DAY"),
    _u(_P_GAME_HOUR, GAME_HOUR, "tick", "code:ascend.config.GAME_HOUR"),
    _u(_P_DAYS_PER_YEAR, SEASON_LENGTH_DAYS * SEASONS_PER_YEAR, "game_day",
       "expr:SEASON_LENGTH_DAYS * SEASONS_PER_YEAR"),
    _u(_P_SEASON_LENGTH_DAYS, SEASON_LENGTH_DAYS, "game_day", "SEASON_LENGTH_DAYS"),
    _u(_P_SEASONS_PER_YEAR, SEASONS_PER_YEAR, "dimensionless", "SEASONS_PER_YEAR"),
    _u(_P_DIURNAL_PEAK_HOUR, DIURNAL_PEAK_HOUR, "hour", "DIURNAL_PEAK_HOUR"),
    _parameter(_P_OBLIQUITY_DEG, OBLIQUITY_DEG, "degree", (0.0, 90.0), "OBLIQUITY_DEG"),
    _parameter(_P_DIURNAL_TO_SEASONAL_RATIO, DIURNAL_TO_SEASONAL_RATIO, "dimensionless", (0.0, 10.0), "DIURNAL_TO_SEASONAL_RATIO"),
    _parameter(_P_HUMIDITY_DIURNAL_SCALE, HUMIDITY_DIURNAL_SCALE, "dimensionless", (0.0, 10.0), "HUMIDITY_DIURNAL_SCALE"),
    _parameter(_P_HUMIDITY_SEASONAL_SCALE, HUMIDITY_SEASONAL_SCALE, "dimensionless", (0.0, 10.0), "HUMIDITY_SEASONAL_SCALE"),
    _parameter(_P_TEMP_PERTURB_SCALE, TEMP_PERTURB_SCALE, "degC", (0.0, 100.0), "TEMP_PERTURB_SCALE"),
    _parameter(_P_HUMIDITY_PERTURB_SCALE, HUMIDITY_PERTURB_SCALE, "pp", (0.0, 100.0), "HUMIDITY_PERTURB_SCALE"),
    _parameter(_P_WIND_PERTURB_SCALE, WIND_PERTURB_SCALE, "mps", (0.0, 100.0), "WIND_PERTURB_SCALE"),
    _parameter(_P_SUNSHINE_PERTURB_SCALE, SUNSHINE_PERTURB_SCALE, "hour", (0.0, 100.0), "SUNSHINE_PERTURB_SCALE"),
    _parameter(_P_TEMP_BOUND_LO, TEMP_BOUNDS[0], "degC", _PLACEHOLDER_BOUNDS, "TEMP_BOUNDS[0]"),
    _parameter(_P_TEMP_BOUND_HI, TEMP_BOUNDS[1], "degC", _PLACEHOLDER_BOUNDS, "TEMP_BOUNDS[1]"),
    _parameter(_P_HUMIDITY_BOUND_LO, HUMIDITY_BOUNDS[0], "pp", _PLACEHOLDER_BOUNDS, "HUMIDITY_BOUNDS[0]"),
    _parameter(_P_HUMIDITY_BOUND_HI, HUMIDITY_BOUNDS[1], "pp", _PLACEHOLDER_BOUNDS, "HUMIDITY_BOUNDS[1]"),
    _parameter(_P_WIND_BOUND_LO, WIND_BOUNDS[0], "mps", _PLACEHOLDER_BOUNDS, "WIND_BOUNDS[0]"),
    _parameter(_P_WIND_BOUND_HI, WIND_BOUNDS[1], "mps", _PLACEHOLDER_BOUNDS, "WIND_BOUNDS[1]"),
    _parameter(_P_SUNSHINE_BOUND_LO, SUNSHINE_BOUNDS[0], "hour", _PLACEHOLDER_BOUNDS, "SUNSHINE_BOUNDS[0]"),
    _parameter(_P_SUNSHINE_BOUND_HI, SUNSHINE_BOUNDS[1], "hour", _PLACEHOLDER_BOUNDS, "SUNSHINE_BOUNDS[1]"),
    _parameter(_P_PRECIP_SIGNAL_MAX, PRECIP_SIGNAL_MAX, "dimensionless", (0.0, 10.0), "PRECIP_SIGNAL_MAX"),
    _parameter(_P_PRECIP_INTENSITY_SCALE, PRECIP_INTENSITY_SCALE, "dimensionless", (0.0, 100.0), "PRECIP_INTENSITY_SCALE"),
    _parameter(_P_PRECIP_ANNUAL_DRY, PRECIP_ANNUAL_DRY, "mm_per_year", (0.0, 1e6), "PRECIP_ANNUAL_DRY"),
    _parameter(_P_PRECIP_ANNUAL_WET, PRECIP_ANNUAL_WET, "mm_per_year", (0.0, 1e6), "PRECIP_ANNUAL_WET"),
    _parameter(_P_PRECIP_THRESHOLD_DRY, PRECIP_THRESHOLD_DRY, "dimensionless", (0.0, 1.0), "PRECIP_THRESHOLD_DRY"),
    _parameter(_P_PRECIP_THRESHOLD_WET, PRECIP_THRESHOLD_WET, "dimensionless", (0.0, 1.0), "PRECIP_THRESHOLD_WET"),
)

# ── 机制 ────────────────────────────────────────────────────────


def _mechanism(
    mechanism_id: str,
    output: str,
    equation: str,
    function,
    parents: tuple[ParentSpec, ...],
    parameters: tuple[ParameterBinding, ...],
    boundary_cases: tuple[str, ...],
    source_dependencies: tuple[object, ...],
    witnesses: tuple[DependencyWitness, ...],
) -> MechanismSpec:
    return MechanismSpec(
        mechanism_id=mechanism_id,
        output=output,
        equation=equation,
        function=function,
        parents=parents,
        parameters=parameters,
        random_sources=(),
        boundary_cases=boundary_cases,
        source_dependencies=source_dependencies,
        witnesses=witnesses,
    )


def _w(
    label: str,
    parent: str,
    inputs: tuple[tuple[str, object], ...],
    alternate_value: object,
    expected_outputs: tuple[object, object],
) -> DependencyWitness:
    return DependencyWitness(
        label=label,
        parent=parent,
        inputs=inputs,
        alternate_value=alternate_value,
        expected_outputs=expected_outputs,
    )


_MECHANISMS = (
    _mechanism(
        "weather.chunk.derive_solar_latitude_proxy.v1",
        SOLAR_LATITUDE_PROXY,
        "clamp(output_max - (sea_level_temperature - input_min) / "
        "(input_max - input_min) * (output_max - output_min), "
        "output_min, output_max)",
        _derive_latitude_equation,
        (
            _parent(SEA_LEVEL_TEMPERATURE, "sea_level_temperature",
                    "world.gen_derived_a", 2.0,
                    "temperature_in_declared_bounds", "inverse"),
        ),
        (
            ParameterBinding(_P_LAT_T_MIN, "input_min"),
            ParameterBinding(_P_LAT_T_MAX, "input_max"),
            ParameterBinding(_P_LAT_MIN, "output_min"),
            ParameterBinding(_P_LAT_MAX, "output_max"),
        ),
        (
            "sea_level_temperature_at_or_below_input_min:output_max",
            "sea_level_temperature_at_or_above_input_max:output_min",
            "finite_interior_input:linear_interpolation",
        ),
        (clamp,),
        (
            _w("sea_level_temperature_changes_latitude",
               SEA_LEVEL_TEMPERATURE,
               ((SEA_LEVEL_TEMPERATURE, -5.0),), 35.0, (80.0, 0.0)),
        ),
    ),
    _mechanism(
        "weather.chunk.derive_seasonal_temperature_amplitude.v1",
        SEASONAL_TEMPERATURE_AMPLITUDE,
        "clamp(cold_endpoint - (annual_temperature - input_temp_min) / "
        "(input_temp_max - input_temp_min) * (cold_endpoint - hot_endpoint) + "
        "clamp((rain_reference - annual_rainfall) / rain_reference, "
        "-0.5, 1.0) * rain_bonus_scale, output_min, output_max)",
        _derive_seasonal_amplitude_equation,
        (
            _parent(ANNUAL_TEMPERATURE, "annual_temperature",
                    "world.gen_derived_b", 0.65,
                    "temperature_in_declared_bounds", "inverse"),
            _parent(ANNUAL_RAINFALL, "annual_rainfall",
                    "world.gen_derived_a", 0.002,
                    "annual_rainfall_in_declared_bounds", "inverse"),
        ),
        (
            ParameterBinding(_P_AMP_T_MIN, "input_temp_min"),
            ParameterBinding(_P_AMP_T_MAX, "input_temp_max"),
            ParameterBinding(_P_AMP_MAX, "cold_endpoint"),
            ParameterBinding(_P_AMP_MIN, "hot_endpoint"),
            ParameterBinding(_P_AMP_R_REF, "rain_reference"),
            ParameterBinding(_P_AMP_R_BONUS, "rain_bonus_scale"),
            ParameterBinding(_P_AMP_BOUND_MIN, "output_min"),
            ParameterBinding(_P_AMP_BOUND_MAX, "output_max"),
        ),
        (
            "rain_factor_below_-0.5:clamp_to_-0.5",
            "rain_factor_above_1.0:clamp_to_1.0",
            "amplitude_below_output_min:clamp_to_output_min",
            "amplitude_above_output_max:clamp_to_output_max",
            "finite_interior_inputs:continuous_formula",
        ),
        (clamp,),
        (
            _w("annual_temperature_changes_amplitude",
               ANNUAL_TEMPERATURE,
               ((ANNUAL_TEMPERATURE, 0.0), (ANNUAL_RAINFALL, 800.0)),
               20.0, (27.15, 14.15)),
            _w("annual_rainfall_changes_amplitude",
               ANNUAL_RAINFALL,
               ((ANNUAL_TEMPERATURE, 15.0), (ANNUAL_RAINFALL, 200.0)),
               2000.0, (18.6, 15.0)),
        ),
    ),
    _mechanism(
        "weather.chunk.derive_diurnal_temperature_amplitude.v1",
        DIURNAL_TEMPERATURE_AMPLITUDE,
        "seasonal_amplitude * diurnal_to_seasonal_ratio",
        _diurnal_amplitude_equation,
        (
            _parent(SEASONAL_TEMPERATURE_AMPLITUDE, "seasonal_amplitude",
                    WEATHER_CHUNK_DERIVED_A, 0.5,
                    "seasonal_amplitude_in_declared_bounds", "forward"),
        ),
        (ParameterBinding(_P_DIURNAL_TO_SEASONAL_RATIO,
                          "diurnal_to_seasonal_ratio"),),
        ("zero_amplitude:zero_output", "positive_amplitude:linear_scaling"),
        (),
        (
            _w("seasonal_amplitude_changes_diurnal_amplitude",
               SEASONAL_TEMPERATURE_AMPLITUDE,
               ((SEASONAL_TEMPERATURE_AMPLITUDE, 10.0),), 20.0, (5.0, 10.0)),
        ),
    ),
    _mechanism(
        "weather.chunk.derive_seasonal_humidity_amplitude.v1",
        SEASONAL_HUMIDITY_AMPLITUDE,
        "seasonal_amplitude * humidity_seasonal_scale",
        _humidity_seasonal_amplitude_equation,
        (
            _parent(SEASONAL_TEMPERATURE_AMPLITUDE, "seasonal_amplitude",
                    WEATHER_CHUNK_DERIVED_A, 0.4,
                    "seasonal_amplitude_in_declared_bounds", "forward"),
        ),
        (ParameterBinding(_P_HUMIDITY_SEASONAL_SCALE, "humidity_seasonal_scale"),),
        ("zero_amplitude:zero_output", "positive_amplitude:linear_scaling"),
        (),
        (
            _w("seasonal_amplitude_changes_humidity_amplitude",
               SEASONAL_TEMPERATURE_AMPLITUDE,
               ((SEASONAL_TEMPERATURE_AMPLITUDE, 10.0),), 20.0, (4.0, 8.0)),
        ),
    ),
    _mechanism(
        "weather.chunk.derive_diurnal_humidity_amplitude.v1",
        DIURNAL_HUMIDITY_AMPLITUDE,
        "seasonal_amplitude * diurnal_to_seasonal_ratio * humidity_diurnal_scale",
        _humidity_diurnal_amplitude_equation,
        (
            _parent(SEASONAL_TEMPERATURE_AMPLITUDE, "seasonal_amplitude",
                    WEATHER_CHUNK_DERIVED_A, 0.4,
                    "seasonal_amplitude_in_declared_bounds", "forward"),
        ),
        (
            ParameterBinding(_P_DIURNAL_TO_SEASONAL_RATIO,
                            "diurnal_to_seasonal_ratio"),
            ParameterBinding(_P_HUMIDITY_DIURNAL_SCALE, "humidity_diurnal_scale"),
        ),
        ("zero_amplitude:zero_output", "positive_amplitude:linear_scaling"),
        (),
        (
            _w("seasonal_amplitude_changes_diurnal_humidity_amplitude",
               SEASONAL_TEMPERATURE_AMPLITUDE,
               ((SEASONAL_TEMPERATURE_AMPLITUDE, 10.0),), 20.0, (4.0, 8.0)),
        ),
    ),
    _mechanism(
        "weather.chunk.derive_precipitation_threshold.v1",
        PRECIPITATION_THRESHOLD,
        "threshold_wet + (threshold_dry - threshold_wet) * "
        "(1 - clamp((annual_rainfall - annual_dry) / (annual_wet - annual_dry), 0, 1))",
        _precip_threshold_equation,
        (
            _parent(ANNUAL_RAINFALL, "annual_rainfall",
                    "world.gen_derived_a", 0.0,
                    "annual_rainfall_in_declared_bounds", "forward"),
        ),
        (
            ParameterBinding(_P_PRECIP_ANNUAL_DRY, "annual_dry"),
            ParameterBinding(_P_PRECIP_ANNUAL_WET, "annual_wet"),
            ParameterBinding(_P_PRECIP_THRESHOLD_DRY, "threshold_dry"),
            ParameterBinding(_P_PRECIP_THRESHOLD_WET, "threshold_wet"),
        ),
        (
            "annual_rainfall_at_or_below_dry_reference:dry_threshold",
            "annual_rainfall_at_or_above_wet_reference:wet_threshold",
            "finite_interior_input:linear_interpolation",
        ),
        (clamp,),
        (
            _w("annual_rainfall_changes_threshold",
               ANNUAL_RAINFALL,
               ((ANNUAL_RAINFALL, 50.0),), 3500.0, (0.55, 0.25)),
        ),
    ),
    _mechanism(
        "weather.tick.derive_day.v1",
        DAY,
        "tick // game_day + 1",
        _day_equation,
        (
            _parent(CLOCK_TICK, "tick", WEATHER_FRAME_INPUT, 0.0,
                    "nonnegative_integer_tick", "forward"),
        ),
        (ParameterBinding(_P_GAME_DAY, "game_day"),),
        ("zero_tick:day_one", "positive_tick:monotonic_day"),
        (),
        (
            _w("tick_changes_day",
               CLOCK_TICK,
               ((CLOCK_TICK, 0),), 172800, (1, 2)),
        ),
    ),
    _mechanism(
        "weather.tick.derive_day_of_year.v1",
        DAY_OF_YEAR,
        "(tick // game_day) % days_per_year",
        _day_of_year_equation,
        (
            _parent(CLOCK_TICK, "tick", WEATHER_FRAME_INPUT, 0.0,
                    "nonnegative_integer_tick", "forward"),
        ),
        (
            ParameterBinding(_P_GAME_DAY, "game_day"),
            ParameterBinding(_P_DAYS_PER_YEAR, "days_per_year"),
        ),
        ("zero_tick:zero", "year_wraparound:modulo"),
        (),
        (
            _w("tick_changes_day_of_year",
               CLOCK_TICK,
               ((CLOCK_TICK, 0),), 172800, (0, 1)),
        ),
    ),
    _mechanism(
        "weather.tick.derive_hour_of_day.v1",
        HOUR_OF_DAY,
        "(tick % game_day) / game_hour",
        _hour_equation,
        (
            _parent(CLOCK_TICK, "tick", WEATHER_FRAME_INPUT, 0.0,
                    "nonnegative_integer_tick", "forward"),
        ),
        (
            ParameterBinding(_P_GAME_DAY, "game_day"),
            ParameterBinding(_P_GAME_HOUR, "game_hour"),
        ),
        ("midnight:zero", "day_wraparound:modulo"),
        (),
        (
            _w("tick_changes_hour",
               CLOCK_TICK,
               ((CLOCK_TICK, 0),), 7200, (0.0, 1.0)),
        ),
    ),
    _mechanism(
        "weather.tick.derive_season.v1",
        SEASON,
        "(day - 1) // season_length_days % seasons_per_year",
        _season_equation,
        (
            _parent(DAY, "day", WEATHER_INSTANT_TICK_INPUT, 0.0,
                    "positive_game_day", "forward"),
        ),
        (
            ParameterBinding(_P_SEASON_LENGTH_DAYS, "season_length_days"),
            ParameterBinding(_P_SEASONS_PER_YEAR, "seasons_per_year"),
        ),
        ("day_one:spring", "season_boundary:index_step"),
        (),
        (
            _w("day_changes_season",
               DAY,
               ((DAY, 1),), 91, (0, 1)),
        ),
    ),
    _mechanism(
        "weather.tick.derive_season_phase_cos.v1",
        SEASON_PHASE_COS,
        "cos(((season + day_of_season / season_length_days) - 1.5) "
        "/ seasons_per_year * 2 * pi)",
        _season_phase_cos_equation,
        (
            _parent(DAY, "day", WEATHER_INSTANT_TICK_INPUT, 0.0,
                    "positive_game_day", "forward"),
        ),
        (
            ParameterBinding(_P_SEASON_LENGTH_DAYS, "season_length_days"),
            ParameterBinding(_P_SEASONS_PER_YEAR, "seasons_per_year"),
        ),
        ("summer_midpoint:cos_one", "winter_midpoint:cos_minus_one"),
        (),
        (
            _w("day_changes_season_phase_cos",
               DAY,
               ((DAY, 46),), 136, (0.0, 1.0)),
        ),
    ),
    _mechanism(
        "weather.tick.derive_diurnal_phase_cos.v1",
        DIURNAL_PHASE_COS,
        "cos((hour - diurnal_peak_hour) / 24 * 2 * pi)",
        _diurnal_phase_cos_equation,
        (
            _parent(HOUR_OF_DAY, "hour", WEATHER_INSTANT_TICK_INPUT, 0.0,
                    "half_open_interval_0_24_hours", "forward"),
        ),
        (ParameterBinding(_P_DIURNAL_PEAK_HOUR, "peak_hour"),),
        ("peak_hour:cos_one", "trough_hour:cos_minus_one"),
        (),
        (
            _w("hour_changes_diurnal_phase_cos",
               HOUR_OF_DAY,
               ((HOUR_OF_DAY, 14.0),), 2.0, (1.0, -1.0)),
        ),
    ),
    _mechanism(
        "weather.tick.derive_solar_declination.v1",
        SOLAR_DECLINATION,
        "radians(obliquity_deg * sin(2 * pi * (day_of_year - "
        "days_per_year / 8) / days_per_year))",
        _solar_declination_equation,
        (
            _parent(DAY_OF_YEAR, "day_of_year", WEATHER_INSTANT_TICK_INPUT, 0.0,
                    "zero_based_day_of_year", "forward"),
        ),
        (
            ParameterBinding(_P_OBLIQUITY_DEG, "obliquity_deg"),
            ParameterBinding(_P_DAYS_PER_YEAR, "days_per_year"),
        ),
        ("equinox:zero", "solstice:max_declination"),
        (),
        (
            _w("day_of_year_changes_declination",
               DAY_OF_YEAR,
               ((DAY_OF_YEAR, 45),), 135,
               (0.0, math.radians(OBLIQUITY_DEG))),
        ),
    ),
    _mechanism(
        "weather.offset.derive_seasonal_temperature.v1",
        SEASONAL_TEMPERATURE_OFFSET,
        "seasonal_amplitude * season_phase_cos",
        _seasonal_temperature_offset_equation,
        (
            _parent(SEASONAL_TEMPERATURE_AMPLITUDE, "amplitude",
                    WEATHER_CHUNK_DERIVED_A, 1.0,
                    "seasonal_amplitude_in_declared_bounds", "forward"),
            _parent(SEASON_PHASE_COS, "season_phase_cos",
                    WEATHER_INSTANT_TICK_DERIVED, 30.0,
                    "cosine_range", "forward"),
        ),
        (),
        ("zero_amplitude:zero_offset", "cosine_extremes:plus_minus_amplitude"),
        (),
        (
            _w("amplitude_changes_seasonal_temperature_offset",
               SEASONAL_TEMPERATURE_AMPLITUDE,
               ((SEASONAL_TEMPERATURE_AMPLITUDE, 10.0),
                (SEASON_PHASE_COS, 1.0)), 20.0, (10.0, 20.0)),
            _w("season_phase_cos_changes_seasonal_temperature_offset",
               SEASON_PHASE_COS,
               ((SEASONAL_TEMPERATURE_AMPLITUDE, 10.0),
                (SEASON_PHASE_COS, 0.5)), 1.0, (5.0, 10.0)),
        ),
    ),
    _mechanism(
        "weather.offset.derive_diurnal_temperature.v1",
        DIURNAL_TEMPERATURE_OFFSET,
        "diurnal_amplitude * diurnal_phase_cos",
        _diurnal_temperature_offset_equation,
        (
            _parent(DIURNAL_TEMPERATURE_AMPLITUDE, "amplitude",
                    WEATHER_CHUNK_DERIVED_B, 1.0,
                    "diurnal_amplitude_in_declared_bounds", "forward"),
            _parent(DIURNAL_PHASE_COS, "diurnal_phase_cos",
                    WEATHER_INSTANT_TICK_DERIVED, 20.0,
                    "cosine_range", "forward"),
        ),
        (),
        ("zero_amplitude:zero_offset", "cosine_extremes:plus_minus_amplitude"),
        (),
        (
            _w("amplitude_changes_diurnal_temperature_offset",
               DIURNAL_TEMPERATURE_AMPLITUDE,
               ((DIURNAL_TEMPERATURE_AMPLITUDE, 5.0),
                (DIURNAL_PHASE_COS, 1.0)), 10.0, (5.0, 10.0)),
            _w("diurnal_phase_cos_changes_diurnal_temperature_offset",
               DIURNAL_PHASE_COS,
               ((DIURNAL_TEMPERATURE_AMPLITUDE, 5.0),
                (DIURNAL_PHASE_COS, 0.5)), 1.0, (2.5, 5.0)),
        ),
    ),
    _mechanism(
        "weather.offset.derive_seasonal_humidity.v1",
        SEASONAL_HUMIDITY_OFFSET,
        "seasonal_humidity_amplitude * (tanh(season_phase_cos * sharpness) "
        "if sharpness > 0 else season_phase_cos)",
        _seasonal_humidity_offset_equation,
        (
            _parent(SEASONAL_HUMIDITY_AMPLITUDE, "amplitude",
                    WEATHER_CHUNK_DERIVED_B, 1.0,
                    "humidity_amplitude_in_declared_bounds", "forward"),
            _parent(SEASON_PHASE_COS, "season_phase_cos",
                    WEATHER_INSTANT_TICK_DERIVED, 20.0,
                    "cosine_range", "forward"),
            _parent(HUMIDITY_SHARPNESS, "sharpness",
                    "world.gen_derived_d", 0.0,
                    "nonnegative_sharpness", "forward"),
        ),
        (),
        ("zero_amplitude:zero_offset", "sharpness_zero:cosine",
         "sharpness_positive:tanh_compression"),
        (),
        (
            _w("amplitude_changes_seasonal_humidity_offset",
               SEASONAL_HUMIDITY_AMPLITUDE,
               ((SEASONAL_HUMIDITY_AMPLITUDE, 4.0),
                (SEASON_PHASE_COS, 1.0), (HUMIDITY_SHARPNESS, 0.0)),
               8.0, (4.0, 8.0)),
            _w("season_phase_cos_changes_seasonal_humidity_offset",
               SEASON_PHASE_COS,
               ((SEASONAL_HUMIDITY_AMPLITUDE, 4.0),
                (SEASON_PHASE_COS, 0.5), (HUMIDITY_SHARPNESS, 0.0)),
               1.0, (2.0, 4.0)),
            _w("sharpness_changes_seasonal_humidity_offset",
               HUMIDITY_SHARPNESS,
               ((SEASONAL_HUMIDITY_AMPLITUDE, 4.0),
                (SEASON_PHASE_COS, 0.5), (HUMIDITY_SHARPNESS, 0.0)),
               2.5, (2.0, 4.0 * math.tanh(0.5 * 2.5))),
        ),
    ),
    _mechanism(
        "weather.offset.derive_diurnal_humidity.v1",
        DIURNAL_HUMIDITY_OFFSET,
        "diurnal_humidity_amplitude * (-diurnal_phase_cos)",
        _diurnal_humidity_offset_equation,
        (
            _parent(DIURNAL_HUMIDITY_AMPLITUDE, "amplitude",
                    WEATHER_CHUNK_DERIVED_B, 1.0,
                    "humidity_amplitude_in_declared_bounds", "forward"),
            _parent(DIURNAL_PHASE_COS, "diurnal_phase_cos",
                    WEATHER_INSTANT_TICK_DERIVED, 20.0,
                    "cosine_range", "forward"),
        ),
        (),
        ("zero_amplitude:zero_offset", "cosine_extremes:inverted"),
        (),
        (
            _w("amplitude_changes_diurnal_humidity_offset",
               DIURNAL_HUMIDITY_AMPLITUDE,
               ((DIURNAL_HUMIDITY_AMPLITUDE, 4.0),
                (DIURNAL_PHASE_COS, 1.0)), 8.0, (-4.0, -8.0)),
            _w("diurnal_phase_cos_changes_diurnal_humidity_offset",
               DIURNAL_PHASE_COS,
               ((DIURNAL_HUMIDITY_AMPLITUDE, 4.0),
                (DIURNAL_PHASE_COS, 0.5)), 1.0, (-2.0, -4.0)),
        ),
    ),
    _mechanism(
        "weather.astronomy.derive_sunrise.v1",
        SUNRISE_HOUR,
        "12 - degrees(acos(-clamp(tan(radians(latitude)) * "
        "tan(solar_declination), -1, 1))) / 15",
        _sunrise_equation,
        (
            _parent(SOLAR_LATITUDE_PROXY, "latitude",
                    WEATHER_CHUNK_DERIVED_A, 0.5,
                    "closed_interval_0_80_degrees", "forward"),
            _parent(SOLAR_DECLINATION, "solar_declination",
                    WEATHER_INSTANT_TICK_DERIVED, 1.0,
                    "solar_declination_range", "forward"),
        ),
        (),
        ("equinox:twelve_noon_correction_zero", "polar_day:sunrise_zero",
         "polar_night:sunrise_twelve"),
        (),
        (
            _w("latitude_changes_sunrise",
               SOLAR_LATITUDE_PROXY,
               ((SOLAR_LATITUDE_PROXY, 0.0), (SOLAR_DECLINATION, 0.4091)),
               80.0, (6.0, 0.0)),
            _w("solar_declination_changes_sunrise",
               SOLAR_DECLINATION,
               ((SOLAR_LATITUDE_PROXY, 80.0), (SOLAR_DECLINATION, 0.0)),
               0.4091, (6.0, 0.0)),
        ),
    ),
    _mechanism(
        "weather.astronomy.derive_sunset.v1",
        SUNSET_HOUR,
        "12 + degrees(acos(-clamp(tan(radians(latitude)) * "
        "tan(solar_declination), -1, 1))) / 15",
        _sunset_equation,
        (
            _parent(SOLAR_LATITUDE_PROXY, "latitude",
                    WEATHER_CHUNK_DERIVED_A, 0.5,
                    "closed_interval_0_80_degrees", "forward"),
            _parent(SOLAR_DECLINATION, "solar_declination",
                    WEATHER_INSTANT_TICK_DERIVED, 1.0,
                    "solar_declination_range", "forward"),
        ),
        (),
        ("equinox:twelve_noon_correction_zero", "polar_day:sunset_twenty_four",
         "polar_night:sunset_twelve"),
        (),
        (
            _w("latitude_changes_sunset",
               SOLAR_LATITUDE_PROXY,
               ((SOLAR_LATITUDE_PROXY, 0.0), (SOLAR_DECLINATION, 0.4091)),
               80.0, (18.0, 24.0)),
            _w("solar_declination_changes_sunset",
               SOLAR_DECLINATION,
               ((SOLAR_LATITUDE_PROXY, 80.0), (SOLAR_DECLINATION, 0.0)),
               0.4091, (18.0, 24.0)),
        ),
    ),
    _mechanism(
        "weather.astronomy.derive_daylight.v1",
        DAYLIGHT_HOURS,
        "sunset - sunrise",
        _daylight_equation,
        (
            _parent(SUNRISE_HOUR, "sunrise", WEATHER_INSTANT_OFFSET, 1.0,
                    "half_open_interval_0_12_hours", "forward"),
            _parent(SUNSET_HOUR, "sunset", WEATHER_INSTANT_OFFSET, 1.0,
                    "half_open_interval_12_24_hours", "forward"),
        ),
        (),
        ("polar_night:zero_daylight", "polar_day:twenty_four_hours"),
        (),
        (
            _w("sunrise_changes_daylight",
               SUNRISE_HOUR,
               ((SUNRISE_HOUR, 6.0), (SUNSET_HOUR, 18.0)),
               8.0, (12.0, 10.0)),
            _w("sunset_changes_daylight",
               SUNSET_HOUR,
               ((SUNRISE_HOUR, 6.0), (SUNSET_HOUR, 18.0)),
               20.0, (12.0, 14.0)),
        ),
    ),
    _mechanism(
        "weather.instant.compose_temperature.v1",
        INSTANT_TEMPERATURE,
        "clamp(annual_temperature + seasonal_offset + diurnal_offset + "
        "perturbation * perturb_scale, lower_bound, upper_bound)",
        _temperature_equation,
        (
            _parent(ANNUAL_TEMPERATURE, "annual_temperature",
                    "world.gen_derived_b", 1.0,
                    "temperature_in_declared_bounds", "forward"),
            _parent(SEASONAL_TEMPERATURE_OFFSET, "seasonal_offset",
                    WEATHER_INSTANT_OFFSET, 1.0,
                    "bounded_by_seasonal_amplitude", "forward"),
            _parent(DIURNAL_TEMPERATURE_OFFSET, "diurnal_offset",
                    WEATHER_INSTANT_OFFSET, 1.0,
                    "bounded_by_diurnal_amplitude", "forward"),
            _parent(FIELD_TEMPERATURE_PERTURBATION, "perturbation",
                    WEATHER_FRAME_INPUT, 5.0,
                    "unified_field_channel_composite", "forward"),
        ),
        (
            ParameterBinding(_P_TEMP_PERTURB_SCALE, "perturb_scale"),
            ParameterBinding(_P_TEMP_BOUND_LO, "lower_bound"),
            ParameterBinding(_P_TEMP_BOUND_HI, "upper_bound"),
        ),
        ("sum_below_lower_bound:clamp_low", "sum_above_upper_bound:clamp_high",
         "finite_interior:no_clamp"),
        (clamp,),
        (
            _w("annual_temperature_changes_instant_temperature",
               ANNUAL_TEMPERATURE,
               ((ANNUAL_TEMPERATURE, 10.0),
                (SEASONAL_TEMPERATURE_OFFSET, 0.0),
                (DIURNAL_TEMPERATURE_OFFSET, 0.0),
                (FIELD_TEMPERATURE_PERTURBATION, 0.0)),
               20.0, (10.0, 20.0)),
            _w("seasonal_offset_changes_instant_temperature",
               SEASONAL_TEMPERATURE_OFFSET,
               ((ANNUAL_TEMPERATURE, 10.0),
                (SEASONAL_TEMPERATURE_OFFSET, 0.0),
                (DIURNAL_TEMPERATURE_OFFSET, 0.0),
                (FIELD_TEMPERATURE_PERTURBATION, 0.0)),
               5.0, (10.0, 15.0)),
            _w("diurnal_offset_changes_instant_temperature",
               DIURNAL_TEMPERATURE_OFFSET,
               ((ANNUAL_TEMPERATURE, 10.0),
                (SEASONAL_TEMPERATURE_OFFSET, 0.0),
                (DIURNAL_TEMPERATURE_OFFSET, 0.0),
                (FIELD_TEMPERATURE_PERTURBATION, 0.0)),
               5.0, (10.0, 15.0)),
            _w("perturbation_changes_instant_temperature",
               FIELD_TEMPERATURE_PERTURBATION,
               ((ANNUAL_TEMPERATURE, 10.0),
                (SEASONAL_TEMPERATURE_OFFSET, 0.0),
                (DIURNAL_TEMPERATURE_OFFSET, 0.0),
                (FIELD_TEMPERATURE_PERTURBATION, 0.0)),
               1.0, (10.0, 15.0)),
        ),
    ),
    _mechanism(
        "weather.instant.compose_humidity.v1",
        INSTANT_HUMIDITY,
        "clamp(baseline + seasonal_offset + diurnal_offset + "
        "perturbation * perturb_scale, lower_bound, upper_bound)",
        _humidity_equation,
        (
            _parent(BASELINE_HUMIDITY, "baseline", "world.gen_derived_d", 1.0,
                    "closed_interval_0_100_percent", "forward"),
            _parent(SEASONAL_HUMIDITY_OFFSET, "seasonal_offset",
                    WEATHER_INSTANT_OFFSET, 1.0,
                    "bounded_by_humidity_amplitude", "forward"),
            _parent(DIURNAL_HUMIDITY_OFFSET, "diurnal_offset",
                    WEATHER_INSTANT_OFFSET, 1.0,
                    "bounded_by_humidity_amplitude", "forward"),
            _parent(FIELD_HUMIDITY_PERTURBATION, "perturbation",
                    WEATHER_FRAME_INPUT, 15.0,
                    "unified_field_channel_composite", "forward"),
        ),
        (
            ParameterBinding(_P_HUMIDITY_PERTURB_SCALE, "perturb_scale"),
            ParameterBinding(_P_HUMIDITY_BOUND_LO, "lower_bound"),
            ParameterBinding(_P_HUMIDITY_BOUND_HI, "upper_bound"),
        ),
        ("sum_below_lower_bound:clamp_low", "sum_above_upper_bound:clamp_high",
         "finite_interior:no_clamp"),
        (clamp,),
        (
            _w("baseline_changes_instant_humidity",
               BASELINE_HUMIDITY,
               ((BASELINE_HUMIDITY, 60.0),
                (SEASONAL_HUMIDITY_OFFSET, 0.0),
                (DIURNAL_HUMIDITY_OFFSET, 0.0),
                (FIELD_HUMIDITY_PERTURBATION, 0.0)),
               70.0, (60.0, 70.0)),
            _w("seasonal_offset_changes_instant_humidity",
               SEASONAL_HUMIDITY_OFFSET,
               ((BASELINE_HUMIDITY, 60.0),
                (SEASONAL_HUMIDITY_OFFSET, 0.0),
                (DIURNAL_HUMIDITY_OFFSET, 0.0),
                (FIELD_HUMIDITY_PERTURBATION, 0.0)),
               5.0, (60.0, 65.0)),
            _w("diurnal_offset_changes_instant_humidity",
               DIURNAL_HUMIDITY_OFFSET,
               ((BASELINE_HUMIDITY, 60.0),
                (SEASONAL_HUMIDITY_OFFSET, 0.0),
                (DIURNAL_HUMIDITY_OFFSET, 0.0),
                (FIELD_HUMIDITY_PERTURBATION, 0.0)),
               5.0, (60.0, 65.0)),
            _w("perturbation_changes_instant_humidity",
               FIELD_HUMIDITY_PERTURBATION,
               ((BASELINE_HUMIDITY, 60.0),
                (SEASONAL_HUMIDITY_OFFSET, 0.0),
                (DIURNAL_HUMIDITY_OFFSET, 0.0),
                (FIELD_HUMIDITY_PERTURBATION, 0.0)),
               1.0, (60.0, 75.0)),
        ),
    ),
    _mechanism(
        "weather.instant.compose_wind_speed.v1",
        INSTANT_WIND_SPEED,
        "clamp(clamp(baseline + perturbation * perturb_scale, "
        "lower_bound, upper_bound) * multiplier, lower_bound, upper_bound)",
        _wind_equation,
        (
            _parent(BASELINE_WIND_SPEED, "baseline", "world.gen_derived_d", 1.0,
                    "closed_interval_0_50_mps", "forward"),
            _parent(FIELD_WIND_PERTURBATION, "perturbation",
                    WEATHER_FRAME_INPUT, 4.0,
                    "unified_field_channel_composite", "forward"),
            _parent(FIELD_WIND_MULTIPLIER, "multiplier",
                    WEATHER_FRAME_INPUT, 0.0,
                    "at_least_one_multiplier", "forward"),
        ),
        (
            ParameterBinding(_P_WIND_PERTURB_SCALE, "perturb_scale"),
            ParameterBinding(_P_WIND_BOUND_LO, "lower_bound"),
            ParameterBinding(_P_WIND_BOUND_HI, "upper_bound"),
        ),
        ("base_below_lower_bound:clamp_low", "base_above_upper_bound:clamp_high",
         "product_above_upper_bound:second_clamp"),
        (clamp,),
        (
            _w("baseline_changes_instant_wind",
               BASELINE_WIND_SPEED,
               ((BASELINE_WIND_SPEED, 5.0),
                (FIELD_WIND_PERTURBATION, 0.0),
                (FIELD_WIND_MULTIPLIER, 1.0)),
               10.0, (5.0, 10.0)),
            _w("perturbation_changes_instant_wind",
               FIELD_WIND_PERTURBATION,
               ((BASELINE_WIND_SPEED, 5.0),
                (FIELD_WIND_PERTURBATION, 0.0),
                (FIELD_WIND_MULTIPLIER, 1.0)),
               1.0, (5.0, 9.0)),
            _w("multiplier_changes_instant_wind",
               FIELD_WIND_MULTIPLIER,
               ((BASELINE_WIND_SPEED, 5.0),
                (FIELD_WIND_PERTURBATION, 0.0),
                (FIELD_WIND_MULTIPLIER, 1.0)),
               2.0, (5.0, 10.0)),
        ),
    ),
    _mechanism(
        "weather.instant.compose_precipitation_intensity.v1",
        INSTANT_PRECIPITATION_INTENSITY,
        "0 if signal <= threshold else "
        "(min(signal, signal_max) - threshold) * intensity_scale "
        "* mean_intensity",
        _precipitation_intensity_equation,
        (
            _parent(FIELD_PRECIPITATION_SIGNAL, "signal",
                    WEATHER_FRAME_INPUT, 0.0,
                    "unified_field_channel_composite", "forward"),
            _parent(PRECIPITATION_THRESHOLD, "threshold",
                    WEATHER_CHUNK_DERIVED_A, 0.0,
                    "closed_interval_wet_to_dry", "forward"),
            _parent(MEAN_PRECIP_INTENSITY, "mean_intensity",
                    "world.gen_derived_d", 0.0,
                    "positive_mean_intensity", "forward"),
        ),
        (
            ParameterBinding(_P_PRECIP_SIGNAL_MAX, "signal_max"),
            ParameterBinding(_P_PRECIP_INTENSITY_SCALE, "intensity_scale"),
        ),
        ("signal_at_or_below_threshold:zero", "signal_above_signal_max:cap",
         "finite_interior:linear_scaling"),
        (),
        (
            _w("signal_changes_precipitation_intensity",
               FIELD_PRECIPITATION_SIGNAL,
                ((FIELD_PRECIPITATION_SIGNAL, 0.5),
                 (PRECIPITATION_THRESHOLD, 0.4),
                 (MEAN_PRECIP_INTENSITY, 5.0)),
               1.0, (1.0, 6.0)),
            _w("threshold_changes_precipitation_intensity",
               PRECIPITATION_THRESHOLD,
               ((FIELD_PRECIPITATION_SIGNAL, 0.5),
                (PRECIPITATION_THRESHOLD, 0.4),
                (MEAN_PRECIP_INTENSITY, 5.0)),
               0.45, (1.0, 0.5)),
            _w("mean_intensity_changes_precipitation_intensity",
               MEAN_PRECIP_INTENSITY,
               ((FIELD_PRECIPITATION_SIGNAL, 0.5),
                (PRECIPITATION_THRESHOLD, 0.4),
                (MEAN_PRECIP_INTENSITY, 5.0)),
               10.0, (1.0, 2.0)),
        ),
    ),
    _mechanism(
        "weather.instant.compose_sunshine.v1",
        INSTANT_SUNSHINE,
        "clamp(daylight_hours + perturbation * perturb_scale, "
        "lower_bound, upper_bound)",
        _sunshine_equation,
        (
            _parent(DAYLIGHT_HOURS, "daylight_hours",
                    WEATHER_INSTANT_COMPOSITE, 1.0,
                    "closed_interval_0_24_hours", "forward"),
            _parent(FIELD_HUMIDITY_PERTURBATION, "perturbation",
                    WEATHER_FRAME_INPUT, 1.5,
                    "unified_field_channel_composite", "forward"),
        ),
        (
            ParameterBinding(_P_SUNSHINE_PERTURB_SCALE, "perturb_scale"),
            ParameterBinding(_P_SUNSHINE_BOUND_LO, "lower_bound"),
            ParameterBinding(_P_SUNSHINE_BOUND_HI, "upper_bound"),
        ),
        ("sum_below_lower_bound:clamp_low", "sum_above_upper_bound:clamp_high",
         "finite_interior:no_clamp"),
        (clamp,),
        (
            _w("daylight_changes_sunshine",
               DAYLIGHT_HOURS,
               ((DAYLIGHT_HOURS, 12.0), (FIELD_HUMIDITY_PERTURBATION, 0.0)),
               16.0, (12.0, 16.0)),
            _w("perturbation_changes_sunshine",
               FIELD_HUMIDITY_PERTURBATION,
               ((DAYLIGHT_HOURS, 12.0), (FIELD_HUMIDITY_PERTURBATION, 0.0)),
               1.0, (12.0, 13.5)),
        ),
    ),
    _mechanism(
        "weather.instant.classify_precipitation_type.v1",
        INSTANT_PRECIPITATION_TYPE,
        "'snow' if round_half_even(instant_temperature, 1) <= 0 else 'rain'",
        _precipitation_type_equation,
        (
            _parent(INSTANT_TEMPERATURE, "instant_temperature",
                    WEATHER_INSTANT_COMPOSITE, 0.0,
                    "finite_temperature_within_declared_bounds", "forward"),
        ),
        (),
        ("rounded_temperature_at_or_below_0:snow",
         "rounded_temperature_above_0:rain"),
        (),
        (
            _w("temperature_changes_precipitation_type",
               INSTANT_TEMPERATURE,
               ((INSTANT_TEMPERATURE, -1.0),), 1.0, ("snow", "rain")),
        ),
    ),
)

WEATHER_NODES = _NODES
WEATHER_PARAMETERS = _PARAMETERS
WEATHER_MECHANISM_SPECS = _MECHANISMS

__all__ = [
    "ANNUAL_RAINFALL",
    "ANNUAL_TEMPERATURE",
    "BASELINE_HUMIDITY",
    "BASELINE_WIND_SPEED",
    "CLOCK_TICK",
    "DAY",
    "DAY_OF_YEAR",
    "DAYLIGHT_HOURS",
    "DIURNAL_HUMIDITY_AMPLITUDE",
    "DIURNAL_HUMIDITY_OFFSET",
    "DIURNAL_PHASE_COS",
    "DIURNAL_TEMPERATURE_AMPLITUDE",
    "DIURNAL_TEMPERATURE_OFFSET",
    "FIELD_HUMIDITY_PERTURBATION",
    "FIELD_PRECIPITATION_SIGNAL",
    "FIELD_TEMPERATURE_PERTURBATION",
    "FIELD_WIND_MULTIPLIER",
    "FIELD_WIND_PERTURBATION",
    "HOUR_OF_DAY",
    "HUMIDITY_SHARPNESS",
    "INSTANT_HUMIDITY",
    "INSTANT_PRECIPITATION_INTENSITY",
    "INSTANT_PRECIPITATION_TYPE",
    "INSTANT_SUNSHINE",
    "INSTANT_TEMPERATURE",
    "INSTANT_WIND_SPEED",
    "MEAN_PRECIP_INTENSITY",
    "PRECIPITATION_THRESHOLD",
    "SEASON",
    "SEASON_PHASE_COS",
    "SEASONAL_HUMIDITY_AMPLITUDE",
    "SEASONAL_HUMIDITY_OFFSET",
    "SEASONAL_TEMPERATURE_AMPLITUDE",
    "SEASONAL_TEMPERATURE_OFFSET",
    "SEA_LEVEL_TEMPERATURE",
    "SOLAR_DECLINATION",
    "SOLAR_LATITUDE_PROXY",
    "SUNRISE_HOUR",
    "SUNSET_HOUR",
    "WEATHER_MECHANISM_SPECS",
    "WEATHER_NODES",
    "WEATHER_PARAMETERS",
]
