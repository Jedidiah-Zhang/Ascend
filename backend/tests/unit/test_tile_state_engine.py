"""地形状态引擎测试 — 声明更新点的单一积分器。

覆盖：注册/就绪/注销、每小时积分与游标、逐步推进 ≡ 一次性补算、
无天气不推进（fail-closed）、阈值穿越成对记录、聚合派生缓存、
脏标记与事件形状。
"""

from types import SimpleNamespace

import pytest

from ascend.config import GAME_HOUR
from ascend.runtime import FrameScheduler, FrameStateStore
from ascend.space.tile_grid import TileGrid
from ascend.space.tile_state import TileStateEngine, state_evolve
from ascend.space.terrain import TerrainType
from ascend.time import WorldClock
from ascend.world_tree import WorldTree


class _FakeWeather:
    """确定性伪天气：每小时可配置 (温度, 雨强 mm/h)；None=未注册。"""

    def __init__(self) -> None:
        self.hours: dict[int, tuple[float, float]] = {}
        self.registered = True
        self.lookup: list[tuple[int, int]] = []

    def get_weather(self, cx: int, cy: int, t: int):
        if not self.registered:
            return None
        self.lookup.append((cx, cy, t))
        hour = t // GAME_HOUR
        temp, rain = self.hours.get(hour, (10.0, 0.0))
        return SimpleNamespace(temperature=temp, rainfall=rain)


def _chunk(cx: int = 0, cy: int = 0, grid: TileGrid | None = None):
    """引擎级最小 chunk 替身（引擎只用坐标/网格/游标/脏标记/版本）。"""
    chunk = SimpleNamespace(
        cx=cx, cy=cy,
        tile_grid=grid if grid is not None else TileGrid(),
        integrated_through=0,
        dirty=False,
        revision=0,
    )

    def mark_modified() -> None:
        chunk.dirty = True
        chunk.revision += 1

    chunk.mark_modified = mark_modified
    return chunk


@pytest.fixture
def env():
    clock = WorldClock(epoch=0)
    weather = _FakeWeather()
    wt = WorldTree()
    engine = TileStateEngine(clock, weather, wt=wt)
    events: list = []
    unsub = wt.subscribe("state_threshold_crossed", events.append)
    yield SimpleNamespace(
        clock=clock, weather=weather, engine=engine, events=events,
    )
    unsub()
    engine.shutdown()


class TestRegistration:
    def test_aggregates_requires_registration(self, env):
        assert env.engine.aggregates(0, 0) == {}
        env.engine.register_chunk(_chunk())
        assert env.engine.aggregates(0, 0)
        assert env.engine.aggregates(9, 9) == {}
        env.engine.unregister_chunk(0, 0)
        assert env.engine.aggregates(0, 0) == {}

    def test_aggregates_shape(self, env):
        env.engine.register_chunk(_chunk())
        agg = env.engine.aggregates(0, 0)
        assert set(agg) == {"water_frozen", "mean_snow", "mean_moisture"}
        assert agg["water_frozen"] is False
        assert agg["mean_snow"] == 0.0

    def test_water_frozen_detection(self, env):
        grid = TileGrid()
        grid.raw_data()[0] = int(TerrainType.WATER)
        grid.set_state("ice", 0, 0, 5)
        env.engine.register_chunk(_chunk(grid=grid))
        assert env.engine.aggregates(0, 0)["water_frozen"] is True

    def test_unregister_all(self, env):
        env.engine.register_chunk(_chunk())
        env.engine.register_chunk(_chunk(1, 0))
        env.engine.unregister_all()
        assert env.engine.aggregates(0, 0) == {}
        assert env.engine.aggregates(1, 0) == {}


class TestIntegration:
    def test_on_tiles_ready_catches_up(self, env):
        env.weather.hours = {h: (10.0, 1.0) for h in range(1, 4)}
        chunk = _chunk()
        env.engine.register_chunk(chunk)
        env.clock.restore(time=3 * GAME_HOUR)
        env.engine.on_tiles_ready(0, 0)
        assert chunk.integrated_through == 3 * GAME_HOUR
        assert sum(chunk.tile_grid.state_raw("moisture")) > 0
        assert chunk.dirty is True

    def test_advance_before_boundary_is_noop(self, env):
        chunk = _chunk()
        env.engine.register_chunk(chunk)
        env.engine.advance(GAME_HOUR - 1)
        assert chunk.integrated_through == 0
        assert sum(chunk.tile_grid.state_raw("moisture")) == 0

    def test_stepwise_equals_one_shot(self, env):
        """逐步推进 ≡ 一次性补算（同一声明积分的路径一致性）。"""
        env.weather.hours = {
            h: (-5.0 if h % 2 else 12.0, 0.5 + 0.1 * h)
            for h in range(1, 7)
        }
        stepwise = _chunk()
        oneshot = _chunk(1, 0)
        env.engine.register_chunk(stepwise)
        env.engine.register_chunk(oneshot)
        for hour in range(1, 7):
            env.engine.advance(hour * GAME_HOUR)
        env.engine.advance(6 * GAME_HOUR)
        for key in ("moisture", "snow", "ice"):
            assert list(stepwise.tile_grid.state_raw(key)) == \
                list(oneshot.tile_grid.state_raw(key))
        assert stepwise.integrated_through == oneshot.integrated_through

    def test_one_hour_matches_kernel_reference(self, env):
        """单小时积分与直接调用内核逐位一致（无额外路径）。"""
        env.weather.hours = {1: (10.0, 2.0)}
        chunk = _chunk()
        env.engine.register_chunk(chunk)
        env.engine.advance(GAME_HOUR)
        reference = TileGrid()
        precip = [[0.0], [0.0], [0.0]]
        precip[0][0] = 2.0 * 24.0
        state_evolve(reference, precip=precip, temp=[10.0], dt=1.0 / 24.0)
        for key in ("moisture", "snow", "ice"):
            assert list(chunk.tile_grid.state_raw(key)) == \
                list(reference.state_raw(key))

    def test_missing_weather_blocks_progress(self, env):
        chunk = _chunk()
        env.engine.register_chunk(chunk)
        env.weather.registered = False
        env.engine.advance(3 * GAME_HOUR)
        assert chunk.integrated_through == 0
        assert sum(chunk.tile_grid.state_raw("moisture")) == 0

    def test_snow_and_moisture_split_by_temperature(self, env):
        env.weather.hours = {1: (-10.0, 3.0)}
        snow_chunk = _chunk()
        env.engine.register_chunk(snow_chunk)
        env.engine.advance(GAME_HOUR)
        assert sum(snow_chunk.tile_grid.state_raw("snow")) > 0
        assert sum(snow_chunk.tile_grid.state_raw("moisture")) == 0

        env.weather.hours = {1: (-10.0, 3.0), 2: (10.0, 3.0)}
        rain_chunk = _chunk(2, 0)
        env.engine.register_chunk(rain_chunk)
        env.engine.advance(2 * GAME_HOUR)
        assert sum(rain_chunk.tile_grid.state_raw("snow")) > 0
        assert sum(rain_chunk.tile_grid.state_raw("moisture")) > 0


class TestThresholdEvents:
    def test_up_and_down_crossings(self, env):
        """雪跨 15：逐步推进下先升档（up）再融化跌破（down）。"""
        env.weather.hours = {
            1: (-10.0, 15.0),                    # 一小时沉积 15 → 升到档 1
            **{h: (20.0, 0.0) for h in range(2, 12)},  # 高温融化
        }
        chunk = _chunk()
        env.engine.register_chunk(chunk)
        for hour in range(1, 12):
            env.engine.advance(hour * GAME_HOUR)
        directions = [(e.data["direction"], e.data["threshold"])
                      for e in env.events]
        assert ("up", 15) in directions
        assert any(direction == "down" and threshold == 15
                   for direction, threshold in directions)

    def test_no_event_for_moisture(self, env):
        """moisture 无阈值档：不产生穿越事件。"""
        env.weather.hours = {h: (10.0, 5.0) for h in range(1, 4)}
        env.engine.register_chunk(_chunk())
        env.engine.advance(3 * GAME_HOUR)
        assert env.events == []

    def test_net_crossing_in_catch_up(self, env):
        """一次性补算跨多档只发净变化一条，threshold=升到的档下界。"""
        env.weather.hours = {h: (-10.0, 40.0) for h in range(1, 3)}
        chunk = _chunk()
        env.engine.register_chunk(chunk)
        env.engine.advance(2 * GAME_HOUR)
        ups = [e for e in env.events if e.data["direction"] == "up"]
        assert len(ups) == 1
        assert ups[0].data["threshold"] in (30, 50)

    def test_event_shape(self, env):
        env.weather.hours = {1: (-10.0, 15.0)}
        env.engine.register_chunk(_chunk())
        env.engine.advance(GAME_HOUR)
        event = env.events[0]
        assert event.event_type == "state_threshold_crossed"
        assert event.data["state"] == "snow"
        assert event.data["cx"] == 0 and event.data["cy"] == 0
        assert event.timestamp == GAME_HOUR

    def test_no_event_on_registration_of_high_state(self, env):
        """已高于档位的既有状态在注册/首次推进时不补发事件。"""
        grid = TileGrid()
        for i in range(len(grid.state_raw("snow"))):
            grid.state_raw("snow")[i] = 60
        env.engine.register_chunk(_chunk(grid=grid))
        env.engine.advance(GAME_HOUR)
        assert env.events == []


class TestFrameCommit:
    """帧事务提交：影子状态在提交前不可见，失败整帧回滚（WC-7.6）。"""

    def _setup(self):
        clock = WorldClock(epoch=0)
        weather = _FakeWeather()
        weather.hours[1] = (10.0, 5.0)
        wt = WorldTree()
        store = FrameStateStore()
        engine = TileStateEngine(clock, weather, wt=wt, store=store)
        chunk = _chunk()
        engine.register_chunk(chunk)
        engine.on_tiles_ready(0, 0)
        return clock, weather, wt, store, engine, chunk

    def test_state_invisible_until_batch_commit(self):
        clock, weather, wt, store, engine, chunk = self._setup()
        seen: list[tuple[int, int]] = []

        def spy(now: int) -> None:
            seen.append((
                chunk.integrated_through,
                max(chunk.tile_grid.state_raw("moisture")),
            ))

        scheduler = FrameScheduler(store=store)
        scheduler.register(
            "terrain.integrate", period=GAME_HOUR, callback=engine.advance,
        )
        scheduler.register("spy", period=GAME_HOUR, callback=spy)
        scheduler.advance(GAME_HOUR)
        assert seen == [(0, 0)], "帧内读者必须看到已提交状态"
        assert chunk.integrated_through == GAME_HOUR
        assert max(chunk.tile_grid.state_raw("moisture")) > 0
        engine.shutdown()

    def test_failing_point_aborts_terrain_frame(self):
        clock, weather, wt, store, engine, chunk = self._setup()
        committed_version = store.version
        state = {"fail": True}

        def failing(now: int) -> None:
            if state["fail"]:
                raise RuntimeError("boom")

        scheduler = FrameScheduler(store=store)
        scheduler.register(
            "terrain.integrate", period=GAME_HOUR, callback=engine.advance,
        )
        scheduler.register("failing", period=GAME_HOUR, callback=failing)
        with pytest.raises(RuntimeError, match="boom"):
            scheduler.advance(GAME_HOUR)
        assert chunk.integrated_through == 0
        assert max(chunk.tile_grid.state_raw("moisture")) == 0
        assert store.version == committed_version
        # 下一帧重试：两个更新点都重跑，整帧提交
        state["fail"] = False
        scheduler.advance(GAME_HOUR)
        assert chunk.integrated_through == GAME_HOUR
        assert max(chunk.tile_grid.state_raw("moisture")) > 0
        assert store.version == committed_version + 1
        engine.shutdown()
