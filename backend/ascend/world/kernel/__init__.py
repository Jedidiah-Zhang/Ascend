"""数值内核 — 世界语义核的地基（WC-4.4 / WC-5）。

对外只暴露三类能力：

- **定点原语**（``fixed``）：Q(bits) 整数运算、半偶舍入、溢出显式失败；
- **冻表超越函数**（``tables``）：cos/sin/tanh/acos/tan 的纯整数查询；
- **地址随机**（``rng``）与**内容摘要**（``digest``）。

本包零世界语义：不含状态、不含机制、不读数据文件。调用方（模块实现）
只经本包做数值与随机；语义核路径不得触碰浮点与 ``math``。
"""

from __future__ import annotations

from .digest import (
    canonical_bytes,
    digest_bytes,
    digest_object,
    digest_text,
    file_digest,
)
from .fixed import (
    add,
    clamp,
    div,
    fit,
    from_ratio,
    mul,
    quantize,
    round_half_even_div,
    sqrt,
    to_float,
)
from .frozen_tables import TABLE_BITS, TABLE_DIGEST
from .rng import ALGORITHM, Address, address_seed, address_value, draw_fixed
from .tables import (
    ACOS_MAX_ERROR,
    DECLARED_EPSILON,
    TANH_MAX_ERROR,
    TAN_MAX_ERROR,
    acos_q,
    cos_q,
    degrees_q,
    sin_q,
    tan_q,
    tanh_q,
)

__all__ = [
    "ACOS_MAX_ERROR",
    "ALGORITHM",
    "Address",
    "DECLARED_EPSILON",
    "TABLE_BITS",
    "TABLE_DIGEST",
    "TANH_MAX_ERROR",
    "TAN_MAX_ERROR",
    "acos_q",
    "add",
    "address_seed",
    "address_value",
    "canonical_bytes",
    "clamp",
    "cos_q",
    "degrees_q",
    "digest_bytes",
    "digest_object",
    "digest_text",
    "div",
    "draw_fixed",
    "file_digest",
    "fit",
    "from_ratio",
    "mul",
    "quantize",
    "round_half_even_div",
    "sin_q",
    "sqrt",
    "tan_q",
    "tanh_q",
    "to_float",
]
