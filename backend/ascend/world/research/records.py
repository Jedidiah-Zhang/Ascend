"""研究记录 — 逐机制求值记录与可重算校验（迁移自旧 trace）。

研究日志用于验证声明和实现，**不等同于智能体感知数据，也不是玩法事件**
（[第一阶段实施定义](../../../../docs/研究理论/第一阶段实施定义.md) §8）：

* 与玩法事件分库：记录只存在于研究通道（net 研究 API / 终端），绝不进入
  天气事件载荷；
* fail-closed：记录要么完整要么拒绝——缺方程版本、缺父值、缺声明的随机
  值时拒绝登记；
* 可重算：把记录里的父值/参数喂回声明方程重跑，输出必须逐位相同
  （:meth:`TraceLog.replay`）。这是"日志可重算任意节点"的可执行断言。

记录内容对应实施定义 §8 的清单：分量实例与更新阶段、方程及声明版本、
父引用标识与实际父值、消费的随机地址、生效的干预、输出值。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping

from ascend.world.meta.declarations import MechanismDecl
from ascend.world.runtime.evaluate import evaluate_direct
from ascend.world.runtime.process import MechanismTrace

__all__ = [
    "MechanismTrace",
    "RandomAddress",
    "TraceLog",
    "TraceRecord",
]


@dataclass(frozen=True, slots=True)
class RandomAddress:
    """一次随机抽取的结构化地址（研究记录用；与内核 Address 同构）。"""

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
    """一次节点求值的完整记录（研究日志的最小单位）。"""

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
    #: 记录性质（双账分离）：
    #: - "eval" = 世界推进时的**发生**求值（唯一具有真值地位的一账）；
    #: - "recompute" = 事后重算（历史查询 / 日摘要采样 / 诊断回放）。
    kind: str = "eval"

    def plain(self) -> dict[str, object]:
        """可序列化视图（研究 API / 终端共用）。"""
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
            "random_addresses": [
                addr.plain() for addr in self.random_addresses
            ],
            "random_values": {
                _address_key(addr): value
                for addr, value in self.random_values
            },
            "intervention": self.intervention,
            "rep": self.rep,
            "output": self.output,
            "boundary": list(self.boundary),
            "kind": self.kind,
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

    与玩法事件分库：本类不订阅事件、不发布事件，只被研究通道读取。

    Parameters:
        program: 不可变世界程序（重算与校验用）。
        capacity: 保留的最大记录数（必须为正）。
    """

    def __init__(self, program: object, *, capacity: int = 4096) -> None:
        if capacity < 1:
            raise ValueError(
                f"capacity 必须为正整数（0 不是「不限制」）: {capacity}"
            )
        self._program = program
        self._capacity = capacity
        self._records: list[TraceRecord] = []
        # 容量淘汰计数（丢失报告）：有界内存意味着超量记录会被丢弃，
        # 研究侧必须知道丢了多少，而不是把"看不到"当成"没发生"。
        self._dropped = 0

    def __repr__(self) -> str:
        return (
            f"TraceLog(records={len(self._records)}, "
            f"capacity={self._capacity})"
        )

    @property
    def program(self) -> object:
        return self._program

    def __len__(self) -> int:
        return len(self._records)

    def record(self, entry: TraceRecord) -> TraceRecord:
        """登记一条记录（fail-closed：不完整即拒绝）。"""
        self._validate(entry)
        self._records.append(entry)
        if len(self._records) > self._capacity:
            overflow = len(self._records) - self._capacity
            del self._records[:overflow]
            self._dropped += overflow
        return entry

    @property
    def dropped(self) -> int:
        """因容量上界被淘汰的记录数（丢失报告；只增不减）。"""
        return self._dropped

    def counts(self) -> dict[str, int]:
        """按记录性质计数：``{"eval": n, "recompute": m}``。"""
        counts = {"eval": 0, "recompute": 0}
        for entry in self._records:
            counts[entry.kind] = counts.get(entry.kind, 0) + 1
        return counts

    def records(
        self,
        *,
        frame: int | None = None,
        node_id: str | None = None,
        kind: str | None = None,
    ) -> tuple[TraceRecord, ...]:
        """按帧/节点/记录性质筛选记录（登记顺序）。"""
        return tuple(
            entry for entry in self._records
            if (frame is None or entry.frame == frame)
            and (node_id is None or entry.node_id == node_id)
            and (kind is None or entry.kind == kind)
        )

    def page(
        self,
        *,
        frame: int | None = None,
        node_id: str | None = None,
        kind: str | None = None,
        offset: int = 0,
        limit: int = 200,
    ) -> tuple[tuple[TraceRecord, ...], int]:
        """筛选后分页读取：返回 ``(本页记录, 筛选后总数)``。"""
        if offset < 0:
            raise ValueError(f"offset 必须 ≥ 0: {offset}")
        if limit < 1:
            raise ValueError(f"limit 必须为正整数: {limit}")
        matched = self.records(frame=frame, node_id=node_id, kind=kind)
        return matched[offset : offset + limit], len(matched)

    def clear(self) -> int:
        """清空记录，返回被清空的条数。"""
        count = len(self._records)
        self._records.clear()
        return count

    def replay(self, entry: TraceRecord) -> object:
        """按记录重算该节点并返回结果（"日志可重算任意节点"）。

        值干预记录直接返回替换值（生成结果已被替换，无方程可重算）。

        Raises:
            KeyError: 记录引用的机制未登记（声明已变）。
            ValueError: 父值与机制声明不符（记录与声明不一致）。
        """
        if entry.rep == "value":
            return entry.output
        mechanism = self._mechanism(entry.mechanism_id)
        inputs = {slot: value for slot, value in entry.parents}
        return evaluate_direct(
            self._program,
            mechanism.id,
            inputs,
            tick=entry.frame,
        )

    def verify(self, entry: TraceRecord) -> bool:
        """重算并与记录输出逐位比对（不做容差：确定性语义精确相等）。"""
        return self.replay(entry) == entry.output

    def verify_all(self) -> list[TraceRecord]:
        """校验全部记录，返回重算不一致的记录（空 = 全部一致）。"""
        return [entry for entry in self._records if not self.verify(entry)]

    def _mechanism(self, mechanism_id: str) -> MechanismDecl:
        mechanism = self._program.mechanisms.get(mechanism_id)
        if mechanism is None:
            raise KeyError(f"记录引用的机制未登记: {mechanism_id}")
        return mechanism

    def _validate(self, entry: TraceRecord) -> None:
        if not entry.node_id:
            raise ValueError("记录缺少节点 ID")
        if entry.kind not in ("eval", "recompute"):
            raise ValueError(
                f"记录性质非法（应为 eval/recompute）: {entry.kind!r}"
            )
        slot = self._program.slots.get(entry.node_id)
        if slot is None:
            raise ValueError(f"记录节点未声明: {entry.node_id}")
        if entry.rep == "value":
            if entry.output is None:
                raise ValueError(f"值覆盖记录缺少输出: {entry.node_id}")
            return
        mechanism = self._mechanism(entry.mechanism_id)
        if entry.node_id not in mechanism.outputs():
            raise ValueError(
                f"记录机制输出与节点不符: {mechanism.id} -> "
                f"{mechanism.outputs()} != {entry.node_id}"
            )
        if not entry.microstep:
            raise ValueError(f"记录缺少更新阶段: {entry.node_id}")
        if entry.microstep != mechanism.when.key:
            raise ValueError(
                f"记录更新阶段与声明不符: {entry.node_id} "
                f"{entry.microstep!r} != {mechanism.when.key!r}"
            )
        if not entry.equation_version:
            raise ValueError(f"记录缺少方程版本: {entry.node_id}")
        expected_parents = {parent.slot for parent in mechanism.parents}
        actual_parents = {name for name, _ in entry.parents}
        if actual_parents != expected_parents:
            missing = sorted(expected_parents - actual_parents)
            extra = sorted(actual_parents - expected_parents)
            raise ValueError(
                f"记录父值不匹配 {mechanism.id}: 缺少={missing}, 多余={extra}"
            )
        expected_sources = set()
        if mechanism.address is not None:
            expected_sources.add(
                f"{mechanism.address.namespace}.{mechanism.address.purpose}"
            )
        recorded_sources = {
            address.source for address in entry.random_addresses
        }
        if recorded_sources != expected_sources:
            missing = sorted(expected_sources - recorded_sources)
            raise ValueError(
                f"记录缺少声明的随机地址 {mechanism.id}: {missing}"
            )
        supplied = {address.source for address, _ in entry.random_values}
        if expected_sources - supplied:
            raise ValueError(
                f"记录缺少随机值 {mechanism.id}: "
                f"{sorted(expected_sources - supplied)}"
            )


def record_from_trace(
    program: object,
    trace: MechanismTrace,
    *,
    frame: int,
    kind: str = "eval",
) -> TraceRecord:
    """运行时捕获 → 研究记录（引擎/适配器共用）。"""
    from ascend.world.kernel import digest_object

    mechanism = program.mechanisms.get(trace.mechanism_id)
    equation_version = (
        digest_object(
            {
                "mechanism": trace.mechanism_id,
                "microstep": trace.microstep,
            }
        )
        if mechanism is not None
        else ""
    )
    return TraceRecord(
        node_id=trace.slot,
        frame=frame,
        instance=tuple(trace.instance),
        microstep=trace.microstep,
        mechanism_id=trace.mechanism_id,
        equation_version=equation_version,
        parents=tuple(trace.parents),
        output=trace.output,
        kind=kind,
    )
