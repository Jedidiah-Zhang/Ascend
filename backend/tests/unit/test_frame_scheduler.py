"""帧调度器与世界树角色门禁测试。

对应《世界契约》WC-4（机制求值纪律）与
docs/研究理论/世界基座/12-世界树角色与帧调度.md：
- 世界状态更新只能由声明更新点触发（注册顺序 = 执行顺序）；
- 驱动信号来自时钟，不经世界树事件；
- 世界写路径所在包不得订阅世界树或时钟（唯一驱动者是 FrameScheduler）。
"""

import re
from pathlib import Path
from types import SimpleNamespace

import pytest

from ascend.runtime import FrameScheduler
from ascend.time import WorldClock


class _Log:
    def __init__(self) -> None:
        self.calls: list[tuple[str, int]] = []

    def make(self, name: str):
        return lambda now: self.calls.append((name, now))


class TestFrameScheduler:
    def test_registration_order_is_execution_order(self):
        log = _Log()
        scheduler = FrameScheduler()
        scheduler.register("b", period=1, callback=log.make("b"))
        scheduler.register("a", period=1, callback=log.make("a"))
        scheduler.advance(1)
        assert log.calls == [("b", 1), ("a", 1)]

    def test_period_gating(self):
        log = _Log()
        scheduler = FrameScheduler()
        scheduler.register("minute", period=120, callback=log.make("minute"))
        scheduler.advance(119)
        assert log.calls == []
        scheduler.advance(120)
        assert log.calls == [("minute", 120)]
        scheduler.advance(121)
        assert log.calls == [("minute", 120)]
        scheduler.advance(240)
        assert log.calls == [("minute", 120), ("minute", 240)]

    def test_skip_catch_up_fires_once(self):
        """跨多边界（快进）每次推进至多触发一次；补齐由系统状态游标负责。"""
        log = _Log()
        scheduler = FrameScheduler()
        scheduler.register("hour", period=7200, callback=log.make("hour"))
        scheduler.advance(10 * 7200)
        assert log.calls == [("hour", 10 * 7200)]

    def test_duplicate_name_rejected(self):
        scheduler = FrameScheduler()
        scheduler.register("x", period=1, callback=lambda now: None)
        with pytest.raises(ValueError, match="重复"):
            scheduler.register("x", period=1, callback=lambda now: None)

    @pytest.mark.parametrize("period", [0, -1, 1.5, "1", True])
    def test_invalid_period_rejected(self, period):
        scheduler = FrameScheduler()
        with pytest.raises(ValueError):
            scheduler.register("x", period=period, callback=lambda now: None)

    def test_clock_binding_drives_tick_and_skip(self):
        log = _Log()
        clock = WorldClock(epoch=0)
        scheduler = FrameScheduler(clock=clock)
        scheduler.register("frame", period=1, callback=log.make("frame"))
        clock.tick()
        assert ("frame", 1) in log.calls
        before = len(log.calls)
        clock.skip(5)
        assert len(log.calls) == before + 1
        assert log.calls[-1] == ("frame", 6)

    def test_deterministic_sequence(self):
        def run() -> list[tuple[str, int]]:
            log = _Log()
            scheduler = FrameScheduler()
            scheduler.register("a", period=3, callback=log.make("a"))
            scheduler.register("b", period=7, callback=log.make("b"))
            for now in range(0, 30):
                scheduler.advance(now)
            return log.calls

        assert run() == run()

    def test_shutdown_stops_clock_driving(self):
        log = _Log()
        clock = WorldClock(epoch=0)
        scheduler = FrameScheduler(clock=clock)
        scheduler.register("frame", period=1, callback=log.make("frame"))
        scheduler.shutdown()
        clock.tick()
        assert log.calls == []


class TestWorldTreeRoleGate:
    """世界写路径的包不得自建订阅（唯一驱动者 = FrameScheduler）。"""

    def test_no_subscriptions_in_world_packages(self):
        root = Path(__file__).resolve().parents[2] / "ascend"
        targets = (root / "space", root / "weather", root / "game.py")
        pattern = re.compile(r"\.(?:subscribe|on_tick|on_skip)\(")
        offenders: list[str] = []
        for target in targets:
            files = (
                sorted(target.rglob("*.py"))
                if target.is_dir()
                else [target]
            )
            for path in files:
                if path.name == "__init__.py":
                    continue
                for number, line in enumerate(
                    path.read_text(encoding="utf-8").splitlines(), 1,
                ):
                    code = line.split("#", 1)[0]
                    if pattern.search(code):
                        offenders.append(
                            f"{path.relative_to(root)}:{number}: {line.strip()}"
                        )
        assert offenders == [], (
            "世界写路径不得订阅世界树/时钟（应注册到 FrameScheduler）: "
            + "; ".join(offenders)
        )
