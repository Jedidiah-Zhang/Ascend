"""干预执行器生产接线测试 — 引擎求值覆盖、force_feature、do 指令、研究 API。

覆盖 P2 生产接线：weather_engine 经 ``evaluate_node`` 按 (节点, chunk,
tick) 覆盖求值（region_tracker 复用同一入口）；force_feature 登记
field_feature 干预（单一事实源 = 注入核）；终端 do 指令组与 net 研究
API 同源落到同一干预表，缺省解析与错误处理一致。

另含两条系统性门禁：
- 可达性漂移巡检：``WIRED_NODES`` 声明 == 实际求值点（源码扫描）。
- i18n：do 指令组用户可见文案中英双语可用。
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from ascend.causal import InterventionRecord, InterventionTable
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
    engine = WeatherEngine(clock, seed=42, world_tree_arg=wt)
    table = InterventionTable(
        _registry(),
        now=lambda: clock.time,
        instance_exists=lambda _node, inst: engine.has_chunk(*inst),
    )
    engine = WeatherEngine(
        clock, seed=42, world_tree_arg=wt, intervention_table=table,
    )
    for cx, cy in ((0, 0), (9, 9)):
        engine.register_chunk(
            cx, cy, WeatherParams(20.0, 800.0, 12.0, 100.0, 60.0, 5.0),
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
            intervention_table=table,
        ),
    )


def _registry():
    from ascend.causal.world import ASCEND_MECHANISMS
    return ASCEND_MECHANISMS


# ── 天气引擎求值覆盖 ─────────────────────────────────────────

class TestEngineEvaluation:
    def test_value_intervention_affects_get_weather(self, weather_engine):
        from ascend.weather import mechanisms as m

        engine, table = weather_engine
        baseline = engine.get_weather(0, 0, time=0)
        table.commit(InterventionRecord(
            target_space="node", target=m.INSTANT_TEMPERATURE,
            instance=(0, 0), rep="value", value=25.0,
            frame_t0=0, duration=1,
        ))
        overridden = engine.get_weather(0, 0, time=0)
        assert overridden.temperature == 25.0
        assert baseline.temperature != 25.0

    def test_value_intervention_scoped_to_instance(self, weather_engine):
        from ascend.weather import mechanisms as m

        engine, table = weather_engine
        table.commit(InterventionRecord(
            target_space="node", target=m.INSTANT_TEMPERATURE,
            instance=(9, 9), rep="value", value=25.0,
            frame_t0=0, duration=1,
        ))
        assert engine.get_weather(9, 9, time=0).temperature == 25.0
        assert engine.get_weather(0, 0, time=0).temperature != 25.0

    def test_parameter_intervention_flows_to_weather(self, weather_engine):
        """参数干预（环境变化）真正流入已接线机制的求值。"""
        from ascend.weather import mechanisms as m

        engine, table = weather_engine
        param_id = "world.parameter.temp_perturb_scale_c"
        assert param_id in table.registry.wired_parameters
        before = engine.get_weather(0, 0, time=0).temperature
        table.commit(InterventionRecord(
            target_space="parameter", target=param_id, rep="value",
            value=100.0, frame_t0=0,
        ))
        after = engine.get_weather(0, 0, time=0).temperature
        assert after != before
        assert table.resolve_parameter(param_id, 0) == (True, 100.0)
        # 环境变化标记 + 快照可见
        snapshot = table.snapshot()
        record = snapshot["parameters"][0]
        assert record["target"] == param_id
        assert record["environment_change"] is True
        assert record["applied_at"] is not None

    def test_unwired_node_rejected(self, weather_engine):
        """已声明但未接线的生成点：登记被拒绝（不再静默无效）。"""
        from ascend.weather import mechanisms as m

        _, table = weather_engine
        with pytest.raises(ValueError, match="未接线"):
            table.commit(InterventionRecord(
                target_space="node", target=m.ANNUAL_TEMPERATURE,
                instance=(0, 0), rep="value", value=25.0,
                frame_t0=0, duration=1,
            ))

    def test_unwired_parameter_rejected(self, weather_engine):
        _, table = weather_engine
        with pytest.raises(ValueError, match="未被已接线机制消费"):
            table.commit(InterventionRecord(
                target_space="parameter",
                target="weather.parameter.latitude.input_min_c",
                rep="value", value=0.0, frame_t0=0,
            ))

    def test_unregistered_instance_rejected(self, weather_engine):
        """目标实例存在性由世界句柄校验（§7 目标分量实例存在）。"""
        from ascend.weather import mechanisms as m

        _, table = weather_engine
        with pytest.raises(ValueError, match="实例不存在"):
            table.commit(InterventionRecord(
                target_space="node", target=m.INSTANT_TEMPERATURE,
                instance=(999, 999), rep="value", value=25.0,
                frame_t0=0, duration=1,
            ))

    def test_instance_domain_must_match(self, weather_engine):
        """实例必须匹配节点实例域（§7 目标分量实例存在，fail-closed）。"""
        from ascend.weather import mechanisms as m

        engine, table = weather_engine
        with pytest.raises(ValueError, match="实例"):
            table.commit(InterventionRecord(
                target_space="node", target=m.INSTANT_TEMPERATURE,
                instance=(), rep="value", value=25.0,
                frame_t0=0, duration=1,
            ))
        with pytest.raises(ValueError, match="实例"):
            table.commit(InterventionRecord(
                target_space="node", target=m.INSTANT_TEMPERATURE,
                instance=(0, 0, 0), rep="value", value=25.0,
                frame_t0=0, duration=1,
            ))
        global_node = next(
            nid for nid, node in table.registry.nodes.items()
            if node.instance_domain.kind == "global_singleton"
            and node.access.interventions
            and nid in table.registry.wired_nodes
        )
        with pytest.raises(ValueError, match="实例"):
            table.commit(InterventionRecord(
                target_space="node", target=global_node,
                instance=(0, 0), rep="value", value=1.0,
                frame_t0=0, duration=1,
            ))

    def test_past_query_uses_intervention_window(self, weather_engine):
        """干预窗口确定性：过去查询按记录窗口解析。"""
        from ascend.weather import mechanisms as m

        engine, table = weather_engine
        table.commit(InterventionRecord(
            target_space="node", target=m.INSTANT_TEMPERATURE,
            instance=(0, 0), rep="value", value=25.0,
            frame_t0=0, duration=1,
        ))
        assert engine.get_weather(0, 0, time=0).temperature == 25.0
        assert engine.get_weather(0, 0, time=3).temperature != 25.0

    def test_event_path_and_query_path_agree(self, weather_engine):
        """同一节点在查询路径与 region_tracker 事件路径上求值一致。

        阈值干预必须同时影响 get_weather 与区域事件判定——两者共用
        ``evaluate_node``，不存在第二个旁路求值点。
        """
        from ascend.weather import mechanisms as m

        engine, table = weather_engine
        before = engine.get_weather(0, 0, time=0).rainfall
        table.commit(InterventionRecord(
            target_space="node", target=m.PRECIPITATION_THRESHOLD,
            instance=(0, 0), rep="value", value=0.26,
            frame_t0=0, duration=None,
        ))
        after = engine.get_weather(0, 0, time=0).rainfall
        assert after != before
        # region_tracker 用同一入口求阈值（注入回调即 engine.evaluate_node）
        tracker_threshold = engine._tracker._evaluate(
            m.PRECIPITATION_THRESHOLD, {m.ANNUAL_RAINFALL: 800.0},
            frame=0, instance=(0, 0),
        )
        assert tracker_threshold == 0.26


# ── force_feature 干预化（单一事实源 = 注入核）───────────────

class TestForceFeatureIntervention:
    def test_force_feature_registers_intervention(self, weather_engine, clock):
        engine, table = weather_engine
        assert engine.force_feature(0, 0, "storm", True) is True
        records = table.snapshot()["features"]
        assert records and records[0]["target_space"] == "field_feature"
        assert records[0]["applied_at"] == clock.time
        assert engine.force_feature(0, 0, "storm", True) is False
        assert engine.force_feature(0, 0, "storm", False) is True
        assert table.snapshot()["features"] == []
        assert engine.force_feature(0, 0, "storm", False) is False

    def test_forced_core_does_not_expire(self, weather_engine, clock):
        """强制核与记录同语义（长期）：不会先于记录过期。"""
        engine, table = weather_engine
        assert engine.force_feature(0, 0, "storm", True) is True
        core = engine.field.features.get_injected(0, 0, "storm")
        assert core is not None and core.duration is None
        far_future = clock.time + 100 * 360 * 24 * 60 * 60
        assert core.is_active(far_future)

    def test_clear_then_stop_still_removes_core(self, weather_engine, clock):
        """回归：do clear 只清记录后，weather feature stop 仍能解除核。"""
        engine, table = weather_engine
        assert engine.force_feature(0, 0, "storm", True) is True
        assert table.clear("field_feature", "storm", (0, 0)) == ("value",)
        assert engine.field.features.get_injected(0, 0, "storm") is not None
        assert engine.force_feature(0, 0, "storm", False) is True
        assert engine.field.features.get_injected(0, 0, "storm") is None

    def test_force_feature_unknown_type_rejected(self, weather_engine):
        engine, _ = weather_engine
        with pytest.raises(ValueError, match="未知特征类型"):
            engine.force_feature(0, 0, "tsunami", True)


# ── 终端 do 指令 ─────────────────────────────────────────────

class TestDoCommands:
    def test_do_value_global_and_chunk(self, executor, weather_engine):
        from ascend.weather import mechanisms as m

        engine, _ = weather_engine
        result = executor.execute(
            f"do value {m.INSTANT_TEMPERATURE} 25 at 0",
        )
        assert result.success is True
        assert engine.get_weather(0, 0, time=0).temperature == 25.0

    def test_do_value_default_next_tick(self, executor, weather_engine, clock):
        """缺省生效帧 = 下一 tick（与研究 API 共用一处解析）。"""
        from ascend.weather import mechanisms as m

        engine, table = weather_engine
        result = executor.execute(f"do value {m.INSTANT_TEMPERATURE} 25")
        assert result.success is True
        record = table.snapshot()["values"][0]
        assert record["frame_t0"] == clock.time + 1
        assert record["duration"] == 1
        assert engine.get_weather(0, 0, time=clock.time).temperature != 25.0

    def test_do_value_invalid_rejected(self, executor):
        from ascend.weather import mechanisms as m

        result = executor.execute(
            f"do value {m.INSTANT_TEMPERATURE} 9999 at 0",
        )
        assert result.success is False
        assert "超出" in result.output or "值域" in result.output

    def test_do_value_rejects_extra_args(self, executor):
        """多余参数 fail-closed（不再静默忽略坐标/垃圾 token）。"""
        from ascend.weather import mechanisms as m

        result = executor.execute(
            f"do value {m.INSTANT_TEMPERATURE} 25 0 0 at 0 junk",
        )
        assert result.success is False
        assert "多余参数" in result.output

    def test_do_value_global_node_rejects_coords(self, executor):
        from ascend.weather import mechanisms as m

        result = executor.execute(f"do value {m.DAY} 5 3 4")
        assert result.success is False
        assert "多余参数" in result.output

    def test_do_value_integer_node(self, executor, weather_engine):
        """integer 域节点 CLI 可登记（按声明类型解析 int）。"""
        _, table = weather_engine
        integer_node = next(
            nid for nid, node in table.registry.nodes.items()
            if node.value.kind == "integer"
            and node.access.interventions
            and nid in table.registry.wired_nodes
        )
        result = executor.execute(f"do value {integer_node} 5 at 0")
        assert result.success is True
        res = table.resolve_node(integer_node, (), 0)
        assert res.rep == "value" and res.value == 5

    def test_do_value_integer_enum_node(self, executor, weather_engine):
        """整数枚举域节点按 choices 类型解析（此前终端无法登记 season）。"""
        from ascend.weather import mechanisms as m

        _, table = weather_engine
        node = table.registry.nodes[m.SEASON]
        assert node.value.kind == "enum"
        assert all(isinstance(c, int) for c in node.value.choices)
        result = executor.execute(f"do value {m.SEASON} 2 at 0")
        assert result.success is True
        assert table.resolve_node(m.SEASON, (), 0).value == 2
        bad = executor.execute(f"do value {m.SEASON} summer at 0")
        assert bad.success is False
        assert "enum" in bad.output

    def test_do_mech(self, executor, weather_engine):
        from ascend.weather import mechanisms as m

        _, table = weather_engine
        mech_id = table.registry.mechanism_for(m.INSTANT_TEMPERATURE).mechanism_id
        result = executor.execute(
            f"do mech {m.INSTANT_TEMPERATURE} {mech_id} at 0",
        )
        assert result.success is True
        res = table.resolve_node(m.INSTANT_TEMPERATURE, (0, 0), 0)
        assert res.rep == "mechanism"
        assert table.snapshot()["mechanisms"][0]["mechanism"] == mech_id

    def test_do_mech_unknown_mechanism(self, executor):
        result = executor.execute("do mech ghost missing_mech")
        assert result.success is False
        assert "未登记" in result.output

    def test_do_param(self, executor, weather_engine):
        _, table = weather_engine
        param_id = "world.parameter.game_day_ticks"
        assert param_id in table.registry.wired_parameters
        result = executor.execute(f"do param {param_id} 1.0 at 0")
        assert result.success is True
        active, value = table.resolve_parameter(param_id, 0)
        assert active and value == 1.0
        assert "环境变化" in result.output

    def test_do_param_rejects_extra_args(self, executor, weather_engine):
        _, table = weather_engine
        param_id = "world.parameter.game_day_ticks"
        result = executor.execute(f"do param {param_id} 1.0 0")
        assert result.success is False

    def test_do_list_and_clear(self, executor, weather_engine):
        from ascend.weather import mechanisms as m

        _, table = weather_engine
        executor.execute(f"do value {m.INSTANT_TEMPERATURE} 25 at 0")
        listing = executor.execute("do list")
        assert listing.success is True
        assert m.INSTANT_TEMPERATURE in listing.output
        assert "[0, 1)" in listing.output

        cleared = executor.execute(
            f"do clear node {m.INSTANT_TEMPERATURE} 0 0",
        )
        assert cleared.success is True
        assert table.resolve_node(m.INSTANT_TEMPERATURE, (0, 0), 0).rep is None

    def test_do_clear_removes_mechanism_too(self, executor, weather_engine):
        """回归：do clear 清空该键全部规格（值 + 机制），不报假成功。"""
        from ascend.weather import mechanisms as m

        _, table = weather_engine
        mech_id = table.registry.mechanism_for(m.INSTANT_TEMPERATURE).mechanism_id
        executor.execute(f"do value {m.INSTANT_TEMPERATURE} 25 0 0 at 0")
        executor.execute(f"do mech {m.INSTANT_TEMPERATURE} {mech_id} 0 0 at 0")
        cleared = executor.execute(
            f"do clear node {m.INSTANT_TEMPERATURE} 0 0",
        )
        assert cleared.success is True
        assert "value" in cleared.output and "mechanism" in cleared.output
        assert table.resolve_node(m.INSTANT_TEMPERATURE, (0, 0), 0).rep is None
        assert table.snapshot()["mechanisms"] == []

    def test_do_clear_rep_filter(self, executor, weather_engine):
        from ascend.weather import mechanisms as m

        _, table = weather_engine
        mech_id = table.registry.mechanism_for(m.INSTANT_TEMPERATURE).mechanism_id
        executor.execute(f"do value {m.INSTANT_TEMPERATURE} 25 0 0 at 0")
        executor.execute(f"do mech {m.INSTANT_TEMPERATURE} {mech_id} 0 0 at 0")
        cleared = executor.execute(
            f"do clear node {m.INSTANT_TEMPERATURE} 0 0 rep value",
        )
        assert cleared.success is True
        assert table.resolve_node(
            m.INSTANT_TEMPERATURE, (0, 0), 0,
        ).rep == "mechanism"

    def test_do_clear_single_coord_is_friendly_error(self, executor):
        """回归：单坐标不再抛 IndexError 逃逸到 dispatcher。"""
        from ascend.weather import mechanisms as m

        result = executor.execute(f"do clear node {m.INSTANT_TEMPERATURE} 3")
        assert result.success is False
        assert "多余参数" in result.output

    def test_do_clear_unknown_node_friendly_error(self, executor):
        result = executor.execute("do clear node ghost_node 0 0")
        assert result.success is False
        assert "未声明" in result.output

    def test_do_without_table(self, clock, calendar, i18n):
        from ascend.terminal.executor import CommandExecutor, ExecutorConfig

        executor = CommandExecutor(
            clock=clock, calendar=calendar, i18n=i18n,
            config=ExecutorConfig(default_chunk=(0, 0)),
        )
        result = executor.execute("do list")
        assert result.success is False
        assert "未挂载" in result.output

    def test_do_help_is_localized(self, clock, calendar, weather_engine):
        """do 指令组文案走 i18n（en_US 下为英文）。"""
        from ascend.terminal.executor import CommandExecutor, ExecutorConfig

        engine, table = weather_engine
        executor = CommandExecutor(
            clock=clock, calendar=calendar, i18n=I18n("en_US"),
            config=ExecutorConfig(
                weather_engine=engine, default_chunk=(0, 0),
                intervention_table=table,
            ),
        )
        help_out = executor.execute("do").output
        assert "Intervention" in help_out or "intervention" in help_out
        assert "干预" not in help_out
        error_out = executor.execute("do bogus").output
        assert "干预" not in error_out

    def test_do_i18n_keys_are_bilingual(self):
        """do 指令组全部键在中英两份语言表中存在且可插值。"""
        import json

        from ascend import i18n as i18n_module

        zh = I18n("zh_CN")
        en = I18n("en_US")
        keys: set[str] = set()
        for lang in ("zh_CN", "en_US"):
            data = json.loads(
                (i18n_module.LANG_DIR / f"{lang}.json").read_text(
                    encoding="utf-8",
                )
            )
            keys |= {
                key for key in data
                if key.startswith("console.do_")
                or key.startswith("console.help_do")
            }
        assert keys, "未发现 do 指令组翻译键"
        for key in sorted(keys):
            assert zh.t(key) != key, f"zh_CN 缺少 {key}"
            assert en.t(key) != key, f"en_US 缺少 {key}"
        assert en.t("console.do_unknown_node", target="x") != \
            en.t("console.do_unknown_node")


# ── 研究 API（net）───────────────────────────────────────────

class TestResearchApi:
    def _handler(self, weather_engine):
        from ascend.net.handlers.research_handler import make_research_handler

        engine, table = weather_engine
        return make_research_handler(table, engine), table

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
        payload = response["payload"]
        assert payload["success"] is True
        assert payload["record"]["seq"] == 1
        assert payload["record"]["target"] == m.INSTANT_TEMPERATURE
        assert payload["record"]["applied_at"] is not None
        res = table.resolve_node(m.INSTANT_TEMPERATURE, (0, 0), 0)
        assert res.rep == "value" and res.value == 25.0
        listing = handler["research_do_list"]({})["payload"]
        assert listing["snapshot"]["values"][0]["target"] == m.INSTANT_TEMPERATURE
        assert len(listing["history"]) == 1

    def test_research_do_defaults_match_terminal(self, weather_engine, clock):
        """API 缺省与终端一致：下一 tick + 单帧（共用一处解析）。"""
        from ascend.weather import mechanisms as m

        handler, table = self._handler(weather_engine)
        response = handler["research_do"]({
            "payload": {
                "space": "node",
                "target": m.INSTANT_TEMPERATURE,
                "instance": [0, 0],
                "value": 25.0,
            },
        })
        assert response["payload"]["success"] is True
        record = table.snapshot()["values"][0]
        assert record["frame_t0"] == clock.time + 1
        assert record["duration"] == 1

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

    def test_research_do_rejects_missing_target(self, weather_engine):
        handler, _ = self._handler(weather_engine)
        response = handler["research_do"]({"payload": {"value": 1.0}})
        assert response["payload"]["success"] is False
        assert "target" in response["payload"]["error"]

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

    def test_research_do_feature_forwards_to_engine(self, weather_engine, clock):
        """feature 空间转发 force_feature（与终端同写入路径，无幽灵记录）。"""
        handler, table = self._handler(weather_engine)
        engine = weather_engine[0]
        response = handler["research_do"]({
            "payload": {
                "space": "feature", "target": "storm", "instance": [0, 0],
                "value": {"active": True},
            },
        })
        assert response["payload"] == {"success": True, "changed": True}
        assert engine.field.features.get_injected(0, 0, "storm") is not None
        assert len(table.snapshot()["features"]) == 1
        response = handler["research_do"]({
            "payload": {
                "space": "feature", "target": "storm", "instance": [0, 0],
                "value": {"active": False},
            },
        })
        assert response["payload"]["changed"] is True
        assert engine.field.features.get_injected(0, 0, "storm") is None

    def test_research_do_feature_clear_removes_core(self, weather_engine):
        """回归：API 清除 feature 走 force_feature，不留孤儿注入核。"""
        handler, table = self._handler(weather_engine)
        engine = weather_engine[0]
        handler["research_do"]({
            "payload": {
                "space": "feature", "target": "storm", "instance": [0, 0],
            },
        })
        assert engine.field.features.get_injected(0, 0, "storm") is not None
        response = handler["research_do_clear"]({
            "payload": {
                "space": "feature", "target": "storm", "instance": [0, 0],
            },
        })
        assert response["payload"] == {"success": True, "cleared": ["value"]}
        assert engine.field.features.get_injected(0, 0, "storm") is None
        assert table.snapshot()["features"] == []

    def test_research_do_feature_rejects_unknown_type(self, weather_engine):
        handler, _ = self._handler(weather_engine)
        response = handler["research_do"]({
            "payload": {
                "space": "feature", "target": "tsunami", "instance": [0, 0],
            },
        })
        assert response["payload"]["success"] is False

    def test_research_do_feature_rejects_unregistered_chunk(self, weather_engine):
        handler, _ = self._handler(weather_engine)
        response = handler["research_do"]({
            "payload": {
                "space": "feature", "target": "storm", "instance": [999, 999],
            },
        })
        assert response["payload"]["success"] is False

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
        assert response["payload"]["cleared"] == ["value"]
        assert table.resolve_node(
            m.INSTANT_TEMPERATURE, (0, 0), 0
        ).rep is None
        # 未命中：success 但仍返回空列表
        response = handler["research_do_clear"]({
            "payload": {"space": "node", "target": m.INSTANT_TEMPERATURE},
        })
        assert response["payload"]["cleared"] == []

    def test_research_do_rejects_bad_instance_types(self, weather_engine):
        """instance 非序列（含 JSON null）→ 规范失败响应，不抛异常。"""
        from ascend.weather import mechanisms as m

        handler, _ = self._handler(weather_engine)
        for bad in (3, 1.5, "ab", {"x": 0}):
            response = handler["research_do"]({
                "payload": {
                    "space": "node", "target": m.INSTANT_TEMPERATURE,
                    "instance": bad, "value": 25.0,
                },
            })
            assert response["payload"]["success"] is False
            assert "instance" in response["payload"]["error"]
        # null 视作空元组 → 空间分量因实例域不符被拒（仍是规范响应）
        response = handler["research_do"]({
            "payload": {
                "space": "node", "target": m.INSTANT_TEMPERATURE,
                "instance": None, "value": 25.0,
            },
        })
        assert response["payload"]["success"] is False

    def test_research_do_clear_rejects_bad_instance_types(self, weather_engine):
        from ascend.weather import mechanisms as m

        handler, _ = self._handler(weather_engine)
        response = handler["research_do_clear"]({
            "payload": {
                "space": "node", "target": m.INSTANT_TEMPERATURE, "instance": 3,
            },
        })
        assert response["payload"]["success"] is False
        assert "instance" in response["payload"]["error"]

    def test_research_do_feature_rejects_bad_instance_types(self, weather_engine):
        handler, _ = self._handler(weather_engine)
        response = handler["research_do"]({
            "payload": {"space": "feature", "target": "storm", "instance": 3},
        })
        assert response["payload"]["success"] is False
        assert "instance" in response["payload"]["error"]

    def test_research_do_clear_feature_unknown_type_is_handled(self, weather_engine):
        """回归：clear 的 feature 分支与 do 对称（不抛未捕获 ValueError）。"""
        handler, _ = self._handler(weather_engine)
        response = handler["research_do_clear"]({
            "payload": {"space": "feature", "target": "tsunami", "instance": [0, 0]},
        })
        assert response["payload"]["success"] is False
        assert "未知特征类型" in response["payload"]["error"]

    def test_research_do_clear_rejects_bad_rep(self, weather_engine):
        from ascend.weather import mechanisms as m

        handler, _ = self._handler(weather_engine)
        for rep in ("bogus", 3):
            response = handler["research_do_clear"]({
                "payload": {
                    "space": "node", "target": m.INSTANT_TEMPERATURE,
                    "instance": [0, 0], "rep": rep,
                },
            })
            assert response["payload"]["success"] is False
            assert "替换规格" in response["payload"]["error"]
        # rep 仅适用于 node 空间（与终端一致）
        response = handler["research_do_clear"]({
            "payload": {
                "space": "parameter", "target": "world.parameter.game_day_ticks",
                "rep": "value",
            },
        })
        assert response["payload"]["success"] is False
        assert "node" in response["payload"]["error"]

    def test_research_do_feature_rejects_irrelevant_fields(self, weather_engine):
        """feature 空间没有帧/时长语义：无关字段一律拒绝（fail-closed）。"""
        handler, _ = self._handler(weather_engine)
        response = handler["research_do"]({
            "payload": {
                "space": "feature", "target": "storm", "instance": [0, 0],
                "duration": 5,
            },
        })
        assert response["payload"]["success"] is False
        assert "duration" in response["payload"]["error"]


# ── 可达性声明漂移巡检（WIRED_NODES == 实际求值点）──────────

class TestWiringDrift:
    def test_wired_nodes_match_evaluation_sites(self):
        """``WIRED_NODES`` 必须等于源码中求值入口实参对应的节点集合。

        扫描 weather_engine.py / region_tracker.py 中全部
        ``self.evaluate_node`` / ``self._evaluate`` 调用的首个实参
        （支持 ``m.X`` 与直接导入的符号），解析为节点 ID 后与声明比对；
        无法解析的实参直接失败——门禁不静默放行，声明腐烂即红。
        """
        from ascend.causal.world import WIRED_NODES
        from ascend.weather import mechanisms as m

        root = Path(__file__).resolve().parents[2] / "ascend" / "weather"
        pattern = re.compile(
            r"self\.(?:evaluate_node|_evaluate)\(\s*"
            r"([A-Za-z_][A-Za-z_0-9]*(?:\.[A-Za-z_][A-Za-z_0-9]*)?)"
        )
        actual: set[str] = set()
        for name in ("weather_engine.py", "region_tracker.py"):
            source = (root / name).read_text(encoding="utf-8")
            args = pattern.findall(source)
            assert args, f"{name} 未发现求值入口调用"
            for arg in args:
                symbol = arg[2:] if arg.startswith("m.") else arg
                value = getattr(m, symbol, None)
                assert isinstance(value, str), (
                    f"无法解析求值点实参 {arg!r}（须为机制模块中的节点符号）"
                )
                actual.add(value)
        assert frozenset(actual) == WIRED_NODES

    def test_wired_nodes_subset_of_declared(self):
        from ascend.causal.world import WIRED_NODES

        assert WIRED_NODES <= set(_registry().nodes)
        assert _registry().wired_parameters
