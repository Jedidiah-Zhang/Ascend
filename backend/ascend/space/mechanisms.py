"""世界生成公式链声明 — 气候/群系/基线标量公式的生产单一事实源。

覆盖空间生成链中的标量公式（纬度噪声→海面温度→年均温→气候档→
群系与基线）；大陆侵蚀/水文/河流/瓦片等算法管线不在本切片
（其输出为未声明边界的下一批迁移对象）。

C 实现经 ctypes 调用，方程版本 = Python 包装源码 + C 源文件 +
注入常量来源（config.py / data/world.json）的文件哈希。
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

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
    WORLD_GEN_DERIVED_A,
    WORLD_GEN_DERIVED_B,
    WORLD_GEN_DERIVED_C,
    WORLD_GEN_DERIVED_D,
    WORLD_GEN_INPUT,
)
from ascend.config import (
    ALPINE_ALTITUDE,
    DESERT_RAINFALL,
    LAPSE_RATE,
    POLAR_TEMP,
    RAINFALL_MAX,
    RAINFALL_MIN,
    RAINFOREST_RAINFALL,
    STEPPE_MIN_TEMP,
    STEPPE_RAINFALL,
    TAIGA_RAINFALL,
    TEMPERATE_TEMP,
    TROPICAL_TEMP,
)
from ascend.mathutil import clamp
from ascend.space import BiomeType, ClimateZone, get_climate_template

_HERE = Path(__file__).resolve().parent
_HYDRO_C = _HERE / "_hydrology.c"
_HYDRO_PY = _HERE / "hydrology.py"
_CLIMATE_PY = _HERE / "climate.py"
_BIOME_PY = _HERE / "biome.py"
_CONFIG_PY = _HERE.parent / "config.py"
_WORLD_JSON = _HERE.parents[2] / "data" / "world.json"
_CLIMATE_JSON = _HERE.parents[2] / "data" / "climate.json"

# ── 节点 ID ─────────────────────────────────────────────────────

LATITUDE_NOISE = "world.gen.latitude_noise"
RAINFALL_NOISE = "world.gen.rainfall_noise"
ALTITUDE = "world.gen.altitude_m"
HUMIDITY_NOISE = "world.gen.humidity_noise"
WIND_NOISE = "world.gen.wind_noise"
MOISTURE_NOISE = "world.gen.moisture_noise"

SEA_LEVEL_TEMPERATURE = "weather.chunk.sea_level_temperature_c"
ANNUAL_RAINFALL = "weather.chunk.annual_rainfall_mm_per_year"
ANNUAL_TEMPERATURE = "weather.chunk.annual_mean_temperature_c"
CLIMATE_ZONE = "world.gen.climate_zone"
BIOME = "world.gen.biome"
BASELINE_HUMIDITY = "weather.chunk.baseline_humidity_percent"
BASELINE_WIND_SPEED = "weather.chunk.baseline_wind_speed_mps"
MEAN_PRECIP_INTENSITY = "weather.chunk.mean_precip_intensity_mm_per_hour"
HUMIDITY_SHARPNESS = "weather.chunk.humidity_sharpness"

# ── 参数 ID ─────────────────────────────────────────────────────

_P_LAPSE_RATE = "world.parameter.lapse_rate_c_per_1000m"
_P_RAINFALL_MIN = "world.parameter.rainfall_min_mm"
_P_RAINFALL_MAX = "world.parameter.rainfall_max_mm"
_P_ALPINE_ALTITUDE = "world.parameter.alpine_altitude_m"
_P_POLAR_TEMP = "world.parameter.polar_temp_c"
_P_DESERT_RAINFALL = "world.parameter.desert_rainfall_mm"
_P_STEPPE_RAINFALL = "world.parameter.steppe_rainfall_mm"
_P_STEPPE_MIN_TEMP = "world.parameter.steppe_min_temp_c"
_P_TROPICAL_TEMP = "world.parameter.tropical_temp_c"
_P_TEMPERATE_TEMP = "world.parameter.temperate_temp_c"
_P_RAINFOREST_RAINFALL = "world.parameter.rainforest_rainfall_mm"
_P_TAIGA_RAINFALL = "world.parameter.taiga_rainfall_mm"
_P_SEA_TEMP_SCALE = "world.parameter.sea_temperature_scale_c"
_P_SEA_TEMP_OFFSET = "world.parameter.sea_temperature_offset_c"
_P_SEA_TEMP_MIN = "world.parameter.sea_temperature_min_c"
_P_SEA_TEMP_MAX = "world.parameter.sea_temperature_max_c"

_CHUNK_INSTANCE = InstanceDomain(
    kind="spatial_field",
    axes=("chunk_x", "chunk_y"),
    creation="chunk_registration",
    destruction="chunk_unregistration",
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
        source_key: 数据键形如 ``LAPSE_RATE``（data/world.json#climate.*
            分区）；C 内嵌常量以 ``c:`` 前缀标注（code-only，无数据键）。
    """
    if source_key.startswith("c:"):
        source = f"code-only:{source_key[2:]}（内嵌 _hydrology.c，由 C 文件哈希覆盖）"
    else:
        source = f"data/world.json#climate.{source_key}"
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


def _sea_level_temperature_equation(latitude_noise: float) -> float:
    """C 端：t = lat_n*25 + 10，clamp [-20, 38]（单源 _hydrology.c）。"""
    from .hydrology import sea_level_temperature_c

    return sea_level_temperature_c(latitude_noise)


def _rainfall_equation(rainfall_noise: float) -> float:
    """C 端：r = min + (n+1)/2*(max-min)，clamp [0, 5000]。"""
    from .hydrology import rainfall_from_noise_c

    return rainfall_from_noise_c(rainfall_noise)


def _lapse_rate_equation(
    sea_level_temperature: float,
    altitude: float,
) -> float:
    """C 端：陆地按海拔直减率降温，clamp [-20, 36]。"""
    from .hydrology import apply_lapse_rate_c

    return apply_lapse_rate_c(sea_level_temperature, altitude)


def _climate_zone_equation(
    mean_temperature: float,
    annual_rainfall: float,
    altitude: float,
) -> int:
    from .hydrology import classify_climate_c

    return int(classify_climate_c(mean_temperature, annual_rainfall, altitude))


def _biome_equation(
    mean_temperature: float,
    annual_rainfall: float,
    altitude: float,
    sea_level_temperature: float,
    moisture_noise: float,
) -> int:
    """群系主隶属（静态细分值域；大陆动态 subdiv_ranges 不在本切片）。"""
    from .biome import biome_from_attrs

    biome = biome_from_attrs(
        mean_temperature, annual_rainfall, altitude, sea_level_temperature,
        moisture_noise,
    )
    return int(biome)


def _baseline_humidity_equation(
    climate_zone: int,
    humidity_noise: float,
) -> float:
    template = get_climate_template(ClimateZone(climate_zone))
    lo, hi = template.humidity_range
    return clamp(lo + (humidity_noise + 1.0) * 0.5 * (hi - lo), 0.0, 100.0)


def _baseline_wind_speed_equation(
    climate_zone: int,
    wind_noise: float,
) -> float:
    template = get_climate_template(ClimateZone(climate_zone))
    lo, hi = template.wind_speed_range
    return clamp(lo + (wind_noise + 1.0) * 0.5 * (hi - lo), 0.0, 50.0)


def _mean_precip_intensity_equation(climate_zone: int) -> float:
    return get_climate_template(ClimateZone(climate_zone)).mean_precip_intensity


def _humidity_sharpness_equation(climate_zone: int) -> float:
    return get_climate_template(ClimateZone(climate_zone)).humidity_sharpness


# ── 节点 ────────────────────────────────────────────────────────


def _boundary(
    node_id: str,
    *,
    kind: str,
    unit: str,
    bounds: tuple[float, float] | None,
    writer: str,
    error_budget: float,
    valid_domain: str,
) -> NodeSpec:
    return _node(
        node_id,
        role="persistent_state",
        origin="slice_boundary",
        kind=kind,
        unit=unit,
        bounds=bounds,
        choices=(),
        quantization="ieee754_binary64",
        in_world_state=True,
        reconstruction=f"not_applicable:{writer}_output",
        schedule="provided_by_external_writer",
        microstep=WORLD_GEN_INPUT,
        writer=writer,
        error_budget=error_budget,
        valid_domain=valid_domain,
    )


def _gen_node(
    node_id: str,
    *,
    kind: str,
    unit: str,
    bounds: tuple[float, float] | None,
    choices: tuple[object, ...],
    reconstruction: str,
    microstep: str,
    writer: str,
    error_budget: float,
    valid_domain: str,
) -> NodeSpec:
    return _node(
        node_id,
        role="mechanism_state",
        origin="mechanism",
        kind=kind,
        unit=unit,
        bounds=bounds,
        choices=choices,
        quantization="ieee754_binary64" if kind == "float" else "exact",
        in_world_state=False,
        reconstruction=reconstruction,
        schedule="on_chunk_generation",
        microstep=microstep,
        writer=writer,
        error_budget=error_budget,
        valid_domain=valid_domain,
    )


_NODES = (
    _boundary(LATITUDE_NOISE, kind="float", unit="dimensionless",
              bounds=(-1.0, 1.0), writer="space.continent_generator",
              error_budget=0.0, valid_domain="closed_interval_-1_1"),
    _boundary(RAINFALL_NOISE, kind="float", unit="dimensionless",
              bounds=(-1.0, 1.0), writer="space.continent_generator",
              error_budget=0.0, valid_domain="closed_interval_-1_1"),
    _boundary(ALTITUDE, kind="float", unit="meter",
              bounds=None, writer="space.terrain_generator",
              error_budget=1.0,
              valid_domain="elevation_after_terrain_calibration_"
                           "no_prior_closed_bound"),
    _boundary(HUMIDITY_NOISE, kind="float", unit="dimensionless",
              bounds=(-1.0, 1.0), writer="space.terrain_generator",
              error_budget=0.0, valid_domain="closed_interval_-1_1"),
    _boundary(WIND_NOISE, kind="float", unit="dimensionless",
              bounds=(-1.0, 1.0), writer="space.terrain_generator",
              error_budget=0.0, valid_domain="closed_interval_-1_1"),
    _boundary(MOISTURE_NOISE, kind="float", unit="dimensionless",
              bounds=(-1.0, 1.0), writer="space.terrain_generator",
              error_budget=0.0, valid_domain="closed_interval_-1_1"),
    _gen_node(
        SEA_LEVEL_TEMPERATURE,
        kind="float",
        unit="degC",
        bounds=(-20.0, 38.0),
        choices=(),
        reconstruction="world.gen.derive_sea_level_temperature.v1",
        microstep=WORLD_GEN_DERIVED_A,
        writer="world.gen.derive_sea_level_temperature.v1",
        error_budget=0.2,
        valid_domain="closed_interval_-20_38_degC",
    ),
    _gen_node(
        ANNUAL_RAINFALL,
        kind="float",
        unit="mm_per_year",
        bounds=None,
        choices=(),
        reconstruction="world.gen.derive_annual_rainfall.v1",
        microstep=WORLD_GEN_DERIVED_A,
        writer="world.gen.derive_annual_rainfall.v1",
        error_budget=100.0,
        valid_domain=(
            "finite_nonnegative_annual_rainfall_after_continent_calibration"
        ),
    ),
    _gen_node(
        ANNUAL_TEMPERATURE,
        kind="float",
        unit="degC",
        bounds=None,
        choices=(),
        reconstruction="world.gen.derive_annual_mean_temperature.v1",
        microstep=WORLD_GEN_DERIVED_B,
        writer="world.gen.derive_annual_mean_temperature.v1",
        error_budget=0.05,
        valid_domain=(
            "finite_temperature_after_continent_calibration_"
            "no_prior_closed_bound"
        ),
    ),
    _gen_node(
        CLIMATE_ZONE,
        kind="enum",
        unit="climate_zone_index",
        bounds=None,
        choices=tuple(range(8)),
        reconstruction="world.gen.classify_climate_zone.v1",
        microstep=WORLD_GEN_DERIVED_C,
        writer="world.gen.classify_climate_zone.v1",
        error_budget=0.0,
        valid_domain="climate_zone_enum_0_7",
    ),
    _gen_node(
        BIOME,
        kind="enum",
        unit="biome_index",
        bounds=None,
        choices=tuple(BiomeType),
        reconstruction="world.gen.classify_biome.v1",
        microstep=WORLD_GEN_DERIVED_D,
        writer="world.gen.classify_biome.v1",
        error_budget=0.0,
        valid_domain="biome_enum",
    ),
    _gen_node(
        BASELINE_HUMIDITY,
        kind="float",
        unit="percent",
        bounds=(0.0, 100.0),
        choices=(),
        reconstruction="world.gen.derive_baseline_humidity.v1",
        microstep=WORLD_GEN_DERIVED_D,
        writer="world.gen.derive_baseline_humidity.v1",
        error_budget=1.0,
        valid_domain="closed_interval_0_100_percent",
    ),
    _gen_node(
        BASELINE_WIND_SPEED,
        kind="float",
        unit="mps",
        bounds=(0.0, 50.0),
        choices=(),
        reconstruction="world.gen.derive_baseline_wind_speed.v1",
        microstep=WORLD_GEN_DERIVED_D,
        writer="world.gen.derive_baseline_wind_speed.v1",
        error_budget=0.5,
        valid_domain="closed_interval_0_50_mps",
    ),
    _gen_node(
        MEAN_PRECIP_INTENSITY,
        kind="float",
        unit="mm_per_hour",
        bounds=(0.0, 100.0),
        choices=(),
        reconstruction="world.gen.derive_mean_precip_intensity.v1",
        microstep=WORLD_GEN_DERIVED_D,
        writer="world.gen.derive_mean_precip_intensity.v1",
        error_budget=0.1,
        valid_domain="climate_template_contract",
    ),
    _gen_node(
        HUMIDITY_SHARPNESS,
        kind="float",
        unit="dimensionless",
        bounds=(0.0, 10.0),
        choices=(),
        reconstruction="world.gen.derive_humidity_sharpness.v1",
        microstep=WORLD_GEN_DERIVED_D,
        writer="world.gen.derive_humidity_sharpness.v1",
        error_budget=0.0,
        valid_domain="nonnegative_sharpness",
    ),
)

# ── 参数 ────────────────────────────────────────────────────────

_PARAMETERS = (
    _parameter(_P_LAPSE_RATE, LAPSE_RATE, "degC_per_1000m", (0.0, 100.0), "LAPSE_RATE"),
    _parameter(_P_RAINFALL_MIN, RAINFALL_MIN, "mm_per_year", (0.0, 1e6), "RAINFALL_MIN"),
    _parameter(_P_RAINFALL_MAX, RAINFALL_MAX, "mm_per_year", (0.0, 1e6), "RAINFALL_MAX"),
    _parameter(_P_ALPINE_ALTITUDE, ALPINE_ALTITUDE, "meter", (0.0, 1e5), "ALPINE_ALTITUDE"),
    _parameter(_P_POLAR_TEMP, POLAR_TEMP, "degC", (-273.15, 100.0), "POLAR_TEMP"),
    _parameter(_P_DESERT_RAINFALL, DESERT_RAINFALL, "mm_per_year", (0.0, 1e6), "DESERT_RAINFALL"),
    _parameter(_P_STEPPE_RAINFALL, STEPPE_RAINFALL, "mm_per_year", (0.0, 1e6), "STEPPE_RAINFALL"),
    _parameter(_P_STEPPE_MIN_TEMP, STEPPE_MIN_TEMP, "degC", (-273.15, 100.0), "STEPPE_MIN_TEMP"),
    _parameter(_P_TROPICAL_TEMP, TROPICAL_TEMP, "degC", (-273.15, 100.0), "TROPICAL_TEMP"),
    _parameter(_P_TEMPERATE_TEMP, TEMPERATE_TEMP, "degC", (-273.15, 100.0), "TEMPERATE_TEMP"),
    _parameter(_P_RAINFOREST_RAINFALL, RAINFOREST_RAINFALL, "mm_per_year", (0.0, 1e6), "RAINFOREST_RAINFALL"),
    _parameter(_P_TAIGA_RAINFALL, TAIGA_RAINFALL, "mm_per_year", (0.0, 1e6), "TAIGA_RAINFALL"),
    _parameter(_P_SEA_TEMP_SCALE, 25.0, "degC", (0.0, 1000.0), "c:sea_temperature_scale_c"),
    _parameter(_P_SEA_TEMP_OFFSET, 10.0, "degC", (-273.15, 1000.0), "c:sea_temperature_offset_c"),
    _parameter(_P_SEA_TEMP_MIN, -20.0, "degC", (-273.15, 1000.0), "c:sea_temperature_min_c"),
    _parameter(_P_SEA_TEMP_MAX, 38.0, "degC", (-273.15, 1000.0), "c:sea_temperature_max_c"),
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


# C 公式的机制版本同时覆盖包装源码与 C 源码 + 注入常量来源。
_C_DEPS = (_HYDRO_C, _HYDRO_PY, _CLIMATE_PY, _CONFIG_PY, _WORLD_JSON)
_TEMPLATE_DEPS = (_CLIMATE_PY, _CLIMATE_JSON, _CONFIG_PY, _WORLD_JSON, clamp,
                  get_climate_template)
_BIOME_DEPS = (_BIOME_PY, _CLIMATE_PY, _HYDRO_C, _HYDRO_PY, _CONFIG_PY,
               _WORLD_JSON)


_MECHANISMS = (
    _mechanism(
        "world.gen.derive_sea_level_temperature.v1",
        SEA_LEVEL_TEMPERATURE,
        "clamp(latitude_noise * 25 + 10, -20, 38)（C 单源 _hydrology.c）",
        _sea_level_temperature_equation,
        (
            _parent(LATITUDE_NOISE, "latitude_noise", WORLD_GEN_INPUT, 25.0,
                    "closed_interval_-1_1", "forward"),
        ),
        (),
        (
            "noise_below_-1.2:clamp_to_-20",
            "noise_above_1.12:clamp_to_38",
            "finite_interior:linear_mapping",
        ),
        _C_DEPS,
        (
            _w("latitude_noise_changes_sea_level_temperature",
               LATITUDE_NOISE,
               ((LATITUDE_NOISE, 0.0),), 0.4, (10.0, 20.0)),
        ),
    ),
    _mechanism(
        "world.gen.derive_annual_rainfall.v1",
        ANNUAL_RAINFALL,
        "clamp(rainfall_min + (noise + 1) * 0.5 * (rainfall_max - "
        "rainfall_min), 0, 5000)（C 单源，min/max 由 config 注入）",
        _rainfall_equation,
        (
            _parent(RAINFALL_NOISE, "rainfall_noise", WORLD_GEN_INPUT,
                    1725.0, "closed_interval_-1_1", "forward"),
        ),
        (),
        (
            "noise_minus_one:rainfall_min",
            "noise_plus_one:rainfall_max",
            "finite_interior:linear_mapping",
        ),
        _C_DEPS,
        (
            _w("rainfall_noise_changes_annual_rainfall",
               RAINFALL_NOISE,
               ((RAINFALL_NOISE, 0.0),), 0.4, (1775.0, 2465.0)),
        ),
    ),
    _mechanism(
        "world.gen.derive_annual_mean_temperature.v1",
        ANNUAL_TEMPERATURE,
        "sea_level_temperature - altitude * lapse_rate / 1000 for "
        "altitude > 0 else sea_level_temperature, clamp [-20, 36]"
        "（C 单源）",
        _lapse_rate_equation,
        (
            _parent(SEA_LEVEL_TEMPERATURE, "sea_level_temperature",
                    WORLD_GEN_DERIVED_A, 1.0,
                    "closed_interval_-20_38_degC", "forward"),
            _parent(ALTITUDE, "altitude", WORLD_GEN_INPUT, 0.009,
                    "elevation_range", "forward"),
        ),
        (),
        (
            "sea_surface:altitude_nonpositive_identity",
            "land:linear_lapse_rate",
            "temperature_below_-20:clamp",
            "temperature_above_36:clamp",
        ),
        _C_DEPS,
        (
            _w("sea_level_temperature_changes_annual_mean_temperature",
               SEA_LEVEL_TEMPERATURE,
               ((SEA_LEVEL_TEMPERATURE, 10.0), (ALTITUDE, 0.0)),
               20.0, (10.0, 20.0)),
            _w("altitude_changes_annual_mean_temperature",
               ALTITUDE,
               ((SEA_LEVEL_TEMPERATURE, 10.0), (ALTITUDE, 0.0)),
               1000.0, (10.0, 1.0)),
        ),
    ),
    _mechanism(
        "world.gen.classify_climate_zone.v1",
        CLIMATE_ZONE,
        "8 档静态决策树（C 单源，阈值由 config 经 hydrology 注入）",
        _climate_zone_equation,
        (
            _parent(ANNUAL_TEMPERATURE, "mean_temperature",
                    WORLD_GEN_DERIVED_B, 0.0,
                    "finite_temperature_within_declared_bounds", "forward"),
            _parent(ANNUAL_RAINFALL, "annual_rainfall",
                    WORLD_GEN_DERIVED_A, 0.0,
                    "finite_nonnegative_annual_rainfall", "forward"),
            _parent(ALTITUDE, "altitude", WORLD_GEN_INPUT, 0.0,
                    "elevation_range", "forward"),
        ),
        (),
        (
            "altitude_above_alpine_threshold:ALPINE",
            "temperature_below_polar_threshold:POLAR_TUNDRA",
            "rainfall_below_desert_threshold:DESERT",
            "decision_tree_priority_order",
        ),
        _C_DEPS,
        (
            _w("altitude_changes_climate_zone",
               ALTITUDE,
               ((ANNUAL_TEMPERATURE, 10.0), (ANNUAL_RAINFALL, 800.0),
                (ALTITUDE, 100.0)),
               3000.0, (4, 7)),
            _w("annual_rainfall_changes_climate_zone",
               ANNUAL_RAINFALL,
               ((ANNUAL_TEMPERATURE, 10.0), (ANNUAL_RAINFALL, 800.0),
                (ALTITUDE, 100.0)),
               100.0, (4, 2)),
            _w("annual_temperature_changes_climate_zone",
               ANNUAL_TEMPERATURE,
               ((ANNUAL_TEMPERATURE, 10.0), (ANNUAL_RAINFALL, 800.0),
                (ALTITUDE, 100.0)),
               25.0, (4, 1)),
        ),
    ),
    _mechanism(
        "world.gen.classify_biome.v1",
        BIOME,
        "海洋按海面温度三档；陆地按气候档 + 细分维度三角隶属取主型"
        "（静态细分值域；大陆动态 subdiv_ranges 不在本切片）",
        _biome_equation,
        (
            _parent(ANNUAL_TEMPERATURE, "mean_temperature",
                    WORLD_GEN_DERIVED_B, 0.0,
                    "finite_temperature_within_declared_bounds", "forward"),
            _parent(ANNUAL_RAINFALL, "annual_rainfall",
                    WORLD_GEN_DERIVED_A, 0.0,
                    "finite_nonnegative_annual_rainfall", "forward"),
            _parent(ALTITUDE, "altitude", WORLD_GEN_INPUT, 0.0,
                    "elevation_range", "forward"),
            _parent(SEA_LEVEL_TEMPERATURE, "sea_level_temperature",
                    WORLD_GEN_DERIVED_A, 0.0,
                    "closed_interval_-20_38_degC", "forward"),
            _parent(MOISTURE_NOISE, "moisture_noise", WORLD_GEN_INPUT, 0.0,
                    "closed_interval_-1_1", "forward"),
        ),
        (),
        (
            "altitude_below_sea_level:ocean_by_sea_temperature",
            "land:climate_subdivision_membership",
            "no_subdivision_config:temperate_deciduous_fallback",
        ),
        _BIOME_DEPS,
        (
            _w("altitude_changes_biome_ocean_land",
               ALTITUDE,
               ((ANNUAL_TEMPERATURE, 15.0), (ANNUAL_RAINFALL, 800.0),
                (ALTITUDE, 100.0), (SEA_LEVEL_TEMPERATURE, 10.0),
                (MOISTURE_NOISE, 0.0)),
               -100.0, (int(BiomeType.TEMPERATE_DECIDUOUS_FOREST),
                        int(BiomeType.TEMPERATE_OCEAN))),
            _w("annual_rainfall_changes_biome",
               ANNUAL_RAINFALL,
               ((ANNUAL_TEMPERATURE, 15.0), (ANNUAL_RAINFALL, 1000.0),
                (ALTITUDE, 100.0), (SEA_LEVEL_TEMPERATURE, 10.0),
                (MOISTURE_NOISE, 0.0)),
               100.0, (int(BiomeType.TEMPERATE_DECIDUOUS_FOREST),
                       int(BiomeType.SANDY_DESERT))),
            _w("annual_temperature_changes_biome",
               ANNUAL_TEMPERATURE,
               ((ANNUAL_TEMPERATURE, 15.0), (ANNUAL_RAINFALL, 800.0),
                (ALTITUDE, 100.0), (SEA_LEVEL_TEMPERATURE, 10.0),
                (MOISTURE_NOISE, 0.0)),
               25.0, (int(BiomeType.TEMPERATE_DECIDUOUS_FOREST),
                      int(BiomeType.TROPICAL_SAVANNA))),
            _w("sea_level_temperature_changes_ocean_biome",
               SEA_LEVEL_TEMPERATURE,
               ((ANNUAL_TEMPERATURE, 15.0), (ANNUAL_RAINFALL, 800.0),
                (ALTITUDE, -100.0), (SEA_LEVEL_TEMPERATURE, 10.0),
                (MOISTURE_NOISE, 0.0)),
               25.0, (int(BiomeType.TEMPERATE_OCEAN),
                      int(BiomeType.WARM_OCEAN))),
            _w("moisture_noise_changes_desert_biome",
               MOISTURE_NOISE,
               ((ANNUAL_TEMPERATURE, 15.0), (ANNUAL_RAINFALL, 100.0),
                (ALTITUDE, 100.0), (SEA_LEVEL_TEMPERATURE, 10.0),
                (MOISTURE_NOISE, 0.0)),
               1.0, (int(BiomeType.SANDY_DESERT),
                     int(BiomeType.ROCKY_DESERT))),
        ),
    ),
    _mechanism(
        "world.gen.derive_baseline_humidity.v1",
        BASELINE_HUMIDITY,
        "clamp(template_humidity_lo + (noise + 1) * 0.5 * "
        "(template_humidity_hi - template_humidity_lo), 0, 100)"
        "（模板数据 data/climate.json）",
        _baseline_humidity_equation,
        (
            _parent(CLIMATE_ZONE, "climate_zone", WORLD_GEN_DERIVED_C, 0.0,
                    "climate_zone_enum_0_7", "forward"),
            _parent(HUMIDITY_NOISE, "humidity_noise", WORLD_GEN_INPUT, 0.0,
                    "closed_interval_-1_1", "forward"),
        ),
        (),
        (
            "noise_minus_one:template_low",
            "noise_plus_one:template_high",
            "finite_interior:linear_interpolation",
        ),
        _TEMPLATE_DEPS,
        (
            _w("humidity_noise_changes_baseline_humidity",
               HUMIDITY_NOISE,
               ((CLIMATE_ZONE, 4), (HUMIDITY_NOISE, 0.0)),
               1.0, (62.5, 80.0)),
            _w("climate_zone_changes_baseline_humidity",
               CLIMATE_ZONE,
               ((CLIMATE_ZONE, 4), (HUMIDITY_NOISE, 0.0)),
               2, (62.5, 17.5)),
        ),
    ),
    _mechanism(
        "world.gen.derive_baseline_wind_speed.v1",
        BASELINE_WIND_SPEED,
        "clamp(template_wind_lo + (noise + 1) * 0.5 * "
        "(template_wind_hi - template_wind_lo), 0, 50)"
        "（模板数据 data/climate.json）",
        _baseline_wind_speed_equation,
        (
            _parent(CLIMATE_ZONE, "climate_zone", WORLD_GEN_DERIVED_C, 0.0,
                    "climate_zone_enum_0_7", "forward"),
            _parent(WIND_NOISE, "wind_noise", WORLD_GEN_INPUT, 0.0,
                    "closed_interval_-1_1", "forward"),
        ),
        (),
        (
            "noise_minus_one:template_low",
            "noise_plus_one:template_high",
            "finite_interior:linear_interpolation",
        ),
        _TEMPLATE_DEPS,
        (
            _w("wind_noise_changes_baseline_wind_speed",
               WIND_NOISE,
               ((CLIMATE_ZONE, 4), (WIND_NOISE, 0.0)),
               1.0, (6.0, 12.0)),
            _w("climate_zone_changes_baseline_wind_speed",
               CLIMATE_ZONE,
               ((CLIMATE_ZONE, 4), (WIND_NOISE, 0.0)),
               0, (6.0, 3.0)),
        ),
    ),
    _mechanism(
        "world.gen.derive_mean_precip_intensity.v1",
        MEAN_PRECIP_INTENSITY,
        "climate_template.mean_precip_intensity（数据契约）",
        _mean_precip_intensity_equation,
        (
            _parent(CLIMATE_ZONE, "climate_zone", WORLD_GEN_DERIVED_C, 0.0,
                    "climate_zone_enum_0_7", "forward"),
        ),
        (),
        ("per_zone_lookup:data_contract",),
        _TEMPLATE_DEPS,
        (
            _w("climate_zone_changes_mean_precip_intensity",
               CLIMATE_ZONE,
               ((CLIMATE_ZONE, 4),), 0, (5.0, 10.0)),
        ),
    ),
    _mechanism(
        "world.gen.derive_humidity_sharpness.v1",
        HUMIDITY_SHARPNESS,
        "climate_template.humidity_sharpness（数据契约）",
        _humidity_sharpness_equation,
        (
            _parent(CLIMATE_ZONE, "climate_zone", WORLD_GEN_DERIVED_C, 0.0,
                    "climate_zone_enum_0_7", "forward"),
        ),
        (),
        ("per_zone_lookup:data_contract",),
        _TEMPLATE_DEPS,
        (
            _w("climate_zone_changes_humidity_sharpness",
               CLIMATE_ZONE,
               ((CLIMATE_ZONE, 4),), 1, (0.0, 2.5)),
        ),
    ),
)

WORLD_GEN_NODES = _NODES
WORLD_GEN_PARAMETERS = _PARAMETERS
WORLD_GEN_MECHANISM_SPECS = _MECHANISMS

__all__ = [
    "ALTITUDE",
    "ANNUAL_RAINFALL",
    "ANNUAL_TEMPERATURE",
    "BASELINE_HUMIDITY",
    "BASELINE_WIND_SPEED",
    "BIOME",
    "CLIMATE_ZONE",
    "HUMIDITY_NOISE",
    "HUMIDITY_SHARPNESS",
    "LATITUDE_NOISE",
    "MEAN_PRECIP_INTENSITY",
    "MOISTURE_NOISE",
    "RAINFALL_NOISE",
    "SEA_LEVEL_TEMPERATURE",
    "WIND_NOISE",
    "WORLD_GEN_MECHANISM_SPECS",
    "WORLD_GEN_NODES",
    "WORLD_GEN_PARAMETERS",
]
