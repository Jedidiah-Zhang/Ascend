"""变量层因果图 — 世界树的"根"：世界参数静态因果图（SCM）。

节点 = 世界参数变量（连续/离散、外生/内生），边 = 结构方程
`node = f(Pa)`。与事件层（graph.py 的 EventGraph，动态实例图）
相对：本图声明一次、结构不变，事件层将来引用它作为"根"
（研究方案 §6：事件因果边与父节点快照）。

边的角色（role）纪律是承重设计：
- structural：正向因果结构边，构成真正的 SCM 因果子图
  （do-operator / 误差递推 / 02 篇定理只作用在这上面）；
- inverse：从可观测场反推/重建潜参数的公式（如
  derive_latitude），方向与真实因果相反，不进因果子图；
- observable：可观测变换（如阈值分级），是同一变量的投影，
  不是新因果边，不进因果子图。

结构边在添加时即校验无环；inverse/observable 边允许与
结构边方向相反，不参与拓扑与可达性。
"""

from __future__ import annotations

from dataclasses import dataclass
from collections import deque

from ..digraph import DirectedGraph

ROLE_STRUCTURAL = "structural"
ROLE_INVERSE = "inverse"
ROLE_OBSERVABLE = "observable"
ROLES = (ROLE_STRUCTURAL, ROLE_INVERSE, ROLE_OBSERVABLE)

_DOMAINS = ("continuous", "discrete")


class CycleError(ValueError):
    """结构边构成有向环时抛出。"""


@dataclass(frozen=True, slots=True)
class VariableSpec:
    """变量声明。

    Attributes:
        name: 变量名（图中节点 ID）。
        domain: "continuous" 或 "discrete"。
        exogenous: 是否外生（无结构边入边）。
        bounds: 连续变量的值域 (min, max)，离散或未知为 None。
        eps: 误差上限（设计预算，与变量同单位；定理 2.5 的 ε_i，
            反事实误差界 Σ ε_u·W(u,t) 的输入）。None 表示未声明。
    """

    name: str
    domain: str = "continuous"
    exogenous: bool = False
    bounds: tuple[float, float] | None = None
    eps: float | None = None


@dataclass(frozen=True, slots=True)
class EdgeSpec:
    """结构方程边声明。

    Attributes:
        role: 边角色（structural / inverse / observable）。
        L: Lipschitz 常数估计（≥0；02 篇误差传播律的必填元数据）。
        equation: 方程引用（函数名或声明键），可选。
    """

    role: str
    L: float
    equation: str | None = None


class VariableGraph(DirectedGraph):
    """变量层静态因果图（SCM 骨架）。

    用法:
        g = VariableGraph()
        g.add_variable("temperature")
        g.declare_edge("latitude", "temperature",
                       role=ROLE_STRUCTURAL, L=0.9)
        order = g.toposort()
        dag = g.structural_dag()
    """

    def __init__(self) -> None:
        """初始化空的节点注册表与边元数据表。"""
        super().__init__()
        self._variables: dict[str, VariableSpec] = {}
        self._edge_meta: dict[tuple[str, str], EdgeSpec] = {}

    # ── 节点声明 ──────────────────────────────────────

    def add_variable(
        self,
        name: str,
        domain: str = "continuous",
        exogenous: bool = False,
        bounds: tuple[float, float] | None = None,
        eps: float | None = None,
    ) -> VariableSpec:
        """声明一个变量节点。

        Args:
            name: 变量名（图中节点 ID）。
            domain: "continuous" 或 "discrete"。
            exogenous: 是否外生。
            bounds: 连续变量值域 (min, max)。

        Returns:
            登记的 VariableSpec。

        Raises:
            ValueError: domain 非法、bounds 倒置或变量重复声明。
        """
        if domain not in _DOMAINS:
            raise ValueError(f"非法 domain: {domain!r}，应为 {_DOMAINS}")
        if bounds is not None:
            lo, hi = bounds
            if lo > hi:
                raise ValueError(f"bounds 倒置: {bounds}")
        if name in self._variables:
            raise ValueError(f"变量重复声明: {name}")
        spec = VariableSpec(name=name, domain=domain,
                            exogenous=exogenous, bounds=bounds, eps=eps)
        self._variables[name] = spec
        self._node_ids.add(name)
        return spec

    def get_variable(self, name: str) -> VariableSpec:
        """查询变量声明。

        Args:
            name: 变量名。

        Returns:
            变量的 VariableSpec。

        Raises:
            KeyError: 变量未声明。
        """
        return self._variables[name]

    @property
    def variables(self) -> dict[str, VariableSpec]:
        """全部变量声明（按声明顺序）。"""
        return dict(self._variables)

    # ── 边声明 ────────────────────────────────────────

    def declare_edge(
        self,
        parent: str,
        child: str,
        *,
        role: str,
        L: float,
        equation: str | None = None,
    ) -> EdgeSpec:
        """声明一条结构方程边 parent → child。

        两端节点必须已 add_variable；同一有序对只允许一条边。
        结构边在添加时即做无环校验（child 已可达 parent 则拒绝）。

        Args:
            parent: 父变量名。
            child: 子变量名。
            role: 边角色（structural / inverse / observable）。
            L: Lipschitz 常数估计（≥0）。
            equation: 方程引用，可选。

        Returns:
            登记的 EdgeSpec。

        Raises:
            KeyError: 端点未声明。
            ValueError: role 非法、L < 0 或边重复。
            CycleError: structural 边会造成因果子图成环。
        """
        if parent not in self._variables:
            raise KeyError(f"父变量未声明: {parent}")
        if child not in self._variables:
            raise KeyError(f"子变量未声明: {child}")
        if role not in ROLES:
            raise ValueError(f"非法 role: {role!r}，应为 {ROLES}")
        if L < 0:
            raise ValueError(f"L 必须 ≥ 0，收到 {L}")
        key = (parent, child)
        if key in self._edge_meta:
            raise ValueError(f"边重复声明: {parent} -> {child}")
        if role == ROLE_STRUCTURAL and self._reachable(child, parent):
            raise CycleError(
                f"structural 边 {parent} -> {child} 会构成因果环")
        spec = EdgeSpec(role=role, L=L, equation=equation)
        self._edge_meta[key] = spec
        self._node_ids.add(parent)
        self._node_ids.add(child)
        self._forward.setdefault(parent, []).append((child, role))
        self._reverse.setdefault(child, []).append((parent, role))
        return spec

    def edge(self, parent: str, child: str) -> EdgeSpec | None:
        """查询边元数据。

        Args:
            parent: 父变量名。
            child: 子变量名。

        Returns:
            边的 EdgeSpec；不存在返回 None。
        """
        return self._edge_meta.get((parent, child))

    def edges(self) -> list[tuple[str, str, EdgeSpec]]:
        """全部边（按声明顺序）。

        Returns:
            (parent, child, EdgeSpec) 列表。
        """
        return [(p, c, s) for (p, c), s in self._edge_meta.items()]

    # ── 禁用的基类变更操作 ────────────────────────────

    def warmup(self, edges: list[tuple[str, str, str]]) -> int:
        """批量添加边 — 变量层禁用。

        声明式图的节点/边元数据（_variables/_edge_meta）必须与
        邻接表同步，基类的裸批量添加会破坏该不变量。

        Raises:
            NotImplementedError: 始终抛出。
        """
        raise NotImplementedError(
            "VariableGraph 是声明式图，请用 declare_edge 逐条声明")

    def remove_nodes(self, node_ids: set[str]) -> None:
        """批量移除节点 — 变量层禁用。

        基类实现只清邻接表，会遗留 _variables/_edge_meta 中的
        陈旧声明，导致 edges()/toposort() 与邻接表不一致。

        Raises:
            NotImplementedError: 始终抛出。
        """
        raise NotImplementedError(
            "VariableGraph 是声明式图（声明一次、结构不变），不支持移除")

    # ── 结构子图查询（仅 structural 边） ───────────────

    def _structural_forward(self) -> dict[str, list[str]]:
        """仅 structural 边的正向邻接。"""
        out: dict[str, list[str]] = {}
        for (p, c), s in self._edge_meta.items():
            if s.role == ROLE_STRUCTURAL:
                out.setdefault(p, []).append(c)
        return out

    def _structural_reverse(self) -> dict[str, list[str]]:
        """仅 structural 边的反向邻接。"""
        out: dict[str, list[str]] = {}
        for (p, c), s in self._edge_meta.items():
            if s.role == ROLE_STRUCTURAL:
                out.setdefault(c, []).append(p)
        return out

    def _reachable(self, src: str, dst: str) -> bool:
        """src 是否可沿 structural 边到达 dst（无深度限制）。"""
        if src == dst:
            return True
        fwd = self._structural_forward()
        visited: set[str] = set()
        queue: deque[str] = deque([src])
        while queue:
            node = queue.popleft()
            if node == dst:
                return True
            if node in visited:
                continue
            visited.add(node)
            for nxt in fwd.get(node, []):
                if nxt not in visited:
                    queue.append(nxt)
        return False

    def structural_dag(self) -> DirectedGraph:
        """仅含 structural 边的 SCM 因果子图。

        do-operator / 误差递推 / 反事实（02 篇定理）只应作用在
        这个子图上；inverse 与 observable 边不参与。

        Returns:
            一个独立的 DirectedGraph（边类型为 "structural"）。
        """
        dag = DirectedGraph()
        for (p, c), s in self._edge_meta.items():
            if s.role == ROLE_STRUCTURAL:
                dag.add_edge(p, c, ROLE_STRUCTURAL)
        return dag

    def toposort(self) -> list[str]:
        """结构边的拓扑序（Kahn，字典序确定性）。

        覆盖全部已声明变量（含孤立节点）。

        Returns:
            拓扑序的变量名列表。

        Raises:
            CycleError: 结构边构成环（添加时应已拦截）。
        """
        fwd = self._structural_forward()
        indeg: dict[str, int] = {v: 0 for v in self._variables}
        for children in fwd.values():
            for c in children:
                indeg[c] += 1
        ready = sorted(v for v, d in indeg.items() if d == 0)
        order: list[str] = []
        while ready:
            node = ready.pop(0)
            order.append(node)
            for c in sorted(fwd.get(node, [])):
                indeg[c] -= 1
                if indeg[c] == 0:
                    ready.append(c)
        if len(order) != len(self._variables):
            cyclic = sorted(v for v in self._variables if v not in order)
            raise CycleError(f"结构边存在环，涉及: {cyclic}")
        return order

    def all_paths(self, source: str, target: str) -> list[list[str]]:
        """枚举 source → target 的全部简单路径（仅 structural 边）。

        因果子图无环，简单路径枚举可穷尽；路径不含重复节点。

        Args:
            source: 起点变量名。
            target: 终点变量名。

        Returns:
            路径列表，每条为变量名序列；无路径返回空列表。
        """
        if source == target:
            return []
        fwd = self._structural_forward()
        paths: list[list[str]] = []

        def dfs(node: str, trail: list[str], visited: set[str]) -> None:
            for nxt in fwd.get(node, []):
                if nxt in visited:
                    continue
                if nxt == target:
                    paths.append(trail + [nxt])
                    continue
                visited.add(nxt)
                dfs(nxt, trail + [nxt], visited)
                visited.remove(nxt)

        dfs(source, [source], {source})
        return paths

    def ancestors(self, node: str) -> set[str]:
        """节点的全部祖先（仅 structural 边，不含自身）。

        Args:
            node: 变量名。

        Returns:
            祖先变量名集合。
        """
        rev = self._structural_reverse()
        visited: set[str] = set()
        queue: deque[str] = deque(rev.get(node, []))
        while queue:
            cur = queue.popleft()
            if cur in visited:
                continue
            visited.add(cur)
            for p in rev.get(cur, []):
                if p not in visited:
                    queue.append(p)
        return visited

    def descendants(self, node: str) -> set[str]:
        """节点的全部后代（仅 structural 边，不含自身）。

        Args:
            node: 变量名。

        Returns:
            后代变量名集合。
        """
        fwd = self._structural_forward()
        visited: set[str] = set()
        queue: deque[str] = deque(fwd.get(node, []))
        while queue:
            cur = queue.popleft()
            if cur in visited:
                continue
            visited.add(cur)
            for c in fwd.get(cur, []):
                if c not in visited:
                    queue.append(c)
        return visited
