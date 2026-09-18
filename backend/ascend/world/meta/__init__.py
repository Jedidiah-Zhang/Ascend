"""元模型 — 六种声明、求值上下文、校验与摘要。

对外入口：``ModulePack`` / ``WorldSpec``（装配）与 ``MechanismContext``
（实现侧接口）；其余为编译器与运行时的内部依赖。
"""

from __future__ import annotations

from .context import MechanismContext
from .declarations import (
    ARITHMETIC_DOMAINS,
    INSTANCE_KINDS,
    PERSIST_CLASSES,
    TIME_MODES,
    AddressUse,
    Arithmetic,
    InstanceDecl,
    InvariantDecl,
    KnobDecl,
    MechanismDecl,
    ModulePack,
    ParameterDecl,
    Parent,
    Permissions,
    RelationDecl,
    Schedule,
    SlotDecl,
    ValueDomain,
    When,
    Witness,
    WorldSpec,
)
from .validate import (
    kernel_digest,
    mechanism_digest,
    module_digest,
    source_digest,
    validate_module,
)

__all__ = [
    "ARITHMETIC_DOMAINS",
    "INSTANCE_KINDS",
    "PERSIST_CLASSES",
    "TIME_MODES",
    "AddressUse",
    "Arithmetic",
    "InstanceDecl",
    "InvariantDecl",
    "KnobDecl",
    "MechanismContext",
    "MechanismDecl",
    "ModulePack",
    "ParameterDecl",
    "Parent",
    "Permissions",
    "RelationDecl",
    "Schedule",
    "SlotDecl",
    "ValueDomain",
    "When",
    "Witness",
    "WorldSpec",
    "kernel_digest",
    "mechanism_digest",
    "module_digest",
    "source_digest",
    "validate_module",
]
