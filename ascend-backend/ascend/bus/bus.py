"""事件总线 — 连接所有模块的骨干。

各模块通过总线发布和订阅事件，不直接耦合。总线负责：
- 事件记录（全局追加日志）
- 事件路由（按 event_type 匹配订阅者）
- 空间索引（按 chunk 分桶）
- 实体索引（按 affected 自动建立）

MVP 阶段同步调用，内存存储。
"""

import threading
from collections.abc import Callable

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

    def __init__(self) -> None:
        """初始化空的事件总线。"""
        self._event_log: list[Event] = []
        self._subscriptions: dict[str, list[Callable[[Event], None]]] = {}
        self._entity_index: dict[str, list[int]] = {}
        self._spatial_index: dict[tuple[int, int], list[int]] = {}
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

    # ── 发布 ──────────────────────────────────────────

    def publish(self, event: Event) -> None:
        """发布事件到总线。

        依次完成:
        1. 写入全局日志
        2. 更新实体索引（initiator + affected）
        3. 更新空间索引（按 chunk 分桶）
        4. 更新事件关系图
        5. 通知匹配的订阅者

        Args:
            event: 要发布的事件。
        """
        with self._lock:
            log_index = len(self._event_log)
            self._event_log.append(event)

            all_entities: set[str] = {event.initiator_id}
            for ap in event.affected:
                all_entities.add(ap.entity_id)
            for eid in all_entities:
                self._entity_index.setdefault(eid, []).append(log_index)

            chunk_key = (event.location[0], event.location[1])
            self._spatial_index.setdefault(chunk_key, []).append(log_index)

            self._graph.add_event(event)

            self._dispatch(event)

    def _dispatch(self, event: Event) -> None:
        """将事件同步分发给所有匹配的订阅者。

        先匹配精确 event_type，再匹配通配符 "*"。

        Args:
            event: 要分发的事件。
        """
        callbacks: list[Callable[[Event], None]] = list(
            self._subscriptions.get(event.event_type, [])
        )
        callbacks.extend(self._subscriptions.get("*", []))

        for cb in callbacks:
            cb(event)

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

        Args:
            center_chunk: 中心 chunk 坐标 (chunk_x, chunk_y)。
            radius: 搜索半径（chunk 数），默认 1 即 3×3 区域。
            start_time: 可选，时间下界。
            end_time: 可选，时间上界。

        Returns:
            区域内满足条件的事件列表。
        """
        cx, cy = center_chunk
        results: list[Event] = []
        indices_seen: set[int] = set()

        for dx in range(-radius, radius + 1):
            for dy in range(-radius, radius + 1):
                chunk_key = (cx + dx, cy + dy)
                for log_idx in self._spatial_index.get(chunk_key, []):
                    if log_idx in indices_seen:
                        continue
                    indices_seen.add(log_idx)
                    ev = self._event_log[log_idx]
                    if start_time is not None and ev.timestamp < start_time:
                        continue
                    if end_time is not None and ev.timestamp > end_time:
                        continue
                    results.append(ev)
        return results

    def get_entity_events(
        self,
        entity_id: str,
        start_time: float,
        end_time: float,
    ) -> list[Event]:
        """查询实体参与的所有事件。

        实体作为发起方或受影响方参与的事件均被索引。

        Args:
            entity_id: 实体唯一标识。
            start_time: 起始时间（包含）。
            end_time: 结束时间（包含）。

        Returns:
            该实体在时间范围内参与的事件列表。
        """
        indices = self._entity_index.get(entity_id, [])
        results: list[Event] = []
        for log_idx in indices:
            ev = self._event_log[log_idx]
            if start_time <= ev.timestamp <= end_time:
                results.append(ev)
        return results

    # ── 事件图代理 ────────────────────────────────────

    @property
    def graph(self) -> EventGraph:
        """事件关系图，供上层做因果链查询和事件升级判定。

        Returns:
            内部 EventGraph 实例。
        """
        return self._graph

    # ── 元信息 ────────────────────────────────────────

    @property
    def event_count(self) -> int:
        """全局事件日志中的事件总数。

        Returns:
            事件总数。
        """
        return len(self._event_log)

    @property
    def subscriber_count(self) -> int:
        """当前订阅者总数（含通配符）。

        Returns:
            订阅回调函数总数。
        """
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
