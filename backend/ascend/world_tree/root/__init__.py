"""世界树 · 变量层（"根"）— 世界参数静态因果图（SCM）。

事件层（world_tree.graph.EventGraph）是动态实例图；本包是
结构不变的变量层因果图，事件层将来引用它作为根。

用法:
    from ascend.world_tree.root import (
        VariableGraph, ROLE_STRUCTURAL, ROLE_INVERSE, ROLE_OBSERVABLE,
    )
"""

from .graph import (
    ROLE_INVERSE,
    ROLE_OBSERVABLE,
    ROLE_STRUCTURAL,
    ROLES,
    CycleError,
    EdgeSpec,
    VariableGraph,
    VariableSpec,
)

__all__ = [
    "VariableGraph",
    "VariableSpec",
    "EdgeSpec",
    "ROLE_STRUCTURAL",
    "ROLE_INVERSE",
    "ROLE_OBSERVABLE",
    "ROLES",
    "CycleError",
]