"""事件有向图 — 维护事件间的关系边。

支持四种关系：
- caused_by: A 导致了 B
- observes: 观测事件引用被观测事件
- co_participant: 多方共同参与
- informed_by: 从他人处获知

内部使用邻接表存储，正向邻接 from_id → [(to_id, relation_type)]，
反向邻接 to_id → [(from_id, relation_type)] 用于加速反向查询。
"""

from collections import deque

from .event import Event


class EventGraph:
    """事件关系有向图。

    维护事件间的四种关系边，支持因果链追溯、结果查询、
    观测者查询和路径检测。
    """

    def __init__(self) -> None:
        """初始化空的邻接表。"""
        self._forward: dict[str, list[tuple[str, str]]] = {}
        self._reverse: dict[str, list[tuple[str, str]]] = {}

    def __repr__(self) -> str:
        """返回图状态的摘要。

        Returns:
            节点数和边数的字符串表示。
        """
        edge_count = sum(len(edges) for edges in self._forward.values())
        return f"EventGraph(nodes={len(self._forward)}, edges={edge_count})"

    # ── 写入 ──────────────────────────────────────────

    def add_event(self, event: Event) -> None:
        """根据事件自身的关系字段建边。

        从 event.caused_by、event.observes、event.co_participants
        中提取关系并调用 add_edge 建立图边。

        Args:
            event: 要建立关系边的事件。
        """
        for cause_id in event.caused_by:
            self.add_edge(cause_id, event.id, "caused_by")
        if event.observes:
            self.add_edge(event.id, event.observes, "observes")
        for participant_id in event.co_participants:
            if participant_id != event.initiator_id:
                self.add_edge(event.id, participant_id, "co_participant")

    def add_edge(self, from_id: str, to_id: str, relation_type: str) -> None:
        """运行时追加关系边。

        用于在事件发布后发现新的因果关系或传播关系。

        Args:
            from_id: 关系源事件 ID。
            to_id: 关系目标事件 ID。
            relation_type: 关系类型字符串。
        """
        self._forward.setdefault(from_id, []).append((to_id, relation_type))
        self._reverse.setdefault(to_id, []).append((from_id, relation_type))

    # ── 查询 ──────────────────────────────────────────

    def get_causal_chain(self, event_id: str, max_depth: int = 10) -> list[str]:
        """沿 caused_by 向上追溯因果链。

        从指定事件开始，不断查找它的 caused_by 上游，
        返回从最远到最近排序的因果事件 ID 列表。

        Args:
            event_id: 要追溯的事件 ID。
            max_depth: 最大追溯步数，防止环导致死循环。

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
