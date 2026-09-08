"""天气机制声明的生产单一事实来源。"""

from __future__ import annotations

import hashlib
import json

from ascend.causal import (
    AccessPolicy,
    DependencyWitness,
    InstanceDomain,
    MathMetadata,
    MechanismRegistry,
    MechanismSpec,
    NodeSpec,
    ParameterBinding,
    ParameterSpec,
    ParentSpec,
    StateOwnership,
    UpdateContract,
    ValueDomain,
)
from ascend.config import (
    LATITUDE_MAX,
    LATITUDE_MIN,
    LATITUDE_T_MAX,
    LATITUDE_T_MIN,
    SEASONAL_AMP_BOUNDS,
    SEASONAL_AMP_MAX,
    SEASONAL_AMP_MIN,
    SEASONAL_AMP_R_BONUS,
    SEASONAL_AMP_R_REF,
    SEASONAL_AMP_T_MAX,
    SEASONAL_AMP_T_MIN,
    TEMP_BOUNDS,
)
from ascend.mathutil import clamp

SEA_LEVEL_TEMPERATURE = "weather.chunk.sea_level_temperature_c"
ANNUAL_TEMPERATURE = "weather.chunk.annual_mean_temperature_c"
ANNUAL_RAINFALL = "weather.chunk.annual_rainfall_mm_per_year"
SOLAR_LATITUDE_PROXY = "weather.chunk.solar_latitude_proxy_deg"
SEASONAL_TEMPERATURE_AMPLITUDE = (
    "weather.chunk.seasonal_temperature_amplitude_c"
)
INSTANT_TEMPERATURE = "weather.instant.temperature_c"
INSTANT_PRECIPITATION_TYPE = "weather.instant.precipitation_type"

_LAT_T_MIN = "weather.parameter.latitude.input_min_c"
_LAT_T_MAX = "weather.parameter.latitude.input_max_c"
_LAT_MIN = "weather.parameter.latitude.output_min_deg"
_LAT_MAX = "weather.parameter.latitude.output_max_deg"
_AMP_T_MIN = "weather.parameter.seasonal_amplitude.input_min_c"
_AMP_T_MAX = "weather.parameter.seasonal_amplitude.input_max_c"
_AMP_MAX = "weather.parameter.seasonal_amplitude.cold_endpoint_c"
_AMP_MIN = "weather.parameter.seasonal_amplitude.hot_endpoint_c"
_AMP_R_REF = "weather.parameter.seasonal_amplitude.rain_reference_mm_per_year"
_AMP_R_BONUS = "weather.parameter.seasonal_amplitude.rain_bonus_c"
_AMP_BOUND_MIN = "weather.parameter.seasonal_amplitude.output_min_c"
_AMP_BOUND_MAX = "weather.parameter.seasonal_amplitude.output_max_c"

_MICROSTEPS = (
    "weather.chunk_input",
    "weather.chunk_derived",
    "weather.instant_input",
    "weather.instant_derived",
)
_CHUNK_INSTANCE = InstanceDomain(
    kind="spatial_field",
    axes=("chunk_x", "chunk_y"),
    creation="chunk_registration",
    destruction="chunk_unregistration",
)
_RESEARCH_AND_AGENT_ACCESS = AccessPolicy(
    interventions=("node", "persistent", "mechanism"),
    research_trace=True,
    observation_protocols=("research.full.v1", "agent.weather.v1"),
)


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
    source_name: str,
) -> ParameterSpec:
    source = f"data/world.json#weather.{source_name}"
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
) -> NodeSpec:
    return NodeSpec(
        node_id=node_id,
        role=role,
        origin=origin,
        instance_domain=_CHUNK_INSTANCE,
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
        access=_RESEARCH_AND_AGENT_ACCESS,
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


def _precipitation_type_equation(instant_temperature: float) -> str:
    return "snow" if round(instant_temperature, 1) <= 0 else "rain"


_NODES = (
    _node(
        SEA_LEVEL_TEMPERATURE,
        role="persistent_state",
        origin="slice_boundary",
        kind="float",
        unit="degC",
        bounds=TEMP_BOUNDS,
        choices=(),
        quantization="ieee754_binary64",
        in_world_state=True,
        reconstruction="not_applicable:persisted_chunk_generation_output",
        schedule="on_chunk_generation",
        microstep="weather.chunk_input",
        writer="space.world_generator",
        error_budget=0.2,
        valid_domain="finite_temperature_within_declared_bounds",
    ),
    _node(
        ANNUAL_TEMPERATURE,
        role="persistent_state",
        origin="slice_boundary",
        kind="float",
        unit="degC",
        bounds=TEMP_BOUNDS,
        choices=(),
        quantization="ieee754_binary64",
        in_world_state=True,
        reconstruction="not_applicable:persisted_chunk_generation_output",
        schedule="on_chunk_generation",
        microstep="weather.chunk_input",
        writer="space.world_generator",
        error_budget=0.05,
        valid_domain="finite_temperature_within_declared_bounds",
    ),
    _node(
        ANNUAL_RAINFALL,
        role="persistent_state",
        origin="slice_boundary",
        kind="float",
        unit="mm_per_year",
        bounds=(0.0, 5000.0),
        choices=(),
        quantization="ieee754_binary64",
        in_world_state=True,
        reconstruction="not_applicable:persisted_chunk_generation_output",
        schedule="on_chunk_generation",
        microstep="weather.chunk_input",
        writer="space.world_generator",
        error_budget=100.0,
        valid_domain="finite_nonnegative_annual_rainfall",
    ),
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
        microstep="weather.chunk_derived",
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
        microstep="weather.chunk_derived",
        writer="weather.chunk.derive_seasonal_temperature_amplitude.v1",
        error_budget=0.1,
        valid_domain="closed_interval_1_30_degC",
    ),
    _node(
        INSTANT_TEMPERATURE,
        role="mechanism_state",
        origin="slice_boundary",
        kind="float",
        unit="degC",
        bounds=TEMP_BOUNDS,
        choices=(),
        quantization="ieee754_binary64",
        in_world_state=False,
        reconstruction="outside_slice:weather.instant.temperature_equation",
        schedule="on_weather_evaluation",
        microstep="weather.instant_input",
        writer="outside_slice:weather.instant.temperature_equation",
        error_budget=0.05,
        valid_domain="finite_temperature_within_declared_bounds",
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
        microstep="weather.instant_derived",
        writer="weather.instant.classify_precipitation_type.v1",
        error_budget=0.0,
        valid_domain="snow_or_rain",
    ),
)

_PARAMETERS = (
    _parameter(_LAT_T_MIN, LATITUDE_T_MIN, "degC", (-273.15, 100.0), "LATITUDE_T_MIN"),
    _parameter(_LAT_T_MAX, LATITUDE_T_MAX, "degC", (-273.15, 100.0), "LATITUDE_T_MAX"),
    _parameter(_LAT_MIN, LATITUDE_MIN, "degree_north", (0.0, 90.0), "LATITUDE_MIN"),
    _parameter(_LAT_MAX, LATITUDE_MAX, "degree_north", (0.0, 90.0), "LATITUDE_MAX"),
    _parameter(_AMP_T_MIN, SEASONAL_AMP_T_MIN, "degC", (-273.15, 100.0), "SEASONAL_AMP_T_MIN"),
    _parameter(_AMP_T_MAX, SEASONAL_AMP_T_MAX, "degC", (-273.15, 100.0), "SEASONAL_AMP_T_MAX"),
    _parameter(_AMP_MAX, SEASONAL_AMP_MAX, "degC", (0.0, 100.0), "SEASONAL_AMP_MAX"),
    _parameter(_AMP_MIN, SEASONAL_AMP_MIN, "degC", (0.0, 100.0), "SEASONAL_AMP_MIN"),
    _parameter(
        _AMP_R_REF,
        SEASONAL_AMP_R_REF,
        "mm_per_year",
        (1.0, 100000.0),
        "SEASONAL_AMP_R_REF",
    ),
    _parameter(_AMP_R_BONUS, SEASONAL_AMP_R_BONUS, "degC", (0.0, 100.0), "SEASONAL_AMP_R_BONUS"),
    _parameter(_AMP_BOUND_MIN, SEASONAL_AMP_BOUNDS[0], "degC", (0.0, 100.0), "SEASONAL_AMP_BOUNDS[0]"),
    _parameter(_AMP_BOUND_MAX, SEASONAL_AMP_BOUNDS[1], "degC", (0.0, 100.0), "SEASONAL_AMP_BOUNDS[1]"),
)

_MECHANISMS = (
    MechanismSpec(
        mechanism_id="weather.chunk.derive_solar_latitude_proxy.v1",
        output=SOLAR_LATITUDE_PROXY,
        equation=(
            "clamp(output_max - (sea_level_temperature - input_min) / "
            "(input_max - input_min) * (output_max - output_min), "
            "output_min, output_max)"
        ),
        function=_derive_latitude_equation,
        parents=(
            _parent(
                SEA_LEVEL_TEMPERATURE,
                "sea_level_temperature",
                "weather.chunk_input",
                2.0,
                "temperature_in_-30_50_degC",
                "inverse",
            ),
        ),
        parameters=(
            ParameterBinding(_LAT_T_MIN, "input_min"),
            ParameterBinding(_LAT_T_MAX, "input_max"),
            ParameterBinding(_LAT_MIN, "output_min"),
            ParameterBinding(_LAT_MAX, "output_max"),
        ),
        random_sources=(),
        boundary_cases=(
            "sea_level_temperature_at_or_below_input_min:output_max",
            "sea_level_temperature_at_or_above_input_max:output_min",
            "finite_interior_input:linear_interpolation",
        ),
        source_dependencies=(clamp,),
        witnesses=(
            DependencyWitness(
                label="sea_level_temperature_changes_latitude",
                parent=SEA_LEVEL_TEMPERATURE,
                inputs=((SEA_LEVEL_TEMPERATURE, -5.0),),
                alternate_value=35.0,
                expected_outputs=(80.0, 0.0),
            ),
        ),
    ),
    MechanismSpec(
        mechanism_id="weather.chunk.derive_seasonal_temperature_amplitude.v1",
        output=SEASONAL_TEMPERATURE_AMPLITUDE,
        equation=(
            "clamp(cold_endpoint - (annual_temperature - input_temp_min) / "
            "(input_temp_max - input_temp_min) * "
            "(cold_endpoint - hot_endpoint) + "
            "clamp((rain_reference - annual_rainfall) / rain_reference, "
            "-0.5, 1.0) * rain_bonus_scale, output_min, output_max)"
        ),
        function=_derive_seasonal_amplitude_equation,
        parents=(
            _parent(
                ANNUAL_TEMPERATURE,
                "annual_temperature",
                "weather.chunk_input",
                0.65,
                "temperature_in_-30_50_degC",
                "inverse",
            ),
            _parent(
                ANNUAL_RAINFALL,
                "annual_rainfall",
                "weather.chunk_input",
                0.002,
                "annual_rainfall_in_0_5000_mm",
                "inverse",
            ),
        ),
        parameters=(
            ParameterBinding(_AMP_T_MIN, "input_temp_min"),
            ParameterBinding(_AMP_T_MAX, "input_temp_max"),
            ParameterBinding(_AMP_MAX, "cold_endpoint"),
            ParameterBinding(_AMP_MIN, "hot_endpoint"),
            ParameterBinding(_AMP_R_REF, "rain_reference"),
            ParameterBinding(_AMP_R_BONUS, "rain_bonus_scale"),
            ParameterBinding(_AMP_BOUND_MIN, "output_min"),
            ParameterBinding(_AMP_BOUND_MAX, "output_max"),
        ),
        random_sources=(),
        boundary_cases=(
            "rain_factor_below_-0.5:clamp_to_-0.5",
            "rain_factor_above_1.0:clamp_to_1.0",
            "amplitude_below_output_min:clamp_to_output_min",
            "amplitude_above_output_max:clamp_to_output_max",
            "finite_interior_inputs:continuous_formula",
        ),
        source_dependencies=(clamp,),
        witnesses=(
            DependencyWitness(
                label="annual_temperature_changes_amplitude",
                parent=ANNUAL_TEMPERATURE,
                inputs=(
                    (ANNUAL_TEMPERATURE, 0.0),
                    (ANNUAL_RAINFALL, 800.0),
                ),
                alternate_value=20.0,
                expected_outputs=(27.15, 14.15),
            ),
            DependencyWitness(
                label="annual_rainfall_changes_amplitude",
                parent=ANNUAL_RAINFALL,
                inputs=(
                    (ANNUAL_TEMPERATURE, 15.0),
                    (ANNUAL_RAINFALL, 200.0),
                ),
                alternate_value=2000.0,
                expected_outputs=(18.6, 15.0),
            ),
        ),
    ),
    MechanismSpec(
        mechanism_id="weather.instant.classify_precipitation_type.v1",
        output=INSTANT_PRECIPITATION_TYPE,
        equation=(
            "'snow' if round_half_even(instant_temperature, 1) <= 0 "
            "else 'rain'"
        ),
        function=_precipitation_type_equation,
        parents=(
            _parent(
                INSTANT_TEMPERATURE,
                "instant_temperature",
                "weather.instant_input",
                0.0,
                "temperature_in_-30_50_degC",
                "forward",
            ),
        ),
        parameters=(),
        random_sources=(),
        boundary_cases=(
            "rounded_temperature_at_or_below_0:snow",
            "rounded_temperature_above_0:rain",
        ),
        source_dependencies=(),
        witnesses=(
            DependencyWitness(
                label="temperature_changes_precipitation_type",
                parent=INSTANT_TEMPERATURE,
                inputs=((INSTANT_TEMPERATURE, -1.0),),
                alternate_value=1.0,
                expected_outputs=("snow", "rain"),
            ),
        ),
    ),
)

WEATHER_MECHANISMS = MechanismRegistry(
    schema_version=2,
    declaration_id="ascend.weather.derived_slice",
    declaration_version="1",
    microstep_order=_MICROSTEPS,
    slice_boundary=(
        "Chunk generation supplies annual fields; the not-yet-migrated instant "
        "temperature mechanism supplies weather.instant.temperature_c."
    ),
    nodes=_NODES,
    parameters=_PARAMETERS,
    exogenous_sources=(),
    mechanisms=_MECHANISMS,
)

__all__ = [
    "ANNUAL_RAINFALL",
    "ANNUAL_TEMPERATURE",
    "INSTANT_PRECIPITATION_TYPE",
    "INSTANT_TEMPERATURE",
    "SEASONAL_TEMPERATURE_AMPLITUDE",
    "SEA_LEVEL_TEMPERATURE",
    "SOLAR_LATITUDE_PROXY",
    "WEATHER_MECHANISMS",
]
