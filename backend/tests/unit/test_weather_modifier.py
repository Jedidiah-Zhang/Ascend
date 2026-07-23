"""ModifierSchedule / WEATHER_MODIFIERS 单元测试。

覆盖：注册表完整性、事件队列（push 递增校验、激活判定、
效果计算、边界检测 pop_due）、队列维护（prune/replenish）、
generate_next 确定性与随机化范围、start 事件数据构造。
"""

import random

import pytest

from ascend.config import GAME_HOUR, GAME_YEAR
from ascend.space import ClimateZone
from ascend.weather.weather_modifier import (
    WEATHER_MODIFIERS,
    ModifierConfig,
    ModifierEvent,
    ModifierSchedule,
)


def _make_schedule(type_name="cold_snap", climate=ClimateZone.TEMPERATE_FOREST,
                   seed=42):
    """构造确定性 ModifierSchedule（所选气候带 rate 必须 > 0）。"""
    config = WEATHER_MODIFIERS[type_name]
    assert config.rates.get(climate, 0.0) > 0, "测试要求 rate > 0 的气候带"
    return ModifierSchedule(random.Random(seed), config, climate)


class TestModifierRegistry:
    """WEATHER_MODIFIERS 注册表完整性。"""

    def test_T1_type_name_matches_key(self):
        """注册表 key 与 config.type_name 一致。"""
        for key, config in WEATHER_MODIFIERS.items():
            assert key == config.type_name

    def test_T2_effect_is_known_category(self):
        """effect 只能是 temperature 或 multiplier。"""
        for config in WEATHER_MODIFIERS.values():
            assert config.effect in ("temperature", "multiplier")

    def test_T3_rates_nonnegative_and_cover_climates(self):
        """所有气候带事件率非负，且键都是 ClimateZone。"""
        for config in WEATHER_MODIFIERS.values():
            for climate, rate in config.rates.items():
                assert isinstance(climate, ClimateZone)
                assert rate >= 0.0

    def test_T4_positive_duration_and_schema(self):
        """mean_duration > 0，start_schema 含 time_of_day。"""
        for config in WEATHER_MODIFIERS.values():
            assert config.mean_duration > 0
            assert "time_of_day" in config.start_schema


class TestModifierEvent:
    """ModifierEvent 数据类。"""

    def test_T5_end_tick(self):
        """end_tick = start_tick + duration。"""
        ev = ModifierEvent(100, 50, "storm", 1.0)
        assert ev.end_tick == 150


class TestScheduleQueue:
    """事件队列操作。"""

    def test_T6_push_requires_increasing_start(self):
        """push 的 start_tick 必须严格递增，否则 ValueError。"""
        sched = _make_schedule()
        sched.push(ModifierEvent(100, 10, "cold_snap", 1.0))
        with pytest.raises(ValueError):
            sched.push(ModifierEvent(100, 10, "cold_snap", 1.0))
        with pytest.raises(ValueError):
            sched.push(ModifierEvent(50, 10, "cold_snap", 1.0))

    def test_T7_is_active_within_window(self):
        """is_active 判定 [start, end) 半开区间。"""
        sched = _make_schedule()
        sched.push(ModifierEvent(100, 50, "cold_snap", 1.0))
        assert not sched.is_active(99)
        assert sched.is_active(100)
        assert sched.is_active(149)
        assert not sched.is_active(150)

    def test_T8_pop_due_detects_transitions(self):
        """pop_due 仅在激活状态翻转时返回 True。"""
        sched = _make_schedule()
        sched.push(ModifierEvent(100, 50, "cold_snap", 1.0))
        sched.seed_current(0)

        assert sched.pop_due(50) is False   # inactive → inactive
        assert sched.pop_due(100) is True   # inactive → active
        assert sched.pop_due(120) is False  # active → active
        assert sched.pop_due(150) is True   # active → inactive

    def test_T9_prune_and_replenish(self):
        """prune_before 清除已结束事件，needs_replenish 判定队列深度。"""
        sched = _make_schedule()
        sched.push(ModifierEvent(100, 50, "cold_snap", 1.0))
        sched.push(ModifierEvent(200, 50, "cold_snap", 1.0))
        assert len(sched) == 2
        assert sched.latest_end_tick() == 250

        sched.prune_before(160)  # 第一场已结束（end=150）
        assert len(sched) == 1
        assert sched.needs_replenish(2)
        assert not sched.needs_replenish(1)

    def test_T10_latest_end_tick_empty_is_none(self):
        """空队列 latest_end_tick 返回 None。"""
        sched = _make_schedule()
        assert sched.latest_end_tick() is None


class TestScheduleEffects:
    """效果计算。"""

    def test_T11_temp_offset_only_for_temperature_effect(self):
        """temperature 类事件激活时输出偏移；multiplier 类恒为 0。"""
        cold = _make_schedule("cold_snap")
        cold.push(ModifierEvent(100, 50, "cold_snap", 1.0))
        offset = cold.temp_offset(120)
        assert offset == pytest.approx(-15.0)  # magnitude=1.0 × base=-15
        assert cold.temp_offset(50) == 0.0     # 未激活

        storm = _make_schedule("storm", ClimateZone.TEMPERATE_FOREST)
        storm.push(ModifierEvent(100, 50, "storm", 1.0))
        assert storm.temp_offset(120) == 0.0

    def test_T12_multiplier_only_for_multiplier_effect(self):
        """multiplier 类事件激活时输出倍率；temperature 类恒为 1.0。"""
        storm = _make_schedule("storm", ClimateZone.TEMPERATE_FOREST)
        storm.push(ModifierEvent(100, 50, "storm", 2.0))
        assert storm.wind_rain_multiplier(120) == pytest.approx(6.0)  # 2.0 × 3.0
        assert storm.wind_rain_multiplier(50) == 1.0  # 未激活

        cold = _make_schedule("cold_snap")
        cold.push(ModifierEvent(100, 50, "cold_snap", 1.0))
        assert cold.wind_rain_multiplier(120) == 1.0

    def test_T13_start_event_data_matches_schema(self):
        """start_event_data 按 config.start_schema 填充字段。"""
        cold = _make_schedule("cold_snap")
        cold.push(ModifierEvent(100, 50, "cold_snap", 1.0))
        data = cold.start_event_data(120)
        assert set(data.keys()) == set(cold._config.start_schema.keys())
        assert data["temperature_offset"] == pytest.approx(-15.0)

        storm = _make_schedule("storm", ClimateZone.TEMPERATE_FOREST)
        storm.push(ModifierEvent(100, 50, "storm", 1.0))
        data = storm.start_event_data(120)
        assert "wind_multiplier" in data
        assert "rain_multiplier" in data


class TestScheduleGeneration:
    """generate_next 随机化。"""

    def test_T14_deterministic_with_same_seed(self):
        """相同 seed 生成完全一致的事件序列。"""
        a = _make_schedule(seed=7)
        b = _make_schedule(seed=7)
        ev_a = a.generate_next(1000)
        ev_b = b.generate_next(1000)
        assert (ev_a.start_tick, ev_a.duration, ev_a.magnitude) == \
               (ev_b.start_tick, ev_b.duration, ev_b.magnitude)

    def test_T15_generated_event_within_bounds(self):
        """生成事件：间隔 0.5-1.5×均值、时长 ≥ 半小时、强度 0.5-1.5。"""
        sched = _make_schedule(seed=3)
        config = WEATHER_MODIFIERS["cold_snap"]
        rate = config.rates[ClimateZone.TEMPERATE_FOREST]
        mean_interval = GAME_YEAR / rate

        for _ in range(20):
            ev = sched.generate_next(0)
            assert 0.5 * mean_interval <= ev.start_tick <= 1.5 * mean_interval
            assert ev.duration >= GAME_HOUR // 2
            assert 0.5 <= ev.magnitude <= 1.5
            assert ev.type_name == "cold_snap"

    def test_T16_zero_rate_climate_gives_infinite_interval(self):
        """rate=0 的气候带 mean_interval 为 inf（调用方需保证不为其生成）。"""
        config = WEATHER_MODIFIERS["cold_snap"]
        assert config.rates[ClimateZone.DESERT] == 0.0
        sched = ModifierSchedule(random.Random(1), config, ClimateZone.DESERT)
        assert sched._mean_interval == float("inf")
