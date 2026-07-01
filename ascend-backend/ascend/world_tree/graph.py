"""事件有向图 — 维护事件间的关系边。

支持四种关系：
- caused_by: A 导致了 B
- observes: 观测事件引用被观测事件
- co_participant: 多方共同参与
- informed_by: 从他人处获知

内部使用邻接表存储，正向邻接 from_id → [(to_id, relation_type)]，
反向邻接 to_id → [(from_id, relation_type)] 用于加速反向查询。
"""

from __future__ import annotations

from collections import deque
from collections.abc import Callable
from typing import TYPE_CHECKING

from .event import Event

if TYPE_CHECKING:
    from .archive import EventArchive


class EventGraph:
    """事件关系有向图。

    维护事件间的四种关系边，支持因果链追溯、结果查询、
    观测者查询和路径检测。
    """

    def __init__(self) -> None:
        """初始化空的邻接表和节点集。"""
        self._forward: dict[str, list[tuple[str, str]]] = {}
        self._reverse: dict[str, list[tuple[str, str]]] = {}
        self._node_ids: set[str] = set()

    @property
    def node_count(self) -> int:
        """图中节点总数。

        Returns:
            节点集中的节点数。
        """
        return len(self._node_ids)

    def __repr__(self) -> str:
        """返回图状态的摘要。

        Returns:
            节点数和边数的字符串表示。
        """
        edge_count = sum(len(edges) for edges in self._forward.values())
        return f"EventGraph(nodes={len(self._node_ids)}, edges={edge_count})"

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

    def add_edge(self, from_id: str, to_id: str, relation_type: str) -> None:
        """运行时追加关系边。

        用于在事件发布后发现新的因果关系或传播关系。
        同时注册涉及的节点。

        Args:
            from_id: 关系源事件 ID。
            to_id: 关系目标事件 ID。
            relation_type: 关系类型字符串。
        """
        self._node_ids.add(from_id)
        self._node_ids.add(to_id)
        self._forward.setdefault(from_id, []).append((to_id, relation_type))
        self._reverse.setdefault(to_id, []).append((from_id, relation_type))

    def warmup(self, edges: list[tuple[str, str, str]]) -> int:
        """批量添加边，用于从归档恢复图结构。

        启动时调用，将 SQLite event_edges 表中的边批量加载到
        内存邻接表，加速重启后的因果追溯。

        Args:
            edges: (from_id, to_id, relation_type) 元组列表。

        Returns:
            成功添加的边数量。
        """
        for from_id, to_id, relation_type in edges:
            self._node_ids.add(from_id)
            self._node_ids.add(to_id)
            self._forward.setdefault(from_id, []).append(
                (to_id, relation_type)
            )
            self._reverse.setdefault(to_id, []).append(
                (from_id, relation_type)
            )
        return len(edges)

    # ── 删除 ──────────────────────────────────────────

    def remove_nodes(self, event_ids: set[str]) -> None:
        """批量移除节点及其关联边。

        从节点集、正向邻接表和反向邻接表中移除指定节点。
        不存在的 ID 静默忽略。重复调用幂等。

        Args:
            event_ids: 要移除的事件 ID 集合。
        """
        for eid in event_ids:
            self._node_ids.discard(eid)

            # 移除出边，同时清理目标节点的反向边
            for to_id, _ in self._forward.pop(eid, []):
                self._reverse[to_id] = [
                    (f, r) for f, r in self._reverse.get(to_id, [])
                    if f != eid
                ]

            # 移除入边，同时清理源节点的正向边
            for from_id, _ in self._reverse.pop(eid, []):
                self._forward[from_id] = [
                    (t, r) for t, r in self._forward.get(from_id, [])
                    if t != eid
                ]

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
        return [f for f, _ in self._reverse.get(physical_event_id, [])
                if any(r == "observes" for _, r in self._forward.get(f, []))]

    def has_path(self, from_id: str, to_id: str, max_depth: int = 20) -> bool:
        """BFS 检查两事件之间是否存在有向路径。

        Args:
            from_id: 起点事件 ID。
            to_id: 终点事件 ID。
            max_depth: 最大搜索深度。

        Returns:
            存在路径时为 True。
        """
        if from_id == to_id:
            return True
        visited: set[str] = set()
        queue: deque[tuple[str, int]] = deque([(from_id, 0)])
        while queue:
            node, depth = queue.popleft()
            if node == to_id:
                return True
            if depth >= max_depth or node in visited:
                continue
            visited.add(node)
            for next_id, _ in self._forward.get(node, []):
                if next_id not in visited:
                    queue.append((next_id, depth + 1))
        return False

    def get_related(self, event_id: str) -> list[tuple[str, str]]:
        """返回与该事件有直接关系边的事件和关系类型。

        Args:
            event_id: 查询的事件 ID。

        Returns:
            (事件ID, 关系类型) 元组列表，包含出入两边。
        """
        outgoing = self._forward.get(event_id, [])
        incoming = [(f, r) for f, r in self._reverse.get(event_id, [])]
        return outgoing + incoming
