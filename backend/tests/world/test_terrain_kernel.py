"""地形内核测试 — 参数表一致、黄金向量、内核对与模块帧。

- 参数表：``terrain.data.build_param_tables`` 与
  ``state_defs.build_param_tables`` 逐值一致；
- 黄金向量：参考实现与冻结向量逐位一致（``data/terrain_golden.json``）；
- 内核对：参考实现 vs C 加速逐位一致（随机 + 黄金输入）；
- 模块帧：编译并推进一帧，输出与冻结向量逐位一致。
"""

from __future__ import annotations

import json
import random
from pathlib import Path

import pytest

from ascend.space.state_defs import build_param_tables as space_param_tables
from ascend.world import LatticeField, Schedule, WorldProcess, WorldSpec, compile_world
from ascend.world.modules import terrain
from ascend.world.modules.terrain import kernel
from ascend.world.modules.terrain.core import TerrainCore
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
    def test_matches_space_loader(self):
        new = build_param_tables()
        old = space_param_tables()
        assert len(new) == len(old) == 7
        for index, (new_table, old_table) in enumerate(zip(new, old)):
            assert new_table == old_table, f"第 {index} 张表不一致"


class TestGoldenVectors:
    def test_reference_matches_golden(self):
        mismatches = []
        for vector in _GOLDEN["vectors"]:
            actual = _call(kernel.evolve_reference, vector["inputs"])
            if actual != vector["outputs"]:
                mismatches.append((vector["inputs"], vector["outputs"], actual))
        assert not mismatches, mismatches[:2]

    def test_accelerated_matches_golden(self):
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
    def test_module_frame_matches_golden(self):
        program = compile_world(
            WorldSpec(
                modules=(terrain.MODULE,),
                schedule=Schedule(periods=(("hour", 1),)),
            )
        )
        payload = _GOLDEN["module"]
        inputs = payload["inputs"]
        coords = (0, 0)
        process = WorldProcess(program)
        process.materialize("lattice.chunk", coords)
        for key, values in inputs["states"].items():
            field = LatticeField((len(values),), 0)
            for index, value in enumerate(values):
                field.set((index,), value)
            process.seed_at(f"terrain.{key}", coords, field)
        process.step(
            inputs={
                "terrain.terrain_id": {coords: inputs["terrain_id"]},
                "terrain.slope": {coords: inputs["slope"]},
                "terrain.cover": {coords: inputs["cover"]},
                "weather.precip_moisture": inputs["precip_moisture"],
                "weather.precip_snow": inputs["precip_snow"],
                "weather.step_temp": inputs["step_temp"],
                "terrain.dt": inputs["dt"],
            }
        )
        for slot, expected in payload["outputs"].items():
            actual = process.committed(slot).get(coords)
            assert actual.values() == tuple(expected)

    def test_module_reproducible(self):
        program = compile_world(
            WorldSpec(
                modules=(terrain.MODULE,),
                schedule=Schedule(periods=(("hour", 1),)),
            )
        )
        payload = _GOLDEN["module"]["inputs"]
        coords = (0, 0)
        inputs = {
            "terrain.terrain_id": {coords: payload["terrain_id"]},
            "terrain.slope": {coords: payload["slope"]},
            "terrain.cover": {coords: payload["cover"]},
            "weather.precip_moisture": payload["precip_moisture"],
            "weather.precip_snow": payload["precip_snow"],
            "weather.step_temp": payload["step_temp"],
            "terrain.dt": payload["dt"],
        }
        results = []
        for _ in range(2):
            process = WorldProcess(program)
            process.materialize("lattice.chunk", coords)
            for key, values in payload["states"].items():
                field = LatticeField((len(values),), 0)
                for index, value in enumerate(values):
                    field.set((index,), value)
                process.seed_at(f"terrain.{key}", coords, field)
            process.step(inputs=inputs)
            results.append(
                tuple(
                    process.committed(f"terrain.{key}").get(coords)
                    for key in kernel.STATE_KEYS
                )
            )
        assert results[0] == results[1]


class TestTerrainCoreAdapter:
    """声明式适配器与数组层入口（``state_evolve_arrays``）逐位一致。"""

    def _array_run(self, case: dict) -> dict[str, list[int]]:
        from array import array

        from ascend.space.tile_state import state_evolve_arrays

        states = {
            key: array("B", case["states"][key])
            for key in kernel.STATE_KEYS
        }
        state_evolve_arrays(
            states,
            array("H", case["terrain"]),
            array("f", case["slope"]),
            precip=case["precip"],
            temp=case["temp"],
            dt=case["dt"],
            tile_cover=case["cover"],
        )
        return {key: list(states[key]) for key in kernel.STATE_KEYS}

    def test_evolve_matches_array_path(self):
        core = TerrainCore()
        for _ in range(6):
            n = _RNG.choice([1, 5, 32])
            case = {
                "states": {
                    key: [_RNG.randint(0, 255) for _ in range(n)]
                    for key in kernel.STATE_KEYS
                },
                "terrain": [_RNG.randint(0, 7) for _ in range(n)],
                "slope": [round(_RNG.uniform(0.0, 1.0), 4) for _ in range(n)],
                "precip": [
                    [round(_RNG.uniform(0.0, 5.0), 4)],
                    [round(_RNG.uniform(0.0, 5.0), 4)],
                    [0.0],
                ],
                "temp": [round(_RNG.uniform(-25.0, 35.0), 4)],
                "dt": _RNG.choice([1.0, 1 / 24]),
                "cover": (
                    None
                    if _RNG.random() < 0.5
                    else [round(_RNG.uniform(0.0, 1.0), 4) for _ in range(n)]
                ),
            }
            expected = self._array_run(case)
            result = core.evolve(
                case["states"],
                case["terrain"],
                case["slope"],
                precip=case["precip"],
                temp=case["temp"],
                dt=case["dt"],
                cover=case["cover"],
            )
            actual = {
                key: list(result[key].values())
                for key in kernel.STATE_KEYS
            }
            assert actual == expected, case

    def test_evolve_into_multi_step(self):
        core = TerrainCore()
        n = 8
        case = {
            "states": {
                key: [_RNG.randint(0, 60) for _ in range(n)]
                for key in kernel.STATE_KEYS
            },
            "terrain": [_RNG.randint(0, 7) for _ in range(n)],
            "slope": [0.1] * n,
            "precip": [
                [1.0, 2.0],
                [0.5, 0.0],
                [0.0, 0.0],
            ],
            "temp": [-5.0, 10.0],
            "dt": 1 / 24,
            "cover": None,
        }
        expected = self._array_run(case)
        from array import array

        states = {
            key: array("B", case["states"][key])
            for key in kernel.STATE_KEYS
        }
        core.evolve_into(
            states,
            array("H", case["terrain"]),
            array("f", case["slope"]),
            precip=case["precip"],
            temp=case["temp"],
            dt=case["dt"],
            cover=case["cover"],
        )
        assert {key: list(states[key]) for key in kernel.STATE_KEYS} == expected
