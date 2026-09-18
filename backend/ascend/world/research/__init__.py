"""研究层 — 观测 G、行动 Γ·Res、实验协议与 oracle。

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
from .experiment import Arm, ExperimentSpec
from .observation import ObservationSpec, observe
from .oracle import Oracle

__all__ = [
    "ActionSpec",
    "Arm",
    "ExperimentSpec",
    "Intervention",
    "ObservationSpec",
    "Oracle",
    "gamma",
    "interventions_at",
    "observe",
    "resolve",
]
