"""基线预测与评分 — E0/E1 骨架。

- :func:`persistence_predictor`：预测下一帧 = 本帧观测（无记忆基线）；
- :func:`score_frames`：逐键 MAE（数值）/ 错误率（离散）；
- :func:`paired_effects`：同单位两臂逐键均值差（CRN 配对效应）。

完整评价层级（E0–E4）、概率评分与校准不在本模块范围内；本模块只提供
可执行的基线口径与报告形状。
"""

from __future__ import annotations

from typing import Callable, Mapping, Sequence

__all__ = [
    "paired_effects",
    "persistence_predictor",
    "score_frames",
]

Observation = Mapping[object, object]
Predictor = Callable[[Mapping[str, object]], Observation]


def persistence_predictor(frame: Mapping[str, object]) -> Observation:
    """基线：把本帧观测原样作为下一帧预测。"""
    return dict(frame["values"])  # type: ignore[arg-type]


def score_frames(
    frames: Sequence[Mapping[str, object]],
    predictor: Predictor,
) -> dict[str, float]:
    """从第 2 帧起评分：数值键 MAE，离散键错误率。"""
    totals: dict[str, float] = {}
    counts: dict[str, int] = {}
    for index in range(1, len(frames)):
        predicted = predictor(frames[index - 1])
        actual = frames[index]["values"]
        for key, expected in actual.items():  # type: ignore[union-attr]
            guess = predicted.get(key)
            if guess is None:
                continue
            name = _key_name(key)
            if isinstance(expected, (int, float)) and not isinstance(
                expected, bool
            ):
                error = abs(float(guess) - float(expected))
            else:
                error = 0.0 if guess == expected else 1.0
            totals[name] = totals.get(name, 0.0) + error
            counts[name] = counts.get(name, 0) + 1
    return {
        name: totals[name] / counts[name]
        for name in sorted(totals)
        if counts[name]
    }


def paired_effects(
    frames_arm: Sequence[Mapping[str, object]],
    frames_baseline: Sequence[Mapping[str, object]],
) -> dict[str, float]:
    """同单位两臂的配对效应（逐键均值差；共同键）。"""
    totals: dict[str, float] = {}
    counts: dict[str, int] = {}
    for arm, base in zip(frames_arm, frames_baseline):
        arm_values = arm["values"]
        base_values = base["values"]
        for key, value in arm_values.items():  # type: ignore[union-attr]
            if key not in base_values:  # type: ignore[operator]
                continue
            other = base_values[key]  # type: ignore[index]
            if not isinstance(value, (int, float)) or isinstance(value, bool):
                continue
            if not isinstance(other, (int, float)) or isinstance(other, bool):
                continue
            name = _key_name(key)
            totals[name] = totals.get(name, 0.0) + (
                float(value) - float(other)
            )
            counts[name] = counts.get(name, 0) + 1
    return {
        name: totals[name] / counts[name]
        for name in sorted(totals)
        if counts[name]
    }


def _key_name(key: object) -> str:
    if isinstance(key, tuple):
        return "|".join(str(part) for part in key)
    return str(key)
