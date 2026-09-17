"""帧调度器与世界树角色门禁测试。

对应《世界契约》WC-4（机制求值纪律）与
docs/研究理论/世界基座/12-世界树角色与帧调度.md：
- 世界状态更新只能由声明更新点触发（注册顺序 = 执行顺序）；
- 驱动信号来自时钟，不经世界树事件；
- 世界写路径所在包不得订阅世界树或时钟（唯一驱动者是 FrameScheduler）；
- 一次推进批次是一个帧事务：写方影子提交，失败整帧回滚（WC-7.6）。
"""

import re
from pathlib import Path
from types import SimpleNamespace

import pytest

from ascend.runtime import FrameScheduler, FrameStateStore
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


class TestFrameTransaction:
    """一次推进批次 = 一个帧事务（WC-7.6）。"""

    def test_writes_invisible_until_batch_commit(self):
        store = FrameStateStore()
        scheduler = FrameScheduler(store=store)
        seen: dict[str, object] = {}

        def callback(now: int) -> None:
            seen["before"] = store.get("k")
            store.stage("k", now)

        scheduler.register("p", period=1, callback=callback)
        scheduler.advance(1)
        assert seen["before"] is None
        assert store.get("k") == 1
        assert store.version == 1

    def test_failing_point_rolls_back_and_retries(self):
        store = FrameStateStore()
        scheduler = FrameScheduler(store=store)
        runs: list[str] = []
        state = {"fail": True}

        def first(now: int) -> None:
            runs.append("first")
            store.stage("k", now)

        def flaky(now: int) -> None:
            runs.append("flaky")
            if state["fail"]:
                state["fail"] = False
                raise RuntimeError("boom")

        scheduler.register("a", period=1, callback=first)
        scheduler.register("b", period=1, callback=flaky)
        with pytest.raises(RuntimeError, match="boom"):
            scheduler.advance(1)
        # 整帧回滚：影子值丢弃、版本不变
        assert store.get("k") is None
        assert store.version == 0
        # 边界回滚：下一帧两个更新点都重试
        scheduler.advance(1)
        assert runs == ["first", "flaky", "first", "flaky"]
        assert store.get("k") == 1

    def test_records_skipped_on_abort(self):
        store = FrameStateStore()
        scheduler = FrameScheduler(store=store)
        hooks: list[int] = []

        def callback(now: int) -> None:
            store.stage_after_commit(lambda: hooks.append(now))
            raise RuntimeError("boom")

        scheduler.register("p", period=1, callback=callback)
        with pytest.raises(RuntimeError, match="boom"):
            scheduler.advance(1)
        assert hooks == []


    def test_commit_failure_rolls_back_boundary(self):
        """提交阶段失败：边界回滚，下一帧重试（幂等动作重放）。"""
        store = FrameStateStore()
        scheduler = FrameScheduler(store=store)
        runs: list = []
        state = {"fail": True}

        def callback(now: int) -> None:
            runs.append(now)

            def apply() -> None:
                if state["fail"]:
                    raise ValueError("applier boom")
                runs.append("applied")

            store.stage_apply(apply)

        scheduler.register("p", period=1, callback=callback)
        with pytest.raises(RuntimeError, match="帧提交失败"):
            scheduler.advance(1)
        assert runs == [1]
        assert store.version == 0
        state["fail"] = False
        scheduler.advance(1)
        assert runs == [1, 1, "applied"]
        assert store.version == 1

    def test_persistent_failure_backs_off_then_recovers(self):
        """连续失败指数退避：退避窗口内不重试，恢复后重试并清零。"""
        store = FrameStateStore()
        scheduler = FrameScheduler(store=store)
        attempts: list[int] = []
        state = {"fail": True}

        def flaky(now: int) -> None:
            attempts.append(now)
            if state["fail"]:
                raise RuntimeError("boom")

        scheduler.register("p", period=1, callback=flaky)
        assert scheduler.consecutive_failures == 0
        # 首次失败：下一帧重试（退避 0）
        with pytest.raises(RuntimeError):
            scheduler.advance(1)
        assert scheduler.consecutive_failures == 1
        # 第二次失败：退避 1 tick（1 + 1 = 2 前不重试）
        with pytest.raises(RuntimeError):
            scheduler.advance(1)
        assert scheduler.consecutive_failures == 2
        scheduler.advance(1)  # 退避窗口内：跳过、不重抛
        assert attempts == [1, 1]
        # 第三次失败：退避 2 tick（2 + 2 = 4）
        with pytest.raises(RuntimeError):
            scheduler.advance(2)
        scheduler.advance(3)
        assert attempts == [1, 1, 2]
        # 第四次失败：退避 4 tick（4 + 4 = 8）
        with pytest.raises(RuntimeError):
            scheduler.advance(4)
        scheduler.advance(7)
        assert attempts == [1, 1, 2, 4]
        state["fail"] = False
        scheduler.advance(8)  # 恢复：提交成功、计数清零
        assert attempts == [1, 1, 2, 4, 8]
        assert scheduler.consecutive_failures == 0
        assert store.version == 1

    def test_backoff_cap(self):
        """退避按 2 的幂增长并封顶 64 tick（第 8 次失败起不再放大）。"""
        scheduler = FrameScheduler(store=FrameStateStore())

        def always_fail(now: int) -> None:
            raise RuntimeError("boom")

        scheduler.register("p", period=1, callback=always_fail)
        deltas: list[int] = []
        now = 1
        for _ in range(10):
            with pytest.raises(RuntimeError):
                scheduler.advance(now)
            deltas.append(scheduler._retry_not_before - now)
            now = scheduler._retry_not_before
        assert deltas == [0, 1, 2, 4, 8, 16, 32, 64, 64, 64]

    def test_backoff_bypassed_by_large_skip(self):
        """skip 大跳越过后退避窗口 → 立即重试（不等待）。"""
        scheduler = FrameScheduler(store=FrameStateStore())
        calls: list[int] = []

        def always_fail(now: int) -> None:
            calls.append(now)
            raise RuntimeError("boom")

        scheduler.register("p", period=1, callback=always_fail)
        with pytest.raises(RuntimeError):
            scheduler.advance(1)
        with pytest.raises(RuntimeError):
            scheduler.advance(1)   # 第二次失败 → 退避至 tick 2
        scheduler.advance(1)       # 窗口内：跳过
        assert calls == [1, 1]
        with pytest.raises(RuntimeError):
            scheduler.advance(10_000)
        assert calls == [1, 1, 10_000]
