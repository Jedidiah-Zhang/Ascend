"""因果世界声明的数据契约。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable


@dataclass(frozen=True, slots=True)
class InstanceDomain:
    """变量实例的创建、销毁与索引域。"""

    kind: str
    axes: tuple[str, ...]
    creation: str
    destruction: str


@dataclass(frozen=True, slots=True)
class ValueDomain:
    """变量值域；不适用项也必须显式写为 ``None`` 或空元组。"""

    kind: str
    unit: str
    bounds: tuple[float, float] | None
    choices: tuple[object, ...]
    missing: str
    quantization: str


@dataclass(frozen=True, slots=True)
class StateOwnership:
    """变量是否属于完整世界状态，以及非状态量的重建规则。"""

    in_world_state: bool
    reconstruction: str


@dataclass(frozen=True, slots=True)
class UpdateContract:
    """变量的帧内写入位置与未更新时语义。"""

    schedule: str
    microstep: str
    when_not_updated: str
    writer: str
    merge_rule: str


@dataclass(frozen=True, slots=True)
class AccessPolicy:
    """变量的干预、研究追踪与智能体观测权限。"""

    interventions: tuple[str, ...]
    research_trace: bool
    observation_protocols: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class MathMetadata:
    """误差传播使用的变量级数学元数据。"""

    error_budget: float
    metric: str
    valid_domain: str


@dataclass(frozen=True, slots=True)
class NodeSpec:
    """完整的内生变量模板声明。"""

    node_id: str
    role: str
    origin: str
    instance_domain: InstanceDomain
    value: ValueDomain
    state: StateOwnership
    update: UpdateContract
    access: AccessPolicy
    math: MathMetadata


@dataclass(frozen=True, slots=True)
class ParentSpec:
    """一个结构父模板及其时间、空间和边界语义。"""

    parent: str
    argument: str
    lag: int
    source_microstep: str
    spatial_offsets: tuple[tuple[int, ...], ...]
    entity_relation: str
    aggregation: str
    broadcast: str
    boundary_operator: str
    guard: str
    lipschitz: float
    metric: str
    valid_domain: str
    analysis_role: str


@dataclass(frozen=True, slots=True)
class ParameterSpec:
    """不属于世界状态的环境参数声明。"""

    parameter_id: str
    value_type: str
    unit: str
    bounds: tuple[float, float] | None
    value: object
    version: str
    intervention_allowed: bool
    source: str


@dataclass(frozen=True, slots=True)
class ParameterBinding:
    """机制参数到 Python 函数参数名的绑定。"""

    parameter: str
    argument: str


@dataclass(frozen=True, slots=True)
class ExogenousSourceSpec:
    """原始世界随机源及稳定地址模板。"""

    source_id: str
    distribution: str
    distribution_parameters: tuple[tuple[str, object], ...]
    draw_microstep: str
    instance_axes: tuple[str, ...]
    address_template: tuple[str, ...]
    shared_by: tuple[str, ...]
    dynamic_field: bool


@dataclass(frozen=True, slots=True)
class RandomBinding:
    """机制随机输入到原始外生源的绑定。"""

    source: str
    argument: str


@dataclass(frozen=True, slots=True)
class DependencyWitness:
    """C1 功能依赖见证：固定上下文，仅替换一个父值。

    Attributes:
        random_values: 见证评估的随机上下文（源 ID → 取值）。
            空 = 每个声明的随机源取注册表占位常数；显式给出可避免
            "父依赖恰好在占位值处抵消"的误拒。
    """

    label: str
    parent: str
    inputs: tuple[tuple[str, object], ...]
    alternate_value: object
    expected_outputs: tuple[object, object]
    random_values: tuple[tuple[str, object], ...] = ()


@dataclass(frozen=True, slots=True)
class MechanismSpec:
    """可执行结构方程及全部显式依赖。"""

    mechanism_id: str
    output: str
    equation: str
    function: Callable[..., object] = field(repr=False, compare=False)
    parents: tuple[ParentSpec, ...]
    parameters: tuple[ParameterBinding, ...]
    random_sources: tuple[RandomBinding, ...]
    boundary_cases: tuple[str, ...]
    source_dependencies: tuple[object, ...] = field(
        repr=False,
        compare=False,
    )
    witnesses: tuple[DependencyWitness, ...]
