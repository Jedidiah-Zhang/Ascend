"""因果机制声明、求值与快照接口。"""

from .intervention import (
    FOREVER,
    INTERVENTION_REP_KINDS,
    INTERVENTION_TARGET_SPACES,
    SINGLE,
    WINDOW,
    InterventionRecord,
    InterventionTable,
    NodeResolution,
    default_duration,
)
from .intervention_engine import InterventionEvaluator, InterventionFrameExecutor
from .observe import (
    AGENT_WEATHER_PROTOCOL,
    RESEARCH_PROTOCOL,
    leaks_research_truth,
    observe,
)
from .registry import MechanismRegistry
from .trace import RandomAddress, TraceLog, TraceRecord
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
    "INTERVENTION_REP_KINDS",
    "INTERVENTION_TARGET_SPACES",
    "InterventionRecord",
    "InterventionTable",
    "NodeResolution",
    "InterventionEvaluator",
    "InterventionFrameExecutor",
    "RandomAddress",
    "TraceLog",
    "TraceRecord",
    "AGENT_WEATHER_PROTOCOL",
    "RESEARCH_PROTOCOL",
    "leaks_research_truth",
    "observe",
    "FOREVER",
    "SINGLE",
    "WINDOW",
    "default_duration",
]
