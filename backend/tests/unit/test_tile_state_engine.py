"""地形状态引擎测试 — 三通道驱动 + 统一对账出口 + 阈值事件。

Coverage: TileStateEngine（生命周期/脉冲/降水涂抹/快进补结算/
阈值穿越/聚合）。天气用真实 WeatherEngine（固定 seed，确定性）。
"""

import pytest

from ascend.config import GAME_DAY
from ascend.space.state_defs import STATE_TYPES
from ascend.space.tile_grid import TileGrid
from ascend.space.tile_state import TileStateEngine
from ascend.space.terrain import TerrainType
from ascend.time import WorldClock
from ascend.weather.weather_engine import WeatherEngine, WeatherParams
from ascend.space.climate import ClimateZone
from ascend.world_tree import Event, WorldTree


def _make_grid(water: bool = False) -> TileGrid:
    """全 GRASSLAND 网格（可选含水面 tile）。"""
    t = int(TerrainType.SHALLOW_WATER) if water else int(TerrainType.GRASSLAND)
    return TileGrid(data=[t] * 40000)


def _chunk(cx=0, cy=0, grid=None):
    return type("ChunkStub", (), {"cx": cx, "cy": cy, "tile_grid": grid, "settled_day": 0})()


def _publish(wt: WorldTree, event_type: str, timestamp: int, data=None) -> None:
    wt.publish(Event(
        timestamp=timestamp,
        location=(0, 0, None, None),
        initiator_type="system",
        initiator_id="test",
        affected=[],
        event_type=event_type,
        data=data or {},
    ))


@pytest.fixture()
def env():
    """引擎 + 天气 + 时钟 + 世界树（seed 固定，确定性）。"""
    wt = WorldTree()
    clock = WorldClock()
    weather = WeatherEngine(clock, seed=42, world_tree_arg=wt)
    bl = WeatherParams(10.0, 800.0, 10.0, 100.0, 60.0, 5.0)
    weather.register_chunk(0, 0, bl, ClimateZone.TEMPERATE_FOREST, 10.0)
    engine = TileStateEngine(clock, weather, wt=wt)
    yield SimpleEnv(wt, clock, weather, engine)
    engine._wt = None  # 防残留订阅
    weather.shutdown()


class SimpleEnv:
    def __init__(self, wt, clock, weather, engine):
        self.wt = wt
        self.clock = clock
        self.weather = weather
        self.engine = engine

    def subscribe(self, event_type):
        events = []
        self.wt.subscribe(event_type, lambda e: events.append(e))
        return events


class TestLifecycle:
    def test_register_and_unregister(self, env):
        grid = _make_grid()
        env.engine.register_chunk(_chunk(grid=grid))
        assert env.engine.aggregates(0, 0)  # 已注册可查聚合
        env.engine.unregister_chunk(0, 0)
        assert env.engine.aggregates(0, 0) == {}

    def test_unregistered_safe(self, env):
        assert env.engine.aggregates(9, 9) == {}
        env.engine.on_tiles_ready(9, 9)  # 不抛
        env.engine.settle_gap(9, 9, 50)

    def test_register_sets_baseline_tier(self, env):
        """注册时初始档位 = 当前状态档位（不误报穿越）。"""
        grid = _make_grid()
        grid.state_raw("snow")[0] = 60  # 已是高档
        env.engine.register_chunk(_chunk(grid=grid))
        env.clock.skip(GAME_DAY)
        _publish(env.wt, "hour_change", env.clock.time)
        events = env.subscribe("state_threshold_crossed")
        assert len(events) == 0


class TestSettleOnReady:
    def test_on_tiles_ready_settles_history(self, env):
        """tile 就绪后从 day1 结算到现在（无事件、无副作用）。"""
        grid = _make_grid()
        env.engine.register_chunk(_chunk(grid=grid))
        env.clock.skip(120 * GAME_DAY)  # 玩家已玩到 day 121
        env.engine.on_tiles_ready(0, 0)
        assert env.engine.aggregates(0, 0)["mean_snow"] >= 0
        # 结算后数据直写生效（无叙事事件污染）
        events = env.subscribe("state_threshold_crossed")
        _publish(env.wt, "hour_change", env.clock.time)
        env.clock.skip(GAME_DAY)
        _publish(env.wt, "hour_change", env.clock.time)
        assert len(events) == 0 or all(
            e.event_type != "state_threshold_crossed" for e in events
        )

    def test_on_tiles_ready_idempotent(self, env):
        """重复 on_tiles_ready 不重复结算（后一次无变化）。"""
        grid = _make_grid()
        env.engine.register_chunk(_chunk(grid=grid))
        env.clock.skip(30 * GAME_DAY)
        env.engine.on_tiles_ready(0, 0)
        snap = grid.to_bytes()
        env.engine.on_tiles_ready(0, 0)
        assert grid.to_bytes() == snap

    def test_on_tiles_ready_day1_noop(self, env):
        """世界开端（day 1）就绪：空结算（无史前历史）。"""
        grid = _make_grid()
        env.engine.register_chunk(_chunk(grid=grid))
        snap = grid.to_bytes()
        env.engine.on_tiles_ready(0, 0)
        assert grid.to_bytes() == snap


class TestHourPulse:
    def test_hour_pulse_evolves_states(self, env):
        """小时脉冲按实时天气演化（沉积 + 衰减）。"""
        grid = _make_grid()
        env.engine.register_chunk(_chunk(grid=grid))
        env.clock.skip(GAME_DAY)
        for h in range(24):
            _publish(env.wt, "hour_change", env.clock.time + h * 7200)
        # 有雨时段 → 湿润沉积；无雨 → 排水衰减
        agg = env.engine.aggregates(0, 0)
        assert 0 <= agg["mean_moisture"] <= 100

    def test_hour_pulse_no_weather_chunk(self, env):
        """天气未注册的 chunk：脉冲空转不抛。"""
        grid = _make_grid()
        env.engine.register_chunk(_chunk(cx=7, cy=7, grid=grid))
        env.clock.skip(GAME_DAY)
        _publish(env.wt, "hour_change", env.clock.time)
        assert env.engine.aggregates(7, 7)


class TestPrecipChannel:
    def test_precip_start_paints_region(self, env):
        """降水开始 → 区域 chunk 即时沉积（不等脉冲）。"""
        grid = _make_grid()
        env.engine.register_chunk(_chunk(grid=grid))
        env.clock.skip(GAME_DAY)
        _publish(env.wt, "precipitation_start", env.clock.time, {
            "precip_type": "rain",
            "intensity": 40.0,
            "time_of_day": 6 * 7200,
            "chunks": ((0, 0), (1, 0), (0, 1)),
        })
        agg = env.engine.aggregates(0, 0)
        assert agg["mean_moisture"] > 0

    def test_precip_start_snow_type(self, env):
        """雪型降水 → snow 状态沉积。"""
        grid = _make_grid()
        env.engine.register_chunk(_chunk(grid=grid))
        env.clock.skip(GAME_DAY)
        _publish(env.wt, "precipitation_start", env.clock.time, {
            "precip_type": "snow",
            "intensity": 30.0,
            "time_of_day": 6 * 7200,
            "chunks": ((0, 0),),
        })
        agg = env.engine.aggregates(0, 0)
        assert agg["mean_snow"] > 0

    def test_precip_unregistered_chunk_ignored(self, env):
        """降水区域含未注册 chunk：忽略不抛。"""
        env.clock.skip(GAME_DAY)
        _publish(env.wt, "precipitation_start", env.clock.time, {
            "precip_type": "rain", "intensity": 40.0,
            "time_of_day": 0, "chunks": ((5, 5),),
        })

    def test_precip_stop_noop(self, env):
        """降水停止：无沉积动作（状态不受影响）。"""
        grid = _make_grid()
        env.engine.register_chunk(_chunk(grid=grid))
        snap = grid.to_bytes()
        _publish(env.wt, "precipitation_stop", env.clock.time, {
            "time_of_day": 0, "chunks": ((0, 0),),
        })
        assert grid.to_bytes() == snap


class TestSettleGap:
    def test_day_change_fast_forward_settles(self, env):
        """快进（skipped_days>0）→ 全部注册 chunk 补结算。"""
        grid = _make_grid()
        env.engine.register_chunk(_chunk(grid=grid))
        env.clock.skip(200 * GAME_DAY)
        _publish(env.wt, "day_change", env.clock.time, {
            "day": 201, "previous_day": 1, "skipped_days": 200,
            "elapsed_days": 200, "day_change_count": 1,
        })
        agg = env.engine.aggregates(0, 0)
        assert agg["mean_snow"] >= 0

    def test_day_change_no_skip_noop(self, env):
        """日常日变（skipped=0）：不结算（小时脉冲已推进）。"""
        grid = _make_grid()
        env.engine.register_chunk(_chunk(grid=grid))
        snap = grid.to_bytes()
        _publish(env.wt, "day_change", env.clock.time, {
            "day": 2, "previous_day": 1, "skipped_days": 0,
            "elapsed_days": 1, "day_change_count": 1,
        })
        assert grid.to_bytes() == snap

    def test_settle_gap_power_idempotent(self, env):
        """重复快进到同日：幂等（第二次无缺口）。"""
        grid = _make_grid()
        env.engine.register_chunk(_chunk(grid=grid))
        env.clock.skip(90 * GAME_DAY)
        _publish(env.wt, "day_change", env.clock.time, {
            "day": 91, "previous_day": 1, "skipped_days": 90,
            "elapsed_days": 90, "day_change_count": 1,
        })
        snap = grid.to_bytes()
        _publish(env.wt, "day_change", env.clock.time, {
            "day": 91, "previous_day": 91, "skipped_days": 0,
            "elapsed_days": 90, "day_change_count": 1,
        })
        assert grid.to_bytes() == snap


class TestThresholdEvents:
    def test_snow_up_crossing(self, env):
        """雪 max 升档 → up 事件（threshold=档位下界）。"""
        grid = _make_grid()
        env.engine.register_chunk(_chunk(grid=grid))
        env.clock.skip(GAME_DAY)
        events = env.subscribe("state_threshold_crossed")
        grid.state_raw("snow")[0] = 60  # 数据直写（模拟积雪）
        _publish(env.wt, "hour_change", env.clock.time)
        ups = [e for e in events if e.data["direction"] == "up"
               and e.data["state"] == "snow"]
        assert len(ups) == 1
        assert ups[0].data["threshold"] == 50
        assert ups[0].data["value"] == 60
        assert ups[0].data["cx"] == 0 and ups[0].data["cy"] == 0

    def test_snow_down_crossing(self, env):
        """雪 max 降档 → down 事件。"""
        grid = _make_grid()
        grid.state_raw("snow")[0] = 60
        env.engine.register_chunk(_chunk(grid=grid))
        env.clock.skip(GAME_DAY)
        events = env.subscribe("state_threshold_crossed")
        grid.state_raw("snow")[0] = 5  # 融化
        _publish(env.wt, "hour_change", env.clock.time)
        downs = [e for e in events if e.data["direction"] == "down"
                 and e.data["state"] == "snow"]
        assert len(downs) == 1
        assert downs[0].data["threshold"] == 15  # 跌破最低档

    def test_same_tier_no_event(self, env):
        """档内波动不发事件。"""
        grid = _make_grid()
        grid.state_raw("snow")[0] = 20  # tier 1 (15≤20<30)
        env.engine.register_chunk(_chunk(grid=grid))
        env.clock.skip(GAME_DAY)
        events = env.subscribe("state_threshold_crossed")
        grid.state_raw("snow")[0] = 25  # 仍在 tier 1
        _publish(env.wt, "hour_change", env.clock.time)
        assert len(events) == 0

    def test_multiple_tier_jump_single_event(self, env):
        """多档跳变只发一次（目标档位）。"""
        grid = _make_grid()
        env.engine.register_chunk(_chunk(grid=grid))
        env.clock.skip(GAME_DAY)
        events = env.subscribe("state_threshold_crossed")
        grid.state_raw("snow")[0] = 200  # 0 → tier 3（跨 15/30/50）
        _publish(env.wt, "hour_change", env.clock.time)
        ups = [e for e in events if e.data["direction"] == "up"]
        assert len(ups) == 1
        assert ups[0].data["threshold"] == 50

    def test_no_threshold_state_no_event(self, env):
        """无阈值的状态（moisture）不发阈值事件。"""
        grid = _make_grid()
        env.engine.register_chunk(_chunk(grid=grid))
        env.clock.skip(GAME_DAY)
        events = env.subscribe("state_threshold_crossed")
        grid.state_raw("moisture")[0] = 100
        _publish(env.wt, "hour_change", env.clock.time)
        assert len(events) == 0


class TestAggregates:
    def test_water_frozen_detection(self, env):
        """水面任一 tile 结冰 → water_frozen=True。"""
        grid = _make_grid(water=True)
        env.engine.register_chunk(_chunk(grid=grid))
        agg = env.engine.aggregates(0, 0)
        assert agg["water_frozen"] is False
        grid.state_raw("ice")[0] = 10
        _publish(env.wt, "hour_change", env.clock.time)  # 触发缓存失效
        assert env.engine.aggregates(0, 0)["water_frozen"] is True

    def test_land_chunk_never_frozen(self, env):
        """陆地 chunk（无水面 tile）恒 water_frozen=False。"""
        grid = _make_grid(water=False)
        env.engine.register_chunk(_chunk(grid=grid))
        grid.state_raw("ice")[0] = 10
        _publish(env.wt, "hour_change", env.clock.time)
        assert env.engine.aggregates(0, 0)["water_frozen"] is False

    def test_aggregates_shape(self, env):
        grid = _make_grid()
        env.engine.register_chunk(_chunk(grid=grid))
        agg = env.engine.aggregates(0, 0)
        assert set(agg) == {"water_frozen", "mean_snow", "mean_moisture"}
        assert 0 <= agg["mean_snow"] <= 255
        assert 0 <= agg["mean_moisture"] <= 100

    def test_aggregates_cached_and_invalidated(self, env):
        """聚合缓存：查询两次同值；写入后失效重算。"""
        from array import array
        grid = _make_grid()
        env.engine.register_chunk(_chunk(grid=grid))
        a1 = env.engine.aggregates(0, 0)
        a2 = env.engine.aggregates(0, 0)
        assert a1 == a2
        grid.state_raw("snow")[:] = array("B", [99]) * 40000
        _publish(env.wt, "hour_change", env.clock.time)
        assert env.engine.aggregates(0, 0)["mean_snow"] == 99


class TestEventContract:
    def test_precip_event_carries_chunks(self):
        """PrecipitationStart/Stop 事件契约：必含 chunks 字段。"""
        from ascend.weather.events import PrecipitationStart, PrecipitationStop
        ev = PrecipitationStart(
            precip_type="rain", intensity=5.0, time_of_day=0,
            chunks=((0, 0), (1, 1)),
        )
        assert ev.as_dict()["chunks"] == [[0, 0], [1, 1]]
        st = PrecipitationStop(time_of_day=0, chunks=())
        assert st.as_dict()["chunks"] == []

    def test_region_event_carries_chunks(self):
        """RegionEvent 契约：chunks 与 cells 同源。"""
        from ascend.weather.region_tracker import RegionEvent
        ev = RegionEvent(
            kind="start",
            cells=[(100.5, 100.5)],
            center_chunk=(0, 0),
            intensity=3.0,
            chunks=((0, 0),),
        )
        assert ev.chunks == ((0, 0),)

class TestDeferredGridReady:
    """注册时 tile_grid 为 None（运行期动态生成）→ 就绪后通道生效。

    回归：快照网格陈旧——on_tiles_ready 不回写网格引用时小时脉冲
    永久跳过、日变更直接崩溃（reviewer 实测复现）。
    """

    def test_hour_pulse_works_after_deferred_ready(self, env):
        """先注册（无网格）→ on_tiles_ready → 小时脉冲正常演化。"""
        grid = _make_grid()
        chunk = _chunk(grid=None)
        env.engine.register_chunk(chunk)
        chunk.tile_grid = grid  # 模拟 tile 生成完成
        env.engine.on_tiles_ready(0, 0)
        env.clock.skip(GAME_DAY)
        for h in range(24):
            _publish(env.wt, "hour_change", env.clock.time + h * 7200)
        agg = env.engine.aggregates(0, 0)
        assert 0 <= agg["mean_moisture"] <= 100, "就绪后脉冲生效"

    def test_day_change_after_deferred_ready_settles(self, env):
        """先注册（无网格）→ on_tiles_ready → 快进日变更正常结算。"""
        grid = _make_grid()
        chunk = _chunk(grid=None)
        env.engine.register_chunk(chunk)
        chunk.tile_grid = grid
        env.engine.on_tiles_ready(0, 0)
        env.clock.skip(200 * GAME_DAY)
        _publish(env.wt, "day_change", env.clock.time, {
            "day": 201, "previous_day": 1, "skipped_days": 200,
            "elapsed_days": 200, "day_change_count": 1,
        })
        assert chunk.settled_day == 201, "快进结算不因延迟就绪而失败"
        assert 0 <= env.engine.aggregates(0, 0)["mean_snow"] <= 255

    def test_day_change_grid_still_none_skips(self, env):
        """tile 始终未就绪：日变更跳过该 chunk 不抛。"""
        chunk = _chunk(grid=None)
        env.engine.register_chunk(chunk)
        env.clock.skip(200 * GAME_DAY)
        _publish(env.wt, "day_change", env.clock.time, {
            "day": 201, "previous_day": 1, "skipped_days": 200,
            "elapsed_days": 200, "day_change_count": 1,
        })
        assert chunk.settled_day == 0, "未就绪不结算"


class TestConcurrentAccess:
    """并发写路径：事件线程 vs handler 线程（RLock 互斥）。"""

    def test_concurrent_settle_and_pulse(self, env):
        """settle（日变更）与脉冲（小时变更）并发：无异常、状态一致。"""
        import threading
        grid = _make_grid()
        chunk = _chunk(grid=grid)
        env.engine.register_chunk(chunk)
        env.clock.skip(30 * GAME_DAY)

        errors: list[Exception] = []

        def settle_worker():
            try:
                for _ in range(20):
                    _publish(env.wt, "day_change", env.clock.time, {
                        "day": 31, "previous_day": 30, "skipped_days": 1,
                        "elapsed_days": 30, "day_change_count": 1,
                    })
            except Exception as exc:  # noqa: BLE001
                errors.append(exc)

        def pulse_worker():
            try:
                for _ in range(20):
                    _publish(env.wt, "hour_change", env.clock.time)
            except Exception as exc:  # noqa: BLE001
                errors.append(exc)

        t1 = threading.Thread(target=settle_worker)
        t2 = threading.Thread(target=pulse_worker)
        t1.start(); t2.start()
        t1.join(); t2.join()
        assert not errors, f"并发写路径异常: {errors}"
        assert chunk.settled_day == 31, "settled_day 一致"
        agg = env.engine.aggregates(0, 0)
        assert 0 <= agg["mean_moisture"] <= 100

    def test_concurrent_register_unregister(self, env):
        """注册/注销与脉冲并发：无 KeyError 撕裂。"""
        import threading
        grid = _make_grid()
        env.engine.register_chunk(_chunk(cx=3, cy=3, grid=grid))
        errors: list[Exception] = []

        def churn_worker():
            try:
                for i in range(30):
                    env.engine.register_chunk(_chunk(cx=i % 5, cy=0, grid=grid))
                    env.engine.unregister_chunk(i % 5, 0)
            except Exception as exc:  # noqa: BLE001
                errors.append(exc)

        def pulse_worker():
            try:
                for _ in range(30):
                    _publish(env.wt, "hour_change", env.clock.time)
            except Exception as exc:  # noqa: BLE001
                errors.append(exc)

        t1 = threading.Thread(target=churn_worker)
        t2 = threading.Thread(target=pulse_worker)
        t1.start(); t2.start()
        t1.join(); t2.join()
        assert not errors, f"并发注册注销异常: {errors}"


class TestSettledDayTracking:
    """脉冲演化推进 settled_day（落盘语义：状态=当日态，读档不重放）。

    回归：脉冲演化不更新 settled_day → 落盘后读档从旧结算日续算，
    把已脉冲演化的区间再结算一遍（双计）。
    """

    def test_hour_pulse_advances_settled_day(self, env):
        """24h 脉冲后 settled_day 跟随当前日。"""
        grid = _make_grid()
        chunk = _chunk(grid=grid)
        env.engine.register_chunk(chunk)
        env.clock.skip(GAME_DAY)  # → day 2
        for h in range(24):
            _publish(env.wt, "hour_change", env.clock.time + h * 7200)
        assert chunk.settled_day == 2, "脉冲演化推进结算日"

    def test_pulse_after_fast_forward_keeps_current_day(self, env):
        """快进结算后脉冲演化：settled_day 推进到脉冲所在日（不重放）。"""
        grid = _make_grid()
        chunk = _chunk(grid=grid)
        env.engine.register_chunk(chunk)
        env.clock.skip(200 * GAME_DAY)  # → day 201
        _publish(env.wt, "day_change", env.clock.time, {
            "day": 201, "previous_day": 1, "skipped_days": 200,
            "elapsed_days": 200, "day_change_count": 1,
        })
        assert chunk.settled_day == 201
        # 当日脉冲演化 → settled_day 保持 201（同日无虚标）
        for h in range(24):
            _publish(env.wt, "hour_change", env.clock.time + h * 7200)
        assert chunk.settled_day == 201, "同日脉冲不虚标"
        # 跨天脉冲（day 202）→ settled_day 跟进 202（读档从 202 续算）
        env.clock.skip(GAME_DAY)
        for h in range(24):
            _publish(env.wt, "hour_change", env.clock.time + h * 7200)
        assert chunk.settled_day == 202, "跨天脉冲推进结算日"
