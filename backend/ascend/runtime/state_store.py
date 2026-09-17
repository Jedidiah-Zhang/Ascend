"""世界状态提交存储 — 帧内影子、帧边界原子发布（WC-7.6）。

每条帧推进批次（或独立更新点）是一个**帧事务**：

- 写方（更新点）在事务内只做两类事：``stage``（纯值）与
  ``stage_apply``（对状态载体的应用动作）；提交前任何读者都看不到
  这些写入——载体的已提交值在提交的临界区内一次性替换；
- ``commit`` 在锁内依次执行全部应用动作并发布纯值，版本号 +1；
  任一写方抛错则 ``abort``，本帧全部影子写入丢弃（状态不变）；
- ``stage_after_commit`` 登记记录回调（事件发布），只在提交成功后
  于锁外执行——记录不得先于状态可见，被回滚的帧不得留事件；
- ``guard`` 返回提交锁，跨字段读取（序列化、聚合）在锁内进行，
  保证读到的是某一已提交版本，而不是半帧。

帧事务是**每线程**的：tick 线程的批次与 tile 生成线程的补齐互不
合并，各自在自己的边界提交；全局只有一条提交锁串行化发布。
无事务打开时（测试/独立调用）写入自动提交，语义等价于单条事务。

注意：本模块零世界语义（foundation 级）；事务边界由调度器与
各更新点自己声明。
"""

from __future__ import annotations

import threading
from contextlib import contextmanager
from typing import Callable, Iterator


class _Transaction:
    """单线程的帧事务影子区。"""

    __slots__ = ("staged", "appliers", "after_commit")

    def __init__(self) -> None:
        self.staged: dict[str, object] = {}
        self.appliers: list[Callable[[], None]] = []
        self.after_commit: list[Callable[[], None]] = []


class FrameStateStore:
    """帧事务存储：影子写入、原子提交、版本发布（事务按线程隔离）。"""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._local = threading.local()
        self._committed: dict[str, object] = {}
        self._version = 0

    def __repr__(self) -> str:
        with self._lock:
            return (
                f"FrameStateStore(v{self._version}, "
                f"values={len(self._committed)})"
            )

    def _transaction(self) -> _Transaction | None:
        return getattr(self._local, "txn", None)

    # ── 帧事务 ────────────────────────────────────────────

    @property
    def transaction_open(self) -> bool:
        """当前线程是否有未闭合的帧事务。"""
        return self._transaction() is not None

    @property
    def version(self) -> int:
        """已提交版本号（每次提交 +1）。"""
        with self._lock:
            return self._version

    def begin_frame(self) -> None:
        """打开当前线程的帧事务。

        Raises:
            RuntimeError: 本线程已有未闭合事务（禁止嵌套）。
        """
        if self._transaction() is not None:
            raise RuntimeError("帧事务未闭合：begin_frame 重入")
        self._local.txn = _Transaction()

    def commit(self) -> int:
        """提交当前线程的帧事务：应用影子写入并发布，返回新版本号。

        Raises:
            RuntimeError: 本线程无打开的事务。
        """
        txn = self._transaction()
        if txn is None:
            raise RuntimeError("帧事务未打开：commit 无对象")
        self._local.txn = None
        with self._lock:
            for apply in txn.appliers:
                apply()
            self._committed.update(txn.staged)
            self._version += 1
            version = self._version
        # 记录回调在锁外执行：状态已可见，且不阻塞其他帧
        for hook in txn.after_commit:
            hook()
        return version

    def abort(self) -> None:
        """丢弃当前线程帧事务的全部影子写入（状态不变）。

        Raises:
            RuntimeError: 本线程无打开的事务。
        """
        if self._transaction() is None:
            raise RuntimeError("帧事务未打开：abort 无对象")
        self._local.txn = None

    @contextmanager
    def frame(self) -> Iterator["FrameStateStore"]:
        """帧事务上下文：正常退出提交，异常回滚后重抛。"""
        self.begin_frame()
        try:
            yield self
        except BaseException:
            self.abort()
            raise
        self.commit()

    # ── 写方接口 ──────────────────────────────────────────

    def stage(self, key: str, value: object) -> None:
        """暂存一个纯值；当前线程无事务时立即提交。"""
        txn = self._transaction()
        if txn is not None:
            txn.staged[key] = value
            return
        with self._lock:
            self._committed[key] = value
            self._version += 1

    def stage_apply(self, apply: Callable[[], None]) -> None:
        """暂存一个应用动作（提交时在锁内执行）；无事务时立即执行。"""
        txn = self._transaction()
        if txn is not None:
            txn.appliers.append(apply)
            return
        with self._lock:
            apply()
            self._version += 1

    def stage_after_commit(self, hook: Callable[[], None]) -> None:
        """登记提交成功后（锁外）执行的记录回调；无事务时立即执行。"""
        txn = self._transaction()
        if txn is not None:
            txn.after_commit.append(hook)
            return
        hook()

    # ── 读方接口 ──────────────────────────────────────────

    def get(self, key: str, default: object = None) -> object:
        """读取已提交纯值。"""
        with self._lock:
            return self._committed.get(key, default)

    def snapshot(self) -> dict[str, object]:
        """已提交纯值的浅拷贝（帧一致视图）。"""
        with self._lock:
            return dict(self._committed)

    def guard(self):
        """提交锁（可作上下文管理器）。

        跨字段读取方（序列化、聚合）持锁读取，保证不观察半帧提交。
        """
        return self._lock
