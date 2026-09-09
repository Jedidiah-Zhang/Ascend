"""完整存档契约测试 — W_t 往返、W4 双跑一致、世界设置与 fail-closed。

契约来源：issue #46 P4（manifest 含世界设置全量、W_t 含生效干预/注入核、
快照 fail-closed）与世界验收协议 04 §3.5（W4 状态充分性）。

W4 的实质断言：两个从同一世界设置出发的实例，一个**不存档**持续演化、
一个**存档后读档**继续演化，在相同未来随机地址下轨迹必须逐位一致。
差异若出现，只可能来自某个未被存档携带的状态分量——这正是完整存档要
排除的东西。
"""

from __future__ import annotations

import json

import pytest

from ascend.causal import InterventionRecord
from ascend.save import (
    STATE_VERSION,
    apply_state,
    collect_state,
    require_state_version,
)
from ascend.space import ClimateZone, WeatherParams
from ascend.time import WorldClock
from ascend.weather import WeatherEngine
from ascend.weather.mechanisms import (
    INSTANT_HUMIDITY,
    INSTANT_TEMPERATURE,
    PRECIPITATION_THRESHOLD,
)
from ascend.world_tree import WorldTree

_SEED = 20260908
_CHUNKS = ((0, 0), (1, 0), (0, 1))
_BASELINE = WeatherParams(20.0, 800.0, 12.0, 100.0, 60.0, 5.0)


class _Player:
    """状态采集/恢复用的最小玩家替身（存档不关心实体实现细节）。"""

    entity = None
    position = (12.0, 34.0)


def _build_world(seed: int = _SEED) -> tuple[WorldClock, WeatherEngine]:
    """构造一个已注册 chunk 的天气世界（相同种子 = 相同世界设置）。"""
    clock = WorldClock()
    engine = WeatherEngine(clock, seed=seed, world_tree_arg=WorldTree())
    for cx, cy in _CHUNKS:
        engine.register_chunk(
            cx, cy, _BASELINE, ClimateZone.TEMPERATE_FOREST, 15.0,
        )
    return clock, engine


def _apply_research_interventions(engine: WeatherEngine, frame: int) -> None:
    """施加一条节点干预 + 一条参数干预（值替换与机制空间各一）。"""
    table = engine.intervention_table
    table.commit(InterventionRecord(
        target_space="node", target=INSTANT_TEMPERATURE, instance=(0, 0),
        rep="value", value=30.0, frame_t0=frame, duration=None,
    ))
    table.commit(InterventionRecord(
        target_space="node", target=PRECIPITATION_THRESHOLD, instance=(1, 0),
        rep="value", value=0.5, frame_t0=frame, duration=2,
    ))
    table.commit(InterventionRecord(
        target_space="parameter",
        target="world.parameter.temp_perturb_scale_c",
        rep="value", value=0.0, frame_t0=frame,
    ))


def _trajectory(
    engine: WeatherEngine, frames: range,
) -> list[tuple]:
    """逐帧采样各 chunk 的天气读出（轨迹比较的统一口径）。"""
    out = []
    for tick in frames:
        for cx, cy in _CHUNKS:
            params = engine.get_weather(cx, cy, tick)
            out.append((
                cx, cy, params.temperature, params.humidity,
                params.wind_speed, params.rainfall,
            ))
    return out


class TestWorldStateRoundTrip:
    """W_t 往返：采集 → 序列化 → 恢复后逐字段一致。"""

    def test_round_trip_is_bit_identical(self):
        """同一世界的采集-恢复-再采集必须逐字段相同（含干预与注入核）。"""
        clock, engine = _build_world()
        _apply_research_interventions(engine, frame=0)
        engine.force_feature(1, 0, "storm", True)
        engine.force_feature(0, 1, "front", True)

        state = collect_state(clock, _Player(), engine, 0)
        blob = json.dumps(state)  # 走一遍真实 JSON 通道

        clock2, engine2 = _build_world()
        apply_state(json.loads(blob), clock2, _Player(), engine2)

        assert collect_state(clock2, _Player(), engine2, 0) == state

    def test_restore_clears_stale_injected_cores(self):
        """恢复是整体替换：目标世界的残留注入核不得存活。"""
        clock, engine = _build_world()
        engine.force_feature(1, 0, "storm", True)
        state = collect_state(clock, _Player(), engine, 0)

        clock2, engine2 = _build_world()
        engine2.force_feature(0, 1, "cold_snap", True)
        apply_state(state, clock2, _Player(), engine2)

        assert engine2.field.features.get_injected(1, 0, "storm") is not None
        assert engine2.field.features.get_injected(0, 1, "cold_snap") is None

    def test_intervention_provenance_preserved(self):
        """干预的 seq/applied_at 随存档往返（研究溯源不可重编号）。"""
        clock, engine = _build_world()
        clock.restore(time=500)
        _apply_research_interventions(engine, frame=501)
        before = engine.intervention_table.persist()

        state = collect_state(clock, _Player(), engine, 0)
        clock2, engine2 = _build_world()
        apply_state(state, clock2, _Player(), engine2)

        assert engine2.intervention_table.persist() == before
        assert engine2.intervention_table.history_plain() == \
            engine.intervention_table.history_plain()


class TestStateSufficiencyW4:
    """W4：清缓存双跑逐位一致（状态充分性的实质断言）。"""

    def test_restored_world_matches_unarchived_continuation(self):
        """A 存档后读档 vs B 不存档：后续轨迹逐位一致。"""
        clock_a, engine_a = _build_world()
        clock_b, engine_b = _build_world()

        # 相同初始随机地址下施加相同干预与注入核
        for engine in (engine_a, engine_b):
            _apply_research_interventions(engine, frame=0)
            engine.force_feature(1, 0, "storm", True)

        # A 在 t=5 存档，随后两边各自继续推进到 t=12
        for tick in range(0, 6):
            clock_a.restore(time=tick)
            clock_b.restore(time=tick)
            engine_a._tracker.update(tick)
            engine_b._tracker.update(tick)
        state = collect_state(clock_a, _Player(), engine_a, 0)

        # 读档到一个全新实例 C（模拟进程重启：缓存全空、状态来自存档）
        clock_c, engine_c = _build_world()
        apply_state(json.loads(json.dumps(state)), clock_c, _Player(), engine_c)

        for tick in range(6, 13):
            clock_b.restore(time=tick)
            clock_c.restore(time=tick)
            engine_b._tracker.update(tick)
            engine_c._tracker.update(tick)

        assert _trajectory(engine_b, range(6, 13)) == \
            _trajectory(engine_c, range(6, 13))

    def test_dropping_interventions_breaks_continuation(self):
        """负例：漏存干预 → 轨迹分叉（证明本测试确实有判别力）。"""
        clock_a, engine_a = _build_world()
        _apply_research_interventions(engine_a, frame=0)
        state = collect_state(clock_a, _Player(), engine_a, 0)

        clock_b, engine_b = _build_world()
        state_without = dict(state)
        # 模拟旧版存档（P4 之前）：只有时钟与玩家，干预与注入核丢失
        state_without["weather"] = {"interventions": [], "feature_cores": []}
        apply_state(state_without, clock_b, _Player(), engine_b)

        assert _trajectory(engine_a, range(0, 4)) != \
            _trajectory(engine_b, range(0, 4)), \
            "丢失干预/注入核后轨迹必须分叉，否则 W4 断言无判别力"


class TestFailClosedLoad:
    """读档 fail-closed：非法载荷拒绝且不留半成品状态。"""

    def test_state_version_required(self):
        with pytest.raises(ValueError, match="state_version"):
            require_state_version({"clock": {"time": 0}})

    def test_unknown_intervention_field_rejected(self):
        clock, engine = _build_world()
        state = collect_state(clock, _Player(), engine, 0)
        state["weather"]["interventions"] = [{
            "target_space": "node", "target": INSTANT_TEMPERATURE,
            "instance": [0, 0], "rep": "value", "value": 1.0,
            "mechanism": None, "frame_t0": 0, "duration": 1, "version": "",
            "applied_at": 0, "seq": 1, "ghost": True,
        }]
        clock2, engine2 = _build_world()
        with pytest.raises(ValueError, match="未知字段"):
            apply_state(state, clock2, _Player(), engine2)
        assert engine2.intervention_table.persist() == []

    def test_unwired_target_rejected_on_restore(self):
        """存档里的干预重新走登记校验：未接线目标不得静默恢复。"""
        clock, engine = _build_world()
        state = collect_state(clock, _Player(), engine, 0)
        state["weather"]["interventions"] = [{
            "target_space": "node",
            "target": "weather.instant.precipitation_type",  # 未接线
            "instance": [0, 0], "rep": "value", "value": "rain",
            "mechanism": None, "frame_t0": 0, "duration": None, "version": "",
            "applied_at": 0, "seq": 1,
        }]
        clock2, engine2 = _build_world()
        with pytest.raises(ValueError, match="未接线"):
            apply_state(state, clock2, _Player(), engine2)

    def test_unknown_feature_type_rejected_on_restore(self):
        clock, engine = _build_world()
        engine.force_feature(1, 0, "storm", True)
        state = collect_state(clock, _Player(), engine, 0)
        state["weather"]["feature_cores"][0]["type_name"] = "tsunami"
        clock2, engine2 = _build_world()
        with pytest.raises(ValueError, match="未注册"):
            apply_state(state, clock2, _Player(), engine2)

    def test_nan_core_field_rejected_on_restore(self):
        clock, engine = _build_world()
        engine.force_feature(1, 0, "storm", True)
        state = collect_state(clock, _Player(), engine, 0)
        state["weather"]["feature_cores"][0]["radius"] = float("nan")
        clock2, engine2 = _build_world()
        with pytest.raises(ValueError, match="有限值"):
            apply_state(state, clock2, _Player(), engine2)

    def test_rejected_payload_leaves_no_partial_table(self):
        """整体拒绝：合法记录也不得先落表。"""
        clock, engine = _build_world()
        _apply_research_interventions(engine, frame=0)
        state = collect_state(clock, _Player(), engine, 0)
        state["weather"]["interventions"].append({"target_space": "node"})
        clock2, engine2 = _build_world()
        with pytest.raises(ValueError, match="缺少字段"):
            apply_state(state, clock2, _Player(), engine2)
        assert engine2.intervention_table.persist() == []

    def test_state_version_constant_is_one(self):
        assert STATE_VERSION == 1
