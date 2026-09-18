"""干预执行引擎 — 覆盖感知求值器与引擎级逐帧执行器。

``InterventionEvaluator`` 是生产求值入口：包裹注册表与干预时间线，按
(目标, 实例, 帧) 解析值干预并在求值点替换；参数干预经 ``parameter_values``
注入注册表求值（同一实现，见 registry.evaluate_mechanism）。

``InterventionFrameExecutor`` 是引擎级（研究切片）逐帧求值器：按微步序对
注册表切片逐节点求值，干预覆盖生效；值覆盖整段不消费随机地址（CRN）。
"""

from __future__ import annotations

from typing import Callable, Mapping

from .frame_history import FrameHistory
from .intervention import InterventionRecord, InterventionTimeline, NodeResolution
from .registry import MechanismRegistry


class InterventionEvaluator:
    """覆盖感知求值器（生产接线入口）。

    Parameters:
        registry: 不可变机制注册表。
        table: 干预时间线；None = 直通注册表（零开销回退）。
        trace: 研究日志；None = 不记录（零开销）。挂载后每次求值都留下
            一条完整记录（fail-closed），值覆盖也如实记录"生成结果被替换"。
    """

    def __init__(
        self,
        registry: MechanismRegistry,
        table: InterventionTimeline | None = None,
        *,
        trace: TraceLog | None = None,
    ) -> None:
        self._registry = registry
        self._table = table
        self._trace = trace

    @property
    def table(self) -> InterventionTimeline | None:
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
        trace_kind: str = "eval",
    ) -> object:
        """按干预解析求值一个节点（仅值覆盖；机制替换已废除，WC-1.3）。

        Args:
            target: 输出节点 ID。
            parent_values: 父值（按调用方已解析的实例与帧提供）。
            frame: 当前世界 tick。
            instance: 实例坐标（全局分量用空元组）。
            random_values: 随机源值（值覆盖命中时不消费，直接返回）。
            parameter_values: 参数槽位覆盖（干预表内联解析）。
            trace_kind: 记录性质（"eval" 发生 / "recompute" 重算；#50）。
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
                trace_kind=trace_kind,
            )
        resolution = table.resolve_node(target, instance, frame)
        if resolution.record is not None:
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
                    kind=trace_kind,
                ))
            return resolution.value
        merged_parameters = self._parameter_overrides(
            table, target, frame, parameter_values
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
            trace_kind=trace_kind,
        )

    def _parameter_overrides(
        self,
        table: InterventionTimeline,
        target: str,
        frame: int,
        parameter_values: Mapping[str, object] | None,
    ) -> dict[str, object] | None:
        """合并调用方参数覆盖与时间线参数干预（干预命中优先）。"""
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
    步序中的最新值；lag≥1 优先经 ``FrameHistory`` 解析历史窗口（未提供
    时 lag=1 回退 ``prev_state``、lag≥2 显式拒绝）。
    值干预命中的节点不消费随机地址（CRN）。追记已消费随机地址供测试断言。

    **空间父模板**：声明了多空间偏移的父引用（``len(spatial_offsets) > 1``）
    需要按格展开。传 ``spatial_cells`` 后，这类节点的帧状态是
    ``{位置: 值}``，执行器对每个位置取偏移邻居（含边界算子）后求值——
    与参考解释器同语义，互为对拍。

    Parameters:
        registry: 注册表切片（研究世界声明）。
        table: 干预表；None = 无干预基线轨迹。
        exogenous: 随机源采样器 ``fn(source_id, frame, instance) -> value``。
        spatial_cells: 空间展开的位置集合；None = 纯标量切片。
    """

    def __init__(
        self,
        registry: MechanismRegistry,
        table: InterventionTimeline | None = None,
        *,
        exogenous: Callable[[str, int, tuple], object] | None = None,
        spatial_cells: tuple[int, ...] | None = None,
    ) -> None:
        self._registry = registry
        self._evaluator = InterventionEvaluator(registry, table)
        self._exogenous = exogenous
        # 空间展开：节点 → 是否按格求值（该节点读多偏移父模板，或是空间实例域）
        self._spatial_cells = (
            tuple(sorted(spatial_cells)) if spatial_cells is not None else None
        )
        if self._spatial_cells is None:
            self._spatial_nodes = frozenset()
        else:
            # 空间节点 = 实例域为空间场（值按位置索引）；多偏移父模板
            # 只是"读邻居"的一种，单偏移的空间节点同样要按格求值。
            self._spatial_nodes = frozenset(
                node_id for node_id, node in registry.nodes.items()
                if node.instance_domain.kind == "spatial_field"
            )
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
        history: FrameHistory | None = None,
    ) -> dict[str, object]:
        """推进一帧；返回新的帧状态（新 dict，不就地修改）。

        空间切片下：空间节点的值是 ``{位置: 值}``，其余节点为标量。

        lag 语义（WC-4.2；issue #49）：``lag=0`` 读本帧已求值值；
        ``lag≥1`` 优先经 ``history``（``FrameHistory``，调用方在每帧
        结束后 ``commit``）解析任意历史窗口；未提供 ``history`` 时
        ``lag=1`` 回退 ``prev_state``，``lag≥2`` 显式拒绝（不静默降级）。
        """
        prev_state = prev_state if prev_state is not None else state
        next_state = dict(state)
        for microstep in self._registry.microstep_order:
            for mechanism in self._by_step.get(microstep, ()):
                output = mechanism.output
                if output in self._spatial_nodes:
                    next_state[output] = self._run_spatial(
                        mechanism, next_state, prev_state, history,
                        frame, instance,
                    )
                    continue
                resolution = (
                    self._evaluator.table.resolve_node(output, instance, frame)
                    if self._evaluator.table is not None
                    else None
                )
                if resolution is not None and resolution.record is not None:
                    next_state[output] = resolution.value
                    continue
                parents = self._parents(
                    mechanism.parents, next_state, prev_state, history,
                    frame, instance,
                )
                random_values: dict[str, object] = {}
                for binding in mechanism.random_sources:
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

    def _run_spatial(
        self,
        mechanism,
        state: dict[str, object],
        prev_state: dict[str, object],
        history: FrameHistory | None,
        frame: int,
        instance: tuple,
    ) -> dict[int, object]:
        """按格展开求值（空间父模板）：逐位置收集偏移邻居后调用方程。"""
        cells = self._spatial_cells
        if not cells:
            raise ValueError("空间节点需要 spatial_cells")
        output: dict[int, object] = {}
        for cell in cells:
            parents: dict[str, object] = {}
            for parent in mechanism.parents:
                raw = self._lagged_value(
                    parent, state, prev_state, history, frame,
                )
                if len(parent.spatial_offsets) <= 1:
                    parents[parent.parent] = raw[cell]
                    continue
                parents[parent.parent] = self._neighbours(
                    raw, parent, cell, cells,
                )
            output[cell] = self._evaluator.evaluate(
                mechanism.output, parents, frame=frame, instance=instance,
            )
        return output

    def _lagged_value(
        self,
        parent,
        state: dict[str, object],
        prev_state: dict[str, object],
        history: FrameHistory | None,
        frame: int,
    ):
        """按声明的 lag 解析父值（fail-closed，不静默降级）。"""
        if parent.lag == 0:
            if parent.parent not in state:
                raise KeyError(f"帧状态缺少父值: {parent.parent}")
            return state[parent.parent]
        if history is not None:
            return history.lookup(parent.parent, parent.lag, frame)
        if parent.lag == 1:
            if parent.parent not in prev_state:
                raise KeyError(f"上帧状态缺少父值: {parent.parent}")
            return prev_state[parent.parent]
        raise ValueError(
            f"lag={parent.lag} 需要 FrameHistory（未提供时仅支持 lag∈{{0,1}}）: "
            f"{parent.parent}"
        )

    @staticmethod
    def _neighbours(
        values: dict, parent, cell: int, cells: tuple[int, ...],
    ) -> tuple:
        """按父模板的空间偏移取邻居值（边界算子作用在位置上）。"""
        low, high = cells[0], cells[-1]
        out = []
        for offset in parent.spatial_offsets:
            position = cell + offset[0]
            if parent.boundary_operator == "replicate":
                position = min(max(position, low), high)
            if position not in values:
                raise KeyError(f"帧状态缺少位置 {position}: {parent.parent}")
            out.append(values[position])
        return tuple(out)

    def _parents(
        self,
        parents,
        state: dict[str, object],
        prev_state: dict[str, object],
        history: FrameHistory | None,
        frame: int,
        instance: tuple,
    ) -> dict[str, object]:
        values: dict[str, object] = {}
        for parent in parents:
            values[parent.parent] = self._lagged_value(
                parent, state, prev_state, history, frame,
            )
            self.reads.add((parent.parent, frame, instance))
        return values


__all__ = ["InterventionEvaluator", "InterventionFrameExecutor"]
