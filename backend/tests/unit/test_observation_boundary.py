"""观测边界测试（消费侧隔离；协议对拍见 tests/world） — 消费端真值隔离与最小 G 协议（世界基座 13 篇 ⑤a）。

- 消费路径（网络 handler）不得触达研究 trace 通道（静态漂移门禁）；
- G 观测映射：协议量化、白名单读出、只含标量、泄露检测有判别力；
- 消费查询与区域通报载荷不含研究通道专有字段。
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest



ROOT = Path(__file__).resolve().parents[2] / "ascend"

#: 消费路径不得导入的研究记录/时间线模块（研究通道有专用 handler）
_FORBIDDEN_IMPORTS = re.compile(
    r"(?:from\s+ascend\.world\.research\.(?:records|timeline)\b"
    r"|import\s+ascend\.world\.research\.(?:records|timeline)\b)"
)


class TestConsumerTruthIsolation:
    """网络消费路径不得触达研究 trace（研究通道有专用 handler）。"""

    def test_handlers_do_not_import_research_trace(self):
        exempt = {"research_handler.py"}
        offenders = []
        for path in sorted((ROOT / "net" / "handlers").glob("*.py")):
            if path.name in exempt:
                continue
            source = path.read_text(encoding="utf-8")
            if _FORBIDDEN_IMPORTS.search(source):
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
