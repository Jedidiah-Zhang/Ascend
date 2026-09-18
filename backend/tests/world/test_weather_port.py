"""天气/世界生成模块移植对拍 — 与旧注册表黄金向量逐位一致（P1）。

黄金向量由旧实现一次性生成（``data/weather_golden.json``）：

- ``vectors``：逐机制输入 → 输出（覆盖旧见证 + 随机采样，含边界值）；
- ``frames``：边界输入 → 一帧内全部 35 个机制输出（帧级顺序契约）。

测试只读冻结向量，不依赖旧注册表：P2 删除旧代码后本对拍继续有效。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from ascend.world import Schedule, WorldProcess, WorldSpec, compile_world
from ascend.world.meta.context import MechanismContext
from ascend.world.modules import weather, worldgen
from ascend.world.modules.pipeline import PIPELINE_PHASES

_GOLDEN = json.loads(
    (Path(__file__).parent / "data" / "weather_golden.json").read_text(
        encoding="utf-8"
    )
)


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


class TestGoldenVectors:
    def test_vector_count(self):
        assert len(_GOLDEN["vectors"]) >= 500
        assert len(_GOLDEN["frames"]) >= 4

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
            process.step(inputs=frame["inputs"])
            for slot, expected in frame["outputs"].items():
                actual = process.committed(slot)
                if actual != expected:
                    mismatches.append((slot, expected, actual))
        assert not mismatches, mismatches[:3]

    def test_frame_reproducible(self, program):
        frame = _GOLDEN["frames"][0]
        first = WorldProcess(program)
        second = WorldProcess(program)
        first.step(inputs=frame["inputs"])
        second.step(inputs=frame["inputs"])
        for slot in frame["outputs"]:
            assert first.committed(slot) == second.committed(slot)
