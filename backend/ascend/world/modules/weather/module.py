"""旧注册表机制声明（P1 逐位移植；生成后即为源码）。

声明形态已转换为新元模型（六种声明）：输出为 derived 槽位、
旧微步 → 新阶段、旧参数绑定 → ParameterDecl + ``ctx.param``、
旧见证 → 只变单父的见证对。生成来源与改写规则见
``/tmp`` 的一次性生成器说明（P2 删除旧注册表后本注记改为历史）。
"""
from __future__ import annotations

from ascend.world.meta.declarations import (
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
from ascend.world.modules.primitives import GLOBAL

CHUNK = InstanceDecl(
    id='lattice.chunk', kind='lattice', identity='xy', size=None,
)

from . import equations as _eq


# ── 槽位（输出 derived + 边界 external）─────────────────
SLOT_WEATHER_ASTRONOMY_DAYLIGHT_HOURS = SlotDecl(
    id='weather.astronomy.daylight_hours',
    on='lattice.chunk',
    persist='derived',
    domain=ValueDomain(kind="float", minimum=0.0, maximum=24.0, unit='hour'),
    writer='weather.astronomy.derive_daylight.v1',
    recompute='机制 weather.astronomy.derive_daylight.v1',
    permissions=Permissions(intervene=True, observe=True, record=True),
)
SLOT_WEATHER_ASTRONOMY_SUNRISE_HOUR = SlotDecl(
    id='weather.astronomy.sunrise_hour',
    on='lattice.chunk',
    persist='derived',
    domain=ValueDomain(kind="float", minimum=0.0, maximum=12.0, unit='hour'),
    writer='weather.astronomy.derive_sunrise.v1',
    recompute='机制 weather.astronomy.derive_sunrise.v1',
    permissions=Permissions(intervene=True, observe=True, record=True),
)
SLOT_WEATHER_ASTRONOMY_SUNSET_HOUR = SlotDecl(
    id='weather.astronomy.sunset_hour',
    on='lattice.chunk',
    persist='derived',
    domain=ValueDomain(kind="float", minimum=12.0, maximum=24.0, unit='hour'),
    writer='weather.astronomy.derive_sunset.v1',
    recompute='机制 weather.astronomy.derive_sunset.v1',
    permissions=Permissions(intervene=True, observe=True, record=True),
)
SLOT_WEATHER_CHUNK_DIURNAL_HUMIDITY_AMPLITUDE_PP = SlotDecl(
    id='weather.chunk.diurnal_humidity_amplitude_pp',
    on='lattice.chunk',
    persist='derived',
    domain=ValueDomain(kind="float", minimum=0.0, maximum=20.0, unit='pp'),
    writer='weather.chunk.derive_diurnal_humidity_amplitude.v1',
    recompute='机制 weather.chunk.derive_diurnal_humidity_amplitude.v1',
    permissions=Permissions(intervene=True, observe=True, record=True),
)
SLOT_WEATHER_CHUNK_DIURNAL_TEMPERATURE_AMPLITUDE_C = SlotDecl(
    id='weather.chunk.diurnal_temperature_amplitude_c',
    on='lattice.chunk',
    persist='derived',
    domain=ValueDomain(kind="float", minimum=0.0, maximum=20.0, unit='degC'),
    writer='weather.chunk.derive_diurnal_temperature_amplitude.v1',
    recompute='机制 weather.chunk.derive_diurnal_temperature_amplitude.v1',
    permissions=Permissions(intervene=True, observe=True, record=True),
)
SLOT_WEATHER_CHUNK_PRECIPITATION_THRESHOLD = SlotDecl(
    id='weather.chunk.precipitation_threshold',
    on='lattice.chunk',
    persist='derived',
    domain=ValueDomain(kind="float", minimum=0.25, maximum=0.55, unit='dimensionless'),
    writer='weather.chunk.derive_precipitation_threshold.v1',
    recompute='机制 weather.chunk.derive_precipitation_threshold.v1',
    permissions=Permissions(intervene=True, observe=True, record=True),
)
SLOT_WEATHER_CHUNK_SEASONAL_HUMIDITY_AMPLITUDE_PP = SlotDecl(
    id='weather.chunk.seasonal_humidity_amplitude_pp',
    on='lattice.chunk',
    persist='derived',
    domain=ValueDomain(kind="float", minimum=0.0, maximum=20.0, unit='pp'),
    writer='weather.chunk.derive_seasonal_humidity_amplitude.v1',
    recompute='机制 weather.chunk.derive_seasonal_humidity_amplitude.v1',
    permissions=Permissions(intervene=True, observe=True, record=True),
)
SLOT_WEATHER_CHUNK_SEASONAL_TEMPERATURE_AMPLITUDE_C = SlotDecl(
    id='weather.chunk.seasonal_temperature_amplitude_c',
    on='lattice.chunk',
    persist='derived',
    domain=ValueDomain(kind="float", minimum=1.0, maximum=30.0, unit='degC'),
    writer='weather.chunk.derive_seasonal_temperature_amplitude.v1',
    recompute='机制 weather.chunk.derive_seasonal_temperature_amplitude.v1',
    permissions=Permissions(intervene=True, observe=True, record=True),
)
SLOT_WEATHER_CHUNK_SOLAR_LATITUDE_PROXY_DEG = SlotDecl(
    id='weather.chunk.solar_latitude_proxy_deg',
    on='lattice.chunk',
    persist='derived',
    domain=ValueDomain(kind="float", minimum=0.0, maximum=80.0, unit='degree_north'),
    writer='weather.chunk.derive_solar_latitude_proxy.v1',
    recompute='机制 weather.chunk.derive_solar_latitude_proxy.v1',
    permissions=Permissions(intervene=True, observe=True, record=True),
)
SLOT_WEATHER_INSTANT_PRECIPITATION_INTENSITY_MM_PER_HOUR = SlotDecl(
    id='weather.instant.precipitation_intensity_mm_per_hour',
    on='lattice.chunk',
    persist='derived',
    domain=ValueDomain(kind="float", minimum=0.0, maximum=100.0, unit='mm_per_hour'),
    writer='weather.instant.compose_precipitation_intensity.v1',
    recompute='机制 weather.instant.compose_precipitation_intensity.v1',
    permissions=Permissions(intervene=True, observe=True, record=True),
)
SLOT_WEATHER_INSTANT_PRECIPITATION_TYPE = SlotDecl(
    id='weather.instant.precipitation_type',
    on='lattice.chunk',
    persist='derived',
    domain=ValueDomain(kind="enum", choices=('snow', 'rain')),
    writer='weather.instant.classify_precipitation_type.v1',
    recompute='机制 weather.instant.classify_precipitation_type.v1',
    permissions=Permissions(intervene=True, observe=True, record=True),
)
SLOT_WEATHER_INSTANT_RELATIVE_HUMIDITY_PERCENT = SlotDecl(
    id='weather.instant.relative_humidity_percent',
    on='lattice.chunk',
    persist='derived',
    domain=ValueDomain(kind="float", minimum=0.0, maximum=100.0, unit='percent'),
    writer='weather.instant.compose_humidity.v1',
    recompute='机制 weather.instant.compose_humidity.v1',
    permissions=Permissions(intervene=True, observe=True, record=True),
)
SLOT_WEATHER_INSTANT_SUNSHINE_HOURS_PER_DAY = SlotDecl(
    id='weather.instant.sunshine_hours_per_day',
    on='lattice.chunk',
    persist='derived',
    domain=ValueDomain(kind="float", minimum=0.0, maximum=24.0, unit='hour_per_day'),
    writer='weather.instant.compose_sunshine.v1',
    recompute='机制 weather.instant.compose_sunshine.v1',
    permissions=Permissions(intervene=True, observe=True, record=True),
)
SLOT_WEATHER_INSTANT_TEMPERATURE_C = SlotDecl(
    id='weather.instant.temperature_c',
    on='lattice.chunk',
    persist='derived',
    domain=ValueDomain(kind="float", minimum=-30.0, maximum=50.0, unit='degC'),
    writer='weather.instant.compose_temperature.v1',
    recompute='机制 weather.instant.compose_temperature.v1',
    permissions=Permissions(intervene=True, observe=True, record=True),
)
SLOT_WEATHER_INSTANT_WIND_SPEED_MPS = SlotDecl(
    id='weather.instant.wind_speed_mps',
    on='lattice.chunk',
    persist='derived',
    domain=ValueDomain(kind="float", minimum=0.0, maximum=50.0, unit='mps'),
    writer='weather.instant.compose_wind_speed.v1',
    recompute='机制 weather.instant.compose_wind_speed.v1',
    permissions=Permissions(intervene=True, observe=True, record=True),
)
SLOT_WEATHER_OFFSET_DIURNAL_HUMIDITY_PP = SlotDecl(
    id='weather.offset.diurnal_humidity_pp',
    on='lattice.chunk',
    persist='derived',
    domain=ValueDomain(kind="float", minimum=-20.0, maximum=20.0, unit='pp'),
    writer='weather.offset.derive_diurnal_humidity.v1',
    recompute='机制 weather.offset.derive_diurnal_humidity.v1',
    permissions=Permissions(intervene=True, observe=True, record=True),
)
SLOT_WEATHER_OFFSET_DIURNAL_TEMPERATURE_C = SlotDecl(
    id='weather.offset.diurnal_temperature_c',
    on='lattice.chunk',
    persist='derived',
    domain=ValueDomain(kind="float", minimum=-20.0, maximum=20.0, unit='degC'),
    writer='weather.offset.derive_diurnal_temperature.v1',
    recompute='机制 weather.offset.derive_diurnal_temperature.v1',
    permissions=Permissions(intervene=True, observe=True, record=True),
)
SLOT_WEATHER_OFFSET_SEASONAL_HUMIDITY_PP = SlotDecl(
    id='weather.offset.seasonal_humidity_pp',
    on='lattice.chunk',
    persist='derived',
    domain=ValueDomain(kind="float", minimum=-20.0, maximum=20.0, unit='pp'),
    writer='weather.offset.derive_seasonal_humidity.v1',
    recompute='机制 weather.offset.derive_seasonal_humidity.v1',
    permissions=Permissions(intervene=True, observe=True, record=True),
)
SLOT_WEATHER_OFFSET_SEASONAL_TEMPERATURE_C = SlotDecl(
    id='weather.offset.seasonal_temperature_c',
    on='lattice.chunk',
    persist='derived',
    domain=ValueDomain(kind="float", minimum=-30.0, maximum=30.0, unit='degC'),
    writer='weather.offset.derive_seasonal_temperature.v1',
    recompute='机制 weather.offset.derive_seasonal_temperature.v1',
    permissions=Permissions(intervene=True, observe=True, record=True),
)
SLOT_WEATHER_TICK_DAY = SlotDecl(
    id='weather.tick.day',
    on='global',
    persist='derived',
    domain=ValueDomain(kind="int", bits=64, minimum=None, maximum=None, unit='game_day'),
    writer='weather.tick.derive_day.v1',
    recompute='机制 weather.tick.derive_day.v1',
    permissions=Permissions(intervene=True, observe=True, record=True),
)
SLOT_WEATHER_TICK_DAY_OF_YEAR = SlotDecl(
    id='weather.tick.day_of_year',
    on='global',
    persist='derived',
    domain=ValueDomain(kind="int", bits=64, minimum=0, maximum=360, unit='game_day'),
    writer='weather.tick.derive_day_of_year.v1',
    recompute='机制 weather.tick.derive_day_of_year.v1',
    permissions=Permissions(intervene=True, observe=True, record=True),
)
SLOT_WEATHER_TICK_DIURNAL_PHASE_COS = SlotDecl(
    id='weather.tick.diurnal_phase_cos',
    on='global',
    persist='derived',
    domain=ValueDomain(kind="float", minimum=-1.0, maximum=1.0, unit='dimensionless'),
    writer='weather.tick.derive_diurnal_phase_cos.v1',
    recompute='机制 weather.tick.derive_diurnal_phase_cos.v1',
    permissions=Permissions(intervene=True, observe=True, record=True),
)
SLOT_WEATHER_TICK_HOUR_OF_DAY = SlotDecl(
    id='weather.tick.hour_of_day',
    on='global',
    persist='derived',
    domain=ValueDomain(kind="float", minimum=0.0, maximum=24.0, unit='hour'),
    writer='weather.tick.derive_hour_of_day.v1',
    recompute='机制 weather.tick.derive_hour_of_day.v1',
    permissions=Permissions(intervene=True, observe=True, record=True),
)
SLOT_WEATHER_TICK_SEASON = SlotDecl(
    id='weather.tick.season',
    on='global',
    persist='derived',
    domain=ValueDomain(kind="enum", choices=(0, 1, 2, 3)),
    writer='weather.tick.derive_season.v1',
    recompute='机制 weather.tick.derive_season.v1',
    permissions=Permissions(intervene=True, observe=True, record=True),
)
SLOT_WEATHER_TICK_SEASON_PHASE_COS = SlotDecl(
    id='weather.tick.season_phase_cos',
    on='global',
    persist='derived',
    domain=ValueDomain(kind="float", minimum=-1.0, maximum=1.0, unit='dimensionless'),
    writer='weather.tick.derive_season_phase_cos.v1',
    recompute='机制 weather.tick.derive_season_phase_cos.v1',
    permissions=Permissions(intervene=True, observe=True, record=True),
)
SLOT_WEATHER_TICK_SOLAR_DECLINATION_RAD = SlotDecl(
    id='weather.tick.solar_declination_rad',
    on='global',
    persist='derived',
    domain=ValueDomain(kind="float", minimum=-0.5, maximum=0.5, unit='radian'),
    writer='weather.tick.derive_solar_declination.v1',
    recompute='机制 weather.tick.derive_solar_declination.v1',
    permissions=Permissions(intervene=True, observe=True, record=True),
)
SLOT_WEATHER_FIELD_HUMIDITY_PERTURBATION = SlotDecl(
    id='weather.field.humidity_perturbation',
    on='lattice.chunk',
    persist='external',
    domain=ValueDomain(kind="float", minimum=-2.0, maximum=2.0, unit='dimensionless'),
    permissions=Permissions(intervene=False, observe=True, record=True),
)
SLOT_WEATHER_FIELD_PRECIPITATION_SIGNAL = SlotDecl(
    id='weather.field.precipitation_signal',
    on='lattice.chunk',
    persist='external',
    domain=ValueDomain(kind="float", minimum=0.0, maximum=10.0, unit='dimensionless'),
    permissions=Permissions(intervene=False, observe=True, record=True),
)
SLOT_WEATHER_FIELD_TEMPERATURE_PERTURBATION = SlotDecl(
    id='weather.field.temperature_perturbation',
    on='lattice.chunk',
    persist='external',
    domain=ValueDomain(kind="float", minimum=-20.0, maximum=20.0, unit='dimensionless'),
    permissions=Permissions(intervene=False, observe=True, record=True),
)
SLOT_WEATHER_FIELD_WIND_MULTIPLIER = SlotDecl(
    id='weather.field.wind_multiplier',
    on='lattice.chunk',
    persist='external',
    domain=ValueDomain(kind="float", minimum=1.0, maximum=100.0, unit='dimensionless'),
    permissions=Permissions(intervene=False, observe=True, record=True),
)
SLOT_WEATHER_FIELD_WIND_PERTURBATION = SlotDecl(
    id='weather.field.wind_perturbation',
    on='lattice.chunk',
    persist='external',
    domain=ValueDomain(kind="float", minimum=-2.0, maximum=2.0, unit='dimensionless'),
    permissions=Permissions(intervene=False, observe=True, record=True),
)
SLOT_WORLD_CLOCK_TICK = SlotDecl(
    id='world.clock.tick',
    on='global',
    persist='external',
    domain=ValueDomain(kind="int", bits=64, minimum=None, maximum=None, unit='tick'),
    permissions=Permissions(intervene=False, observe=True, record=True),
)

# ── 实现包装（ctx → 方程关键字参数）─────────────────────
def _impl_0(ctx: object) -> object:
    return _eq._derive_latitude_equation(
        sea_level_temperature=ctx.parent('sea_level_temperature'),
        input_min=ctx.param('weather.parameter.latitude.input_min_c'),
        input_max=ctx.param('weather.parameter.latitude.input_max_c'),
        output_min=ctx.param('weather.parameter.latitude.output_min_deg'),
        output_max=ctx.param('weather.parameter.latitude.output_max_deg'),
    )

def _impl_1(ctx: object) -> object:
    return _eq._derive_seasonal_amplitude_equation(
        annual_temperature=ctx.parent('annual_temperature'),
        annual_rainfall=ctx.parent('annual_rainfall'),
        input_temp_min=ctx.param('weather.parameter.seasonal_amplitude.input_min_c'),
        input_temp_max=ctx.param('weather.parameter.seasonal_amplitude.input_max_c'),
        cold_endpoint=ctx.param('weather.parameter.seasonal_amplitude.cold_endpoint_c'),
        hot_endpoint=ctx.param('weather.parameter.seasonal_amplitude.hot_endpoint_c'),
        rain_reference=ctx.param(
            'weather.parameter.seasonal_amplitude.rain_reference_mm_per_year',
        ),
        rain_bonus_scale=ctx.param('weather.parameter.seasonal_amplitude.rain_bonus_c'),
        output_min=ctx.param('weather.parameter.seasonal_amplitude.output_min_c'),
        output_max=ctx.param('weather.parameter.seasonal_amplitude.output_max_c'),
    )

def _impl_2(ctx: object) -> object:
    return _eq._diurnal_amplitude_equation(
        seasonal_amplitude=ctx.parent('seasonal_amplitude'),
        diurnal_to_seasonal_ratio=ctx.param(
            'world.parameter.diurnal_to_seasonal_ratio',
        ),
    )

def _impl_3(ctx: object) -> object:
    return _eq._humidity_seasonal_amplitude_equation(
        seasonal_amplitude=ctx.parent('seasonal_amplitude'),
        humidity_seasonal_scale=ctx.param('world.parameter.humidity_seasonal_scale'),
    )

def _impl_4(ctx: object) -> object:
    return _eq._humidity_diurnal_amplitude_equation(
        seasonal_amplitude=ctx.parent('seasonal_amplitude'),
        diurnal_to_seasonal_ratio=ctx.param(
            'world.parameter.diurnal_to_seasonal_ratio',
        ),
        humidity_diurnal_scale=ctx.param('world.parameter.humidity_diurnal_scale'),
    )

def _impl_5(ctx: object) -> object:
    return _eq._precip_threshold_equation(
        annual_rainfall=ctx.parent('annual_rainfall'),
        annual_dry=ctx.param('world.parameter.precip_annual_dry_mm'),
        annual_wet=ctx.param('world.parameter.precip_annual_wet_mm'),
        threshold_dry=ctx.param('world.parameter.precip_threshold_dry'),
        threshold_wet=ctx.param('world.parameter.precip_threshold_wet'),
    )

def _impl_6(ctx: object) -> object:
    return _eq._day_equation(
        tick=ctx.parent('tick'),
        game_day=ctx.param('world.parameter.game_day_ticks'),
    )

def _impl_7(ctx: object) -> object:
    return _eq._day_of_year_equation(
        tick=ctx.parent('tick'),
        game_day=ctx.param('world.parameter.game_day_ticks'),
        days_per_year=ctx.param('world.parameter.days_per_year'),
    )

def _impl_8(ctx: object) -> object:
    return _eq._hour_equation(
        tick=ctx.parent('tick'),
        game_day=ctx.param('world.parameter.game_day_ticks'),
        game_hour=ctx.param('world.parameter.game_hour_ticks'),
    )

def _impl_9(ctx: object) -> object:
    return _eq._season_equation(
        day=ctx.parent('day'),
        season_length_days=ctx.param('world.parameter.season_length_days'),
        seasons_per_year=ctx.param('world.parameter.seasons_per_year'),
    )

def _impl_10(ctx: object) -> object:
    return _eq._season_phase_cos_equation(
        day=ctx.parent('day'),
        season_length_days=ctx.param('world.parameter.season_length_days'),
        seasons_per_year=ctx.param('world.parameter.seasons_per_year'),
    )

def _impl_11(ctx: object) -> object:
    return _eq._diurnal_phase_cos_equation(
        hour=ctx.parent('hour'),
        peak_hour=ctx.param('world.parameter.diurnal_peak_hour'),
    )

def _impl_12(ctx: object) -> object:
    return _eq._solar_declination_equation(
        day_of_year=ctx.parent('day_of_year'),
        obliquity_deg=ctx.param('world.parameter.obliquity_deg'),
        days_per_year=ctx.param('world.parameter.days_per_year'),
    )

def _impl_13(ctx: object) -> object:
    return _eq._seasonal_temperature_offset_equation(
        amplitude=ctx.parent('amplitude'),
        season_phase_cos=ctx.parent('season_phase_cos'),
    )

def _impl_14(ctx: object) -> object:
    return _eq._diurnal_temperature_offset_equation(
        amplitude=ctx.parent('amplitude'),
        diurnal_phase_cos=ctx.parent('diurnal_phase_cos'),
    )

def _impl_15(ctx: object) -> object:
    return _eq._seasonal_humidity_offset_equation(
        amplitude=ctx.parent('amplitude'),
        season_phase_cos=ctx.parent('season_phase_cos'),
        sharpness=ctx.parent('sharpness'),
    )

def _impl_16(ctx: object) -> object:
    return _eq._diurnal_humidity_offset_equation(
        amplitude=ctx.parent('amplitude'),
        diurnal_phase_cos=ctx.parent('diurnal_phase_cos'),
    )

def _impl_17(ctx: object) -> object:
    return _eq._sunrise_equation(
        latitude=ctx.parent('latitude'),
        solar_declination=ctx.parent('solar_declination'),
    )

def _impl_18(ctx: object) -> object:
    return _eq._sunset_equation(
        latitude=ctx.parent('latitude'),
        solar_declination=ctx.parent('solar_declination'),
    )

def _impl_19(ctx: object) -> object:
    return _eq._daylight_equation(
        sunrise=ctx.parent('sunrise'),
        sunset=ctx.parent('sunset'),
    )

def _impl_20(ctx: object) -> object:
    return _eq._temperature_equation(
        annual_temperature=ctx.parent('annual_temperature'),
        seasonal_offset=ctx.parent('seasonal_offset'),
        diurnal_offset=ctx.parent('diurnal_offset'),
        perturbation=ctx.parent('perturbation'),
        perturb_scale=ctx.param('world.parameter.temp_perturb_scale_c'),
        lower_bound=ctx.param('world.parameter.temp_bound_min_c'),
        upper_bound=ctx.param('world.parameter.temp_bound_max_c'),
    )

def _impl_21(ctx: object) -> object:
    return _eq._humidity_equation(
        baseline=ctx.parent('baseline'),
        seasonal_offset=ctx.parent('seasonal_offset'),
        diurnal_offset=ctx.parent('diurnal_offset'),
        perturbation=ctx.parent('perturbation'),
        perturb_scale=ctx.param('world.parameter.humidity_perturb_scale_pp'),
        lower_bound=ctx.param('world.parameter.humidity_bound_min_pp'),
        upper_bound=ctx.param('world.parameter.humidity_bound_max_pp'),
    )

def _impl_22(ctx: object) -> object:
    return _eq._wind_equation(
        baseline=ctx.parent('baseline'),
        perturbation=ctx.parent('perturbation'),
        multiplier=ctx.parent('multiplier'),
        perturb_scale=ctx.param('world.parameter.wind_perturb_scale_mps'),
        lower_bound=ctx.param('world.parameter.wind_bound_min_mps'),
        upper_bound=ctx.param('world.parameter.wind_bound_max_mps'),
    )

def _impl_23(ctx: object) -> object:
    return _eq._precipitation_intensity_equation(
        signal=ctx.parent('signal'),
        threshold=ctx.parent('threshold'),
        mean_intensity=ctx.parent('mean_intensity'),
        signal_max=ctx.param('world.parameter.precip_signal_max'),
        intensity_scale=ctx.param('world.parameter.precip_intensity_scale'),
    )

def _impl_24(ctx: object) -> object:
    return _eq._sunshine_equation(
        daylight_hours=ctx.parent('daylight_hours'),
        perturbation=ctx.parent('perturbation'),
        perturb_scale=ctx.param('world.parameter.sunshine_perturb_scale_h'),
        lower_bound=ctx.param('world.parameter.sunshine_bound_min_h'),
        upper_bound=ctx.param('world.parameter.sunshine_bound_max_h'),
    )

def _impl_25(ctx: object) -> object:
    return _eq._precipitation_type_equation(
        instant_temperature=ctx.parent('instant_temperature'),
    )


# ── 见证（旧见证 → 只变单父的见证对）────────────────────
WITNESSES_0 = (
    Witness(
        'sea_level_temperature_changes_latitude.base',
        {'sea_level_temperature': -5.0},
        (80.0,),
        {'weather.parameter.latitude.input_max_c': 35.0,
         'weather.parameter.latitude.input_min_c': -5.0,
         'weather.parameter.latitude.output_max_deg': 80.0,
         'weather.parameter.latitude.output_min_deg': 0.0},
    ),
    Witness(
        'sea_level_temperature_changes_latitude.alt',
        {'sea_level_temperature': 35.0},
        (0.0,),
        {'weather.parameter.latitude.input_max_c': 35.0,
         'weather.parameter.latitude.input_min_c': -5.0,
         'weather.parameter.latitude.output_max_deg': 80.0,
         'weather.parameter.latitude.output_min_deg': 0.0},
    ),
)

WITNESSES_1 = (
    Witness(
        'annual_temperature_changes_amplitude.base',
        {'annual_rainfall': 800.0, 'annual_temperature': 0.0},
        (27.149999998509884,),
        {'weather.parameter.seasonal_amplitude.cold_endpoint_c': 28.0,
         'weather.parameter.seasonal_amplitude.hot_endpoint_c': 2.0,
         'weather.parameter.seasonal_amplitude.input_max_c': 35.0,
         'weather.parameter.seasonal_amplitude.input_min_c': -5.0,
         'weather.parameter.seasonal_amplitude.output_max_c': 30.0,
         'weather.parameter.seasonal_amplitude.output_min_c': 1.0,
         'weather.parameter.seasonal_amplitude.rain_bonus_c': 4.0,
         'weather.parameter.seasonal_amplitude.rain_reference_mm_per_year': 2000.0},
    ),
    Witness(
        'annual_temperature_changes_amplitude.alt',
        {'annual_rainfall': 800.0, 'annual_temperature': 20.0},
        (14.149999998509884,),
        {'weather.parameter.seasonal_amplitude.cold_endpoint_c': 28.0,
         'weather.parameter.seasonal_amplitude.hot_endpoint_c': 2.0,
         'weather.parameter.seasonal_amplitude.input_max_c': 35.0,
         'weather.parameter.seasonal_amplitude.input_min_c': -5.0,
         'weather.parameter.seasonal_amplitude.output_max_c': 30.0,
         'weather.parameter.seasonal_amplitude.output_min_c': 1.0,
         'weather.parameter.seasonal_amplitude.rain_bonus_c': 4.0,
         'weather.parameter.seasonal_amplitude.rain_reference_mm_per_year': 2000.0},
    ),
    Witness(
        'annual_rainfall_changes_amplitude.base',
        {'annual_rainfall': 200.0, 'annual_temperature': 15.0},
        (18.600000001490116,),
        {'weather.parameter.seasonal_amplitude.cold_endpoint_c': 28.0,
         'weather.parameter.seasonal_amplitude.hot_endpoint_c': 2.0,
         'weather.parameter.seasonal_amplitude.input_max_c': 35.0,
         'weather.parameter.seasonal_amplitude.input_min_c': -5.0,
         'weather.parameter.seasonal_amplitude.output_max_c': 30.0,
         'weather.parameter.seasonal_amplitude.output_min_c': 1.0,
         'weather.parameter.seasonal_amplitude.rain_bonus_c': 4.0,
         'weather.parameter.seasonal_amplitude.rain_reference_mm_per_year': 2000.0},
    ),
    Witness(
        'annual_rainfall_changes_amplitude.alt',
        {'annual_rainfall': 2000.0, 'annual_temperature': 15.0},
        (15.0,),
        {'weather.parameter.seasonal_amplitude.cold_endpoint_c': 28.0,
         'weather.parameter.seasonal_amplitude.hot_endpoint_c': 2.0,
         'weather.parameter.seasonal_amplitude.input_max_c': 35.0,
         'weather.parameter.seasonal_amplitude.input_min_c': -5.0,
         'weather.parameter.seasonal_amplitude.output_max_c': 30.0,
         'weather.parameter.seasonal_amplitude.output_min_c': 1.0,
         'weather.parameter.seasonal_amplitude.rain_bonus_c': 4.0,
         'weather.parameter.seasonal_amplitude.rain_reference_mm_per_year': 2000.0},
    ),
)

WITNESSES_2 = (
    Witness(
        'seasonal_amplitude_changes_diurnal_amplitude.base',
        {'seasonal_amplitude': 10.0},
        (5.0,),
        {'world.parameter.diurnal_to_seasonal_ratio': 0.5},
    ),
    Witness(
        'seasonal_amplitude_changes_diurnal_amplitude.alt',
        {'seasonal_amplitude': 20.0},
        (10.0,),
        {'world.parameter.diurnal_to_seasonal_ratio': 0.5},
    ),
)

WITNESSES_3 = (
    Witness(
        'seasonal_amplitude_changes_humidity_amplitude.base',
        {'seasonal_amplitude': 10.0},
        (4.00000000372529,),
        {'world.parameter.humidity_seasonal_scale': 0.4},
    ),
    Witness(
        'seasonal_amplitude_changes_humidity_amplitude.alt',
        {'seasonal_amplitude': 20.0},
        (8.00000000745058,),
        {'world.parameter.humidity_seasonal_scale': 0.4},
    ),
)

WITNESSES_4 = (
    Witness(
        'seasonal_amplitude_changes_diurnal_humidity_amplitude.base',
        {'seasonal_amplitude': 10.0},
        (3.9999999990686774,),
        {'world.parameter.diurnal_to_seasonal_ratio': 0.5,
         'world.parameter.humidity_diurnal_scale': 0.8},
    ),
    Witness(
        'seasonal_amplitude_changes_diurnal_humidity_amplitude.alt',
        {'seasonal_amplitude': 20.0},
        (7.999999998137355,),
        {'world.parameter.diurnal_to_seasonal_ratio': 0.5,
         'world.parameter.humidity_diurnal_scale': 0.8},
    ),
)

WITNESSES_5 = (
    Witness(
        'annual_rainfall_changes_threshold.base',
        {'annual_rainfall': 50.0},
        (0.5499999998137355,),
        {'world.parameter.precip_annual_dry_mm': 50.0,
         'world.parameter.precip_annual_wet_mm': 3500.0,
         'world.parameter.precip_threshold_dry': 0.55,
         'world.parameter.precip_threshold_wet': 0.25},
    ),
    Witness(
        'annual_rainfall_changes_threshold.alt',
        {'annual_rainfall': 3500.0},
        (0.25,),
        {'world.parameter.precip_annual_dry_mm': 50.0,
         'world.parameter.precip_annual_wet_mm': 3500.0,
         'world.parameter.precip_threshold_dry': 0.55,
         'world.parameter.precip_threshold_wet': 0.25},
    ),
)

WITNESSES_6 = (
    Witness(
        'tick_changes_day.base',
        {'tick': 0},
        (1,),
        {'world.parameter.game_day_ticks': 172800},
    ),
    Witness(
        'tick_changes_day.alt',
        {'tick': 172800},
        (2,),
        {'world.parameter.game_day_ticks': 172800},
    ),
)

WITNESSES_7 = (
    Witness(
        'tick_changes_day_of_year.base',
        {'tick': 0},
        (0,),
        {'world.parameter.days_per_year': 360,
         'world.parameter.game_day_ticks': 172800},
    ),
    Witness(
        'tick_changes_day_of_year.alt',
        {'tick': 172800},
        (1,),
        {'world.parameter.days_per_year': 360,
         'world.parameter.game_day_ticks': 172800},
    ),
)

WITNESSES_8 = (
    Witness(
        'tick_changes_hour.base',
        {'tick': 0},
        (0.0,),
        {'world.parameter.game_day_ticks': 172800,
         'world.parameter.game_hour_ticks': 7200},
    ),
    Witness(
        'tick_changes_hour.alt',
        {'tick': 7200},
        (1.0,),
        {'world.parameter.game_day_ticks': 172800,
         'world.parameter.game_hour_ticks': 7200},
    ),
)

WITNESSES_9 = (
    Witness(
        'day_changes_season.base',
        {'day': 1},
        (0,),
        {'world.parameter.season_length_days': 90,
         'world.parameter.seasons_per_year': 4},
    ),
    Witness(
        'day_changes_season.alt',
        {'day': 91},
        (1,),
        {'world.parameter.season_length_days': 90,
         'world.parameter.seasons_per_year': 4},
    ),
)

WITNESSES_10 = (
    Witness(
        'day_changes_season_phase_cos.base',
        {'day': 46},
        (0.0,),
        {'world.parameter.season_length_days': 90,
         'world.parameter.seasons_per_year': 4},
    ),
    Witness(
        'day_changes_season_phase_cos.alt',
        {'day': 136},
        (1.0,),
        {'world.parameter.season_length_days': 90,
         'world.parameter.seasons_per_year': 4},
    ),
)

WITNESSES_11 = (
    Witness(
        'hour_changes_diurnal_phase_cos.base',
        {'hour': 14.0},
        (1.0,),
        {'world.parameter.diurnal_peak_hour': 14},
    ),
    Witness(
        'hour_changes_diurnal_phase_cos.alt',
        {'hour': 2.0},
        (-1.0,),
        {'world.parameter.diurnal_peak_hour': 14},
    ),
)

WITNESSES_12 = (
    Witness(
        'day_of_year_changes_declination.base',
        {'day_of_year': 45},
        (0.0,),
        {'world.parameter.days_per_year': 360,
         'world.parameter.obliquity_deg': 23.44},
    ),
    Witness(
        'day_of_year_changes_declination.alt',
        {'day_of_year': 135},
        (0.4091051733121276,),
        {'world.parameter.days_per_year': 360,
         'world.parameter.obliquity_deg': 23.44},
    ),
)

WITNESSES_13 = (
    Witness(
        'amplitude_changes_seasonal_temperature_offset.base',
        {'amplitude': 10.0, 'season_phase_cos': 1.0},
        (10.0,),
        {},
    ),
    Witness(
        'amplitude_changes_seasonal_temperature_offset.alt',
        {'amplitude': 20.0, 'season_phase_cos': 1.0},
        (20.0,),
        {},
    ),
    Witness(
        'season_phase_cos_changes_seasonal_temperature_offset.base',
        {'amplitude': 10.0, 'season_phase_cos': 0.5},
        (5.0,),
        {},
    ),
    Witness(
        'season_phase_cos_changes_seasonal_temperature_offset.alt',
        {'amplitude': 10.0, 'season_phase_cos': 1.0},
        (10.0,),
        {},
    ),
)

WITNESSES_14 = (
    Witness(
        'amplitude_changes_diurnal_temperature_offset.base',
        {'amplitude': 5.0, 'diurnal_phase_cos': 1.0},
        (5.0,),
        {},
    ),
    Witness(
        'amplitude_changes_diurnal_temperature_offset.alt',
        {'amplitude': 10.0, 'diurnal_phase_cos': 1.0},
        (10.0,),
        {},
    ),
    Witness(
        'diurnal_phase_cos_changes_diurnal_temperature_offset.base',
        {'amplitude': 5.0, 'diurnal_phase_cos': 0.5},
        (2.5,),
        {},
    ),
    Witness(
        'diurnal_phase_cos_changes_diurnal_temperature_offset.alt',
        {'amplitude': 5.0, 'diurnal_phase_cos': 1.0},
        (5.0,),
        {},
    ),
)

WITNESSES_15 = (
    Witness(
        'amplitude_changes_seasonal_humidity_offset.base',
        {'amplitude': 4.0, 'season_phase_cos': 1.0, 'sharpness': 0.0},
        (4.0,),
        {},
    ),
    Witness(
        'amplitude_changes_seasonal_humidity_offset.alt',
        {'amplitude': 8.0, 'season_phase_cos': 1.0, 'sharpness': 0.0},
        (8.0,),
        {},
    ),
    Witness(
        'season_phase_cos_changes_seasonal_humidity_offset.base',
        {'amplitude': 4.0, 'season_phase_cos': 0.5, 'sharpness': 0.0},
        (2.0,),
        {},
    ),
    Witness(
        'season_phase_cos_changes_seasonal_humidity_offset.alt',
        {'amplitude': 4.0, 'season_phase_cos': 1.0, 'sharpness': 0.0},
        (4.0,),
        {},
    ),
    Witness(
        'sharpness_changes_seasonal_humidity_offset.base',
        {'amplitude': 4.0, 'season_phase_cos': 0.5, 'sharpness': 0.0},
        (2.0,),
        {},
    ),
    Witness(
        'sharpness_changes_seasonal_humidity_offset.alt',
        {'amplitude': 4.0, 'season_phase_cos': 0.5, 'sharpness': 2.5},
        (3.3931345604360104,),
        {},
    ),
)

WITNESSES_16 = (
    Witness(
        'amplitude_changes_diurnal_humidity_offset.base',
        {'amplitude': 4.0, 'diurnal_phase_cos': 1.0},
        (-4.0,),
        {},
    ),
    Witness(
        'amplitude_changes_diurnal_humidity_offset.alt',
        {'amplitude': 8.0, 'diurnal_phase_cos': 1.0},
        (-8.0,),
        {},
    ),
    Witness(
        'diurnal_phase_cos_changes_diurnal_humidity_offset.base',
        {'amplitude': 4.0, 'diurnal_phase_cos': 0.5},
        (-2.0,),
        {},
    ),
    Witness(
        'diurnal_phase_cos_changes_diurnal_humidity_offset.alt',
        {'amplitude': 4.0, 'diurnal_phase_cos': 1.0},
        (-4.0,),
        {},
    ),
)

WITNESSES_17 = (
    Witness(
        'latitude_changes_sunrise.base',
        {'latitude': 0.0, 'solar_declination': 0.4091},
        (6.0,),
        {},
    ),
    Witness(
        'latitude_changes_sunrise.alt',
        {'latitude': 80.0, 'solar_declination': 0.4091},
        (0.0,),
        {},
    ),
    Witness(
        'solar_declination_changes_sunrise.base',
        {'latitude': 80.0, 'solar_declination': 0.0},
        (6.0,),
        {},
    ),
    Witness(
        'solar_declination_changes_sunrise.alt',
        {'latitude': 80.0, 'solar_declination': 0.4091},
        (0.0,),
        {},
    ),
)

WITNESSES_18 = (
    Witness(
        'latitude_changes_sunset.base',
        {'latitude': 0.0, 'solar_declination': 0.4091},
        (18.0,),
        {},
    ),
    Witness(
        'latitude_changes_sunset.alt',
        {'latitude': 80.0, 'solar_declination': 0.4091},
        (24.0,),
        {},
    ),
    Witness(
        'solar_declination_changes_sunset.base',
        {'latitude': 80.0, 'solar_declination': 0.0},
        (18.0,),
        {},
    ),
    Witness(
        'solar_declination_changes_sunset.alt',
        {'latitude': 80.0, 'solar_declination': 0.4091},
        (24.0,),
        {},
    ),
)

WITNESSES_19 = (
    Witness(
        'sunrise_changes_daylight.base',
        {'sunrise': 6.0, 'sunset': 18.0},
        (12.0,),
        {},
    ),
    Witness(
        'sunrise_changes_daylight.alt',
        {'sunrise': 8.0, 'sunset': 18.0},
        (10.0,),
        {},
    ),
    Witness(
        'sunset_changes_daylight.base',
        {'sunrise': 6.0, 'sunset': 18.0},
        (12.0,),
        {},
    ),
    Witness(
        'sunset_changes_daylight.alt',
        {'sunrise': 6.0, 'sunset': 20.0},
        (14.0,),
        {},
    ),
)

WITNESSES_20 = (
    Witness(
        'annual_temperature_changes_instant_temperature.base',
        {'annual_temperature': 10.0,
         'diurnal_offset': 0.0,
         'perturbation': 0.0,
         'seasonal_offset': 0.0},
        (10.0,),
        {'world.parameter.temp_bound_max_c': 50.0,
         'world.parameter.temp_bound_min_c': -30.0,
         'world.parameter.temp_perturb_scale_c': 5.0},
    ),
    Witness(
        'annual_temperature_changes_instant_temperature.alt',
        {'annual_temperature': 20.0,
         'diurnal_offset': 0.0,
         'perturbation': 0.0,
         'seasonal_offset': 0.0},
        (20.0,),
        {'world.parameter.temp_bound_max_c': 50.0,
         'world.parameter.temp_bound_min_c': -30.0,
         'world.parameter.temp_perturb_scale_c': 5.0},
    ),
    Witness(
        'seasonal_offset_changes_instant_temperature.base',
        {'annual_temperature': 10.0,
         'diurnal_offset': 0.0,
         'perturbation': 0.0,
         'seasonal_offset': 0.0},
        (10.0,),
        {'world.parameter.temp_bound_max_c': 50.0,
         'world.parameter.temp_bound_min_c': -30.0,
         'world.parameter.temp_perturb_scale_c': 5.0},
    ),
    Witness(
        'seasonal_offset_changes_instant_temperature.alt',
        {'annual_temperature': 10.0,
         'diurnal_offset': 0.0,
         'perturbation': 0.0,
         'seasonal_offset': 5.0},
        (15.0,),
        {'world.parameter.temp_bound_max_c': 50.0,
         'world.parameter.temp_bound_min_c': -30.0,
         'world.parameter.temp_perturb_scale_c': 5.0},
    ),
    Witness(
        'diurnal_offset_changes_instant_temperature.base',
        {'annual_temperature': 10.0,
         'diurnal_offset': 0.0,
         'perturbation': 0.0,
         'seasonal_offset': 0.0},
        (10.0,),
        {'world.parameter.temp_bound_max_c': 50.0,
         'world.parameter.temp_bound_min_c': -30.0,
         'world.parameter.temp_perturb_scale_c': 5.0},
    ),
    Witness(
        'diurnal_offset_changes_instant_temperature.alt',
        {'annual_temperature': 10.0,
         'diurnal_offset': 5.0,
         'perturbation': 0.0,
         'seasonal_offset': 0.0},
        (15.0,),
        {'world.parameter.temp_bound_max_c': 50.0,
         'world.parameter.temp_bound_min_c': -30.0,
         'world.parameter.temp_perturb_scale_c': 5.0},
    ),
    Witness(
        'perturbation_changes_instant_temperature.base',
        {'annual_temperature': 10.0,
         'diurnal_offset': 0.0,
         'perturbation': 0.0,
         'seasonal_offset': 0.0},
        (10.0,),
        {'world.parameter.temp_bound_max_c': 50.0,
         'world.parameter.temp_bound_min_c': -30.0,
         'world.parameter.temp_perturb_scale_c': 5.0},
    ),
    Witness(
        'perturbation_changes_instant_temperature.alt',
        {'annual_temperature': 10.0,
         'diurnal_offset': 0.0,
         'perturbation': 1.0,
         'seasonal_offset': 0.0},
        (15.0,),
        {'world.parameter.temp_bound_max_c': 50.0,
         'world.parameter.temp_bound_min_c': -30.0,
         'world.parameter.temp_perturb_scale_c': 5.0},
    ),
)

WITNESSES_21 = (
    Witness(
        'baseline_changes_instant_humidity.base',
        {'baseline': 60.0,
         'diurnal_offset': 0.0,
         'perturbation': 0.0,
         'seasonal_offset': 0.0},
        (60.0,),
        {'world.parameter.humidity_bound_max_pp': 100.0,
         'world.parameter.humidity_bound_min_pp': 0.0,
         'world.parameter.humidity_perturb_scale_pp': 15.0},
    ),
    Witness(
        'baseline_changes_instant_humidity.alt',
        {'baseline': 70.0,
         'diurnal_offset': 0.0,
         'perturbation': 0.0,
         'seasonal_offset': 0.0},
        (70.0,),
        {'world.parameter.humidity_bound_max_pp': 100.0,
         'world.parameter.humidity_bound_min_pp': 0.0,
         'world.parameter.humidity_perturb_scale_pp': 15.0},
    ),
    Witness(
        'seasonal_offset_changes_instant_humidity.base',
        {'baseline': 60.0,
         'diurnal_offset': 0.0,
         'perturbation': 0.0,
         'seasonal_offset': 0.0},
        (60.0,),
        {'world.parameter.humidity_bound_max_pp': 100.0,
         'world.parameter.humidity_bound_min_pp': 0.0,
         'world.parameter.humidity_perturb_scale_pp': 15.0},
    ),
    Witness(
        'seasonal_offset_changes_instant_humidity.alt',
        {'baseline': 60.0,
         'diurnal_offset': 0.0,
         'perturbation': 0.0,
         'seasonal_offset': 5.0},
        (65.0,),
        {'world.parameter.humidity_bound_max_pp': 100.0,
         'world.parameter.humidity_bound_min_pp': 0.0,
         'world.parameter.humidity_perturb_scale_pp': 15.0},
    ),
    Witness(
        'diurnal_offset_changes_instant_humidity.base',
        {'baseline': 60.0,
         'diurnal_offset': 0.0,
         'perturbation': 0.0,
         'seasonal_offset': 0.0},
        (60.0,),
        {'world.parameter.humidity_bound_max_pp': 100.0,
         'world.parameter.humidity_bound_min_pp': 0.0,
         'world.parameter.humidity_perturb_scale_pp': 15.0},
    ),
    Witness(
        'diurnal_offset_changes_instant_humidity.alt',
        {'baseline': 60.0,
         'diurnal_offset': 5.0,
         'perturbation': 0.0,
         'seasonal_offset': 0.0},
        (65.0,),
        {'world.parameter.humidity_bound_max_pp': 100.0,
         'world.parameter.humidity_bound_min_pp': 0.0,
         'world.parameter.humidity_perturb_scale_pp': 15.0},
    ),
    Witness(
        'perturbation_changes_instant_humidity.base',
        {'baseline': 60.0,
         'diurnal_offset': 0.0,
         'perturbation': 0.0,
         'seasonal_offset': 0.0},
        (60.0,),
        {'world.parameter.humidity_bound_max_pp': 100.0,
         'world.parameter.humidity_bound_min_pp': 0.0,
         'world.parameter.humidity_perturb_scale_pp': 15.0},
    ),
    Witness(
        'perturbation_changes_instant_humidity.alt',
        {'baseline': 60.0,
         'diurnal_offset': 0.0,
         'perturbation': 1.0,
         'seasonal_offset': 0.0},
        (75.0,),
        {'world.parameter.humidity_bound_max_pp': 100.0,
         'world.parameter.humidity_bound_min_pp': 0.0,
         'world.parameter.humidity_perturb_scale_pp': 15.0},
    ),
)

WITNESSES_22 = (
    Witness(
        'baseline_changes_instant_wind.base',
        {'baseline': 5.0, 'multiplier': 1.0, 'perturbation': 0.0},
        (5.0,),
        {'world.parameter.wind_bound_max_mps': 50.0,
         'world.parameter.wind_bound_min_mps': 0.0,
         'world.parameter.wind_perturb_scale_mps': 4.0},
    ),
    Witness(
        'baseline_changes_instant_wind.alt',
        {'baseline': 10.0, 'multiplier': 1.0, 'perturbation': 0.0},
        (10.0,),
        {'world.parameter.wind_bound_max_mps': 50.0,
         'world.parameter.wind_bound_min_mps': 0.0,
         'world.parameter.wind_perturb_scale_mps': 4.0},
    ),
    Witness(
        'perturbation_changes_instant_wind.base',
        {'baseline': 5.0, 'multiplier': 1.0, 'perturbation': 0.0},
        (5.0,),
        {'world.parameter.wind_bound_max_mps': 50.0,
         'world.parameter.wind_bound_min_mps': 0.0,
         'world.parameter.wind_perturb_scale_mps': 4.0},
    ),
    Witness(
        'perturbation_changes_instant_wind.alt',
        {'baseline': 5.0, 'multiplier': 1.0, 'perturbation': 1.0},
        (9.0,),
        {'world.parameter.wind_bound_max_mps': 50.0,
         'world.parameter.wind_bound_min_mps': 0.0,
         'world.parameter.wind_perturb_scale_mps': 4.0},
    ),
    Witness(
        'multiplier_changes_instant_wind.base',
        {'baseline': 5.0, 'multiplier': 1.0, 'perturbation': 0.0},
        (5.0,),
        {'world.parameter.wind_bound_max_mps': 50.0,
         'world.parameter.wind_bound_min_mps': 0.0,
         'world.parameter.wind_perturb_scale_mps': 4.0},
    ),
    Witness(
        'multiplier_changes_instant_wind.alt',
        {'baseline': 5.0, 'multiplier': 2.0, 'perturbation': 0.0},
        (10.0,),
        {'world.parameter.wind_bound_max_mps': 50.0,
         'world.parameter.wind_bound_min_mps': 0.0,
         'world.parameter.wind_perturb_scale_mps': 4.0},
    ),
)

WITNESSES_23 = (
    Witness(
        'signal_changes_precipitation_intensity.base',
        {'mean_intensity': 5.0, 'signal': 0.5, 'threshold': 0.4},
        (0.9999999962747097,),
        {'world.parameter.precip_intensity_scale': 2.0,
         'world.parameter.precip_signal_max': 1.2},
    ),
    Witness(
        'signal_changes_precipitation_intensity.alt',
        {'mean_intensity': 5.0, 'signal': 1.0, 'threshold': 0.4},
        (5.99999999627471,),
        {'world.parameter.precip_intensity_scale': 2.0,
         'world.parameter.precip_signal_max': 1.2},
    ),
    Witness(
        'threshold_changes_precipitation_intensity.base',
        {'mean_intensity': 5.0, 'signal': 0.5, 'threshold': 0.4},
        (0.9999999962747097,),
        {'world.parameter.precip_intensity_scale': 2.0,
         'world.parameter.precip_signal_max': 1.2},
    ),
    Witness(
        'threshold_changes_precipitation_intensity.alt',
        {'mean_intensity': 5.0, 'signal': 0.5, 'threshold': 0.45},
        (0.49999999813735485,),
        {'world.parameter.precip_intensity_scale': 2.0,
         'world.parameter.precip_signal_max': 1.2},
    ),
    Witness(
        'mean_intensity_changes_precipitation_intensity.base',
        {'mean_intensity': 5.0, 'signal': 0.5, 'threshold': 0.4},
        (0.9999999962747097,),
        {'world.parameter.precip_intensity_scale': 2.0,
         'world.parameter.precip_signal_max': 1.2},
    ),
    Witness(
        'mean_intensity_changes_precipitation_intensity.alt',
        {'mean_intensity': 10.0, 'signal': 0.5, 'threshold': 0.4},
        (1.9999999925494194,),
        {'world.parameter.precip_intensity_scale': 2.0,
         'world.parameter.precip_signal_max': 1.2},
    ),
)

WITNESSES_24 = (
    Witness(
        'daylight_changes_sunshine.base',
        {'daylight_hours': 12.0, 'perturbation': 0.0},
        (12.0,),
        {'world.parameter.sunshine_bound_max_h': 24.0,
         'world.parameter.sunshine_bound_min_h': 0.0,
         'world.parameter.sunshine_perturb_scale_h': 1.5},
    ),
    Witness(
        'daylight_changes_sunshine.alt',
        {'daylight_hours': 16.0, 'perturbation': 0.0},
        (16.0,),
        {'world.parameter.sunshine_bound_max_h': 24.0,
         'world.parameter.sunshine_bound_min_h': 0.0,
         'world.parameter.sunshine_perturb_scale_h': 1.5},
    ),
    Witness(
        'perturbation_changes_sunshine.base',
        {'daylight_hours': 12.0, 'perturbation': 0.0},
        (12.0,),
        {'world.parameter.sunshine_bound_max_h': 24.0,
         'world.parameter.sunshine_bound_min_h': 0.0,
         'world.parameter.sunshine_perturb_scale_h': 1.5},
    ),
    Witness(
        'perturbation_changes_sunshine.alt',
        {'daylight_hours': 12.0, 'perturbation': 1.0},
        (13.5,),
        {'world.parameter.sunshine_bound_max_h': 24.0,
         'world.parameter.sunshine_bound_min_h': 0.0,
         'world.parameter.sunshine_perturb_scale_h': 1.5},
    ),
)

WITNESSES_25 = (
    Witness(
        'temperature_changes_precipitation_type.base',
        {'instant_temperature': -1.0},
        ('snow',),
        {},
    ),
    Witness(
        'temperature_changes_precipitation_type.alt',
        {'instant_temperature': 1.0},
        ('rain',),
        {},
    ),
)

# ── 机制声明 ────────────────────────────────────────────
MECHANISM_0 = MechanismDecl(
    id='weather.chunk.derive_solar_latitude_proxy.v1',
    output='weather.chunk.solar_latitude_proxy_deg',
    parents=(
        Parent(
            slot='weather.chunk.sea_level_temperature_c',
            argument='sea_level_temperature',
            lag=0,
        ),
    ),
    impl=_impl_0,
    when=When(mode='phase', key='weather.chunk_derived_a'),
    equation=(
        "clamp(output_max - (sea_level_temperature - input_min) / (in"
        "put_max - input_min) * (output_max - output_min), output_min"
        ", output_max)"
    ),
    arithmetic=Arithmetic(domain='fixed', bits=30),
    params=(
        'weather.parameter.latitude.input_min_c',
        'weather.parameter.latitude.input_max_c',
        'weather.parameter.latitude.output_min_deg',
        'weather.parameter.latitude.output_max_deg',
    ),
    witnesses=WITNESSES_0,
)

MECHANISM_1 = MechanismDecl(
    id='weather.chunk.derive_seasonal_temperature_amplitude.v1',
    output='weather.chunk.seasonal_temperature_amplitude_c',
    parents=(
        Parent(
            slot='weather.chunk.annual_mean_temperature_c',
            argument='annual_temperature',
            lag=0,
        ),
        Parent(
            slot='weather.chunk.annual_rainfall_mm_per_year',
            argument='annual_rainfall',
            lag=0,
        ),
    ),
    impl=_impl_1,
    when=When(mode='phase', key='weather.chunk_derived_a'),
    equation=(
        "clamp(cold_endpoint - (annual_temperature - input_temp_min) "
        "/ (input_temp_max - input_temp_min) * (cold_endpoint - hot_e"
        "ndpoint) + clamp((rain_reference - annual_rainfall) / rain_r"
        "eference, -0.5, 1.0) * rain_bonus_scale, output_min, output_"
        "max)"
    ),
    arithmetic=Arithmetic(domain='fixed', bits=30),
    params=(
        'weather.parameter.seasonal_amplitude.input_min_c',
        'weather.parameter.seasonal_amplitude.input_max_c',
        'weather.parameter.seasonal_amplitude.cold_endpoint_c',
        'weather.parameter.seasonal_amplitude.hot_endpoint_c',
        'weather.parameter.seasonal_amplitude.rain_reference_mm_per_year',
        'weather.parameter.seasonal_amplitude.rain_bonus_c',
        'weather.parameter.seasonal_amplitude.output_min_c',
        'weather.parameter.seasonal_amplitude.output_max_c',
    ),
    witnesses=WITNESSES_1,
)

MECHANISM_2 = MechanismDecl(
    id='weather.chunk.derive_diurnal_temperature_amplitude.v1',
    output='weather.chunk.diurnal_temperature_amplitude_c',
    parents=(
        Parent(
            slot='weather.chunk.seasonal_temperature_amplitude_c',
            argument='seasonal_amplitude',
            lag=0,
        ),
    ),
    impl=_impl_2,
    when=When(mode='phase', key='weather.chunk_derived_b'),
    equation=(
        "seasonal_amplitude * diurnal_to_seasonal_ratio"
    ),
    arithmetic=Arithmetic(domain='fixed', bits=30),
    params=('world.parameter.diurnal_to_seasonal_ratio',),
    witnesses=WITNESSES_2,
)

MECHANISM_3 = MechanismDecl(
    id='weather.chunk.derive_seasonal_humidity_amplitude.v1',
    output='weather.chunk.seasonal_humidity_amplitude_pp',
    parents=(
        Parent(
            slot='weather.chunk.seasonal_temperature_amplitude_c',
            argument='seasonal_amplitude',
            lag=0,
        ),
    ),
    impl=_impl_3,
    when=When(mode='phase', key='weather.chunk_derived_b'),
    equation=(
        "seasonal_amplitude * humidity_seasonal_scale"
    ),
    arithmetic=Arithmetic(domain='fixed', bits=30),
    params=('world.parameter.humidity_seasonal_scale',),
    witnesses=WITNESSES_3,
)

MECHANISM_4 = MechanismDecl(
    id='weather.chunk.derive_diurnal_humidity_amplitude.v1',
    output='weather.chunk.diurnal_humidity_amplitude_pp',
    parents=(
        Parent(
            slot='weather.chunk.seasonal_temperature_amplitude_c',
            argument='seasonal_amplitude',
            lag=0,
        ),
    ),
    impl=_impl_4,
    when=When(mode='phase', key='weather.chunk_derived_b'),
    equation=(
        "seasonal_amplitude * diurnal_to_seasonal_ratio * humidity_di"
        "urnal_scale"
    ),
    arithmetic=Arithmetic(domain='fixed', bits=30),
    params=(
        'world.parameter.diurnal_to_seasonal_ratio',
        'world.parameter.humidity_diurnal_scale',
    ),
    witnesses=WITNESSES_4,
)

MECHANISM_5 = MechanismDecl(
    id='weather.chunk.derive_precipitation_threshold.v1',
    output='weather.chunk.precipitation_threshold',
    parents=(
        Parent(
            slot='weather.chunk.annual_rainfall_mm_per_year',
            argument='annual_rainfall',
            lag=0,
        ),
    ),
    impl=_impl_5,
    when=When(mode='phase', key='weather.chunk_derived_a'),
    equation=(
        "threshold_wet + (threshold_dry - threshold_wet) * (1 - clamp"
        "((annual_rainfall - annual_dry) / (annual_wet - annual_dry),"
        " 0, 1))"
    ),
    arithmetic=Arithmetic(domain='fixed', bits=30),
    params=(
        'world.parameter.precip_annual_dry_mm',
        'world.parameter.precip_annual_wet_mm',
        'world.parameter.precip_threshold_dry',
        'world.parameter.precip_threshold_wet',
    ),
    witnesses=WITNESSES_5,
)

MECHANISM_6 = MechanismDecl(
    id='weather.tick.derive_day.v1',
    output='weather.tick.day',
    parents=(
        Parent(
            slot='world.clock.tick',
            argument='tick',
            lag=0,
        ),
    ),
    impl=_impl_6,
    when=When(mode='phase', key='weather.instant_tick_input'),
    equation=(
        "tick // game_day + 1"
    ),
    arithmetic=Arithmetic(domain='fixed', bits=30),
    params=('world.parameter.game_day_ticks',),
    witnesses=WITNESSES_6,
)

MECHANISM_7 = MechanismDecl(
    id='weather.tick.derive_day_of_year.v1',
    output='weather.tick.day_of_year',
    parents=(
        Parent(
            slot='world.clock.tick',
            argument='tick',
            lag=0,
        ),
    ),
    impl=_impl_7,
    when=When(mode='phase', key='weather.instant_tick_input'),
    equation=(
        "(tick // game_day) % days_per_year"
    ),
    arithmetic=Arithmetic(domain='fixed', bits=30),
    params=('world.parameter.game_day_ticks', 'world.parameter.days_per_year'),
    witnesses=WITNESSES_7,
)

MECHANISM_8 = MechanismDecl(
    id='weather.tick.derive_hour_of_day.v1',
    output='weather.tick.hour_of_day',
    parents=(
        Parent(
            slot='world.clock.tick',
            argument='tick',
            lag=0,
        ),
    ),
    impl=_impl_8,
    when=When(mode='phase', key='weather.instant_tick_input'),
    equation=(
        "(tick % game_day) / game_hour"
    ),
    arithmetic=Arithmetic(domain='fixed', bits=30),
    params=('world.parameter.game_day_ticks', 'world.parameter.game_hour_ticks'),
    witnesses=WITNESSES_8,
)

MECHANISM_9 = MechanismDecl(
    id='weather.tick.derive_season.v1',
    output='weather.tick.season',
    parents=(
        Parent(
            slot='weather.tick.day',
            argument='day',
            lag=0,
        ),
    ),
    impl=_impl_9,
    when=When(mode='phase', key='weather.instant_tick_derived'),
    equation=(
        "(day - 1) // season_length_days % seasons_per_year"
    ),
    arithmetic=Arithmetic(domain='fixed', bits=30),
    params=('world.parameter.season_length_days', 'world.parameter.seasons_per_year'),
    witnesses=WITNESSES_9,
)

MECHANISM_10 = MechanismDecl(
    id='weather.tick.derive_season_phase_cos.v1',
    output='weather.tick.season_phase_cos',
    parents=(
        Parent(
            slot='weather.tick.day',
            argument='day',
            lag=0,
        ),
    ),
    impl=_impl_10,
    when=When(mode='phase', key='weather.instant_tick_derived'),
    equation=(
        "cos(((((day - 1) // season_length_days % seasons_per_year) +"
        " ((day - 1) % season_length_days) / season_length_days) - 1."
        "5) / seasons_per_year * 2 * pi)"
    ),
    arithmetic=Arithmetic(domain='fixed', bits=30),
    params=('world.parameter.season_length_days', 'world.parameter.seasons_per_year'),
    witnesses=WITNESSES_10,
)

MECHANISM_11 = MechanismDecl(
    id='weather.tick.derive_diurnal_phase_cos.v1',
    output='weather.tick.diurnal_phase_cos',
    parents=(
        Parent(
            slot='weather.tick.hour_of_day',
            argument='hour',
            lag=0,
        ),
    ),
    impl=_impl_11,
    when=When(mode='phase', key='weather.instant_tick_derived'),
    equation=(
        "cos((hour - peak_hour) / 24 * 2 * pi)"
    ),
    arithmetic=Arithmetic(domain='fixed', bits=30),
    params=('world.parameter.diurnal_peak_hour',),
    witnesses=WITNESSES_11,
)

MECHANISM_12 = MechanismDecl(
    id='weather.tick.derive_solar_declination.v1',
    output='weather.tick.solar_declination_rad',
    parents=(
        Parent(
            slot='weather.tick.day_of_year',
            argument='day_of_year',
            lag=0,
        ),
    ),
    impl=_impl_12,
    when=When(mode='phase', key='weather.instant_tick_derived'),
    equation=(
        "radians(obliquity_deg * sin(2 * pi * (day_of_year - days_per"
        "_year / 8) / days_per_year))"
    ),
    arithmetic=Arithmetic(domain='fixed', bits=30),
    params=('world.parameter.obliquity_deg', 'world.parameter.days_per_year'),
    witnesses=WITNESSES_12,
)

MECHANISM_13 = MechanismDecl(
    id='weather.offset.derive_seasonal_temperature.v1',
    output='weather.offset.seasonal_temperature_c',
    parents=(
        Parent(
            slot='weather.chunk.seasonal_temperature_amplitude_c',
            argument='amplitude',
            lag=0,
        ),
        Parent(
            slot='weather.tick.season_phase_cos',
            argument='season_phase_cos',
            lag=0,
        ),
    ),
    impl=_impl_13,
    when=When(mode='phase', key='weather.instant_offset'),
    equation=(
        "amplitude * season_phase_cos"
    ),
    arithmetic=Arithmetic(domain='fixed', bits=30),
    params=(),
    witnesses=WITNESSES_13,
)

MECHANISM_14 = MechanismDecl(
    id='weather.offset.derive_diurnal_temperature.v1',
    output='weather.offset.diurnal_temperature_c',
    parents=(
        Parent(
            slot='weather.chunk.diurnal_temperature_amplitude_c',
            argument='amplitude',
            lag=0,
        ),
        Parent(
            slot='weather.tick.diurnal_phase_cos',
            argument='diurnal_phase_cos',
            lag=0,
        ),
    ),
    impl=_impl_14,
    when=When(mode='phase', key='weather.instant_offset'),
    equation=(
        "amplitude * diurnal_phase_cos"
    ),
    arithmetic=Arithmetic(domain='fixed', bits=30),
    params=(),
    witnesses=WITNESSES_14,
)

MECHANISM_15 = MechanismDecl(
    id='weather.offset.derive_seasonal_humidity.v1',
    output='weather.offset.seasonal_humidity_pp',
    parents=(
        Parent(
            slot='weather.chunk.seasonal_humidity_amplitude_pp',
            argument='amplitude',
            lag=0,
        ),
        Parent(
            slot='weather.tick.season_phase_cos',
            argument='season_phase_cos',
            lag=0,
        ),
        Parent(
            slot='weather.chunk.humidity_sharpness',
            argument='sharpness',
            lag=0,
        ),
    ),
    impl=_impl_15,
    when=When(mode='phase', key='weather.instant_offset'),
    equation=(
        "amplitude * (tanh(season_phase_cos * sharpness) if sharpness"
        " > 0 else season_phase_cos)"
    ),
    arithmetic=Arithmetic(domain='fixed', bits=30),
    params=(),
    witnesses=WITNESSES_15,
)

MECHANISM_16 = MechanismDecl(
    id='weather.offset.derive_diurnal_humidity.v1',
    output='weather.offset.diurnal_humidity_pp',
    parents=(
        Parent(
            slot='weather.chunk.diurnal_humidity_amplitude_pp',
            argument='amplitude',
            lag=0,
        ),
        Parent(
            slot='weather.tick.diurnal_phase_cos',
            argument='diurnal_phase_cos',
            lag=0,
        ),
    ),
    impl=_impl_16,
    when=When(mode='phase', key='weather.instant_offset'),
    equation=(
        "amplitude * (-diurnal_phase_cos)"
    ),
    arithmetic=Arithmetic(domain='fixed', bits=30),
    params=(),
    witnesses=WITNESSES_16,
)

MECHANISM_17 = MechanismDecl(
    id='weather.astronomy.derive_sunrise.v1',
    output='weather.astronomy.sunrise_hour',
    parents=(
        Parent(
            slot='weather.chunk.solar_latitude_proxy_deg',
            argument='latitude',
            lag=0,
        ),
        Parent(
            slot='weather.tick.solar_declination_rad',
            argument='solar_declination',
            lag=0,
        ),
    ),
    impl=_impl_17,
    when=When(mode='phase', key='weather.instant_offset'),
    equation=(
        "12 - degrees(acos(-clamp(tan(radians(latitude)) * tan(solar_"
        "declination), -1, 1))) / 15"
    ),
    arithmetic=Arithmetic(domain='fixed', bits=30),
    params=(),
    witnesses=WITNESSES_17,
)

MECHANISM_18 = MechanismDecl(
    id='weather.astronomy.derive_sunset.v1',
    output='weather.astronomy.sunset_hour',
    parents=(
        Parent(
            slot='weather.chunk.solar_latitude_proxy_deg',
            argument='latitude',
            lag=0,
        ),
        Parent(
            slot='weather.tick.solar_declination_rad',
            argument='solar_declination',
            lag=0,
        ),
    ),
    impl=_impl_18,
    when=When(mode='phase', key='weather.instant_offset'),
    equation=(
        "12 + degrees(acos(-clamp(tan(radians(latitude)) * tan(solar_"
        "declination), -1, 1))) / 15"
    ),
    arithmetic=Arithmetic(domain='fixed', bits=30),
    params=(),
    witnesses=WITNESSES_18,
)

MECHANISM_19 = MechanismDecl(
    id='weather.astronomy.derive_daylight.v1',
    output='weather.astronomy.daylight_hours',
    parents=(
        Parent(
            slot='weather.astronomy.sunrise_hour',
            argument='sunrise',
            lag=0,
        ),
        Parent(
            slot='weather.astronomy.sunset_hour',
            argument='sunset',
            lag=0,
        ),
    ),
    impl=_impl_19,
    when=When(mode='phase', key='weather.instant_composite'),
    equation=(
        "sunset - sunrise"
    ),
    arithmetic=Arithmetic(domain='fixed', bits=30),
    params=(),
    witnesses=WITNESSES_19,
)

MECHANISM_20 = MechanismDecl(
    id='weather.instant.compose_temperature.v1',
    output='weather.instant.temperature_c',
    parents=(
        Parent(
            slot='weather.chunk.annual_mean_temperature_c',
            argument='annual_temperature',
            lag=0,
        ),
        Parent(
            slot='weather.offset.seasonal_temperature_c',
            argument='seasonal_offset',
            lag=0,
        ),
        Parent(
            slot='weather.offset.diurnal_temperature_c',
            argument='diurnal_offset',
            lag=0,
        ),
        Parent(
            slot='weather.field.temperature_perturbation',
            argument='perturbation',
            lag=0,
        ),
    ),
    impl=_impl_20,
    when=When(mode='phase', key='weather.instant_composite'),
    equation=(
        "clamp(annual_temperature + seasonal_offset + diurnal_offset "
        "+ perturbation * perturb_scale, lower_bound, upper_bound)"
    ),
    arithmetic=Arithmetic(domain='fixed', bits=30),
    params=(
        'world.parameter.temp_perturb_scale_c',
        'world.parameter.temp_bound_min_c',
        'world.parameter.temp_bound_max_c',
    ),
    witnesses=WITNESSES_20,
)

MECHANISM_21 = MechanismDecl(
    id='weather.instant.compose_humidity.v1',
    output='weather.instant.relative_humidity_percent',
    parents=(
        Parent(
            slot='weather.chunk.baseline_humidity_percent',
            argument='baseline',
            lag=0,
        ),
        Parent(
            slot='weather.offset.seasonal_humidity_pp',
            argument='seasonal_offset',
            lag=0,
        ),
        Parent(
            slot='weather.offset.diurnal_humidity_pp',
            argument='diurnal_offset',
            lag=0,
        ),
        Parent(
            slot='weather.field.humidity_perturbation',
            argument='perturbation',
            lag=0,
        ),
    ),
    impl=_impl_21,
    when=When(mode='phase', key='weather.instant_composite'),
    equation=(
        "clamp(baseline + seasonal_offset + diurnal_offset + perturba"
        "tion * perturb_scale, lower_bound, upper_bound)"
    ),
    arithmetic=Arithmetic(domain='fixed', bits=30),
    params=(
        'world.parameter.humidity_perturb_scale_pp',
        'world.parameter.humidity_bound_min_pp',
        'world.parameter.humidity_bound_max_pp',
    ),
    witnesses=WITNESSES_21,
)

MECHANISM_22 = MechanismDecl(
    id='weather.instant.compose_wind_speed.v1',
    output='weather.instant.wind_speed_mps',
    parents=(
        Parent(
            slot='weather.chunk.baseline_wind_speed_mps',
            argument='baseline',
            lag=0,
        ),
        Parent(
            slot='weather.field.wind_perturbation',
            argument='perturbation',
            lag=0,
        ),
        Parent(
            slot='weather.field.wind_multiplier',
            argument='multiplier',
            lag=0,
        ),
    ),
    impl=_impl_22,
    when=When(mode='phase', key='weather.instant_composite'),
    equation=(
        "clamp(clamp(baseline + perturbation * perturb_scale, lower_b"
        "ound, upper_bound) * multiplier, lower_bound, upper_bound)"
    ),
    arithmetic=Arithmetic(domain='fixed', bits=30),
    params=(
        'world.parameter.wind_perturb_scale_mps',
        'world.parameter.wind_bound_min_mps',
        'world.parameter.wind_bound_max_mps',
    ),
    witnesses=WITNESSES_22,
)

MECHANISM_23 = MechanismDecl(
    id='weather.instant.compose_precipitation_intensity.v1',
    output='weather.instant.precipitation_intensity_mm_per_hour',
    parents=(
        Parent(
            slot='weather.field.precipitation_signal',
            argument='signal',
            lag=0,
        ),
        Parent(
            slot='weather.chunk.precipitation_threshold',
            argument='threshold',
            lag=0,
        ),
        Parent(
            slot='weather.chunk.mean_precip_intensity_mm_per_hour',
            argument='mean_intensity',
            lag=0,
        ),
    ),
    impl=_impl_23,
    when=When(mode='phase', key='weather.instant_composite'),
    equation=(
        "0 if signal <= threshold else (min(signal, signal_max) - thr"
        "eshold) * intensity_scale * mean_intensity"
    ),
    arithmetic=Arithmetic(domain='fixed', bits=30),
    params=(
        'world.parameter.precip_signal_max',
        'world.parameter.precip_intensity_scale',
    ),
    witnesses=WITNESSES_23,
)

MECHANISM_24 = MechanismDecl(
    id='weather.instant.compose_sunshine.v1',
    output='weather.instant.sunshine_hours_per_day',
    parents=(
        Parent(
            slot='weather.astronomy.daylight_hours',
            argument='daylight_hours',
            lag=0,
        ),
        Parent(
            slot='weather.field.humidity_perturbation',
            argument='perturbation',
            lag=0,
        ),
    ),
    impl=_impl_24,
    when=When(mode='phase', key='weather.instant_readout'),
    equation=(
        "clamp(daylight_hours + perturbation * perturb_scale, lower_b"
        "ound, upper_bound)"
    ),
    arithmetic=Arithmetic(domain='fixed', bits=30),
    params=(
        'world.parameter.sunshine_perturb_scale_h',
        'world.parameter.sunshine_bound_min_h',
        'world.parameter.sunshine_bound_max_h',
    ),
    witnesses=WITNESSES_24,
)

MECHANISM_25 = MechanismDecl(
    id='weather.instant.classify_precipitation_type.v1',
    output='weather.instant.precipitation_type',
    parents=(
        Parent(
            slot='weather.instant.temperature_c',
            argument='instant_temperature',
            lag=0,
        ),
    ),
    impl=_impl_25,
    when=When(mode='phase', key='weather.instant_readout'),
    equation=(
        "'snow' if round_half_even(instant_temperature, 1) <= 0 else 'rain'"
    ),
    arithmetic=Arithmetic(domain='fixed', bits=30),
    params=(),
    witnesses=WITNESSES_25,
)

# ── 模块包 ──────────────────────────────────────────────
MODULE = ModulePack(
    id='weather',
    version='1',
    instances=(GLOBAL, CHUNK),
    parameters=(
        ParameterDecl(
            id='weather.parameter.latitude.input_min_c',
            default=-5.0,
            minimum=-273.15,
            maximum=100.0,
            unit='degC',
        ),
        ParameterDecl(
            id='weather.parameter.latitude.input_max_c',
            default=35.0,
            minimum=-273.15,
            maximum=100.0,
            unit='degC',
        ),
        ParameterDecl(
            id='weather.parameter.latitude.output_min_deg',
            default=0.0,
            minimum=0.0,
            maximum=90.0,
            unit='degree_north',
        ),
        ParameterDecl(
            id='weather.parameter.latitude.output_max_deg',
            default=80.0,
            minimum=0.0,
            maximum=90.0,
            unit='degree_north',
        ),
        ParameterDecl(
            id='weather.parameter.seasonal_amplitude.input_min_c',
            default=-5.0,
            minimum=-273.15,
            maximum=100.0,
            unit='degC',
        ),
        ParameterDecl(
            id='weather.parameter.seasonal_amplitude.input_max_c',
            default=35.0,
            minimum=-273.15,
            maximum=100.0,
            unit='degC',
        ),
        ParameterDecl(
            id='weather.parameter.seasonal_amplitude.cold_endpoint_c',
            default=28.0,
            minimum=0.0,
            maximum=100.0,
            unit='degC',
        ),
        ParameterDecl(
            id='weather.parameter.seasonal_amplitude.hot_endpoint_c',
            default=2.0,
            minimum=0.0,
            maximum=100.0,
            unit='degC',
        ),
        ParameterDecl(
            id='weather.parameter.seasonal_amplitude.rain_reference_mm_per_year',
            default=2000.0,
            minimum=1.0,
            maximum=100000.0,
            unit='mm_per_year',
        ),
        ParameterDecl(
            id='weather.parameter.seasonal_amplitude.rain_bonus_c',
            default=4.0,
            minimum=0.0,
            maximum=100.0,
            unit='degC',
        ),
        ParameterDecl(
            id='weather.parameter.seasonal_amplitude.output_min_c',
            default=1.0,
            minimum=0.0,
            maximum=100.0,
            unit='degC',
        ),
        ParameterDecl(
            id='weather.parameter.seasonal_amplitude.output_max_c',
            default=30.0,
            minimum=0.0,
            maximum=100.0,
            unit='degC',
        ),
        ParameterDecl(
            id='world.parameter.diurnal_to_seasonal_ratio',
            default=0.5,
            minimum=0.0,
            maximum=10.0,
            unit='dimensionless',
        ),
        ParameterDecl(
            id='world.parameter.humidity_seasonal_scale',
            default=0.4,
            minimum=0.0,
            maximum=10.0,
            unit='dimensionless',
        ),
        ParameterDecl(
            id='world.parameter.humidity_diurnal_scale',
            default=0.8,
            minimum=0.0,
            maximum=10.0,
            unit='dimensionless',
        ),
        ParameterDecl(
            id='world.parameter.precip_annual_dry_mm',
            default=50.0,
            minimum=0.0,
            maximum=1000000.0,
            unit='mm_per_year',
        ),
        ParameterDecl(
            id='world.parameter.precip_annual_wet_mm',
            default=3500.0,
            minimum=0.0,
            maximum=1000000.0,
            unit='mm_per_year',
        ),
        ParameterDecl(
            id='world.parameter.precip_threshold_dry',
            default=0.55,
            minimum=0.0,
            maximum=1.0,
            unit='dimensionless',
        ),
        ParameterDecl(
            id='world.parameter.precip_threshold_wet',
            default=0.25,
            minimum=0.0,
            maximum=1.0,
            unit='dimensionless',
        ),
        ParameterDecl(
            id='world.parameter.game_day_ticks',
            default=172800,
            minimum=0.0,
            maximum=1000000000000.0,
            unit='tick',
        ),
        ParameterDecl(
            id='world.parameter.days_per_year',
            default=360,
            minimum=0.0,
            maximum=1000000000000.0,
            unit='game_day',
        ),
        ParameterDecl(
            id='world.parameter.game_hour_ticks',
            default=7200,
            minimum=0.0,
            maximum=1000000000000.0,
            unit='tick',
        ),
        ParameterDecl(
            id='world.parameter.season_length_days',
            default=90,
            minimum=0.0,
            maximum=1000000000000.0,
            unit='game_day',
        ),
        ParameterDecl(
            id='world.parameter.seasons_per_year',
            default=4,
            minimum=0.0,
            maximum=1000000000000.0,
            unit='dimensionless',
        ),
        ParameterDecl(
            id='world.parameter.diurnal_peak_hour',
            default=14,
            minimum=0.0,
            maximum=1000000000000.0,
            unit='hour',
        ),
        ParameterDecl(
            id='world.parameter.obliquity_deg',
            default=23.44,
            minimum=0.0,
            maximum=90.0,
            unit='degree',
        ),
        ParameterDecl(
            id='world.parameter.temp_perturb_scale_c',
            default=5.0,
            minimum=0.0,
            maximum=100.0,
            unit='degC',
        ),
        ParameterDecl(
            id='world.parameter.temp_bound_min_c',
            default=-30.0,
            minimum=-1000000000000.0,
            maximum=1000000000000.0,
            unit='degC',
        ),
        ParameterDecl(
            id='world.parameter.temp_bound_max_c',
            default=50.0,
            minimum=-1000000000000.0,
            maximum=1000000000000.0,
            unit='degC',
        ),
        ParameterDecl(
            id='world.parameter.humidity_perturb_scale_pp',
            default=15.0,
            minimum=0.0,
            maximum=100.0,
            unit='pp',
        ),
        ParameterDecl(
            id='world.parameter.humidity_bound_min_pp',
            default=0.0,
            minimum=-1000000000000.0,
            maximum=1000000000000.0,
            unit='pp',
        ),
        ParameterDecl(
            id='world.parameter.humidity_bound_max_pp',
            default=100.0,
            minimum=-1000000000000.0,
            maximum=1000000000000.0,
            unit='pp',
        ),
        ParameterDecl(
            id='world.parameter.wind_perturb_scale_mps',
            default=4.0,
            minimum=0.0,
            maximum=100.0,
            unit='mps',
        ),
        ParameterDecl(
            id='world.parameter.wind_bound_min_mps',
            default=0.0,
            minimum=-1000000000000.0,
            maximum=1000000000000.0,
            unit='mps',
        ),
        ParameterDecl(
            id='world.parameter.wind_bound_max_mps',
            default=50.0,
            minimum=-1000000000000.0,
            maximum=1000000000000.0,
            unit='mps',
        ),
        ParameterDecl(
            id='world.parameter.precip_signal_max',
            default=1.2,
            minimum=0.0,
            maximum=10.0,
            unit='dimensionless',
        ),
        ParameterDecl(
            id='world.parameter.precip_intensity_scale',
            default=2.0,
            minimum=0.0,
            maximum=100.0,
            unit='dimensionless',
        ),
        ParameterDecl(
            id='world.parameter.sunshine_perturb_scale_h',
            default=1.5,
            minimum=0.0,
            maximum=100.0,
            unit='hour',
        ),
        ParameterDecl(
            id='world.parameter.sunshine_bound_min_h',
            default=0.0,
            minimum=-1000000000000.0,
            maximum=1000000000000.0,
            unit='hour',
        ),
        ParameterDecl(
            id='world.parameter.sunshine_bound_max_h',
            default=24.0,
            minimum=-1000000000000.0,
            maximum=1000000000000.0,
            unit='hour',
        ),
    ),
    slots=(
        SLOT_WEATHER_ASTRONOMY_DAYLIGHT_HOURS,
        SLOT_WEATHER_ASTRONOMY_SUNRISE_HOUR,
        SLOT_WEATHER_ASTRONOMY_SUNSET_HOUR,
        SLOT_WEATHER_CHUNK_DIURNAL_HUMIDITY_AMPLITUDE_PP,
        SLOT_WEATHER_CHUNK_DIURNAL_TEMPERATURE_AMPLITUDE_C,
        SLOT_WEATHER_CHUNK_PRECIPITATION_THRESHOLD,
        SLOT_WEATHER_CHUNK_SEASONAL_HUMIDITY_AMPLITUDE_PP,
        SLOT_WEATHER_CHUNK_SEASONAL_TEMPERATURE_AMPLITUDE_C,
        SLOT_WEATHER_CHUNK_SOLAR_LATITUDE_PROXY_DEG,
        SLOT_WEATHER_INSTANT_PRECIPITATION_INTENSITY_MM_PER_HOUR,
        SLOT_WEATHER_INSTANT_PRECIPITATION_TYPE,
        SLOT_WEATHER_INSTANT_RELATIVE_HUMIDITY_PERCENT,
        SLOT_WEATHER_INSTANT_SUNSHINE_HOURS_PER_DAY,
        SLOT_WEATHER_INSTANT_TEMPERATURE_C,
        SLOT_WEATHER_INSTANT_WIND_SPEED_MPS,
        SLOT_WEATHER_OFFSET_DIURNAL_HUMIDITY_PP,
        SLOT_WEATHER_OFFSET_DIURNAL_TEMPERATURE_C,
        SLOT_WEATHER_OFFSET_SEASONAL_HUMIDITY_PP,
        SLOT_WEATHER_OFFSET_SEASONAL_TEMPERATURE_C,
        SLOT_WEATHER_TICK_DAY,
        SLOT_WEATHER_TICK_DAY_OF_YEAR,
        SLOT_WEATHER_TICK_DIURNAL_PHASE_COS,
        SLOT_WEATHER_TICK_HOUR_OF_DAY,
        SLOT_WEATHER_TICK_SEASON,
        SLOT_WEATHER_TICK_SEASON_PHASE_COS,
        SLOT_WEATHER_TICK_SOLAR_DECLINATION_RAD,
        SLOT_WEATHER_FIELD_HUMIDITY_PERTURBATION,
        SLOT_WEATHER_FIELD_PRECIPITATION_SIGNAL,
        SLOT_WEATHER_FIELD_TEMPERATURE_PERTURBATION,
        SLOT_WEATHER_FIELD_WIND_MULTIPLIER,
        SLOT_WEATHER_FIELD_WIND_PERTURBATION,
        SLOT_WORLD_CLOCK_TICK,
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
        MECHANISM_9,
        MECHANISM_10,
        MECHANISM_11,
        MECHANISM_12,
        MECHANISM_13,
        MECHANISM_14,
        MECHANISM_15,
        MECHANISM_16,
        MECHANISM_17,
        MECHANISM_18,
        MECHANISM_19,
        MECHANISM_20,
        MECHANISM_21,
        MECHANISM_22,
        MECHANISM_23,
        MECHANISM_24,
        MECHANISM_25,
    ),
    evidence=('P1 黄金向量对拍（tests/world/test_weather_port.py）',),
    notes='由旧机制注册表一次性移植（P1）；P2 后为唯一事实源。',
)
