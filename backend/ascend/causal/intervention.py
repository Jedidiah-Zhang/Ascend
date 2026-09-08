"""干预执行器 — 干预记录、干预表、执行前校验与解析。

干预执行器是研究者对世界施加干预的工程入口：把一次干预（do）形式化为一条
不可变的干预记录（``InterventionRecord``），登记进干预表（``InterventionTable``），
在节点/参数求值点按固定规则替换生成。语义契约（第一阶段实施定义 §7、
工程符号体系 §5、世界基座 08）：

- **目标空间**：node（节点生成结果）、parameter（参数槽位，环境变化）、
  field_feature（统一天气场特征核控制，仅由 ``WeatherEngine.force_feature``
  写入，net 研究 API 转发到该入口）。
- **替换规格**：value（换常数）/ mechanism（换公式）；参数与特征核
  控制仅 value。
- **时长（两轴模型）**：single（1 帧，原"节点干预"）／window（>1 帧
  窗口，窗口结束自然演化）／forever（None，长期钉住直至被替换或清除）。
  合法组合由 ``_DURATION_RULES`` 一处声明；参数与特征核控制恒为
  forever（环境变化 / 特征核控制）。
- **优先级/重叠（固定规则，无人工数字）**：研究者原子序列执行，
  同一 (空间, 目标, 实例, 替换规格) 后到覆盖先到；同一节点同时存在
  值覆盖与机制覆盖时值覆盖优先（影响范围更小，§7 "数值优先于机制"）。
- **可达性（fail-closed）**：登记前校验目标是否在当前引擎的求值点上
  （``MechanismRegistry.wired_nodes`` / ``wired_parameters``）。已声明但
  未接线的分量会被**拒绝**，而不是登记成功却永不生效。
- **CRN 随机流契约**：值覆盖整段不消费原机制随机地址；机制覆盖的
  替换机制随机源不得与无关机制重叠（可新增全新地址、可复用原地址）。
- **执行前校验（§7 六条）**：目标存在且可达、值属声明值域、帧/时长有效、
  权限允许（``AccessPolicy.interventions`` / ``ParameterSpec.intervention_allowed``）、
  实例存在且匹配实例域、参数自动标记环境变化。读出/边界分量的保护由
  注册表声明期不变量保证（这类分量不得声明干预权限）。

本模块不依赖求值路径（见 :mod:`ascend.causal.intervention_engine` 的
``InterventionEvaluator`` / ``InterventionFrameExecutor``）。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Mapping

from .registry import MechanismRegistry
from .spec import MechanismSpec, NodeSpec, ParameterSpec

# 目标空间与替换规格的合法组合
INTERVENTION_TARGET_SPACES = frozenset({"node", "parameter", "field_feature"})
INTERVENTION_REP_KINDS = frozenset({"value", "mechanism"})
# 目标空间允许的替换规格
_REP_BY_SPACE = {
    "node": frozenset({"value", "mechanism"}),
    "parameter": frozenset({"value"}),
    "field_feature": frozenset({"value"}),
}

# ── 时长语义（两轴模型的第二轴）────────────────────────────
SINGLE = "single"      # 1 帧：只替换一次生成结果，断原入边
WINDOW = "window"      # >1 帧：窗口内逐帧替换，窗口结束自然演化
FOREVER = "forever"    # None：长期钉住直至被替换/清除
# (空间, 替换规格) → 允许的时长类别；一处声明，校验只查本表
_DURATION_RULES: dict[tuple[str, str], frozenset[str]] = {
    ("node", "value"): frozenset({SINGLE, WINDOW, FOREVER}),
    ("node", "mechanism"): frozenset({FOREVER, WINDOW}),
    ("parameter", "value"): frozenset({FOREVER}),
    ("field_feature", "value"): frozenset({FOREVER}),
}


def _duration_class(duration: int | None) -> str:
    """时长 → 语义类别；非法时长抛 ValueError（fail-closed）。"""
    if duration is None:
        return FOREVER
    if not isinstance(duration, int) or isinstance(duration, bool) or duration < 1:
        raise ValueError(
            f"时长必须为 None（长期）或正整数 tick: {duration!r}"
        )
    return SINGLE if duration == 1 else WINDOW


def default_duration(target_space: str, rep: str) -> int | None:
    """缺省时长（终端 do 与研究 API 共用一处）。

    值替换单帧（原"节点干预"），其余（机制/参数/特征核控制）长期。
    """
    return 1 if (target_space == "node" and rep == "value") else None


@dataclass(frozen=True, slots=True)
class InterventionRecord:
    """一条干预（干预记录）。

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
        applied_at: 登记时的世界 tick（``InterventionTable.commit`` 盖章，只读）。
        seq: 全局自增序号（``InterventionTable.commit`` 盖章，只读）。
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
    applied_at: int | None = field(default=None, init=False)
    seq: int | None = field(default=None, init=False)

    @property
    def environment_change(self) -> bool:
        """参数干预恒为环境变化（由目标空间推导，无独立字段可撒谎）。"""
        return self.target_space == "parameter"


@dataclass(frozen=True, slots=True)
class NodeResolution:
    """节点求值解析结果：命中的干预或空（保持原机制）。"""

    rep: str | None = None
    record: InterventionRecord | None = None
    value: object = None
    mechanism: object = None


class InterventionTable:
    """干预表 — 干预记录的单一事实源与解析器。

    构造绑定一个不可变注册表用于校验；表自身随登记/清除演化，
    但每条记录不可变，历史按登记顺序保留（供 P3 trace / P4 还原）。

    Parameters:
        registry: 不可变机制注册表（提供值域、权限、可达性声明）。
        now: 世界时钟读取函数 ``() -> tick``；登记时盖章 ``applied_at``。
            None = 不记录登记时刻（仅测试/无世界句柄场景）。
        instance_exists: 实例存在性查询 ``(节点 ID, 实例元组) -> bool``；
            由引擎注入（当前 = chunk 是否已注册）。None = 跳过存在性校验。
    """

    def __init__(
        self,
        registry: MechanismRegistry,
        *,
        now: Callable[[], int] | None = None,
        instance_exists: Callable[[str, tuple], bool] | None = None,
    ) -> None:
        self._registry = registry
        self._now = now
        self._instance_exists = instance_exists
        # 键 → 当前有效记录（同一键后到覆盖先到）
        self._values: dict[tuple[str, str, tuple], InterventionRecord] = {}
        self._mechanisms: dict[tuple[str, str, tuple], InterventionRecord] = {}
        self._parameters: dict[str, InterventionRecord] = {}
        self._features: dict[tuple[str, str, tuple], InterventionRecord] = {}
        self._history: list[InterventionRecord] = []
        self._seq: int = 0

    @property
    def registry(self) -> MechanismRegistry:
        """绑定的不可变注册表（供终端/研究 API 解析目标）。"""
        return self._registry

    def default_frame(self) -> int:
        """缺省生效帧 = 下一 tick（无时钟注入时为 0）。

        终端 do 与研究 API 共用，保证两个入口的缺省语义一致。
        """
        return self._now() + 1 if self._now is not None else 0

    # ── 登记与清除 ─────────────────────────────────────────

    def commit(self, record: InterventionRecord) -> InterventionRecord:
        """校验并登记一条干预；同一键的后登记替换先登记（原子序列）。

        Args:
            record: 待登记干预（``applied_at``/``seq`` 由本方法盖章）。

        Returns:
            登记后的不可变记录。

        Raises:
            ValueError: 任一执行前校验失败（fail-closed）。
        """
        raw_instance = record.instance
        if raw_instance is None:
            raw_instance = ()
        if not isinstance(raw_instance, (tuple, list)):
            raise ValueError(
                f"实例必须为元组或列表（None 视作空元组）: {raw_instance!r}"
            )
        normalized = InterventionRecord(
            target_space=record.target_space,
            target=record.target,
            instance=tuple(raw_instance),
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
        )
        self._validate(normalized)
        self._seq += 1
        object.__setattr__(
            normalized, "applied_at",
            self._now() if self._now is not None else None,
        )
        object.__setattr__(normalized, "seq", self._seq)
        if normalized.target_space == "node":
            if normalized.rep == "value":
                self._values[(normalized.target, normalized.instance)] = normalized
            else:
                self._mechanisms[
                    (normalized.target, normalized.instance)
                ] = normalized
        elif normalized.target_space == "parameter":
            self._parameters[normalized.target] = normalized
        else:
            self._features[
                (normalized.target, normalized.instance)
            ] = normalized
        self._history.append(normalized)
        return normalized

    def clear(
        self,
        target_space: str,
        target: str,
        instance: tuple = (),
        *,
        rep: str | None = None,
    ) -> tuple[str, ...]:
        """清除指定键的干预（撤销）。

        Args:
            target_space: "node" | "parameter" | "field_feature"。
            target: 节点 ID / 参数 ID / 特征类型名。
            instance: 实例坐标（全局分量用空元组）。
            rep: 仅清除指定替换规格；None = 该键的全部规格。

        Returns:
            实际清除的替换规格元组（空元组 = 未命中）。
        """
        key = (target, tuple(instance or ()))
        if target_space == "node":
            removed: list[str] = []
            if rep in (None, "value") and self._values.pop(key, None) is not None:
                removed.append("value")
            if (
                rep in (None, "mechanism")
                and self._mechanisms.pop(key, None) is not None
            ):
                removed.append("mechanism")
            return tuple(removed)
        if target_space == "parameter":
            if rep not in (None, "value"):
                return ()
            if self._parameters.pop(target, None) is None:
                return ()
            return ("value",)
        if rep not in (None, "value"):
            return ()
        if self._features.pop(key, None) is None:
            return ()
        return ("value",)

    # ── 解析 ───────────────────────────────────────────────

    def resolve_node(
        self,
        target: str,
        instance: tuple,
        frame: int,
    ) -> NodeResolution:
        """解析节点求值：值干预 > 机制干预 > 原机制（固定规则）。

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

    # ── 快照与历史 ─────────────────────────────────────────

    @property
    def history(self) -> tuple[InterventionRecord, ...]:
        """按登记顺序的全部干预记录（含被替换的历史，供 trace/还原）。"""
        return tuple(self._history)

    def history_plain(self) -> list[dict[str, object]]:
        """按登记顺序的完整历史（可序列化，含 seq/applied_at）。"""
        return [self.record_plain(record) for record in self._history]

    def snapshot(self) -> dict[str, object]:
        """当前有效记录的确定性快照（可序列化）。"""
        records = {
            "values": sorted(
                (self.record_plain(rec) for rec in self._values.values()),
                key=lambda item: (item["target"], item["instance"]),
            ),
            "mechanisms": sorted(
                (self.record_plain(rec) for rec in self._mechanisms.values()),
                key=lambda item: (item["target"], item["instance"]),
            ),
            "parameters": sorted(
                (self.record_plain(rec) for rec in self._parameters.values()),
                key=lambda item: item["target"],
            ),
            "features": sorted(
                (self.record_plain(rec) for rec in self._features.values()),
                key=lambda item: (item["target"], item["instance"]),
            ),
        }
        return records

    # ── 内部 ───────────────────────────────────────────────

    @staticmethod
    def _active(record: InterventionRecord, frame: int) -> bool:
        if frame < record.frame_t0:
            return False
        if record.duration is None:
            return True
        return frame < record.frame_t0 + record.duration

    @staticmethod
    def record_plain(record: InterventionRecord) -> dict[str, object]:
        """单条记录的可序列化视图（快照/历史/研究 API 共用）。"""
        return {
            "target_space": record.target_space,
            "target": record.target,
            "instance": list(record.instance),
            "rep": record.rep,
            "value": record.value,
            "mechanism": (
                record.mechanism.mechanism_id
                if record.mechanism is not None
                else None
            ),
            "frame_t0": record.frame_t0,
            "duration": record.duration,
            "version": record.version,
            "environment_change": record.environment_change,
            "applied_at": record.applied_at,
            "seq": record.seq,
        }

    # ── 执行前校验（§7 六条）───────────────────────────────

    def _validate(self, record: InterventionRecord) -> None:
        if record.target_space not in INTERVENTION_TARGET_SPACES:
            raise ValueError(f"非法目标空间: {record.target_space!r}")
        if record.rep not in INTERVENTION_REP_KINDS:
            raise ValueError(f"非法替换规格: {record.rep!r}")
        if record.rep not in _REP_BY_SPACE[record.target_space]:
            raise ValueError(
                f"目标空间 {record.target_space} 不允许替换规格 {record.rep!r}"
            )
        if (
            not isinstance(record.frame_t0, int)
            or isinstance(record.frame_t0, bool)
            or record.frame_t0 < 0
        ):
            raise ValueError(
                f"生效帧必须为非负整数 tick: {record.frame_t0!r}"
            )
        duration_class = _duration_class(record.duration)
        if duration_class not in _DURATION_RULES[
            (record.target_space, record.rep)
        ]:
            raise ValueError(
                f"{record.target_space}/{record.rep} 不支持时长类别 "
                f"{duration_class}（允许: "
                f"{sorted(_DURATION_RULES[(record.target_space, record.rep)])}）"
            )
        if not record.target:
            raise ValueError("干预目标不能为空")

        if record.target_space == "node":
            self._validate_node(record)
        elif record.target_space == "parameter":
            self._validate_parameter(record)
        else:
            self._validate_feature(record)

    def _validate_node(self, record: InterventionRecord) -> None:
        registry = self._registry
        if record.target not in registry.nodes:
            raise ValueError(f"目标分量未声明: {record.target}")
        node = registry.nodes[record.target]
        # 可达性：已声明但引擎未执行的生成点拒绝登记（防静默无效干预）
        if record.target not in registry.wired_nodes:
            raise ValueError(
                f"目标分量未接线（当前引擎不执行该生成点）: {record.target}"
            )
        self._validate_node_instance(record, node)
        # 权限：值覆盖单帧→node、窗口/长期→persistent、机制覆盖→mechanism
        required = _required_intervention(record)
        if required not in node.access.interventions:
            raise ValueError(
                f"{record.target} 不允许 {required} 干预 "
                f"(权限={node.access.interventions})"
            )
        if record.rep == "value":
            if record.value is None:
                raise ValueError("值干预必须提供替换值")
            registry.require_node_value(record.target, record.value)
        else:
            self._validate_mechanism(record, node)

    def _validate_node_instance(
        self, record: InterventionRecord, node: NodeSpec,
    ) -> None:
        """实例必须匹配节点实例域且真实存在（§7 目标分量实例存在）。

        实例域形状不符或实例不存在均拒绝，防"登记成功但求值点永不命中"。
        """
        kind = node.instance_domain.kind
        if kind == "global_singleton":
            if record.instance != ():
                raise ValueError(
                    f"{record.target} 为全局分量，实例必须为空元组: "
                    f"{record.instance}"
                )
            return
        if len(record.instance) != len(node.instance_domain.axes):
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
        if (
            self._instance_exists is not None
            and not self._instance_exists(record.target, record.instance)
        ):
            raise ValueError(
                f"目标实例不存在: {record.target} {record.instance}"
            )

    def _validate_mechanism(
        self, record: InterventionRecord, node: NodeSpec
    ) -> None:
        registry = self._registry
        mechanism = record.mechanism
        if not isinstance(mechanism, MechanismSpec):
            raise ValueError("机制干预必须提供 MechanismSpec")
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

    def _validate_parameter(self, record: InterventionRecord) -> None:
        registry = self._registry
        if record.target not in registry.parameters:
            raise ValueError(f"目标参数未声明: {record.target}")
        if record.target not in registry.wired_parameters:
            raise ValueError(
                f"目标参数未被已接线机制消费（环境变化不会生效）: "
                f"{record.target}"
            )
        parameter = registry.parameters[record.target]
        if not parameter.intervention_allowed:
            raise ValueError(
                f"参数不允许干预（环境变化）: {record.target}"
            )
        if record.value is None:
            raise ValueError("参数干预必须提供替换值")
        registry.require_parameter_value(record.target, record.value)

    @staticmethod
    def _validate_feature(record: InterventionRecord) -> None:
        if not isinstance(record.target, str) or not record.target.strip():
            raise ValueError("特征核控制干预必须指明特征类型")
        if len(record.instance) != 2 or not all(
            isinstance(axis, int) and not isinstance(axis, bool)
            for axis in record.instance
        ):
            raise ValueError("特征核控制干预必须指明整数 (cx, cy) 实例")


def _required_intervention(record: InterventionRecord) -> str:
    """干预所需的 ``AccessPolicy.interventions`` 条目（唯一映射处）。

    机制替换改的是规律 → mechanism；值替换单帧只替换一次生成结果 →
    node；值替换窗口/长期是"持续钉住" → persistent（影响范围更大）。
    """
    if record.rep == "mechanism":
        return "mechanism"
    return "node" if record.duration == 1 else "persistent"


__all__ = [
    "FOREVER",
    "INTERVENTION_REP_KINDS",
    "INTERVENTION_TARGET_SPACES",
    "SINGLE",
    "WINDOW",
    "InterventionRecord",
    "InterventionTable",
    "NodeResolution",
    "default_duration",
]
