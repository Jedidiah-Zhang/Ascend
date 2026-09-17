"""观测边界测试 — 消费端真值隔离与最小 G 协议（世界基座 13 篇 ⑤a）。

- 消费路径（网络 handler）不得触达研究 trace 通道（静态漂移门禁）；
- G 观测映射：协议量化、白名单读出、只含标量、泄露检测有判别力；
- 消费查询与区域通报载荷不含研究通道专有字段。
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from ascend.causal import (
    AGENT_WEATHER_PROTOCOL,
    RESEARCH_PROTOCOL,
    leaks_research_truth,
    observe,
    protocol_nodes,
)

ROOT = Path(__file__).resolve().parents[2] / "ascend"

#: 研究通道专有字段（消费载荷出现即泄露）
FORBIDDEN = {
    "equation_version", "resolved_version", "parents",
    "random_addresses", "microstep", "trace",
}


class TestConsumerTruthIsolation:
    """网络消费路径不得触达研究 trace（研究通道有专用 handler）。"""

    def test_handlers_do_not_import_research_trace(self):
        exempt = {"research_handler.py"}
        pattern = re.compile(
            r"(?:from\s+ascend\.causal\s+import[^\n]*\bTrace"
            r"|from\s+ascend\.causal\.trace\b"
            r"|import\s+ascend\.causal\.trace\b)"
        )
        offenders = []
        for path in sorted((ROOT / "net" / "handlers").glob("*.py")):
            if path.name in exempt:
                continue
            source = path.read_text(encoding="utf-8")
            if pattern.search(source):
                offenders.append(path.name)
        assert offenders == [], (
            f"消费 handler 不得导入研究 trace（应走 G 观测或研究 handler）: "
            f"{offenders}"
        )

    def test_weather_query_payload_only_scalars(self):
        """天气查询返回只含标量（无嵌套真值结构）。"""
        from ascend.space import ClimateZone, WeatherParams
        from ascend.time import WorldClock
        from ascend.weather.weather_engine import WeatherEngine
        from ascend.world_tree import WorldTree

        engine = WeatherEngine(WorldClock(), seed=42, world_tree_arg=WorldTree())
        engine.register_chunk(
            0, 0, WeatherParams(20.0, 800.0, 12.0, 100.0, 60.0, 5.0),
            ClimateZone.TEMPERATE_FOREST, 15.0,
        )
        report = engine.get_weather_report(0, 0)
        assert report is not None
        weather, *rest = report
        scalars = (
            weather.temperature, weather.rainfall, weather.sunshine,
            weather.altitude, weather.humidity, weather.wind_speed, *rest,
        )
        for value in scalars:
            assert value is None or isinstance(value, (int, float))
        engine.shutdown()


class TestObserveProtocol:
    """最小 G：按协议量化、白名单、只含标量。"""

    _WORLD = {
        "weather.instant.temperature_c": 12.3456,
        "weather.instant.relative_humidity_percent": 63.21,
        "weather.chunk.precipitation_threshold": 0.4,
    }

    def test_agent_protocol_quantizes(self):
        payload = observe(
            AGENT_WEATHER_PROTOCOL, self._WORLD,
            allow=("weather.instant.temperature_c",),
        )
        assert payload == {"weather.instant.temperature_c": 12.3}

    def test_research_protocol_raw(self):
        payload = observe(
            RESEARCH_PROTOCOL, self._WORLD,
            allow=("weather.instant.temperature_c",),
        )
        assert payload == {"weather.instant.temperature_c": 12.3456}

    def test_allow_list_restricts(self):
        payload = observe(
            AGENT_WEATHER_PROTOCOL, self._WORLD,
            allow=("weather.instant.relative_humidity_percent",),
        )
        assert set(payload) == {"weather.instant.relative_humidity_percent"}

    def test_unknown_protocol_rejected(self):
        with pytest.raises(ValueError, match="未声明"):
            observe("agent.unknown.v1", self._WORLD, allow=())
        with pytest.raises(ValueError, match="未声明"):
            protocol_nodes("agent.unknown.v1", ())

    def test_leak_detector_has_teeth(self):
        assert leaks_research_truth({"a": 1, "b": 1.5}) is False
        assert leaks_research_truth({"a": {"equation_version": "v"}}) is True
        assert leaks_research_truth({"a": ["addr"]}) is True
        payload = observe(
            AGENT_WEATHER_PROTOCOL, self._WORLD,
            allow=tuple(self._WORLD),
        )
        assert leaks_research_truth(payload) is False


class TestRegionNoticeIsolation:
    """区域降水通报是观察输出：载荷只含通报字段，无研究真值。"""

    def test_region_event_payload_clean(self):
        from ascend.causal import PlannedIntervention
        from ascend.config import GAME_MINUTE
        from ascend.space import ClimateZone
        from ascend.time import WorldClock
        from ascend.weather import mechanisms as m
        from ascend.weather.weather_engine import WeatherEngine
        from ascend.world_tree import WorldTree

        from tests.unit.test_weather import _find_dry_chunk, _make_baseline

        wt = WorldTree()
        events: list = []
        wt.subscribe("precipitation_start", lambda e: events.append(e))
        clock = WorldClock()
        domain: list[tuple[int, int]] = []
        engine = WeatherEngine(
            clock, seed=42, world_tree_arg=wt,
            climate_lookup=lambda cx, cy: (800.0, 5.0),
            region_domain=lambda: tuple(domain),
        )
        now = clock.time
        dry = _find_dry_chunk(engine, now)
        assert dry is not None
        domain.append(dry)
        engine.register_chunk(
            dry[0], dry[1], _make_baseline(temp=20.0),
            ClimateZone.TEMPERATE_FOREST, 15.0,
        )
        engine.intervention_table.plan(PlannedIntervention(
            target_space="node", target=m.PRECIPITATION_THRESHOLD,
            instance=dry, value=0.25,
            start_frame=now, stop_frame=now + GAME_MINUTE, source="test",
        ))
        engine.advance(now)
        assert events, "前提：窗口干预应派发区域 start"
        data = events[0].data
        assert not (FORBIDDEN & set(data))
        assert set(data) == {
            "intensity", "precip_type", "time_of_day", "chunks",
        }
        engine.shutdown()
