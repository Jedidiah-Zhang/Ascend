"""地形内核测试 — 参数表一致、黄金向量、内核对与模块帧（P1）。

- 参数表：新装载器 vs 旧 ``state_defs.build_param_tables`` 逐值一致；
- 黄金向量：新参考实现 vs 旧实现输出（``data/terrain_golden.json``）；
- 内核对：参考实现 vs C 加速逐位一致（随机 + 黄金输入）；
- 模块帧：新核心编译并推进一帧，输出与旧实现一致。
"""

from __future__ import annotations

import json
import random
from pathlib import Path

import pytest

from ascend.space.state_defs import build_param_tables as old_param_tables
from ascend.world import LatticeField, Schedule, WorldProcess, WorldSpec, compile_world
from ascend.world.modules import terrain
from ascend.world.modules.terrain import kernel
from ascend.world.modules.terrain.data import build_param_tables

_GOLDEN = json.loads(
    (Path(__file__).parent / "data" / "terrain_golden.json").read_text(
        encoding="utf-8"
    )
)
_RNG = random.Random(20260918)


def _call(func, case: dict) -> dict[str, list[int]]:
    return func(
        case["states"],
        case["terrain"],
        case["slope"],
        precip=case["precip"],
        temp=case["temp"],
        dt=case["dt"],
        cover=case["cover"],
    )


class TestParamTables:
    def test_matches_old_loader(self):
        new = build_param_tables()
        old = old_param_tables()
        assert len(new) == len(old) == 7
        for index, (new_table, old_table) in enumerate(zip(new, old)):
            assert new_table == old_table, f"第 {index} 张表不一致"


class TestGoldenVectors:
    def test_reference_matches_old(self):
        mismatches = []
        for vector in _GOLDEN["vectors"]:
            actual = _call(kernel.evolve_reference, vector["inputs"])
            if actual != vector["outputs"]:
                mismatches.append((vector["inputs"], vector["outputs"], actual))
        assert not mismatches, mismatches[:2]

    def test_accelerated_matches_old(self):
        mismatches = []
        for vector in _GOLDEN["vectors"]:
            actual = _call(kernel.evolve_accelerated, vector["inputs"])
            if actual != vector["outputs"]:
                mismatches.append((vector["inputs"], vector["outputs"], actual))
        assert not mismatches, mismatches[:2]


class TestKernelPair:
    def test_bit_identical_random(self):
        for _ in range(12):
            n = _RNG.choice([1, 3, 17, 64])
            case = {
                "states": {
                    key: [_RNG.randint(0, 255) for _ in range(n)]
                    for key in kernel.STATE_KEYS
                },
                "terrain": [_RNG.randint(0, 7) for _ in range(n)],
                "slope": [_RNG.random() for _ in range(n)],
                "precip": [
                    [_RNG.random() * 5.0],
                    [_RNG.random() * 5.0],
                    [0.0],
                ],
                "temp": [_RNG.uniform(-25.0, 35.0)],
                "dt": _RNG.choice([1.0, 1 / 24, 24.0]),
                "cover": (
                    None
                    if _RNG.random() < 0.5
                    else [_RNG.random() for _ in range(n)]
                ),
            }
            reference = _call(kernel.evolve_reference, case)
            accelerated = _call(kernel.evolve_accelerated, case)
            assert reference == accelerated, case

    def test_empty_steps(self):
        case = {
            "states": {key: [1, 2] for key in kernel.STATE_KEYS},
            "terrain": [0, 0],
            "slope": [0.0, 0.0],
            "precip": [[], [], []],
            "temp": [],
            "dt": 1.0,
            "cover": None,
        }
        assert _call(kernel.evolve_reference, case) == {
            key: [1, 2] for key in kernel.STATE_KEYS
        }


class TestTerrainModuleFrame:
    def test_module_frame_matches_old(self):
        program = compile_world(
            WorldSpec(
                modules=(terrain.MODULE,),
                schedule=Schedule(periods=(("hour", 1),)),
            )
        )
        payload = _GOLDEN["module"]
        inputs = payload["inputs"]
        initial = {
            f"terrain.{key}": LatticeField(
                (4,), 0,
            )
            for key in kernel.STATE_KEYS
        }
        for key, values in inputs["states"].items():
            field = initial[f"terrain.{key}"]
            for index, value in enumerate(values):
                field.set((index,), value)
        process = WorldProcess(program, initial_state=initial)
        process.step(
            inputs={
                "terrain.terrain_id": inputs["terrain_id"],
                "terrain.slope": inputs["slope"],
                "terrain.cover": inputs["cover"],
                "weather.precip_moisture": inputs["precip_moisture"],
                "weather.precip_snow": inputs["precip_snow"],
                "weather.step_temp": inputs["step_temp"],
                "terrain.dt": inputs["dt"],
            }
        )
        for slot, expected in payload["outputs"].items():
            assert process.committed(slot).values() == tuple(expected)

    def test_module_reproducible(self):
        program = compile_world(
            WorldSpec(
                modules=(terrain.MODULE,),
                schedule=Schedule(periods=(("hour", 1),)),
            )
        )
        payload = _GOLDEN["module"]["inputs"]
        externals = {
            "terrain.terrain_id": payload["terrain_id"],
            "terrain.slope": payload["slope"],
            "terrain.cover": payload["cover"],
            "weather.precip_moisture": payload["precip_moisture"],
            "weather.precip_snow": payload["precip_snow"],
            "weather.step_temp": payload["step_temp"],
            "terrain.dt": payload["dt"],
        }
        first = WorldProcess(program)
        second = WorldProcess(program)
        first.step(inputs=externals)
        second.step(inputs=externals)
        for key in kernel.STATE_KEYS:
            slot = f"terrain.{key}"
            assert first.committed(slot) == second.committed(slot)
