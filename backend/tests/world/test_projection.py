"""研究投影测试（P2-3b）— 新声明 → equations.json 同 schema。

- 确定性：两次投影逐位一致；
- 结构：47 节点 / 35 机制 / 56 参数 / 64 边（55 linear + 9 jump）；
- 自洽：边与父引用一一对应；见证覆盖每个父引用；
- 模式校验：研究侧 ``schema.load_declaration`` + ``schema.validate`` 通过。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).parents[3]
sys.path.insert(0, str(ROOT / "backend"))
sys.path.insert(0, str(ROOT / "research" / "equations"))

import export_world  # noqa: E402


@pytest.fixture(scope="module")
def projection():
    return export_world.project(export_world.build_program())


class TestProjectionShape:
    def test_counts(self, projection):
        assert len(projection["nodes"]) == 47
        assert len(projection["variables"]) == 47
        assert len(projection["mechanisms"]) == 35
        assert len(projection["parameters"]) == 56
        assert len(projection["edges"]) == 64

    def test_modulus_counts(self, projection):
        linear = [
            edge for edge in projection["edges"]
            if edge["modulus_kind"] == "linear"
        ]
        jump = [
            edge for edge in projection["edges"]
            if edge["modulus_kind"] == "jump"
        ]
        assert (len(linear), len(jump)) == (55, 9)
        assert all(edge["L"] is not None for edge in linear)
        assert all(edge["jump_bound"] is not None for edge in jump)

    def test_edges_match_parents(self, projection):
        edge_keys = {
            (edge["parent"], edge["child"])
            for edge in projection["edges"]
        }
        expected = {
            (parent["parent"], mechanism["output"])
            for mechanism in projection["mechanisms"].values()
            for parent in mechanism["parents"]
        }
        assert edge_keys == expected

    def test_witness_coverage(self, projection):
        for mid, mechanism in projection["mechanisms"].items():
            parents = {parent["parent"] for parent in mechanism["parents"]}
            covered = {
                witness["parent"] for witness in mechanism["witnesses"]
            }
            assert covered == parents, mid


class TestProjectionDeterminism:
    def test_same_program_same_json(self):
        first = export_world.to_json()
        second = export_world.to_json()
        assert first == second

    def test_declaration_hash_stable(self, projection):
        again = export_world.project(export_world.build_program())
        assert (
            projection["declaration"]["hash"]
            == again["declaration"]["hash"]
        )


class TestProjectionSchema:
    def test_research_schema_valid(self):
        import schema

        graph = schema.load_declaration(
            ROOT / "research" / "equations" / "equations.json"
        )
        assert schema.validate(graph) == []
