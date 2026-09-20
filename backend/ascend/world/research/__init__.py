"""研究层 — 观测 G、行动 Γ·Res、实验协议、oracle、控制世界与评分。

研究层与世界严格分离（WC-10.3）：观测只读已提交状态、行动只写干预
时间线、实验协议与评分属于协议身份而非世界身份。
"""

from __future__ import annotations

from .action import (
    ActionSpec,
    Intervention,
    gamma,
    interventions_at,
    resolve,
)
from .controls import (
    ControlTemplate,
    i0_control,
    i1_control,
    invalid_intervention_control,
    mechanism_replacement_control,
    pseudo_correlation_control,
)
from .experiment import Arm, ExperimentSpec
from .observation import ObservationSpec, observe
from .oracle import Oracle
from .pipeline import UnitResult, run_experiment
from .scoring import paired_effects, persistence_predictor, score_frames
from .subject import ScriptedSubject, ScriptedSubjectSpec
from .timeline import (
    FOREVER,
    INTERVENTION_TARGET_SPACES,
    SINGLE,
    WINDOW,
    InterventionRecord,
    InterventionTimeline,
    NodeResolution,
    PlannedIntervention,
    default_duration,
)

__all__ = [
    "ActionSpec",
    "Arm",
    "ControlTemplate",
    "ExperimentSpec",
    "FOREVER",
    "INTERVENTION_TARGET_SPACES",
    "Intervention",
    "InterventionRecord",
    "InterventionTimeline",
    "NodeResolution",
    "ObservationSpec",
    "Oracle",
    "PlannedIntervention",
    "SINGLE",
    "ScriptedSubject",
    "ScriptedSubjectSpec",
    "UnitResult",
    "WINDOW",
    "default_duration",
    "gamma",
    "i0_control",
    "i1_control",
    "interventions_at",
    "invalid_intervention_control",
    "mechanism_replacement_control",
    "observe",
    "paired_effects",
    "persistence_predictor",
    "pseudo_correlation_control",
    "resolve",
    "run_experiment",
    "score_frames",
]
