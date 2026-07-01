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
from concurrent.futures import ThreadPoolExecutor

from ascend.log import get_logger
from .archive import EventArchive
from .event import Event
from .graph import EventGraph
from .registry import SchemaRegistry


class WorldTree:
    """事件总线。

    所有模块通过此总线通信。模块 A 发布事件时不关心模块 B 是否在监听，
    订阅者按 event_type 匹配接收。
    线程安全：publish 和 subscribe 由内部锁保护。

    用法:
        world_tree = WorldTree()
        world_tree.subscribe("weather_change", lambda e: print("天气变了"))
        world_tree.publish(Event(...))
    """

    def __init__(
        self,
        *,
        validate: bool = True,
        archive_path: str | None = None,
        max_memory_events: int | None = None,
        schema_registry: SchemaRegistry | None = None,
    ) -> None:
        """初始化空的事件总线。

        Args:
            validate: True 时在 publish 前校验事件必填字段。默认开启。
            archive_path: SQLite 归档数据库路径。None 表示不启用归档，
                         trim 时直接丢弃旧事件。传入路径则 trim 时归档到磁盘，
                         查询方法自动从归档合并结果。
            max_memory_events: 内存中最大事件数。超过此阈值时 publish()
                         自动触发 _trim() 将旧事件移出内存（若配了
                         archive_path 则先归档）。None 表示不自动 trim。
            schema_registry: 可选的 SchemaRegistry。传入后 publish 会
                        对已注册的事件类型执行 data 字段类型校验。
        """
        self._validate = validate
        self._max_memory_events = max_memory_events
        self._schema_registry = schema_registry
        self._trim_cycle: int = 0
        self._publish_count: int = 0
        self._trim_count: int = 0
        self._async_dispatch_count: int = 0
        self._event_log: list[Event] = []
        self._id_index: dict[str, Event] = {}
        self._subscriptions: dict[str, list[Callable[[Event], None]]] = {}
        self._entity_index: dict[str, list[Event]] = {}
        self._spatial_index: dict[tuple[int, int], set[Event]] = {}
        self._graph: EventGraph = EventGraph()
        self._lock: threading.RLock = threading.RLock()
        self._archive: EventArchive | None = (
            EventArchive(archive_path) if archive_path else None
        )
        self._async_executor: ThreadPoolExecutor = ThreadPoolExecutor(
            thread_name_prefix="worldtree",
        )

    def __repr__(self) -> str:
        """返回总线状态摘要。

        Returns:
            含事件数、订阅数、图的 repr 字符串。
        """
        return (
            f"WorldTree(events={self.event_count}, "
            f"subscribers={self.subscriber_count}, "
            f"graph={self._graph!r})"
        )

    # ── 校验 ──────────────────────────────────────────

    @staticmethod
    def _validate_event(event: Event) -> None:
        """校验事件必填字段。"""
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
            raise ValueError(f"位置格式无效: {location}")

    def _validate_schema(self, event: Event) -> None:
        """校验事件 data 字段是否符合注册的 schema。"""
        if self._schema_registry is None:
            return
        errors = self._schema_registry.validate(
            event.event_type, event.data
        )
        if errors:
            raise ValueError(
                f"事件 schema 校验失败 (event_type={event.event_type}, "
                f"id={event.id}):\n  " + "\n  ".join(errors)
            )

    # ── Schema 注册 ────────────────────────────────────

    @property
    def schema_registry(self) -> SchemaRegistry | None:
        """事件 schema 注册表。"""
        return self._schema_registry

    def register_event_schema(
        self,
        event_type: str,
        *,
        required: dict[str, type | tuple[type, ...]] | None = None,
        optional: dict[str, type | tuple[type, ...]] | None = None,
        description: str = "",
    ) -> None:
        """注册一个事件类型的 schema，若未配置 registry 则自动创建。"""
        if self._schema_registry is None:
            self._schema_registry = SchemaRegistry()
        self._schema_registry.register(
            event_type,
            required=required,
            optional=optional,
            description=description,
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
            self._validate_schema(event)

        self._publish_count += 1

        with self._lock:
            self._event_log.append(event)
            self._id_index[event.id] = event

            all_entities: set[str] = {event.initiator_id}
            for ap in event.affected:
                all_entities.add(ap.entity_id)
            for eid in all_entities:
                self._entity_index.setdefault(eid, []).append(event)

            chunk_key = (event.location[0], event.location[1])
            self._spatial_index.setdefault(chunk_key, set()).add(event)

            self._graph.add_event(event)

            # 在锁内抓取订阅者快照，锁外分发
            callbacks = list(self._subscriptions.get(event.event_type, []))
            callbacks.extend(self._subscriptions.get("*", []))

        if callbacks:
            self._dispatch(event, callbacks)

        # 自动 trim：超过阈值时驱逐旧事件
        if (
            self._max_memory_events is not None
            and len(self._event_log) > self._max_memory_events
        ):
            # trim 到阈值的一半，留出余量避免频繁触发
            keep = self._max_memory_events // 2
            if keep > 0 and len(self._event_log) > keep:
                cutoff_time = self._event_log[-keep].timestamp
                self._trim(cutoff_time)

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

    def subscribe_async(
        self, event_type: str, callback: Callable[[Event], None]
    ) -> Callable[[], None]:
        """订阅某类事件，回调在后台线程中执行。

        异步回调不阻塞 publish()，适合网络 I/O、文件写入等
        耗时操作。回调异常由 _safe_async_call 隔离，不影响线程池。

        Args:
            event_type: 事件类型字符串。"*" 表示订阅所有事件。
            callback: 在后台线程中调用的函数。

        Returns:
            一个无参函数，调用后取消此订阅。
        """
        def _async_wrapper(event: Event) -> None:
            """将回调提交到线程池执行。"""
            self._async_executor.submit(self._safe_async_call, callback, event)

        return self.subscribe(event_type, _async_wrapper)

    def _safe_async_call(
        self, callback: Callable[[Event], None], event: Event
    ) -> None:
        """在异步上下文中安全调用回调，异常隔离。

        Args:
            callback: 异步订阅者回调。
            event: 要传递的事件。
        """
        try:
            callback(event)
        except Exception:
            logger = get_logger(__name__)
            logger.exception(
                "异步回调执行失败: event_id=%s event_type=%s callback=%s",
                event.id, event.event_type, callback.__name__,
            )
        finally:
            self._async_dispatch_count += 1

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
        若启用归档且查询范围超出内存窗口，自动从 SQLite 归档合并结果。

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

            earliest_ts = (
                self._event_log[0].timestamp if self._event_log else None
            )
            archive = self._archive

        # 若查询范围超出内存窗口，从归档合并
        if archive and earliest_ts is not None and start_time < earliest_ts:
            arch_end = min(end_time, earliest_ts - 0.001)
            archived = archive.query_time_range(
                start_time, arch_end,
                event_type=event_type, initiator_type=initiator_type,
            )
            return archived + results

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
        若启用归档且查询范围超出内存窗口，自动从 SQLite 归档合并结果。

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

            for dx in range(-radius, radius + 1):
                for dy in range(-radius, radius + 1):
                    chunk_key = (cx + dx, cy + dy)
                    for ev in self._spatial_index.get(chunk_key, set()):
                        if start_time is not None and ev.timestamp < start_time:
                            continue
                        if end_time is not None and ev.timestamp > end_time:
                            continue
                        results.append(ev)

            earliest_ts = (
                self._event_log[0].timestamp if self._event_log else None
            )
            archive = self._archive

        # 若查询范围超出内存窗口，从归档合并
        if (
            archive and earliest_ts is not None
            and start_time is not None
            and start_time < earliest_ts
        ):
            arch_end = min(
                end_time if end_time is not None else float("inf"),
                earliest_ts - 0.001,
            )
            archived = archive.query_region(
                center_chunk, radius,
                start_time=start_time, end_time=arch_end,
            )
            return archived + results

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
        若启用归档且查询范围超出内存窗口，自动从 SQLite 归档合并结果。

        Args:
            entity_id: 实体唯一标识。
            start_time: 起始时间（包含）。
            end_time: 结束时间（包含）。

        Returns:
            该实体在时间范围内参与的事件列表，按时间排序。
        """
        with self._lock:
            events = self._entity_index.get(entity_id, [])

            in_memory: list[Event] = []
            if events:
                lo = bisect.bisect_left(
                    events, start_time, key=lambda e: e.timestamp,
                )
                hi = bisect.bisect_right(
                    events, end_time, key=lambda e: e.timestamp,
                )
                in_memory = events[lo:hi]

            earliest_ts = (
                self._event_log[0].timestamp if self._event_log else None
            )
            archive = self._archive

        # 若查询范围超出内存窗口，从归档合并
        if archive and earliest_ts is not None and start_time < earliest_ts:
            arch_end = min(end_time, earliest_ts - 0.001)
            archived = archive.query_entity(entity_id, start_time, arch_end)
            return archived + in_memory

        return in_memory

    def get_event_by_id(self, event_id: str) -> Event | None:
        """按 ID 查找事件，先查内存再查归档。

        内存中 O(1) 查找；未命中时通过归档主键索引查询。
        适用于 EventGraph 因果追溯后按 ID 获取事件体内容。

        Args:
            event_id: 事件唯一标识。

        Returns:
            重建的 Event 实例，不存在时返回 None。
        """
        with self._lock:
            ev = self._id_index.get(event_id)
            if ev is not None:
                return ev

        # 不在内存，查归档
        if self._archive:
            return self._archive.query_by_id(event_id)
        return None

    # ── 事件图代理 ────────────────────────────────────

    @property
    def graph(self) -> EventGraph:
        """事件关系图，供上层做因果链查询和事件升级判定。

        Returns:
            内部 EventGraph 实例。
        """
        return self._graph

    # ── 生命周期 ──────────────────────────────────────

    def warmup_graph(self, max_events: int = 10000) -> int:
        """从归档边表预热事件图。

        将 SQLite event_edges 表中最近 max_events 个事件的边
        批量加载到内存邻接表，加速重启后因果追溯和路径查询。

        Args:
            max_events: 从归档取最近多少个事件来预热。

        Returns:
            成功加载的边数。无归档时返回 0。
        """
        if self._archive is None:
            return 0

        rows = self._archive._db.execute(
            "SELECT id FROM events ORDER BY timestamp DESC LIMIT ?",
            (max_events,),
        ).fetchall()
        event_ids = [r[0] for r in rows]
        if not event_ids:
            return 0

        edges = self._archive.query_edges_bulk(event_ids)
        return self._graph.warmup(edges)

    def configure(
        self,
        *,
        archive_path: str | None = None,
        max_memory_events: int | None = None,
    ) -> None:
        """在构造后配置归档和内存限制。

        用于在 GameEngine.start() 中根据运行环境配置世界树，
        避免在模块导入时就需要确定这些参数。

        Args:
            archive_path: SQLite 归档路径。None 保持现状。
            max_memory_events: 内存事件上限。None 保持现状。
        """
        if archive_path is not None and self._archive is None:
            self._archive = EventArchive(archive_path)
        if max_memory_events is not None:
            self._max_memory_events = max_memory_events

    def _trim(self, before_time: float) -> int:
        """移除早于指定时间的事件体以回收内存（内部方法，权重感知）。

        由 publish() 在事件数超过 max_memory_events 时自动调用，
        模块外部不应直接调用。

        每调用一次 _trim_cycle 递增 1。事件仅当其 weight ≤ cycle
        时才被移除——高权重事件存活更多 trim 周期：
          - weight 1: 第 1 次 trim 即移除
          - weight 3: 存活 2 次 trim，第 3 次移除
          - weight 5: 存活 4 次 trim，第 5 次移除

        同时维护事件图的一致性，移除的节点可通过
        EventGraph.get_causal_chain(lookup=...) 补全。

        Args:
            before_time: 时间戳 < before_time 的事件候选移除。

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

            self._trim_cycle += 1
            cycle = self._trim_cycle
            self._trim_count += 1

            # 权重分层：按 weight 分别处理前缀中的事件
            to_archive: list[Event] = []
            to_keep: list[Event] = []
            for ev in self._event_log[:cutoff]:
                if ev.weight <= cycle:
                    to_archive.append(ev)
                else:
                    to_keep.append(ev)

            # 归档到磁盘（仅归档符合条件的低权重事件）
            if self._archive and to_archive:
                self._archive.archive(to_archive)

            # 记录被移除的事件 ID
            removed_ids = {ev.id for ev in to_archive}

            # 重建日志：保留的高权重旧事件 + 新事件
            self._event_log = to_keep + self._event_log[cutoff:]

            # 同步移除图中节点
            if removed_ids:
                self._graph.remove_nodes(removed_ids)

            # 增量清理索引（仅移除被归档事件，无需 O(N) 全量重建）
            # 1. _id_index：直接按 key 删除
            for ev in to_archive:
                del self._id_index[ev.id]

            # 2. _entity_index：被 trim 的事件总是位于列表前缀（时间有序）
            affected_entities: set[str] = set()
            for ev in to_archive:
                affected_entities.add(ev.initiator_id)
                for ap in ev.affected:
                    affected_entities.add(ap.entity_id)
            for eid in affected_entities:
                events = self._entity_index.get(eid)
                if not events:
                    continue
                cut = 0
                for ev in events:
                    if ev.id in removed_ids:
                        cut += 1
                    else:
                        break
                if cut:
                    self._entity_index[eid] = events[cut:]

            # 3. _spatial_index：逐事件从集合中移除
            for ev in to_archive:
                chunk_key = (ev.location[0], ev.location[1])
                chunk_set = self._spatial_index.get(chunk_key)
                if chunk_set:
                    chunk_set.discard(ev)

            removed_count = len(removed_ids)
            logger = get_logger(__name__)
            logger.info(
                "修剪事件(cycle=%d): 移除 %d 条(时间 < %.0f)，"
                "保留 %d 条高权重，剩余 %d 条",
                cycle, removed_count, before_time,
                len(to_keep), len(self._event_log),
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

    @property
    def stats(self) -> dict[str, int]:
        """运行统计指标。"""
        with self._lock:
            archive_count = 0
            if self._archive:
                try:
                    row = self._archive._db.execute(
                        "SELECT COUNT(*) FROM events"
                    ).fetchone()
                    if row:
                        archive_count = row[0]
                except Exception:
                    pass

            return {
                "publish_count": self._publish_count,
                "event_count": len(self._event_log),
                "trim_count": self._trim_count,
                "trim_cycle": self._trim_cycle,
                "subscriber_count": sum(
                    len(cbs) for cbs in self._subscriptions.values()
                ),
                "graph_nodes": self._graph.node_count,
                "async_dispatch_count": self._async_dispatch_count,
                "archive_event_count": archive_count,
                "max_memory_events": (
                    self._max_memory_events
                    if self._max_memory_events is not None
                    else 0
                ),
            }

    def await_async(self) -> None:
        """等待所有正在执行的异步回调完成。

        阻塞直到线程池中所有已提交的 subscribe_async 回调执行完毕。
        应在游戏退出前调用，避免异步任务被强制中断。

        调用后异步订阅者不再接收新事件。
        """
        self._async_executor.shutdown(wait=True)

    def clear(self) -> None:
        """清空所有事件和订阅。

        仅用于测试重置，生产环境不调用。
        """
        with self._lock:
            self._event_log.clear()
            self._id_index.clear()
            self._subscriptions.clear()
            self._entity_index.clear()
            self._spatial_index.clear()
            self._graph = EventGraph()
            self._trim_cycle = 0
            self._publish_count = 0
            self._trim_count = 0
            self._async_dispatch_count = 0
