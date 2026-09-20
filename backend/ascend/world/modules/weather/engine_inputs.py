"""天气引擎输入模块 — 引擎提供的 chunk 基线（引擎切换）。

旧引擎把 chunk 气候基线（大陆模型生成 / 派生）作为边界输入提供，不重算
这些机制（``WIRED_NODES`` 只含 20 个天气节点）。本模块把这些槽位声明为
external，与「wired 天气子集」组合编译，保持与旧引擎相同的数据流；
生成程序声明化后由生成程序产出。
"""

from __future__ import annotations

from dataclasses import replace

from ascend.world.meta.declarations import ModulePack
from ascend.world.modules import worldgen
from ascend.world.modules.primitives import GLOBAL

from .module import CHUNK, MODULE as WEATHER_MODULE

__all__ = ["BASELINE_IDS", "MODULE"]

# 引擎提供的 11 个基线节点（旧 weather_engine._boundary_values）
BASELINE_IDS: tuple[str, ...] = (
    "weather.chunk.annual_mean_temperature_c",
    "weather.chunk.annual_rainfall_mm_per_year",
    "weather.chunk.baseline_humidity_percent",
    "weather.chunk.baseline_wind_speed_mps",
    "weather.chunk.diurnal_humidity_amplitude_pp",
    "weather.chunk.diurnal_temperature_amplitude_c",
    "weather.chunk.humidity_sharpness",
    "weather.chunk.mean_precip_intensity_mm_per_hour",
    "weather.chunk.seasonal_humidity_amplitude_pp",
    "weather.chunk.seasonal_temperature_amplitude_c",
    "weather.chunk.solar_latitude_proxy_deg",
)

_SOURCES = {
    slot.id: slot
    for pack in (WEATHER_MODULE, worldgen.MODULE)
    for slot in pack.slots
}

_SLOTS = tuple(
    replace(
        _SOURCES[slot_id],
        persist="external",
        writer=None,
        recompute="",
    )
    for slot_id in BASELINE_IDS
)

MODULE = ModulePack(
    id="weather.engine_inputs",
    version="1",
    instances=(GLOBAL, CHUNK),
    slots=_SLOTS,
    evidence=("引擎切换对拍：tests/world/test_engine_switch.py",),
    notes="引擎提供的 chunk 基线（旧 WIRED 子集的输入面）。",
)
