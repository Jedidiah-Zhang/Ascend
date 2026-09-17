"""内核对拍测试 — 参考实现与加速实现逐位一致（世界基座 13 篇第三步）。

覆盖：
- 生产内核对存在且可调用；
- 地形演化内核：C（``state_evolve_arrays``）与 Python 参考在随机输入、
  边界输入、多云/冻结/衰减组合、多步长下**逐位一致**；
- 世界程序绑定：加速位/内核版本进身份；
- 内核对声明 fail-closed（锚点未知/重复/缺实现）。
"""

from array import array
import random

import pytest

from ascend.causal.kernels import KernelPair, default_kernel_pairs
from ascend.causal.program import compile_default_program, compile_world_program
from ascend.causal.world import ASCEND_MECHANISMS
from ascend.space.state_reference import state_evolve_reference
from ascend.space.tile_grid import TileGrid
from ascend.space.tile_state import state_evolve, state_evolve_arrays

from tests.unit.test_program import TICKS
from ascend.causal.fate_registry import load_fate_namespaces
from ascend.causal.state_schema import load_declaration
from ascend.causal.update_points import load_update_points


def _random_inputs(rng: random.Random, n: int, n_steps: int):
    states = {
        key: array("B", [rng.randrange(0, 256) for _ in range(n)])
        for key in ("moisture", "snow", "ice")
    }
    terrain = array("H", [rng.randrange(0, 256) for _ in range(n)])
    slope = array("f", [round(rng.uniform(0.0, 1.5), 4) for _ in range(n)])
    precip = [
        [round(rng.uniform(0.0, 60.0), 4) for _ in range(n_steps)]
        for _ in range(3)
    ]
    temp = [round(rng.uniform(-45.0, 45.0), 4) for _ in range(n_steps)]
    return states, terrain, slope, precip, temp


class TestTerrainKernelPair:
    def test_default_pairs_exist(self):
        pairs = default_kernel_pairs()
        assert [pair.anchor for pair in pairs] == ["terrain.integrate"]
        pair = pairs[0]
        assert pair.version
        assert callable(pair.reference)
        assert callable(pair.accelerated)

    @pytest.mark.parametrize("seed", [1, 7, 42, 20260916])
    def test_bit_identical_random(self, seed):
        rng = random.Random(seed)
        n, n_steps = 96, rng.randrange(1, 4)
        states, terrain, slope, precip, temp = _random_inputs(
            rng, n, n_steps,
        )
        reference = {key: array("B", arr) for key, arr in states.items()}
        accelerated = {key: array("B", arr) for key, arr in states.items()}
        state_evolve_arrays(
            accelerated, terrain, slope,
            precip=precip, temp=temp, dt=1.0 / 24.0,
        )
        state_evolve_reference(
            reference, terrain, slope,
            precip=precip, temp=temp, dt=1.0 / 24.0,
        )
        for key in states:
            assert list(reference[key]) == list(accelerated[key]), key

    @pytest.mark.parametrize("cover_mode", ["none", "cover"])
    def test_bit_identical_cover(self, cover_mode):
        rng = random.Random(99)
        n, n_steps = 64, 2
        states, terrain, slope, precip, temp = _random_inputs(
            rng, n, n_steps,
        )
        cover = (
            [round(rng.uniform(0.2, 1.0), 4) for _ in range(n)]
            if cover_mode == "cover" else None
        )
        reference = {key: array("B", arr) for key, arr in states.items()}
        accelerated = {key: array("B", arr) for key, arr in states.items()}
        state_evolve_arrays(
            accelerated, terrain, slope, precip=precip, temp=temp,
            dt=1.0 / 24.0, tile_cover=cover,
        )
        state_evolve_reference(
            reference, terrain, slope, precip=precip, temp=temp,
            dt=1.0 / 24.0, tile_cover=cover,
        )
        for key in states:
            assert list(reference[key]) == list(accelerated[key]), key

    def test_bit_identical_on_real_grid(self):
        rng = random.Random(2026)
        grid = TileGrid()
        for key in ("moisture", "snow", "ice"):
            arr = grid.state_raw(key)
            for i in range(0, len(arr), 97):
                arr[i] = rng.randrange(0, 256)
        for i in range(0, 40000, 311):
            grid.set_slope(i % 200, i // 200, rng.uniform(0.0, 1.0))
            grid.set(i % 200, i // 200, rng.randrange(0, 12))
        precip = [[3.5, 0.0], [0.0, 12.0], [0.0, 0.0]]
        temp = [-20.0, 30.0]
        reference_grid = TileGrid.from_bytes(grid.to_bytes())
        state_evolve(grid, precip=precip, temp=temp, dt=1.0 / 24.0)
        state_evolve_reference(
            {
                key: reference_grid.state_raw(key)
                for key in ("moisture", "snow", "ice")
            },
            reference_grid.raw_data(),
            reference_grid.slope_raw(),
            precip=precip, temp=temp, dt=1.0 / 24.0,
        )
        for key in ("moisture", "snow", "ice"):
            assert bytes(grid.state_raw(key)) == \
                bytes(reference_grid.state_raw(key)), key

    def test_shape_mismatch_rejected(self):
        states = {key: array("B", [0] * 8) for key in ("moisture", "snow", "ice")}
        with pytest.raises(ValueError, match="precip 形状"):
            state_evolve_reference(
                states, array("H", [0] * 8), array("f", [0.0] * 8),
                precip=[[0.0, 0.0], [0.0], [0.0]], temp=[0.0, 0.0],
            )


class TestProgramBinding:
    def test_terrain_point_kernel_bound(self):
        program = compile_default_program()
        binding = program.point_kernel_for("terrain.integrate")
        assert binding.version == "state.kernel.v1"
        assert binding.accelerated is not None
        assert "state_evolve_reference" in binding.reference_name
        assert "state_evolve_arrays" in binding.accelerated_name
        assert program.settings()["kernels_digest"].startswith("sha256:")

    def test_kernel_version_changes_identity(self):
        from ascend.causal.kernels import default_kernel_pairs

        pair = default_kernel_pairs()[0]
        changed = KernelPair(
            anchor=pair.anchor, version="state.kernel.v2",
            reference=pair.reference, accelerated=pair.accelerated,
            note=pair.note,
        )
        first = compile_default_program()
        second = compile_world_program(
            ASCEND_MECHANISMS,
            state=load_declaration(),
            addresses=load_fate_namespaces(),
            points=load_update_points(),
            ticks=dict(TICKS),
            kernels=(changed,),
        )
        assert first.identity != second.identity

    def test_unknown_anchor_rejected(self):
        pair = default_kernel_pairs()[0]
        bad = KernelPair(
            anchor="no.such.anchor", version="v1",
            reference=pair.reference, accelerated=pair.accelerated,
            note="",
        )
        with pytest.raises(ValueError, match="锚点未声明"):
            compile_world_program(
                ASCEND_MECHANISMS,
                state=load_declaration(),
                addresses=load_fate_namespaces(),
                points=load_update_points(),
                ticks=dict(TICKS),
                kernels=(bad,),
            )

    def test_duplicate_anchor_rejected(self):
        pair = default_kernel_pairs()[0]
        with pytest.raises(ValueError, match="重复锚点"):
            compile_world_program(
                ASCEND_MECHANISMS,
                state=load_declaration(),
                addresses=load_fate_namespaces(),
                points=load_update_points(),
                ticks=dict(TICKS),
                kernels=(pair, pair),
            )

    def test_missing_reference_rejected(self):
        pair = KernelPair(
            anchor="terrain.integrate", version="v1",
            reference=None, accelerated=None, note="",
        )
        with pytest.raises(ValueError, match="参考实现"):
            compile_world_program(
                ASCEND_MECHANISMS,
                state=load_declaration(),
                addresses=load_fate_namespaces(),
                points=load_update_points(),
                ticks=dict(TICKS),
                kernels=(pair,),
            )

    def test_mechanism_kernel_binding(self):
        spec = next(
            s for s in ASCEND_MECHANISMS.mechanisms.values()
            if s.output == "weather.tick.day"
        )
        pair = KernelPair(
            anchor="weather.tick.day", version="tick.day.v1",
            reference=spec.function, accelerated=lambda **kwargs: 1,
            note="测试用",
        )
        program = compile_world_program(
            ASCEND_MECHANISMS,
            state=load_declaration(),
            addresses=load_fate_namespaces(),
            points=load_update_points(),
            ticks=dict(TICKS),
            kernels=(pair,),
        )
        kernel = program.kernel_for("weather.tick.day")
        assert kernel.kernel_version == "tick.day.v1"
        assert kernel.accelerated is not None

    def test_mechanism_kernel_reference_mismatch_rejected(self):
        """机制输出锚点声明了与注册表不同的参考实现 → 编译期拒绝（陷阱）。"""
        pair = KernelPair(
            anchor="weather.tick.day", version="tick.day.v1",
            reference=lambda **kwargs: 1, accelerated=None,
            note="测试用",
        )
        with pytest.raises(ValueError, match="参考实现与注册表不一致"):
            compile_world_program(
                ASCEND_MECHANISMS,
                state=load_declaration(),
                addresses=load_fate_namespaces(),
                points=load_update_points(),
                ticks=dict(TICKS),
                kernels=(pair,),
            )
