"""事件总线 — 连接所有模块的骨干。

各模块通过总线发布和订阅事件，不直接耦合。总线负责：
- 事件记录（全局追加日志）
- 事件路由（按 event_type 匹配订阅者）
- 空间索引（按 chunk 分桶）
- 实体索引（按 affected 自动建立）

MVP 阶段同步调用，内存存储。
"""

import bisect
import threading
from collections.abc import Callable

from ascend.log import get_logger
from .event import Event
from .graph import EventGraph


class EventBus:
    """事件总线。

    所有模块通过此总线通信。模块 A 发布事件时不关心模块 B 是否在监听，
    订阅者按 event_type 匹配接收。
    线程安全：publish 和 subscribe 由内部锁保护。

    用法:
        bus = EventBus()
        bus.subscribe("weather_change", lambda e: print("天气变了"))
        bus.publish(Event(...))
    """

    def __init__(self, *, validate: bool = True) -> None:
        """初始化空的事件总线。

        Args:
            validate: True 时在 publish 前校验事件必填字段。默认开启。
        """
        self._validate = validate
        self._event_log: list[Event] = []
        self._subscriptions: dict[str, list[Callable[[Event], None]]] = {}
        self._entity_index: dict[str, list[int]] = {}
        self._spatial_index: dict[tuple[int, int], set[int]] = {}
        self._graph: EventGraph = EventGraph()
        self._lock: threading.RLock = threading.RLock()

    def __repr__(self) -> str:
        """返回总线状态摘要。

        Returns:
            含事件数、订阅数、图的 repr 字符串。
        """
        return (
            f"EventBus(events={self.event_count}, "
            f"subscribers={self.subscriber_count}, "
            f"graph={self._graph!r})"
        )

    # ── 校验 ──────────────────────────────────────────

    @staticmethod
    def _validate_event(event: Event) -> None:
        """校验事件必填字段。

        在校验开启时由 publish() 在锁外调用，快速失败。

        Args:
            event: 要校验的事件。

        Raises:
            ValueError: 必填字段无效时抛出。
        """
        if not event.event_type or not event.event_type.strip():
            raise ValueError(f"事件类型不能为空: {event}")
        if not event.initiator_id or not event.initiator_id.strip():
            raise ValueError(f"发起方 ID 不能为空: {event}")
        if event.initiator_type not in ("system", "npc", "player"):
            raise ValueError(
                f"无效的发起方类型: {event.initiator_type}，"
                f"应为 'system' / 'npc' / 'player'"
            )
        if event.timestamp < 0:
            raise ValueError(f"时间戳不能为负: {event.timestamp}")
        location = event.location
        if not isinstance(location, tuple) or len(location) < 2:
            raise ValueError(
                f"位置格式无效: {location}"
            )

    # ── 发布 ──────────────────────────────────────────

    def publish(self, event: Event) -> None:
        """发布事件到总线。

        依次完成:
        1. 校验事件必填字段（可选，锁外）
        2. 写入全局日志
        3. 更新实体索引（initiator + affected）
        4. 更新空间索引（按 chunk 分桶）
        5. 更新事件关系图
        6. 通知匹配的订阅者（锁外分发，回调异常隔离）

        Args:
            event: 要发布的事件。

        Raises:
            ValueError: 校验开启且事件必填字段无效时抛出。
        """
        if self._validate:
            self._validate_event(event)

        with self._lock:
            log_index = len(self._event_log)
            self._event_log.append(event)

            all_entities: set[str] = {event.initiator_id}
            for ap in event.affected:
                all_entities.add(ap.entity_id)
            for eid in all_entities:
                self._entity_index.setdefault(eid, []).append(log_index)

            chunk_key = (event.location[0], event.location[1])
            self._spatial_index.setdefault(chunk_key, set()).add(log_index)

            self._graph.add_event(event)

            # 在锁内抓取订阅者快照，锁外分发
            callbacks = list(self._subscriptions.get(event.event_type, []))
            callbacks.extend(self._subscriptions.get("*", []))

        if callbacks:
            self._dispatch(event, callbacks)

    def _dispatch(
        self, event: Event, callbacks: list[Callable[[Event], None]]
    ) -> None:
        """将事件分发给指定的回调列表。

        每个回调独立 try/except，单个回调异常不影响其他回调。
        分发在锁外执行，回调可安全地调用 publish() 触发新事件。

        Args:
            event: 要分发的事件。
            callbacks: 已解析的回调列表（由 publish 在锁内抓取）。
        """
        logger = get_logger(__name__)
        for cb in callbacks:
            try:
                cb(event)
            except Exception:
                logger.exception(
                    "事件分发回调失败: event_id=%s event_type=%s",
                    event.id, event.event_type,
                )

    # ── 订阅 ──────────────────────────────────────────

    def subscribe(
        self, event_type: str, callback: Callable[[Event], None]
    ) -> Callable[[], None]:
        """订阅某类事件。

        Args:
            event_type: 要订阅的事件类型字符串。"*" 表示订阅所有事件。
            callback: 事件触发时调用的函数，接收 Event 作为唯一参数。

        Returns:
            一个无参函数，调用后取消此订阅。
        """
        with self._lock:
            self._subscriptions.setdefault(event_type, []).append(callback)

        def unsubscribe() -> None:
            """取消此订阅。若已取消则静默忽略。"""
            with self._lock:
                try:
                    self._subscriptions[event_type].remove(callback)
                except ValueError:
                    pass

        return unsubscribe

    # ── 查询 ──────────────────────────────────────────

    def get_events_in_range(
        self,
        start_time: float,
        end_time: float,
        *,
        event_type: str | None = None,
        initiator_type: str | None = None,
    ) -> list[Event]:
        """按时间范围查询事件。

        使用二分查找定位时间边界，避免全量扫描。

        Args:
            start_time: 起始时间（包含）。
            end_time: 结束时间（包含）。
            event_type: 可选，按事件类型过滤。
            initiator_type: 可选，按发起方类型过滤。

        Returns:
            满足条件的事件列表，按时间排序。
        """
        with self._lock:
            lo = self._bisect_time(start_time)
            hi = self._bisect_time(end_time, find_end=True)

            results: list[Event] = []
            for i in range(lo, min(hi, len(self._event_log))):
                ev = self._event_log[i]
                if event_type and ev.event_type != event_type:
                    continue
                if initiator_type and ev.initiator_type != initiator_type:
                    continue
                results.append(ev)
            return results

    def _bisect_time(self, target: float, find_end: bool = False) -> int:
        """二分查找目标时间在事件日志中的插入位置。

        Args:
            target: 目标时间戳。
            find_end: True 时返回 target 之后第一个位置（不含），
                      False 时返回第一个 >= target 的位置。

        Returns:
            日志索引。
        """
        log = self._event_log
        lo, hi = 0, len(log)
        while lo < hi:
            mid = (lo + hi) // 2
            if log[mid].timestamp < target or (
                find_end and log[mid].timestamp == target
            ):
                lo = mid + 1
            else:
                hi = mid
        return lo

    def get_events_in_region(
        self,
        center_chunk: tuple[int, int],
        radius: int = 1,
        *,
        start_time: float | None = None,
        end_time: float | None = None,
    ) -> list[Event]:
        """按空间区域查询事件。

        在 center_chunk 及其周围 radius 个 chunk 的范围内搜索。
        预计算时间范围对应的日志索引区间，用整数比较代替浮点时间戳比较。

        Args:
            center_chunk: 中心 chunk 坐标 (chunk_x, chunk_y)。
            radius: 搜索半径（chunk 数），默认 1 即 3×3 区域。
            start_time: 可选，时间下界。
            end_time: 可选，时间上界。

        Returns:
            区域内满足条件的事件列表。
        """
        with self._lock:
            cx, cy = center_chunk
            results: list[Event] = []

            # 预计算时间边界对应的日志索引范围
            time_lo = (
                self._bisect_time(start_time) if start_time is not None else 0
            )
            time_hi = (
                self._bisect_time(end_time, find_end=True)
                if end_time is not None
                else len(self._event_log)
            )

            for dx in range(-radius, radius + 1):
                for dy in range(-radius, radius + 1):
                    chunk_key = (cx + dx, cy + dy)
                    for log_idx in self._spatial_index.get(chunk_key, set()):
                        # 整数比较，避免 O(1) 次的浮点属性访问
                        if log_idx < time_lo or log_idx >= time_hi:
                            continue
                        results.append(self._event_log[log_idx])
            return results

    def get_entity_events(
        self,
        entity_id: str,
        start_time: float,
        end_time: float,
    ) -> list[Event]:
        """查询实体参与的所有事件。

        实体作为发起方或受影响方参与的事件均被索引。
        使用二分查找定位时间边界，O(log E + K)，E 为该实体事件总数，
        K 为时间范围内的事件数。

        Args:
            entity_id: 实体唯一标识。
            start_time: 起始时间（包含）。
            end_time: 结束时间（包含）。

        Returns:
            该实体在时间范围内参与的事件列表，按时间排序。
        """
        with self._lock:
            indices = self._entity_index.get(entity_id, [])
            if not indices:
                return []

            # 实体索引列表按时间有序（事件按时间顺序追加），二分定位
            lo = bisect.bisect_left(
                indices, start_time,
                key=lambda i: self._event_log[i].timestamp,
            )
            hi = bisect.bisect_right(
                indices, end_time,
                key=lambda i: self._event_log[i].timestamp,
            )
            return [self._event_log[i] for i in indices[lo:hi]]

    # ── 事件图代理 ────────────────────────────────────

    @property
    def graph(self) -> EventGraph:
        """事件关系图，供上层做因果链查询和事件升级判定。

        Returns:
            内部 EventGraph 实例。
        """
        return self._graph

    # ── 生命周期 ──────────────────────────────────────

    def trim(self, before_time: float) -> int:
        """移除早于指定时间的事件体以回收内存。

        只移除 _event_log 中的事件体和实体/空间索引中的引用，
        **不动事件图**——图的节点（ID 字符串）和边全部保留，
        因果链追溯不受影响。

        在锁内重建索引，锁外调用方看到一致状态。

        Args:
            before_time: 时间戳 < before_time 的事件将被移除。

        Returns:
            移除的事件数量。

        Raises:
            ValueError: before_time 为负值。
        """
        if before_time < 0:
            raise ValueError(f"清理时间不能为负: {before_time}")

        with self._lock:
            cutoff = self._bisect_time(before_time, find_end=True)
            if cutoff == 0:
                return 0

            # 记录被移除的事件 ID（仅用于日志）
            removed_ids = [ev.id for ev in self._event_log[:cutoff]]

            # 截断事件日志
            self._event_log = self._event_log[cutoff:]

            # 重建实体索引（索引值变为新日志中的位置）
            self._entity_index.clear()
            for i, ev in enumerate(self._event_log):
                all_entities = {ev.initiator_id}
                for ap in ev.affected:
                    all_entities.add(ap.entity_id)
                for eid in all_entities:
                    self._entity_index.setdefault(eid, []).append(i)

            # 重建空间索引
            self._spatial_index.clear()
            for i, ev in enumerate(self._event_log):
                chunk_key = (ev.location[0], ev.location[1])
                self._spatial_index.setdefault(chunk_key, set()).add(i)

            # 不动 _graph —— 因果链结构保留

            removed_count = len(removed_ids)
            logger = get_logger(__name__)
            logger.info(
                "修剪事件: 移除 %d 条(时间 < %.0f)，剩余 %d 条，图结构保留",
                removed_count, before_time, len(self._event_log),
            )
            return removed_count

    # ── 元信息 ────────────────────────────────────────

    @property
    def event_count(self) -> int:
        """全局事件日志中的事件总数。

        Returns:
            事件总数。
        """
        with self._lock:
            return len(self._event_log)

    @property
    def subscriber_count(self) -> int:
        """当前订阅者总数（含通配符）。

        Returns:
            订阅回调函数总数。
        """
        with self._lock:
            return sum(len(cbs) for cbs in self._subscriptions.values())

    def clear(self) -> None:
        """清空所有事件和订阅。

        仅用于测试重置，生产环境不调用。
        """
        with self._lock:
            self._event_log.clear()
            self._subscriptions.clear()
            self._entity_index.clear()
            self._spatial_index.clear()
            self._graph = EventGraph()
