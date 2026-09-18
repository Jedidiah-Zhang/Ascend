"""引擎切换对拍（P2-2）— 新适配器 vs 旧引擎边界求值。

- ``engine_golden.json``：旧注册表按引擎边界（11 基线 + tick + 5 场扰动）
  逐 chunk 求值 wired 节点（20）；
- 新 ``WeatherCore`` 必须逐位一致（单 chunk 与多 chunk 同帧）；
- 节点干预（逐实例值替换）在新路径上生效。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from ascend.world.modules.weather.core import WIRED_OUTPUTS, WeatherCore

_GOLDEN = json.loads(
    (Path(__file__).parent / "data" / "engine_golden.json").read_text(
        encoding="utf-8"
    )
)
_TICK = "world.clock.tick"


def _boundary(record: dict) -> dict:
    coords = tuple(record["coords"])
    boundary = {}
    for slot, value in record["inputs"].items():
        if slot == _TICK:
            boundary[(slot, ())] = value
        else:
            boundary[(slot, coords)] = value
    return boundary


def _value(values: dict, slot: str, coords: tuple) -> object:
    """全局槽位实例为 ()，chunk 槽位为坐标。"""
    if (slot, coords) in values:
        return values[(slot, coords)]
    return values[(slot, ())]


@pytest.fixture(scope="module")
def core():
    return WeatherCore()


class TestEngineSwitch:
    def test_wired_outputs_are_20(self, core):
        assert len(WIRED_OUTPUTS) == 20
        assert set(WIRED_OUTPUTS) == set(
            mechanism.outputs()[0]
            for mechanism in core.program.mechanisms.values()
        )

    def test_wired_matches_old(self, core):
        mismatches = []
        for record in _GOLDEN["chunks"]:
            coords = tuple(record["coords"])
            values = core.evaluate(
                now=record["inputs"][_TICK],
                boundary=_boundary(record),
                instances=[coords],
            )
            for slot, expected in record["outputs"].items():
                actual = _value(values, slot, coords)
                if actual != expected:
                    mismatches.append((coords, slot, expected, actual))
        assert not mismatches, mismatches[:3]

    def test_multiple_chunks_same_frame(self, core):
        records = _GOLDEN["chunks"]
        boundary = {}
        for record in records:
            boundary.update(_boundary(record))
        values = core.evaluate(
            now=_GOLDEN["tick"],
            boundary=boundary,
            instances=[tuple(record["coords"]) for record in records],
        )
        mismatches = []
        for record in records:
            coords = tuple(record["coords"])
            for slot, expected in record["outputs"].items():
                actual = _value(values, slot, coords)
                if actual != expected:
                    mismatches.append((coords, slot, expected, actual))
        assert not mismatches, mismatches[:3]

    def test_intervention_on_new_path(self, core):
        record = _GOLDEN["chunks"][0]
        coords = tuple(record["coords"])
        target = "weather.instant.temperature_c"
        values = core.evaluate(
            now=record["inputs"][_TICK],
            boundary=_boundary(record),
            instances=[coords],
            instance_overrides={target: {coords: 123.0}},
        )
        assert values[(target, coords)] == 123.0

    def test_reproducible(self, core):
        record = _GOLDEN["chunks"][0]
        coords = tuple(record["coords"])
        first = core.evaluate(
            now=record["inputs"][_TICK],
            boundary=_boundary(record),
            instances=[coords],
        )
        second = core.evaluate(
            now=record["inputs"][_TICK],
            boundary=_boundary(record),
            instances=[coords],
        )
        assert first == second
