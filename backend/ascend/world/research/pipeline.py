"""实验管线 — 按单位与臂运行 oracle，产出基线分数与配对效应。

E0–E3 骨架：对每个实验单位（种子）与每条臂运行真实轨迹（oracle），用
注册的基线预测器评分，并计算相对基线臂的 CRN 配对效应。概率评分与校准
（《反事实与认知 02》）在阶段二接入；本模块只提供可执行的骨架与报告形状。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Mapping

from ascend.world.research.experiment import ExperimentSpec
from ascend.world.research.oracle import Oracle
from ascend.world.research.scoring import (
    paired_effects,
    persistence_predictor,
    score_frames,
)

__all__ = ["UnitResult", "run_experiment"]


@dataclass(frozen=True, slots=True)
class UnitResult:
    """单个（单位, 臂）的结果：基线分数 + 相对基线臂的配对效应。"""

    seed: int
    arm: str
    scores: Mapping[str, float]
    paired: Mapping[str, float]


def run_experiment(
    program: object,
    experiment: ExperimentSpec,
    *,
    predictor: Callable[[Mapping[str, object]], Mapping[object, object]]
    | None = None,
    initial_state: Mapping[str, object] | None = None,
) -> tuple[UnitResult, ...]:
    """按单位 × 臂运行实验；返回逐单位结果（含配对效应）。"""
    oracle = Oracle(program, experiment.observation)
    scorer = predictor or persistence_predictor
    baseline = experiment.arms[0]
    results: list[UnitResult] = []
    for seed in experiment.unit_seeds:
        frames_by_arm: dict[str, tuple] = {}
        for arm in experiment.arms:
            frames_by_arm[arm.id] = oracle.rollout(
                seed=seed,
                arm=arm,
                horizon=experiment.horizon,
                initial_state=initial_state,
            )
        base_frames = frames_by_arm[baseline.id]
        for arm in experiment.arms:
            frames = frames_by_arm[arm.id]
            paired = (
                {}
                if arm.id == baseline.id
                else paired_effects(frames, base_frames)
            )
            results.append(
                UnitResult(
                    seed=seed,
                    arm=arm.id,
                    scores=score_frames(frames, scorer),
                    paired=paired,
                )
            )
    return tuple(results)
