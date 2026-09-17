"""帧调度器 — 世界状态更新的唯一执行入口（声明更新点驱动）。

设计决定（docs/研究理论/世界基座/12-世界树角色与帧调度.md）：
- 世界状态的演化只能由**声明的更新点**触发，按注册顺序确定性执行；
- 信号来自时钟（tick / skip，驱动层），**不经过世界树事件**；
- 每个更新点每次推进至多触发一次；跨多边界（快进）由系统自身
  按状态游标补齐（义务即槽位，WC-3.3）；
- 世界树只承担记录/观测分发，订阅者不得回写世界状态；
- 一次推进批次是**一个帧事务**（WC-7.6）：写方在事务内只写影子，
  批次末尾原子提交；任一更新点抛错则整帧回滚（状态与边界都回退，
  下一帧重试）——记录回调只在提交成功后执行。
"""

from __future__ import annotations

import threading
from dataclasses import dataclass
from typing import Callable

from ascend.world_tree import SubscriptionScope

from .state_store import FrameStateStore


@dataclass(frozen=True, slots=True)
class UpdatePoint:
    """一个声明更新点。

    Attributes:
        name: 全局唯一名称（重名注册即拒绝）。
        period: 周期（游戏 tick）；``now // period`` 推进即视为越过边界。
        callback: 触发时调用 ``callback(now)``。
    """

    name: str
    period: int
    callback: Callable[[int], None]


class FrameScheduler:
    """按声明顺序驱动世界更新点（唯一执行入口）。"""

    def __init__(self, clock=None, store: FrameStateStore | None = None) -> None:
        self._points: list[UpdatePoint] = []
        self._names: set[str] = set()
        # 已处理到的边界（tick）；0 表示"尚未越过任何边界"。
        self._last_boundary: dict[str, int] = {}
        # 帧事务存储：一次推进批次的原子提交点（WC-7.6）。
        self.store = store if store is not None else FrameStateStore()
        # 推进串行锁：tick 线程与直接 skip 调用可能并发进入推进。
        self._advance_lock = threading.RLock()
        self._scope = SubscriptionScope()
        if clock is not None:
            self.bind_clock(clock)

    def bind_clock(self, clock) -> None:
        """订阅时钟推进信号（驱动层；非世界树事件）。"""
        self._scope.capture(clock.on_tick(
            lambda game_time: self.advance(game_time),
        ))
        self._scope.capture(clock.on_skip(
            lambda skipped, game_time: self.advance(game_time),
        ))

    def register(
        self,
        name: str,
        *,
        period: int,
        callback: Callable[[int], None],
    ) -> UpdatePoint:
        """注册一个声明更新点（注册顺序即执行顺序）。

        Raises:
            ValueError: 名称为空/重复，或周期非正整数。
        """
        if not isinstance(name, str) or not name:
            raise ValueError(f"更新点名称必须为非空字符串: {name!r}")
        if name in self._names:
            raise ValueError(f"更新点重复注册: {name}")
        if type(period) is not int or period <= 0:
            raise ValueError(f"周期必须为正整数 tick: {period!r}")
        point = UpdatePoint(name=name, period=period, callback=callback)
        self._points.append(point)
        self._names.add(name)
        self._last_boundary[name] = 0
        return point

    def advance(self, now: int) -> None:
        """推进到 ``now``：按声明顺序执行本批次更新点（帧事务内）。

        边界在回调成功后推进；任一回调抛错即整帧回滚——影子写入
        丢弃、本批边界恢复，下一帧从头重试（fail-closed）。
        跨线程调用（tick 线程与 skip 调用方）由推进锁串行化。
        """
        with self._advance_lock:
            self._advance_locked(now)

    def _advance_locked(self, now: int) -> None:
        """推进主体（调用方须持有推进锁）。"""
        due: list[tuple[UpdatePoint, int]] = []
        for point in self._points:
            boundary = (now // point.period) * point.period
            if boundary <= self._last_boundary[point.name]:
                continue
            due.append((point, boundary))
        if not due:
            return
        previous = [
            (point, self._last_boundary[point.name]) for point, _ in due
        ]
        self.store.begin_frame()
        try:
            for point, boundary in due:
                point.callback(now)
                self._last_boundary[point.name] = boundary
        except BaseException:
            for point, last in previous:
                self._last_boundary[point.name] = last
            self.store.abort()
            raise
        self.store.commit()

    def shutdown(self) -> None:
        """退订时钟信号，释放资源。"""
        self._scope.close()
