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
SLOT_WEATHER_CHUNK_ANNUAL_MEAN_TEMPERATURE_C = SlotDecl(
    id='weather.chunk.annual_mean_temperature_c',
    on='lattice.chunk',
    persist='derived',
    domain=ValueDomain(kind="float", minimum=None, maximum=None, unit='degC'),
    writer='world.gen.derive_annual_mean_temperature.v1',
    recompute='机制 world.gen.derive_annual_mean_temperature.v1',
)
SLOT_WEATHER_CHUNK_ANNUAL_RAINFALL_MM_PER_YEAR = SlotDecl(
    id='weather.chunk.annual_rainfall_mm_per_year',
    on='lattice.chunk',
    persist='derived',
    domain=ValueDomain(kind="float", minimum=None, maximum=None, unit='mm_per_year'),
    writer='world.gen.derive_annual_rainfall.v1',
    recompute='机制 world.gen.derive_annual_rainfall.v1',
)
SLOT_WEATHER_CHUNK_BASELINE_HUMIDITY_PERCENT = SlotDecl(
    id='weather.chunk.baseline_humidity_percent',
    on='lattice.chunk',
    persist='derived',
    domain=ValueDomain(kind="float", minimum=0.0, maximum=100.0, unit='percent'),
    writer='world.gen.derive_baseline_humidity.v1',
    recompute='机制 world.gen.derive_baseline_humidity.v1',
)
SLOT_WEATHER_CHUNK_BASELINE_WIND_SPEED_MPS = SlotDecl(
    id='weather.chunk.baseline_wind_speed_mps',
    on='lattice.chunk',
    persist='derived',
    domain=ValueDomain(kind="float", minimum=0.0, maximum=50.0, unit='mps'),
    writer='world.gen.derive_baseline_wind_speed.v1',
    recompute='机制 world.gen.derive_baseline_wind_speed.v1',
)
SLOT_WEATHER_CHUNK_HUMIDITY_SHARPNESS = SlotDecl(
    id='weather.chunk.humidity_sharpness',
    on='lattice.chunk',
    persist='derived',
    domain=ValueDomain(kind="float", minimum=0.0, maximum=10.0, unit='dimensionless'),
    writer='world.gen.derive_humidity_sharpness.v1',
    recompute='机制 world.gen.derive_humidity_sharpness.v1',
)
SLOT_WEATHER_CHUNK_MEAN_PRECIP_INTENSITY_MM_PER_HOUR = SlotDecl(
    id='weather.chunk.mean_precip_intensity_mm_per_hour',
    on='lattice.chunk',
    persist='derived',
    domain=ValueDomain(kind="float", minimum=2.0, maximum=10.0, unit='mm_per_hour'),
    writer='world.gen.derive_mean_precip_intensity.v1',
    recompute='机制 world.gen.derive_mean_precip_intensity.v1',
)
SLOT_WEATHER_CHUNK_SEA_LEVEL_TEMPERATURE_C = SlotDecl(
    id='weather.chunk.sea_level_temperature_c',
    on='lattice.chunk',
    persist='derived',
    domain=ValueDomain(kind="float", minimum=-20.0, maximum=38.0, unit='degC'),
    writer='world.gen.derive_sea_level_temperature.v1',
    recompute='机制 world.gen.derive_sea_level_temperature.v1',
)
SLOT_WORLD_GEN_BIOME = SlotDecl(
    id='world.gen.biome',
    on='lattice.chunk',
    persist='derived',
    domain=ValueDomain(kind="enum", choices=(0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17, 18)),
    writer='world.gen.classify_biome.v1',
    recompute='机制 world.gen.classify_biome.v1',
)
SLOT_WORLD_GEN_CLIMATE_ZONE = SlotDecl(
    id='world.gen.climate_zone',
    on='lattice.chunk',
    persist='derived',
    domain=ValueDomain(kind="enum", choices=(0, 1, 2, 3, 4, 5, 6, 7)),
    writer='world.gen.classify_climate_zone.v1',
    recompute='机制 world.gen.classify_climate_zone.v1',
)
SLOT_WORLD_GEN_ALTITUDE_M = SlotDecl(
    id='world.gen.altitude_m',
    on='lattice.chunk',
    persist='external',
    domain=ValueDomain(kind="float", minimum=None, maximum=None, unit='meter'),
    permissions=Permissions(observe=True),
)
SLOT_WORLD_GEN_HUMIDITY_NOISE = SlotDecl(
    id='world.gen.humidity_noise',
    on='lattice.chunk',
    persist='external',
    domain=ValueDomain(kind="float", minimum=-1.0, maximum=1.0, unit='dimensionless'),
    permissions=Permissions(observe=True),
)
SLOT_WORLD_GEN_LATITUDE_NOISE = SlotDecl(
    id='world.gen.latitude_noise',
    on='lattice.chunk',
    persist='external',
    domain=ValueDomain(kind="float", minimum=-1.0, maximum=1.0, unit='dimensionless'),
    permissions=Permissions(observe=True),
)
SLOT_WORLD_GEN_MOISTURE_NOISE = SlotDecl(
    id='world.gen.moisture_noise',
    on='lattice.chunk',
    persist='external',
    domain=ValueDomain(kind="float", minimum=-1.0, maximum=1.0, unit='dimensionless'),
    permissions=Permissions(observe=True),
)
SLOT_WORLD_GEN_RAINFALL_NOISE = SlotDecl(
    id='world.gen.rainfall_noise',
    on='lattice.chunk',
    persist='external',
    domain=ValueDomain(kind="float", minimum=-1.0, maximum=1.0, unit='dimensionless'),
    permissions=Permissions(observe=True),
)
SLOT_WORLD_GEN_WIND_NOISE = SlotDecl(
    id='world.gen.wind_noise',
    on='lattice.chunk',
    persist='external',
    domain=ValueDomain(kind="float", minimum=-1.0, maximum=1.0, unit='dimensionless'),
    permissions=Permissions(observe=True),
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


# ── 见证（旧见证 → 只变单父的见证对）────────────────────
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
        ),
    ),
    impl=_impl_0,
    when=When(mode='phase', key='world.gen_derived_a'),
    equation=(
        "clamp(latitude_noise * 25 + 10, -20, 38)（定点 Q30，issue #52）"
    ),
    arithmetic=Arithmetic(domain='fixed', bits=30),
    params=(),
    witnesses=WITNESSES_0,
)

MECHANISM_1 = MechanismDecl(
    id='world.gen.derive_annual_rainfall.v1',
    output='weather.chunk.annual_rainfall_mm_per_year',
    parents=(
        Parent(
            slot='world.gen.rainfall_noise',
            argument='rainfall_noise',
            lag=0,
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
)

MECHANISM_2 = MechanismDecl(
    id='world.gen.derive_annual_mean_temperature.v1',
    output='weather.chunk.annual_mean_temperature_c',
    parents=(
        Parent(
            slot='weather.chunk.sea_level_temperature_c',
            argument='sea_level_temperature',
            lag=0,
        ),
        Parent(
            slot='world.gen.altitude_m',
            argument='altitude',
            lag=0,
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
)

MECHANISM_3 = MechanismDecl(
    id='world.gen.classify_climate_zone.v1',
    output='world.gen.climate_zone',
    parents=(
        Parent(
            slot='weather.chunk.annual_mean_temperature_c',
            argument='mean_temperature',
            lag=0,
        ),
        Parent(
            slot='weather.chunk.annual_rainfall_mm_per_year',
            argument='annual_rainfall',
            lag=0,
        ),
        Parent(
            slot='world.gen.altitude_m',
            argument='altitude',
            lag=0,
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
)

MECHANISM_4 = MechanismDecl(
    id='world.gen.classify_biome.v1',
    output='world.gen.biome',
    parents=(
        Parent(
            slot='weather.chunk.annual_mean_temperature_c',
            argument='mean_temperature',
            lag=0,
        ),
        Parent(
            slot='weather.chunk.annual_rainfall_mm_per_year',
            argument='annual_rainfall',
            lag=0,
        ),
        Parent(
            slot='world.gen.altitude_m',
            argument='altitude',
            lag=0,
        ),
        Parent(
            slot='weather.chunk.sea_level_temperature_c',
            argument='sea_level_temperature',
            lag=0,
        ),
        Parent(
            slot='world.gen.moisture_noise',
            argument='moisture_noise',
            lag=0,
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
)

MECHANISM_5 = MechanismDecl(
    id='world.gen.derive_baseline_humidity.v1',
    output='weather.chunk.baseline_humidity_percent',
    parents=(
        Parent(
            slot='world.gen.climate_zone',
            argument='climate_zone',
            lag=0,
        ),
        Parent(
            slot='world.gen.humidity_noise',
            argument='humidity_noise',
            lag=0,
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
)

MECHANISM_6 = MechanismDecl(
    id='world.gen.derive_baseline_wind_speed.v1',
    output='weather.chunk.baseline_wind_speed_mps',
    parents=(
        Parent(
            slot='world.gen.climate_zone',
            argument='climate_zone',
            lag=0,
        ),
        Parent(
            slot='world.gen.wind_noise',
            argument='wind_noise',
            lag=0,
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
)

MECHANISM_7 = MechanismDecl(
    id='world.gen.derive_mean_precip_intensity.v1',
    output='weather.chunk.mean_precip_intensity_mm_per_hour',
    parents=(
        Parent(
            slot='world.gen.climate_zone',
            argument='climate_zone',
            lag=0,
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
)

MECHANISM_8 = MechanismDecl(
    id='world.gen.derive_humidity_sharpness.v1',
    output='weather.chunk.humidity_sharpness',
    parents=(
        Parent(
            slot='world.gen.climate_zone',
            argument='climate_zone',
            lag=0,
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
)

# ── 模块包 ──────────────────────────────────────────────
MODULE = ModulePack(
    id='worldgen',
    version='1',
    instances=(GLOBAL, CHUNK),
    parameters=(
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
    evidence=('P1 黄金向量对拍（tests/world/test_weather_port.py）',),
    notes='由旧机制注册表一次性移植（P1）；P2 后为唯一事实源。',
)
