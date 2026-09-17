"""干预时间线 — 计划（外部输入 I_t）与已发生记录（只追加）。

《世界契约》WC-6 / 设计决定 D2：
- 干预以逐帧独立记录进入时间线，只追加；同一帧、同一目标后到覆盖先到；
- 持续干预 = 窗口内逐帧记录；撤销 = 后续帧不再记录，既有记录不改写；
- 干预属于外部输入，不是 W_t；历史求值只用记录，不看"当前表"；
- 运行内机制替换不属于世界内干预（WC-1.3，结构变更 = 换世界）。

实现分层：
- :class:`InterventionPlan` 语义由 :class:`InterventionTimeline` 内联承载
  （``plan`` / ``revoke``）；计划条目含 ``[start_frame, stop_frame)`` 窗口；
- 求值点（``resolve_node`` / ``resolve_parameter``）惰性物化：该帧生效且
  尚无记录时补记一条，保证"已求值帧的记录"只追加、撤销不改写历史。
"""

from __future__ import annotations

import threading
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field

from .registry import MechanismRegistry
from .spec import NodeSpec

# 目标空间（值干预）
INTERVENTION_TARGET_SPACES = frozenset({"node", "parameter", "field_feature"})

# 时长语义类别（由窗口推导，仅用于校验与文案）
SINGLE = "single"      # 1 帧
WINDOW = "window"      # >1 帧
FOREVER = "forever"    # 长期
# 空间 → 允许的时长类别
_DURATION_RULES: dict[str, frozenset[str]] = {
    "node": frozenset({SINGLE, WINDOW, FOREVER}),
    "parameter": frozenset({FOREVER}),
    "field_feature": frozenset({FOREVER}),
}


def _duration_class(duration: int | None) -> str:
    """时长（帧数）→ 语义类别；非法时长抛 ValueError（fail-closed）。"""
    if duration is None:
        return FOREVER
    if not isinstance(duration, int) or isinstance(duration, bool) or duration < 1:
        raise ValueError(
            f"时长必须为 None（长期）或正整数 tick: {duration!r}"
        )
    return SINGLE if duration == 1 else WINDOW


def default_duration(target_space: str) -> int | None:
    """缺省时长（终端 do 与研究 API 共用一处）。

    节点值替换缺省单帧（原"节点干预"），参数/特征核控制缺省长期。
    """
    return 1 if target_space == "node" else None


@dataclass(frozen=True, slots=True)
class PlannedIntervention:
    """一条计划条目：目标在 ``[start_frame, stop_frame)`` 内逐帧替换。

    Attributes:
        target_space: "node" | "parameter" | "field_feature"。
        target: 节点 ID / 参数 ID / 特征类型名。
        instance: 实例坐标元组；全局分量用空元组 ()。
        value: 替换值。
        start_frame: 生效起始帧（含）。
        stop_frame: 失效帧（不含）；None = 长期，直至撤销。
        source: 来源标识（terminal / research / feature 等）。
        version: 登记方提供的版本号（溯源用）。
        seq: 计划登记序号（Timeline 盖章，只读）。
        submitted_at: 登记时的世界 tick（Timeline 盖章，只读）。
    """

    target_space: str
    target: str
    instance: tuple = ()
    value: object = None
    start_frame: int = 0
    stop_frame: int | None = None
    source: str = ""
    version: str = ""
    seq: int | None = field(default=None, init=False)
    submitted_at: int | None = field(default=None, init=False)

    def active_at(self, frame: int) -> bool:
        """该帧是否处于计划窗口内（逐帧独立判定）。"""
        if frame < self.start_frame:
            return False
        return self.stop_frame is None or frame < self.stop_frame

    def duration_class(self) -> str:
        """窗口对应的时长类别。"""
        if self.stop_frame is None:
            return FOREVER
        return _duration_class(self.stop_frame - self.start_frame)

    def plain(self) -> dict[str, object]:
        """可序列化视图（计划载荷）。"""
        return {
            "target_space": self.target_space,
            "target": self.target,
            "instance": list(self.instance),
            "value": self.value,
            "start_frame": self.start_frame,
            "stop_frame": self.stop_frame,
            "source": self.source,
            "version": self.version,
            "seq": self.seq,
            "submitted_at": self.submitted_at,
        }


@dataclass(frozen=True, slots=True)
class InterventionRecord:
    """一条已发生记录：某帧实际生效的值替换（时间线单位，只追加）。

    Attributes:
        frame: 记录绑定帧（求值点物化）。
        target_space / target / instance: 同计划条目。
        value: 实际替换值。
        source / version: 来源与版本（溯源）。
        plan_seq: 命中的计划条目序号（可回溯撤销历史）。
        applied_at: 物化时的世界 tick（只读）。
        seq: 记录追加序号（只读）。
    """

    frame: int
    target_space: str
    target: str
    instance: tuple
    value: object
    source: str
    version: str
    plan_seq: int | None
    applied_at: int | None = field(default=None, init=False)
    seq: int | None = field(default=None, init=False)

    def plain(self) -> dict[str, object]:
        """可序列化视图（记录载荷）。"""
        return {
            "frame": self.frame,
            "target_space": self.target_space,
            "target": self.target,
            "instance": list(self.instance),
            "value": self.value,
            "source": self.source,
            "version": self.version,
            "plan_seq": self.plan_seq,
            "applied_at": self.applied_at,
            "seq": self.seq,
        }


@dataclass(frozen=True, slots=True)
class NodeResolution:
    """节点求值解析结果：命中的记录或空（保持原机制）。"""

    rep: str | None = None
    record: InterventionRecord | None = None
    value: object = None


class InterventionTimeline:
    """干预时间线 — 计划登记/撤销 + 逐帧记录（只追加）+ 求值解析。

    线程安全：登记/撤销/物化/解析/持久化共用一把可重入锁。

    Parameters:
        registry: 不可变机制注册表（提供值域、权限、接线声明）。
        now: 世界时钟读取函数 ``() -> tick``；登记时盖章 ``submitted_at``。
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
        self._plan: list[PlannedIntervention] = []
        self._plan_seq: int = 0
        self._records: dict[tuple, dict[int, InterventionRecord]] = {}
        self._record_list: list[InterventionRecord] = []
        self._record_seq: int = 0
        self._lock = threading.RLock()

    @property
    def registry(self) -> MechanismRegistry:
        """绑定的不可变注册表（供终端/研究 API 解析目标）。"""
        return self._registry

    def default_frame(self) -> int:
        """缺省生效帧 = 下一 tick（无时钟注入时为 0）。"""
        return self._now() + 1 if self._now is not None else 0

    def current_frame(self) -> int:
        """当前世界帧（无时钟注入时为 0）。"""
        return self._now() if self._now is not None else 0

    # ── 计划：登记与撤销 ───────────────────────────────────

    def plan(self, entry: PlannedIntervention) -> PlannedIntervention:
        """校验并登记一条计划条目（同一键的后续条目按 seq 后到优先）。

        Raises:
            ValueError: 任一执行前校验失败（fail-closed）。
        """
        normalized = self._normalize(entry)
        self._validate(normalized)
        with self._lock:
            self._plan_seq += 1
            object.__setattr__(normalized, "seq", self._plan_seq)
            object.__setattr__(
                normalized, "submitted_at",
                self._now() if self._now is not None else None,
            )
            self._plan.append(normalized)
        return normalized

    def revoke(
        self,
        target_space: str,
        target: str,
        instance: tuple = (),
        *,
        at_frame: int | None = None,
    ) -> int:
        """撤销：把该键所有长期条目在 ``at_frame`` 处结束（后续帧不再记录）。

        撤销立即生效：缺省 ``at_frame`` = 当前帧，即当前帧起不再物化；
        既有记录不改写，已求值帧的结果不变（WC-6.2）。

        Returns:
            实际结束的计划条目数（0 = 未命中）。
        """
        key = (target_space, target, tuple(instance or ()))
        stop = at_frame if at_frame is not None else self.current_frame()
        if not isinstance(stop, int) or isinstance(stop, bool) or stop < 0:
            raise ValueError(f"撤销帧必须为非负整数 tick: {stop!r}")
        stopped = 0
        with self._lock:
            for index, entry in enumerate(self._plan):
                if (entry.target_space, entry.target, entry.instance) != key:
                    continue
                if entry.stop_frame is not None and entry.stop_frame <= stop:
                    continue
                new_stop = max(stop, entry.start_frame)
                if entry.stop_frame == new_stop:
                    continue
                self._plan[index] = self._replace_stop(entry, new_stop)
                stopped += 1
        return stopped

    def plan_entries(self) -> tuple[PlannedIntervention, ...]:
        """全部计划条目（按登记顺序）。"""
        with self._lock:
            return tuple(self._plan)

    def active_plans(self, frame: int) -> tuple[PlannedIntervention, ...]:
        """该帧生效的计划投影：同键取 seq 最大者，按 (空间, 目标, 实例) 排序。"""
        with self._lock:
            plans = list(self._plan)
        by_key: dict[tuple, PlannedIntervention] = {}
        for entry in plans:
            if not entry.active_at(frame):
                continue
            key = (entry.target_space, entry.target, entry.instance)
            current = by_key.get(key)
            if current is None or (entry.seq or 0) > (current.seq or 0):
                by_key[key] = entry
        return tuple(sorted(
            by_key.values(),
            key=lambda item: (item.target_space, item.target, item.instance),
        ))

    # ── 求值解析（求值点惰性物化）──────────────────────────

    def resolve_node(
        self,
        target: str,
        instance: tuple,
        frame: int,
    ) -> NodeResolution:
        """解析节点求值：命中记录即值替换，否则保持原机制。"""
        record = self._materialize("node", target, instance, frame)
        if record is None:
            return NodeResolution()
        return NodeResolution(rep="value", record=record, value=record.value)

    def resolve_parameter(
        self,
        parameter_id: str,
        frame: int,
    ) -> tuple[bool, object]:
        """解析参数槽位覆盖；返回 (是否命中, 替换值)。"""
        record = self._materialize("parameter", parameter_id, (), frame)
        if record is None:
            return False, None
        return True, record.value

    def _materialize(
        self,
        target_space: str,
        target: str,
        instance: tuple,
        frame: int,
    ) -> InterventionRecord | None:
        """该帧生效且尚无记录时补记一条（幂等；后到优先由 seq 决定）。"""
        key = (target_space, target, tuple(instance or ()))
        existing = self._records.get(key, {}).get(frame)
        if existing is not None:
            return existing
        with self._lock:
            existing = self._records.get(key, {}).get(frame)
            if existing is not None:
                return existing
            candidates = [
                entry for entry in self._plan
                if entry.target_space == target_space
                and entry.target == target
                and entry.instance == key[2]
                and entry.active_at(frame)
            ]
            if not candidates:
                return None
            entry = max(candidates, key=lambda item: item.seq or 0)
            record = InterventionRecord(
                frame=frame,
                target_space=target_space,
                target=target,
                instance=key[2],
                value=entry.value,
                source=entry.source,
                version=entry.version,
                plan_seq=entry.seq,
            )
            self._record_seq += 1
            object.__setattr__(record, "seq", self._record_seq)
            object.__setattr__(
                record, "applied_at",
                self._now() if self._now is not None else None,
            )
            self._records.setdefault(key, {})[frame] = record
            self._record_list.append(record)
            return record

    # ── 记录与快照 ─────────────────────────────────────────

    def records(self) -> tuple[InterventionRecord, ...]:
        """全部已发生记录（按时序）。"""
        with self._lock:
            return tuple(self._record_list)

    def record_at(
        self,
        target_space: str,
        target: str,
        instance: tuple,
        frame: int,
    ) -> InterventionRecord | None:
        """查询指定 (键, 帧) 的已发生记录。"""
        key = (target_space, target, tuple(instance or ()))
        with self._lock:
            return self._records.get(key, {}).get(frame)

    def history_plain(self) -> list[dict[str, object]]:
        """全部已发生记录的可序列化视图（按规范排序）。"""
        with self._lock:
            records = list(self._record_list)
        return [record.plain() for record in self._canonical_records(records)]

    def snapshot(self, frame: int) -> list[dict[str, object]]:
        """当前生效计划投影（可序列化；``do list`` 用）。"""
        return [entry.plain() for entry in self.active_plans(frame)]

    # ── 持久化（计划 = 实验上下文；记录 = 已发生前缀）────────

    def persist(self) -> dict[str, object]:
        """确定性载荷：``{"plan": [...], "records": [...]}``。

        - 计划按登记顺序（seq）保留，撤销已固化为 ``stop_frame``；
        - 记录按 ``(frame, 空间, 目标, 实例, seq)`` 规范排序，跨运行逐位一致。
        """
        with self._lock:
            plan = sorted(
                self._plan,
                key=lambda item: item.seq if item.seq is not None else -1,
            )
            records = self._canonical_records(list(self._record_list))
        return {
            "plan": [entry.plain() for entry in plan],
            "records": [record.plain() for record in records],
        }

    def restore(
        self,
        payload: object,
        *,
        instance_loader: Callable[[str, tuple], bool] | None = None,
    ) -> int:
        """从存档载荷恢复（读档路径，fail-closed，全量校验后落表）。

        两阶段：先对全部计划/记录做完整校验（含重复记录检测），任一
        条目非法即抛出；校验全部通过后才统一落表（此后不再失败）——
        失败不会在时间线里留下半成品。

        Args:
            payload: :meth:`persist` 输出的映射。
            instance_loader: 可选实例装载器；仅本调用期间生效。

        Returns:
            实际恢复的条目数（计划 + 记录）。

        Raises:
            ValueError: 载荷结构或任一条目校验失败（整体拒绝）。
        """
        if not isinstance(payload, Mapping):
            raise ValueError(f"干预载荷必须为映射: {type(payload).__name__}")
        extra = sorted(set(payload) - {"plan", "records"})
        if extra:
            raise ValueError(f"干预载荷含未知字段: {extra}")
        plan_items = payload.get("plan", [])
        record_items = payload.get("records", [])
        if not isinstance(plan_items, (list, tuple)):
            raise ValueError("干预载荷 plan 必须为列表")
        if not isinstance(record_items, (list, tuple)):
            raise ValueError("干预载荷 records 必须为列表")
        plans = [self._plan_from_plain(item) for item in plan_items]
        records = [self._record_from_plain(item) for item in record_items]

        previous = self._instance_exists
        if instance_loader is not None:
            def _with_loader(node_id: str, instance: tuple) -> bool:
                if previous is not None and previous(node_id, instance):
                    return True
                if instance_loader(node_id, instance):
                    return True
                return previous(node_id, instance) if previous else False
            self._instance_exists = _with_loader
        try:
            with self._lock:
                # 阶段一：全量校验（不改动时间线）
                keys: set[tuple] = set()
                for record in records:
                    key = (record.target_space, record.target, record.instance)
                    if (key, record.frame) in keys:
                        raise ValueError(
                            f"重复记录: {record.target_space}/{record.target} "
                            f"@{record.frame}"
                        )
                    keys.add((key, record.frame))
                for entry in plans:
                    self._validate(entry, allow_empty=True)
                for record in records:
                    self._validate_record(record)
                # 阶段二：统一落表（此后不再有失败点）
                for entry in plans:
                    self._plan_seq = max(self._plan_seq, entry.seq or 0)
                    self._plan.append(entry)
                for record in records:
                    self._record_seq = max(self._record_seq, record.seq or 0)
                    key = (
                        record.target_space, record.target, record.instance,
                    )
                    self._records.setdefault(key, {})[record.frame] = record
                    self._record_list.append(record)
        finally:
            self._instance_exists = previous
        return len(plans) + len(records)

    # ── 内部：规范化与序列化 ───────────────────────────────

    @staticmethod
    def _replace_stop(
        entry: PlannedIntervention, stop_frame: int,
    ) -> PlannedIntervention:
        replacement = PlannedIntervention(
            target_space=entry.target_space,
            target=entry.target,
            instance=entry.instance,
            value=entry.value,
            start_frame=entry.start_frame,
            stop_frame=stop_frame,
            source=entry.source,
            version=entry.version,
        )
        object.__setattr__(replacement, "seq", entry.seq)
        object.__setattr__(replacement, "submitted_at", entry.submitted_at)
        return replacement

    @staticmethod
    def _normalize(entry: PlannedIntervention) -> PlannedIntervention:
        raw_instance = entry.instance
        if raw_instance is None:
            raw_instance = ()
        if not isinstance(raw_instance, (tuple, list)):
            raise ValueError(
                f"实例必须为元组或列表（None 视作空元组）: {raw_instance!r}"
            )
        return PlannedIntervention(
            target_space=entry.target_space,
            target=entry.target,
            instance=tuple(raw_instance),
            value=(
                dict(entry.value)
                if isinstance(entry.value, dict)
                else entry.value
            ),
            start_frame=entry.start_frame,
            stop_frame=entry.stop_frame,
            source=entry.source,
            version=entry.version,
        )

    @staticmethod
    def _canonical_records(
        records: Sequence[InterventionRecord],
    ) -> list[InterventionRecord]:
        return sorted(
            records,
            key=lambda item: (
                item.frame,
                item.target_space,
                item.target,
                item.instance,
                item.seq if item.seq is not None else -1,
            ),
        )

    @staticmethod
    def _plan_from_plain(item: object) -> PlannedIntervention:
        if not isinstance(item, Mapping):
            raise ValueError(f"计划条目必须为映射: {item!r}")
        known = {
            "target_space", "target", "instance", "value", "start_frame",
            "stop_frame", "source", "version", "seq", "submitted_at",
        }
        extra = sorted(set(item) - known)
        if extra:
            raise ValueError(f"计划条目含未知字段: {extra}")
        missing = sorted(known - set(item))
        if missing:
            raise ValueError(f"计划条目缺少字段: {missing}")
        raw_instance = item["instance"]
        if not isinstance(raw_instance, (list, tuple)):
            raise ValueError(f"计划实例必须为列表或元组: {raw_instance!r}")
        seq = item["seq"]
        if not isinstance(seq, int) or isinstance(seq, bool) or seq < 1:
            raise ValueError(f"计划 seq 必须为正整数: {seq!r}")
        submitted_at = item["submitted_at"]
        if submitted_at is not None and (
            not isinstance(submitted_at, int)
            or isinstance(submitted_at, bool)
            or submitted_at < 0
        ):
            raise ValueError(
                f"计划 submitted_at 必须为 None 或非负整数 tick: {submitted_at!r}"
            )
        value = item["value"]
        entry = PlannedIntervention(
            target_space=item["target_space"],
            target=item["target"],
            instance=tuple(raw_instance),
            value=dict(value) if isinstance(value, dict) else value,
            start_frame=item["start_frame"],
            stop_frame=item["stop_frame"],
            source=item["source"],
            version=item["version"],
        )
        object.__setattr__(entry, "seq", seq)
        object.__setattr__(entry, "submitted_at", submitted_at)
        return entry

    @staticmethod
    def _record_from_plain(item: object) -> InterventionRecord:
        if not isinstance(item, Mapping):
            raise ValueError(f"记录条目必须为映射: {item!r}")
        known = {
            "frame", "target_space", "target", "instance", "value",
            "source", "version", "plan_seq", "applied_at", "seq",
        }
        extra = sorted(set(item) - known)
        if extra:
            raise ValueError(f"记录条目含未知字段: {extra}")
        missing = sorted(known - set(item))
        if missing:
            raise ValueError(f"记录条目缺少字段: {missing}")
        raw_instance = item["instance"]
        if not isinstance(raw_instance, (list, tuple)):
            raise ValueError(f"记录实例必须为列表或元组: {raw_instance!r}")
        for label in ("frame", "seq"):
            value = item[label]
            if not isinstance(value, int) or isinstance(value, bool) or value < 0:
                raise ValueError(f"记录 {label} 必须为非负整数: {value!r}")
        applied_at = item["applied_at"]
        if applied_at is not None and (
            not isinstance(applied_at, int)
            or isinstance(applied_at, bool)
            or applied_at < 0
        ):
            raise ValueError(
                f"记录 applied_at 必须为 None 或非负整数: {applied_at!r}"
            )
        if item["seq"] < 1:
            raise ValueError(f"记录 seq 必须为正整数: {item['seq']!r}")
        plan_seq = item["plan_seq"]
        if plan_seq is not None and (
            not isinstance(plan_seq, int)
            or isinstance(plan_seq, bool)
            or plan_seq < 1
        ):
            raise ValueError(f"记录 plan_seq 必须为 None 或正整数: {plan_seq!r}")
        value = item["value"]
        record = InterventionRecord(
            frame=item["frame"],
            target_space=item["target_space"],
            target=item["target"],
            instance=tuple(raw_instance),
            value=dict(value) if isinstance(value, dict) else value,
            source=item["source"],
            version=item["version"],
            plan_seq=plan_seq,
        )
        object.__setattr__(record, "applied_at", item["applied_at"])
        object.__setattr__(record, "seq", item["seq"])
        return record

    # ── 内部：执行前校验 ───────────────────────────────────

    def _validate(
        self,
        entry: PlannedIntervention,
        *,
        allow_empty: bool = False,
    ) -> None:
        if entry.target_space not in INTERVENTION_TARGET_SPACES:
            raise ValueError(f"非法目标空间: {entry.target_space!r}")
        for label, value in (
            ("start_frame", entry.start_frame),
            ("stop_frame", entry.stop_frame),
        ):
            if value is None:
                continue
            if not isinstance(value, int) or isinstance(value, bool) or value < 0:
                raise ValueError(f"{label} 必须为非负整数 tick: {value!r}")
        if entry.stop_frame is not None:
            if entry.stop_frame < entry.start_frame or (
                entry.stop_frame == entry.start_frame and not allow_empty
            ):
                raise ValueError(
                    f"窗口为空: [{entry.start_frame}, {entry.stop_frame})"
                )
        duration_class = (
            entry.duration_class() if entry.stop_frame is not None else FOREVER
        )
        if duration_class not in _DURATION_RULES[entry.target_space]:
            raise ValueError(
                f"{entry.target_space} 不支持时长类别 {duration_class}"
                f"（允许: {sorted(_DURATION_RULES[entry.target_space])}）"
            )
        if not isinstance(entry.target, str) or not entry.target:
            raise ValueError("干预目标不能为空")
        if not isinstance(entry.source, str):
            raise ValueError(f"来源标识必须为字符串: {entry.source!r}")
        if not isinstance(entry.version, str):
            raise ValueError(f"干预版本号必须为字符串: {entry.version!r}")
        if entry.target_space == "node":
            self._validate_node(entry)
        elif entry.target_space == "parameter":
            self._validate_parameter(entry)
        else:
            self._validate_feature(entry)

    def _validate_node(self, entry: PlannedIntervention) -> None:
        registry = self._registry
        if entry.target not in registry.nodes:
            raise ValueError(f"目标分量未声明: {entry.target}")
        node = registry.nodes[entry.target]
        if entry.target not in registry.wired_nodes:
            raise ValueError(
                f"目标分量未接线（当前引擎不执行该生成点）: {entry.target}"
            )
        self._validate_instance(entry.target, entry.instance, node)
        required = (
            "node" if entry.duration_class() == SINGLE else "persistent"
        )
        if required not in node.access.interventions:
            raise ValueError(
                f"{entry.target} 不允许 {required} 干预 "
                f"(权限={node.access.interventions})"
            )
        if entry.value is None:
            raise ValueError("值干预必须提供替换值")
        registry.require_node_value(entry.target, entry.value)

    def _validate_instance(
        self, target: str, instance: tuple, node: NodeSpec,
    ) -> None:
        kind = node.instance_domain.kind
        if kind == "global_singleton":
            if instance != ():
                raise ValueError(
                    f"{target} 为全局分量，实例必须为空元组: {instance}"
                )
            return
        if len(instance) != len(node.instance_domain.axes):
            raise ValueError(
                f"{target} 实例必须匹配实例域 {kind} "
                f"axes={node.instance_domain.axes}: {instance}"
            )
        if not all(
            isinstance(axis, int) and not isinstance(axis, bool)
            for axis in instance
        ):
            raise ValueError(f"{target} 实例坐标必须为整数: {instance}")
        if (
            self._instance_exists is not None
            and not self._instance_exists(target, instance)
        ):
            raise ValueError(f"目标实例不存在: {target} {instance}")

    def _validate_parameter(self, entry: PlannedIntervention) -> None:
        registry = self._registry
        if entry.target not in registry.parameters:
            raise ValueError(f"目标参数未声明: {entry.target}")
        if entry.target not in registry.wired_parameters:
            raise ValueError(
                f"目标参数未被已接线机制消费（环境变化不会生效）: "
                f"{entry.target}"
            )
        parameter = registry.parameters[entry.target]
        if not parameter.intervention_allowed:
            raise ValueError(f"参数不允许干预（环境变化）: {entry.target}")
        if entry.value is None:
            raise ValueError("参数干预必须提供替换值")
        registry.require_parameter_value(entry.target, entry.value)

    def _validate_feature(self, entry: PlannedIntervention) -> None:
        if not isinstance(entry.target, str) or not entry.target.strip():
            raise ValueError("特征核控制干预必须指明特征类型")
        if len(entry.instance) != 2 or not all(
            isinstance(axis, int) and not isinstance(axis, bool)
            for axis in entry.instance
        ):
            raise ValueError("特征核控制干预必须指明整数 (cx, cy) 实例")

    def _validate_record(self, record: InterventionRecord) -> None:
        """恢复记录时校验目标与值域（存档不是绕过校验的后门）。"""
        entry = PlannedIntervention(
            target_space=record.target_space,
            target=record.target,
            instance=record.instance,
            value=record.value,
            start_frame=record.frame,
            stop_frame=None,
            source=record.source,
            version=record.version,
        )
        if record.target_space == "node":
            self._validate_node(entry)
        elif record.target_space == "parameter":
            self._validate_parameter(entry)
        else:
            self._validate_feature(entry)


__all__ = [
    "FOREVER",
    "INTERVENTION_TARGET_SPACES",
    "SINGLE",
    "WINDOW",
    "InterventionRecord",
    "InterventionTimeline",
    "NodeResolution",
    "PlannedIntervention",
    "default_duration",
]
