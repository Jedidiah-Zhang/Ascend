"""实体演练模块测试 — 实体 + 事件 + 资源 + Γ（无策略）。

- 声明自洽：守恒常量与初值一致；
- 事件门控：不触发事件世界不动；只触发 ``harvest`` 只产生采集量（流量，
  不结算）；``harvest`` + ``harvest.settle`` 完成携带与扣减；
- 链接：采集者经目标链接读资源（restrict 语义的同帧点读），资源经归属
  链接读采集者的本帧采集量；
- Γ：目标指派走行动 → 干预（``ActionSpec`` → ``resolve`` →
  ``interventions_at``），世界内无策略；
- 守恒：Σ 存量 + Σ 携带 == 声明总量（逐帧）；界内（record）负值留痕；
- fail-closed：未指派目标（空链接键）即帧失败。
"""

from __future__ import annotations

import pytest

from ascend.world import Schedule, WorldProcess, WorldSpec, compile_world
from ascend.world.modules import harvest
from ascend.world.research.action import (
    ActionSpec,
    interventions_at,
    resolve,
)
from ascend.world.runtime import FrameFailure

_PHASES = ("hold",)
_EVENTS = ("harvest", "harvest.settle")


def _program():
    return compile_world(
        WorldSpec(
            modules=(harvest.MODULE,),
            schedule=Schedule(phases=_PHASES),
        )
    )


def _total(process: WorldProcess) -> int:
    return (
        sum(process.committed("harvest.stock").values())
        + sum(process.committed("harvest.carried").values())
    )


def _spawn_world(*, harvester: str = "h1") -> WorldProcess:
    process = WorldProcess(_program())
    for index in range(harvest.RESOURCE_COUNT):
        process.spawn_entity("entity.resource", f"r{index}")
    process.spawn_entity("entity.harvester", harvester)
    return process


def _assign_target(process: WorldProcess, harvester: str, target: str) -> None:
    """Γ：行动 → 干预（目标 + 归属双向指派；世界内无策略）。"""
    actions = (
        ActionSpec(
            id="assign", target_slot="harvest.target",
            value={(harvester,): target},
        ),
        ActionSpec(
            id="own", target_slot="harvest.owner",
            value={(target,): harvester},
        ),
    )
    next_tick = process.tick + 1
    resolved = resolve(actions, tick=next_tick)
    process.step(interventions=interventions_at(resolved, next_tick))


class TestDeclarationSelfConsistency:
    def test_declared_total_matches_initial_values(self):
        program = _program()
        resource = program.slots["harvest.stock"]
        harvester = program.slots["harvest.carried"]
        count = program.instances["entity.resource"]
        assert count.kind == "entity"
        assert (
            harvest.RESOURCE_COUNT * resource.initial
            + 0 * harvester.initial
            == harvest.TOTAL_RESOURCE
        )

    def test_module_is_declaration_only(self):
        assert harvest.MODULE.id == "drill.harvest"
        assert harvest.MODULE.relations
        assert harvest.MODULE.invariants


class TestEventGating:
    def test_no_events_no_change(self):
        process = _spawn_world()
        _assign_target(process, "h1", "r0")
        before_stock = process.committed("harvest.stock").values()
        process.step()
        assert process.committed("harvest.stock").values() == before_stock
        assert process.committed("harvest.carried").values() == (0,)
        assert process.committed("harvest.yield").values() == (0,)

    def test_harvest_event_only_yields(self):
        process = _spawn_world()
        _assign_target(process, "h1", "r0")
        process.step(events=("harvest",))
        assert process.committed("harvest.yield").values() == (3,)
        assert process.committed("harvest.carried").values() == (0,)
        assert process.committed("harvest.stock").values() == (5, 5)
        assert _total(process) == harvest.TOTAL_RESOURCE

    def test_settle_event_moves_resource(self):
        process = _spawn_world()
        _assign_target(process, "h1", "r0")
        process.step(events=_EVENTS)
        assert process.committed("harvest.carried").values() == (3,)
        assert process.committed("harvest.stock").values() == (2, 5)
        assert _total(process) == harvest.TOTAL_RESOURCE

    def test_drains_target_then_stops(self):
        process = _spawn_world()
        _assign_target(process, "h1", "r0")
        for _ in range(4):
            process.step(events=_EVENTS)
        # r0 存量 5：两帧采满（3 + 2），第三帧起采集量 0
        assert process.committed("harvest.stock").values() == (0, 5)
        assert process.committed("harvest.carried").values() == (5,)
        assert process.committed("harvest.yield").values() == (0,)
        assert _total(process) == harvest.TOTAL_RESOURCE


class TestLinks:
    def test_target_link_selects_resource(self):
        process = _spawn_world()
        _assign_target(process, "h1", "r1")
        process.step(events=_EVENTS)
        assert process.committed("harvest.stock").values() == (5, 2)

    def test_unassigned_target_yields_zero(self):
        """链接未指派 = 无流动（父槽位声明 missing=0）。"""
        process = _spawn_world()
        process.step(events=_EVENTS)
        assert process.committed("harvest.yield").values() == (0,)
        assert process.committed("harvest.stock").values() == (5, 5)
        assert _total(process) == harvest.TOTAL_RESOURCE

    def test_dangling_target_fails_closed(self):
        """指向不存在的实体仍 fail-closed（区别于"未指派"）。"""
        process = _spawn_world()
        process.step(interventions={
            "harvest.target": {("h1",): "ghost"},
        })
        with pytest.raises(FrameFailure, match="KeyError|ghost"):
            process.step(events=_EVENTS)

    def test_owner_link_consumes_correct_resource(self):
        """归属链接：只有归属该采集者的资源被扣减。"""
        process = _spawn_world()
        process.spawn_entity("entity.harvester", "h2")
        _assign_target(process, "h1", "r0")
        actions = (
            ActionSpec(
                id="own", target_slot="harvest.owner",
                value={("r0",): "h1", ("r1",): "h2"},
            ),
            ActionSpec(
                id="own2", target_slot="harvest.target",
                value={("h2",): "r1"},
            ),
        )
        next_tick = process.tick + 1
        resolved = resolve(actions, tick=next_tick)
        process.step(
            events=_EVENTS,
            interventions=interventions_at(resolved, next_tick),
        )
        # 两个采集者各采自己目标：r0 -3、r1 -3
        assert process.committed("harvest.stock").values() == (2, 2)


class TestConservation:
    def test_total_holds_across_frames(self):
        process = _spawn_world()
        _assign_target(process, "h1", "r0")
        assert _total(process) == harvest.TOTAL_RESOURCE
        for _ in range(4):
            process.step(events=_EVENTS)
            assert _total(process) == harvest.TOTAL_RESOURCE

    def test_violation_rejected_and_rolled_back(self):
        process = _spawn_world()
        process.step()
        with pytest.raises(FrameFailure, match="不守恒"):
            process.step(interventions={"harvest.stock": {("r0",): 6}})
        assert process.tick == 1
        assert process.committed("harvest.stock").values() == (5, 5)

    def test_bounds_violation_recorded(self):
        """采集量为负（record）：结算把负流量传导，守恒仍成立。"""
        process = _spawn_world()
        _assign_target(process, "h1", "r0")
        result = process.step(
            events=_EVENTS,
            interventions={"harvest.yield": {("h1",): -1}},
        )
        assert any("负值" in message for message in result.violations)
        assert _total(process) == harvest.TOTAL_RESOURCE
