"""地形状态模块 — 统一演化内核。

一个 field 作用域机制（整场内核）：三状态通道 + 地形/坡度/遮蔽 +
逐步降水/温度/步长。参考实现与 C 加速逐位一致（内核对）；黄金向量
（tests/world/data/terrain_golden.json）为冻结契约数据。

**边界**：lattice 支持固定尺寸与流式物化；更新点周期由驱动层声明绑定。
参数表来自 data/terrain.json（内容数据）。
"""
from __future__ import annotations

from ascend.world.meta.declarations import (
    Arithmetic,
    InstanceDecl,
    MechanismDecl,
    ModulePack,
    Parent,
    Permissions,
    SlotDecl,
    ValueDomain,
    When,
    Witness,
)
from ascend.world.modules.primitives import GLOBAL

from . import kernel as _kernel

__all__ = ["MODULE"]

CHUNK = InstanceDecl(
    id="lattice.chunk", kind="lattice", identity="xy", size=None,
    axes=("chunk_x", "chunk_y"),
)

_INTERVENE = Permissions(intervene=True, observe=True, record=True)
_OBSERVE = Permissions(observe=True, record=True)

SLOT_TERRAIN_MOISTURE = SlotDecl(
    id='terrain.moisture',
    on='lattice.chunk',
    persist='state',
    domain=ValueDomain(kind='int', bits=8, minimum=0, maximum=100),
    permissions=_INTERVENE,
    writer='terrain.integrate', initial=0,
    role='mechanism_state',
    schedule='on_terrain_integration',
    quantization='integer',
    metric='absolute_difference',
    access_interventions=('node', 'persistent'),
    observation_protocols=('research.full.v1',),
    research_trace=True,
)

SLOT_TERRAIN_SNOW = SlotDecl(
    id='terrain.snow',
    on='lattice.chunk',
    persist='state',
    domain=ValueDomain(kind='int', bits=8, minimum=0, maximum=255),
    permissions=_INTERVENE,
    writer='terrain.integrate', initial=0,
    role='mechanism_state',
    schedule='on_terrain_integration',
    quantization='integer',
    metric='absolute_difference',
    access_interventions=('node', 'persistent'),
    observation_protocols=('research.full.v1',),
    research_trace=True,
)

SLOT_TERRAIN_ICE = SlotDecl(
    id='terrain.ice',
    on='lattice.chunk',
    persist='state',
    domain=ValueDomain(kind='int', bits=8, minimum=0, maximum=255),
    permissions=_INTERVENE,
    writer='terrain.integrate', initial=0,
    role='mechanism_state',
    schedule='on_terrain_integration',
    quantization='integer',
    metric='absolute_difference',
    access_interventions=('node', 'persistent'),
    observation_protocols=('research.full.v1',),
    research_trace=True,
)

SLOT_TERRAIN_TERRAIN_ID = SlotDecl(
    id='terrain.terrain_id',
    on='lattice.chunk',
    persist='external',
    domain=ValueDomain(kind='int', bits=16, minimum=0, maximum=255),
    permissions=_OBSERVE,
)

SLOT_TERRAIN_SLOPE = SlotDecl(
    id='terrain.slope',
    on='lattice.chunk',
    persist='external',
    domain=ValueDomain(kind='float', minimum=0.0, maximum=1.0),
    permissions=_OBSERVE,
)

SLOT_TERRAIN_COVER = SlotDecl(
    id='terrain.cover',
    on='lattice.chunk',
    persist='external',
    domain=ValueDomain(kind='float', minimum=0.0, maximum=1.0),
    permissions=_OBSERVE,
)

SLOT_WEATHER_PRECIP_MOISTURE = SlotDecl(
    id='weather.precip_moisture',
    on='global',
    persist='external',
    domain=ValueDomain(kind='float', minimum=0.0, maximum=100.0),
    permissions=_OBSERVE,
)

SLOT_WEATHER_PRECIP_SNOW = SlotDecl(
    id='weather.precip_snow',
    on='global',
    persist='external',
    domain=ValueDomain(kind='float', minimum=0.0, maximum=100.0),
    permissions=_OBSERVE,
)

SLOT_WEATHER_STEP_TEMP = SlotDecl(
    id='weather.step_temp',
    on='global',
    persist='external',
    domain=ValueDomain(kind='float', minimum=-100.0, maximum=100.0),
    permissions=_OBSERVE,
)

SLOT_TERRAIN_DT = SlotDecl(
    id='terrain.dt',
    on='global',
    persist='external',
    domain=ValueDomain(kind='float', minimum=0.0, maximum=10.0),
    permissions=_OBSERVE,
)

def _integrate_reference(ctx: object) -> dict[str, list[int]]:
    result = _kernel.evolve_reference(
        {key: ctx.parent(key) for key in _kernel.STATE_KEYS},
        ctx.parent("terrain_id"),
        ctx.parent("slope"),
        precip=[
            [ctx.parent("precip_moisture")],
            [ctx.parent("precip_snow")],
            [0.0],  # 冰通道无沉积（内容数据 ice.deposit 全 0）
        ],
        temp=[ctx.parent("step_temp")],
        dt=ctx.parent("dt"),
        cover=ctx.parent("cover"),
    )
    return {f"terrain.{key}": result[key] for key in _kernel.STATE_KEYS}


def _integrate_accelerated(ctx: object) -> dict[str, list[int]]:
    result = _kernel.evolve_accelerated(
        {key: ctx.parent(key) for key in _kernel.STATE_KEYS},
        ctx.parent("terrain_id"),
        ctx.parent("slope"),
        precip=[
            [ctx.parent("precip_moisture")],
            [ctx.parent("precip_snow")],
            [0.0],  # 冰通道无沉积（内容数据 ice.deposit 全 0）
        ],
        temp=[ctx.parent("step_temp")],
        dt=ctx.parent("dt"),
        cover=ctx.parent("cover"),
    )
    return {f"terrain.{key}": result[key] for key in _kernel.STATE_KEYS}


_WITNESSES = (
    Witness(
        'base',
        {'cover': None,
         'dt': 1.0,
         'ice': [0, 0, 10, 20],
         'moisture': [0, 10, 20, 30],
         'precip_moisture': 5.0,
         'precip_snow': 4.0,
         'slope': [0.0, 0.2, 0.0, 0.5],
         'snow': [5, 0, 0, 0],
         'step_temp': -5.0,
         'terrain_id': [0, 0, 7, 7]},
        ([8, 17, 20, 30], [9, 4, 4, 4], [0, 0, 18, 28]),
    ),
    Witness(
        'moisture_changed',
        {'cover': None,
         'dt': 1.0,
         'ice': [0, 0, 10, 20],
         'moisture': [1, 11, 21, 31],
         'precip_moisture': 5.0,
         'precip_snow': 4.0,
         'slope': [0.0, 0.2, 0.0, 0.5],
         'snow': [5, 0, 0, 0],
         'step_temp': -5.0,
         'terrain_id': [0, 0, 7, 7]},
        ([9, 18, 21, 31], [9, 4, 4, 4], [0, 0, 18, 28]),
    ),
    Witness(
        'snow_changed',
        {'cover': None,
         'dt': 1.0,
         'ice': [0, 0, 10, 20],
         'moisture': [0, 10, 20, 30],
         'precip_moisture': 5.0,
         'precip_snow': 4.0,
         'slope': [0.0, 0.2, 0.0, 0.5],
         'snow': [9, 0, 0, 0],
         'step_temp': -5.0,
         'terrain_id': [0, 0, 7, 7]},
        ([8, 17, 20, 30], [13, 4, 4, 4], [0, 0, 18, 28]),
    ),
    Witness(
        'ice_changed',
        {'cover': None,
         'dt': 1.0,
         'ice': [0, 0, 20, 20],
         'moisture': [0, 10, 20, 30],
         'precip_moisture': 5.0,
         'precip_snow': 4.0,
         'slope': [0.0, 0.2, 0.0, 0.5],
         'snow': [5, 0, 0, 0],
         'step_temp': -5.0,
         'terrain_id': [0, 0, 7, 7]},
        ([8, 17, 20, 30], [9, 4, 4, 4], [0, 0, 28, 28]),
    ),
    Witness(
        'terrain_changed',
        {'cover': None,
         'dt': 1.0,
         'ice': [0, 0, 10, 20],
         'moisture': [0, 10, 20, 30],
         'precip_moisture': 5.0,
         'precip_snow': 4.0,
         'slope': [0.0, 0.2, 0.0, 0.5],
         'snow': [5, 0, 0, 0],
         'step_temp': -5.0,
         'terrain_id': [0, 0, 0, 0]},
        ([8, 17, 26, 34], [9, 4, 4, 4], [0, 0, 10, 20]),
    ),
    Witness(
        'slope_changed',
        {'cover': None,
         'dt': 1.0,
         'ice': [0, 0, 10, 20],
         'moisture': [0, 10, 20, 30],
         'precip_moisture': 5.0,
         'precip_snow': 4.0,
         'slope': [1.0, 1.0, 1.0, 1.0],
         'snow': [5, 0, 0, 0],
         'step_temp': -5.0,
         'terrain_id': [0, 0, 7, 7]},
        ([8, 16, 20, 30], [9, 4, 4, 4], [0, 0, 18, 28]),
    ),
    Witness(
        'cover_changed',
        {'cover': [0.5, 0.5, 0.5, 0.5],
         'dt': 1.0,
         'ice': [0, 0, 10, 20],
         'moisture': [0, 10, 20, 30],
         'precip_moisture': 5.0,
         'precip_snow': 4.0,
         'slope': [0.0, 0.2, 0.0, 0.5],
         'snow': [5, 0, 0, 0],
         'step_temp': -5.0,
         'terrain_id': [0, 0, 7, 7]},
        ([4, 13, 20, 30], [7, 2, 2, 2], [0, 0, 18, 28]),
    ),
    Witness(
        'precip_moisture_changed',
        {'cover': None,
         'dt': 1.0,
         'ice': [0, 0, 10, 20],
         'moisture': [0, 10, 20, 30],
         'precip_moisture': 4.0,
         'precip_snow': 4.0,
         'slope': [0.0, 0.2, 0.0, 0.5],
         'snow': [5, 0, 0, 0],
         'step_temp': -5.0,
         'terrain_id': [0, 0, 7, 7]},
        ([6, 15, 20, 30], [9, 4, 4, 4], [0, 0, 18, 28]),
    ),
    Witness(
        'precip_snow_changed',
        {'cover': None,
         'dt': 1.0,
         'ice': [0, 0, 10, 20],
         'moisture': [0, 10, 20, 30],
         'precip_moisture': 5.0,
         'precip_snow': 3.0,
         'slope': [0.0, 0.2, 0.0, 0.5],
         'snow': [5, 0, 0, 0],
         'step_temp': -5.0,
         'terrain_id': [0, 0, 7, 7]},
        ([8, 17, 20, 30], [8, 3, 3, 3], [0, 0, 18, 28]),
    ),
    Witness(
        'step_temp_changed',
        {'cover': None,
         'dt': 1.0,
         'ice': [0, 0, 10, 20],
         'moisture': [0, 10, 20, 30],
         'precip_moisture': 5.0,
         'precip_snow': 4.0,
         'slope': [0.0, 0.2, 0.0, 0.5],
         'snow': [5, 0, 0, 0],
         'step_temp': 10.0,
         'terrain_id': [0, 0, 7, 7]},
        ([8, 15, 20, 30], [2, 4, 4, 4], [0, 0, 0, 0]),
    ),
    Witness(
        'dt_changed',
        {'cover': None,
         'dt': 0.25,
         'ice': [0, 0, 10, 20],
         'moisture': [0, 10, 20, 30],
         'precip_moisture': 5.0,
         'precip_snow': 4.0,
         'slope': [0.0, 0.2, 0.0, 0.5],
         'snow': [5, 0, 0, 0],
         'step_temp': -5.0,
         'terrain_id': [0, 0, 7, 7]},
        ([2, 12, 20, 30], [6, 1, 1, 1], [0, 0, 12, 22]),
    ),
)

MODULE = ModulePack(
    id='terrain',
    version='1',
    instances=(GLOBAL, CHUNK),
    slots=(
        SLOT_TERRAIN_MOISTURE,
        SLOT_TERRAIN_SNOW,
        SLOT_TERRAIN_ICE,
        SLOT_TERRAIN_TERRAIN_ID,
        SLOT_TERRAIN_SLOPE,
        SLOT_TERRAIN_COVER,
        SLOT_WEATHER_PRECIP_MOISTURE,
        SLOT_WEATHER_PRECIP_SNOW,
        SLOT_WEATHER_STEP_TEMP,
        SLOT_TERRAIN_DT,
    ),
    mechanisms=(
        MechanismDecl(
            id='terrain.integrate',
            output=(
                'terrain.moisture', 'terrain.snow', 'terrain.ice',
            ),
            parents=(
                Parent(
                    slot='terrain.moisture',
                    argument='moisture',
                    lag=1,
                ),
                Parent(
                    slot='terrain.snow',
                    argument='snow',
                    lag=1,
                ),
                Parent(
                    slot='terrain.ice',
                    argument='ice',
                    lag=1,
                ),
                Parent(
                    slot='terrain.terrain_id',
                    argument='terrain_id',
                    lag=0,
                ),
                Parent(
                    slot='terrain.slope',
                    argument='slope',
                    lag=0,
                ),
                Parent(
                    slot='terrain.cover',
                    argument='cover',
                    lag=0,
                ),
                Parent(
                    slot='weather.precip_moisture',
                    argument='precip_moisture',
                    lag=0,
                ),
                Parent(
                    slot='weather.precip_snow',
                    argument='precip_snow',
                    lag=0,
                ),
                Parent(
                    slot='weather.step_temp',
                    argument='step_temp',
                    lag=0,
                ),
                Parent(
                    slot='terrain.dt',
                    argument='dt',
                    lag=0,
                ),
            ),
            impl=_integrate_reference,
            accelerated=_integrate_accelerated,
            scope='field',
            when=When(mode='period', key='hour'),
            equation='统一演化公式（见 kernel.py 模块注释）',
            arithmetic=Arithmetic(domain='fixed', bits=30),
            witnesses=_WITNESSES,
            boundary_cases=(
                '状态按通道上界 clamp（[0, state_max]）',
                '输出按 int(v + 0.5) 量化回 uint8',
                'precip 形状 / slope 长度与状态长度不符即拒绝',
                'dt 由调度周期（hour）给出；dt=0 时状态不变',
            ),
            notes='C 加速与参考实现逐位一致（内核对）；黄金向量冻结',
        ),
    ),
    evidence=(
        '内核对：tests/world/test_terrain_kernel.py',
        '黄金向量：tests/world/data/terrain_golden.json（冻结契约数据）',
    ),
    notes='地形状态模块：统一演化内核。',
)
