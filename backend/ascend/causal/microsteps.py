"""因果世界声明的全局微步偏序（唯一事实源）。"""

from __future__ import annotations

WORLD_GEN_INPUT = "world.gen_input"
WORLD_GEN_DERIVED_A = "world.gen_derived_a"
WORLD_GEN_DERIVED_B = "world.gen_derived_b"
WORLD_GEN_DERIVED_C = "world.gen_derived_c"
WORLD_GEN_DERIVED_D = "world.gen_derived_d"
WEATHER_CHUNK_DERIVED_A = "weather.chunk_derived_a"
WEATHER_CHUNK_DERIVED_B = "weather.chunk_derived_b"
WEATHER_FRAME_INPUT = "weather.frame_input"
WEATHER_INSTANT_TICK_INPUT = "weather.instant_tick_input"
WEATHER_INSTANT_TICK_DERIVED = "weather.instant_tick_derived"
WEATHER_INSTANT_OFFSET = "weather.instant_offset"
WEATHER_INSTANT_COMPOSITE = "weather.instant_composite"
WEATHER_INSTANT_READOUT = "weather.instant_readout"

MICROSTEP_ORDER: tuple[str, ...] = (
    WORLD_GEN_INPUT,
    WORLD_GEN_DERIVED_A,
    WORLD_GEN_DERIVED_B,
    WORLD_GEN_DERIVED_C,
    WORLD_GEN_DERIVED_D,
    WEATHER_CHUNK_DERIVED_A,
    WEATHER_CHUNK_DERIVED_B,
    WEATHER_FRAME_INPUT,
    WEATHER_INSTANT_TICK_INPUT,
    WEATHER_INSTANT_TICK_DERIVED,
    WEATHER_INSTANT_OFFSET,
    WEATHER_INSTANT_COMPOSITE,
    WEATHER_INSTANT_READOUT,
)
