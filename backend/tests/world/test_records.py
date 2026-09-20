"""研究记录测试— 运行时捕获、记录校验与可重算。

- 运行时：``step(trace=True)`` 逐机制捕获（父值/阶段/输出）；
- 记录：``TraceLog.record`` fail-closed 校验（节点/阶段/父值/机制）；
- 可重算：``replay`` 用记录父值重跑声明方程，输出逐位一致；
- 有界内存：容量淘汰计数（丢失报告）。
"""

from __future__ import annotations

import pytest

from ascend.world import Schedule, WorldProcess, WorldSpec, compile_world
from ascend.world.modules import toy
from ascend.world.research.records import (
    RandomAddress,
    TraceLog,
    TraceRecord,
    record_from_trace,
)

_PHASES = ("stage1", "stage2", "stage3")


@pytest.fixture()
def program():
    return compile_world(
        WorldSpec(modules=(toy.W0,), schedule=Schedule(phases=_PHASES))
    )


class TestRuntimeCapture:
    def test_step_trace_captures_mechanisms(self, program):
        process = WorldProcess(program)
        result = process.step(trace=True)
        captured = {trace.mechanism_id for trace in result.traces}
        assert captured == {
            "toy.mid1.compute", "toy.mid2.compute", "toy.x.advance",
        }
        for trace in result.traces:
            assert trace.instance == ()
            assert trace.microstep in _PHASES
            assert trace.output is not None

    def test_trace_disabled_by_default(self, program):
        result = WorldProcess(program).step()
        assert result.traces == ()


class TestTraceLog:
    def _records(self, program) -> list[TraceRecord]:
        process = WorldProcess(program)
        result = process.step(trace=True)
        return [
            record_from_trace(program, trace, frame=result.tick)
            for trace in result.traces
        ]

    def test_record_and_replay(self, program):
        log = TraceLog(program, capacity=16)
        for record in self._records(program):
            log.record(record)
        assert log.counts()["eval"] == 3
        assert log.verify_all() == []
        for record in log.records():
            assert log.replay(record) == record.output

    def test_validation_fail_closed(self, program):
        log = TraceLog(program, capacity=16)
        good = self._records(program)[0]
        log.record(good)
        with pytest.raises(ValueError):
            log.record(
                TraceRecord(node_id="toy.ghost", frame=1, microstep="stage1")
            )
        with pytest.raises(ValueError):
            log.record(
                TraceRecord(
                    node_id=good.node_id,
                    frame=1,
                    microstep="wrong",
                    mechanism_id=good.mechanism_id,
                    equation_version="v",
                    parents=good.parents,
                    output=good.output,
                )
            )
        with pytest.raises(ValueError):
            log.record(
                TraceRecord(
                    node_id=good.node_id,
                    frame=1,
                    microstep=good.microstep,
                    mechanism_id=good.mechanism_id,
                    equation_version="v",
                    parents=(),
                    output=good.output,
                )
            )

    def test_capacity_and_dropped(self, program):
        log = TraceLog(program, capacity=2)
        for index in range(5):
            record = self._records(program)[0]
            log.record(
                TraceRecord(
                    node_id=record.node_id,
                    frame=index,
                    microstep=record.microstep,
                    mechanism_id=record.mechanism_id,
                    equation_version=record.equation_version,
                    parents=record.parents,
                    output=record.output,
                )
            )
        assert len(log) == 2
        assert log.dropped == 3
        assert log.records(frame=4)

    def test_page_filters(self, program):
        log = TraceLog(program, capacity=16)
        for record in self._records(program):
            log.record(record)
        page, total = log.page(node_id="toy.x", limit=1)
        assert total == 1 and len(page) == 1
        assert page[0].node_id == "toy.x"

    def test_value_override_record_replays_output(self, program):
        log = TraceLog(program, capacity=4)
        record = TraceRecord(
            node_id="toy.x",
            frame=1,
            microstep="stage3",
            mechanism_id="",
            rep="value",
            output=42,
        )
        log.record(record)
        assert log.replay(record) == 42
