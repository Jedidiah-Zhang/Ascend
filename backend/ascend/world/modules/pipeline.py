"""世界生成 → 天气链的阶段序。

阶段名即帧内求值序（`world.gen_input` … `weather.instant_readout`）：
同帧父引用必须由更早阶段提供。本序是调度与机制声明的唯一事实源。
"""

from __future__ import annotations

__all__ = ["PIPELINE_PHASES"]

PIPELINE_PHASES: tuple[str, ...] = (
    "world.gen_input",
    "world.gen_derived_a",
    "world.gen_derived_b",
    "world.gen_derived_c",
    "world.gen_derived_d",
    "weather.chunk_derived_a",
    "weather.chunk_derived_b",
    "weather.frame_input",
    "weather.instant_tick_input",
    "weather.instant_tick_derived",
    "weather.instant_offset",
    "weather.instant_composite",
    "weather.instant_readout",
)
