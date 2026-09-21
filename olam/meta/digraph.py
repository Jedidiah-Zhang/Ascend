"""通用有向图 — 带类型边的有向图基类。

事件层（graph.py 的 EventGraph）与变量层（root/ 的 VariableGraph）
共用同一套存储与遍历原语，避免两份邻接表实现漂移。

内部使用邻接表存储：正向邻接 from_id → [(to_id, relation_type)]，
反向邻接 to_id → [(from_id, relation_type)] 用于加速反向查询。
节点以字符串 ID 标识，边携带关系类型字符串。
"""

from __future__ import annotations

from collections import deque


class DirectedGraph:
    """带类型关系边的通用有向图基类。

    仅提供与领域无关的存储与遍历能力；关系语义（哪类边是
    "因果"、查询的含义）由子类定义。
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
        return f"{type(self).__name__}(nodes={len(self._node_ids)}, edges={edge_count})"

    # ── 写入 ──────────────────────────────────────────

    def add_edge(self, from_id: str, to_id: str, relation_type: str) -> None:
        """添加一条带类型的关系边并注册两端节点。

        Args:
            from_id: 边源节点 ID。
            to_id: 边目标节点 ID。
            relation_type: 关系类型字符串。
        """
        self._node_ids.add(from_id)
        self._node_ids.add(to_id)
        self._forward.setdefault(from_id, []).append((to_id, relation_type))
        self._reverse.setdefault(to_id, []).append((from_id, relation_type))

    def warmup(self, edges: list[tuple[str, str, str]]) -> int:
        """批量添加边。

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

    def remove_nodes(self, node_ids: set[str]) -> None:
        """批量移除节点及其关联边。

        从节点集、正向邻接表和反向邻接表中移除指定节点。
        不存在的 ID 静默忽略。重复调用幂等。

        Args:
            node_ids: 要移除的节点 ID 集合。
        """
        for nid in node_ids:
            self._node_ids.discard(nid)

            # 移除出边，同时清理目标节点的反向边
            for to_id, _ in self._forward.pop(nid, []):
                self._reverse[to_id] = [
                    (f, r) for f, r in self._reverse.get(to_id, [])
                    if f != nid
                ]

            # 移除入边，同时清理源节点的正向边
            for from_id, _ in self._reverse.pop(nid, []):
                self._forward[from_id] = [
                    (t, r) for t, r in self._forward.get(from_id, [])
                    if t != nid
                ]

    # ── 查询 ──────────────────────────────────────────

    def has_path(self, from_id: str, to_id: str, max_depth: int = 20) -> bool:
        """BFS 检查两节点之间是否存在有向路径。

        Args:
            from_id: 起点节点 ID。
            to_id: 终点节点 ID。
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

    def get_related(self, node_id: str) -> list[tuple[str, str]]:
        """返回与该节点有直接关系边的节点和关系类型。

        Args:
            node_id: 查询的节点 ID。

        Returns:
            (节点ID, 关系类型) 元组列表，包含出入两边。
        """
        outgoing = self._forward.get(node_id, [])
        incoming = [(f, r) for f, r in self._reverse.get(node_id, [])]
        return outgoing + incoming

    def neighbors(
        self,
        node_id: str,
        relation_type: str | None = None,
    ) -> list[str]:
        """返回节点的直接后继（出边目标）。

        Args:
            node_id: 查询的节点 ID。
            relation_type: 可选 — 只返回指定关系类型的边。

        Returns:
            后继节点 ID 列表，按插入顺序。
        """
        edges = self._forward.get(node_id, [])
        if relation_type is None:
            return [t for t, _ in edges]
        return [t for t, r in edges if r == relation_type]

    def predecessors(
        self,
        node_id: str,
        relation_type: str | None = None,
    ) -> list[str]:
        """返回节点的直接前驱（入边来源）。

        Args:
            node_id: 查询的节点 ID。
            relation_type: 可选 — 只返回指定关系类型的边。

        Returns:
            前驱节点 ID 列表，按插入顺序。
        """
        edges = self._reverse.get(node_id, [])
        if relation_type is None:
            return [f for f, _ in edges]
        return [f for f, r in edges if r == relation_type]
