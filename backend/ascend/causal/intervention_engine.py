"""干预执行引擎 — 覆盖感知求值器与引擎级逐帧执行器。

``InterventionEvaluator`` 是生产求值入口：包裹注册表与干预表，按 (目标,
实例, 帧) 解析值/机制干预并在求值点替换；参数干预经 ``parameter_values``
注入注册表求值（同一实现，见 registry.evaluate_mechanism）。

``InterventionFrameExecutor`` 是引擎级（研究切片）逐帧求值器：按微步序对
注册表切片逐节点求值，干预覆盖生效，并维护 CRN 随机流契约（值覆盖
整段不消费随机地址；机制覆盖只消费替换机制声明的源）。
"""

from __future__ import annotations

from typing import Callable, Mapping

from .intervention import InterventionRecord, InterventionTable, NodeResolution
from .registry import MechanismRegistry


class InterventionEvaluator:
    """覆盖感知求值器（生产接线入口）。

    Parameters:
        registry: 不可变机制注册表。
        table: 干预表；None = 直通注册表（零开销回退）。
        trace: 研究日志；None = 不记录（零开销）。挂载后每次求值都留下
            一条完整记录（fail-closed），值覆盖也如实记录"生成结果被替换"。
    """

    def __init__(
        self,
        registry: MechanismRegistry,
        table: InterventionTable | None = None,
        *,
        trace: TraceLog | None = None,
    ) -> None:
        self._registry = registry
        self._table = table
        self._trace = trace

    @property
    def table(self) -> InterventionTable | None:
        return self._table

    @property
    def trace(self) -> TraceLog | None:
        """挂载的研究日志（未挂载 = 不记录）。"""
        return self._trace

    def evaluate(
        self,
        target: str,
        parent_values: Mapping[str, object],
        *,
        frame: int = 0,
        instance: tuple = (),
        random_values: Mapping[str, object] | None = None,
        parameter_values: Mapping[str, object] | None = None,
    ) -> object:
        """按干预解析求值一个节点（值覆盖 > 机制覆盖 > 原机制）。

        Args:
            target: 输出节点 ID。
            parent_values: 父值（按调用方已解析的实例与帧提供）。
            frame: 当前世界 tick。
            instance: 实例坐标（全局分量用空元组）。
            random_values: 随机源值（值覆盖命中时不消费，直接返回）。
            parameter_values: 参数槽位覆盖（干预表内联解析）。
        """
        table = self._table
        trace = self._trace
        if table is None:
            return self._registry.evaluate(
                target,
                parent_values,
                random_values=random_values,
                parameter_values=parameter_values,
                trace=trace,
                frame=frame,
                instance=instance,
            )
        resolution = table.resolve_node(target, instance, frame)
        if resolution.rep == "value":
            if trace is not None:
                trace.record(self._registry.build_trace_record(
                    resolution=resolution,
                    target=target,
                    parent_values=parent_values,
                    frame=frame,
                    instance=instance,
                    mechanism=None,
                    parameters=None,
                    random_values=None,
                    output=resolution.value,
                ))
            return resolution.value
        merged_parameters = self._parameter_overrides(
            table, target, resolution, frame, parameter_values
        )
        if resolution.rep == "mechanism":
            return self._registry.evaluate_mechanism(
                resolution.mechanism,
                parent_values,
                random_values=random_values,
                parameter_values=merged_parameters,
                trace=trace,
                resolution=resolution,
                frame=frame,
                instance=instance,
            )
        return self._registry.evaluate(
            target,
            parent_values,
            random_values=random_values,
            parameter_values=merged_parameters,
            trace=trace,
            resolution=resolution,
            frame=frame,
            instance=instance,
        )

    def _parameter_overrides(
        self,
        table: InterventionTable,
        target: str,
        resolution: NodeResolution,
        frame: int,
        parameter_values: Mapping[str, object] | None,
    ) -> dict[str, object] | None:
        """合并调用方参数覆盖与干预表参数干预（表内活跃覆盖优先）。"""
        mechanism = None
        if resolution.rep == "mechanism":
            mechanism = resolution.mechanism
        else:
            mechanism = self._registry.mechanisms.get(target)
            if mechanism is None:
                mechanism = self._registry.mechanism_for(target)
        merged = dict(parameter_values or {})
        for binding in mechanism.parameters:
            active, value = table.resolve_parameter(binding.parameter, frame)
            if active:
                merged[binding.parameter] = value
        return merged


class InterventionFrameExecutor:
    """引擎级逐帧求值器（W1/W2 验收 runner 用研究切片）。

    按注册表微步偏序逐节点求值，父值从帧状态取用：lag=0 读当帧在
    步序中的最新值，lag>=1 读 ``prev_state``（上一帧结束快照）。
    值干预命中的节点不消费随机地址（CRN）；机制干预只消费替换机制
    声明的随机源。追记已消费随机地址供测试断言。

    Parameters:
        registry: 注册表切片（研究世界声明）。
        table: 干预表；None = 无干预基线轨迹。
        exogenous: 随机源采样器 ``fn(source_id, frame, instance) -> value``。
    """

    def __init__(
        self,
        registry: MechanismRegistry,
        table: InterventionTable | None = None,
        *,
        exogenous: Callable[[str, int, tuple], object] | None = None,
    ) -> None:
        self._registry = registry
        self._evaluator = InterventionEvaluator(registry, table)
        self._exogenous = exogenous
        self._by_step: dict[str, list] = {}
        for mechanism in registry.mechanisms.values():
            microstep = registry.nodes[mechanism.output].update.microstep
            self._by_step.setdefault(microstep, []).append(mechanism)
        self.drawn: set[tuple[str, int, tuple]] = set()
        self.reads: set[tuple[str, int, tuple]] = set()

    def run_frame(
        self,
        frame: int,
        state: dict[str, object],
        prev_state: dict[str, object] | None = None,
        *,
        instance: tuple = (),
    ) -> dict[str, object]:
        """推进一帧；返回新的帧状态（新 dict，不就地修改）。"""
        prev_state = prev_state if prev_state is not None else state
        next_state = dict(state)
        for microstep in self._registry.microstep_order:
            for mechanism in self._by_step.get(microstep, ()):
                output = mechanism.output
                resolution = (
                    self._evaluator.table.resolve_node(output, instance, frame)
                    if self._evaluator.table is not None
                    else None
                )
                if resolution is not None and resolution.rep == "value":
                    next_state[output] = resolution.value
                    continue
                effective = (
                    resolution.mechanism
                    if resolution is not None and resolution.rep == "mechanism"
                    else mechanism
                )
                # 父值按生效机制（含替换机制）的父集提供
                parents = self._parents(
                    effective.parents, next_state, prev_state,
                    frame, instance,
                )
                random_values: dict[str, object] = {}
                for binding in effective.random_sources:
                    value = self._exogenous(
                        binding.source, frame, instance
                    )
                    random_values[binding.source] = value
                    self.drawn.add((binding.source, frame, instance))
                next_state[output] = self._evaluator.evaluate(
                    output,
                    parents,
                    frame=frame,
                    instance=instance,
                    random_values=random_values,
                )
        return next_state

    def _parents(
        self,
        parents,
        state: dict[str, object],
        prev_state: dict[str, object],
        frame: int,
        instance: tuple,
    ) -> dict[str, object]:
        values: dict[str, object] = {}
        for parent in parents:
            if parent.lag == 0:
                if parent.parent not in state:
                    raise KeyError(f"帧状态缺少父值: {parent.parent}")
                values[parent.parent] = state[parent.parent]
            elif parent.lag == 1:
                if parent.parent not in prev_state:
                    raise KeyError(f"上帧状态缺少父值: {parent.parent}")
                values[parent.parent] = prev_state[parent.parent]
            else:
                raise ValueError(
                    f"研究切片执行器仅支持 lag∈{0,1}: "
                    f"{parent.parent} lag={parent.lag}"
                )
            self.reads.add((parent.parent, frame, instance))
        return values


__all__ = ["InterventionEvaluator", "InterventionFrameExecutor"]
