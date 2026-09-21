"""机制声明 — 世界生成的槽位、参数、机制与见证。

输出为 derived 槽位；参数经 ParameterDecl + ``ctx.param`` 绑定；见证为
只变单父的见证对。实现见 ``equations.py``，黄金向量
（``testbench/world/data/weather_golden.json``）为冻结契约数据。
"""
from __future__ import annotations

from olam.meta.declarations import (
    InstanceDecl,
    Arithmetic,
    MechanismDecl,
    ModulePack,
    ParameterDecl,
    Parent,
    Permissions,
    SlotDecl,
    ValueDomain,
    When,
    Witness,
)
from olam.modules.primitives import GLOBAL

CHUNK = InstanceDecl(
    id='lattice.chunk', kind='lattice', identity='xy', size=None,
    axes=('chunk_x', 'chunk_y'),
)

from . import equations as _eq


# ── 槽位（输出 derived + 边界 external）─────────────────
SLOT_WEATHER_CHUNK_ANNUAL_MEAN_TEMPERATURE_C = SlotDecl(
    id='weather.chunk.annual_mean_temperature_c',
    on='lattice.chunk',
    persist='derived',
    domain=ValueDomain(kind="float", minimum=None, maximum=None, unit='degC'),
    writer='world.gen.derive_annual_mean_temperature.v1',
    recompute='机制 world.gen.derive_annual_mean_temperature.v1',
    permissions=Permissions(intervene=True, observe=True, record=True),
    role='mechanism_state',
    schedule='on_chunk_generation',
    quantization='ieee754_binary64',
    metric='absolute_difference',
    epsilon=0.05,
    access_interventions=('node', 'persistent'),
    research_trace=True,
    observation_protocols=('research.full.v1', 'agent.weather.v1'),

    valid_domain='finite_temperature_after_continent_calibration_no_prior_closed_bound',

)
SLOT_WEATHER_CHUNK_ANNUAL_RAINFALL_MM_PER_YEAR = SlotDecl(
    id='weather.chunk.annual_rainfall_mm_per_year',
    on='lattice.chunk',
    persist='derived',
    domain=ValueDomain(kind="float", minimum=None, maximum=None, unit='mm_per_year'),
    writer='world.gen.derive_annual_rainfall.v1',
    recompute='机制 world.gen.derive_annual_rainfall.v1',
    permissions=Permissions(intervene=True, observe=True, record=True),
    role='mechanism_state',
    schedule='on_chunk_generation',
    quantization='ieee754_binary64',
    metric='absolute_difference',
    epsilon=100.0,
    access_interventions=('node', 'persistent'),
    research_trace=True,
    observation_protocols=('research.full.v1', 'agent.weather.v1'),

    valid_domain='finite_nonnegative_annual_rainfall_after_continent_calibration',

)
SLOT_WEATHER_CHUNK_BASELINE_HUMIDITY_PERCENT = SlotDecl(
    id='weather.chunk.baseline_humidity_percent',
    on='lattice.chunk',
    persist='derived',
    domain=ValueDomain(kind="float", minimum=0.0, maximum=100.0, unit='percent'),
    writer='world.gen.derive_baseline_humidity.v1',
    recompute='机制 world.gen.derive_baseline_humidity.v1',
    permissions=Permissions(intervene=True, observe=True, record=True),
    role='mechanism_state',
    schedule='on_chunk_generation',
    quantization='ieee754_binary64',
    metric='absolute_difference',
    epsilon=1.0,
    access_interventions=('node', 'persistent'),
    research_trace=True,
    observation_protocols=('research.full.v1', 'agent.weather.v1'),

    valid_domain='closed_interval_0_100_percent',

)
SLOT_WEATHER_CHUNK_BASELINE_WIND_SPEED_MPS = SlotDecl(
    id='weather.chunk.baseline_wind_speed_mps',
    on='lattice.chunk',
    persist='derived',
    domain=ValueDomain(kind="float", minimum=0.0, maximum=50.0, unit='mps'),
    writer='world.gen.derive_baseline_wind_speed.v1',
    recompute='机制 world.gen.derive_baseline_wind_speed.v1',
    permissions=Permissions(intervene=True, observe=True, record=True),
    role='mechanism_state',
    schedule='on_chunk_generation',
    quantization='ieee754_binary64',
    metric='absolute_difference',
    epsilon=0.5,
    access_interventions=('node', 'persistent'),
    research_trace=True,
    observation_protocols=('research.full.v1', 'agent.weather.v1'),

    valid_domain='closed_interval_0_50_mps',

)
SLOT_WEATHER_CHUNK_HUMIDITY_SHARPNESS = SlotDecl(
    id='weather.chunk.humidity_sharpness',
    on='lattice.chunk',
    persist='derived',
    domain=ValueDomain(kind="float", minimum=0.0, maximum=10.0, unit='dimensionless'),
    writer='world.gen.derive_humidity_sharpness.v1',
    recompute='机制 world.gen.derive_humidity_sharpness.v1',
    permissions=Permissions(intervene=True, observe=True, record=True),
    role='mechanism_state',
    schedule='on_chunk_generation',
    quantization='ieee754_binary64',
    metric='absolute_difference',
    epsilon=0.0,
    access_interventions=('node', 'persistent'),
    research_trace=True,
    observation_protocols=('research.full.v1', 'agent.weather.v1'),

    valid_domain='nonnegative_sharpness',

)
SLOT_WEATHER_CHUNK_MEAN_PRECIP_INTENSITY_MM_PER_HOUR = SlotDecl(
    id='weather.chunk.mean_precip_intensity_mm_per_hour',
    on='lattice.chunk',
    persist='derived',
    domain=ValueDomain(kind="float", minimum=2.0, maximum=10.0, unit='mm_per_hour'),
    writer='world.gen.derive_mean_precip_intensity.v1',
    recompute='机制 world.gen.derive_mean_precip_intensity.v1',
    permissions=Permissions(intervene=True, observe=True, record=True),
    role='mechanism_state',
    schedule='on_chunk_generation',
    quantization='ieee754_binary64',
    metric='absolute_difference',
    epsilon=0.1,
    access_interventions=('node', 'persistent'),
    research_trace=True,
    observation_protocols=('research.full.v1', 'agent.weather.v1'),

    valid_domain='climate_template_contract',

)
SLOT_WEATHER_CHUNK_SEA_LEVEL_TEMPERATURE_C = SlotDecl(
    id='weather.chunk.sea_level_temperature_c',
    on='lattice.chunk',
    persist='derived',
    domain=ValueDomain(kind="float", minimum=-20.0, maximum=38.0, unit='degC'),
    writer='world.gen.derive_sea_level_temperature.v1',
    recompute='机制 world.gen.derive_sea_level_temperature.v1',
    permissions=Permissions(intervene=True, observe=True, record=True),
    role='mechanism_state',
    schedule='on_chunk_generation',
    quantization='ieee754_binary64',
    metric='absolute_difference',
    epsilon=0.2,
    access_interventions=('node', 'persistent'),
    research_trace=True,
    observation_protocols=('research.full.v1', 'agent.weather.v1'),

    valid_domain='closed_interval_-20_38_degC',

)
SLOT_WORLD_GEN_BIOME = SlotDecl(
    id='world.gen.biome',
    on='lattice.chunk',
    persist='derived',
    domain=ValueDomain(kind='enum', choices=(0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17, 18), unit='biome_index'),
    writer='world.gen.classify_biome.v1',
    recompute='机制 world.gen.classify_biome.v1',
    permissions=Permissions(intervene=True, observe=True, record=True),
    role='mechanism_state',
    schedule='on_chunk_generation',
    quantization='exact',
    metric='discrete',
    epsilon=0.0,
    access_interventions=('node', 'persistent'),
    research_trace=True,
    observation_protocols=('research.full.v1', 'agent.weather.v1'),

    valid_domain='biome_enum',

)
SLOT_WORLD_GEN_CLIMATE_ZONE = SlotDecl(
    id='world.gen.climate_zone',
    on='lattice.chunk',
    persist='derived',
    domain=ValueDomain(kind='enum', choices=(0, 1, 2, 3, 4, 5, 6, 7), unit='climate_zone_index'),
    writer='world.gen.classify_climate_zone.v1',
    recompute='机制 world.gen.classify_climate_zone.v1',
    permissions=Permissions(intervene=True, observe=True, record=True),
    role='mechanism_state',
    schedule='on_chunk_generation',
    quantization='exact',
    metric='discrete',
    epsilon=0.0,
    access_interventions=('node', 'persistent'),
    research_trace=True,
    observation_protocols=('research.full.v1', 'agent.weather.v1'),

    valid_domain='climate_zone_enum_0_7',

)
SLOT_WORLD_GEN_ALTITUDE_M = SlotDecl(
    id='world.gen.altitude_m',
    on='lattice.chunk',
    persist='external',
    domain=ValueDomain(kind="float", minimum=None, maximum=None, unit='meter'),
    permissions=Permissions(intervene=False, observe=True, record=True),
    role='persistent_state',
    schedule='provided_by_external_writer',
    quantization='ieee754_binary64',
    metric='absolute_difference',
    epsilon=1.0,
    access_interventions=(),
    research_trace=True,
    observation_protocols=('research.full.v1', 'agent.weather.v1'),

    valid_domain='elevation_after_terrain_calibration_no_prior_closed_bound',

    external_source='not_applicable:space.terrain_generator_output',

    external_writer='space.terrain_generator',

)
SLOT_WORLD_GEN_HUMIDITY_NOISE = SlotDecl(
    id='world.gen.humidity_noise',
    on='lattice.chunk',
    persist='external',
    domain=ValueDomain(kind="float", minimum=-1.0, maximum=1.0, unit='dimensionless'),
    permissions=Permissions(intervene=False, observe=True, record=True),
    role='persistent_state',
    schedule='provided_by_external_writer',
    quantization='ieee754_binary64',
    metric='absolute_difference',
    epsilon=0.0,
    access_interventions=(),
    research_trace=True,
    observation_protocols=('research.full.v1', 'agent.weather.v1'),

    valid_domain='closed_interval_-1_1',

    external_source='not_applicable:space.terrain_generator_output',

    external_writer='space.terrain_generator',

)
SLOT_WORLD_GEN_LATITUDE_NOISE = SlotDecl(
    id='world.gen.latitude_noise',
    on='lattice.chunk',
    persist='external',
    domain=ValueDomain(kind="float", minimum=-1.0, maximum=1.0, unit='dimensionless'),
    permissions=Permissions(intervene=False, observe=True, record=True),
    role='persistent_state',
    schedule='provided_by_external_writer',
    quantization='ieee754_binary64',
    metric='absolute_difference',
    epsilon=0.0,
    access_interventions=(),
    research_trace=True,
    observation_protocols=('research.full.v1', 'agent.weather.v1'),

    valid_domain='closed_interval_-1_1',

    external_source='not_applicable:space.continent_generator_output',

    external_writer='space.continent_generator',

)
SLOT_WORLD_GEN_MOISTURE_NOISE = SlotDecl(
    id='world.gen.moisture_noise',
    on='lattice.chunk',
    persist='external',
    domain=ValueDomain(kind="float", minimum=-1.0, maximum=1.0, unit='dimensionless'),
    permissions=Permissions(intervene=False, observe=True, record=True),
    role='persistent_state',
    schedule='provided_by_external_writer',
    quantization='ieee754_binary64',
    metric='absolute_difference',
    epsilon=0.0,
    access_interventions=(),
    research_trace=True,
    observation_protocols=('research.full.v1', 'agent.weather.v1'),

    valid_domain='closed_interval_-1_1',

    external_source='not_applicable:space.terrain_generator_output',

    external_writer='space.terrain_generator',

)
SLOT_WORLD_GEN_RAINFALL_NOISE = SlotDecl(
    id='world.gen.rainfall_noise',
    on='lattice.chunk',
    persist='external',
    domain=ValueDomain(kind="float", minimum=-1.0, maximum=1.0, unit='dimensionless'),
    permissions=Permissions(intervene=False, observe=True, record=True),
    role='persistent_state',
    schedule='provided_by_external_writer',
    quantization='ieee754_binary64',
    metric='absolute_difference',
    epsilon=0.0,
    access_interventions=(),
    research_trace=True,
    observation_protocols=('research.full.v1', 'agent.weather.v1'),

    valid_domain='closed_interval_-1_1',

    external_source='not_applicable:space.continent_generator_output',

    external_writer='space.continent_generator',

)
SLOT_WORLD_GEN_WIND_NOISE = SlotDecl(
    id='world.gen.wind_noise',
    on='lattice.chunk',
    persist='external',
    domain=ValueDomain(kind="float", minimum=-1.0, maximum=1.0, unit='dimensionless'),
    permissions=Permissions(intervene=False, observe=True, record=True),
    role='persistent_state',
    schedule='provided_by_external_writer',
    quantization='ieee754_binary64',
    metric='absolute_difference',
    epsilon=0.0,
    access_interventions=(),
    research_trace=True,
    observation_protocols=('research.full.v1', 'agent.weather.v1'),

    valid_domain='closed_interval_-1_1',

    external_source='not_applicable:space.terrain_generator_output',

    external_writer='space.terrain_generator',

)

# ── 实现包装（ctx → 方程关键字参数）─────────────────────
def _impl_0(ctx: object) -> object:
    return _eq._sea_level_temperature_equation(
        latitude_noise=ctx.parent('latitude_noise'),
    )

def _impl_1(ctx: object) -> object:
    return _eq._rainfall_equation(
        rainfall_noise=ctx.parent('rainfall_noise'),
    )

def _impl_2(ctx: object) -> object:
    return _eq._lapse_rate_equation(
        sea_level_temperature=ctx.parent('sea_level_temperature'),
        altitude=ctx.parent('altitude'),
    )

def _impl_3(ctx: object) -> object:
    return _eq._climate_zone_equation(
        mean_temperature=ctx.parent('mean_temperature'),
        annual_rainfall=ctx.parent('annual_rainfall'),
        altitude=ctx.parent('altitude'),
    )

def _impl_4(ctx: object) -> object:
    return _eq._biome_equation(
        mean_temperature=ctx.parent('mean_temperature'),
        annual_rainfall=ctx.parent('annual_rainfall'),
        altitude=ctx.parent('altitude'),
        sea_level_temperature=ctx.parent('sea_level_temperature'),
        moisture_noise=ctx.parent('moisture_noise'),
    )

def _impl_5(ctx: object) -> object:
    return _eq._baseline_humidity_equation(
        climate_zone=ctx.parent('climate_zone'),
        humidity_noise=ctx.parent('humidity_noise'),
    )

def _impl_6(ctx: object) -> object:
    return _eq._baseline_wind_speed_equation(
        climate_zone=ctx.parent('climate_zone'),
        wind_noise=ctx.parent('wind_noise'),
    )

def _impl_7(ctx: object) -> object:
    return _eq._mean_precip_intensity_equation(
        climate_zone=ctx.parent('climate_zone'),
    )

def _impl_8(ctx: object) -> object:
    return _eq._humidity_sharpness_equation(
        climate_zone=ctx.parent('climate_zone'),
    )


# ── 见证（只变单父的见证对）──────────────────────────────
WITNESSES_0 = (
    Witness(
        'latitude_noise_changes_sea_level_temperature.base',
        {'latitude_noise': 0.0},
        (10.0,),
        {},
    ),
    Witness(
        'latitude_noise_changes_sea_level_temperature.alt',
        {'latitude_noise': 0.4},
        (20.000000009313226,),
        {},
    ),
)

WITNESSES_1 = (
    Witness(
        'rainfall_noise_changes_annual_rainfall.base',
        {'rainfall_noise': 0.0},
        (1775.0,),
        {},
    ),
    Witness(
        'rainfall_noise_changes_annual_rainfall.alt',
        {'rainfall_noise': 0.4},
        (2465.0000006426126,),
        {},
    ),
)

WITNESSES_2 = (
    Witness(
        'sea_level_temperature_changes_annual_mean_temperature.base',
        {'altitude': 0.0, 'sea_level_temperature': 10.0},
        (10.0,),
        {},
    ),
    Witness(
        'sea_level_temperature_changes_annual_mean_temperature.alt',
        {'altitude': 0.0, 'sea_level_temperature': 20.0},
        (20.0,),
        {},
    ),
    Witness(
        'altitude_changes_annual_mean_temperature.base',
        {'altitude': 0.0, 'sea_level_temperature': 10.0},
        (10.0,),
        {},
    ),
    Witness(
        'altitude_changes_annual_mean_temperature.alt',
        {'altitude': 1000.0, 'sea_level_temperature': 10.0},
        (1.0,),
        {},
    ),
)

WITNESSES_3 = (
    Witness(
        'altitude_changes_climate_zone.base',
        {'altitude': 100.0, 'annual_rainfall': 800.0, 'mean_temperature': 10.0},
        (4,),
        {},
    ),
    Witness(
        'altitude_changes_climate_zone.alt',
        {'altitude': 3000.0, 'annual_rainfall': 800.0, 'mean_temperature': 10.0},
        (7,),
        {},
    ),
    Witness(
        'annual_rainfall_changes_climate_zone.base',
        {'altitude': 100.0, 'annual_rainfall': 800.0, 'mean_temperature': 10.0},
        (4,),
        {},
    ),
    Witness(
        'annual_rainfall_changes_climate_zone.alt',
        {'altitude': 100.0, 'annual_rainfall': 100.0, 'mean_temperature': 10.0},
        (2,),
        {},
    ),
    Witness(
        'annual_temperature_changes_climate_zone.base',
        {'altitude': 100.0, 'annual_rainfall': 800.0, 'mean_temperature': 10.0},
        (4,),
        {},
    ),
    Witness(
        'annual_temperature_changes_climate_zone.alt',
        {'altitude': 100.0, 'annual_rainfall': 800.0, 'mean_temperature': 25.0},
        (1,),
        {},
    ),
)

WITNESSES_4 = (
    Witness(
        'altitude_changes_biome_ocean_land.base',
        {'altitude': 100.0,
         'annual_rainfall': 800.0,
         'mean_temperature': 15.0,
         'moisture_noise': 0.0,
         'sea_level_temperature': 10.0},
        (9,),
        {},
    ),
    Witness(
        'altitude_changes_biome_ocean_land.alt',
        {'altitude': -100.0,
         'annual_rainfall': 800.0,
         'mean_temperature': 15.0,
         'moisture_noise': 0.0,
         'sea_level_temperature': 10.0},
        (17,),
        {},
    ),
    Witness(
        'annual_rainfall_changes_biome.base',
        {'altitude': 100.0,
         'annual_rainfall': 1000.0,
         'mean_temperature': 15.0,
         'moisture_noise': 0.0,
         'sea_level_temperature': 10.0},
        (9,),
        {},
    ),
    Witness(
        'annual_rainfall_changes_biome.alt',
        {'altitude': 100.0,
         'annual_rainfall': 100.0,
         'mean_temperature': 15.0,
         'moisture_noise': 0.0,
         'sea_level_temperature': 10.0},
        (4,),
        {},
    ),
    Witness(
        'annual_temperature_changes_biome.base',
        {'altitude': 100.0,
         'annual_rainfall': 800.0,
         'mean_temperature': 15.0,
         'moisture_noise': 0.0,
         'sea_level_temperature': 10.0},
        (9,),
        {},
    ),
    Witness(
        'annual_temperature_changes_biome.alt',
        {'altitude': 100.0,
         'annual_rainfall': 800.0,
         'mean_temperature': 25.0,
         'moisture_noise': 0.0,
         'sea_level_temperature': 10.0},
        (2,),
        {},
    ),
    Witness(
        'sea_level_temperature_changes_ocean_biome.base',
        {'altitude': -100.0,
         'annual_rainfall': 800.0,
         'mean_temperature': 15.0,
         'moisture_noise': 0.0,
         'sea_level_temperature': 10.0},
        (17,),
        {},
    ),
    Witness(
        'sea_level_temperature_changes_ocean_biome.alt',
        {'altitude': -100.0,
         'annual_rainfall': 800.0,
         'mean_temperature': 15.0,
         'moisture_noise': 0.0,
         'sea_level_temperature': 25.0},
        (16,),
        {},
    ),
    Witness(
        'moisture_noise_changes_desert_biome.base',
        {'altitude': 100.0,
         'annual_rainfall': 100.0,
         'mean_temperature': 15.0,
         'moisture_noise': 0.0,
         'sea_level_temperature': 10.0},
        (4,),
        {},
    ),
    Witness(
        'moisture_noise_changes_desert_biome.alt',
        {'altitude': 100.0,
         'annual_rainfall': 100.0,
         'mean_temperature': 15.0,
         'moisture_noise': 1.0,
         'sea_level_temperature': 10.0},
        (5,),
        {},
    ),
)

WITNESSES_5 = (
    Witness(
        'humidity_noise_changes_baseline_humidity.base',
        {'climate_zone': 4, 'humidity_noise': 0.0},
        (62.5,),
        {},
    ),
    Witness(
        'humidity_noise_changes_baseline_humidity.alt',
        {'climate_zone': 4, 'humidity_noise': 1.0},
        (80.0,),
        {},
    ),
    Witness(
        'climate_zone_changes_baseline_humidity.base',
        {'climate_zone': 4, 'humidity_noise': 0.0},
        (62.5,),
        {},
    ),
    Witness(
        'climate_zone_changes_baseline_humidity.alt',
        {'climate_zone': 2, 'humidity_noise': 0.0},
        (17.5,),
        {},
    ),
)

WITNESSES_6 = (
    Witness(
        'wind_noise_changes_baseline_wind_speed.base',
        {'climate_zone': 4, 'wind_noise': 0.0},
        (6.0,),
        {},
    ),
    Witness(
        'wind_noise_changes_baseline_wind_speed.alt',
        {'climate_zone': 4, 'wind_noise': 1.0},
        (12.0,),
        {},
    ),
    Witness(
        'climate_zone_changes_baseline_wind_speed.base',
        {'climate_zone': 4, 'wind_noise': 0.0},
        (6.0,),
        {},
    ),
    Witness(
        'climate_zone_changes_baseline_wind_speed.alt',
        {'climate_zone': 0, 'wind_noise': 0.0},
        (3.0,),
        {},
    ),
)

WITNESSES_7 = (
    Witness(
        'climate_zone_changes_mean_precip_intensity.base',
        {'climate_zone': 4},
        (5.0,),
        {},
    ),
    Witness(
        'climate_zone_changes_mean_precip_intensity.alt',
        {'climate_zone': 0},
        (10.0,),
        {},
    ),
)

WITNESSES_8 = (
    Witness(
        'climate_zone_changes_humidity_sharpness.base',
        {'climate_zone': 4},
        (0.0,),
        {},
    ),
    Witness(
        'climate_zone_changes_humidity_sharpness.alt',
        {'climate_zone': 1},
        (2.5,),
        {},
    ),
)

# ── 机制声明 ────────────────────────────────────────────
MECHANISM_0 = MechanismDecl(
    id='world.gen.derive_sea_level_temperature.v1',
    output='weather.chunk.sea_level_temperature_c',
    parents=(
        Parent(
            slot='world.gen.latitude_noise',
            argument='latitude_noise',
            lag=0,
            analysis_role='forward',
            valid_domain='closed_interval_-1_1',
            modulus_kind='linear',
            lipschitz=25.0,
            jump_bound=None,
            metric='absolute_difference',
        ),
    ),
    impl=_impl_0,
    when=When(mode='phase', key='world.gen_derived_a'),
    equation=(
        "clamp(latitude_noise * 25 + 10, -20, 38)（定点 Q30）"
    ),
    arithmetic=Arithmetic(domain='fixed', bits=30),
    params=(),
    witnesses=WITNESSES_0,
    boundary_cases=(
        'noise_below_-1.2:clamp_to_-20',
        'noise_above_1.12:clamp_to_38',
        'finite_interior:linear_mapping',
    ),

)

MECHANISM_1 = MechanismDecl(
    id='world.gen.derive_annual_rainfall.v1',
    output='weather.chunk.annual_rainfall_mm_per_year',
    parents=(
        Parent(
            slot='world.gen.rainfall_noise',
            argument='rainfall_noise',
            lag=0,
            analysis_role='forward',
            valid_domain='closed_interval_-1_1',
            modulus_kind='linear',
            lipschitz=1725.0,
            jump_bound=None,
            metric='absolute_difference',
        ),
    ),
    impl=_impl_1,
    when=When(mode='phase', key='world.gen_derived_a'),
    equation=(
        "clamp(rainfall_min + (noise + 1) * 0.5 * (rainfall_max - rai"
        "nfall_min), 0, 5000)（定点 Q30，min/max 由 config 注入）"
    ),
    arithmetic=Arithmetic(domain='fixed', bits=30),
    params=(),
    witnesses=WITNESSES_1,
    boundary_cases=(
        'noise_minus_one:rainfall_min',
        'noise_plus_one:rainfall_max',
        'finite_interior:linear_mapping',
    ),

)

MECHANISM_2 = MechanismDecl(
    id='world.gen.derive_annual_mean_temperature.v1',
    output='weather.chunk.annual_mean_temperature_c',
    parents=(
        Parent(
            slot='weather.chunk.sea_level_temperature_c',
            argument='sea_level_temperature',
            lag=0,
            analysis_role='forward',
            valid_domain='closed_interval_-20_38_degC',
            modulus_kind='linear',
            lipschitz=1.0,
            jump_bound=None,
            metric='absolute_difference',
        ),
        Parent(
            slot='world.gen.altitude_m',
            argument='altitude',
            lag=0,
            analysis_role='forward',
            valid_domain='elevation_range',
            modulus_kind='linear',
            lipschitz=0.009,
            jump_bound=None,
            metric='absolute_difference',
        ),
    ),
    impl=_impl_2,
    when=When(mode='phase', key='world.gen_derived_b'),
    equation=(
        "sea_level_temperature - altitude * lapse_rate / 1000 for alt"
        "itude > 0 else sea_level_temperature, clamp [-20, 36]（定点 Q30"
        "）"
    ),
    arithmetic=Arithmetic(domain='fixed', bits=30),
    params=(),
    witnesses=WITNESSES_2,
    boundary_cases=(
        'sea_surface:altitude_nonpositive_identity',
        'land:linear_lapse_rate',
        'temperature_below_-20:clamp',
        'temperature_above_36:clamp',
    ),

)

MECHANISM_3 = MechanismDecl(
    id='world.gen.classify_climate_zone.v1',
    output='world.gen.climate_zone',
    parents=(
        Parent(
            slot='weather.chunk.annual_mean_temperature_c',
            argument='mean_temperature',
            lag=0,
            analysis_role='forward',
            valid_domain='finite_temperature_within_declared_bounds',
            modulus_kind='linear',
            lipschitz=0.0,
            jump_bound=None,
            metric='absolute_difference',
        ),
        Parent(
            slot='weather.chunk.annual_rainfall_mm_per_year',
            argument='annual_rainfall',
            lag=0,
            analysis_role='forward',
            valid_domain='finite_nonnegative_annual_rainfall',
            modulus_kind='linear',
            lipschitz=0.0,
            jump_bound=None,
            metric='absolute_difference',
        ),
        Parent(
            slot='world.gen.altitude_m',
            argument='altitude',
            lag=0,
            analysis_role='forward',
            valid_domain='elevation_range',
            modulus_kind='linear',
            lipschitz=0.0,
            jump_bound=None,
            metric='absolute_difference',
        ),
    ),
    impl=_impl_3,
    when=When(mode='phase', key='world.gen_derived_c'),
    equation=(
        "8 档静态决策树（定点量化域整数比较，阈值由 config 注入）"
    ),
    arithmetic=Arithmetic(domain='fixed', bits=30),
    params=(),
    witnesses=WITNESSES_3,
    boundary_cases=(
        'altitude_above_alpine_threshold:ALPINE',
        'temperature_below_polar_threshold:POLAR_TUNDRA',
        'rainfall_below_desert_threshold:DESERT',
        'decision_tree_priority_order',
    ),

)

MECHANISM_4 = MechanismDecl(
    id='world.gen.classify_biome.v1',
    output='world.gen.biome',
    parents=(
        Parent(
            slot='weather.chunk.annual_mean_temperature_c',
            argument='mean_temperature',
            lag=0,
            analysis_role='forward',
            valid_domain='finite_temperature_within_declared_bounds',
            modulus_kind='linear',
            lipschitz=0.0,
            jump_bound=None,
            metric='absolute_difference',
        ),
        Parent(
            slot='weather.chunk.annual_rainfall_mm_per_year',
            argument='annual_rainfall',
            lag=0,
            analysis_role='forward',
            valid_domain='finite_nonnegative_annual_rainfall',
            modulus_kind='linear',
            lipschitz=0.0,
            jump_bound=None,
            metric='absolute_difference',
        ),
        Parent(
            slot='world.gen.altitude_m',
            argument='altitude',
            lag=0,
            analysis_role='forward',
            valid_domain='elevation_range',
            modulus_kind='linear',
            lipschitz=0.0,
            jump_bound=None,
            metric='absolute_difference',
        ),
        Parent(
            slot='weather.chunk.sea_level_temperature_c',
            argument='sea_level_temperature',
            lag=0,
            analysis_role='forward',
            valid_domain='closed_interval_-20_38_degC',
            modulus_kind='linear',
            lipschitz=0.0,
            jump_bound=None,
            metric='absolute_difference',
        ),
        Parent(
            slot='world.gen.moisture_noise',
            argument='moisture_noise',
            lag=0,
            analysis_role='forward',
            valid_domain='closed_interval_-1_1',
            modulus_kind='linear',
            lipschitz=0.0,
            jump_bound=None,
            metric='absolute_difference',
        ),
    ),
    impl=_impl_4,
    when=When(mode='phase', key='world.gen_derived_d'),
    equation=(
        "海洋按海面温度三档；陆地按气候档 + 细分维度三角隶属取主型（定点 Q30；静态细分值域，动态 subdiv_ranges 属大陆管线边界）"
    ),
    arithmetic=Arithmetic(domain='fixed', bits=30),
    params=(),
    witnesses=WITNESSES_4,
    boundary_cases=(
        'altitude_below_sea_level:ocean_by_sea_temperature',
        'land:climate_subdivision_membership',
        'no_subdivision_config:temperate_deciduous_fallback',
    ),

)

MECHANISM_5 = MechanismDecl(
    id='world.gen.derive_baseline_humidity.v1',
    output='weather.chunk.baseline_humidity_percent',
    parents=(
        Parent(
            slot='world.gen.climate_zone',
            argument='climate_zone',
            lag=0,
            analysis_role='forward',
            valid_domain='climate_zone_enum_0_7',
            modulus_kind='jump',
            lipschitz=None,
            jump_bound=90.0,
            metric='absolute_difference',
        ),
        Parent(
            slot='world.gen.humidity_noise',
            argument='humidity_noise',
            lag=0,
            analysis_role='forward',
            valid_domain='closed_interval_-1_1',
            modulus_kind='linear',
            lipschitz=20.0,
            jump_bound=None,
            metric='absolute_difference',
        ),
    ),
    impl=_impl_5,
    when=When(mode='phase', key='world.gen_derived_d'),
    equation=(
        "clamp(template_humidity_lo + (noise + 1) * 0.5 * (template_h"
        "umidity_hi - template_humidity_lo), 0, 100)（模板数据 data/climat"
        "e.json）"
    ),
    arithmetic=Arithmetic(domain='fixed', bits=30),
    params=(),
    witnesses=WITNESSES_5,
    boundary_cases=(
        'noise_minus_one:template_low',
        'noise_plus_one:template_high',
        'finite_interior:linear_interpolation',
    ),

)

MECHANISM_6 = MechanismDecl(
    id='world.gen.derive_baseline_wind_speed.v1',
    output='weather.chunk.baseline_wind_speed_mps',
    parents=(
        Parent(
            slot='world.gen.climate_zone',
            argument='climate_zone',
            lag=0,
            analysis_role='forward',
            valid_domain='climate_zone_enum_0_7',
            modulus_kind='jump',
            lipschitz=None,
            jump_bound=25.0,
            metric='absolute_difference',
        ),
        Parent(
            slot='world.gen.wind_noise',
            argument='wind_noise',
            lag=0,
            analysis_role='forward',
            valid_domain='closed_interval_-1_1',
            modulus_kind='linear',
            lipschitz=11.0,
            jump_bound=None,
            metric='absolute_difference',
        ),
    ),
    impl=_impl_6,
    when=When(mode='phase', key='world.gen_derived_d'),
    equation=(
        "clamp(template_wind_lo + (noise + 1) * 0.5 * (template_wind_"
        "hi - template_wind_lo), 0, 50)（模板数据 data/climate.json）"
    ),
    arithmetic=Arithmetic(domain='fixed', bits=30),
    params=(),
    witnesses=WITNESSES_6,
    boundary_cases=(
        'noise_minus_one:template_low',
        'noise_plus_one:template_high',
        'finite_interior:linear_interpolation',
    ),

)

MECHANISM_7 = MechanismDecl(
    id='world.gen.derive_mean_precip_intensity.v1',
    output='weather.chunk.mean_precip_intensity_mm_per_hour',
    parents=(
        Parent(
            slot='world.gen.climate_zone',
            argument='climate_zone',
            lag=0,
            analysis_role='forward',
            valid_domain='climate_zone_enum_0_7',
            modulus_kind='jump',
            lipschitz=None,
            jump_bound=8.0,
            metric='absolute_difference',
        ),
    ),
    impl=_impl_7,
    when=When(mode='phase', key='world.gen_derived_d'),
    equation=(
        "climate_template.mean_precip_intensity（数据契约）"
    ),
    arithmetic=Arithmetic(domain='fixed', bits=30),
    params=(),
    witnesses=WITNESSES_7,
    boundary_cases=('per_zone_lookup:data_contract',),

)

MECHANISM_8 = MechanismDecl(
    id='world.gen.derive_humidity_sharpness.v1',
    output='weather.chunk.humidity_sharpness',
    parents=(
        Parent(
            slot='world.gen.climate_zone',
            argument='climate_zone',
            lag=0,
            analysis_role='forward',
            valid_domain='climate_zone_enum_0_7',
            modulus_kind='jump',
            lipschitz=None,
            jump_bound=2.5,
            metric='absolute_difference',
        ),
    ),
    impl=_impl_8,
    when=When(mode='phase', key='world.gen_derived_d'),
    equation=(
        "climate_template.humidity_sharpness（数据契约）"
    ),
    arithmetic=Arithmetic(domain='fixed', bits=30),
    params=(),
    witnesses=WITNESSES_8,
    boundary_cases=('per_zone_lookup:data_contract',),

)

# ── 模块包 ──────────────────────────────────────────────
MODULE = ModulePack(
    id='worldgen',
    version='1',
    instances=(GLOBAL, CHUNK),
    parameters=(
        ParameterDecl(
            id='world.parameter.alpine_altitude_m',
            default=2000.0,
            minimum=0.0,
            maximum=100000.0,
            unit='meter',
            source='data/world.json#climate.ALPINE_ALTITUDE',
            intervention_allowed=True,
        ),
        ParameterDecl(
            id='world.parameter.desert_rainfall_mm',
            default=200.0,
            minimum=0.0,
            maximum=1000000.0,
            unit='mm_per_year',
            source='data/world.json#climate.DESERT_RAINFALL',
            intervention_allowed=True,
        ),
        ParameterDecl(
            id='world.parameter.lapse_rate_c_per_1000m',
            default=9.0,
            minimum=0.0,
            maximum=100.0,
            unit='degC_per_1000m',
            source='data/world.json#climate.LAPSE_RATE',
            intervention_allowed=True,
        ),
        ParameterDecl(
            id='world.parameter.polar_temp_c',
            default=-5.0,
            minimum=-273.15,
            maximum=100.0,
            unit='degC',
            source='data/world.json#climate.POLAR_TEMP',
            intervention_allowed=True,
        ),
        ParameterDecl(
            id='world.parameter.rainfall_max_mm',
            default=3500.0,
            minimum=0.0,
            maximum=1000000.0,
            unit='mm_per_year',
            source='data/world.json#climate.RAINFALL_MAX',
            intervention_allowed=True,
        ),
        ParameterDecl(
            id='world.parameter.rainfall_min_mm',
            default=50.0,
            minimum=0.0,
            maximum=1000000.0,
            unit='mm_per_year',
            source='data/world.json#climate.RAINFALL_MIN',
            intervention_allowed=True,
        ),
        ParameterDecl(
            id='world.parameter.rainforest_rainfall_mm',
            default=1500.0,
            minimum=0.0,
            maximum=1000000.0,
            unit='mm_per_year',
            source='data/world.json#climate.RAINFOREST_RAINFALL',
            intervention_allowed=True,
        ),
        ParameterDecl(
            id='world.parameter.sea_temperature_max_c',
            default=38.0,
            minimum=-273.15,
            maximum=1000.0,
            unit='degC',
            source='code-only:sea_temperature_max_c（内嵌 _hydrology.c，由 C 文件哈希覆盖）',
            intervention_allowed=True,
        ),
        ParameterDecl(
            id='world.parameter.sea_temperature_min_c',
            default=-20.0,
            minimum=-273.15,
            maximum=1000.0,
            unit='degC',
            source='code-only:sea_temperature_min_c（内嵌 _hydrology.c，由 C 文件哈希覆盖）',
            intervention_allowed=True,
        ),
        ParameterDecl(
            id='world.parameter.sea_temperature_offset_c',
            default=10.0,
            minimum=-273.15,
            maximum=1000.0,
            unit='degC',
            source='code-only:sea_temperature_offset_c（内嵌 _hydrology.c，由 C 文件哈希覆盖）',
            intervention_allowed=True,
        ),
        ParameterDecl(
            id='world.parameter.sea_temperature_scale_c',
            default=25.0,
            minimum=0.0,
            maximum=1000.0,
            unit='degC',
            source='code-only:sea_temperature_scale_c（内嵌 _hydrology.c，由 C 文件哈希覆盖）',
            intervention_allowed=True,
        ),
        ParameterDecl(
            id='world.parameter.steppe_min_temp_c',
            default=5.0,
            minimum=-273.15,
            maximum=100.0,
            unit='degC',
            source='data/world.json#climate.STEPPE_MIN_TEMP',
            intervention_allowed=True,
        ),
        ParameterDecl(
            id='world.parameter.steppe_rainfall_mm',
            default=600.0,
            minimum=0.0,
            maximum=1000000.0,
            unit='mm_per_year',
            source='data/world.json#climate.STEPPE_RAINFALL',
            intervention_allowed=True,
        ),
        ParameterDecl(
            id='world.parameter.taiga_rainfall_mm',
            default=400.0,
            minimum=0.0,
            maximum=1000000.0,
            unit='mm_per_year',
            source='data/world.json#climate.TAIGA_RAINFALL',
            intervention_allowed=True,
        ),
        ParameterDecl(
            id='world.parameter.temperate_temp_c',
            default=5.0,
            minimum=-273.15,
            maximum=100.0,
            unit='degC',
            source='data/world.json#climate.TEMPERATE_TEMP',
            intervention_allowed=True,
        ),
        ParameterDecl(
            id='world.parameter.tropical_temp_c',
            default=20.0,
            minimum=-273.15,
            maximum=100.0,
            unit='degC',
            source='data/world.json#climate.TROPICAL_TEMP',
            intervention_allowed=True,
        ),
    ),
    slots=(
        SLOT_WEATHER_CHUNK_ANNUAL_MEAN_TEMPERATURE_C,
        SLOT_WEATHER_CHUNK_ANNUAL_RAINFALL_MM_PER_YEAR,
        SLOT_WEATHER_CHUNK_BASELINE_HUMIDITY_PERCENT,
        SLOT_WEATHER_CHUNK_BASELINE_WIND_SPEED_MPS,
        SLOT_WEATHER_CHUNK_HUMIDITY_SHARPNESS,
        SLOT_WEATHER_CHUNK_MEAN_PRECIP_INTENSITY_MM_PER_HOUR,
        SLOT_WEATHER_CHUNK_SEA_LEVEL_TEMPERATURE_C,
        SLOT_WORLD_GEN_BIOME,
        SLOT_WORLD_GEN_CLIMATE_ZONE,
        SLOT_WORLD_GEN_ALTITUDE_M,
        SLOT_WORLD_GEN_HUMIDITY_NOISE,
        SLOT_WORLD_GEN_LATITUDE_NOISE,
        SLOT_WORLD_GEN_MOISTURE_NOISE,
        SLOT_WORLD_GEN_RAINFALL_NOISE,
        SLOT_WORLD_GEN_WIND_NOISE,
    ),
    mechanisms=(
        MECHANISM_0,
        MECHANISM_1,
        MECHANISM_2,
        MECHANISM_3,
        MECHANISM_4,
        MECHANISM_5,
        MECHANISM_6,
        MECHANISM_7,
        MECHANISM_8,
    ),
    evidence=('定点实现与浮点参考对拍（testbench/world/test_worldgen_fixed.py）',),
    notes='世界生成标量机制：海面温度、降雨、温度递减、气候档与群系判定。',
)
