"""神迹系统 — 干预执行器核心（OverrideTable 语义 + 执行前校验 + 解析）。

神迹系统是研究者在世界上的"上帝之手"：把一次干预（do）形式化为一条
不可变的神迹记录（``MiracleRecord``），登记进神迹表（``MiracleTable``），
在节点/参数求值点按固定规则替换生成。语义契约（第一阶段实施定义 §7、
工程符号体系 §5、世界基座 04 §3.2/§3.3）：

- **目标空间**：node（节点生成结果）、parameter（参数槽位，环境变化）、
  field_feature（统一天气场特征核控制，运行时状态桥接）。
- **替换规格**：value（换常数）/ mechanism（换公式）；参数与特征核
  控制仅 value。
- **时长**：1 帧（原"节点干预"）／窗口（原"持续干预"）／长期（None，
  直到被替换或清除）。值覆盖可 1 帧或窗口；机制覆盖默认长期；参数与
  特征核控制为长期（环境变化）。
- **优先级/重叠（固定规则，无人工数字）**：研究者原子序列执行，
  同一 (空间, 目标, 实例, 替换规格) 后到覆盖先到；同一节点同时存在
  值覆盖与机制覆盖时值覆盖优先（影响范围更小，§7 "数值优先于机制"）。
- **CRN 随机流契约**：值覆盖整段不消费原机制随机地址；机制覆盖的
  替换机制随机源不得与无关机制重叠（可新增全新地址、可复用原地址）。
- **执行前校验（§7 六条）**：目标存在、值属声明值域、帧有效、权限
  允许（AccessPolicy.interventions / ParameterSpec.intervention_allowed）、
  重叠规则固定（无歧义）、读出分量保护（readout / slice_boundary 节点
  不可独立干预）、参数标记为环境变化。

本模块不依赖求值路径（见 :mod:`ascend.causal.miracle_engine` 的
``MiracleEvaluator`` / ``MiracleFrameExecutor``）。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Mapping

from .registry import MechanismRegistry
from .spec import MechanismSpec, NodeSpec, ParameterSpec

# 目标空间与替换规格的合法组合
MIRACLE_TARGET_SPACES = frozenset({"node", "parameter", "field_feature"})
MIRACLE_REP_KINDS = frozenset({"value", "mechanism"})
# 目标空间允许的替换规格
_REP_BY_SPACE = {
    "node": frozenset({"value", "mechanism"}),
    "parameter": frozenset({"value"}),
    "field_feature": frozenset({"value"}),
}


@dataclass(frozen=True, slots=True)
class MiracleRecord:
    """一条神迹（干预记录）。

    Attributes:
        target_space: "node" | "parameter" | "field_feature"。
        target: 节点 ID / 参数 ID / 特征类型名（field_feature）。
        instance: 实例坐标元组；全局分量用空元组 ()。
        rep: "value" | "mechanism"。
        value: rep="value" 时的替换值。
        mechanism: rep="mechanism" 时的替换机制（MechanismSpec）。
        frame_t0: 生效帧（tick）。
        duration: 时长；1=单帧，>1=窗口，None=长期。
        version: 登记方提供的版本号（溯源用）。
        environment_change: 参数神迹恒 True（环境变化，独立报告）。
        applied_at: 登记时的世界 tick（引擎写入）。
        seq: 全局自增序号（引擎写入，供 P3 trace / P4 还原）。
    """

    target_space: str
    target: str
    instance: tuple = ()
    rep: str = "value"
    value: object = None
    mechanism: object = None
    frame_t0: int = 0
    duration: int | None = None
    version: str = ""
    environment_change: bool = False
    applied_at: int | None = None
    seq: int | None = None


@dataclass(frozen=True, slots=True)
class NodeResolution:
    """节点求值解析结果：命中的神迹或空（保持原机制）。"""

    rep: str | None = None
    record: MiracleRecord | None = None
    value: object = None
    mechanism: object = None


class MiracleTable:
    """神迹表 — 神迹记录的单一事实源与解析器。

    构造绑定一个不可变注册表用于校验；表自身随登记/清除演化，
    但每条记录不可变，历史按登记顺序保留（供 P3 trace / P4 还原）。
    """

    def __init__(self, registry: MechanismRegistry) -> None:
        self._registry = registry
        # 键 → 当前有效记录（同一键后到覆盖先到）
        self._values: dict[tuple[str, str, tuple], MiracleRecord] = {}
        self._mechanisms: dict[tuple[str, str, tuple], MiracleRecord] = {}
        self._parameters: dict[str, MiracleRecord] = {}
        self._features: dict[tuple[str, str, tuple], MiracleRecord] = {}
        self._history: list[MiracleRecord] = []
        self._seq: int = 0

    @property
    def registry(self) -> MechanismRegistry:
        """绑定的不可变注册表（供终端/研究 API 解析目标）。"""
        return self._registry

    # ── 登记与清除 ─────────────────────────────────────────

    def commit(
        self,
        record: MiracleRecord,
        *,
        applied_at: int | None = None,
    ) -> MiracleRecord:
        """校验并登记一条神迹；同一键的后登记替换先登记（原子序列）。

        Args:
            record: 待登记神迹。
            applied_at: 登记时的世界 tick（引擎写入历史）。

        Returns:
            登记后的不可变记录（含 seq/applied_at）。

        Raises:
            ValueError: 任一执行前校验失败（fail-closed）。
        """
        self._validate(record)
        self._seq += 1
        stored = MiracleRecord(
            target_space=record.target_space,
            target=record.target,
            instance=tuple(record.instance or ()),
            rep=record.rep,
            value=(
                dict(record.value)
                if isinstance(record.value, dict)
                else record.value
            ),
            mechanism=record.mechanism,
            frame_t0=record.frame_t0,
            duration=record.duration,
            version=record.version,
            environment_change=(
                record.environment_change
                or record.target_space == "parameter"
            ),
            applied_at=applied_at,
            seq=self._seq,
        )
        if stored.target_space == "node":
            if stored.rep == "value":
                self._values[(stored.target, stored.instance)] = stored
            else:
                self._mechanisms[(stored.target, stored.instance)] = stored
        elif stored.target_space == "parameter":
            self._parameters[stored.target] = stored
        else:
            self._features[(stored.target, stored.instance)] = stored
        self._history.append(stored)
        return stored

    def clear(
        self,
        target_space: str,
        target: str,
        instance: tuple = (),
        *,
        rep: str | None = None,
    ) -> bool:
        """清除指定键的神迹（撤销）；返回是否命中。"""
        key = (target, tuple(instance or ()))
        if target_space == "node":
            if rep in (None, "value"):
                removed = self._values.pop(key, None)
                if removed is not None:
                    return True
            if rep in (None, "mechanism"):
                removed = self._mechanisms.pop(key, None)
                return removed is not None
            return False
        if target_space == "parameter":
            removed = self._parameters.pop(target, None)
            return removed is not None
        removed = self._features.pop(key, None)
        return removed is not None

    # ── 解析 ───────────────────────────────────────────────

    def resolve_node(
        self,
        target: str,
        instance: tuple,
        frame: int,
    ) -> NodeResolution:
        """解析节点求值：值神迹 > 机制神迹 > 原机制（固定规则）。

        值覆盖影响范围小于公式覆盖 → 同节点同时活跃时值优先。
        """
        value_key = (target, tuple(instance or ()))
        record = self._values.get(value_key)
        if record is not None and self._active(record, frame):
            return NodeResolution(
                rep="value", record=record,
                value=record.value, mechanism=None,
            )
        mechanism = self._mechanisms.get(value_key)
        if mechanism is not None and self._active(mechanism, frame):
            return NodeResolution(
                rep="mechanism", record=mechanism,
                value=None, mechanism=mechanism.mechanism,
            )
        return NodeResolution()

    def resolve_parameter(
        self,
        parameter_id: str,
        frame: int,
    ) -> tuple[bool, object]:
        """解析参数槽位覆盖；返回 (是否活跃, 覆盖值)。"""
        record = self._parameters.get(parameter_id)
        if record is not None and self._active(record, frame):
            return True, record.value
        return False, None

    def resolve_feature(
        self,
        feature: str,
        instance: tuple,
        frame: int,
    ) -> tuple[bool, MiracleRecord | None]:
        """解析统一天气场特征核控制神迹（force_feature 桥接）。"""
        record = self._features.get((feature, tuple(instance or ())))
        if record is not None and self._active(record, frame):
            return True, record
        return False, None

    # ── 快照与历史 ─────────────────────────────────────────

    @property
    def history(self) -> tuple[MiracleRecord, ...]:
        """按登记顺序的全部神迹记录（含被替换的历史，供 trace/还原）。"""
        return tuple(self._history)

    def snapshot(self) -> dict[str, object]:
        """当前有效记录的确定性快照（可序列化）。"""
        records = {
            "values": sorted(
                (self._record_plain(rec) for rec in self._values.values()),
                key=lambda item: (item["target"], item["instance"]),
            ),
            "mechanisms": sorted(
                (
                    {**self._record_plain(rec), "mechanism": rec.mechanism.mechanism_id}
                    for rec in self._mechanisms.values()
                ),
                key=lambda item: (item["target"], item["instance"]),
            ),
            "parameters": sorted(
                (self._record_plain(rec) for rec in self._parameters.values()),
                key=lambda item: item["target"],
            ),
            "features": sorted(
                (
                    self._record_plain(rec)
                    for rec in self._features.values()
                ),
                key=lambda item: (item["target"], item["instance"]),
            ),
        }
        return records

    # ── 内部 ───────────────────────────────────────────────

    @staticmethod
    def _active(record: MiracleRecord, frame: int) -> bool:
        if frame < record.frame_t0:
            return False
        if record.duration is None:
            return True
        return frame < record.frame_t0 + record.duration

    @staticmethod
    def _record_plain(record: MiracleRecord) -> dict[str, object]:
        return {
            "target_space": record.target_space,
            "target": record.target,
            "instance": list(record.instance),
            "rep": record.rep,
            "value": record.value,
            "frame_t0": record.frame_t0,
            "duration": record.duration,
            "version": record.version,
            "environment_change": record.environment_change,
            "applied_at": record.applied_at,
            "seq": record.seq,
        }

    # ── 执行前校验（§7 六条）───────────────────────────────

    def _validate(self, record: MiracleRecord) -> None:
        if record.target_space not in MIRACLE_TARGET_SPACES:
            raise ValueError(f"非法目标空间: {record.target_space!r}")
        if record.rep not in MIRACLE_REP_KINDS:
            raise ValueError(f"非法替换规格: {record.rep!r}")
        allowed_reps = _REP_BY_SPACE[record.target_space]
        if record.rep not in allowed_reps:
            raise ValueError(
                f"目标空间 {record.target_space} 不允许替换规格 {record.rep!r}"
            )
        if not isinstance(record.frame_t0, int) or record.frame_t0 < 0:
            raise ValueError(
                f"生效帧必须为非负整数 tick: {record.frame_t0!r}"
            )
        if record.duration is not None and (
            not isinstance(record.duration, int) or record.duration < 1
        ):
            raise ValueError(
                f"时长必须为 None（长期）或正整数 tick: {record.duration!r}"
            )
        if not record.target:
            raise ValueError("神迹目标不能为空")

        if record.target_space == "node":
            self._validate_node(record)
        elif record.target_space == "parameter":
            self._validate_parameter(record)
        else:
            self._validate_feature(record)

    def _validate_node(self, record: MiracleRecord) -> None:
        registry = self._registry
        if record.target not in registry.nodes:
            raise ValueError(f"目标分量未声明: {record.target}")
        node = registry.nodes[record.target]
        # 实例必须匹配节点实例域（§7 "目标分量实例存在"）
        self._validate_node_instance(record, node)
        # 读出分量保护：readout 与 slice_boundary 节点不可独立干预
        if node.role not in ("mechanism_state", "persistent_state"):
            raise ValueError(
                f"读出/边界分量不可独立干预: {record.target} "
                f"(role={node.role})"
            )
        if node.origin != "mechanism":
            raise ValueError(
                f"非机制生成节点不可干预: {record.target} "
                f"(origin={node.origin})"
            )
        # 权限：值覆盖按时长映射 node/persistent，机制覆盖映射 mechanism
        required = (
            "mechanism"
            if record.rep == "mechanism"
            else ("persistent" if (record.duration or 1) > 1 else "node")
        )
        if required not in node.access.interventions:
            raise ValueError(
                f"{record.target} 不允许 {required} 神迹 "
                f"(权限={node.access.interventions})"
            )
        if record.rep == "value":
            if record.value is None:
                raise ValueError("值神迹必须提供替换值")
            registry.require_node_value(record.target, record.value)
        else:
            self._validate_mechanism(record, node)

    def _validate_node_instance(
        self, record: MiracleRecord, node: NodeSpec,
    ) -> None:
        """实例必须匹配节点实例域（global_singleton → ()，spatial_field → (cx, cy)）。

        防"登记成功但求值点永不命中"的静默无效神迹（fail-closed）。
        """
        kind = node.instance_domain.kind
        if kind == "global_singleton":
            if record.instance != ():
                raise ValueError(
                    f"{record.target} 为全局分量，实例必须为空元组: "
                    f"{record.instance}"
                )
            return
        if not isinstance(record.instance, (tuple, list)) or len(
            record.instance
        ) != len(node.instance_domain.axes):
            raise ValueError(
                f"{record.target} 实例必须匹配实例域 {kind} "
                f"axes={node.instance_domain.axes}: {record.instance}"
            )
        if not all(
            isinstance(axis, int) and not isinstance(axis, bool)
            for axis in record.instance
        ):
            raise ValueError(
                f"{record.target} 实例坐标必须为整数: {record.instance}"
            )

    def _validate_mechanism(
        self, record: MiracleRecord, node: NodeSpec
    ) -> None:
        registry = self._registry
        mechanism = record.mechanism
        if not isinstance(mechanism, MechanismSpec):
            raise ValueError("机制神迹必须提供 MechanismSpec")
        if mechanism.output != record.target:
            raise ValueError(
                f"替换机制输出 {mechanism.output} != 目标 {record.target}"
            )
        try:
            original = registry.mechanism_for(record.target)
        except KeyError:
            raise ValueError(f"目标节点无原机制: {record.target}") from None
        # 父集必须不越出原机制（替换规律不改读入分量集合）
        original_parents = {parent.parent for parent in original.parents}
        new_parents = {parent.parent for parent in mechanism.parents}
        extra = new_parents - original_parents
        if extra:
            raise ValueError(
                f"替换机制读入未声明的父分量: {sorted(extra)}"
            )
        # CRN 随机源契约：不得与无关机制声明的随机源重叠
        # （可复用原机制地址、可新增全新地址；shared_by 声明的共享除外）
        claimed = {binding.source for binding in mechanism.random_sources}
        others: set[str] = set()
        for other in registry.mechanisms.values():
            if other.mechanism_id == original.mechanism_id:
                continue
            for binding in other.random_sources:
                if binding.source not in claimed:
                    continue
                source_decl = registry.exogenous_sources.get(binding.source)
                shared = (
                    source_decl.shared_by
                    if source_decl is not None
                    else ()
                )
                if (
                    other.mechanism_id not in shared
                    and original.mechanism_id not in shared
                ):
                    others.add(binding.source)
        if others:
            raise ValueError(
                f"替换机制随机源与无关机制重叠: {sorted(others)}"
            )

    def _validate_parameter(self, record: MiracleRecord) -> None:
        registry = self._registry
        if record.target not in registry.parameters:
            raise ValueError(f"目标参数未声明: {record.target}")
        parameter = registry.parameters[record.target]
        if not parameter.intervention_allowed:
            raise ValueError(
                f"参数不允许神迹（环境变化）: {record.target}"
            )
        if record.duration is not None:
            raise ValueError(
                "参数神迹为环境变化，必须长期生效（duration=None）"
            )
        if record.value is None:
            raise ValueError("参数神迹必须提供替换值")
        registry.require_parameter_value(record.target, record.value)

    @staticmethod
    def _validate_feature(record: MiracleRecord) -> None:
        if record.rep != "value":
            raise ValueError("特征核控制神迹仅支持 value 替换规格")
        if not isinstance(record.target, str) or not record.target.strip():
            raise ValueError("特征核控制神迹必须指明特征类型")
        if not isinstance(record.instance, (tuple, list)) or len(record.instance) != 2:
            raise ValueError("特征核控制神迹必须指明 (cx, cy) 实例")


__all__ = [
    "MIRACLE_TARGET_SPACES",
    "MIRACLE_REP_KINDS",
    "MiracleRecord",
    "NodeResolution",
    "MiracleTable",
]