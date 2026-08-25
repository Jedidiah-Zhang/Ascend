"""跨领域事件契约统一测试。

覆盖 time/entity/game 各领域事件（weather 事件样本见 test_weather.py）：
  - as_dict 键 == dataclass 字段集合
  - event_type 全局唯一非空（含 weather 域，锁全项目碰撞）
  - as_dict 输出为 JSON 形状（tuple→list），可被 json.dumps 直接序列化
"""

import json

import pytest
from dataclasses import fields

from ascend.time.events import MinuteChange, HourChange, DayChange, DayEnd
from ascend.entity.events import (
    EntityBorn, EntityDied, EntityMoved, PlayerTeleported,
)
from ascend.game import WorldInitialized
from ascend.weather.events import (
    TemperatureChange, HumidityChange, WindChange, SunshineChange,
    PrecipitationStart, PrecipitationStop, SeasonChange, Sunrise, Sunset,
    ColdSnapStart, ColdSnapStop, HeatWaveStart, HeatWaveStop,
    StormStart, StormStop,
)


SAMPLES: dict[type, dict] = {
    MinuteChange: dict(day=1, hour=5, minute=30, game_time=36000),
    HourChange: dict(day=1, hour=6, previous_hour=5, hour_change_count=1),
    DayChange: dict(
        day=2, previous_day=1, elapsed_days=2,
        day_change_count=1, skipped_days=0,
    ),
    DayEnd: dict(day=1, elapsed_days=1),
    EntityBorn: dict(
        entity_id="abc", entity_type="CREATURE", controller="NONE",
        position=[3, 5, 2, 2], layer_id=0, x=98, y=102,
    ),
    EntityDied: dict(entity_id="abc", entity_type="CREATURE"),
    EntityMoved: dict(
        entity_id="abc", old_position=[3, 5, 2, 2],
        new_position=[4, 5, 0, 0], layer_id=0, x=100, y=102,
    ),
    PlayerTeleported: dict(x=120.5, y=88.0),
    WorldInitialized: dict(seed="2a", birth_chunk=[3, 5], loaded_chunks=9,
                           world_id="w1"),
}

# 全项目事件类清单（含 weather 域，供全局唯一性断言）
ALL_CLASSES = list(SAMPLES) + [
    TemperatureChange, HumidityChange, WindChange, SunshineChange,
    PrecipitationStart, PrecipitationStop, SeasonChange, Sunrise, Sunset,
    ColdSnapStart, ColdSnapStop, HeatWaveStart, HeatWaveStop,
    StormStart, StormStop,
]


class TestEventContracts:
    @pytest.mark.parametrize("cls", list(SAMPLES))
    def test_contract(self, cls):
        """as_dict 键 == 字段集合，样本值完整往返。"""
        ev = cls(**SAMPLES[cls])
        d = ev.as_dict()
        assert set(d) == {f.name for f in fields(cls)}
        for name, value in SAMPLES[cls].items():
            assert d[name] == value

    def test_event_types_unique_and_nonempty(self):
        """全项目 event_type 唯一非空（跨 time/entity/game/weather 域）。"""
        seen: dict[str, type] = {}
        for cls in ALL_CLASSES:
            assert cls.event_type, f"{cls.__name__}.event_type 为空"
            assert cls.event_type not in seen, \
                f"event_type 重复: {cls.event_type}"
            seen[cls.event_type] = cls

    def test_as_dict_is_json_serializable(self):
        """所有事件 data 可被 json.dumps 直接序列化（含 tuple 位置字段）。

        weather 域的 JSON 可序列化由 test_weather.py 的同类测试覆盖。
        """
        for cls in SAMPLES:
            d = cls(**SAMPLES[cls]).as_dict()
            json.dumps(d, ensure_ascii=False)
