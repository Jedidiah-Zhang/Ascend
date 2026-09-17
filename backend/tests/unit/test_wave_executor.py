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
    def test_matches_hand_sequence(self, now):
        from ascend.causal.world import ASCEND_MECHANISMS

        engine = self._engine()
        if now > 0:
            engine._clock.skip(now)
        boundary = self._boundary(engine, now)

        def provide(node_id, instance):
            if node_id in boundary:
                return boundary[node_id]
            raise KeyError(f"未提供边界值: {node_id}")

        values = execute_waves(
            compile_default_program(), ASCEND_MECHANISMS,
            frame=now, evaluate=engine.evaluate_node, provide=provide,
            instances=[(0, 0)],
        )
        params = engine.get_weather(0, 0, now)
        assert params is not None
        instance = (0, 0)
        assert values[(wm.INSTANT_TEMPERATURE, instance)] == params.temperature
        assert values[(wm.INSTANT_HUMIDITY, instance)] == params.humidity
        assert values[(wm.INSTANT_WIND_SPEED, instance)] == params.wind_speed
        assert values[(wm.INSTANT_SUNSHINE, instance)] == params.sunshine
        assert values[
            (wm.INSTANT_PRECIPITATION_INTENSITY, instance)
        ] == params.rainfall

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
