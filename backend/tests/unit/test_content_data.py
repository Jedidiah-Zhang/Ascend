"""内容数据测试（climate / biome）— 数据文件加载 + 契约校验 + i18n label。

Coverage: ascend.data.load_content + climate._build_climate_templates +
biome._build_biome_templates/_build_subdiv_configs。
"""

import pytest

from ascend.data import load_content
from ascend.space.climate import (
    ClimateZone,
    SeasonalityMode,
    get_climate_template,
    _build_climate_templates,
)
from ascend.space.biome import (
    BiomeType,
    get_template,
    _OCEAN_BIOMES,
    _SUBDIV_CONFIGS,
    _build_biome_templates,
    _build_subdiv_configs,
)
from ascend.config import GAME_DAY, GAME_HOUR
from ascend.weather.features import (
    FEATURE_TYPES,
    T_FRONT,
    T_STORM,
    T_COLD_SNAP,
    T_HEAT_WAVE,
    FeatureConfig,
    _build_feature_types,
)


def _climate_mini(zone: dict) -> dict:
    return {
        "version": 1,
        "climate": {
            "ascend:temperate_forest": {
                "value": 4,
                "label_key": "climate.temperate_forest",
                "humidity_range": [45.0, 80.0],
                "wind_speed_range": [0.0, 12.0],
                "seasonality": "four_season",
                "display_color": "#4a7c3f",
            },
            "ascend:desert": {
                "value": 2,
                "label_key": "climate.desert",
                "humidity_range": [5.0, 30.0],
                "wind_speed_range": [2.0, 15.0],
                "seasonality": "none",
                "display_color": "#e6c878",
            },
            **zone,
        },
    }


class TestClimateData:
    def test_climate_data_exists(self):
        doc = load_content("climate")
        assert doc["version"] == 1
        assert len(doc["climate"]) == 8

    def test_all_zones_loaded_with_enum_values(self):
        """数据 value 与枚举一致，label_key 已填充。"""
        for zone in ClimateZone:
            tmpl = get_climate_template(zone)
            assert tmpl.climate is zone
            assert zone.label_key.startswith("climate.")

    def test_values_contiguous(self):
        values = sorted(z.value for z in ClimateZone)
        assert values == list(range(8))

    def test_seasonality_resolved(self):
        assert get_climate_template(ClimateZone.TEMPERATE_FOREST).seasonality \
            == SeasonalityMode.FOUR_SEASON
        assert get_climate_template(ClimateZone.TROPICAL_SAVANNA).seasonality \
            == SeasonalityMode.MONSOON

    def test_label_resolves_via_i18n(self):
        """label 由 label_key 经 i18n 解析（非硬编码中文）。"""
        assert ClimateZone.ALPINE.label == "高山"
        assert ClimateZone.EQUATORIAL_RAINFOREST.label_key == "climate.equatorial_rainforest"

    def test_value_mismatch_rejected(self):
        doc = _climate_mini({})
        doc["climate"]["ascend:temperate_forest"]["value"] = 99
        with pytest.raises(ValueError, match="不一致"):
            _build_climate_templates(doc)

    def test_bad_seasonality_rejected(self):
        doc = _climate_mini({})
        doc["climate"]["ascend:temperate_forest"]["seasonality"] = "bogus"
        with pytest.raises(ValueError, match="seasonality"):
            _build_climate_templates(doc)

    def test_missing_zone_rejected(self):
        doc = _climate_mini({})
        del doc["climate"]["ascend:temperate_forest"]
        with pytest.raises(ValueError, match="缺气候档"):
            _build_climate_templates(doc)

    def test_missing_label_key_rejected(self):
        doc = _climate_mini({})
        del doc["climate"]["ascend:temperate_forest"]["label_key"]
        with pytest.raises(ValueError, match="label_key"):
            _build_climate_templates(doc)


def _biome_mini(extra: dict) -> dict:
    return {
        "version": 1,
        "biome": {
            "ascend:temperate_deciduous_forest": {
                "value": 9,
                "label_key": "biome.temperate_deciduous_forest",
                "climate": "ascend:temperate_forest",
                "water_ratio": 0.08,
                "mountain_ratio": 0.05,
                "tree_density": 0.70,
                "terrain_bias": {},
                "creature_weights": {},
                "resource_weights": {},
                "ocean": False,
            },
            "ascend:temperate_mixed_forest": {
                "value": 8,
                "label_key": "biome.temperate_mixed_forest",
                "climate": "ascend:temperate_forest",
                "water_ratio": 0.10,
                "mountain_ratio": 0.08,
                "tree_density": 0.65,
                "terrain_bias": {},
                "creature_weights": {},
                "resource_weights": {},
                "ocean": False,
            },
            **extra,
        },
        "subdiv": {
            "ascend:temperate_forest": {
                "dimension": "temperature",
                "low": "ascend:temperate_mixed_forest",
                "high": "ascend:temperate_deciduous_forest",
                "value_min": 5.0,
                "value_max": 20.0,
            },
            "ascend:desert": {
                "dimension": "moisture",
                "low": "ascend:sandy_desert",
                "high": "ascend:rocky_desert",
                "value_min": -1.0,
                "value_max": 1.0,
            },
        },
    }


class TestBiomeData:
    def test_biome_data_exists(self):
        doc = load_content("biome")
        assert doc["version"] == 1
        assert len(doc["biome"]) == 19
        assert len(doc["subdiv"]) == 8

    def test_all_biomes_loaded_with_enum_values(self):
        for biome in BiomeType:
            tmpl = get_template(biome)
            assert tmpl.biome_type is biome
            assert biome.label_key.startswith("biome.")

    def test_values_contiguous(self):
        values = sorted(b.value for b in BiomeType)
        assert values == list(range(19))

    def test_ocean_derived_from_flag(self):
        """海洋群系由数据 ocean 标志派生。"""
        assert _OCEAN_BIOMES == frozenset({
            BiomeType.WARM_OCEAN, BiomeType.TEMPERATE_OCEAN, BiomeType.COLD_OCEAN,
        })
        assert BiomeType.WARM_OCEAN.is_ocean
        assert not BiomeType.TUNDRA.is_ocean
    def test_subdiv_covers_all_climate_zones(self):
        assert set(_SUBDIV_CONFIGS) == set(ClimateZone)

    def test_subdiv_low_high_resolve(self):
        cfg = _SUBDIV_CONFIGS[ClimateZone.TEMPERATE_FOREST]
        assert cfg.low is BiomeType.TEMPERATE_MIXED_FOREST
        assert cfg.high is BiomeType.TEMPERATE_DECIDUOUS_FOREST
        assert cfg.dimension == "temperature"

    def test_label_resolves_via_i18n(self):
        assert BiomeType.TROPICAL_RAINFOREST.label == "热带雨林"
        assert BiomeType.COLD_OCEAN.label == "冷水海洋"

    def test_value_mismatch_rejected(self):
        doc = _biome_mini({
            "ascend:temperate_mixed_forest": {
                "value": 99, "label_key": "x", "climate": "ascend:temperate_forest",
                "ocean": False,
            },
        })
        with pytest.raises(ValueError, match="不一致"):
            _build_biome_templates(doc)

    def test_missing_biome_rejected(self):
        doc = _biome_mini({})
        del doc["biome"]["ascend:temperate_deciduous_forest"]
        with pytest.raises(ValueError, match="缺群系"):
            _build_biome_templates(doc)

    def test_bad_climate_ref_rejected(self):
        doc = _biome_mini({})
        doc["biome"]["ascend:temperate_deciduous_forest"]["climate"] = "ascend:nope"
        with pytest.raises(KeyError):
            _build_biome_templates(doc)

    def test_missing_subdiv_rejected(self):
        doc = _biome_mini({})
        del doc["subdiv"]["ascend:temperate_forest"]
        with pytest.raises(ValueError, match="缺细分配置"):
            _build_subdiv_configs(doc)


def _feature_mini(extra: dict) -> dict:
    return {
        "version": 1,
        "features": {
            "ascend:storm": {
                "effect": "multiplier",
                "rates": {"ascend:temperate_forest": 1.0},
                "mean_duration": {"hours": 6},
                "base_intensity": 3.0,
                "radius_range": [1500.0, 3000.0],
                "speed_range": [2.0, 5.0],
                "precip_boost": 1.0,
                "events": {"start": "storm_start", "stop": "storm_stop"},
            },
            **extra,
        },
    }


class TestWeatherFeatureData:
    def test_weather_data_exists(self):
        doc = load_content("weather")
        assert doc["version"] == 1
        assert len(doc["features"]) == 4

    def test_all_feature_types_loaded(self):
        assert set(FEATURE_TYPES) == {"cold_snap", "heat_wave", "storm", "front"}
        assert FEATURE_TYPES[T_COLD_SNAP].type_name == "cold_snap"

    def test_duration_units_converted(self):
        """days/hours → tick 转换。"""
        assert FEATURE_TYPES[T_COLD_SNAP].mean_duration == 3 * GAME_DAY
        assert FEATURE_TYPES[T_STORM].mean_duration == 6 * GAME_HOUR
        assert FEATURE_TYPES[T_FRONT].mean_duration == 10 * GAME_HOUR

    def test_event_classes_mapped(self):
        """事件类由代码侧映射（null → None）。"""
        assert FEATURE_TYPES[T_COLD_SNAP].start_event_cls.__name__ == "ColdSnapStart"
        assert FEATURE_TYPES[T_FRONT].start_event_cls is None

    def test_rates_resolve_climate_zones(self):
        assert FEATURE_TYPES[T_COLD_SNAP].rates[ClimateZone.POLAR_TUNDRA] == 2.0
        assert FEATURE_TYPES[T_HEAT_WAVE].rates[ClimateZone.DESERT] == 2.0

    def test_type_name_is_local_part(self):
        """运行时 type_name = 数据键 local 部分（协议标识）。"""
        assert FEATURE_TYPES[T_STORM].type_name == "storm"

    def test_bad_effect_rejected(self):
        doc = _feature_mini({})
        doc["features"]["ascend:storm"]["effect"] = "bogus"
        with pytest.raises(ValueError, match="effect"):
            _build_feature_types(doc)

    def test_bad_duration_rejected(self):
        doc = _feature_mini({})
        doc["features"]["ascend:storm"]["mean_duration"] = {"weeks": 1}
        with pytest.raises(ValueError, match="mean_duration"):
            _build_feature_types(doc)

    def test_unknown_event_class_rejected(self):
        doc = _feature_mini({})
        doc["features"]["ascend:storm"]["events"]["start"] = "bogus"
        with pytest.raises(ValueError, match="事件类"):
            _build_feature_types(doc)

    def test_duplicate_local_name_rejected(self):
        doc = _feature_mini({
            "mymod:storm": {
                "effect": "multiplier",
                "rates": {}, "mean_duration": {"hours": 1},
                "base_intensity": 1.0, "radius_range": [1.0, 2.0],
                "speed_range": [1.0, 2.0], "events": {},
            },
        })
        with pytest.raises(ValueError, match="local 名重复"):
            _build_feature_types(doc)
