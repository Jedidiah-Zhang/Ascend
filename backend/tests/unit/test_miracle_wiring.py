"""神迹系统生产接线测试 — 天气引擎求值覆盖、force_feature、do 指令、研究 API。

覆盖 P2 生产接线：weather_engine 经 MiracleEvaluator 按 (节点, chunk,
tick) 覆盖；force_feature 登记 field_feature 神迹；终端 do 指令组与
net research 研究 API 同源落到同一神迹表。
"""

from __future__ import annotations

import pytest

from ascend.causal import MiracleRecord, MiracleTable
from ascend.time import WorldClock, GameCalendar
from ascend.i18n import I18n


@pytest.fixture
def clock() -> WorldClock:
    return WorldClock()


@pytest.fixture
def calendar(clock):
    return GameCalendar(clock)


@pytest.fixture
def i18n():
    return I18n("zh_CN")


@pytest.fixture
def weather_engine(clock):
    from ascend.space import WeatherParams, ClimateZone
    from ascend.world_tree import WorldTree
    from ascend.weather import WeatherEngine

    wt = WorldTree()
    table = MiracleTable(_registry())
    engine = WeatherEngine(clock, seed=42, world_tree_arg=wt,
                           miracle_table=table)
    engine.register_chunk(
        0, 0, WeatherParams(20.0, 800.0, 12.0, 100.0, 60.0, 5.0),
        ClimateZone.TEMPERATE_FOREST, 15.0,
    )
    yield engine, table
    engine.shutdown()


@pytest.fixture
def executor(clock, calendar, i18n, weather_engine):
    from ascend.terminal.executor import CommandExecutor, ExecutorConfig

    engine, table = weather_engine
    return CommandExecutor(
        clock=clock, calendar=calendar, i18n=i18n,
        config=ExecutorConfig(
            weather_engine=engine, default_chunk=(0, 0),
            miracle_table=table,
        ),
    )


def _registry():
    from ascend.causal.world import ASCEND_MECHANISMS
    return ASCEND_MECHANISMS


# ── 天气引擎求值覆盖 ─────────────────────────────────────────

class TestEngineEvaluation:
    def test_value_miracle_affects_get_weather(self, weather_engine):
        from ascend.weather import mechanisms as m

        engine, table = weather_engine
        baseline = engine.get_weather(0, 0, time=0)
        table.commit(MiracleRecord(
            target_space="node", target=m.INSTANT_TEMPERATURE,
            instance=(0, 0), rep="value", value=25.0,
            frame_t0=0, duration=1,
        ))
        overridden = engine.get_weather(0, 0, time=0)
        assert overridden.temperature == 25.0
        assert baseline.temperature != 25.0
        # 窗口结束后恢复原机制
        table.commit(MiracleRecord(
            target_space="node", target=m.INSTANT_TEMPERATURE,
            instance=(0, 0), rep="value", value=25.0,
            frame_t0=5, duration=1,
        ))
        assert engine.get_weather(0, 0).temperature != 25.0

    def test_value_miracle_scoped_to_instance(self, weather_engine):
        """实例隔离：只影响 (0,0)，(0,0) 之外不受影响。"""
        from ascend.weather import mechanisms as m

        engine, table = weather_engine
        table.commit(MiracleRecord(
            target_space="node", target=m.INSTANT_TEMPERATURE,
            instance=(9, 9), rep="value", value=25.0,
            frame_t0=0, duration=1,
        ))
        assert engine.get_weather(0, 0).temperature != 25.0

    def test_parameter_miracle_flows_to_weather(self, weather_engine):
        """参数神迹（环境变化）改变绑定参数槽位的求值。"""
        from ascend.weather import mechanisms as m

        engine, table = weather_engine
        # 取一个 intervention_allowed 的 float 参数作为目标
        param_id = None
        for pid, param in table.registry.parameters.items():
            if param.intervention_allowed and param.value_type == "float":
                param_id = pid
                break
        assert param_id is not None
        base = engine.get_weather(0, 0)
        value = base.temperature
        table.commit(MiracleRecord(
            target_space="parameter", target=param_id, rep="value",
            value=value, frame_t0=0,
        ))
        # 覆盖为当前值 → 不影响数值；解析与快照应反映活跃环境变化
        assert table.resolve_parameter(param_id, 0) == (True, value)
        snapshot = table.snapshot()
        assert any(
            rec["target"] == param_id for rec in snapshot["parameters"]
        )

    def test_instance_domain_must_match(self, weather_engine):
        """实例必须匹配节点实例域（§7 目标分量实例存在，fail-closed）。"""
        from ascend.weather import mechanisms as m

        engine, table = weather_engine
        # chunk 实例域节点必须给 (cx, cy)
        with pytest.raises(ValueError, match="实例"):
            table.commit(MiracleRecord(
                target_space="node", target=m.INSTANT_TEMPERATURE,
                instance=(), rep="value", value=25.0,
                frame_t0=0, duration=1,
            ))
        with pytest.raises(ValueError, match="实例"):
            table.commit(MiracleRecord(
                target_space="node", target=m.INSTANT_TEMPERATURE,
                instance=(0, 0, 0), rep="value", value=25.0,
                frame_t0=0, duration=1,
            ))
        # 全局分量必须空实例
        global_node = next(
            nid for nid, node in table.registry.nodes.items()
            if node.instance_domain.kind == "global_singleton"
            and node.role in ("mechanism_state", "persistent_state")
            and node.origin == "mechanism"
        )
        with pytest.raises(ValueError, match="实例"):
            table.commit(MiracleRecord(
                target_space="node", target=global_node,
                instance=(0, 0), rep="value", value=1.0,
                frame_t0=0, duration=1,
            ))

    def test_past_query_uses_miracle_window(self, weather_engine):
        """神迹窗口确定性：过去查询按记录窗口解析。"""
        from ascend.weather import mechanisms as m

        engine, table = weather_engine
        table.commit(MiracleRecord(
            target_space="node", target=m.INSTANT_TEMPERATURE,
            instance=(0, 0), rep="value", value=25.0,
            frame_t0=0, duration=1,
        ))
        # 无记录帧（未来无查询）：过去帧 0 命中
        assert engine.get_weather(0, 0, time=0).temperature == 25.0
        assert engine.get_weather(0, 0, time=3).temperature != 25.0


# ── force_feature 神迹化 ─────────────────────────────────────

class TestForceFeatureMiracle:
    def test_force_feature_registers_miracle(self, weather_engine, clock):
        engine, table = weather_engine
        assert engine.force_feature(0, 0, "storm", True) is True
        active, record = table.resolve_feature("storm", (0, 0), clock.time)
        assert active and record.target_space == "field_feature"
        # no-op：已处于目标状态
        assert engine.force_feature(0, 0, "storm", True) is False
        # 解除 → 神迹清除
        assert engine.force_feature(0, 0, "storm", False) is True
        active, _ = table.resolve_feature("storm", (0, 0), clock.time)
        assert not active
        assert engine.force_feature(0, 0, "storm", False) is False

    def test_force_feature_unknown_type_rejected(self, weather_engine):
        engine, _ = weather_engine
        with pytest.raises(ValueError, match="未知特征类型"):
            engine.force_feature(0, 0, "tsunami", True)


# ── 终端 do 指令 ─────────────────────────────────────────────

class TestDoCommands:
    def test_do_value_global_and_chunk(self, executor, weather_engine):
        from ascend.weather import mechanisms as m

        engine, table = weather_engine
        result = executor.execute(
            f"do value {m.INSTANT_TEMPERATURE} 25 at 0",
        )
        assert result.success is True
        assert engine.get_weather(0, 0, time=0).temperature == 25.0

    def test_do_value_invalid_rejected(self, executor):
        from ascend.weather import mechanisms as m

        result = executor.execute(
            f"do value {m.INSTANT_TEMPERATURE} 9999 at 0",
        )
        assert result.success is False
        assert "超出" in result.output or "值域" in result.output

    def test_do_value_integer_node(self, executor, weather_engine):
        """integer 域节点 CLI 可登记（按声明类型解析 int）。"""
        _, table = weather_engine
        integer_node = next(
            nid for nid, node in table.registry.nodes.items()
            if node.value.kind == "integer"
            and node.role in ("mechanism_state", "persistent_state")
            and node.origin == "mechanism"
            and node.access.interventions
        )
        result = executor.execute(
            f"do value {integer_node} 5 at 0",
        )
        assert result.success is True
        res = table.resolve_node(
            integer_node,
            (0, 0) if table.registry.nodes[integer_node].instance_domain.kind
            == "spatial_field" else (),
            0,
        )
        assert res.rep == "value" and res.value == 5

    def test_do_mech(self, executor, weather_engine):
        from ascend.weather import mechanisms as m

        engine, table = weather_engine
        mechanism = table.registry.mechanism_for(m.INSTANT_TEMPERATURE)
        result = executor.execute(
            f"do mech {m.INSTANT_TEMPERATURE} {mechanism.mechanism_id} at 0",
        )
        assert result.success is True
        res = table.resolve_node(m.INSTANT_TEMPERATURE, (0, 0), 0)
        assert res.rep == "mechanism"

    def test_do_mech_unknown_mechanism(self, executor):
        result = executor.execute("do mech ghost missing_mech")
        assert result.success is False
        assert "未登记" in result.output

    def test_do_param(self, executor, weather_engine):
        engine, table = weather_engine
        param_id = next(
            pid for pid, param in table.registry.parameters.items()
            if param.intervention_allowed and param.value_type == "float"
        )
        result = executor.execute(f"do param {param_id} 1.0 at 0")
        assert result.success is True
        active, value = table.resolve_parameter(param_id, 0)
        assert active and value == 1.0
        assert "环境变化" in result.output

    def test_do_list_and_clear(self, executor, weather_engine):
        from ascend.weather import mechanisms as m

        _, table = weather_engine
        executor.execute(f"do value {m.INSTANT_TEMPERATURE} 25 at 0")
        listing = executor.execute("do list")
        assert listing.success is True
        assert m.INSTANT_TEMPERATURE in listing.output
        cleared = executor.execute(
            f"do clear node {m.INSTANT_TEMPERATURE}",
        )
        assert cleared.success is True
        assert table.resolve_node(m.INSTANT_TEMPERATURE, (0, 0), 0).rep is None

    def test_do_without_table(self, clock, calendar, i18n):
        from ascend.terminal.executor import CommandExecutor

        executor = CommandExecutor(clock=clock, calendar=calendar, i18n=i18n)
        result = executor.execute("do list")
        assert result.success is False
        assert "未挂载" in result.output


# ── 研究 API（net）───────────────────────────────────────────

class TestResearchApi:
    def _handler(self, weather_engine):
        from ascend.net.handlers.research_handler import make_research_handler

        _, table = weather_engine
        return make_research_handler(table), table

    def test_research_do_and_list(self, weather_engine):
        from ascend.weather import mechanisms as m

        handler, table = self._handler(weather_engine)
        response = handler["research_do"]({
            "payload": {
                "space": "node",
                "target": m.INSTANT_TEMPERATURE,
                "instance": [0, 0],
                "rep": "value",
                "value": 25.0,
                "frame_t0": 0,
                "duration": 1,
            },
        })
        assert response["payload"]["success"] is True
        assert isinstance(response["payload"]["seq"], int)
        res = table.resolve_node(m.INSTANT_TEMPERATURE, (0, 0), 0)
        assert res.rep == "value" and res.value == 25.0
        listing = handler["research_do_list"]({})
        assert any(
            rec["target"] == m.INSTANT_TEMPERATURE
            for rec in listing["payload"]["snapshot"]["values"]
        )

    def test_research_do_rejects_bad_value(self, weather_engine):
        from ascend.weather import mechanisms as m

        handler, _ = self._handler(weather_engine)
        response = handler["research_do"]({
            "payload": {
                "space": "node",
                "target": m.INSTANT_TEMPERATURE,
                "instance": [0, 0],
                "rep": "value",
                "value": 9999.0,
                "frame_t0": 0,
                "duration": 1,
            },
        })
        assert response["payload"]["success"] is False

    def test_research_do_mechanism(self, weather_engine):
        from ascend.weather import mechanisms as m

        handler, table = self._handler(weather_engine)
        mechanism = table.registry.mechanism_for(m.INSTANT_TEMPERATURE)
        response = handler["research_do"]({
            "payload": {
                "space": "node",
                "target": m.INSTANT_TEMPERATURE,
                "instance": [0, 0],
                "rep": "mechanism",
                "mechanism_id": mechanism.mechanism_id,
                "frame_t0": 0,
            },
        })
        assert response["payload"]["success"] is True
        assert table.resolve_node(
            m.INSTANT_TEMPERATURE, (0, 0), 0
        ).rep == "mechanism"

    def test_research_do_clear(self, weather_engine):
        from ascend.weather import mechanisms as m

        handler, table = self._handler(weather_engine)
        handler["research_do"]({
            "payload": {
                "space": "node",
                "target": m.INSTANT_TEMPERATURE,
                "instance": [0, 0],
                "rep": "value",
                "value": 25.0,
                "frame_t0": 0,
                "duration": 1,
            },
        })
        response = handler["research_do_clear"]({
            "payload": {
                "space": "node",
                "target": m.INSTANT_TEMPERATURE,
                "instance": [0, 0],
            },
        })
        assert response["payload"]["success"] is True
        assert response["payload"]["cleared"] is True
        assert table.resolve_node(
            m.INSTANT_TEMPERATURE, (0, 0), 0
        ).rep is None