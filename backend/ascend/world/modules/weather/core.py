"""天气引擎适配器 — 新核心无状态求值（P2-2 引擎切换）。

引擎在任意 tick 查询天气（含历史重算），而 wired 天气机制全部是派生量
（无状态、无 lag）：适配器每次求值构造一个临时 ``WorldProcess``——物化
请求的 chunk、注入边界输入与干预覆盖、推进一帧、读回全部机制输出。

**wired 子集**：旧 ``WIRED_NODES`` 的 20 个节点在此固化为引擎求值子集
（其余 6 个天气机制产出基线/读出，由引擎作为边界提供）。P2-3 删除旧
注册表后，本清单即引擎的权威求值面。
"""

from __future__ import annotations

from dataclasses import replace
from typing import Mapping

from ascend.world.compile import compile_world
from ascend.world.meta.declarations import Schedule, WorldSpec
from ascend.world.runtime import DynamicField, WorldProcess

from ..pipeline import PIPELINE_PHASES
from . import engine_inputs
from .module import MODULE as WEATHER_MODULE

__all__ = ["WIRED_OUTPUTS", "WeatherCore", "wired_weather_pack"]

_CHUNK = "lattice.chunk"

# 引擎求值子集（旧 WIRED_NODES，20 节点）
WIRED_OUTPUTS: tuple[str, ...] = (
    "weather.astronomy.daylight_hours",
    "weather.astronomy.sunrise_hour",
    "weather.astronomy.sunset_hour",
    "weather.chunk.precipitation_threshold",
    "weather.instant.precipitation_intensity_mm_per_hour",
    "weather.instant.relative_humidity_percent",
    "weather.instant.sunshine_hours_per_day",
    "weather.instant.temperature_c",
    "weather.instant.wind_speed_mps",
    "weather.offset.diurnal_humidity_pp",
    "weather.offset.diurnal_temperature_c",
    "weather.offset.seasonal_humidity_pp",
    "weather.offset.seasonal_temperature_c",
    "weather.tick.day",
    "weather.tick.day_of_year",
    "weather.tick.diurnal_phase_cos",
    "weather.tick.hour_of_day",
    "weather.tick.season",
    "weather.tick.season_phase_cos",
    "weather.tick.solar_declination_rad",
)


def wired_weather_pack():
    """天气模块的 wired 子集（只保留被求值机制及其依赖槽位）。"""
    wired = tuple(
        mechanism
        for mechanism in WEATHER_MODULE.mechanisms
        if mechanism.outputs()[0] in WIRED_OUTPUTS
    )
    kept_slots = set(WIRED_OUTPUTS)
    for mechanism in wired:
        for parent in mechanism.parents:
            if parent.slot in engine_inputs.BASELINE_IDS:
                continue  # 引擎提供的基线由 engine_inputs 声明
            kept_slots.add(parent.slot)
    kept_slots -= set(engine_inputs.BASELINE_IDS)
    return replace(
        WEATHER_MODULE,
        id="weather.wired",
        mechanisms=wired,
        slots=tuple(
            slot for slot in WEATHER_MODULE.slots if slot.id in kept_slots
        ),
        notes="引擎求值子集（旧 WIRED_NODES）；P5 后由生成程序补全。",
    )


class WeatherCore:
    """按 (tick, 边界输入, 实例集合) 求值 wired 天气机制（无状态）。"""

    def __init__(self, program: object | None = None) -> None:
        self._program = program or compile_world(
            WorldSpec(
                modules=(engine_inputs.MODULE, wired_weather_pack()),
                schedule=Schedule(phases=PIPELINE_PHASES),
            )
        )
        self._outputs = tuple(
            slot
            for mechanism in self._program.mechanisms.values()
            for slot in mechanism.outputs()
        )

    @property
    def program(self) -> object:
        return self._program

    @property
    def wired_outputs(self) -> tuple[str, ...]:
        """引擎求值面（wired 节点清单）。"""
        return WIRED_OUTPUTS

    def evaluate(
        self,
        *,
        now: int,
        boundary: Mapping[tuple[str, tuple], object],
        instances: list[tuple[int, int]],
        node_overrides: Mapping[str, object] | None = None,
        instance_overrides: Mapping[str, Mapping[tuple, object]] | None = None,
        parameter_overrides: Mapping[str, object] | None = None,
    ) -> dict[tuple[str, tuple], object]:
        """求值一帧，返回 ``{(槽位, 实例): 值}``。"""
        values, _ = self.evaluate_frame(
            now=now,
            boundary=boundary,
            instances=instances,
            node_overrides=node_overrides,
            instance_overrides=instance_overrides,
            parameter_overrides=parameter_overrides,
        )
        return values

    def evaluate_frame(
        self,
        *,
        now: int,
        boundary: Mapping[tuple[str, tuple], object],
        instances: list[tuple[int, int]],
        node_overrides: Mapping[str, object] | None = None,
        instance_overrides: Mapping[str, Mapping[tuple, object]] | None = None,
        parameter_overrides: Mapping[str, object] | None = None,
        trace: bool = False,
    ) -> "tuple[dict[tuple[str, tuple], object], tuple]":
        """求值一帧；``trace`` 为真时同时返回逐机制捕获。"""
        process = WorldProcess(self._program)
        for coords in instances:
            process.materialize(_CHUNK, tuple(coords))
        inputs: dict[str, object] = {}
        for (node_id, instance), value in boundary.items():
            if instance == ():
                inputs[node_id] = value
            else:
                mapping = inputs.setdefault(node_id, {})
                mapping[tuple(instance)] = value  # type: ignore[index]
        interventions: dict[str, object] = {}
        if node_overrides:
            interventions.update(node_overrides)
        if instance_overrides:
            interventions.update(instance_overrides)
        result = process.step(
            inputs=inputs,
            interventions=interventions or None,
            parameters=parameter_overrides or None,
            trace=trace,
        )
        values: dict[tuple[str, tuple], object] = {}
        for slot_id in self._outputs:
            value = process.committed(slot_id)
            if isinstance(value, DynamicField):
                for coords, item in value.items():
                    values[(slot_id, coords)] = item
            else:
                values[(slot_id, ())] = value
        return values, result.traces
