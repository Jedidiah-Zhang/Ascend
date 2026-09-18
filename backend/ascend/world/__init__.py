"""Ascend 世界核心 — 声明式世界工具包（世界架构 00）。

对外入口（深模块，内部实现不跨层暴露）：

- **声明**：``ModulePack`` / ``WorldSpec`` 与六种声明数据类；
- **编译**：``compile_world`` → :class:`WorldProgram`（不可变、可复现、携带身份）；
- **运行**：``WorldProcess``（帧事务、快照/重放、干预输入）；
- **研究**：``ObservationSpec`` / ``ActionSpec`` / ``ExperimentSpec`` / ``Oracle``；
- **证据**：``run_acceptance`` / ``run_witnesses``。

内核原语（定点/冻表/地址随机/摘要）经 ``ascend.world.kernel`` 提供给
模块实现；本包不承载游戏内容（内容数据在 ``data/*.json``）。
"""

from __future__ import annotations

from .compile import CompileError, UpdateGroup, WorldProgram, compile_world
from .evidence import (
    AcceptanceResult,
    run_acceptance,
    run_witnesses,
    witness_coverage_issues,
)
from .meta import (
    AddressUse,
    Arithmetic,
    InstanceDecl,
    InvariantDecl,
    KnobDecl,
    MechanismContext,
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
from .research import (
    ActionSpec,
    Arm,
    ExperimentSpec,
    Intervention,
    ObservationSpec,
    Oracle,
    gamma,
    interventions_at,
    observe,
    resolve,
)
from .runtime import (
    FrameFailure,
    FrameResult,
    LatticeField,
    WorldInvalidatedError,
    WorldProcess,
)

__all__ = [
    "AcceptanceResult",
    "ActionSpec",
    "AddressUse",
    "Arithmetic",
    "Arm",
    "CompileError",
    "ExperimentSpec",
    "FrameFailure",
    "FrameResult",
    "InstanceDecl",
    "Intervention",
    "InvariantDecl",
    "KnobDecl",
    "LatticeField",
    "MechanismContext",
    "MechanismDecl",
    "ModulePack",
    "ObservationSpec",
    "Oracle",
    "ParameterDecl",
    "Parent",
    "Permissions",
    "RelationDecl",
    "Schedule",
    "SlotDecl",
    "UpdateGroup",
    "ValueDomain",
    "When",
    "Witness",
    "WorldInvalidatedError",
    "WorldProcess",
    "WorldProgram",
    "WorldSpec",
    "compile_world",
    "gamma",
    "interventions_at",
    "observe",
    "resolve",
    "run_acceptance",
    "run_witnesses",
    "witness_coverage_issues",
]
