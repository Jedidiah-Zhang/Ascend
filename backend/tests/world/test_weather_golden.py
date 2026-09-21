"""天气/世界生成模块对拍 — 引擎求值子集与黄金向量逐位一致。

黄金向量是冻结的契约数据（``data/weather_golden.json``）：

- ``vectors``：逐机制输入 → 输出（见证 + 随机采样，含边界值）；
- ``frames``：单 chunk 边界输入 → 一帧内全部 35 个机制输出；
- ``chunks``：多 chunk 同帧（共享逻辑 tick）边界输入 → 各 chunk 输出。

测试只读冻结向量；实现漂移即红。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from ascend.world import (
    DynamicField,
    Schedule,
    WorldProcess,
    WorldSpec,
    compile_world,
)
from ascend.world.meta.context import MechanismContext
from ascend.world.modules import weather, worldgen
from ascend.world.modules.pipeline import PIPELINE_PHASES

_GOLDEN = json.loads(
    (Path(__file__).parent / "data" / "weather_golden.json").read_text(
        encoding="utf-8"
    )
)

_CHUNK = "lattice.chunk"
_SINGLE = (0, 0)


@pytest.fixture(scope="module")
def program():
    return compile_world(
        WorldSpec(
            modules=(worldgen.MODULE, weather.MODULE),
            schedule=Schedule(phases=PIPELINE_PHASES),
        )
    )


def _evaluate(program, mechanism_id: str, inputs: dict) -> object:
    mechanism = program.mechanisms[mechanism_id]
    argument_of = {parent.slot: parent.argument for parent in mechanism.parents}
    parent_values = {
        argument_of[slot]: value for slot, value in inputs.items()
    }
    context = MechanismContext(
        mechanism=mechanism,
        root_seed=0,
        tick=0,
        parent_values=parent_values,
        params=program.parameters,
    )
    return mechanism.impl(context)


def _single_chunk_inputs(inputs: dict, coords: tuple[int, ...]) -> dict:
    """单 chunk 边界输入 → 运行时输入（chunk 槽位按实例映射，tick 标量）。"""
    runtime: dict[str, object] = {}
    for slot, value in inputs.items():
        if slot == "world.clock.tick":
            runtime[slot] = value
        else:
            runtime[slot] = {coords: value}
    return runtime


def _multi_chunk_inputs(records: list[dict]) -> dict:
    inputs: dict[str, object] = {}
    for record in records:
        coords = tuple(record["coords"])
        for slot, value in record["inputs"].items():
            if slot == "world.clock.tick":
                inputs[slot] = value
            else:
                mapping = inputs.setdefault(slot, {})
                mapping[coords] = value  # type: ignore[index]
    return inputs


def _at(process: WorldProcess, slot: str, coords: tuple[int, ...]) -> object:
    value = process.committed(slot)
    if isinstance(value, DynamicField):
        return value.get(coords)
    return value


class TestGoldenVectors:
    def test_vector_count(self):
        assert len(_GOLDEN["vectors"]) >= 500
        assert len(_GOLDEN["frames"]) >= 4
        assert len(_GOLDEN["chunks"]) >= 4

    def test_vectors_bit_exact(self, program):
        mismatches = []
        for vector in _GOLDEN["vectors"]:
            actual = _evaluate(program, vector["mechanism"], vector["inputs"])
            if actual != vector["output"]:
                mismatches.append(
                    (vector["mechanism"], vector["inputs"],
                     vector["output"], actual)
                )
        assert not mismatches, mismatches[:3]

    def test_frames_bit_exact(self, program):
        mismatches = []
        for frame in _GOLDEN["frames"]:
            process = WorldProcess(program)
            process.materialize(_CHUNK, _SINGLE)
            process.step(inputs=_single_chunk_inputs(frame["inputs"], _SINGLE))
            for slot, expected in frame["outputs"].items():
                actual = _at(process, slot, _SINGLE)
                if actual != expected:
                    mismatches.append((slot, expected, actual))
        assert not mismatches, mismatches[:3]

    def test_chunks_bit_exact(self, program):
        records = _GOLDEN["chunks"]
        process = WorldProcess(program)
        for record in records:
            process.materialize(_CHUNK, tuple(record["coords"]))
        process.step(inputs=_multi_chunk_inputs(records))
        mismatches = []
        for record in records:
            coords = tuple(record["coords"])
            for slot, expected in record["outputs"].items():
                actual = _at(process, slot, coords)
                if actual != expected:
                    mismatches.append((coords, slot, expected, actual))
        assert not mismatches, mismatches[:3]

    def test_materialization_order_independent(self, program):
        records = _GOLDEN["chunks"]
        inputs = _multi_chunk_inputs(records)
        first = WorldProcess(program)
        second = WorldProcess(program)
        for record in records:
            first.materialize(_CHUNK, tuple(record["coords"]))
        for record in reversed(records):
            second.materialize(_CHUNK, tuple(record["coords"]))
        first.step(inputs=inputs)
        second.step(inputs=inputs)
        for record in records:
            coords = tuple(record["coords"])
            for slot in record["outputs"]:
                assert _at(first, slot, coords) == _at(second, slot, coords)

    def test_frame_reproducible(self, program):
        frame = _GOLDEN["frames"][0]
        first = WorldProcess(program)
        second = WorldProcess(program)
        for process in (first, second):
            process.materialize(_CHUNK, _SINGLE)
            process.step(inputs=_single_chunk_inputs(frame["inputs"], _SINGLE))
        for slot in frame["outputs"]:
            assert _at(first, slot, _SINGLE) == _at(second, slot, _SINGLE)
