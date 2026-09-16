"""地形状态内核测试 — C 对拍 + 行为。

Coverage: tile_state.py 的 state_evolve（C 内核数值等价性）。
TileStateEngine（单一积分器/对账出口）测试在 test_tile_state_engine.py。
"""

import struct

import pytest

from ascend.config import GAME_DAY, GAME_HOUR
from ascend.space.state_defs import STATE_TYPES, state_keys
from ascend.space.tile_grid import TileGrid
from ascend.space.tile_state import state_evolve
from ascend.space.terrain import TERRAIN_DEFS, TerrainType


# ── 纯 Python 参考内核（与 _state.c 公式逐项对应，对拍基准） ──

def _param_tables():
    """与 tile_state._build_param_tables 相同的表（白盒对拍）。"""
    keys = state_keys()
    n = len(keys)
    deposit = [0.0] * (n * 256)
    drain = [0.0] * (n * 256)
    melt = [0.0] * (n * 256)
    freeze = [0.0] * (n * 256)
    for si, key in enumerate(keys):
        for name, defn in TERRAIN_DEFS.items():
            p = defn.states[key]
            if p is None:
                continue
            base = si * 256 + defn.value
            deposit[base] = p.deposit
            drain[base] = p.drain
            melt[base] = p.melt
            freeze[base] = p.freeze
    freeze_below = [
        cfg.freeze_below if cfg.freeze_below is not None else -99999.0
        for cfg in STATE_TYPES.values()
    ]
    melt_above = [cfg.melt_above or 0.0 for cfg in STATE_TYPES.values()]
    state_max = [float(cfg.bounds[1]) for cfg in STATE_TYPES.values()]
    return deposit, drain, melt, freeze, freeze_below, melt_above, state_max


_DEP, _DRN, _MLT, _FRZ, _FBL, _MAB, _SMAX = _param_tables()


def ref_evolve(grid: TileGrid, precip, temp, dt=1.0, tile_cover=None) -> None:
    """参考实现：逐 tile 逐步，公式与 _state.c 一致（含舍入语义）。"""
    keys = state_keys()
    terrain = grid.raw_data()
    slope = grid.slope_raw()
    n_steps = len(temp)
    for s, key in enumerate(keys):
        arr = grid.state_raw(key)
        fb = _FBL[s]
        ma = _MAB[s]
        hi = _SMAX[s]
        for k in range(n_steps):
            prec = precip[s][k]
            t_k = temp[k]
            melt_k = t_k - ma if t_k > ma else 0.0
            freez_k = fb - t_k if fb > -9000.0 and t_k < fb else 0.0
            for i in range(len(arr)):
                v = float(arr[i])
                ti = int(terrain[i])
                cover = 1.0 if tile_cover is None else tile_cover[i]
                delta = (
                    prec * _DEP[s * 256 + ti] * cover
                    + freez_k * _FRZ[s * 256 + ti]
                    - v * (_MLT[s * 256 + ti] * melt_k
                           + _DRN[s * 256 + ti] * (1.0 + slope[i]))
                )
                v2 = v + delta * dt
                v2 = 0.0 if v2 < 0.0 else (hi if v2 > hi else v2)
                arr[i] = int(v2 + 0.5)


def _make_grid(n: int = 200, terrain_fill=None) -> TileGrid:
    """构造 n×n 手工网格（TileGrid 固定 200×200；默认全 GRASSLAND）。"""
    grid = TileGrid(
        data=[int(terrain_fill or TerrainType.GRASSLAND)] * (n * n),
        elevation=[0.0] * (n * n),
        slope=[0.0] * (n * n),
    )
    for i in range(n * n):
        if terrain_fill is None:
            t = [TerrainType.GRASSLAND, TerrainType.ROCK,
                 TerrainType.WATER, TerrainType.MARSH][i % 4]
            grid.raw_data()[i] = int(t)
        grid.slope_raw()[i] = (i % 7) * 0.1
    return grid


def _run_ref_and_c(grid, precip, temp, dt=1.0, cover=None):
    c_grid = grid.to_bytes()
    g2 = TileGrid.from_bytes(c_grid)
    ref_evolve(grid, precip, temp, dt, cover)
    state_evolve(g2, precip=precip, temp=temp, dt=dt, tile_cover=cover)
    return grid, g2


# ── C 与 Python 参考内核对拍 ─────────────────────────────

class TestKernelParity:
    """C 内核与参考实现逐位一致（确定性 + 数值等价）。"""

    def test_simple_rain_deposit(self):
        g, g2 = _run_ref_and_c(
            _make_grid(),
            [[10.0] * 3, [0.0] * 3, [0.0] * 3],
            [20.0] * 3,
        )
        assert g2.to_bytes() == g.to_bytes()

    def test_snow_accumulate_melt_cycle(self):
        g, g2 = _run_ref_and_c(
            _make_grid(),
            [[0.0] * 5, [15.0] * 3 + [0.0] * 2, [0.0] * 5],
            [-5.0, -5.0, -5.0, 5.0, 10.0],
        )
        assert g2.to_bytes() == g.to_bytes()

    def test_ice_freeze_thaw(self):
        g, g2 = _run_ref_and_c(
            _make_grid(),
            [[0.0] * 4, [0.0] * 4, [0.0] * 4],
            [-10.0, -10.0, -5.0, 5.0],
        )
        assert g2.to_bytes() == g.to_bytes()

    def test_random_days_parity(self):
        import random
        rng = random.Random(1234)
        grid = _make_grid()
        for i in range(len(grid.raw_data())):
            grid.raw_data()[i] = rng.randrange(0, 9)
            grid.slope_raw()[i] = rng.random() * 2.0
        for key in state_keys():
            raw = grid.state_raw(key)
            for i in range(0, len(raw), 3):
                raw[i] = rng.randrange(0, 60)
        n = 25
        precip = [
            [rng.choice([0.0, 5.0, 20.0, 40.0]) for _ in range(n)]
            for _ in range(3)
        ]
        temp = [rng.uniform(-15.0, 30.0) for _ in range(n)]
        g, g2 = _run_ref_and_c(grid, precip, temp)
        assert g2.to_bytes() == g.to_bytes()

    def test_pulse_dt_parity(self):
        """运行期脉冲（dt=1/24）与参考一致。"""
        g, g2 = _run_ref_and_c(
            _make_grid(),
            [[2.0], [0.0], [0.0]],
            [15.0],
            dt=1 / 24,
        )
        assert g2.to_bytes() == g.to_bytes()

    def test_cover_parity(self):
        cover = [1.0] * 40000
        cover[0] = 0.0
        cover[1] = 0.3
        g, g2 = _run_ref_and_c(
            _make_grid(),
            [[30.0], [0.0], [0.0]],
            [10.0],
            cover=cover,
        )
        assert g2.to_bytes() == g.to_bytes()


# ── 内核行为（物理语义冒烟，非对拍） ──────────────────────

class TestKernelBehavior:
    def test_no_weather_no_change(self):
        """无降水无冻结：全零状态保持零；已有状态按衰减项演化。"""
        grid = _make_grid()
        state_evolve(grid, precip=[[0.0]] * 3, temp=[20.0])
        for key in state_keys():
            assert all(v == 0 for v in grid.state_raw(key))

    def test_snow_deposit_proportional(self):
        """雪沉积 ∝ 降水 × deposit（1mm 雪 → 1cm）。"""
        grid = _make_grid()
        state_evolve(grid, precip=[[0.0], [10.0], [0.0]], temp=[-5.0])
        raw = grid.state_raw("snow")
        assert raw[0] == 10  # grassland：10mm × deposit 1.0
        assert raw[64] == 10  # shallow_water：同样沉积

    def test_snow_melt_warm(self):
        """正温融化：衰减 = state × melt × T（触底 clamp 到 0）。"""
        grid = _make_grid()
        for i in range(64):
            grid.set_state("snow", i % 8, i // 8, 50)
        state_evolve(grid, precip=[[0.0]] * 3, temp=[10.0])
        raw = grid.state_raw("snow")
        # 50 − 50×0.15×10 = −25 → clamp 0
        assert raw[0] == 0
        assert all(0 <= v <= 255 for v in raw)

    def test_moisture_soil_only(self):
        """湿润仅土壤类（GRASSLAND/MARSH）；岩石与水面恒 0。"""
        grid = _make_grid()
        grid.raw_data()[0] = int(TerrainType.GRASSLAND)
        grid.raw_data()[1] = int(TerrainType.ROCK)
        grid.raw_data()[2] = int(TerrainType.WATER)
        state_evolve(grid, precip=[[50.0], [0.0], [0.0]], temp=[15.0])
        raw = grid.state_raw("moisture")
        assert raw[0] > 0
        assert raw[1] == 0
        assert raw[2] == 0

    def test_ice_only_water(self):
        """结冰仅水面 tile 非零。"""
        grid = _make_grid()
        state_evolve(grid, precip=[[0.0]] * 3, temp=[-10.0])
        raw = grid.state_raw("ice")
        # 每 4 格一个 WATER（i%4==2）
        for i in range(64):
            if i % 4 == 2:
                assert raw[i] > 0, f"水面 tile {i} 应结冰"
            else:
                assert raw[i] == 0, f"陆地 tile {i} 不应结冰"

    def test_clamp_bounds(self):
        """状态 clamp 到注册表 bounds（moisture ≤ 100）。"""
        grid = _make_grid()
        state_evolve(grid, precip=[[200.0] * 5, [0.0] * 5, [0.0] * 5], temp=[-10.0] * 5)
        raw = grid.state_raw("moisture")
        assert all(0 <= v <= 100 for v in raw)
        assert raw[0] == 100  # 巨量降水顶格

    def test_cover_zero_no_deposit(self):
        """全遮蔽（建筑）：无沉积。"""
        grid = _make_grid()
        cover = [0.0] * 40000
        state_evolve(
            grid, precip=[[30.0], [0.0], [0.0]], temp=[10.0], tile_cover=cover,
        )
        assert all(v == 0 for v in grid.state_raw("moisture"))

    def test_drain_slope_dependency(self):
        """排水与坡度正相关：斜坡 tile 湿润衰减更快。"""
        grid = _make_grid()
        for i in range(64):
            grid.set_state("moisture", i % 8, i // 8, 80)
        grid.raw_data()[0] = int(TerrainType.GRASSLAND)
        grid.raw_data()[1] = int(TerrainType.GRASSLAND)
        grid.slope_raw()[0] = 0.0
        grid.slope_raw()[1] = 5.0
        state_evolve(grid, precip=[[0.0]] * 3, temp=[0.0])  # 无蒸发（T=0），仅排水
        raw = grid.state_raw("moisture")
        assert raw[0] == 80 - int(80 * 0.10 * 1 + 0.5)  # 平地日排 10%
        assert raw[1] == 80 - int(80 * 0.10 * 6 + 0.5)  # 陡坡日排 60%
        assert raw[0] > raw[1]

    def test_shape_mismatch_raises(self):
        """precip/temp 形状错误抛 ValueError（空步=空操作不报错）。"""
        state_evolve(_make_grid(), precip=[[1.0]], temp=[])  # 空操作
        with pytest.raises(ValueError):
            state_evolve(_make_grid(), precip=[[1.0], [1.0]], temp=[1.0])
        with pytest.raises(ValueError):
            state_evolve(
                _make_grid(), precip=[[1.0]] * 3, temp=[1.0], tile_cover=[1.0],
            )
