"""验收 runner 的独立参考解释器 — 直接读声明、不调用引擎求值路径。

为什么要有它：用生产函数与生产函数比较只能检查数据搬运，发现不了方程实现
错误（[世界验收协议](../../../docs/研究理论/世界基座/04-世界验收协议.md) §4.1）。
因此本解释器只依赖 ``MechanismRegistry`` 的**声明数据**（父集/参数/随机源/
更新阶段），自己按声明顺序求值，与引擎轨迹逐节点对拍。

参考解释器只实现第一阶段声明允许的语义：
- 阶段按 ``microstep_order`` 全序；
- 父引用 ``lag=0`` 读本帧已求值结果，``lag>=1`` 读历史帧快照；
- 干预按 (目标空间, 替换规格) 替换生成，值与机制覆盖优先于原机制；
- 随机源按显式给定值消费（本 runner 用固定随机上下文，不引入新随机性）。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Mapping

from ascend.causal.registry import MechanismRegistry


@dataclass(frozen=True, slots=True)
class ValueOverride:
    """参考解释器的一条值覆盖（节点干预/持续干预）。"""

    node_id: str
    value: object
    frame_t0: int = 0
    duration: int | None = None

    def active(self, frame: int) -> bool:
        if frame < self.frame_t0:
            return False
        if self.duration is None:
            return True
        return frame < self.frame_t0 + self.duration


@dataclass
class ReferenceTrace:
    """参考解释器的逐帧结果与首次分歧定位。"""

    frames: list[dict[str, object]] = field(default_factory=list)
    divergence: tuple | None = None


class ReferenceInterpreter:
    """声明参考解释器（研究切片世界）。

    Parameters:
        registry: 不可变机制注册表。
        overrides: 值覆盖列表（按登记顺序，后者覆盖前者）。
        exogenous: 随机源取值 ``fn(source_id, frame, instance) -> value``；
            None = 使用声明分布的中位数近似（0.0），仅用于确定性切片。
    """

    def __init__(
        self,
        registry: MechanismRegistry,
        *,
        overrides: tuple[ValueOverride, ...] = (),
        exogenous=None,
    ) -> None:
        self._registry = registry
        self._overrides = tuple(overrides)
        self._exogenous = exogenous
        self._by_step: dict[str, list] = {}
        for mechanism in registry.mechanisms.values():
            step = registry.nodes[mechanism.output].update.microstep
            self._by_step.setdefault(step, []).append(mechanism)

    def _override(self, node_id: str, frame: int):
        """最后一条生效的值覆盖（后到覆盖先到）。"""
        hit = None
        for override in self._overrides:
            if override.node_id == node_id and override.active(frame):
                hit = override
        return hit

    def run(
        self,
        frames: range,
        initial: Mapping[str, object],
        *,
        instance: tuple = (),
    ) -> ReferenceTrace:
        """按帧推进并返回逐帧状态（每帧为 节点→值 的映射）。

        Args:
            frames: 要推进的帧序列（连续）。
            initial: 帧 0 之前的初始状态（节点→值）。
            instance: 实例坐标（记录用）。

        Returns:
            :class:`ReferenceTrace`（frames 按帧序）。
        """
        state = dict(initial)
        trace = ReferenceTrace()
        for frame in frames:
            prev = dict(state)
            current = dict(state)
            for step in self._registry.microstep_order:
                for mechanism in self._by_step.get(step, ()):
                    output = mechanism.output
                    override = self._override(output, frame)
                    if override is not None:
                        current[output] = override.value
                        continue
                    parents: dict[str, object] = {}
                    for parent in mechanism.parents:
                        source = prev if parent.lag >= 1 else current
                        if parent.parent not in source:
                            raise KeyError(
                                f"参考解释器缺父值: {parent.parent} "
                                f"（lag={parent.lag}, frame={frame}）"
                            )
                        parents[parent.parent] = source[parent.parent]
                    random_values = {}
                    for binding in mechanism.random_sources:
                        if self._exogenous is None:
                            raise ValueError(
                                f"机制 {mechanism.mechanism_id} 声明了随机源 "
                                f"{binding.source}，参考解释器需要 exogenous 回调"
                            )
                        random_values[binding.source] = self._exogenous(
                            binding.source, frame, instance,
                        )
                    current[output] = self._registry.evaluate_mechanism(
                        mechanism, parents, random_values=random_values,
                    )
            state = current
            trace.frames.append(dict(state))
        return trace


class SpatialReferenceInterpreter:
    """空间父模板参考解释器：按格逐点求值，独立于引擎的实例展开。

    Parameters:
        registry: 注册表（含声明了多空间偏移的父模板）。
        cells: 一维格坐标序列。
        overrides: 值覆盖（按格）。
    """

    def __init__(
        self,
        registry: MechanismRegistry,
        *,
        cells: tuple[int, ...],
        overrides: tuple = (),
    ) -> None:
        self._registry = registry
        self._cells = tuple(cells)
        self._overrides = tuple(overrides)
        self._by_step: dict[str, list] = {}
        for mechanism in registry.mechanisms.values():
            step = registry.nodes[mechanism.output].update.microstep
            self._by_step.setdefault(step, []).append(mechanism)

    def _value(self, state: Mapping, node_id: str, position: int):
        if position in state[node_id]:
            return state[node_id][position]
        raise KeyError(f"参考解释器缺位置 {position}: {node_id}")

    def _parents(self, mechanism, prev, current) -> dict[str, object]:
        values: dict[str, object] = {}
        for parent in mechanism.parents:
            source = prev if parent.lag >= 1 else current
            if len(parent.spatial_offsets) <= 1:
                values[parent.parent] = source[parent.parent][self._cell]
                continue
            out = []
            for offset in parent.spatial_offsets:
                position = self._cell + offset[0]
                if parent.boundary_operator == "replicate":
                    position = min(max(position, self._cells[0]), self._cells[-1])
                out.append(self._value(source, parent.parent, position))
            values[parent.parent] = tuple(out)
        return values

    def run(self, frames: range, initial: Mapping[int, object]) -> list[dict]:
        """逐帧推进；返回每帧的 节点→位置元组 快照。"""
        state = {node_id: dict(values) for node_id, values in initial.items()}
        out = []
        for frame in frames:
            prev = {node_id: dict(values) for node_id, values in state.items()}
            current = {
                node_id: dict(values) for node_id, values in state.items()
            }
            for step in self._registry.microstep_order:
                for mechanism in self._by_step.get(step, ()):
                    for cell in self._cells:
                        self._cell = cell
                        current[mechanism.output][cell] = (
                            self._registry.evaluate_mechanism(
                                mechanism, self._parents(mechanism, prev, current),
                            )
                        )
            state = current
            out.append({
                node_id: tuple(values[cell] for cell in self._cells)
                for node_id, values in state.items()
            })
        return out


def first_divergence(
    reference: list[dict[str, object]],
    engine: list[dict[str, object]],
    *,
    frame_offset: int = 0,
) -> tuple | None:
    """逐帧逐节点比较，返回首个分歧 ``(帧, 节点, 参考值, 引擎值)``。"""
    for index, (ref_frame, eng_frame) in enumerate(zip(reference, engine)):
        for node_id in sorted(ref_frame):
            if node_id not in eng_frame:
                return (index + frame_offset, node_id, ref_frame[node_id], None)
            if ref_frame[node_id] != eng_frame[node_id]:
                return (
                    index + frame_offset, node_id,
                    ref_frame[node_id], eng_frame[node_id],
                )
    if len(reference) != len(engine):
        return (min(len(reference), len(engine)) + frame_offset, "<frames>",
                len(reference), len(engine))
    return None
