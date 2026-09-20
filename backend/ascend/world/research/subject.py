"""脚本主体 — 观测/行动闭环的最小主体（不含学习器）。

主体只在帧边界观测（G），按固定策略选择动作并经具身映射 Γ 转为世界干预。
它用于打通"观测 → 行动 → 干预 → oracle 真值 → 评分"的研究管线骨架，
并为后续学习器提供接口形状。
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ascend.world.research.action import ActionSpec, Intervention, gamma
from ascend.world.research.observation import ObservationSpec, observe

__all__ = ["ScriptedSubject", "ScriptedSubjectSpec"]


@dataclass(frozen=True, slots=True)
class ScriptedSubjectSpec:
    """脚本主体规格：位置 + 观测协议 + 固定动作序列。"""

    id: str
    position: tuple[int, int]
    observation: ObservationSpec
    actions: tuple[ActionSpec, ...]
    protocol: str = "scripted.v1"

    def __post_init__(self) -> None:
        if not self.id:
            raise ValueError("主体必须给出 id")
        if not self.actions:
            raise ValueError("脚本主体至少一个动作")


@dataclass(slots=True)
class ScriptedSubject:
    """固定策略主体：帧边界观测（G），循环取动作经 Γ 变为干预。"""

    spec: ScriptedSubjectSpec
    history: list = field(default_factory=list)
    cursor: int = 0

    def observe(self, program: object, process: object) -> dict[str, object]:
        """帧边界观测（记入历史）。"""
        observation = observe(
            program,
            process,
            self.spec.observation,
            observer=self.spec.id,
            position=self.spec.position,
        )
        self.history.append(
            {"kind": "observation", "tick": process.tick, "data": observation}
        )
        return observation

    def act(self, *, tick: int) -> Intervention:
        """选择动作并经 Γ 映射为世界干预（记入历史）。"""
        action = self.spec.actions[self.cursor % len(self.spec.actions)]
        self.cursor += 1
        intervention = gamma({}, action, tick=tick)
        self.history.append(
            {"kind": "action", "tick": tick, "action": action.id}
        )
        return intervention
