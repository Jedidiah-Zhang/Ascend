"""研究 trace 生产接线测试 — 引擎挂载、终端指令、研究 API 与事件分库。

覆盖 P3 接线：``WeatherEngine.enable_trace`` → 求值点记录 → 终端
``trace`` 指令组与 net ``research_trace_*`` 同源；并锁死"研究日志与玩法
事件分库"这条边界（事件载荷不得出现 trace 字段）。
"""

from __future__ import annotations

import pytest

from ascend.causal import InterventionRecord, InterventionTable
from ascend.causal.world import ASCEND_MECHANISMS
from ascend.i18n import I18n
from ascend.space import ClimateZone, WeatherParams
from ascend.time import GameCalendar, WorldClock
from ascend.weather import WeatherEngine
from ascend.weather.mechanisms import INSTANT_TEMPERATURE
from ascend.world_tree import WorldTree

_CHUNK = (0, 0)


@pytest.fixture()
def clock() -> WorldClock:
    return WorldClock()


@pytest.fixture()
def i18n() -> I18n:
    return I18n("zh_CN")


@pytest.fixture()
def engine(clock):
    """含 chunk (0,0) 的天气引擎（独立世界树 + 独立干预表）。"""
    wt = WorldTree()
    engine = WeatherEngine(clock, seed=42, world_tree_arg=wt)
    table = InterventionTable(
        ASCEND_MECHANISMS,
        now=lambda: clock.time,
        instance_exists=lambda _node, inst: engine.has_chunk(*inst),
    )
    engine = WeatherEngine(
        clock, seed=42, world_tree_arg=wt, intervention_table=table,
    )
    engine.register_chunk(
        *_CHUNK, WeatherParams(20.0, 800.0, 12.0, 100.0, 60.0, 5.0),
        ClimateZone.TEMPERATE_FOREST, 15.0,
    )
    yield engine, table
    engine.shutdown()


@pytest.fixture()
def executor(clock, i18n, engine):
    from ascend.terminal.executor import CommandExecutor, ExecutorConfig

    weather, table = engine
    return CommandExecutor(
        clock=clock, calendar=GameCalendar(clock), i18n=i18n,
        config=ExecutorConfig(
            weather_engine=weather, default_chunk=_CHUNK,
            intervention_table=table,
        ),
    )


class TestEngineTracing:
    """引擎挂载：默认关闭、开启后逐节点记录、重算一致。"""

    def test_trace_off_by_default(self, engine):
        weather, _ = engine
        assert weather.trace is None
        weather.get_weather(*_CHUNK)
        assert weather.trace is None

    def test_enable_records_every_wired_node(self, engine):
        weather, _ = engine
        log = weather.enable_trace()
        weather.get_weather(*_CHUNK)
        assert len(log) > 0
        for entry in log.records():
            assert entry.microstep
            assert entry.rep == "value" or entry.equation_version
            assert log.verify(entry) is True
        assert log.verify_all() == []

    def test_enable_is_idempotent(self, engine):
        weather, _ = engine
        first = weather.enable_trace()
        assert weather.enable_trace() is first

    def test_disable_stops_recording(self, engine):
        weather, _ = engine
        log = weather.enable_trace()
        weather.get_weather(*_CHUNK)
        count = len(log)
        weather.disable_trace()
        weather.get_weather(*_CHUNK)
        assert len(log) == count
        assert weather.trace is None

    def test_trace_does_not_change_weather(self, engine):
        """开启 trace 前后同一时刻的天气读数逐位一致（纯观察层）。"""
        weather, _ = engine
        baseline = weather.get_weather(*_CHUNK)
        weather.enable_trace()
        traced = weather.get_weather(*_CHUNK)
        assert traced.temperature == baseline.temperature
        assert traced.humidity == baseline.humidity
        assert traced.rainfall == baseline.rainfall

    def test_value_intervention_recorded_with_provenance(self, engine, clock):
        weather, table = engine
        log = weather.enable_trace()
        table.commit(InterventionRecord(
            target_space="node", target=INSTANT_TEMPERATURE,
            instance=_CHUNK, rep="value", value=30.0,
            frame_t0=0, duration=None,
        ))
        params = weather.get_weather(*_CHUNK)
        assert params.temperature == 30.0
        entry = log.records(node_id=INSTANT_TEMPERATURE)[-1]
        assert entry.rep == "value"
        assert entry.intervention["target"] == INSTANT_TEMPERATURE
        assert entry.intervention["seq"] == 1
        assert log.verify(entry) is True


class TestTraceIsSeparateFromGameplayEvents:
    """研究日志与玩法事件分库（事件载荷不得泄露 trace 字段）。"""

    def test_events_carry_no_trace_fields(self, engine, clock):
        """真实玩法事件（季节跨越）载荷不得含 trace 字段。"""
        from ascend.config import GAME_DAY
        from ascend.world_tree import AffectedParty, Event

        weather, _ = engine
        wt = weather._wt
        captured: list = []
        wt.subscribe("*", lambda event: captured.append(event))

        def publish_minute(game_time: int) -> None:
            wt.publish(Event(
                timestamp=game_time, location=(0, 0, None, None),
                initiator_type="system", initiator_id="test",
                affected=[AffectedParty("world", "subject")],
                event_type="minute_change",
                data={"game_time": game_time, "hour": 6, "minute": 0},
            ))

        weather.enable_trace()
        publish_minute(clock.time)           # 首次（不发季节事件）
        clock.skip(90 * GAME_DAY)            # 跨季节边界
        publish_minute(clock.time)
        weather.get_weather(*_CHUNK)
        assert captured, "前提：跨季节应产生玩法事件"
        forbidden = {
            "equation_version", "resolved_version", "parents",
            "random_addresses", "microstep", "trace",
        }
        for event in captured:
            data = event.data if isinstance(event.data, dict) else {}
            assert not (forbidden & set(data)), \
                f"事件 {event.event_type} 泄露了研究日志字段: {data}"


class TestTerminalTraceCommands:
    """终端 trace 指令组。"""

    def test_status_off_then_on(self, executor):
        assert "关闭" in executor.execute("trace status").output
        result = executor.execute("trace on 16")
        assert result.success and "已开启" in result.output
        assert "开启" in executor.execute("trace status").output

    def test_on_twice_rejected(self, executor):
        executor.execute("trace on")
        assert executor.execute("trace on").success is False

    def test_off_without_on_rejected(self, executor):
        assert executor.execute("trace off").success is False

    def test_list_and_show_after_query(self, executor, engine):
        weather, _ = engine
        executor.execute("trace on")
        weather.get_weather(*_CHUNK)
        listed = executor.execute("trace list")
        assert listed.success and INSTANT_TEMPERATURE in listed.output
        shown = executor.execute(f"trace show {INSTANT_TEMPERATURE}")
        assert shown.success
        assert "方程版本" in shown.output
        assert "边界处理" in shown.output

    def test_list_filters_by_frame(self, executor, engine):
        weather, _ = engine
        executor.execute("trace on")
        weather.get_weather(*_CHUNK)
        empty = executor.execute("trace list frame 999999")
        assert empty.success and "无匹配" in empty.output

    def test_verify_reports_all_consistent(self, executor, engine):
        weather, _ = engine
        executor.execute("trace on")
        weather.get_weather(*_CHUNK)
        result = executor.execute("trace verify")
        assert result.success and "通过" in result.output

    def test_clear(self, executor, engine):
        weather, _ = engine
        executor.execute("trace on")
        weather.get_weather(*_CHUNK)
        result = executor.execute("trace clear")
        assert result.success
        assert weather.trace is not None and len(weather.trace) == 0

    def test_help_lists_commands(self, executor):
        result = executor.execute("trace")
        assert result.success is False
        assert "trace on" in result.output

    def test_i18n_english(self, clock, engine):
        from ascend.terminal.executor import CommandExecutor, ExecutorConfig

        weather, table = engine
        executor = CommandExecutor(
            clock=clock, calendar=GameCalendar(clock), i18n=I18n("en_US"),
            config=ExecutorConfig(
                weather_engine=weather, default_chunk=_CHUNK,
                intervention_table=table,
            ),
        )
        assert "off" in executor.execute("trace status").output
        assert executor.execute("trace on").success is True


class TestResearchTraceApi:
    """net 研究 API（与终端同源同一日志实例）。"""

    def _handler(self, engine):
        from ascend.net.handlers.research_handler import make_research_handler

        weather, table = engine
        return make_research_handler(table, weather)

    def test_list_requires_trace_enabled(self, engine):
        handler = self._handler(engine)
        response = handler["research_trace_list"]({"payload": {}})
        assert response["payload"]["success"] is False

    def test_list_and_replay(self, engine):
        weather, _ = engine
        weather.enable_trace()
        weather.get_weather(*_CHUNK)
        handler = self._handler(engine)
        listed = handler["research_trace_list"]({"payload": {}})
        assert listed["payload"]["success"] is True
        assert listed["payload"]["records"]
        replayed = handler["research_trace_replay"]({
            "payload": {"node_id": INSTANT_TEMPERATURE},
        })
        assert replayed["payload"]["success"] is True
        assert replayed["payload"]["consistent"] is True

    def test_replay_missing_node_rejected(self, engine):
        weather, _ = engine
        weather.enable_trace()
        handler = self._handler(engine)
        response = handler["research_trace_replay"]({"payload": {}})
        assert response["payload"]["success"] is False

    def test_bad_frame_type_rejected(self, engine):
        weather, _ = engine
        weather.enable_trace()
        handler = self._handler(engine)
        response = handler["research_trace_list"]({
            "payload": {"frame": "x"},
        })
        assert response["payload"]["success"] is False

    def test_clear(self, engine):
        weather, _ = engine
        weather.enable_trace()
        weather.get_weather(*_CHUNK)
        handler = self._handler(engine)
        response = handler["research_trace_clear"]({"payload": {}})
        assert response["payload"]["success"] is True
        assert response["payload"]["cleared"] > 0
