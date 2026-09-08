"""声明快照的研究图投影加载与结构校验。

``equations.json`` 由生产 ``MechanismRegistry`` 确定性生成；本模块只
读取其中的 ``variables/edges`` 兼容投影，供图巡检和 Lean bridge 使用。

结构不变式（role 枚举、L≥0、悬空引用、结构边无环）由
VariableGraph 在声明时强制；本模块补充**语义校验**：
  - 写者完整性：非切片边界且参与结构边的变量必须有结构入边；
  - 因果子图非空：至少一条 structural 边；
  - 无孤立变量（仅提示级）。
"""

from __future__ import annotations

import json
from pathlib import Path

from ascend.world_tree.root import (
    ROLE_STRUCTURAL,
    VariableGraph,
)


def load_declaration(path: str | Path) -> VariableGraph:
    """加载声明 JSON 并构建 VariableGraph。

    Args:
        path: equations.json 路径。

    Returns:
        构建好的 VariableGraph。

    Raises:
        ValueError / KeyError: 声明违反 VariableGraph 结构不变式
            （非法 role、L<0、悬空引用、重复声明、结构环）。
    """
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    version = data.get("schema_version")
    if version != 2 or data.get("version") != version:
        raise ValueError(
            f"不支持的声明版本: {version!r}（当前仅支持 2）")
    for key in (
        "declaration",
        "nodes",
        "parameters",
        "exogenous_sources",
        "mechanisms",
        "variables",
        "edges",
    ):
        if key not in data:
            raise KeyError(f"声明快照缺少必需键 {key!r}")
    declaration = data["declaration"]
    for key in ("id", "version", "hash", "microstep_order", "slice_boundary"):
        if key not in declaration:
            raise KeyError(f"declaration 缺少必需键 {key!r}")
    graph = VariableGraph()
    for name, spec in data["variables"].items():
        for key in ("domain", "exogenous", "bounds", "eps"):
            if key not in spec:
                raise KeyError(f"variables[{name!r}] 缺少必需键 {key!r}")
        bounds = spec["bounds"]
        graph.add_variable(
            name,
            domain=spec["domain"],
            exogenous=spec["exogenous"],
            bounds=tuple(bounds) if bounds is not None else None,
            eps=spec["eps"],
        )
    for edge in data["edges"]:
        for key in ("parent", "child", "role", "L", "equation"):
            if key not in edge:
                raise KeyError(f"edge 缺少必需键 {key!r}: {edge!r}")
        graph.declare_edge(
            edge["parent"],
            edge["child"],
            role=edge["role"],
            L=edge["L"],
            equation=edge["equation"],
        )
    return graph


def validate(graph: VariableGraph) -> list[str]:
    """结构语义校验（不改变图）。

    Args:
        graph: 已构建的 VariableGraph。

    Returns:
        问题描述列表；空列表表示通过。
    """
    issues: list[str] = []
    structural = [e for e in graph.edges() if e[2].role == ROLE_STRUCTURAL]
    if not structural:
        issues.append("无 structural 边 — 因果子图为空")
    for name, spec in graph.variables.items():
        participates = any(
            es.role == ROLE_STRUCTURAL and (p == name or c == name)
            for (p, c, es) in graph.edges()
        )
        if not spec.exogenous and participates:
            if not graph.predecessors(name, ROLE_STRUCTURAL):
                issues.append(
                    f"写者完整性: {name} 非切片边界且参与结构边，但无结构入边")
    for name, spec in graph.variables.items():
        parents = graph.predecessors(name, ROLE_STRUCTURAL)
        if spec.exogenous and parents:
            issues.append(
                f"声明矛盾: {name} 标记 exogenous 但有结构入边: {sorted(parents)}")
    isolated = [v for v in graph.variables if not graph.get_related(v)]
    if isolated:
        issues.append(f"孤立变量（无任何边）: {sorted(isolated)}")
    return issues
