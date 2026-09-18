"""数值原语包（issue #53）：精确语义内核与包络的地基。

- ``fixed``：二进制定点运算（纯整数、半偶舍入、溢出 fail-closed）；
- ``frozen_tables``/``tables``：超越函数冻表（cos/sin/tanh/acos/
  tan/degrees）与纯整数查询、声明误差；
- ``diurnal``：昼夜链定点/冻表实现（迁移先导）；
- ``enclosure``：包络表示与代数、冻表函数区间扩展（P4）。
"""

from . import enclosure, fixed

__all__ = ["enclosure", "fixed"]
