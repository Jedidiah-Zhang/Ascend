"""实验协议 — 实验单位、臂、查询与划分（阶段二评价的容器）。

数据划分以**实验单位** ``ω``（世界种子及其地址空间）为最小独立单位：
同单位内不同臂共享全部随机地址（CRN 配对，WC-7.3）；训练/测试按单位
划分，不能把同一单位的帧打散后随机分配。

本模块只定义数据形态与时间线语义；训练器与评分在阶段二接入。
"""

from __future__ import annotations

from dataclasses import dataclass, field

from olam.protocols.action import Intervention, interventions_at
from olam.protocols.observation import ObservationSpec

__all__ = ["Arm", "ExperimentSpec"]


@dataclass(frozen=True, slots=True)
class Arm:
    """实验臂：一条干预时间线（后续可追加行动策略挂载点）。"""

    id: str
    interventions: tuple[Intervention, ...] = ()

    def __post_init__(self) -> None:
        if not self.id:
            raise ValueError("实验臂必须给出 id")

    def at(self, tick: int) -> dict[str, object]:
        """本帧干预（用于帧事务输入）。"""
        return interventions_at(self.interventions, tick)


@dataclass(frozen=True, slots=True)
class ExperimentSpec:
    """实验规格：臂集合 + 单位种子 + 地平线 + 观测协议。"""

    id: str
    arms: tuple[Arm, ...]
    unit_seeds: tuple[int, ...]
    horizon: int
    observation: ObservationSpec
    splits: dict[str, tuple[int, ...]] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.arms:
            raise ValueError("实验至少一个臂")
        if not self.unit_seeds:
            raise ValueError("实验至少一个实验单位")
        if type(self.horizon) is not int or self.horizon <= 0:
            raise ValueError(f"地平线必须为正整数: {self.horizon!r}")
        ids = [arm.id for arm in self.arms]
        if len(set(ids)) != len(ids):
            raise ValueError("实验臂 id 不得重复")

    def arm(self, arm_id: str) -> Arm:
        for arm in self.arms:
            if arm.id == arm_id:
                return arm
        raise KeyError(f"未声明实验臂: {arm_id}")
