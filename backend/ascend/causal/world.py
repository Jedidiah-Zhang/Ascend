"""全局因果机制注册表 — 唯一事实源组装。

合并天气模块与空间生成公式链的声明片段为单一不可变注册表。
生产求值点（derive.py / weather_engine.py / climate.py）经惰性导入
消费本注册表，避免与领域模块的 import 环。
"""

from __future__ import annotations

from .microsteps import MICROSTEP_ORDER
from .registry import MechanismRegistry
from ascend.space.mechanisms import (
    WORLD_GEN_MECHANISM_SPECS,
    WORLD_GEN_NODES,
    WORLD_GEN_PARAMETERS,
)
from ascend.weather.mechanisms import (
    WEATHER_MECHANISM_SPECS,
    WEATHER_NODES,
    WEATHER_PARAMETERS,
)


def build_registry() -> MechanismRegistry:
    """组装全局注册表（供打包布局回归测试重建；源码模式恒定不可变）。"""
    return MechanismRegistry(
        schema_version=3,
        declaration_id="ascend.world.scalar_formulas",
        declaration_version="1",
        microstep_order=MICROSTEP_ORDER,
        slice_boundary=(
            "Unified weather field channels, continent/hydrology/tile algorithm "
            "pipelines, and dynamic biome subdivision ranges remain outside this "
            "slice; their outputs enter as declared boundary inputs."
        ),
        nodes=WEATHER_NODES + WORLD_GEN_NODES,
        parameters=WEATHER_PARAMETERS + WORLD_GEN_PARAMETERS,
        exogenous_sources=(),
        mechanisms=WEATHER_MECHANISM_SPECS + WORLD_GEN_MECHANISM_SPECS,
    )


ASCEND_MECHANISMS = build_registry()

__all__ = ["ASCEND_MECHANISMS", "MICROSTEP_ORDER", "build_registry"]
