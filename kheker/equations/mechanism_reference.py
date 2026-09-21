"""机制级独立参考求值。

优先级：``REFERENCE_IMPLS`` 中的独立函数；否则按声明方程字符串求值
（``expression.py``，变量解析顺序：机制形参 → 配置常量小写名）。
两者都解释不了即拒绝（fail-closed），由 ``reference_check`` 的覆盖门禁
在 CI 中暴露。
"""

from __future__ import annotations

from collections.abc import Mapping

from olam import constants as _config

from expression import (
    ExpressionError,
    bind_arguments,
    evaluate,
    referenced_names,
)
from reference_impl import REFERENCE_IMPLS


def config_constants() -> dict[str, object]:
    """配置常量的小写名视图（方程字符串可按小写名引用，如 game_day）。"""
    return {
        name.lower(): value
        for name, value in vars(_config).items()
        if name.isupper()
        and isinstance(value, (int, float, bool, str))
        and not callable(value)
    }


def reference_value(
    mechanism,
    arguments: Mapping[str, object],
    parameters: Mapping[str, object],
) -> object:
    """独立参考求值：先查参考实现，再按方程表达式求值。

    ``arguments`` 按父 argument 名给出；``parameters`` 按参数 ID 给出值。
    """
    env = bind_arguments(mechanism, arguments, parameters)
    impl = REFERENCE_IMPLS.get(mechanism.id)
    if impl is not None:
        return impl(env)
    variables = config_constants()
    variables.update(env)
    missing = sorted(set(referenced_names(mechanism.equation)) - set(variables))
    if missing:
        raise ExpressionError(
            f"{mechanism.id}: 方程字符串引用未绑定名 {missing}；"
            f"请改写为可执行表达式或登记 REFERENCE_IMPLS"
        )
    return evaluate(mechanism.equation, variables)


def unresolved_names(mechanism) -> list[str]:
    """方程字符串中无法解析的名字（覆盖门禁用；参考实现覆盖则返回空）。"""
    if mechanism.id in REFERENCE_IMPLS:
        return []
    args = {
        parent.argument for parent in mechanism.parents
    } | {
        argument for _, argument in mechanism.param_arguments
    }
    try:
        names = referenced_names(mechanism.equation)
    except ExpressionError:
        return [f"<不可解析方程> {mechanism.equation[:40]!r}"]
    return sorted(set(names) - args - set(config_constants()))


__all__ = ["config_constants", "reference_value", "unresolved_names"]
