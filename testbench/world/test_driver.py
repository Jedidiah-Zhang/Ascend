"""帧调度器测试— 声明周期绑定、帧事务与失效语义。

对应《世界契约》WC-4（机制求值纪律）：
- 世界状态更新只能由声明更新点触发（注册顺序 = 执行顺序）；
- 驱动信号来自时钟，不经世界树事件；
- 世界写路径所在包不得订阅世界树或时钟（唯一驱动者是 FrameScheduler）；
- 一次推进是一个帧事务：写方影子提交，提交前失败整帧回滚重试，
  提交相位失败世界失效（WC-7.6 / WC-9.2）。
"""

import re
from pathlib import Path
from types import SimpleNamespace

import pytest

from olam.runtime import (
    FrameScheduler,
    FrameStateStore,
    WorldInvalidatedError,
    bind_periods,
)
from olam.runtime import WorldClock


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
        repo = Path(__file__).resolve().parents[2]
        targets = (repo / "olam" / "adapters",
                   repo / "olam" / "generation",
                   repo / "miskhak" / "app.py")
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
                            f"{path.relative_to(repo)}:{number}: {line.strip()}"
                        )
        assert offenders == [], (
            "世界写路径不得订阅世界树/时钟（应注册到 FrameScheduler）: "
            + "; ".join(offenders)
        )


class TestFrameTransaction:
    """一次推进 = 一个帧事务（WC-7.6）。"""

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


    def test_applier_failure_invalidates_world_without_replay(self):
        """提交中失败：世界失效——不重放、不重试。"""
        store = FrameStateStore()
        scheduler = FrameScheduler(store=store)
        runs: list = []

        def callback(now: int) -> None:
            runs.append(now)

            def apply() -> None:
                raise ValueError("applier boom")

            store.stage_apply(apply)

        scheduler.register("p", period=1, callback=callback)
        with pytest.raises(WorldInvalidatedError, match="帧提交失败"):
            scheduler.advance(1)
        assert runs == [1]
        assert store.version == 0
        assert scheduler.invalidated is not None
        # 不重试：再次推进直接拒绝，回调不再执行（同一帧不重复更新）
        with pytest.raises(WorldInvalidatedError):
            scheduler.advance(1)
        assert runs == [1]

    def test_record_failure_after_commit_does_not_replay_frame(self):
        """提交后记录回调失败：状态已提交一次，世界失效且不重放。

        边界不得回退重放同一帧：第二次推进必须直接拒绝，计数保持 1。
        """
        store = FrameStateStore()
        scheduler = FrameScheduler(store=store)
        runs: list[int] = []

        def callback(now: int) -> None:
            runs.append(now)
            store.stage("n", len(runs))

            def bad_hook() -> None:
                raise RuntimeError("record boom")

            store.stage_after_commit(bad_hook)

        scheduler.register("p", period=1, callback=callback)
        with pytest.raises(WorldInvalidatedError, match="已提交"):
            scheduler.advance(1)
        assert store.get("n") == 1, "已提交状态保持一次"
        assert store.version == 1
        assert scheduler.invalidated is not None
        with pytest.raises(WorldInvalidatedError):
            scheduler.advance(1)
        assert runs == [1], "同一逻辑帧不得重复执行"

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


class TestBindPeriods:
    """声明周期绑定（唯一执行入口 = 声明的周期；fail-closed）。"""

    def _schedule(self):
        from olam.assembly import build_game_program

        return build_game_program().schedule

    def test_registers_in_declared_order(self):
        from olam.constants import GAME_HOUR

        scheduler = FrameScheduler()
        log: list[tuple[str, int]] = []
        keys = bind_periods(self._schedule(), scheduler, {
            "minute": lambda now: log.append(("minute", now)),
            "hour": lambda now: log.append(("hour", now)),
        })
        assert keys == ("minute", "hour")
        scheduler.advance(GAME_HOUR)
        assert log == [("minute", GAME_HOUR), ("hour", GAME_HOUR)]

    def test_missing_binding_rejected(self):
        with pytest.raises(ValueError, match="缺少"):
            bind_periods(self._schedule(), FrameScheduler(), {
                "minute": lambda now: None,
            })

    def test_extra_binding_rejected(self):
        with pytest.raises(ValueError, match="多余"):
            bind_periods(self._schedule(), FrameScheduler(), {
                "minute": lambda now: None,
                "hour": lambda now: None,
                "second": lambda now: None,
            })

    def test_period_ticks_come_from_declaration(self):
        from olam.constants import GAME_MINUTE

        scheduler = FrameScheduler()
        calls: list[int] = []
        bind_periods(self._schedule(), scheduler, {
            "minute": lambda now: calls.append(now),
            "hour": lambda now: None,
        })
        scheduler.advance(GAME_MINUTE - 1)
        assert calls == []
        scheduler.advance(GAME_MINUTE)
        assert calls == [GAME_MINUTE]
