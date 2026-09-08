"""因果机制注册表 P1 契约测试。"""

from dataclasses import replace
import json
from pathlib import Path

import pytest

from ascend.causal import MechanismRegistry
from ascend.weather import derive
from ascend.weather.mechanisms import (
    ANNUAL_RAINFALL,
    ANNUAL_TEMPERATURE,
    INSTANT_PRECIPITATION_TYPE,
    INSTANT_TEMPERATURE,
    SEASONAL_TEMPERATURE_AMPLITUDE,
    SEA_LEVEL_TEMPERATURE,
    SOLAR_LATITUDE_PROXY,
    WEATHER_MECHANISMS,
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
        "schema_version": WEATHER_MECHANISMS.schema_version,
        "declaration_id": WEATHER_MECHANISMS.declaration_id,
        "declaration_version": WEATHER_MECHANISMS.declaration_version,
        "microstep_order": WEATHER_MECHANISMS.microstep_order,
        "slice_boundary": WEATHER_MECHANISMS.slice_boundary,
        "nodes": tuple(WEATHER_MECHANISMS.nodes.values()),
        "parameters": tuple(WEATHER_MECHANISMS.parameters.values()),
        "exogenous_sources": tuple(
            WEATHER_MECHANISMS.exogenous_sources.values()
        ),
        "mechanisms": tuple(WEATHER_MECHANISMS.mechanisms.values()),
    }
    values.update(changes)
    return MechanismRegistry(**values)


class TestRegistryValidation:
    def test_weather_slice_passes_c0_and_c1(self):
        assert WEATHER_MECHANISMS.validate_c0() == ()
        assert WEATHER_MECHANISMS.validate_c1() == ()
        assert set(WEATHER_MECHANISMS.nodes) == EXPECTED_NODES

    def test_duplicate_node_is_rejected(self):
        nodes = tuple(WEATHER_MECHANISMS.nodes.values())
        with pytest.raises(ValueError, match="重复节点"):
            _rebuild_registry(nodes=nodes + (nodes[0],))

    def test_duplicate_parameter_is_rejected(self):
        parameters = tuple(WEATHER_MECHANISMS.parameters.values())
        with pytest.raises(ValueError, match="重复参数"):
            _rebuild_registry(parameters=parameters + (parameters[0],))

    def test_parameter_value_type_mismatch_is_rejected(self):
        parameters = tuple(WEATHER_MECHANISMS.parameters.values())
        for bad_value in ("abc", True):
            broken = tuple(
                replace(item, value=bad_value) if item is parameters[0] else item
                for item in parameters
            )
            with pytest.raises(ValueError, match="与声明类型 float 不符|参数值域"):
                _rebuild_registry(parameters=broken)

    def test_parameter_bounds_inverted_is_rejected(self):
        parameters = tuple(WEATHER_MECHANISMS.parameters.values())
        broken = tuple(
            replace(item, bounds=(1.0, -1.0)) if item is parameters[0] else item
            for item in parameters
        )
        with pytest.raises(ValueError, match="bounds 倒置"):
            _rebuild_registry(parameters=broken)

    def test_uninspectable_equation_source_is_rejected(self):
        """打包/动态环境无源码可读时拒绝生成无来源版本（fail-closed）。"""
        mechanism = WEATHER_MECHANISMS.mechanism_for(SOLAR_LATITUDE_PROXY)
        function = eval(  # noqa: S307  无源码路径的刻意构造
            "lambda sea_level_temperature, input_min, input_max, "
            "output_min, output_max: 0.0"
        )
        broken = replace(mechanism, function=function)
        mechanisms = tuple(
            broken if item.mechanism_id == broken.mechanism_id else item
            for item in WEATHER_MECHANISMS.mechanisms.values()
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
        mechanisms = tuple(WEATHER_MECHANISMS.mechanisms.values())[1:]
        with pytest.raises(ValueError, match="没有写者"):
            _rebuild_registry(mechanisms=mechanisms)

    def test_slice_boundary_node_with_writer_is_rejected(self):
        boundary = next(
            item for item in WEATHER_MECHANISMS.nodes.values()
            if item.origin == "slice_boundary"
        )
        mechanisms = tuple(WEATHER_MECHANISMS.mechanisms.values())
        first = mechanisms[0]
        forged = replace(first, output=boundary.node_id)
        with pytest.raises(ValueError, match="节点不是 mechanism origin"):
            _rebuild_registry(mechanisms=(forged, *mechanisms[1:]))

    def test_same_microstep_parent_is_rejected(self):
        mechanism = WEATHER_MECHANISMS.mechanism_for(SOLAR_LATITUDE_PROXY)
        parent = replace(
            mechanism.parents[0],
            source_microstep="weather.chunk_derived",
        )
        broken = replace(mechanism, parents=(parent,))
        mechanisms = tuple(
            broken if item.mechanism_id == broken.mechanism_id else item
            for item in WEATHER_MECHANISMS.mechanisms.values()
        )
        with pytest.raises(ValueError, match="更早微步"):
            _rebuild_registry(mechanisms=mechanisms)

    def test_callable_signature_drift_is_rejected(self):
        mechanism = next(iter(WEATHER_MECHANISMS.mechanisms.values()))
        broken = replace(mechanism, function=lambda: 0.0)
        mechanisms = tuple(
            broken if item.mechanism_id == broken.mechanism_id else item
            for item in WEATHER_MECHANISMS.mechanisms.values()
        )
        with pytest.raises(ValueError, match="函数签名"):
            _rebuild_registry(mechanisms=mechanisms)

    def test_stale_c1_witness_is_rejected(self):
        mechanism = WEATHER_MECHANISMS.mechanism_for(
            SOLAR_LATITUDE_PROXY
        )
        witness = replace(
            mechanism.witnesses[0],
            expected_outputs=(79.0, 1.0),
        )
        broken = replace(mechanism, witnesses=(witness,))
        mechanisms = tuple(
            broken if item.mechanism_id == broken.mechanism_id else item
            for item in WEATHER_MECHANISMS.mechanisms.values()
        )
        with pytest.raises(ValueError, match="C1"):
            _rebuild_registry(mechanisms=mechanisms)

    def test_malformed_witness_shape_is_rejected(self):
        mechanism = WEATHER_MECHANISMS.mechanism_for(SOLAR_LATITUDE_PROXY)
        witness = replace(mechanism.witnesses[0], expected_outputs=(80.0,))
        broken = replace(mechanism, witnesses=(witness,))
        mechanisms = tuple(
            broken if item.mechanism_id == broken.mechanism_id else item
            for item in WEATHER_MECHANISMS.mechanisms.values()
        )
        with pytest.raises(ValueError, match="恰为二元"):
            _rebuild_registry(mechanisms=mechanisms)

    def test_registry_is_immutable(self):
        with pytest.raises(AttributeError, match="不可变"):
            WEATHER_MECHANISMS.microstep_order = ("x",)
        with pytest.raises(AttributeError, match="不可变"):
            WEATHER_MECHANISMS.new_attr = 1


class TestWeatherMechanisms:
    def test_each_parent_has_a_version_bound_witness(self):
        snapshot = WEATHER_MECHANISMS.snapshot()
        for mechanism in snapshot["mechanisms"].values():
            witnessed = {item["parent"] for item in mechanism["witnesses"]}
            parents = {item["parent"] for item in mechanism["parents"]}
            assert witnessed == parents
            assert all(
                item["equation_version"] == mechanism["equation_version"]
                for item in mechanism["witnesses"]
            )

    def test_operational_edges_are_structural(self):
        edges = WEATHER_MECHANISMS.snapshot()["edges"]
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
        assert WEATHER_MECHANISMS.evaluate(
            SOLAR_LATITUDE_PROXY,
            {SEA_LEVEL_TEMPERATURE: -5.0},
        ) == pytest.approx(80.0)
        assert WEATHER_MECHANISMS.evaluate(
            SOLAR_LATITUDE_PROXY,
            {SEA_LEVEL_TEMPERATURE: 35.0},
        ) == pytest.approx(0.0)
        assert WEATHER_MECHANISMS.evaluate(
            SEASONAL_TEMPERATURE_AMPLITUDE,
            {ANNUAL_TEMPERATURE: 15.0, ANNUAL_RAINFALL: 200.0},
        ) == pytest.approx(18.6)
        assert WEATHER_MECHANISMS.evaluate(
            SEASONAL_TEMPERATURE_AMPLITUDE,
            {ANNUAL_TEMPERATURE: 15.0, ANNUAL_RAINFALL: 2000.0},
        ) == pytest.approx(15.0)
        assert WEATHER_MECHANISMS.evaluate(
            INSTANT_PRECIPITATION_TYPE,
            {INSTANT_TEMPERATURE: -0.049},
        ) == "snow"
        assert WEATHER_MECHANISMS.evaluate(
            INSTANT_PRECIPITATION_TYPE,
            {INSTANT_TEMPERATURE: 0.051},
        ) == "rain"

    def test_evaluation_requires_exact_legal_parent_values(self):
        with pytest.raises(KeyError, match="父输入"):
            WEATHER_MECHANISMS.evaluate(SOLAR_LATITUDE_PROXY, {})
        with pytest.raises(ValueError, match="值域"):
            WEATHER_MECHANISMS.evaluate(
                INSTANT_PRECIPITATION_TYPE,
                {INSTANT_TEMPERATURE: 1000.0},
            )

    def test_evaluate_rejects_bool_and_nonfinite_for_float_node(self):
        import math
        for bad in (True, False, math.nan, math.inf):
            with pytest.raises(ValueError, match="值域"):
                WEATHER_MECHANISMS.evaluate(
                    SOLAR_LATITUDE_PROXY,
                    {SEA_LEVEL_TEMPERATURE: bad},
                )

    def test_public_derive_functions_raise_out_of_domain(self):
        with pytest.raises(ValueError):
            derive.precip_type_for(1000.0)
        with pytest.raises(ValueError):
            derive.derive_latitude(100.0)
        with pytest.raises(ValueError):
            derive.derive_seasonal_amp(100.0, 800.0)

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

        monkeypatch.setattr(derive, "WEATHER_MECHANISMS", FakeRegistry())
        result = getattr(derive, function_name)(*inputs.values())
        assert result == expected
        assert calls == [(output, inputs)]


class TestRegistrySnapshot:
    def test_snapshot_has_explicit_c0_sections_and_stable_hash(self):
        snapshot = WEATHER_MECHANISMS.snapshot()
        assert snapshot["schema_version"] == 2
        assert snapshot["version"] == 2
        assert snapshot["declaration"]["hash"].startswith("sha256:")
        assert snapshot == WEATHER_MECHANISMS.snapshot()
        assert set(snapshot["nodes"]) == EXPECTED_NODES
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
        assert committed == WEATHER_MECHANISMS.snapshot()
