"""因果机制注册表 P1 契约测试。"""

from dataclasses import replace
import json
from pathlib import Path

import pytest

from ascend.causal import MechanismRegistry
from ascend.causal.world import ASCEND_MECHANISMS
from ascend.weather import derive
from ascend.weather.mechanisms import (
    ANNUAL_RAINFALL,
    ANNUAL_TEMPERATURE,
    INSTANT_PRECIPITATION_TYPE,
    INSTANT_TEMPERATURE,
    SEASONAL_TEMPERATURE_AMPLITUDE,
    SEA_LEVEL_TEMPERATURE,
    SOLAR_LATITUDE_PROXY,
)


EXPECTED_NODES = {
    ANNUAL_RAINFALL,
    ANNUAL_TEMPERATURE,
    INSTANT_PRECIPITATION_TYPE,
    INSTANT_TEMPERATURE,
    SEASONAL_TEMPERATURE_AMPLITUDE,
    SEA_LEVEL_TEMPERATURE,
    SOLAR_LATITUDE_PROXY,
}


def _rebuild_registry(**changes) -> MechanismRegistry:
    values = {
        "schema_version": ASCEND_MECHANISMS.schema_version,
        "declaration_id": ASCEND_MECHANISMS.declaration_id,
        "declaration_version": ASCEND_MECHANISMS.declaration_version,
        "microstep_order": ASCEND_MECHANISMS.microstep_order,
        "slice_boundary": ASCEND_MECHANISMS.slice_boundary,
        "nodes": tuple(ASCEND_MECHANISMS.nodes.values()),
        "parameters": tuple(ASCEND_MECHANISMS.parameters.values()),
        "exogenous_sources": tuple(
            ASCEND_MECHANISMS.exogenous_sources.values()
        ),
        "mechanisms": tuple(ASCEND_MECHANISMS.mechanisms.values()),
    }
    values.update(changes)
    return MechanismRegistry(**values)


class TestRegistryValidation:
    def test_weather_slice_passes_c0_and_c1(self):
        assert ASCEND_MECHANISMS.validate_c0() == ()
        assert ASCEND_MECHANISMS.validate_c1() == ()
        assert EXPECTED_NODES <= set(ASCEND_MECHANISMS.nodes)

    def test_duplicate_node_is_rejected(self):
        nodes = tuple(ASCEND_MECHANISMS.nodes.values())
        with pytest.raises(ValueError, match="重复节点"):
            _rebuild_registry(nodes=nodes + (nodes[0],))

    def test_duplicate_parameter_is_rejected(self):
        parameters = tuple(ASCEND_MECHANISMS.parameters.values())
        with pytest.raises(ValueError, match="重复参数"):
            _rebuild_registry(parameters=parameters + (parameters[0],))

    def test_parameter_value_type_mismatch_is_rejected(self):
        parameters = tuple(ASCEND_MECHANISMS.parameters.values())
        for bad_value in ("abc", True):
            broken = tuple(
                replace(item, value=bad_value) if item is parameters[0] else item
                for item in parameters
            )
            with pytest.raises(ValueError, match="与声明类型 float 不符|参数值域"):
                _rebuild_registry(parameters=broken)

    def test_parameter_bounds_inverted_is_rejected(self):
        parameters = tuple(ASCEND_MECHANISMS.parameters.values())
        broken = tuple(
            replace(item, bounds=(1.0, -1.0)) if item is parameters[0] else item
            for item in parameters
        )
        with pytest.raises(ValueError, match="bounds 倒置"):
            _rebuild_registry(parameters=broken)

    def test_uninspectable_equation_source_is_rejected(self):
        """打包/动态环境无源码可读时拒绝生成无来源版本（fail-closed）。"""
        mechanism = ASCEND_MECHANISMS.mechanism_for(SOLAR_LATITUDE_PROXY)
        function = eval(  # noqa: S307  无源码路径的刻意构造
            "lambda sea_level_temperature, input_min, input_max, "
            "output_min, output_max: 0.0"
        )
        broken = replace(mechanism, function=function)
        mechanisms = tuple(
            broken if item.mechanism_id == broken.mechanism_id else item
            for item in ASCEND_MECHANISMS.mechanisms.values()
        )
        with pytest.raises(ValueError, match="拒绝生成无来源版本"):
            _rebuild_registry(mechanisms=mechanisms)

    def test_exogenous_source_with_undeclared_microstep_is_rejected(self):
        from ascend.causal import ExogenousSourceSpec

        source = ExogenousSourceSpec(
            source_id="test.source",
            distribution="constant_0",
            distribution_parameters=(),
            draw_microstep="not.declared",
            instance_axes=("chunk_x",),
            address_template=("seed", "test"),
            shared_by=(),
            dynamic_field=False,
        )
        with pytest.raises(ValueError, match="微步未声明"):
            _rebuild_registry(exogenous_sources=(source,))

    def test_mechanism_node_without_writer_is_rejected(self):
        mechanisms = tuple(ASCEND_MECHANISMS.mechanisms.values())[1:]
        with pytest.raises(ValueError, match="没有写者"):
            _rebuild_registry(mechanisms=mechanisms)

    def test_slice_boundary_node_with_writer_is_rejected(self):
        boundary = next(
            item for item in ASCEND_MECHANISMS.nodes.values()
            if item.origin == "slice_boundary"
        )
        mechanisms = tuple(ASCEND_MECHANISMS.mechanisms.values())
        first = mechanisms[0]
        forged = replace(first, output=boundary.node_id)
        with pytest.raises(ValueError, match="节点不是 mechanism origin"):
            _rebuild_registry(mechanisms=(forged, *mechanisms[1:]))

    def test_same_microstep_parent_is_rejected(self):
        mechanism = ASCEND_MECHANISMS.mechanism_for(SOLAR_LATITUDE_PROXY)
        parent = replace(
            mechanism.parents[0],
            source_microstep="weather.chunk_derived",
        )
        broken = replace(mechanism, parents=(parent,))
        mechanisms = tuple(
            broken if item.mechanism_id == broken.mechanism_id else item
            for item in ASCEND_MECHANISMS.mechanisms.values()
        )
        with pytest.raises(ValueError, match="源微步"):
            _rebuild_registry(mechanisms=mechanisms)

    def test_callable_signature_drift_is_rejected(self):
        mechanism = next(iter(ASCEND_MECHANISMS.mechanisms.values()))
        broken = replace(mechanism, function=lambda: 0.0)
        mechanisms = tuple(
            broken if item.mechanism_id == broken.mechanism_id else item
            for item in ASCEND_MECHANISMS.mechanisms.values()
        )
        with pytest.raises(ValueError, match="函数签名"):
            _rebuild_registry(mechanisms=mechanisms)

    def test_stale_c1_witness_is_rejected(self):
        mechanism = ASCEND_MECHANISMS.mechanism_for(
            SOLAR_LATITUDE_PROXY
        )
        witness = replace(
            mechanism.witnesses[0],
            expected_outputs=(79.0, 1.0),
        )
        broken = replace(mechanism, witnesses=(witness,))
        mechanisms = tuple(
            broken if item.mechanism_id == broken.mechanism_id else item
            for item in ASCEND_MECHANISMS.mechanisms.values()
        )
        with pytest.raises(ValueError, match="C1"):
            _rebuild_registry(mechanisms=mechanisms)

    def test_malformed_witness_shape_is_rejected(self):
        mechanism = ASCEND_MECHANISMS.mechanism_for(SOLAR_LATITUDE_PROXY)
        witness = replace(mechanism.witnesses[0], expected_outputs=(80.0,))
        broken = replace(mechanism, witnesses=(witness,))
        mechanisms = tuple(
            broken if item.mechanism_id == broken.mechanism_id else item
            for item in ASCEND_MECHANISMS.mechanisms.values()
        )
        with pytest.raises(ValueError, match="恰为二元"):
            _rebuild_registry(mechanisms=mechanisms)

    def test_registry_is_immutable(self):
        with pytest.raises(AttributeError, match="不可变"):
            ASCEND_MECHANISMS.microstep_order = ("x",)
        with pytest.raises(AttributeError, match="不可变"):
            ASCEND_MECHANISMS.new_attr = 1


class TestWeatherMechanisms:
    def test_each_parent_has_a_version_bound_witness(self):
        snapshot = ASCEND_MECHANISMS.snapshot()
        for mechanism in snapshot["mechanisms"].values():
            witnessed = {item["parent"] for item in mechanism["witnesses"]}
            parents = {item["parent"] for item in mechanism["parents"]}
            assert witnessed == parents
            assert all(
                item["equation_version"] == mechanism["equation_version"]
                for item in mechanism["witnesses"]
            )

    def test_operational_edges_are_structural(self):
        edges = ASCEND_MECHANISMS.snapshot()["edges"]
        assert edges
        assert all(edge["role"] == "structural" for edge in edges)
        inverse = {
            (edge["parent"], edge["child"])
            for edge in edges
            if edge["analysis_role"] == "inverse"
        }
        assert inverse == {
            (SEA_LEVEL_TEMPERATURE, SOLAR_LATITUDE_PROXY),
            (ANNUAL_TEMPERATURE, SEASONAL_TEMPERATURE_AMPLITUDE),
            (ANNUAL_RAINFALL, SEASONAL_TEMPERATURE_AMPLITUDE),
        }

    def test_equations_match_independent_reference_values(self):
        assert ASCEND_MECHANISMS.evaluate(
            SOLAR_LATITUDE_PROXY,
            {SEA_LEVEL_TEMPERATURE: -5.0},
        ) == pytest.approx(80.0)
        assert ASCEND_MECHANISMS.evaluate(
            SOLAR_LATITUDE_PROXY,
            {SEA_LEVEL_TEMPERATURE: 35.0},
        ) == pytest.approx(0.0)
        assert ASCEND_MECHANISMS.evaluate(
            SEASONAL_TEMPERATURE_AMPLITUDE,
            {ANNUAL_TEMPERATURE: 15.0, ANNUAL_RAINFALL: 200.0},
        ) == pytest.approx(18.6)
        assert ASCEND_MECHANISMS.evaluate(
            SEASONAL_TEMPERATURE_AMPLITUDE,
            {ANNUAL_TEMPERATURE: 15.0, ANNUAL_RAINFALL: 2000.0},
        ) == pytest.approx(15.0)
        assert ASCEND_MECHANISMS.evaluate(
            INSTANT_PRECIPITATION_TYPE,
            {INSTANT_TEMPERATURE: -0.049},
        ) == "snow"
        assert ASCEND_MECHANISMS.evaluate(
            INSTANT_PRECIPITATION_TYPE,
            {INSTANT_TEMPERATURE: 0.051},
        ) == "rain"

    def test_evaluation_requires_exact_legal_parent_values(self):
        with pytest.raises(KeyError, match="父输入"):
            ASCEND_MECHANISMS.evaluate(SOLAR_LATITUDE_PROXY, {})
        with pytest.raises(ValueError, match="值域"):
            ASCEND_MECHANISMS.evaluate(
                INSTANT_PRECIPITATION_TYPE,
                {INSTANT_TEMPERATURE: 1000.0},
            )

    def test_evaluate_rejects_bool_and_nonfinite_for_float_node(self):
        import math
        for bad in (True, False, math.nan, math.inf):
            with pytest.raises(ValueError, match="值域"):
                ASCEND_MECHANISMS.evaluate(
                    SOLAR_LATITUDE_PROXY,
                    {SEA_LEVEL_TEMPERATURE: bad},
                )

    def test_public_derive_functions_raise_out_of_domain(self):
        with pytest.raises(ValueError):
            derive.precip_type_for(1000.0)
        with pytest.raises(ValueError):
            derive.derive_latitude(100.0)
        with pytest.raises(ValueError):
            derive.derive_seasonal_amp(float("nan"), 800.0)

    @pytest.mark.parametrize(
        ("function_name", "output", "inputs", "expected"),
        [
            (
                "derive_latitude",
                SOLAR_LATITUDE_PROXY,
                {SEA_LEVEL_TEMPERATURE: 10.0},
                123.0,
            ),
            (
                "derive_seasonal_amp",
                SEASONAL_TEMPERATURE_AMPLITUDE,
                {ANNUAL_TEMPERATURE: 10.0, ANNUAL_RAINFALL: 500.0},
                12.3,
            ),
            (
                "precip_type_for",
                INSTANT_PRECIPITATION_TYPE,
                {INSTANT_TEMPERATURE: -2.0},
                "hail",
            ),
        ],
    )
    def test_public_weather_functions_delegate_to_registry(
        self, monkeypatch, function_name, output, inputs, expected,
    ):
        calls = []

        class FakeRegistry:
            def evaluate(self, target, parent_values):
                calls.append((target, parent_values))
                return expected

        monkeypatch.setattr(derive, "_registry", lambda: FakeRegistry())
        result = getattr(derive, function_name)(*inputs.values())
        assert result == expected
        assert calls == [(output, inputs)]


class TestRegistrySnapshot:
    def test_snapshot_has_explicit_c0_sections_and_stable_hash(self):
        snapshot = ASCEND_MECHANISMS.snapshot()
        assert snapshot["schema_version"] == 3
        assert snapshot["version"] == 3
        assert snapshot["declaration"]["hash"].startswith("sha256:")
        assert snapshot == ASCEND_MECHANISMS.snapshot()
        assert EXPECTED_NODES <= set(snapshot["nodes"])
        assert "parameters" in snapshot
        assert snapshot["exogenous_sources"] == {}
        for node in snapshot["nodes"].values():
            assert set(node) == {
                "access",
                "instance_domain",
                "math",
                "origin",
                "role",
                "state",
                "update",
                "value",
            }
        for mechanism in snapshot["mechanisms"].values():
            assert mechanism["random_sources"] == []
            assert mechanism["boundary_cases"]
            assert mechanism["equation_version"].startswith("sha256:")
            assert mechanism["resolved_version"].startswith("sha256:")

    def test_committed_equations_snapshot_is_generated_from_registry(self):
        root = Path(__file__).resolve().parents[3]
        path = root / "research" / "equations" / "equations.json"
        committed = json.loads(path.read_text(encoding="utf-8"))
        assert committed == ASCEND_MECHANISMS.snapshot()


class TestIndependentReferenceParity:
    """独立参考实现（手写旧公式）与注册表链式求值逐节点对拍。"""

    @staticmethod
    def _reference_chain(now, bl, season_cos, diurnal_cos, decl, perturb,
                         wind_perturb, hum_perturb, signal, multiplier):
        import math
        from ascend.config import (GAME_DAY, GAME_HOUR, HUMIDITY_BOUNDS,
                                   HUMIDITY_PERTURB_SCALE,
                                   SUNSHINE_BOUNDS, SUNSHINE_PERTURB_SCALE,
                                   TEMP_BOUNDS, TEMP_PERTURB_SCALE,
                                   WIND_BOUNDS, WIND_PERTURB_SCALE)
        day = now // GAME_DAY + 1
        doy = (now // GAME_DAY) % 360
        hour = (now % GAME_DAY) / GAME_HOUR
        season_temp = bl["seasonal_amp"] * season_cos
        diurnal_temp = bl["diurnal_amp"] * diurnal_cos
        if bl["sharpness"] > 0:
            season_hum = bl["hum_seasonal_amp"] * math.tanh(
                season_cos * bl["sharpness"])
        else:
            season_hum = bl["hum_seasonal_amp"] * season_cos
        diurnal_hum = bl["hum_diurnal_amp"] * (-diurnal_cos)
        lat = math.radians(bl["latitude"])
        tp = max(-1.0, min(1.0, math.tan(lat) * math.tan(decl)))
        half = math.degrees(math.acos(-tp)) / 15.0
        sr = 12.0 - half
        ss = 12.0 + half
        daylight = ss - sr
        temperature = min(max(
            bl["temperature"] + season_temp + diurnal_temp
            + perturb * TEMP_PERTURB_SCALE, TEMP_BOUNDS[0]), TEMP_BOUNDS[1])
        humidity = min(max(
            bl["humidity"] + season_hum + diurnal_hum
            + hum_perturb * HUMIDITY_PERTURB_SCALE,
            HUMIDITY_BOUNDS[0]), HUMIDITY_BOUNDS[1])
        wind0 = min(max(bl["wind"] + wind_perturb * WIND_PERTURB_SCALE,
                        WIND_BOUNDS[0]), WIND_BOUNDS[1])
        wind = min(max(wind0 * multiplier, WIND_BOUNDS[0]), WIND_BOUNDS[1])
        sunshine = min(max(
            daylight + hum_perturb * SUNSHINE_PERTURB_SCALE,
            SUNSHINE_BOUNDS[0]), SUNSHINE_BOUNDS[1])
        return (day, doy, hour, season_temp, diurnal_temp, season_hum,
                diurnal_hum, sr, ss, daylight, temperature, humidity, wind,
                sunshine)

    def test_registry_chain_matches_independent_reference(self):
        import math
        from ascend.config import GAME_DAY, GAME_HOUR
        m = __import__("ascend.weather.mechanisms", fromlist=["x"])
        reg = ASCEND_MECHANISMS
        contexts = []
        for tick in (0, 3600, 14 * GAME_HOUR, GAME_DAY * 46, GAME_DAY * 200):
            day = reg.evaluate(m.DAY, {m.CLOCK_TICK: tick})
            doy = reg.evaluate(m.DAY_OF_YEAR, {m.CLOCK_TICK: tick})
            hour = reg.evaluate(m.HOUR_OF_DAY, {m.CLOCK_TICK: tick})
            contexts.append((
                tick,
                reg.evaluate(m.SEASON_PHASE_COS, {m.DAY: day}),
                reg.evaluate(m.DIURNAL_PHASE_COS, {m.HOUR_OF_DAY: hour}),
                reg.evaluate(m.SOLAR_DECLINATION, {m.DAY_OF_YEAR: doy}),
            ))
        for bl in (
            {"temperature": 20.0, "humidity": 60.0, "wind": 5.0,
             "seasonal_amp": 12.0, "diurnal_amp": 6.0,
             "hum_seasonal_amp": 4.8, "hum_diurnal_amp": 4.8,
             "sharpness": 0.0, "latitude": 40.0},
            {"temperature": -10.0, "humidity": 80.0, "wind": 2.0,
             "seasonal_amp": 24.0, "diurnal_amp": 12.0,
             "hum_seasonal_amp": 9.6, "hum_diurnal_amp": 9.6,
             "sharpness": 2.5, "latitude": 75.0},
        ):
            for tick, season_cos, diurnal_cos, decl in contexts:
                perturb = 0.3
                wind_perturb = -0.4
                hum_perturb = 0.2
                signal = 0.6
                multiplier = 1.4
                ref = self._reference_chain(
                    tick, bl, season_cos, diurnal_cos, decl, perturb,
                    wind_perturb, hum_perturb, signal, multiplier,
                )
                reg_day = reg.evaluate(m.DAY, {m.CLOCK_TICK: tick})
                reg_doy = reg.evaluate(m.DAY_OF_YEAR, {m.CLOCK_TICK: tick})
                reg_hour = reg.evaluate(m.HOUR_OF_DAY, {m.CLOCK_TICK: tick})
                season_temp = reg.evaluate(
                    m.SEASONAL_TEMPERATURE_OFFSET,
                    {m.SEASONAL_TEMPERATURE_AMPLITUDE: bl["seasonal_amp"],
                     m.SEASON_PHASE_COS: season_cos})
                diurnal_temp = reg.evaluate(
                    m.DIURNAL_TEMPERATURE_OFFSET,
                    {m.DIURNAL_TEMPERATURE_AMPLITUDE: bl["diurnal_amp"],
                     m.DIURNAL_PHASE_COS: diurnal_cos})
                season_hum = reg.evaluate(
                    m.SEASONAL_HUMIDITY_OFFSET,
                    {m.SEASONAL_HUMIDITY_AMPLITUDE: bl["hum_seasonal_amp"],
                     m.SEASON_PHASE_COS: season_cos,
                     m.HUMIDITY_SHARPNESS: bl["sharpness"]})
                diurnal_hum = reg.evaluate(
                    m.DIURNAL_HUMIDITY_OFFSET,
                    {m.DIURNAL_HUMIDITY_AMPLITUDE: bl["hum_diurnal_amp"],
                     m.DIURNAL_PHASE_COS: diurnal_cos})
                sr = reg.evaluate(
                    m.SUNRISE_HOUR,
                    {m.SOLAR_LATITUDE_PROXY: bl["latitude"],
                     m.SOLAR_DECLINATION: decl})
                ss = reg.evaluate(
                    m.SUNSET_HOUR,
                    {m.SOLAR_LATITUDE_PROXY: bl["latitude"],
                     m.SOLAR_DECLINATION: decl})
                daylight = reg.evaluate(
                    m.DAYLIGHT_HOURS, {m.SUNRISE_HOUR: sr, m.SUNSET_HOUR: ss})
                temperature = reg.evaluate(
                    m.INSTANT_TEMPERATURE,
                    {m.ANNUAL_TEMPERATURE: bl["temperature"],
                     m.SEASONAL_TEMPERATURE_OFFSET: season_temp,
                     m.DIURNAL_TEMPERATURE_OFFSET: diurnal_temp,
                     m.FIELD_TEMPERATURE_PERTURBATION: perturb})
                humidity = reg.evaluate(
                    m.INSTANT_HUMIDITY,
                    {m.BASELINE_HUMIDITY: bl["humidity"],
                     m.SEASONAL_HUMIDITY_OFFSET: season_hum,
                     m.DIURNAL_HUMIDITY_OFFSET: diurnal_hum,
                     m.FIELD_HUMIDITY_PERTURBATION: hum_perturb})
                wind = reg.evaluate(
                    m.INSTANT_WIND_SPEED,
                    {m.BASELINE_WIND_SPEED: bl["wind"],
                     m.FIELD_WIND_PERTURBATION: wind_perturb,
                     m.FIELD_WIND_MULTIPLIER: multiplier})
                sunshine = reg.evaluate(
                    m.INSTANT_SUNSHINE,
                    {m.DAYLIGHT_HOURS: daylight,
                     m.FIELD_HUMIDITY_PERTURBATION: hum_perturb})
                reg_values = (
                    reg_day, reg_doy, reg_hour, season_temp, diurnal_temp,
                    season_hum, diurnal_hum, sr, ss, daylight, temperature,
                    humidity, wind, sunshine,
                )
                for got, want in zip(reg_values, ref):
                    if isinstance(got, int):
                        assert got == want
                    else:
                        assert got == pytest.approx(want, abs=1e-12)


class TestSourcelessPackagedBuild:
    """打包（Nuitka standalone，无 .py/.c）布局下注册表构造回归测试。

    发行物只有编译产物 + data/*.json + lang/*.json（.c 不分发）。
    源码模式缺失来源应 fail-closed（见 test_uninspectable...）；
    打包模式必须降级而非崩溃 —— 否则"进入世界"即崩（review 2026-09-08）。
    """

    def test_build_and_evaluate_in_sourceless_mode(self, monkeypatch):
        monkeypatch.setenv("ASCEND_SOURCELESS", "1")
        from ascend.causal.world import build_registry
        from ascend.weather.field import precip_threshold

        registry = build_registry()
        assert len(registry.mechanisms) == len(ASCEND_MECHANISMS.mechanisms)
        assert registry.evaluate(SOLAR_LATITUDE_PROXY,
                                 {SEA_LEVEL_TEMPERATURE: 15.0}) == pytest.approx(
            ASCEND_MECHANISMS.evaluate(SOLAR_LATITUDE_PROXY,
                                       {SEA_LEVEL_TEMPERATURE: 15.0}))
        assert precip_threshold(100.0) == pytest.approx(
            0.5456521739130435, abs=1e-12)

    def test_sourceless_versions_are_degraded_and_stable(self, monkeypatch):
        monkeypatch.setenv("ASCEND_SOURCELESS", "1")
        from ascend.causal.world import build_registry

        registry = build_registry()
        snapshots = registry.snapshot()["mechanisms"]
        assert snapshots, "无机制快照"
        for mechanism_id, snapshot in snapshots.items():
            assert snapshot["equation_version"].startswith(
                "sha256-packaged:"
            ), mechanism_id
        again = build_registry()
        assert {
            mechanism_id: snapshot["equation_version"]
            for mechanism_id, snapshot in again.snapshot()["mechanisms"].items()
        } == {
            mechanism_id: snapshot["equation_version"]
            for mechanism_id, snapshot in snapshots.items()
        }


class TestSnapshotPortability:
    """快照跨机器确定性：方程版本/快照不得含绝对路径。

    CI 检出目录与本地不同（/home/runner vs /home/Jedidiah），
    equations.json 曾因此漂移（CI run 34236383065）。
    """

    def test_source_dependency_labels_are_layout_relative(self):
        snapshot = ASCEND_MECHANISMS.snapshot()["mechanisms"]
        assert snapshot
        for mechanism_id, mechanism in snapshot.items():
            for dep in mechanism["source_dependencies"]:
                assert not dep.startswith("/"), (
                    f"{mechanism_id}: 依赖含绝对路径 {dep!r}"
                )
                if dep.startswith("file:"):
                    raise AssertionError(mechanism_id)

    def test_equation_versions_stable_across_checkout(self, monkeypatch,
                                                       tmp_path):
        """镜像另一检出位置（不同根、相同布局）→ 摘要不变。

        文件依赖哈希 = 内容 + 相对 backend 锚点的标签（不含绝对路径），
        callable 源码摘要在两处一致 → 版本跨机器确定。
        """
        import shutil
        from dataclasses import replace
        import ascend.causal.registry as registry
        import ascend.space.mechanisms as space_mech
        import ascend.weather.mechanisms as weather_mech
        from ascend.causal.microsteps import MICROSTEP_ORDER
        from ascend.causal import MechanismRegistry

        before = {
            mid: m["equation_version"]
            for mid, m in ASCEND_MECHANISMS.snapshot()["mechanisms"].items()
        }
        backend_root = Path(__file__).parents[2]   # backend/
        repo_root = Path(__file__).parents[3]      # 仓库根
        mirror_backend = tmp_path / "ci-mirror" / "backend"
        mirror_ascend = mirror_backend / "ascend"
        mirror_data = tmp_path / "ci-mirror" / "data"
        path_map: dict[str, Path] = {}
        for relative in (
            Path("ascend/space/_hydrology.c"),
            Path("ascend/space/hydrology.py"),
            Path("ascend/space/climate.py"),
            Path("ascend/space/biome.py"),
            Path("ascend/config.py"),
        ):
            target = mirror_ascend / relative.relative_to("ascend")
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(backend_root / relative, target)
            path_map[str(backend_root / relative)] = target
        mirror_data.mkdir(parents=True, exist_ok=True)
        for data_name in ("world.json", "climate.json"):
            shutil.copy2(repo_root / "data" / data_name,
                         mirror_data / data_name)
            path_map[str(repo_root / "data" / data_name)] = (
                mirror_data / data_name
            )

        # spec 在 import 期已捕获原始路径 → 用镜像路径重建空间切片 spec。
        moved_specs = []
        for spec in space_mech.WORLD_GEN_MECHANISM_SPECS:
            deps = tuple(
                path_map.get(str(dep), dep) for dep in spec.source_dependencies
            )
            moved_specs.append(replace(spec, source_dependencies=deps))

        monkeypatch.setattr(registry, "_ANCHOR", mirror_backend)
        moved = MechanismRegistry(
            schema_version=ASCEND_MECHANISMS.schema_version,
            declaration_id=ASCEND_MECHANISMS.declaration_id,
            declaration_version=ASCEND_MECHANISMS.declaration_version,
            microstep_order=MICROSTEP_ORDER,
            slice_boundary=ASCEND_MECHANISMS.slice_boundary,
            nodes=weather_mech.WEATHER_NODES + space_mech.WORLD_GEN_NODES,
            parameters=(
                weather_mech.WEATHER_PARAMETERS
                + space_mech.WORLD_GEN_PARAMETERS
            ),
            exogenous_sources=(),
            mechanisms=tuple(moved_specs)
            + weather_mech.WEATHER_MECHANISM_SPECS,
        )
        after = {
            mid: m["equation_version"]
            for mid, m in moved.snapshot()["mechanisms"].items()
        }
        assert after == before
