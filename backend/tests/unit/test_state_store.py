"""帧事务状态存储测试：影子写入、原子提交、回滚、守卫与版本。"""

import threading

import pytest

from ascend.runtime import FrameStateStore


class TestLifecycle:
    def test_stage_visible_only_after_commit(self):
        store = FrameStateStore()
        store.begin_frame()
        store.stage("k", 1)
        assert store.get("k") is None
        assert store.snapshot() == {}
        version = store.commit()
        assert store.get("k") == 1
        assert store.snapshot() == {"k": 1}
        assert version == 1
        assert store.version == 1
        assert not store.transaction_open

    def test_abort_discards_staged(self):
        store = FrameStateStore()
        store.begin_frame()
        store.stage("k", 1)
        store.abort()
        assert store.get("k") is None
        assert store.version == 0
        assert not store.transaction_open

    def test_applier_runs_inside_commit(self):
        store = FrameStateStore()
        applied: list[str] = []
        store.begin_frame()
        store.stage_apply(lambda: applied.append("apply"))
        store.stage("k", 1)
        assert applied == []
        store.commit()
        assert applied == ["apply"]
        assert store.get("k") == 1

    def test_after_commit_hook_sees_committed_value(self):
        store = FrameStateStore()
        seen: list[object] = []
        store.begin_frame()
        store.stage("k", 7)
        store.stage_after_commit(lambda: seen.append(store.get("k")))
        assert seen == []
        store.commit()
        assert seen == [7]

    def test_after_commit_hook_not_run_on_abort(self):
        store = FrameStateStore()
        called: list[bool] = []
        store.begin_frame()
        store.stage_after_commit(lambda: called.append(True))
        store.abort()
        assert called == []

    def test_nested_begin_rejected(self):
        store = FrameStateStore()
        store.begin_frame()
        with pytest.raises(RuntimeError, match="重入"):
            store.begin_frame()
        store.abort()

    def test_commit_without_frame_rejected(self):
        store = FrameStateStore()
        with pytest.raises(RuntimeError, match="无对象"):
            store.commit()
        with pytest.raises(RuntimeError, match="无对象"):
            store.abort()


class TestAutoCommit:
    """无帧事务时（测试/独立调用）写入自动提交。"""

    def test_stage_commits_immediately(self):
        store = FrameStateStore()
        store.stage("k", 1)
        assert store.get("k") == 1
        assert store.version == 1

    def test_stage_apply_runs_immediately(self):
        store = FrameStateStore()
        applied: list[str] = []
        store.stage_apply(lambda: applied.append("now"))
        assert applied == ["now"]
        assert store.version == 1

    def test_after_commit_runs_immediately(self):
        store = FrameStateStore()
        called: list[bool] = []
        store.stage_after_commit(lambda: called.append(True))
        assert called == [True]


class TestFrameContext:
    def test_frame_context_commits(self):
        store = FrameStateStore()
        with store.frame():
            store.stage("k", 1)
        assert store.get("k") == 1

    def test_frame_context_rolls_back_on_error(self):
        store = FrameStateStore()
        with pytest.raises(ValueError):
            with store.frame():
                store.stage("k", 1)
                raise ValueError("boom")
        assert store.get("k") is None
        assert not store.transaction_open


class TestGuard:
    def test_guard_blocks_commit(self):
        store = FrameStateStore()
        store.stage("k", 1)
        entered = threading.Event()
        release = threading.Event()

        def reader() -> None:
            with store.guard():
                entered.set()
                release.wait(timeout=5.0)

        reader_thread = threading.Thread(target=reader)
        reader_thread.start()
        assert entered.wait(timeout=2.0)

        committed = threading.Event()

        def writer() -> None:
            store.begin_frame()
            store.stage("k", 2)
            store.commit()
            committed.set()

        writer_thread = threading.Thread(target=writer)
        writer_thread.start()
        assert not committed.wait(timeout=0.2), "提交不得越过持有守卫的读者"
        release.set()
        writer_thread.join(timeout=5.0)
        reader_thread.join(timeout=5.0)
        assert committed.is_set()
        assert store.get("k") == 2


class TestThreadIsolation:
    """帧事务按线程隔离：工作线程补齐不与 tick 批次合并。"""

    def test_transactions_are_thread_local(self):
        store = FrameStateStore()
        store.begin_frame()
        assert store.transaction_open
        other: list[bool] = []

        def worker() -> None:
            other.append(store.transaction_open)
            store.stage("k", "worker")  # 自动提交（本线程无事务）

        thread = threading.Thread(target=worker)
        thread.start()
        thread.join(timeout=5.0)
        assert other == [False]
        assert store.transaction_open, "主线程事务不受工作线程影响"
        assert store.get("k") == "worker"
        store.abort()

    def test_concurrent_frames_commit_independently(self):
        store = FrameStateStore()
        barrier = threading.Barrier(2)
        results: dict[str, int] = {}

        def worker(name: str) -> None:
            with store.frame():
                barrier.wait(timeout=5.0)
                store.stage(name, name)
            results[name] = store.version

        threads = [
            threading.Thread(target=worker, args=(name,))
            for name in ("a", "b")
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=5.0)
        assert store.get("a") == "a"
        assert store.get("b") == "b"
        assert store.version == 2
        assert sorted(results.values()) == [1, 2]


class TestCommitFailure:
    """应用动作抛错：异常向调用方传播，帧可重试（幂等动作安全重放）。

    已执行动作不回滚、记录回调不执行——重试帧重新登记后再执行（文档化语义）。
    """

    def test_applier_failure_propagates(self):
        store = FrameStateStore()
        applied: list[str] = []

        def failing() -> None:
            raise ValueError("applier boom")

        store.begin_frame()
        store.stage_apply(lambda: applied.append("first"))
        store.stage_apply(failing)
        with pytest.raises(RuntimeError, match="帧提交失败"):
            store.commit()
        assert applied == ["first"], "已执行动作不回滚（文档化语义）"
        assert store.version == 0
        assert not store.transaction_open, "失败后事务已摘下"

    def test_after_commit_hooks_dropped_on_commit_failure(self):
        """提交失败：已登记记录回调一律不执行；重试帧重新登记后执行。"""
        store = FrameStateStore()
        hooks: list[str] = []
        state = {"fail": True}

        def apply() -> None:
            if state["fail"]:
                raise ValueError("applier boom")

        store.begin_frame()
        store.stage_apply(apply)
        store.stage_after_commit(lambda: hooks.append("first"))
        with pytest.raises(RuntimeError, match="帧提交失败"):
            store.commit()
        assert hooks == [], "失败帧的记录回调不得执行"
        state["fail"] = False
        store.begin_frame()
        store.stage_apply(apply)
        store.stage_after_commit(lambda: hooks.append("retry"))
        store.commit()
        assert hooks == ["retry"]

    def test_frame_retry_succeeds(self):
        store = FrameStateStore()
        state = {"fail": True}
        applied: list[str] = []

        def flaky() -> None:
            if state["fail"]:
                state["fail"] = False
                raise ValueError("boom")
            applied.append("ok")

        with pytest.raises(RuntimeError):
            with store.frame():
                store.stage_apply(flaky)
        with store.frame():
            store.stage_apply(flaky)
        assert applied == ["ok"]
        assert store.version == 1
