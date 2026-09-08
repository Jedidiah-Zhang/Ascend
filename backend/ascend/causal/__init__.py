"""因果机制声明、求值与快照接口。"""

from .miracle import (
    MIRACLE_REP_KINDS,
    MIRACLE_TARGET_SPACES,
    MiracleRecord,
    MiracleTable,
    NodeResolution,
)
from .miracle_engine import MiracleEvaluator, MiracleFrameExecutor
from .registry import MechanismRegistry
from .spec import (
    AccessPolicy,
    DependencyWitness,
    ExogenousSourceSpec,
    InstanceDomain,
    MathMetadata,
    MechanismSpec,
    NodeSpec,
    ParameterBinding,
    ParameterSpec,
    ParentSpec,
    RandomBinding,
    StateOwnership,
    UpdateContract,
    ValueDomain,
)

__all__ = [
    "AccessPolicy",
    "DependencyWitness",
    "ExogenousSourceSpec",
    "InstanceDomain",
    "MathMetadata",
    "MechanismRegistry",
    "MechanismSpec",
    "NodeSpec",
    "ParameterBinding",
    "ParameterSpec",
    "ParentSpec",
    "RandomBinding",
    "StateOwnership",
    "UpdateContract",
    "ValueDomain",
    "MIRACLE_REP_KINDS",
    "MIRACLE_TARGET_SPACES",
    "MiracleRecord",
    "MiracleTable",
    "NodeResolution",
    "MiracleEvaluator",
    "MiracleFrameExecutor",
]
