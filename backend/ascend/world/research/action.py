"""行动协议 Γ 与冲突解析 Res（WC-10.3；第二/三阶段的接入点）。

行动不直接写世界：候选行动经具身映射转化为**世界干预**，多个主体的候选
经 Res 合并为联合干预后进入帧事务。当前最小可执行形式：单目标值干预
+ 稳定优先级（平局可用声明随机流 ``U^res``）。

机制替换不属于世界内干预（WC-1.3）：结构变化 = 换世界。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping

from ascend.world.kernel import Address, address_value

__all__ = [
    "ActionSpec",
    "Intervention",
    "gamma",
    "interventions_at",
    "resolve",
]


@dataclass(frozen=True, slots=True)
class ActionSpec:
    """一个可执行行动：目标槽位 + 替换值 + 时长（帧）。"""

    id: str
    target_slot: str
    value: object
    duration: int = 1
    priority: int = 0

    def __post_init__(self) -> None:
        if not self.id:
            raise ValueError("行动必须给出 id")
        if type(self.duration) is not int or self.duration <= 0:
            raise ValueError(f"行动时长必须为正整数: {self.duration!r}")


@dataclass(frozen=True, slots=True)
class Intervention:
    """世界干预：把某槽位在 [start, start+duration) 帧内替换为给定值。"""

    target_slot: str
    value: object
    start_tick: int
    duration: int = 1

    def active(self, tick: int) -> bool:
        return self.start_tick <= tick < self.start_tick + self.duration


def gamma(
    body_state: Mapping[str, object],
    action: ActionSpec,
    *,
    tick: int,
) -> Intervention:
    """具身映射 Γ：行动 → 世界干预（最小形式：恒等 + 时长）。

    ``body_state`` 是身体状态的只读视图（实体切片接入失败模式与资源
    约束后在此展开）；当前实现不读取它，但接口保留。
    """
    del body_state
    return Intervention(
        target_slot=action.target_slot,
        value=action.value,
        start_tick=tick,
        duration=action.duration,
    )


def resolve(
    actions: tuple[ActionSpec, ...],
    *,
    tick: int,
    tie_break_seed: int | None = None,
) -> tuple[Intervention, ...]:
    """冲突解析 Res：同目标取优先级高者；平局按稳定序或声明随机流。

    无平局随机时按 ``action.id`` 字典序（稳定键）；给出 ``tie_break_seed``
    时按地址 ``res/tie`` 抽取（可复现的平局裁决）。
    """
    by_target: dict[str, list[ActionSpec]] = {}
    for action in actions:
        by_target.setdefault(action.target_slot, []).append(action)
    resolved: list[Intervention] = []
    for target in sorted(by_target):
        candidates = by_target[target]
        best = max(action.priority for action in candidates)
        winners = [action for action in candidates if action.priority == best]
        if len(winners) == 1:
            winner = winners[0]
        else:
            winners.sort(key=lambda action: action.id)
            if tie_break_seed is None:
                winner = winners[0]
            else:
                address = Address(
                    namespace="res",
                    purpose="tie",
                    instance=(target, *[action.id for action in winners]),
                    time=tick,
                    draw_index=0,
                )
                index = address_value(
                    tie_break_seed, address, minimum=0,
                    maximum=len(winners) - 1,
                )
                winner = winners[index]
        resolved.append(gamma({}, winner, tick=tick))
    return tuple(resolved)


def interventions_at(
    interventions: tuple[Intervention, ...],
    tick: int,
) -> dict[str, object]:
    """本帧生效的干预（``{槽位: 替换值}``）。"""
    return {
        intervention.target_slot: intervention.value
        for intervention in interventions
        if intervention.active(tick)
    }
