"""帧调度器 — 世界状态更新的唯一执行入口（声明更新点驱动）。

设计决定（docs/研究理论/世界基座/12-世界树角色与帧调度.md）：
- 世界状态的演化只能由**声明的更新点**触发，按注册顺序确定性执行；
- 信号来自时钟（tick / skip，驱动层），**不经过世界树事件**；
- 每个更新点每次推进至多触发一次；跨多边界（快进）由系统自身
  按状态游标补齐（义务即槽位，WC-3.3）；
- 世界树只承担记录/观测分发，订阅者不得回写世界状态。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from ascend.world_tree import SubscriptionScope


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

    def __init__(self, clock=None) -> None:
        self._points: list[UpdatePoint] = []
        self._names: set[str] = set()
        # 已处理到的边界（tick）；0 表示"尚未越过任何边界"。
        self._last_boundary: dict[str, int] = {}
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
        """推进到 ``now``：按声明顺序触发已越过边界且尚未处理的更新点。"""
        for point in self._points:
            boundary = (now // point.period) * point.period
            if boundary <= self._last_boundary[point.name]:
                continue
            self._last_boundary[point.name] = boundary
            point.callback(now)

    def shutdown(self) -> None:
        """退订时钟信号，释放资源。"""
        self._scope.close()
