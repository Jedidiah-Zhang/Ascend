"""全局因果机制注册表 — 唯一事实源组装。

合并天气模块与空间生成公式链的声明片段为单一不可变注册表。
生产求值点（derive.py / weather_engine.py / climate.py）经惰性导入
消费本注册表，避免与领域模块的 import 环。

``WIRED_NODES`` 是干预执行器可达性的事实源：当前引擎真正执行的生成节点
（weather_engine 的 tick 派生 + chunk 求值，以及 region_tracker 经注入
求值器复用的两个降水节点）。未列出的已声明节点登记干预时会被拒绝，
而不是静默无效。漂移由 ``tests/unit/test_intervention_wiring.py`` 的
求值点巡检锁死（扫描 ``evaluate_node`` 调用与本源比对）。
"""

from __future__ import annotations

from .microsteps import MICROSTEP_ORDER
from .registry import MechanismRegistry
from ascend.space.mechanisms import (
    WORLD_GEN_MECHANISM_SPECS,
    WORLD_GEN_NODES,
    WORLD_GEN_PARAMETERS,
)
from ascend.weather import mechanisms as wm
from ascend.weather.mechanisms import (
    WEATHER_MECHANISM_SPECS,
    WEATHER_NODES,
    WEATHER_PARAMETERS,
)

# 当前引擎真正求值的节点（tick 派生 7 + chunk 求值 13）。
WIRED_NODES: frozenset[str] = frozenset({
    # _tick_context（全局实例）
    wm.DAY,
    wm.HOUR_OF_DAY,
    wm.DAY_OF_YEAR,
    wm.SEASON,
    wm.SOLAR_DECLINATION,
    wm.SEASON_PHASE_COS,
    wm.DIURNAL_PHASE_COS,
    # _compute_params（chunk 实例）
    wm.SEASONAL_TEMPERATURE_OFFSET,
    wm.DIURNAL_TEMPERATURE_OFFSET,
    wm.SEASONAL_HUMIDITY_OFFSET,
    wm.DIURNAL_HUMIDITY_OFFSET,
    wm.SUNRISE_HOUR,
    wm.SUNSET_HOUR,
    wm.DAYLIGHT_HOURS,
    wm.PRECIPITATION_THRESHOLD,
    wm.INSTANT_TEMPERATURE,
    wm.INSTANT_HUMIDITY,
    wm.INSTANT_WIND_SPEED,
    wm.INSTANT_PRECIPITATION_INTENSITY,
    wm.INSTANT_SUNSHINE,
})


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
        wired_nodes=WIRED_NODES,
        nodes=WEATHER_NODES + WORLD_GEN_NODES,
        parameters=WEATHER_PARAMETERS + WORLD_GEN_PARAMETERS,
        exogenous_sources=(),
        mechanisms=WEATHER_MECHANISM_SPECS + WORLD_GEN_MECHANISM_SPECS,
    )


ASCEND_MECHANISMS = build_registry()

__all__ = ["ASCEND_MECHANISMS", "MICROSTEP_ORDER", "WIRED_NODES", "build_registry"]
