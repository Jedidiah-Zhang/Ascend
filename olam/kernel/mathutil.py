"""基础数值工具 — 无状态、无 IO 的纯 Python 叶子模块。

供需要避开重依赖链的模块（如研究巡检、因果声明）直接导入。
"""


def clamp(value: float, lo: float, hi: float) -> float:
    """将值钳制在 [lo, hi] 区间内。

    Args:
        value: 输入值。
        lo: 下限。
        hi: 上限。

    Returns:
        钳制后的值。
    """
    return max(lo, min(hi, value))
