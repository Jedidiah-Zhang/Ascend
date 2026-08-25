"""变量层因果图测试 — world_tree/root 的 VariableGraph。

Coverage: 节点/边声明与元数据、role 纪律（structural 构成因果子图、
inverse/observable 不进）、拓扑序、环拒绝、路径枚举、祖先/后代。
"""

import pytest

from ascend.world_tree.root import (
    ROLE_INVERSE,
    ROLE_OBSERVABLE,
    ROLE_STRUCTURAL,
    CycleError,
    VariableGraph,
)


def make_graph() -> VariableGraph:
    """最小变量层图：latitude/elevation 外生 → temperature → 降水系。"""
    g = VariableGraph()
    g.add_variable("latitude", exogenous=True)
    g.add_variable("elevation", exogenous=True)
    g.add_variable("temperature")
    g.add_variable("precipitation")
    g.add_variable("precip_type", domain="discrete")
    g.declare_edge("latitude", "temperature", role=ROLE_STRUCTURAL, L=0.9)
    g.declare_edge("elevation", "temperature", role=ROLE_STRUCTURAL, L=0.5)
    g.declare_edge("temperature", "precipitation", role=ROLE_STRUCTURAL, L=0.8)
    g.declare_edge("temperature", "precip_type", role=ROLE_STRUCTURAL, L=0.0,
               equation="precip_type_for(temp)")
    return g


# ── 节点声明 ──────────────────────────────────────────

class TestAddVariable:
    def test_metadata_registered(self):
        g = VariableGraph()
        g.add_variable("temperature", domain="continuous",
                       exogenous=False, bounds=(-60.0, 60.0))
        v = g.get_variable("temperature")
        assert v.name == "temperature"
        assert v.domain == "continuous"
        assert not v.exogenous
        assert v.bounds == (-60.0, 60.0)
        assert g.node_count == 1

    def test_discrete_domain(self):
        g = VariableGraph()
        g.add_variable("precip_type", domain="discrete")
        assert g.get_variable("precip_type").domain == "discrete"

    def test_rejects_bad_domain(self):
        g = VariableGraph()
        with pytest.raises(ValueError):
            g.add_variable("x", domain="categorical")

    def test_rejects_inverted_bounds(self):
        g = VariableGraph()
        with pytest.raises(ValueError):
            g.add_variable("x", bounds=(10.0, -10.0))

    def test_duplicate_variable_rejected(self):
        g = VariableGraph()
        g.add_variable("temperature")
        with pytest.raises(ValueError):
            g.add_variable("temperature")


# ── 边声明与元数据 ────────────────────────────────────

class TestAddEdge:
    def test_edge_metadata(self):
        g = make_graph()
        e = g.edge("temperature", "precip_type")
        assert e is not None
        assert e.role == ROLE_STRUCTURAL
        assert e.L == 0.0
        assert e.equation == "precip_type_for(temp)"

    def test_dangling_reference_rejected(self):
        g = VariableGraph()
        g.add_variable("temperature")
        with pytest.raises(KeyError):
            g.declare_edge("temperature", "nobody", role=ROLE_STRUCTURAL, L=1.0)
        with pytest.raises(KeyError):
            g.declare_edge("nobody", "temperature", role=ROLE_STRUCTURAL, L=1.0)

    def test_bad_role_rejected(self):
        g = VariableGraph()
        g.add_variable("a")
        g.add_variable("b")
        with pytest.raises(ValueError):
            g.declare_edge("a", "b", role="causal", L=1.0)

    def test_negative_L_rejected(self):
        g = VariableGraph()
        g.add_variable("a")
        g.add_variable("b")
        with pytest.raises(ValueError):
            g.declare_edge("a", "b", role=ROLE_STRUCTURAL, L=-0.1)

    def test_duplicate_edge_rejected(self):
        g = VariableGraph()
        g.add_variable("a")
        g.add_variable("b")
        g.declare_edge("a", "b", role=ROLE_STRUCTURAL, L=1.0)
        with pytest.raises(ValueError):
            g.declare_edge("a", "b", role=ROLE_INVERSE, L=1.0)

    def test_edges_iteration(self):
        g = make_graph()
        assert len(g.edges()) == 4


# ── 拓扑序与环 ────────────────────────────────────────

class TestToposort:
    def test_structural_order(self):
        g = make_graph()
        order = g.toposort()
        assert order.index("latitude") < order.index("temperature")
        assert order.index("elevation") < order.index("temperature")
        assert order.index("temperature") < order.index("precipitation")
        assert order.index("temperature") < order.index("precip_type")

    def test_structural_cycle_rejected(self):
        g = make_graph()
        with pytest.raises(CycleError):
            g.declare_edge("precipitation", "temperature",
                       role=ROLE_STRUCTURAL, L=1.0)

    def test_inverse_edge_can_reverse_structural(self):
        """inverse 边方向可与 structural 相反，不破坏因果子图。"""
        g = make_graph()
        g.add_variable("latent")
        g.declare_edge("temperature", "latent", role=ROLE_INVERSE, L=1.0)
        g.declare_edge("precipitation", "temperature", role=ROLE_INVERSE, L=0.1)
        order = g.toposort()
        assert order.index("latitude") < order.index("temperature")


# ── 路径与可达性（structural 子图上） ──────────────────

class TestPaths:
    def test_all_paths_multi(self):
        g = make_graph()
        g.add_variable("humidity")
        g.declare_edge("temperature", "humidity", role=ROLE_STRUCTURAL, L=0.3)
        g.declare_edge("humidity", "precipitation", role=ROLE_STRUCTURAL, L=0.7)
        paths = g.all_paths("temperature", "precipitation")
        assert ["temperature", "precipitation"] in paths
        assert ["temperature", "humidity", "precipitation"] in paths

    def test_all_paths_disjoint(self):
        g = make_graph()
        assert g.all_paths("latitude", "precip_type") == \
            [["latitude", "temperature", "precip_type"]]
        assert g.all_paths("latitude", "latitude") == []

    def test_ancestors_descendants(self):
        g = make_graph()
        assert g.ancestors("precipitation") == {
            "latitude", "elevation", "temperature"}
        assert g.descendants("latitude") == {
            "temperature", "precipitation", "precip_type"}
        assert g.ancestors("latitude") == set()
        assert g.descendants("precip_type") == set()

    def test_inverse_edge_not_in_reachability(self):
        """inverse 边不参与祖先/后代可达性。"""
        g = make_graph()
        g.add_variable("latent")
        g.declare_edge("temperature", "latent", role=ROLE_INVERSE, L=1.0)
        assert g.descendants("temperature") == {
            "precipitation", "precip_type"}
        assert "latent" not in g.descendants("temperature")
        assert "temperature" not in g.ancestors("latent")


# ── structural_dag：仅 structural 边的 SCM 子图 ────────

class TestStructuralDag:
    def test_only_structural_edges(self):
        g = make_graph()
        g.add_variable("latent")
        g.declare_edge("temperature", "latent", role=ROLE_INVERSE, L=1.0)
        g.declare_edge("precipitation", "precip_type",
                   role=ROLE_OBSERVABLE, L=0.0)
        dag = g.structural_dag()
        assert dag.node_count == 5
        assert set(dag.neighbors("temperature")) == {
            "precipitation", "precip_type"}
        assert dag.neighbors("latent") == []
        assert "latent" not in dag._node_ids

    def test_base_type(self):
        from ascend.world_tree.digraph import DirectedGraph
        assert isinstance(make_graph().structural_dag(), DirectedGraph)


# ── 基类方法契约 ──────────────────────────────────────

class TestInheritedContract:
    def test_neighbors_predecessors_role_filter(self):
        """基类邻居/前驱查询的 relation_type 过滤在变量层可用。"""
        g = make_graph()
        assert g.neighbors("temperature", ROLE_STRUCTURAL) == [
            "precipitation", "precip_type"]
        assert g.predecessors("temperature", ROLE_STRUCTURAL) == [
            "latitude", "elevation"]
        assert g.neighbors("temperature", ROLE_INVERSE) == []

    def test_mutation_primitives_disabled(self):
        """warmup/remove_nodes 会破坏声明元数据，必须被禁用。"""
        g = make_graph()
        with pytest.raises(NotImplementedError):
            g.remove_nodes({"temperature"})
        with pytest.raises(NotImplementedError):
            g.warmup([])
        # 禁用后图状态保持完整
        assert g.node_count == 5
        assert len(g.edges()) == 4
