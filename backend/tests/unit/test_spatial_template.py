"""空间父模板声明契约测试 — 多空间偏移的父值与值域语义。

覆盖 P5 为 W3 验收补的注册表能力：
- 同一父引用可声明多个空间偏移，父值是各偏移处取值的元组；
- 单偏移（或不声明偏移）父值仍按分量声明值域校验；
- ``tuple`` 值类型用于"空间聚合"分量（多格取值的容器）。

切片构造见 ``research/acceptance/slices.py``（验收 runner 的独立声明）。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_ACCEPTANCE = Path(__file__).resolve().parents[3] / "research" / "acceptance"
sys.path.insert(0, str(_ACCEPTANCE))

import slices  # noqa: E402

from ascend.causal import MechanismRegistry  # noqa: E402


class TestSpatialTemplateDeclaration:
    """多偏移父模板的声明与求值契约。"""

    def test_multi_offset_parent_requires_tuple(self):
        """多偏移父值必须是等长元组，长度不符即拒绝。"""
        registry = slices.w3_registry()
        mechanism = registry.mechanism_for("v")
        with pytest.raises(ValueError, match="空间父模板"):
            registry.evaluate_mechanism(mechanism, {"u": 1.0})
        with pytest.raises(ValueError, match="空间父模板"):
            registry.evaluate_mechanism(mechanism, {"u": (1.0, 2.0)})

    def test_multi_offset_parent_accepts_tuple(self):
        registry = slices.w3_registry()
        mechanism = registry.mechanism_for("v")
        output = registry.evaluate_mechanism(mechanism, {"u": (1.0, 1.0, 1.0)})
        assert output == slices._w3_equation((1.0, 1.0, 1.0))

    def test_single_offset_parent_uses_value_domain(self):
        """单偏移父值按分量值域校验（越界拒绝）。"""
        registry = slices.w2_registry(bounds=(0.0, 10.0))
        mechanism = registry.mechanism_for("x")
        assert registry.evaluate_mechanism(mechanism, {"x": 1.0}) == 2.0
        with pytest.raises(ValueError, match="超出声明值域"):
            registry.evaluate_mechanism(mechanism, {"x": 99.0})

    def test_witness_covers_every_parent_node(self):
        registry = slices.w3_registry()
        parents = {item.parent for item in registry.mechanism_for("v").parents}
        witnessed = {
            item.parent for item in registry.mechanism_for("v").witnesses
        }
        assert parents == witnessed


class TestTupleValueKind:
    """``tuple`` 值类型：空间聚合分量的值域声明。"""

    def _registry(self) -> MechanismRegistry:
        return slices.registry(
            "research.tuple",
            steps=slices._W2_STEPS,
            nodes=(slices.node("a", kind="tuple"),),
            mechanisms=(
                slices.mechanism(
                    "t.id", "a", lambda a_prev: a_prev,
                    parents=(slices.parent("a", "a_prev", lag=1),),
                    witnesses=(slices.witness(
                        "a", (("a", (1.0, 2.0)),), (3.0, 4.0),
                        ((1.0, 2.0), (3.0, 4.0)),
                    ),),
                ),
            ),
            wired=frozenset({"a"}),
        )

    def test_tuple_value_accepted(self):
        registry = self._registry()
        assert registry.validate_c0() == ()

    def test_tuple_kind_rejects_non_tuple(self):
        registry = self._registry()
        mechanism = registry.mechanism_for("a")
        with pytest.raises(ValueError, match="不属于声明值域 tuple"):
            registry.evaluate_mechanism(mechanism, {"a": 1.0})

    def test_tuple_kind_forbids_bounds(self):
        from ascend.causal import ValueDomain

        issues = MechanismRegistry._validate_domain(
            "a", ValueDomain(
                kind="tuple", unit="u", bounds=(0.0, 1.0), choices=(),
                missing="forbidden", quantization="exact",
            ),
        )
        assert any("bounds" in issue for issue in issues)
