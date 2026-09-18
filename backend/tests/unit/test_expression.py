"""受限方程表达式求值测试（issue #49：声明语义作为独立参考）。"""

from __future__ import annotations

import math
import sys
from pathlib import Path

import pytest

_EQ = Path(__file__).resolve().parents[3] / "research" / "equations"
sys.path.insert(0, str(_EQ))

import expression  # noqa: E402


class TestEvaluate:
    def test_arithmetic_and_constants(self):
        assert expression.evaluate("2 * (3 + 4)", {}) == 14
        assert expression.evaluate("x / 2", {"x": 5.0}) == 2.5
        assert expression.evaluate("tick // game_day + 1",
                                   {"tick": 25, "game_day": 10}) == 3
        assert expression.evaluate("tick % game_day",
                                   {"tick": 25, "game_day": 10}) == 5
        assert expression.evaluate("cos(pi) ", {}) == pytest.approx(-1.0)

    def test_conditionals_and_comparisons(self):
        assert expression.evaluate(
            "'snow' if temp <= 0 else 'rain'", {"temp": -1.0},
        ) == "snow"
        assert expression.evaluate(
            "a if a > b and b >= 0 else a + b", {"a": 2.0, "b": 1.0},
        ) == 2.0
        assert expression.evaluate(
            "not flag", {"flag": False},
        ) is True

    def test_clamp_and_round_half_even(self):
        assert expression.evaluate("clamp(x, 0, 10)", {"x": -3.0}) == 0.0
        assert expression.evaluate("clamp(x, 0, 10)", {"x": 11.0}) == 10.0
        # Python round = 半偶（2.5→2、3.5→4）；0.35 有二进制表示误差，不作断言
        assert expression.evaluate("round_half_even(x)", {"x": 2.5}) == 2
        assert expression.evaluate("round_half_even(x)", {"x": 3.5}) == 4
        assert expression.evaluate("round_half_even(x, 1)", {"x": 0.25}) == 0.2

    def test_annotation_is_stripped(self):
        text = "clamp(latitude_noise * 25 + 10, -20, 38)（C 单源 _hydrology.c）"
        assert expression.evaluate(text, {"latitude_noise": 0.0}) == 10.0

    def test_unknown_variable_rejected(self):
        with pytest.raises(KeyError, match="未绑定变量"):
            expression.evaluate("a + b", {"a": 1.0})

    def test_referenced_names(self):
        names = expression.referenced_names(
            "clamp(amplitude * (tanh(phase * sharpness) if sharpness > 0 "
            "else phase), -1, 1) + pi"
        )
        assert names == frozenset({"amplitude", "phase", "sharpness"})

    @pytest.mark.parametrize("text, match", [
        ("__import__('os').system('true')", "未白名单函数|不受支持"),
        ("obj.attr", "不受支持"),
        ("[x for x in y]", "不受支持"),
        ("lambda x: x", "不受支持"),
        ("open('f')", "未白名单函数"),
        ("min(x, key=len)", "关键字实参|不受支持"),
        ("x + ", "不可解析"),
        ("", "为空"),
    ])
    def test_rejects_unsupported(self, text, match):
        with pytest.raises(expression.ExpressionError, match=match):
            expression.parse(text)


class TestMathFunctions:
    def test_trig_functions_match_math(self):
        for name, function in (
            ("tanh", math.tanh), ("cos", math.cos), ("sin", math.sin),
            ("tan", math.tan),
        ):
            value = expression.evaluate(f"{name}(x)", {"x": 0.3})
            assert value == pytest.approx(function(0.3))
        assert expression.evaluate("degrees(x)", {"x": math.pi}) == 180.0
        assert expression.evaluate("radians(x)", {"x": 180.0}) == math.pi
        assert expression.evaluate("abs(x)", {"x": -2.5}) == 2.5
        assert expression.evaluate("min(a, b)", {"a": 1, "b": 2}) == 1
        assert expression.evaluate("max(a, b)", {"a": 1, "b": 2}) == 2
