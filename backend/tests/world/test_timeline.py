"""干预时间线测试— 计划 / 撤销 / 只追加记录 / 持久化 / 声明校验。

对应《世界契约》WC-6（干预）与 D2：

- 逐帧独立记录、只追加；撤销 = 后续帧不再记录，既有记录不改写；
- 求值解析只看记录，不看"当前表"；
- 运行内机制替换已废除（WC-1.3）。

校验以编译后的 wired 程序为唯一事实源（引擎求值面 = 可干预面）：
未接线分量/未被消费参数/越域值一律拒绝，存档恢复同样重走校验。
"""

from __future__ import annotations

import dataclasses

import pytest

from ascend.world.modules.weather.core import WeatherCore
from ascend.world.research.timeline import (
    InterventionTimeline,
    PlannedIntervention,
    default_duration,
)

NODE = "weather.chunk.precipitation_threshold"
GLOBAL_NODE = "weather.tick.day"
UNWIRED_NODE = "weather.chunk.seasonal_temperature_amplitude_c"
PARAM = "world.parameter.game_day_ticks"

_PROGRAM = None


def _program():
    """wired 天气程序（引擎求值面；模块级缓存，程序不可变）。"""
    global _PROGRAM
    if _PROGRAM is None:
        _PROGRAM = WeatherCore().program
    return _PROGRAM


def _timeline(**kwargs) -> InterventionTimeline:
    return InterventionTimeline(_program(), **kwargs)


def _node_entry(
    value: float = 0.4,
    *,
    start: int = 0,
    stop: int | None = None,
    instance: tuple = (0, 0),
    target: str = NODE,
    source: str = "test",
) -> PlannedIntervention:
    return PlannedIntervention(
        target_space="node", target=target, instance=instance,
        value=value, start_frame=start, stop_frame=stop, source=source,
    )


class TestPlanValidation:
    def test_plan_stamps_seq_and_submitted_at(self):
        timeline = _timeline(now=lambda: 42)
        entry = timeline.plan(_node_entry())
        assert entry.seq == 1
        assert entry.submitted_at == 42
        assert timeline.plan_entries() == (entry,)

    def test_empty_windows_rejected(self):
        timeline = _timeline()
        with pytest.raises(ValueError):
            timeline.plan(_node_entry(start=5, stop=5))
        with pytest.raises(ValueError):
            timeline.plan(_node_entry(start=5, stop=4))

    def test_negative_frames_rejected(self):
        timeline = _timeline()
        with pytest.raises(ValueError):
            timeline.plan(_node_entry(start=-1))

    def test_unknown_space_rejected(self):
        timeline = _timeline()
        with pytest.raises(ValueError):
            timeline.plan(PlannedIntervention(
                target_space="mechanism", target=NODE, value=1.0,
            ))

    def test_unwired_node_rejected(self):
        timeline = _timeline()
        with pytest.raises(ValueError):
            timeline.plan(_node_entry(target=UNWIRED_NODE))

    def test_node_outside_program_rejected(self):
        """程序绑定求值面：完整模块里存在、wired 子集里不存在的分量拒绝。"""
        timeline = _timeline()
        with pytest.raises(ValueError, match="未声明"):
            timeline.plan(_node_entry(
                target="weather.instant.precipitation_type", value=0.4,
            ))

    def test_out_of_domain_value_rejected(self):
        timeline = _timeline()
        with pytest.raises(ValueError):
            timeline.plan(_node_entry(value=2.0))

    def test_int_domain_rejects_float(self):
        timeline = _timeline()
        with pytest.raises(ValueError, match="值域"):
            timeline.plan(_node_entry(target=GLOBAL_NODE, value=3.5,
                                      instance=()))

    def test_enum_domain_rejects_unknown_choice(self):
        timeline = _timeline()
        with pytest.raises(ValueError, match="enum"):
            timeline.plan(_node_entry(
                target="weather.tick.season", value=9, instance=(),
            ))

    def test_missing_value_rejected(self):
        timeline = _timeline()
        with pytest.raises(ValueError):
            timeline.plan(_node_entry(value=None))

    def test_instance_shape_rejected(self):
        timeline = _timeline()
        with pytest.raises(ValueError):
            timeline.plan(_node_entry(instance=(0,)))
        with pytest.raises(ValueError):
            timeline.plan(_node_entry(instance=("x", 0)))

    def test_instance_existence_checked(self):
        timeline = _timeline(
            instance_exists=lambda target, instance: False,
        )
        with pytest.raises(ValueError):
            timeline.plan(_node_entry())

    def test_global_node_needs_empty_instance(self):
        timeline = _timeline()
        with pytest.raises(ValueError):
            timeline.plan(
                _node_entry(target=GLOBAL_NODE, instance=(0, 0)),
            )
        entry = timeline.plan(
            _node_entry(target=GLOBAL_NODE, value=3, instance=()),
        )
        assert entry.instance == ()

    def test_node_duration_rules(self):
        timeline = _timeline()
        assert timeline.plan(_node_entry(start=0, stop=1)).seq == 1
        assert timeline.plan(_node_entry(start=0, stop=9)).seq == 2
        assert timeline.plan(_node_entry(start=0, stop=None)).seq == 3

    def test_parameter_only_forever(self):
        timeline = _timeline()
        with pytest.raises(ValueError):
            timeline.plan(PlannedIntervention(
                target_space="parameter", target=PARAM, value=100.0,
                start_frame=0, stop_frame=1,
            ))
        entry = timeline.plan(PlannedIntervention(
            target_space="parameter", target=PARAM, value=100.0,
            start_frame=0, stop_frame=None,
        ))
        assert entry.seq == 1

    def test_parameter_must_be_consumed(self):
        timeline = _timeline()
        with pytest.raises(ValueError):
            timeline.plan(PlannedIntervention(
                target_space="parameter",
                target="world.parameter.diurnal_to_seasonal_ratio",
                value=0.5,
            ))

    def test_consumed_parameters_match_wired_face(self):
        timeline = _timeline()
        consumed = timeline.consumed_parameters
        assert PARAM in consumed
        assert "world.parameter.diurnal_to_seasonal_ratio" not in consumed
        assert timeline.parameter_decl(PARAM) is not None
        assert timeline.parameter_decl("weather.parameter.ghost") is None

    def test_feature_requires_chunk_instance(self):
        timeline = _timeline()
        with pytest.raises(ValueError):
            timeline.plan(PlannedIntervention(
                target_space="field_feature", target="storm",
                instance=(0,), value={"active": True},
            ))
        entry = timeline.plan(PlannedIntervention(
            target_space="field_feature", target="storm",
            instance=(1, 2),
            value={"active": True, "spec": {
                "center_x": 100.0, "center_y": 200.0, "radius": 3000.0,
                "magnitude": 1.0, "born_tick": 0, "duration": None,
                "vel_x": 0.0, "vel_y": 0.0,
            }},
        ))
        assert entry.seq == 1

    def test_feature_active_requires_spec(self):
        """active=True 必须携带核规格（读档投影事实源，WC-6.5 / #51）。"""
        timeline = _timeline()
        with pytest.raises(ValueError, match="核规格"):
            timeline.plan(PlannedIntervention(
                target_space="field_feature", target="storm",
                instance=(1, 2), value={"active": True},
            ))
        with pytest.raises(ValueError, match="核规格"):
            timeline.plan(PlannedIntervention(
                target_space="field_feature", target="storm",
                instance=(1, 2), value={"active": bool("x"), "spec": {}},
            ))

    def test_feature_spec_rejects_non_finite(self):
        timeline = _timeline()
        with pytest.raises(ValueError, match="有限值"):
            timeline.plan(PlannedIntervention(
                target_space="field_feature", target="storm",
                instance=(1, 2),
                value={"active": True, "spec": {
                    "center_x": 0.0, "center_y": 0.0, "radius": float("nan"),
                    "magnitude": 1.0, "born_tick": 0, "duration": None,
                    "vel_x": 0.0, "vel_y": 0.0,
                }},
            ))

    def test_mechanism_replacement_is_gone(self):
        field_names = {
            item.name for item in dataclasses.fields(PlannedIntervention)
        }
        assert "mechanism" not in field_names
        assert "rep" not in field_names
        assert not hasattr(InterventionTimeline, "commit")
        assert not hasattr(InterventionTimeline, "clear")


class TestResolution:
    def test_resolve_materializes_record(self):
        timeline = _timeline(now=lambda: 7)
        timeline.plan(_node_entry(value=0.4))
        resolution = timeline.resolve_node(NODE, (0, 0), 3)
        assert resolution.rep == "value"
        assert resolution.value == 0.4
        record = timeline.record_at("node", NODE, (0, 0), 3)
        assert record is resolution.record
        assert record.seq == 1
        assert record.applied_at == 7
        assert record.source == "test"
        assert len(timeline.records()) == 1
        assert timeline.history_plain()[0]["frame"] == 3

    def test_resolve_outside_window_is_empty(self):
        timeline = _timeline()
        timeline.plan(_node_entry(value=0.4, start=5, stop=8))
        assert timeline.resolve_node(NODE, (0, 0), 4).record is None
        assert timeline.resolve_node(NODE, (0, 0), 8).record is None
        assert timeline.records() == ()
        assert timeline.resolve_node(NODE, (0, 0), 5).value == 0.4
        assert len(timeline.records()) == 1

    def test_materialize_is_idempotent(self):
        timeline = _timeline()
        timeline.plan(_node_entry(value=0.4))
        first = timeline.resolve_node(NODE, (0, 0), 2).record
        second = timeline.resolve_node(NODE, (0, 0), 2).record
        assert first is second
        assert len(timeline.records()) == 1

    def test_later_plan_wins_same_frame(self):
        timeline = _timeline()
        timeline.plan(_node_entry(value=0.3))
        timeline.plan(_node_entry(value=0.5))
        assert timeline.resolve_node(NODE, (0, 0), 0).value == 0.5
        assert len(timeline.plan_entries()) == 2

    def test_parameter_resolution(self):
        timeline = _timeline()
        timeline.plan(PlannedIntervention(
            target_space="parameter", target=PARAM, value=1000.0,
            start_frame=2, stop_frame=None,
        ))
        assert timeline.resolve_parameter(PARAM, 1) == (False, None)
        assert timeline.resolve_parameter(PARAM, 2) == (True, 1000.0)
        assert len(timeline.records()) == 1

    def test_resolution_order_independent(self):
        forward = _timeline()
        backward = _timeline()
        for timeline in (forward, backward):
            timeline.plan(_node_entry(value=0.4))
        for frame in range(6):
            forward.resolve_node(NODE, (0, 0), frame)
        for frame in reversed(range(6)):
            backward.resolve_node(NODE, (0, 0), frame)
        for frame in range(6):
            assert forward.record_at("node", NODE, (0, 0), frame).value == \
                backward.record_at("node", NODE, (0, 0), frame).value


class TestRevoke:
    def test_revoke_stops_future_but_keeps_history(self):
        timeline = _timeline(now=lambda: 0)
        timeline.plan(_node_entry(value=0.4))
        assert timeline.resolve_node(NODE, (0, 0), 5).value == 0.4
        assert timeline.revoke("node", NODE, (0, 0), at_frame=10) == 1
        assert timeline.resolve_node(NODE, (0, 0), 5).value == 0.4
        assert timeline.resolve_node(NODE, (0, 0), 10).record is None
        assert timeline.resolve_node(NODE, (0, 0), 12).record is None
        assert len(timeline.records()) == 1

    def test_revoke_materializes_active_past_frames(self):
        timeline = _timeline(now=lambda: 0)
        timeline.plan(_node_entry(value=0.4))
        timeline.revoke("node", NODE, (0, 0), at_frame=10)
        frame7 = timeline.resolve_node(NODE, (0, 0), 7)
        assert frame7.value == 0.4

    def test_revoke_returns_count_and_is_idempotent(self):
        timeline = _timeline(now=lambda: 0)
        timeline.plan(_node_entry(value=0.3))
        timeline.plan(_node_entry(value=0.5))
        assert timeline.revoke("node", NODE, (0, 0)) == 2
        assert timeline.revoke("node", NODE, (0, 0)) == 0
        assert timeline.revoke("node", NODE, (9, 9)) == 0

    def test_revoke_truncates_open_window(self):
        timeline = _timeline(now=lambda: 10)
        timeline.plan(_node_entry(value=0.3, start=0, stop=100))
        assert timeline.revoke("node", NODE, (0, 0)) == 1
        assert timeline.resolve_node(NODE, (0, 0), 9).value == 0.3
        assert timeline.resolve_node(NODE, (0, 0), 10).record is None

    def test_revoke_cancels_future_window(self):
        timeline = _timeline(now=lambda: 10)
        timeline.plan(_node_entry(value=0.3, start=50, stop=60))
        assert timeline.revoke("node", NODE, (0, 0)) == 1
        assert timeline.resolve_node(NODE, (0, 0), 50).record is None

    def test_revoke_leaves_finished_window(self):
        timeline = _timeline(now=lambda: 10)
        timeline.plan(_node_entry(value=0.3, start=0, stop=5))
        assert timeline.revoke("node", NODE, (0, 0)) == 0
        assert timeline.resolve_node(NODE, (0, 0), 3).value == 0.3

    def test_revoke_takes_effect_immediately(self):
        timeline = _timeline(now=lambda: 100)
        timeline.plan(_node_entry(value=0.4, start=0))
        assert timeline.revoke("node", NODE, (0, 0)) == 1
        assert timeline.resolve_node(NODE, (0, 0), 99).value == 0.4
        assert timeline.resolve_node(NODE, (0, 0), 100).record is None
        assert timeline.resolve_node(NODE, (0, 0), 101).record is None


class TestSnapshot:
    def test_snapshot_projects_active_plans(self):
        timeline = _timeline()
        timeline.plan(_node_entry(value=0.3, start=0, stop=1))
        timeline.plan(_node_entry(value=0.5, start=0, stop=None))
        snapshot = timeline.snapshot(0)
        assert len(snapshot) == 1
        assert snapshot[0]["value"] == 0.5
        assert timeline.snapshot(5) == [snapshot[0]]


class TestPersistence:
    def test_persist_shape_and_roundtrip(self):
        timeline = _timeline(now=lambda: 3)
        timeline.plan(_node_entry(value=0.4))
        timeline.resolve_node(NODE, (0, 0), 1)
        payload = timeline.persist()
        assert set(payload) == {"plan", "records"}
        assert len(payload["plan"]) == 1
        assert len(payload["records"]) == 1

        restored = _timeline(now=lambda: 3)
        assert restored.restore(payload) == 2
        assert restored.persist() == payload
        assert restored.resolve_node(NODE, (0, 0), 1).value == 0.4
        assert restored.resolve_node(NODE, (0, 0), 9).value == 0.4

    def test_persist_records_sorted_by_frame(self):
        timeline = _timeline()
        timeline.plan(_node_entry(value=0.4))
        for frame in (3, 1, 2):
            timeline.resolve_node(NODE, (0, 0), frame)
        frames = [item["frame"] for item in timeline.persist()["records"]]
        assert frames == [1, 2, 3]

    def test_restore_rejects_non_mapping(self):
        with pytest.raises(ValueError):
            _timeline().restore([])

    def test_restore_rejects_unknown_payload_field(self):
        timeline = _timeline()
        timeline.plan(_node_entry(value=0.4))
        payload = timeline.persist()
        payload["extra"] = 1
        with pytest.raises(ValueError):
            _timeline().restore(payload)

    def test_restore_rejects_unknown_entry_field(self):
        timeline = _timeline()
        timeline.plan(_node_entry(value=0.4))
        payload = timeline.persist()
        payload["plan"][0]["extra"] = 1
        with pytest.raises(ValueError):
            _timeline().restore(payload)

    def test_restore_rejects_missing_entry_field(self):
        timeline = _timeline()
        timeline.plan(_node_entry(value=0.4))
        payload = timeline.persist()
        del payload["plan"][0]["source"]
        with pytest.raises(ValueError):
            _timeline().restore(payload)

    def test_restore_rejects_tampered_value(self):
        timeline = _timeline()
        timeline.plan(_node_entry(value=0.4))
        payload = timeline.persist()
        payload["plan"][0]["value"] = 2.0
        with pytest.raises(ValueError):
            _timeline().restore(payload)

    def test_restore_rejects_unwired_target(self):
        timeline = _timeline()
        timeline.plan(_node_entry(value=0.4))
        payload = timeline.persist()
        payload["plan"][0]["target"] = UNWIRED_NODE
        with pytest.raises(ValueError):
            _timeline().restore(payload)

    def test_restore_rejects_duplicate_records(self):
        timeline = _timeline()
        timeline.plan(_node_entry(value=0.4))
        record = timeline.resolve_node(NODE, (0, 0), 2).record
        payload = timeline.persist()
        payload["records"].append(dict(record.plain(), seq=2))
        with pytest.raises(ValueError):
            _timeline().restore(payload)

    def test_restore_failure_leaves_no_partial_plan(self):
        """两阶段恢复：合法条目在前、非法条目在后 → 时间线不变。"""
        source = _timeline()
        source.plan(_node_entry(value=0.4))
        payload = source.persist()
        payload["plan"].append({
            **payload["plan"][0],
            "target": UNWIRED_NODE,
            "seq": 2,
        })
        target = _timeline()
        with pytest.raises(ValueError):
            target.restore(payload)
        assert target.persist() == {"plan": [], "records": []}
        assert target.history_plain() == []

    def test_restore_failure_leaves_no_partial_records(self):
        """记录阶段失败同样不留半成品（前合法后非法）。"""
        source = _timeline()
        source.plan(_node_entry(value=0.4))
        source.resolve_node(NODE, (0, 0), 1)
        payload = source.persist()
        payload["records"].append({
            **payload["records"][0],
            "frame": 2,
            "seq": 2,
            "value": 2.0,          # 越界：校验失败
        })
        target = _timeline()
        with pytest.raises(ValueError):
            target.restore(payload)
        assert target.persist() == {"plan": [], "records": []}
        assert target.history_plain() == []

    def test_restore_uses_instance_loader(self):
        timeline = _timeline()
        timeline.plan(_node_entry(value=0.4))
        payload = timeline.persist()
        target = _timeline(instance_exists=lambda node, inst: False)
        with pytest.raises(ValueError):
            target.restore(payload)
        assert target.restore(
            payload, instance_loader=lambda node, inst: True,
        ) == 1

    def test_restore_preserves_seq_for_future_materialization(self):
        timeline = _timeline()
        timeline.plan(_node_entry(value=0.4))
        timeline.plan(_node_entry(value=0.5))
        payload = timeline.persist()
        restored = _timeline()
        restored.restore(payload)
        assert restored.resolve_node(NODE, (0, 0), 4).value == 0.5


class TestDefaults:
    def test_default_duration_by_space(self):
        assert default_duration("node") == 1
        assert default_duration("parameter") is None
        assert default_duration("field_feature") is None

    def test_default_and_current_frame(self):
        assert _timeline(now=lambda: 5).default_frame() == 6
        assert _timeline(now=lambda: 5).current_frame() == 5
        assert _timeline().default_frame() == 0
        assert _timeline().current_frame() == 0
