"""事件有向图 — 维护事件间的关系边。

支持四种关系：
- caused_by: A 导致了 B
- observes: 观测事件引用被观测事件
- co_participant: 多方共同参与
- informed_by: 从他人处获知

存储与遍历原语由 digraph.py 的 DirectedGraph 基类提供（事件层与
变量层共用同一套实现）；本模块只保留事件语义的方法。
"""

from __future__ import annotations

from collections.abc import Callable

from .digraph import DirectedGraph
from .event import Event


class EventGraph(DirectedGraph):
    """事件关系有向图。

    维护事件间的四种关系边，支持因果链追溯、结果查询、
    观测者查询和路径检测。
    """

    # ── 写入 ──────────────────────────────────────────

    def add_event(self, event: Event) -> None:
        """根据事件自身的关系字段建边。

        从 event.caused_by、event.observes、event.co_participants
        中提取关系并调用 add_edge 建立图边。
        同时注册节点到节点集，确保孤立节点也被追踪。

        Args:
            event: 要建立关系边的事件。
        """
        self._node_ids.add(event.id)
        for cause_id in event.caused_by:
            self._node_ids.add(cause_id)
            self.add_edge(cause_id, event.id, "caused_by")
        if event.observes:
            self._node_ids.add(event.observes)
            self.add_edge(event.id, event.observes, "observes")
        for participant_id in event.co_participants:
            if participant_id != event.initiator_id:
                self._node_ids.add(participant_id)
                self.add_edge(event.id, participant_id, "co_participant")

    # ── 查询 ──────────────────────────────────────────

    def get_causal_chain(
        self, event_id: str,
        max_depth: int = 10,
        *,
        lookup: Callable[[str], Event | None] | None = None,
    ) -> list[str]:
        """沿 caused_by 向上追溯因果链。

        从指定事件开始，不断查找它的 caused_by 上游，
        返回从最远到最近排序的因果事件 ID 列表。
        若提供 lookup 回调且内存图中未找到边，
        则通过 lookup 获取事件体并加载其 caused_by 信息。

        Args:
            event_id: 要追溯的事件 ID。
            max_depth: 最大追溯步数，防止环导致死循环。
            lookup: 可选 — 接受事件 ID 返回 Event 或 None 的回调。
                    用于图节点被 trim 后从归档或内存补全边信息。

        Returns:
            从远到近排序的事件 ID 列表（含中间因，不含 event_id 自身）。
        """
        chain: list[str] = []
        current = event_id
        visited: set[str] = set()
        for _ in range(max_depth):
            if current in visited:
                break
            visited.add(current)
            # caused_by 边从因指向果，找当前事件的因需查反向邻接
            parents = [f for f, r in self._reverse.get(current, []) if r == "caused_by"]
            if not parents and lookup:
                # 内存图中未找到边，尝试通过 lookup 获取事件体
                ev = lookup(current)
                if ev and ev.caused_by:
                    for cause_id in ev.caused_by:
                        self._node_ids.add(cause_id)
                        self._node_ids.add(current)
                        self.add_edge(cause_id, current, "caused_by")
                    parents = list(ev.caused_by)
            if not parents:
                break
            current = parents[0]
            chain.append(current)
        chain.reverse()
        return chain

    def get_consequences(self, event_id: str) -> list[str]:
        """查询事件的直接后果。

        Args:
            event_id: 查询的事件 ID。

        Returns:
            以此事件为直接原因的事件 ID 列表。
        """
        return [t for t, r in self._forward.get(event_id, []) if r == "caused_by"]

    def get_observers(self, physical_event_id: str) -> list[str]:
        """查询观测了某物理事件的所有 observation 事件。

        Args:
            physical_event_id: 被观测的物理事件 ID。

        Returns:
            observation 类型的事件 ID 列表。
        """
        return [f for f, r in self._reverse.get(physical_event_id, [])
                if r == "observes"]
