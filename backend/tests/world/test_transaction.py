"""帧事务提交存储测试（P2-3c-3）— 影子写入、原子提交、回滚、守卫与版本。

对应《世界契约》WC-7.6 / WC-9.2；存储是驱动层设施（零世界语义），
由引擎（事件/缓存）与调度器（帧边界）共用。
"""

import threading

import pytest

from ascend.world.runtime import FrameStateStore, WorldInvalidatedError


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


class TestCommitPhaseFailure:
    """提交相位失败 → 世界失效、不重放（WC-9.2 / #51）。

    与提交前失败（回调抛错，可回滚重试）不同：应用动作/记录回调位于
    提交相位，状态可能已部分应用或已提交——此时回滚不可行，世界失效
    （轨迹作废），写路径一律 fail-closed。
    """

    def test_applier_failure_invalidates_world(self):
        store = FrameStateStore()
        applied: list[str] = []

        def failing() -> None:
            raise ValueError("applier boom")

        store.begin_frame()
        store.stage_apply(lambda: applied.append("first"))
        store.stage_apply(failing)
        with pytest.raises(WorldInvalidatedError, match="帧提交失败"):
            store.commit()
        assert applied == ["first"], "已执行动作不回滚（失效前副作用保留）"
        assert store.version == 0
        assert not store.transaction_open
        assert store.invalidated is not None
        # 失效后：读仍可诊断；写/提交一律拒绝
        assert store.get("k") is None
        with pytest.raises(WorldInvalidatedError):
            store.begin_frame()
        with pytest.raises(WorldInvalidatedError):
            store.stage("k", 1)

    def test_record_hook_failure_invalidates_after_publish(self):
        """记录回调失败：状态已提交可见（不得回滚），世界失效且不重放。"""
        store = FrameStateStore()
        hooks: list[str] = []

        def bad_hook() -> None:
            raise RuntimeError("hook boom")

        store.begin_frame()
        store.stage("k", 1)
        store.stage_after_commit(bad_hook)
        store.stage_after_commit(lambda: hooks.append("after"))
        with pytest.raises(WorldInvalidatedError, match="已提交"):
            store.commit()
        assert store.get("k") == 1, "已发布的状态不得被回滚"
        assert store.version == 1
        assert store.invalidated is not None
        assert hooks == [], "失败点之后的记录回调不再执行"

    def test_auto_commit_paths_refused_after_invalidation(self):
        store = FrameStateStore()

        def failing() -> None:
            raise ValueError("boom")

        store.begin_frame()
        store.stage_apply(failing)
        with pytest.raises(WorldInvalidatedError):
            store.commit()
        with pytest.raises(WorldInvalidatedError):
            store.stage("x", 1)
        with pytest.raises(WorldInvalidatedError):
            store.stage_apply(lambda: None)
        with pytest.raises(WorldInvalidatedError):
            store.stage_after_commit(lambda: None)
