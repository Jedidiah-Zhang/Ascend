"""模块接入协议 — 声明面完整性与证据义务的机器检查。

接入一个模块 = **声明 + 证据 + 测试，零框架改动**（见
``docs/归档/研究理论/世界架构/02-模块接入协议.md``）：

- **声明面**：六种声明 + 参数/旋钮（编译器静态校验 C0–C2 已覆盖）；
- **证据义务**：模块说明、证据清单、机制方程与见证、边界情形；
- **研究元数据**：槽位 role/schedule/quantization/metric/access/观测协议
  （投影模块的投影期义务，P3b）；
- **误差界元数据**：每条父引用的 L/jump_bound 与有效域（G7 义务）。

已知豁免（:data:`PROTOCOL_EXEMPTIONS`）：按模块登记"允许缺失"的项与
理由——缺口显式可见，不静默通过；豁免消失（补齐后）即自动收紧。
"""

from __future__ import annotations

from dataclasses import dataclass

from olam.meta.declarations import ModulePack

__all__ = [
    "PROTOCOL_EXEMPTIONS",
    "ProtocolIssue",
    "module_protocol_issues",
]


@dataclass(frozen=True, slots=True)
class ProtocolIssue:
    """一条协议缺口：``code``（类别）+ ``target``（模块/槽位/机制/父引用）。"""

    code: str
    target: str

    def __str__(self) -> str:
        return f"{self.code}: {self.target}"


#: 已登记豁免：模块 id → ((缺口类别, 目标或前缀*), ...)。
#: 每条豁免都必须在《02-模块接入协议》里给出理由与缺口说明。
PROTOCOL_EXEMPTIONS: dict[str, tuple[tuple[str, str], ...]] = {
    # 场内核（terrain.integrate）：单机制整场内核，逐父 L 与有效域
    # 元数据未登记（内核误差界在 kernel.py 内声明；当前由内核对 +
    # 黄金向量承载证据）。
    "terrain": (
        ("parent.modulus", "terrain.integrate<-*"),
        ("parent.valid_domain", "terrain.integrate<-*"),
    ),
}


def _exempt(pack_id: str, issue: ProtocolIssue) -> bool:
    for code, pattern in PROTOCOL_EXEMPTIONS.get(pack_id, ()):
        if code != issue.code:
            continue
        if pattern == issue.target:
            return True
        if pattern.endswith("*") and issue.target.startswith(pattern[:-1]):
            return True
    return False


def module_protocol_issues(
    pack: ModulePack,
    *,
    require_research_metadata: bool = True,
    require_modulus: bool = True,
) -> tuple[ProtocolIssue, ...]:
    """按接入协议检查一个模块包；返回缺口（已登记豁免不在其中）。

    Args:
        pack: 模块包（六声明 + 参数/旋钮 + 证据义务）。
        require_research_metadata: 是否要求槽位研究元数据（投影模块与
            验收演练模块为 True；纯测试夹具可为 False）。
        require_modulus: 是否要求父引用误差界元数据（同左）。
    """
    issues: list[ProtocolIssue] = []

    if not pack.notes.strip():
        issues.append(ProtocolIssue("module.notes", pack.id))
    if not pack.evidence:
        issues.append(ProtocolIssue("module.evidence", pack.id))

    for slot in pack.slots:
        if slot.persist not in ("state", "derived"):
            continue
        if require_research_metadata and not (
            slot.role and slot.schedule and slot.quantization and slot.metric
        ):
            issues.append(ProtocolIssue("slot.metadata", slot.id))
        if (
            require_research_metadata
            and slot.permissions.intervene
            and not slot.access_interventions
        ):
            issues.append(ProtocolIssue("slot.access", slot.id))
        if (
            require_research_metadata
            and slot.permissions.observe
            and not slot.observation_protocols
        ):
            issues.append(ProtocolIssue("slot.access", slot.id))

    for mechanism in pack.mechanisms:
        if not mechanism.equation:
            issues.append(ProtocolIssue("mechanism.equation", mechanism.id))
        if not mechanism.boundary_cases:
            issues.append(
                ProtocolIssue("mechanism.boundary_cases", mechanism.id)
            )
        if not require_modulus:
            continue
        for parent in mechanism.parents:
            target = f"{mechanism.id}<-{parent.slot}"
            if (
                parent.modulus_kind == "linear"
                and parent.lipschitz is None
            ) or (
                parent.modulus_kind == "jump"
                and parent.jump_bound is None
            ):
                issues.append(ProtocolIssue("parent.modulus", target))
            if not parent.valid_domain:
                issues.append(ProtocolIssue("parent.valid_domain", target))

    return tuple(
        issue for issue in issues if not _exempt(pack.id, issue)
    )
