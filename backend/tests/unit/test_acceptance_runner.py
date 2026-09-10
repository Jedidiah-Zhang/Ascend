"""世界验收 runner 契约测试 — 判据集合、产物形状与判别力。

契约来源：[世界验收协议](../../../docs/研究理论/世界基座/04-世界验收协议.md)
§1（保存输入/参考输出/引擎输出/首分歧）。本测试锁死 runner 的**报告契约**
与关键判据的判别力，避免判据退化为"永远通过"。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

_ACCEPTANCE = Path(__file__).resolve().parents[3] / "research" / "acceptance"
sys.path.insert(0, str(_ACCEPTANCE))

import checks  # noqa: E402
import reference  # noqa: E402
import slices  # noqa: E402


class TestSlices:
    """验收切片本身必须满足 C0/C1（构造期自检）。"""

    @pytest.mark.parametrize("builder", [
        slices.w0_registry, slices.w1_registry, slices.w2_registry,
        slices.w3_registry,
    ])
    def test_slice_passes_registry_validation(self, builder):
        registry = builder()
        assert registry.validate_c0() == ()
        assert registry.validate_c1() == ()

    def test_w3_declares_spatial_template(self):
        registry = slices.w3_registry()
        parents = registry.mechanism_for("v").parents
        assert len(parents) == 1
        assert len(parents[0].spatial_offsets) == 3
        assert parents[0].boundary_operator == "replicate"
        assert parents[0].lag == 1


class TestReferenceInterpreter:
    """参考解释器独立于引擎：手算表逐值对拍。"""

    def test_w0_hand_computed(self):
        registry = slices.w0_registry()
        frames = reference.ReferenceInterpreter(registry).run(
            range(1), {"x": 1.0, "U_mid": 2.0, "mid1": 0.0, "mid2": 0.0},
        ).frames
        assert frames[0]["mid1"] == 3.0
        assert frames[0]["mid2"] == 6.0
        assert frames[0]["x"] == 6.0

    def test_w2_hand_computed(self):
        registry = slices.w2_registry()
        frames = reference.ReferenceInterpreter(registry).run(
            range(3), {"x": 0.0},
        ).frames
        assert [frame["x"] for frame in frames] == [1.0, 2.0, 3.0]

    def test_w3_spatial_hand_computed(self):
        registry = slices.w3_registry()
        cells = tuple(range(slices.W3_CELLS))
        frames = reference.SpatialReferenceInterpreter(
            registry, cells=cells,
        ).run(range(1), {
            "u": {index: float(index + 1) for index in cells},
            "v": {index: 0.0 for index in cells},
        })
        assert frames[0]["v"] == (1.25, 2.0, 3.0, 4.0, 4.75)

    def test_w3_boundary_replicate(self):
        """边界格按 replicate 算子计算（越界邻格取边缘格自身）。"""
        registry = slices.w3_registry()
        cells = tuple(range(slices.W3_CELLS))
        frames = reference.SpatialReferenceInterpreter(
            registry, cells=cells,
        ).run(range(1), {
            "u": {index: 1.0 for index in cells},
            "v": {index: 0.0 for index in cells},
        })
        assert frames[0]["v"] == (1.0, 1.0, 1.0, 1.0, 1.0)

    def test_first_divergence_locates_node_and_frame(self):
        ref = [{"x": 1.0, "y": 2.0}]
        engine = [{"x": 1.0, "y": 3.0}]
        assert reference.first_divergence(ref, engine) == (0, "y", 2.0, 3.0)

    def test_first_divergence_none_when_identical(self):
        frame = [{"x": 1.0}]
        assert reference.first_divergence(frame, frame) is None


class TestChecks:
    """判据可执行、有判别力、报告含输入/参考/引擎/首分歧。"""

    @pytest.mark.parametrize("check", checks.ALL_CHECKS)
    def test_check_runs_and_reports(self, check):
        result = check()
        assert result.code
        assert result.title
        assert isinstance(result.passed, bool)
        assert result.detail
        payload = result.plain()
        assert set(payload) >= {
            "code", "title", "passed", "detail", "input",
            "reference", "engine", "first_divergence",
        }
        assert json.loads(json.dumps(payload)) == payload

    def test_all_checks_pass(self):
        failed = [
            check().code for check in checks.ALL_CHECKS if not check().passed
        ]
        assert failed == [], f"未通过判据: {failed}"

    def test_c2_rejects_bad_stage_order(self):
        """C2 判别力：违规声明必须被判据侧拒绝（不重实现判据逻辑）。"""
        result = checks.check_c2_rejects_violation()
        assert result.passed, result.detail
        assert "违规声明被拒" in result.detail

    def test_first_divergence_detects_extra_engine_node(self):
        """引擎多出节点也算分歧（此前只遍历参考侧，会漏报）。"""
        assert reference.first_divergence(
            [{"a": 1.0}], [{"a": 1.0, "b": 99.0}],
        ) == (0, "b", None, 99.0)

    def test_first_divergence_detects_missing_engine_node(self):
        assert reference.first_divergence(
            [{"a": 1.0, "b": 2.0}], [{"a": 1.0}],
        ) == (0, "b", 2.0, None)

    def test_first_divergence_reports_real_frame_label(self):
        """报的是真实帧标签，不是 list 下标。"""
        assert reference.first_divergence(
            [{"a": 1.0}], [{"a": 2.0}], frames=[5],
        ) == (5, "a", 1.0, 2.0)

    def test_w3_engine_matches_reference(self):
        """W3 有引擎侧：帧执行器支持空间展开，与参考逐格一致。"""
        from ascend.causal.intervention_engine import InterventionFrameExecutor

        registry = slices.w3_registry()
        cells = tuple(range(slices.W3_CELLS))
        engine = InterventionFrameExecutor(registry, spatial_cells=cells)
        state = {"u": {i: float(i + 1) for i in cells},
                 "v": {i: 0.0 for i in cells}}
        out = engine.run_frame(0, state)
        assert tuple(out["v"][i] for i in cells) == (1.25, 2.0, 3.0, 4.0, 4.75)

    def test_w3_engine_scalar_mode_rejects_spatial(self):
        """不传 spatial_cells 时空间切片必须显式失败（不静默错算）。"""
        from ascend.causal.intervention_engine import InterventionFrameExecutor

        registry = slices.w3_registry()
        engine = InterventionFrameExecutor(registry)
        with pytest.raises(ValueError):
            engine.run_frame(0, {"u": {0: 1.0}, "v": {0: 0.0}})

    def test_w2_arms_are_distinct(self):
        result = checks.check_w2()
        assert result.passed
        engine = result.engine
        assert len({tuple(engine["node"]), tuple(engine["persist"]),
                    tuple(engine["mech"])}) == 3

    def test_w3_unit_perturbation_within_kernel(self):
        result = checks.check_w3()
        assert result.passed
        # 引擎侧与参考侧都在结果里，扰动响应由判据 detail 断言
        assert result.reference == (1.0, 1.0, 1.0, 1.0, 1.0)
        assert result.engine == (1.0, 1.0, 1.0, 1.0, 1.0)
        assert "(0.0, 0.25, 0.5, 0.25, 0.0)" in result.detail

    def test_w5_reports_subject_subset(self):
        result = checks.check_w5()
        assert result.passed
        reference_view = result.reference
        assert set(reference_view["subject_visible"]) < set(
            reference_view["research_visible"]
        )

    def test_w5_leak_detector_is_discriminative(self):
        """W5 的泄露检测真能失败（此前恒空）。"""
        from ascend.causal import leaks_research_truth

        assert leaks_research_truth({"temperature": 21.5}) is False
        assert leaks_research_truth(
            {"temperature": 21.5, "parents": {"x": 1.0}}
        ) is True
        assert leaks_research_truth(
            {"temperature": 21.5, "random_addresses": [{"source": "u"}]}
        ) is True
