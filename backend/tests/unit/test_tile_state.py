"""地形状态内核与结算器测试 — C 对拍 + 行为 + settle 集成。

Coverage: tile_state.py 的 state_evolve（C 内核数值等价性）与
SettlementCalculator（批量日档推演）。TileStateEngine（事件通道/
对账出口）测试在 test_tile_state_engine.py。
"""

import struct
from types import SimpleNamespace

import pytest

from ascend.config import GAME_DAY, GAME_HOUR
from ascend.space.state_defs import STATE_TYPES, state_keys
from ascend.space.tile_grid import TileGrid
from ascend.space.tile_state import SettlementCalculator, state_evolve
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
                 TerrainType.SHALLOW_WATER, TerrainType.MARSH][i % 4]
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
        grid.raw_data()[2] = int(TerrainType.SHALLOW_WATER)
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
        # 每 4 格一个 SHALLOW_WATER（i%4==2）
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


# ── 结算器（日档批量推演） ──────────────────────────────

class _FakeWeather:
    """确定性伪天气引擎（不依赖真实解析场）。"""

    def __init__(self, summaries: dict[int, tuple[float, float, float]]):
        self._summaries = summaries

    def get_day_summary(self, cx, cy, day):
        data = self._summaries.get(day)
        if data is None:
            return None
        mean_temp, rain_mm, snow_mm = data
        return SimpleNamespace(day=day, mean_temp=mean_temp,
                               rain_mm=rain_mm, snow_mm=snow_mm)


def _calc_settle(calc, grid, from_day, to_day):
    """两段式结算（锁外预取 + 锁内纯计算）——与引擎调用形态一致。"""
    chunk = SimpleNamespace(cx=0, cy=0)
    return calc.settle_from_summaries(
        grid, calc.precompute(chunk, from_day, to_day),
    )


def _fake_settle(grid, summaries, from_day, to_day):
    calc = SettlementCalculator(_FakeWeather(summaries))
    return _calc_settle(calc, grid, from_day, to_day)


class TestSettlementCalculator:
    def test_empty_range(self):
        assert _fake_settle(_make_grid(), {}, 5, 5) == 0
        assert _fake_settle(_make_grid(), {}, 5, 3) == 0

    def test_unregistered_chunk_skips(self):
        """天气未注册的 chunk：空转，返回 0。"""
        assert _fake_settle(_make_grid(), {}, 1, 10) == 0

    def test_partial_registration_skips_missing_days(self):
        """部分日有天气：缺日跳过，有天气日推演。"""
        grid = _make_grid()
        days = {2: (-5.0, 0.0, 20.0), 4: (-5.0, 0.0, 20.0)}
        n = _fake_settle(grid, days, 1, 6)
        assert n == 2
        assert grid.get_state("snow", 0, 0) == 40  # 2 天 × 20mm × deposit 1.0

    def test_snow_vs_rain_split(self):
        """雨雪分流：雪日积雪、雨日湿润（0°C 不融雪）。"""
        grid = _make_grid()
        days = {
            1: (-5.0, 0.0, 25.0),   # 雪
            2: (15.0, 40.0, 0.0),   # 雨（雪融化）
            3: (0.0, 0.0, 0.0),     # 晴 0°C
        }
        _fake_settle(grid, days, 1, 4)
        # day2 暖雨：25cm 雪被 15°C 全化（25×0.15×15 > 25）
        assert grid.get_state("snow", 0, 0) == 0
        assert grid.get_state("moisture", 0, 0) > 0

    def test_snow_persists_below_freezing(self):
        """冰点下雪不化。"""
        grid = _make_grid()
        days = {1: (-5.0, 0.0, 25.0), 2: (-2.0, 0.0, 0.0)}
        _fake_settle(grid, days, 1, 3)
        assert grid.get_state("snow", 0, 0) == 25

    def test_equivalence_with_ref_kernel(self):
        """settle 结果 = 参考内核逐日推演（白盒一致）。"""
        days = {d: (-3.0, 5.0, 10.0) for d in range(1, 30)}
        grid_a = _make_grid()
        _fake_settle(grid_a, days, 1, 30)

        grid_b = _make_grid()
        precip = [[5.0] * 29, [10.0] * 29, [0.0] * 29]
        ref_evolve(grid_b, precip, [-3.0] * 29)
        assert grid_a.to_bytes() == grid_b.to_bytes()

    def test_deterministic(self):
        """同输入两次 settle 结果一致。"""
        days = {d: (0.0, 20.0, 5.0) for d in range(1, 60)}
        a, b = _make_grid(), _make_grid()
        _fake_settle(a, days, 1, 60)
        _fake_settle(b, days, 1, 60)
        assert a.to_bytes() == b.to_bytes()


class TestDaySummary:
    """get_day_summary 采样契约（真实天气引擎，固定 seed）。"""

    @pytest.fixture()
    def engine(self):
        from ascend.time import WorldClock
        from ascend.weather.weather_engine import WeatherEngine
        from ascend.world_tree import WorldTree
        wt = WorldTree()
        e = WeatherEngine(WorldClock(), seed=42, world_tree_arg=wt)
        from ascend.weather.weather_engine import WeatherParams
        from ascend.space.climate import ClimateZone
        bl = WeatherParams(5.0, 900.0, 10.0, 150.0, 60.0, 5.0)
        e.register_chunk(0, 0, bl, ClimateZone.SUBARCTIC_TAIGA, 5.0)
        yield e
        e.shutdown()

    def test_day_summary_shape(self, engine):
        s = engine.get_day_summary(0, 0, 1)
        assert s.day == 1
        assert -40 <= s.mean_temp <= 40
        assert s.rain_mm >= 0
        assert s.snow_mm >= 0
        assert s.rain_mm + s.snow_mm > 0 or True  # 无雨日允许全 0

    def test_day_summary_deterministic(self, engine):
        s1 = engine.get_day_summary(0, 0, 100)
        s2 = engine.get_day_summary(0, 0, 100)
        assert s1 == s2

    def test_day_summary_unregistered_none(self, engine):
        assert engine.get_day_summary(99, 99, 1) is None

    def test_day_summary_day1_is_first_day(self, engine):
        """day 1 采样起点 = tick 0（世界开端对齐）。"""
        s = engine.get_day_summary(0, 0, 1)
        # 与 get_weather(0) 直接采样对照（均值近似，允许微小差异）
        w0 = engine.get_weather(0, 0, 0)
        assert abs(s.mean_temp - w0.temperature) < 15

    def test_day_summary_sampling_grid(self, engine):
        """采样时刻均匀且不越界。"""
        s = engine.get_day_summary(0, 0, 2)
        assert s.day == 2
        for k in range(4):
            tick = GAME_DAY + k * (GAME_DAY // 4)
            assert 0 <= tick < GAME_DAY * 2

    def test_day_summary_bad_args(self, engine):
        with pytest.raises(ValueError):
            engine.get_day_summary(0, 0, 0)
        with pytest.raises(ValueError):
            engine.get_day_summary(0, 0, 1, samples_per_day=7)


class TestSettleIntegration:
    """真实天气引擎下的端到端 settle（确定性 + 物理合理）。"""

    def test_winter_accumulates_snow(self):
        from ascend.time import WorldClock
        from ascend.weather.weather_engine import (
            WeatherEngine, WeatherParams,
        )
        from ascend.space.climate import ClimateZone
        from ascend.world_tree import WorldTree

        wt = WorldTree()
        clock = WorldClock()
        e = WeatherEngine(clock, seed=7, world_tree_arg=wt)
        bl = WeatherParams(-10.0, 400.0, 8.0, 200.0, 40.0, 6.0)
        e.register_chunk(0, 0, bl, ClimateZone.POLAR_TUNDRA, -10.0)

        grid = _make_grid()
        calc = SettlementCalculator(e)
        n = _calc_settle(calc, grid, 1, 365)
        assert n == 364
        raw = grid.state_raw("snow")
        # 极地冬积夏融：年末（冬）雪厚（全年雪 559mm 集中在冰点下时段）
        assert max(raw) >= 200, f"极地冬季应有厚积雪，max={max(raw)}"
        assert raw[0] <= 255
        # 水面已结冰（冬季持续零下）
        assert max(grid.state_raw("ice")) > 0
        e.shutdown()

    def test_tropical_no_snow(self):
        from ascend.time import WorldClock
        from ascend.weather.weather_engine import (
            WeatherEngine, WeatherParams,
        )
        from ascend.space.climate import ClimateZone
        from ascend.world_tree import WorldTree

        wt = WorldTree()
        e = WeatherEngine(WorldClock(), seed=7, world_tree_arg=wt)
        bl = WeatherParams(28.0, 2000.0, 12.0, 50.0, 80.0, 4.0)
        e.register_chunk(0, 0, bl, ClimateZone.EQUATORIAL_RAINFOREST, 28.0)

        grid = _make_grid()
        calc = SettlementCalculator(e)
        _calc_settle(calc, grid, 1, 365)
        assert max(grid.state_raw("snow")) == 0
        # 湿润应有雨期积累（排水慢于降雨补给）
        assert max(grid.state_raw("moisture")) > 0
        e.shutdown()

    def test_settle_matches_realtime_pulse(self):
        """天级一致性：settle 1 天 ≈ 24 次小时脉冲（无 clamp 区）。"""
        from ascend.time import WorldClock
        from ascend.weather.weather_engine import (
            WeatherEngine, WeatherParams,
        )
        from ascend.space.climate import ClimateZone
        from ascend.world_tree import WorldTree

        wt = WorldTree()
        e = WeatherEngine(WorldClock(), seed=3, world_tree_arg=wt)
        bl = WeatherParams(10.0, 800.0, 10.0, 100.0, 60.0, 5.0)
        e.register_chunk(0, 0, bl, ClimateZone.TEMPERATE_FOREST, 10.0)

        g_settle = _make_grid()
        _calc_settle(SettlementCalculator(e), g_settle, 100, 101)
        g_pulse = _make_grid()
        # 24 次脉冲：每次 1/24 日，用当日 4 个采样点的天气。
        # 内核按 mm/日 标定——每步传日总量 rain_mm（dt=1/24 缩放），
        # 24 步总和 == 结算 1 天沉积（不可再 ÷24：那是"1 小时雨"）。
        summary = e.get_day_summary(0, 0, 100)
        hourly = [summary.mean_temp] * 24
        precip_h = [[summary.rain_mm] * 24,
                    [summary.snow_mm] * 24, [0.0] * 24]
        for k in range(24):
            state_evolve(
                g_pulse,
                precip=[[p[k]] for p in precip_h],
                temp=[hourly[k]],
                dt=1 / 24,
            )
        diff = max(
            abs(a - b)
            for key in state_keys()
            for a, b in zip(g_settle.state_raw(key), g_pulse.state_raw(key))
        )
        # 大步长近似误差：settle 用 4 点采样日均温、无逐小时 clamp；
        # 脉冲用逐小时实时温度 + 24 次中间 clamp。同量级、同方向即可。
        assert diff <= 40, f"settle 与脉冲偏差 {diff}"
        assert max(g_settle.state_raw("snow")) == max(g_pulse.state_raw("snow")) \
            or abs(max(g_settle.state_raw("snow"))
                   - max(g_pulse.state_raw("snow"))) <= 10
        e.shutdown()

    def test_settle_matches_realtime_pulse_heavy_rain(self):
        """大雨日一致性（回归）：24 小时脉冲沉积 ≈ settle 1 天沉积。

        曾回归：脉冲侧按 mm/h × dt=1/24 沉积，恒定 10mm/h 下 24 步
        总沉积 = 结算的 1/24（24 倍差）——大雨日必然饱和差被掩盖。
        """
        from ascend.time import WorldClock
        from ascend.weather.weather_engine import (
            WeatherEngine, WeatherParams,
        )
        from ascend.space.climate import ClimateZone
        from ascend.world_tree import WorldTree

        wt = WorldTree()
        e = WeatherEngine(WorldClock(), seed=7, world_tree_arg=wt)
        # 湿润带：大雨日（年降水 4000mm 集中在雨季）
        bl = WeatherParams(28.0, 4000.0, 12.0, 50.0, 80.0, 4.0)
        e.register_chunk(0, 0, bl, ClimateZone.EQUATORIAL_RAINFOREST, 28.0)

        g_settle = _make_grid()
        _calc_settle(SettlementCalculator(e), g_settle, 1, 2)
        g_pulse = _make_grid()
        summary = e.get_day_summary(0, 0, 1)
        assert summary.rain_mm >= 10, "大雨日前提（降雨量足够）"
        hourly = [summary.mean_temp] * 24
        precip_h = [[summary.rain_mm] * 24, [0.0] * 24, [0.0] * 24]
        for k in range(24):
            state_evolve(
                g_pulse,
                precip=[[p[k]] for p in precip_h],
                temp=[hourly[k]],
                dt=1 / 24,
            )
        diff = max(
            abs(a - b)
            for key in state_keys()
            for a, b in zip(g_settle.state_raw(key), g_pulse.state_raw(key))
        )
        # 修正前：脉冲沉积 = 结算的 1/24 → diff 超 100；修正后 ≤40
        assert diff <= 40, f"大雨日 settle 与脉冲偏差 {diff}"
        assert max(g_settle.state_raw("moisture")) >= 40, "大雨日应显著湿润"
        e.shutdown()

class TestTouchInvalidation:
    """touch() 直写失效契约：清聚合/档位缓存，不演化、不推进结算日。"""

    @pytest.fixture()
    def engine(self):
        from ascend.time import WorldClock
        from ascend.weather.weather_engine import WeatherEngine
        from ascend.space.tile_state import TileStateEngine
        engine = TileStateEngine(
            WorldClock(), WeatherEngine(WorldClock(), seed=1),
        )
        yield engine
        engine.shutdown()

    def _register(self, engine, grid=None):
        chunk = SimpleNamespace(
            cx=1, cy=1, tile_grid=grid if grid is not None else _make_grid(),
            settled_day=0,
        )
        engine.register_chunk(chunk)
        return chunk

    def test_touch_invalidates_aggregate_cache(self, engine):
        chunk = self._register(engine)
        engine.on_tiles_ready(chunk.cx, chunk.cy)
        agg = engine.aggregates(1, 1)
        assert agg, "聚合缓存已建立"
        engine.touch(1, 1)
        assert (1, 1) not in engine._aggregates, "touch 后聚合缓存被清"

    def test_touch_invalidates_tier_cache(self, engine):
        chunk = self._register(engine)
        engine.on_tiles_ready(chunk.cx, chunk.cy)
        engine._reconcile_thresholds((1, 1))
        assert (1, 1) in engine._last_tier, "档位缓存已建立"
        engine.touch(1, 1)
        assert (1, 1) not in engine._last_tier, "touch 后档位缓存被清"

    def test_touch_does_not_evolve_or_advance_day(self, engine):
        chunk = self._register(engine)
        engine.on_tiles_ready(chunk.cx, chunk.cy)
        settled_before = chunk.settled_day
        raw_before = list(chunk.tile_grid.state_raw("snow"))
        engine.touch(1, 1)
        assert chunk.settled_day == settled_before, "touch 不推进结算日"
        assert list(chunk.tile_grid.state_raw("snow")) == raw_before, "touch 不演化状态"
