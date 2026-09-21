"""声明方程字符串的受限表达式语义（独立参考实现）。

``mechanisms[*].equation`` 是声明的规范语义文本。本模块提供**不依赖生产
函数**的受限求值器：验收参考解释器与证书核验都以它为语义基准，从而把
"用生产函数对拍生产函数"改为"用声明语义对拍生产实现"。

支持子集（超集即拒绝，fail-closed）：

- 算术 ``+ - * / // %``、一元 ``+ -``、比较、``and/or/not``、条件表达式
  ``a if cond else b``、括号；
- 白名单函数 ``clamp / min / max / abs / tanh / cos / sin / tan / acos /
  radians / degrees / round_half_even``（后者等价 Python ``round``，半偶）；
- 常量 ``pi``；变量名 = 机制函数的形参名（父 argument + 参数 argument）。

字符串末尾的全角括号中文注记（如 ``（定点 Q30）``）会被剥离后再解析；
剥离后仍不可解析即拒绝（该机制需登记独立参考实现，见
``reference_impl.py``）。
"""

from __future__ import annotations

import ast
import math
import re
from collections.abc import Mapping

_ALLOWED_BINARY = {
    ast.Add: lambda a, b: a + b,
    ast.Sub: lambda a, b: a - b,
    ast.Mult: lambda a, b: a * b,
    ast.Div: lambda a, b: a / b,
    ast.FloorDiv: lambda a, b: a // b,
    ast.Mod: lambda a, b: a % b,
}
_ALLOWED_UNARY = {
    ast.USub: lambda a: -a,
    ast.UAdd: lambda a: +a,
    ast.Not: lambda a: not a,
}
_ALLOWED_COMPARE = {
    ast.Eq: lambda a, b: a == b,
    ast.NotEq: lambda a, b: a != b,
    ast.Lt: lambda a, b: a < b,
    ast.LtE: lambda a, b: a <= b,
    ast.Gt: lambda a, b: a > b,
    ast.GtE: lambda a, b: a >= b,
}
_ALLOWED_FUNCTIONS: dict[str, object] = {
    "clamp": lambda value, lo, hi: max(lo, min(hi, value)),
    "min": min,
    "max": max,
    "abs": abs,
    "tanh": math.tanh,
    "cos": math.cos,
    "sin": math.sin,
    "tan": math.tan,
    "acos": math.acos,
    "radians": math.radians,
    "degrees": math.degrees,
    "round_half_even": round,
}
_ALLOWED_CONSTANTS = {"pi": math.pi}

_ANNOTATION = re.compile(r"（[^（）]*）\s*$")


class ExpressionError(ValueError):
    """方程字符串不是受支持的表达式，或求值失败。"""


def parse(text: str) -> ast.Expression:
    """解析方程字符串为受限 AST；不可解析即 :class:`ExpressionError`。"""
    if not isinstance(text, str) or not text.strip():
        raise ExpressionError(f"方程字符串为空: {text!r}")
    candidates = [text.strip()]
    stripped = text.strip()
    while True:
        stripped = _ANNOTATION.sub("", stripped).strip()
        if stripped and stripped != candidates[-1]:
            candidates.append(stripped)
        else:
            break
    errors = []
    for candidate in candidates:
        try:
            tree = ast.parse(candidate, mode="eval")
        except SyntaxError as exc:
            errors.append(f"{candidate!r}: {exc.msg}")
            continue
        _validate(tree)
        return tree
    raise ExpressionError(
        f"方程字符串不可解析（剥离注记后仍失败）: {text!r}；"
        f"尝试: {'; '.join(errors)}"
    )


def referenced_names(text: str) -> frozenset[str]:
    """表达式引用的变量名集合（函数名与常量除外）。"""
    tree = parse(text)
    call_names = {
        node.func.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    }
    names = {
        node.id for node in ast.walk(tree) if isinstance(node, ast.Name)
    }
    return frozenset(names - call_names - set(_ALLOWED_CONSTANTS))


def evaluate(text: str, variables: Mapping[str, object]) -> object:
    """按受限语义求值方程字符串（未知变量即 KeyError）。"""
    return evaluate_tree(parse(text), variables)


def evaluate_tree(
    tree: ast.Expression, variables: Mapping[str, object],
) -> object:
    """求值已解析的受限 AST。"""
    return _eval_node(tree.body, variables)


def bind_arguments(
    mechanism,
    arguments: Mapping[str, object],
    parameters: Mapping[str, object],
) -> dict[str, object]:
    """把父值（按 argument 名）与参数值绑定为表达式变量（按 argument 名）。

    Args:
        mechanism: ``MechanismDecl``（含 parents/param_arguments）。
        arguments: ``{父 argument: 值}``（调用方按声明语义解析）。
        parameters: 程序参数值表 ``{参数 ID: 值}``。

    Raises:
        KeyError: 父值缺失或参数未声明（fail-closed）。
    """
    bound: dict[str, object] = {}
    for parent in mechanism.parents:
        if parent.argument not in arguments:
            raise KeyError(
                f"表达式绑定缺父值: {parent.slot}（{mechanism.id}）"
            )
        bound[parent.argument] = arguments[parent.argument]
    for parameter_id, argument in mechanism.param_arguments:
        if parameter_id not in parameters:
            raise KeyError(
                f"表达式绑定缺参数: {parameter_id}"
            )
        bound[argument] = parameters[parameter_id]
    return bound


def _validate(tree: ast.Expression) -> None:
    for node in ast.walk(tree):
        if isinstance(node, (ast.Add, ast.Sub, ast.Mult, ast.Div,
                             ast.FloorDiv, ast.Mod, ast.USub, ast.UAdd,
                             ast.Not, ast.Eq, ast.NotEq, ast.Lt, ast.LtE,
                             ast.Gt, ast.GtE, ast.And, ast.Or,
                             ast.Load, ast.Expression, ast.IfExp)):
            continue
        if isinstance(node, (ast.BinOp, ast.UnaryOp, ast.BoolOp,
                             ast.Compare, ast.Call)):
            continue
        if isinstance(node, ast.Name):
            continue  # 变量名/常量/函数名的合法性由求值与调用检查兜底
        if isinstance(node, ast.Constant):
            if isinstance(node.value, (int, float, str, bool)):
                continue
        raise ExpressionError(
            f"方程表达式包含不受支持的语法: {type(node).__name__}"
        )
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            if not isinstance(node.func, ast.Name):
                raise ExpressionError("方程表达式只允许按名调用白名单函数")
            if node.func.id not in _ALLOWED_FUNCTIONS:
                raise ExpressionError(
                    f"方程表达式调用了未白名单函数: {node.func.id}"
                )
            if node.keywords:
                raise ExpressionError("方程表达式不允许关键字实参")


def _eval_node(node: ast.AST, variables: Mapping[str, object]) -> object:
    if isinstance(node, ast.Constant):
        return node.value
    if isinstance(node, ast.Name):
        if node.id in _ALLOWED_CONSTANTS:
            return _ALLOWED_CONSTANTS[node.id]
        if node.id in variables:
            return variables[node.id]
        raise KeyError(f"方程表达式引用了未绑定变量: {node.id}")
    if isinstance(node, ast.BinOp):
        op = _ALLOWED_BINARY.get(type(node.op))
        if op is None:
            raise ExpressionError(f"不支持的二元运算: {type(node.op).__name__}")
        return op(_eval_node(node.left, variables),
                  _eval_node(node.right, variables))
    if isinstance(node, ast.UnaryOp):
        op = _ALLOWED_UNARY.get(type(node.op))
        if op is None:
            raise ExpressionError(f"不支持的一元运算: {type(node.op).__name__}")
        return op(_eval_node(node.operand, variables))
    if isinstance(node, ast.BoolOp):
        values = [_eval_node(value, variables) for value in node.values]
        return all(values) if isinstance(node.op, ast.And) else any(values)
    if isinstance(node, ast.Compare):
        left = _eval_node(node.left, variables)
        for op_node, right_node in zip(node.ops, node.comparators):
            op = _ALLOWED_COMPARE.get(type(op_node))
            if op is None:
                raise ExpressionError(
                    f"不支持的比较运算: {type(op_node).__name__}"
                )
            right = _eval_node(right_node, variables)
            if not op(left, right):
                return False
            left = right
        return True
    if isinstance(node, ast.IfExp):
        if _eval_node(node.test, variables):
            return _eval_node(node.body, variables)
        return _eval_node(node.orelse, variables)
    if isinstance(node, ast.Call):
        function = _ALLOWED_FUNCTIONS[node.func.id]  # _validate 已保证
        args = [_eval_node(arg, variables) for arg in node.args]
        return function(*args)
    raise ExpressionError(f"求值遇到不受支持的节点: {type(node).__name__}")


__all__ = [
    "ExpressionError",
    "bind_arguments",
    "evaluate",
    "evaluate_tree",
    "parse",
    "referenced_names",
]
