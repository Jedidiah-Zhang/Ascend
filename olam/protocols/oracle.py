"""oracle — 真实机制的平行轨迹查询（综述 §2.4.1；反事实与认知 00）。

给定实验单位（种子）、实验臂与地平线，从同一初始状态出发生成**真实观测
序列**；同一单位的多个臂共享全部随机地址（CRN 配对），配对差即世界真实
的干预效应。oracle 是评价的上界与正控制来源，不参与智能体可见通道。
"""

from __future__ import annotations

from typing import Mapping

from olam.protocols.experiment import Arm
from olam.protocols.observation import ObservationSpec, observe
from olam.runtime.process import WorldProcess

__all__ = ["Oracle"]


class Oracle:
    """世界声明的直接执行者（研究侧真值查询）。"""

    def __init__(
        self,
        program: object,
        observation: ObservationSpec,
    ) -> None:
        self._program = program
        self._observation = observation

    @property
    def observation(self) -> ObservationSpec:
        """本 oracle 使用的观测协议。"""
        return self._observation

    def rollout(
        self,
        *,
        seed: int,
        arm: Arm,
        horizon: int,
        initial_state: Mapping[str, object] | None = None,
        tick: int = 0,
    ) -> tuple[dict[str, object], ...]:
        """单个实验单位的真实观测序列（含该臂的干预时间线）。"""
        process = WorldProcess(
            self._program,
            seed=seed,
            initial_state=initial_state,
            tick=tick,
        )
        frames: list[dict[str, object]] = []
        for _ in range(horizon):
            process.step(interventions=arm.at(process.tick + 1))
            frames.append(
                observe(
                    self._program,
                    process,
                    self._observation,
                    observer="oracle",
                )
            )
        return tuple(frames)

    def paired_delta(
        self,
        *,
        seed: int,
        arm: Arm,
        baseline: Arm,
        horizon: int,
        slot: str,
        initial_state: Mapping[str, object] | None = None,
    ) -> tuple[object, ...]:
        """同一 ω 的配对效应：``arm − baseline`` 逐帧差。"""
        left = self.rollout(
            seed=seed,
            arm=arm,
            horizon=horizon,
            initial_state=initial_state,
        )
        right = self.rollout(
            seed=seed,
            arm=baseline,
            horizon=horizon,
            initial_state=initial_state,
        )
        return tuple(
            first["values"][slot] - second["values"][slot]  # type: ignore[operator]
            for first, second in zip(left, right)
        )
