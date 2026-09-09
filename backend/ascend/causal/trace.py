"""研究 trace — 逐节点求值记录、结构化随机地址与可重算校验。

研究日志用于验证声明和实现，**不等同于智能体感知数据，也不是玩法事件**
（[第一阶段实施定义](../../../../docs/研究理论/第一阶段实施定义.md) §8）：

* 与玩法事件分库：trace 只存在于 ``causal/`` 与研究通道（net 研究 API /
  终端），绝不进入 ``weather/events.py`` 的事件载荷；
* fail-closed：记录要么完整要么拒绝——缺方程版本、缺父值、缺声明的随机源
  值时**拒绝求值**，而不是留一条残缺记录；
* 可重算：把记录里的父值/参数/随机值喂回声明方程重跑，输出必须逐位相同
  （:meth:`TraceLog.replay`）。这是"日志可重算任意节点"的可执行断言。

记录内容对应实施定义 §8 的清单：分量实例与更新阶段、方程及声明版本、
父引用标识与实际父值、消费的随机地址、生效的干预及优先级、输出值与边界
处理结果。

**随机地址的现状**：生产声明当前不含外生随机源（天气切片的随机性在统一
天气场内部，被声明为 slice_boundary 边界输入）。本模块的
:class:`RandomAddress` 与随机值记录接口已就绪，外生源登记后即自动生效。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Mapping

if TYPE_CHECKING:
    from .registry import MechanismRegistry

# 干预在记录中的呈现：替换规格 → 人类可读标签
_INTERVENTION_LABELS = {
    "value": "value",
    "mechanism": "mechanism",
}


@dataclass(frozen=True, slots=True)
class RandomAddress:
    """一次随机抽取的结构化地址。

    Attributes:
        source: 随机源 ID（``ExogenousSourceSpec.source_id``）。
        frame: 抽取所在的世界帧（tick）。
        instance: 抽取所在实例（全局分量用空元组）。
        draw_index: 同一 (源, 帧, 实例) 内的抽取序号，从 0 起。
    """

    source: str
    frame: int
    instance: tuple = ()
    draw_index: int = 0

    def plain(self) -> dict[str, object]:
        """可序列化视图。"""
        return {
            "source": self.source,
            "frame": self.frame,
            "instance": list(self.instance),
            "draw_index": self.draw_index,
        }


@dataclass(frozen=True, slots=True)
class TraceRecord:
    """一次节点求值的完整记录（研究日志的最小单位）。

    Attributes:
        node_id: 输出分量 ID。
        frame: 逻辑帧（tick）。
        instance: 实例坐标（全局分量用空元组）。
        microstep: 该分量的更新阶段 r_v。
        mechanism_id: 生效机制的 ID（值干预时为空字符串）。
        equation_version: 生效方程的源码/依赖摘要（值干预时为空）。
        resolved_version: 方程 + 参数 + 边界组合摘要（值干预时为空）。
        parents: 父引用 ``(父节点 ID, 实际父值)``，按节点 ID 排序。
        parameters: 生效参数槽位 ``(参数 ID, 实际取值)``，按参数 ID 排序。
        random_addresses: 消费的结构化随机地址（含实际值，可选）。
        random_values: 地址 → 实际抽取值（重算自包含；无源时为空）。
        intervention: 生效干预的记录视图（无干预时为 None）。
        rep: 生效替换规格（"value" / "mechanism" / None）。
        output: 实际输出值。
        boundary: 声明边界情况元组（无声明为空元组）。
    """

    node_id: str
    frame: int
    instance: tuple = ()
    microstep: str = ""
    mechanism_id: str = ""
    equation_version: str = ""
    resolved_version: str = ""
    parents: tuple[tuple[str, object], ...] = ()
    parameters: tuple[tuple[str, object], ...] = ()
    random_addresses: tuple[RandomAddress, ...] = ()
    random_values: tuple[tuple[RandomAddress, object], ...] = ()
    intervention: dict[str, object] | None = None
    rep: str | None = None
    output: object = None
    boundary: tuple[str, ...] = ()

    def plain(self) -> dict[str, object]:
        """可序列化视图（研究 API / 终端 / 落盘共用）。"""
        return {
            "node_id": self.node_id,
            "frame": self.frame,
            "instance": list(self.instance),
            "microstep": self.microstep,
            "mechanism_id": self.mechanism_id,
            "equation_version": self.equation_version,
            "resolved_version": self.resolved_version,
            "parents": {name: value for name, value in self.parents},
            "parameters": {name: value for name, value in self.parameters},
            "random_addresses": [addr.plain() for addr in self.random_addresses],
            "random_values": {
                _address_key(addr): value for addr, value in self.random_values
            },
            "intervention": self.intervention,
            "rep": self.rep,
            "output": self.output,
            "boundary": list(self.boundary),
        }


def _address_key(address: RandomAddress) -> str:
    """地址的稳定字符串键（JSON 映射用）。"""
    return "|".join((
        address.source,
        str(address.frame),
        ",".join(str(axis) for axis in address.instance),
        str(address.draw_index),
    ))


class TraceLog:
    """研究日志 — 有界内存环形缓冲 + 可重算校验。

    与玩法事件分库：本类不订阅世界树、不发布事件，只被研究通道读取。

    Parameters:
        registry: 不可变机制注册表（重算用）。
        capacity: 保留的最大记录数（0 = 不限制；生产用有界值）。
    """

    def __init__(
        self,
        registry: MechanismRegistry,
        *,
        capacity: int = 4096,
    ) -> None:
        if capacity < 0:
            raise ValueError(f"capacity 必须 ≥ 0: {capacity}")
        self._registry = registry
        self._capacity = capacity
        self._records: list[TraceRecord] = []

    def __repr__(self) -> str:
        return (
            f"TraceLog(records={len(self._records)}, "
            f"capacity={self._capacity})"
        )

    @property
    def registry(self) -> MechanismRegistry:
        return self._registry

    def __len__(self) -> int:
        return len(self._records)

    def record(self, entry: TraceRecord) -> TraceRecord:
        """登记一条记录（fail-closed：不完整即拒绝）。

        Raises:
            ValueError: 记录缺少节点/阶段、父值与声明不符、机制未登记、
                或声明的随机源缺值。
        """
        self._validate(entry)
        self._records.append(entry)
        if self._capacity and len(self._records) > self._capacity:
            del self._records[: len(self._records) - self._capacity]
        return entry

    def records(
        self,
        *,
        frame: int | None = None,
        node_id: str | None = None,
    ) -> tuple[TraceRecord, ...]:
        """按帧/节点筛选记录（登记顺序）。"""
        return tuple(
            entry for entry in self._records
            if (frame is None or entry.frame == frame)
            and (node_id is None or entry.node_id == node_id)
        )

    def clear(self) -> int:
        """清空记录，返回被清空的条数。"""
        count = len(self._records)
        self._records.clear()
        return count

    def replay(self, entry: TraceRecord) -> object:
        """按记录重算该节点并返回结果（"日志可重算任意节点"）。

        值干预记录直接返回替换值（生成结果已被替换，无方程可重算）；
        其余记录用记录中的父值/参数/随机值执行声明方程。

        Raises:
            KeyError: 记录引用的机制未登记（声明已变）。
            ValueError: 父值/随机值/参数与机制声明不符（记录与声明不一致）。
        """
        if entry.rep == "value":
            return entry.output
        mechanism = self._registry.mechanisms.get(entry.mechanism_id)
        if mechanism is None:
            raise KeyError(f"记录引用的机制未登记: {entry.mechanism_id}")
        return self._registry.evaluate_mechanism(
            mechanism,
            dict(entry.parents),
            random_values=dict(entry.random_values) or None,
            parameter_values=dict(entry.parameters) or None,
        )

    def verify(self, entry: TraceRecord) -> bool:
        """重算并与记录输出逐位比对（不做容差：确定性语义精确相等）。"""
        return self.replay(entry) == entry.output

    def verify_all(self) -> list[TraceRecord]:
        """校验全部记录，返回重算不一致的记录（空 = 全部一致）。"""
        return [entry for entry in self._records if not self.verify(entry)]

    def _validate(self, entry: TraceRecord) -> None:
        if not entry.node_id:
            raise ValueError("trace 记录缺少节点 ID")
        node = self._registry.nodes.get(entry.node_id)
        if node is None:
            raise ValueError(f"trace 记录节点未声明: {entry.node_id}")
        if not entry.microstep:
            raise ValueError(f"trace 记录缺少更新阶段: {entry.node_id}")
        if entry.microstep != node.update.microstep:
            raise ValueError(
                f"trace 记录更新阶段与声明不符: {entry.node_id} "
                f"{entry.microstep!r} != {node.update.microstep!r}"
            )
        if entry.rep == "value":
            # 值覆盖：生成结果被替换，无方程可重算
            if entry.output is None:
                raise ValueError(f"值覆盖记录缺少输出: {entry.node_id}")
            return
        mechanism = self._registry.mechanisms.get(entry.mechanism_id)
        if mechanism is None:
            raise ValueError(
                f"trace 记录机制未登记: {entry.mechanism_id or entry.node_id}"
            )
        if mechanism.output != entry.node_id:
            raise ValueError(
                f"trace 记录机制输出与节点不符: "
                f"{mechanism.mechanism_id} -> {mechanism.output} "
                f"!= {entry.node_id}"
            )
        if not entry.equation_version:
            raise ValueError(f"trace 记录缺少方程版本: {entry.node_id}")
        expected_parents = {parent.parent for parent in mechanism.parents}
        actual_parents = {name for name, _ in entry.parents}
        if actual_parents != expected_parents:
            missing = sorted(expected_parents - actual_parents)
            extra = sorted(actual_parents - expected_parents)
            raise ValueError(
                f"trace 记录父值不匹配 {mechanism.mechanism_id}: "
                f"缺少={missing}, 多余={extra}"
            )
        expected_sources = {item.source for item in mechanism.random_sources}
        recorded_sources = {address.source for address in entry.random_addresses}
        if recorded_sources != expected_sources:
            missing = sorted(expected_sources - recorded_sources)
            raise ValueError(
                f"trace 记录缺少声明的随机地址 {mechanism.mechanism_id}: "
                f"{missing}"
            )
        supplied = {address.source for address, _ in entry.random_values}
        if expected_sources - supplied:
            raise ValueError(
                f"trace 记录缺少随机值 {mechanism.mechanism_id}: "
                f"{sorted(expected_sources - supplied)}"
            )


__all__ = [
    "RandomAddress",
    "TraceLog",
    "TraceRecord",
]
