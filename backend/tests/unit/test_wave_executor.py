"""波次执行器测试 — 串行/并行逐位一致 + 与手工顺序求值等价。

覆盖：
- 合成机制图：串行结果 = 并行结果；父值顺序与实例映射正确；
- 同波并发（屏障法证明同波任务确实并行）；wired 过滤；
- fail-closed：边界值缺失、lag/多偏移父依赖显式拒绝；
- 真实天气引擎对拍：同一时刻波次执行结果与引擎手工顺序逐位一致
  （同一注册表、同一输入，不同执行结构）。
"""

from types import SimpleNamespace
import threading

import pytest

from ascend.causal.fate_registry import load_fate_namespaces
from ascend.causal.program import compile_default_program, compile_world_program
from ascend.causal.state_schema import load_declaration
from ascend.causal.update_points import load_update_points
from ascend.config import GAME_DAY, TILE_MAP_SIZE
from ascend.runtime import execute_waves
from ascend.space import ClimateZone
from ascend.space.climate import WeatherParams
from ascend.time import WorldClock
from ascend.weather import mechanisms as wm
from ascend.weather.field import CH_HUMIDITY, CH_TEMPERATURE, CH_WIND
from ascend.weather.weather_engine import WeatherEngine
from ascend.world_tree import WorldTree

from tests.unit.test_program import TICKS


def _fn(**kwargs) -> None:
    """合成机制占位实现（稳定名称）。"""
    return None


def _node(microstep: str, axes: tuple = ()):
    return SimpleNamespace(
        update=SimpleNamespace(microstep=microstep),
        instance_domain=SimpleNamespace(axes=axes),
    )


def _parent(name: str, *, lag: int = 0, source_microstep: str = "a",
            offsets: int = 1):
    return SimpleNamespace(
        parent=name, lag=lag, source_microstep=source_microstep,
        spatial_offsets=((0, 0),) * offsets if offsets else (),
    )


def _mechanism(mid: str, output: str, parents: tuple = ()):
    return SimpleNamespace(
        mechanism_id=mid, output=output, parents=tuple(parents),
        function=_fn,
    )


def _registry(mechanisms, nodes, wired):
    return SimpleNamespace(
        microstep_order=("in", "a", "b", "c"),
        nodes=nodes,
        mechanisms={m.mechanism_id: m for m in mechanisms},
        declaration_hash="sha256:" + "ab" * 32,
        wired_nodes=frozenset(wired),
        equation_version=lambda output: f"eq:{output}",
        resolved_version=lambda output: f"res:{output}",
    )


def _build_program(registry):
    return compile_world_program(
        registry,
        state=load_declaration(),
        addresses=load_fate_namespaces(),
        points=load_update_points(),
        ticks=dict(TICKS),
        kernels=(),
    )


def _graph(*, extra=(), wired=None):
    """合成图：n.in(全局边界) + n.p(chunk 边界) → n.x → n.y → n.z。"""
    mechanisms = [
        _mechanism(
            "m.x", "n.x", [_parent("n.in", source_microstep="in")],
        ),
        _mechanism(
            "m.y", "n.y", [_parent("n.x", source_microstep="a"),
                           _parent("n.p", source_microstep="in")],
        ),
        _mechanism("m.z", "n.z", [_parent("n.y", source_microstep="b")]),
        *extra,
    ]
    nodes = {
        "n.in": _node("in"),
        "n.p": _node("in", ("chunk_x", "chunk_y")),
        "n.x": _node("a"),
        "n.y": _node("b", ("chunk_x", "chunk_y")),
        "n.z": _node("c", ("chunk_x", "chunk_y")),
    }
    return _registry(
        mechanisms, nodes,
        wired if wired is not None else ("n.x", "n.y", "n.z"),
    )


class TestWaveExecution:
    def _run(self, registry, *, parallel, instances=((0, 0), (0, 1)),
             provide=None, evaluate=None, workers=None):
        program = _build_program(registry)
        calls: list = []
        if evaluate is None:
            def evaluate(node_id, parents, *, frame, instance):
                calls.append((node_id, instance))
                return (node_id, tuple(sorted(parents.items())))

        def default_provide(node_id, instance):
            return {"n.in": 10, "n.p": 20}[node_id]

        values = execute_waves(
            program, registry, frame=7,
            evaluate=evaluate,
            provide=provide or default_provide,
            instances=list(instances),
            parallel=parallel,
            workers=workers,
        )
        return values, calls

    def test_serial_values_and_instance_mapping(self):
        values, _ = self._run(_graph(), parallel=False)
        assert values[("n.x", ())] == ("n.x", (("n.in", 10),))
        for instance in ((0, 0), (0, 1)):
            assert values[("n.y", instance)] == (
                "n.y",
                (("n.p", 20), ("n.x", values[("n.x", ())])),
            )
            assert values[("n.z", instance)] == (
                "n.z", (("n.y", values[("n.y", instance)]),),
            )

    def test_serial_equals_parallel(self):
        registry = _graph()
        serial, _ = self._run(registry, parallel=False)
        parallel, _ = self._run(registry, parallel=True, workers=4)
        assert serial == parallel

    def test_same_wave_runs_concurrently(self):
        """同波两个实例任务在并行模式下确实并发（屏障证明）。"""
        barrier = threading.Barrier(2, timeout=5.0)

        def evaluate(node_id, parents, *, frame, instance):
            if node_id == "n.y":
                barrier.wait()
            return (node_id, instance)

        values, _ = self._run(
            _graph(), parallel=True, workers=2, evaluate=evaluate,
        )
        assert ("n.y", (0, 0)) in values
        assert ("n.y", (0, 1)) in values

    def test_wired_filter(self):
        extra = (
            _mechanism("m.w", "n.w", [_parent("n.in", source_microstep="in")]),
        )
        registry = _graph(extra=extra)
        registry.nodes["n.w"] = _node("b")
        values, _ = self._run(registry, parallel=False)
        assert ("n.w", ()) not in values
        program = _build_program(registry)
        values_all = execute_waves(
            program, registry, frame=7,
            evaluate=lambda node_id, parents, *, frame, instance: node_id,
            provide=lambda node_id, instance: 1,
            instances=[(0, 0)],
            wired_only=False,
        )
        assert values_all[("n.w", ())] == "n.w"

    def test_missing_boundary_value_rejected(self):
        def provide(node_id, instance):
            raise KeyError(f"未提供边界值: {node_id}")

        with pytest.raises(KeyError, match="未提供边界值"):
            self._run(_graph(), parallel=False, provide=provide)

    def test_unsupported_parent_rejected(self):
        extra = (
            _mechanism(
                "m.lag", "n.lag",
                [_parent("n.in", lag=1, source_microstep="in")],
            ),
        )
        registry = _graph(extra=extra, wired=("n.x", "n.y", "n.z", "n.lag"))
        registry.nodes["n.lag"] = _node("b")
        with pytest.raises(NotImplementedError, match="父依赖暂不支持"):
            self._run(registry, parallel=False)

    def test_single_nonzero_offset_parent_rejected(self):
        """单偏移但非 (0,0)：不得静默退回同实例取值（fail-closed）。"""
        parent = SimpleNamespace(
            parent="n.in", lag=0, source_microstep="in",
            spatial_offsets=((1, 0),),
        )
        extra = (_mechanism("m.off", "n.off", [parent]),)
        registry = _graph(extra=extra, wired=("n.x", "n.y", "n.z", "n.off"))
        registry.nodes["n.off"] = _node("b")
        with pytest.raises(NotImplementedError, match="父依赖暂不支持"):
            self._run(registry, parallel=False)

def _hand_sequence(engine, now, boundary, instance, wm):
    """独立参考序列：按声明依赖逐节点手工求值（不经波次计划）。

    这是节点顺序/父集的事实参考（与旧手工实现同构）；波次执行器结果
    必须逐位等于它——验证波次分组、父值装配与声明依赖一致。
    """
    ev = engine.evaluate_node
    field = engine._fields[instance]
    bl = field.baseline
    b = lambda node: boundary[node]  # noqa: E731

    day = ev(wm.DAY, {wm.CLOCK_TICK: now}, frame=now, instance=())
    hour = ev(wm.HOUR_OF_DAY, {wm.CLOCK_TICK: now}, frame=now, instance=())
    doy = ev(wm.DAY_OF_YEAR, {wm.CLOCK_TICK: now}, frame=now, instance=())
    solar_decl = ev(
        wm.SOLAR_DECLINATION, {wm.DAY_OF_YEAR: doy}, frame=now, instance=(),
    )
    season_cos = ev(
        wm.SEASON_PHASE_COS, {wm.DAY: day}, frame=now, instance=(),
    )
    diurnal_cos = ev(
        wm.DIURNAL_PHASE_COS, {wm.HOUR_OF_DAY: hour}, frame=now, instance=(),
    )
    season_temp = ev(wm.SEASONAL_TEMPERATURE_OFFSET, {
        wm.SEASONAL_TEMPERATURE_AMPLITUDE: bl.seasonal_amp,
        wm.SEASON_PHASE_COS: season_cos,
    }, frame=now, instance=instance)
    diurnal_temp = ev(wm.DIURNAL_TEMPERATURE_OFFSET, {
        wm.DIURNAL_TEMPERATURE_AMPLITUDE: bl.diurnal_amp,
        wm.DIURNAL_PHASE_COS: diurnal_cos,
    }, frame=now, instance=instance)
    season_hum = ev(wm.SEASONAL_HUMIDITY_OFFSET, {
        wm.SEASONAL_HUMIDITY_AMPLITUDE: bl.humidity_seasonal_amp,
        wm.SEASON_PHASE_COS: season_cos,
        wm.HUMIDITY_SHARPNESS: bl.humidity_sharpness,
    }, frame=now, instance=instance)
    diurnal_hum = ev(wm.DIURNAL_HUMIDITY_OFFSET, {
        wm.DIURNAL_HUMIDITY_AMPLITUDE: bl.humidity_diurnal_amp,
        wm.DIURNAL_PHASE_COS: diurnal_cos,
    }, frame=now, instance=instance)
    sr = ev(wm.SUNRISE_HOUR, {
        wm.SOLAR_LATITUDE_PROXY: bl.latitude,
        wm.SOLAR_DECLINATION: solar_decl,
    }, frame=now, instance=instance)
    ss = ev(wm.SUNSET_HOUR, {
        wm.SOLAR_LATITUDE_PROXY: bl.latitude,
        wm.SOLAR_DECLINATION: solar_decl,
    }, frame=now, instance=instance)
    temperature = ev(wm.INSTANT_TEMPERATURE, {
        wm.ANNUAL_TEMPERATURE: bl.temperature,
        wm.SEASONAL_TEMPERATURE_OFFSET: season_temp,
        wm.DIURNAL_TEMPERATURE_OFFSET: diurnal_temp,
        wm.FIELD_TEMPERATURE_PERTURBATION: b(
            wm.FIELD_TEMPERATURE_PERTURBATION),
    }, frame=now, instance=instance)
    humidity = ev(wm.INSTANT_HUMIDITY, {
        wm.BASELINE_HUMIDITY: bl.humidity,
        wm.SEASONAL_HUMIDITY_OFFSET: season_hum,
        wm.DIURNAL_HUMIDITY_OFFSET: diurnal_hum,
        wm.FIELD_HUMIDITY_PERTURBATION: b(wm.FIELD_HUMIDITY_PERTURBATION),
    }, frame=now, instance=instance)
    wind_speed = ev(wm.INSTANT_WIND_SPEED, {
        wm.BASELINE_WIND_SPEED: bl.wind_speed,
        wm.FIELD_WIND_PERTURBATION: b(wm.FIELD_WIND_PERTURBATION),
        wm.FIELD_WIND_MULTIPLIER: b(wm.FIELD_WIND_MULTIPLIER),
    }, frame=now, instance=instance)
    threshold = ev(wm.PRECIPITATION_THRESHOLD, {
        wm.ANNUAL_RAINFALL: bl.rainfall,
    }, frame=now, instance=instance)
    intensity = ev(wm.INSTANT_PRECIPITATION_INTENSITY, {
        wm.FIELD_PRECIPITATION_SIGNAL: b(wm.FIELD_PRECIPITATION_SIGNAL),
        wm.PRECIPITATION_THRESHOLD: threshold,
        wm.MEAN_PRECIP_INTENSITY: bl.mean_intensity,
    }, frame=now, instance=instance)
    daylight = ev(wm.DAYLIGHT_HOURS, {
        wm.SUNRISE_HOUR: sr, wm.SUNSET_HOUR: ss,
    }, frame=now, instance=instance)
    sunshine = ev(wm.INSTANT_SUNSHINE, {
        wm.DAYLIGHT_HOURS: daylight,
        wm.FIELD_HUMIDITY_PERTURBATION: b(wm.FIELD_HUMIDITY_PERTURBATION),
    }, frame=now, instance=instance)
    return {
        wm.INSTANT_TEMPERATURE: temperature,
        wm.INSTANT_HUMIDITY: humidity,
        wm.INSTANT_WIND_SPEED: wind_speed,
        wm.INSTANT_PRECIPITATION_INTENSITY: intensity,
        wm.INSTANT_SUNSHINE: sunshine,
        wm.SUNRISE_HOUR: sr,
        wm.SUNSET_HOUR: ss,
    }


class TestWeatherEquivalence:
    """真实天气图：波次执行与引擎手工顺序逐位一致。"""

    def _engine(self):
        clock = WorldClock()
        wt = WorldTree()
        engine = WeatherEngine(clock, seed=42, world_tree_arg=wt)
        baseline = WeatherParams(
            temperature=17.0, rainfall=900.0, sunshine=12.0,
            altitude=120.0, humidity=65.0, wind_speed=4.0,
        )
        engine.register_chunk(0, 0, baseline, ClimateZone.TEMPERATE_FOREST, 15.0)
        return engine

    def _boundary(self, engine, now: int) -> dict:
        field = engine._fields[(0, 0)]
        bl = field.baseline
        wx = 0.5 * TILE_MAP_SIZE
        wy = 0.5 * TILE_MAP_SIZE
        ufield = engine._field
        cores = ufield.collect_cores(wx, wy, now)
        drift = ufield.texture.drift_offset(now)
        return {
            wm.CLOCK_TICK: now,
            wm.SOLAR_LATITUDE_PROXY: bl.latitude,
            wm.ANNUAL_RAINFALL: bl.rainfall,
            wm.BASELINE_HUMIDITY: bl.humidity,
            wm.MEAN_PRECIP_INTENSITY: bl.mean_intensity,
            wm.ANNUAL_TEMPERATURE: bl.temperature,
            wm.BASELINE_WIND_SPEED: bl.wind_speed,
            wm.DIURNAL_HUMIDITY_AMPLITUDE: bl.humidity_diurnal_amp,
            wm.DIURNAL_TEMPERATURE_AMPLITUDE: bl.diurnal_amp,
            wm.SEASONAL_HUMIDITY_AMPLITUDE: bl.humidity_seasonal_amp,
            wm.SEASONAL_TEMPERATURE_AMPLITUDE: bl.seasonal_amp,
            wm.HUMIDITY_SHARPNESS: bl.humidity_sharpness,
            wm.FIELD_TEMPERATURE_PERTURBATION: ufield.sample(
                CH_TEMPERATURE, wx, wy, now, cores, drift,
            ),
            wm.FIELD_HUMIDITY_PERTURBATION: ufield.sample(
                CH_HUMIDITY, wx, wy, now, cores, drift,
            ),
            wm.FIELD_WIND_PERTURBATION: ufield.sample(
                CH_WIND, wx, wy, now, cores, drift,
            ),
            wm.FIELD_WIND_MULTIPLIER: ufield.wind_multiplier(
                wx, wy, now, cores, drift,
            ),
            wm.FIELD_PRECIPITATION_SIGNAL: ufield.precip_signal(
                wx, wy, now, cores, drift,
            ),
        }

    @pytest.mark.parametrize(
        "now", [0, 3600, 12 * 3600, 5 * GAME_DAY + 7 * 3600],
    )
    def test_production_matches_hand_sequence(self, now):
        """生产路径（波次执行器）逐位等于独立手工序列。"""
        engine = self._engine()
        if now > 0:
            engine._clock.skip(now)
        boundary = self._boundary(engine, now)
        reference = _hand_sequence(engine, now, boundary, (0, 0), wm)
        params = engine.get_weather(0, 0, now)
        assert params is not None
        assert params.temperature == reference[wm.INSTANT_TEMPERATURE]
        assert params.humidity == reference[wm.INSTANT_HUMIDITY]
        assert params.wind_speed == reference[wm.INSTANT_WIND_SPEED]
        assert params.rainfall == reference[
            wm.INSTANT_PRECIPITATION_INTENSITY]
        assert params.sunshine == reference[wm.INSTANT_SUNSHINE]
        report = engine.get_weather_report(0, 0)
        assert report is not None
        assert report[1] == reference[wm.SUNRISE_HOUR]
        assert report[2] == reference[wm.SUNSET_HOUR]

    def test_wave_parallel_matches_serial_engine(self):
        """引擎并发分区开启后结果逐位一致（trace 未挂载）。"""
        for now in (7 * 3600, 3 * GAME_DAY + 5 * 3600):
            serial = self._engine()
            parallel = self._engine()
            parallel._wave_parallel = True
            serial._clock.skip(now)
            parallel._clock.skip(now)
            assert serial.get_weather(0, 0, now) == parallel.get_weather(0, 0, now)
            assert serial.get_weather_report(0, 0) ==                 parallel.get_weather_report(0, 0)
            serial.shutdown()
            parallel.shutdown()

    def test_serial_equals_parallel_on_real_graph(self):
        from ascend.causal.world import ASCEND_MECHANISMS

        engine = self._engine()
        now = 3 * GAME_DAY + 5 * 3600
        engine._clock.skip(now)
        boundary = self._boundary(engine, now)

        def provide(node_id, instance):
            return boundary[node_id]

        program = compile_default_program()
        kwargs = dict(
            frame=now, evaluate=ASCEND_MECHANISMS.evaluate, provide=provide,
            instances=[(0, 0), (0, 1), (0, 2)],
        )
        serial = execute_waves(program, ASCEND_MECHANISMS, **kwargs)
        parallel = execute_waves(
            program, ASCEND_MECHANISMS, parallel=True, workers=4, **kwargs,
        )
        assert serial == parallel
