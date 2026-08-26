"""声明模式 — 加载 equations.json → VariableGraph，并做结构校验。

声明数据（research/equations/equations.json）是研究侧规范：指导代码
如何写、验证代码是否符合，不被生产代码引用。本模块是声明管线与
未来 Lean bridge 共用的加载器。

结构不变式（role 枚举、L≥0、悬空引用、结构边无环）由
VariableGraph 在声明时强制；本模块补充**语义校验**：
  - Pa 完整性：非外生且参与结构边的变量必须有结构入边；
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
    version = data.get("version")
    if version != 1:
        raise ValueError(
            f"不支持的声明版本: {version!r}（当前仅支持 1）")
    graph = VariableGraph()
    for name, spec in data["variables"].items():
        bounds = spec.get("bounds")
        graph.add_variable(
            name,
            domain=spec.get("domain", "continuous"),
            exogenous=spec.get("exogenous", False),
            bounds=tuple(bounds) if bounds else None,
            eps=spec.get("eps"),
        )
    for edge in data["edges"]:
        graph.declare_edge(
            edge["parent"],
            edge["child"],
            role=edge["role"],
            L=edge["L"],
            equation=edge.get("equation"),
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
                    f"Pa 完整性: {name} 非外生且参与结构边，但无结构入边")
    for name, spec in graph.variables.items():
        parents = graph.predecessors(name, ROLE_STRUCTURAL)
        if spec.exogenous and parents:
            issues.append(
                f"声明矛盾: {name} 标记 exogenous 但有结构入边: {sorted(parents)}")
    isolated = [v for v in graph.variables if not graph.get_related(v)]
    if isolated:
        issues.append(f"孤立变量（无任何边）: {sorted(isolated)}")
    return issues