"""帧调度器 — 世界状态更新的唯一执行入口（声明周期驱动）。

设计决定（docs/研究理论/世界基座/12-世界树角色与帧调度.md，迁入新核心）：

- 世界状态的演化只能由**声明的周期**触发，按注册顺序确定性执行；
- 信号来自时钟（tick / skip，驱动层），**不经过世界树事件**；
- 每个更新点每次推进至多触发一次；跨多边界（快进）由系统自身
  按状态游标补齐（义务即槽位，WC-3.3）；
- 世界树只承担记录/观测分发，订阅者不得回写世界状态；
- 一次推进批次是**一个帧事务**（WC-7.6）：写方在事务内只写影子，
  批次末尾原子提交——记录回调只在提交成功后执行；
- 失败语义分两级（WC-9.2 / #51）：
  - **提交前失败**（更新点回调抛错）：整帧回滚（状态与边界都回退），
    首次失败下一帧重试；连续失败按指数退避节流（2 的幂次 tick，
    上限 64），退避期间推进直接跳过（不执行、不重抛）；
  - **提交相位失败**（应用动作或提交后记录回调抛错，见
    :class:`~ascend.world.runtime.transaction.WorldInvalidatedError`）：
    状态可能已部分应用或已提交，回滚边界无意义且重放会破坏
    "同一逻辑帧只更新一次"——调度器转入**世界失效**：标记
    :attr:`FrameScheduler.invalidated`，拒绝后续推进，不重试。

``bind_periods`` 把调度声明的周期键绑定到引擎推进回调（键必须已在
调度中声明，fail-closed）——"执行权只来自声明"，不存在运行时偷偷
注册的更新。
"""

from __future__ import annotations

import threading
from dataclasses import dataclass
from typing import Callable, Mapping

from ascend.log import get_logger
from ascend.world_tree import SubscriptionScope

from .transaction import FrameStateStore, WorldInvalidatedError

logger = get_logger(__name__)


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

    # 连续失败退避上限（tick）：持久失败时重试代价可控（ADR-12 D5）。
    _RETRY_BACKOFF_CAP: int = 64

    def __init__(self, clock=None, store: FrameStateStore | None = None) -> None:
        self._points: list[UpdatePoint] = []
        self._names: set[str] = set()
        # 已处理到的边界（tick）；0 表示"尚未越过任何边界"。
        self._last_boundary: dict[str, int] = {}
        # 连续失败计数与下一次允许重试的时刻（退避；推进锁内读写）。
        self._consecutive_failures = 0
        self._retry_not_before = 0
        # 世界失效原因（提交相位失败；WC-9.2 / #51）。
        self._invalidated: str | None = None
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
        提交前失败（ADR-12 D5）：首次失败下一帧重试；连续失败按指数
        退避节流（2 的幂次 tick，上限 ``_RETRY_BACKOFF_CAP``），退避
        期间推进直接跳过（不执行、不重抛）。
        提交相位失败（应用动作/记录回调，WC-9.2 / #51）：世界失效，
        本方法此后一律抛 :class:`WorldInvalidatedError`（不推进、
        不重试）。
        跨线程调用（tick 线程与 skip 调用方）由推进锁串行化。
        """
        with self._advance_lock:
            if self._invalidated is not None:
                raise WorldInvalidatedError(self._invalidated)
            self._advance_locked(now)

    @property
    def consecutive_failures(self) -> int:
        """当前连续帧失败次数（提交成功后清零）。"""
        with self._advance_lock:
            return self._consecutive_failures

    @property
    def invalidated(self) -> str | None:
        """世界失效原因；None = 正常（提交相位失败后不再推进）。"""
        with self._advance_lock:
            return self._invalidated

    def _backoff_ticks(self) -> int:
        """连续失败 n 次的退避 tick 数：首次下一帧重试，其后 2 的幂、封顶。"""
        if self._consecutive_failures <= 1:
            return 0
        exponent = min(
            self._consecutive_failures - 2,
            self._RETRY_BACKOFF_CAP.bit_length() - 1,
        )
        return min(1 << exponent, self._RETRY_BACKOFF_CAP)

    def _advance_locked(self, now: int) -> None:
        """推进主体（调用方须持有推进锁）。"""
        if now < self._retry_not_before:
            return  # 退避窗口内：不重试、不重抛（D5）
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
            self.store.commit()
        except BaseException:
            # 帧失败：先摘事务（若仍打开）
            if self.store.transaction_open:
                self.store.abort()
            invalidated = self.store.invalidated
            if invalidated is not None:
                # 提交相位失败：状态可能已部分应用或已提交，回滚边界
                # 无意义；重放会破坏"同一逻辑帧只更新一次"（WC-9.2/P1）。
                self._invalidated = invalidated
                logger.error(
                    "世界失效（帧推进至 %d）：%s", now, invalidated,
                )
                raise
            # 提交前失败：边界回滚，下一帧重试（影子已丢弃，状态未变）
            for point, last in previous:
                self._last_boundary[point.name] = last
            self._consecutive_failures += 1
            self._retry_not_before = now + self._backoff_ticks()
            failures = self._consecutive_failures
            if failures == 1 or (failures & (failures - 1)) == 0:
                # 首次与 2 的幂次失败各汇总一条（避免每次重试刷日志）
                logger.warning(
                    "帧推进连续失败 %d 次：边界已回滚，%d tick 后重试",
                    failures, self._retry_not_before - now,
                )
            raise
        else:
            self._consecutive_failures = 0
            self._retry_not_before = 0

    def shutdown(self) -> None:
        """退订时钟信号，释放资源。"""
        self._scope.close()


def bind_periods(
    schedule,
    scheduler: FrameScheduler,
    bindings: Mapping[str, Callable[[int], None]],
) -> tuple[str, ...]:
    """把调度声明的周期键绑定到更新点回调（按声明顺序注册）。

    Args:
        schedule: ``Schedule``（``periods``：周期键 → tick 数）。
        scheduler: ``FrameScheduler``。
        bindings: 周期键 → 推进回调 ``callback(now)``。

    Returns:
        按声明顺序注册的周期键。

    Raises:
        ValueError: 绑定与调度声明不匹配（缺少或多余；fail-closed——
            "执行权只来自声明"）。
    """
    declared = {key: ticks for key, ticks in schedule.periods}
    missing = sorted(set(declared) - set(bindings))
    extra = sorted(set(bindings) - set(declared))
    if missing or extra:
        raise ValueError(f"更新点绑定不匹配: 缺少={missing}, 多余={extra}")
    for key, ticks in schedule.periods:
        scheduler.register(key, period=ticks, callback=bindings[key])
    return tuple(key for key, _ in schedule.periods)


__all__ = [
    "FrameScheduler",
    "UpdatePoint",
    "bind_periods",
    "WorldInvalidatedError",
]
