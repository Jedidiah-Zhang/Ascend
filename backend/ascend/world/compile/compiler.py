"""编译器 — 声明到不可变程序（世界架构 00 §3）。

流水线：模块合并 → 声明索引 → 参数/旋钮解析 → 静态校验 → 更新点计划 →
身份摘要。编译只依赖声明，不依赖运行实例；同一声明两次编译身份逐位一致。

静态校验（fail-closed，问题一次报全）：

1. 模块自洽（id 唯一、输出槽位已声明）与依赖闭合（``depends_on`` 全在装配内）；
2. 跨模块 id 唯一；状态/派生槽位的写者存在且双向一致（单写者）；
3. 父引用：槽位存在、关系存在、自引用必须 ``lag≥1``、同帧依赖必须由更早
   更新组提供（parameter/external 不参与排序）；
4. 时间：``when.key`` 属于阶段/周期/事件；
5. 随机：地址 ``(namespace, purpose)`` 全局唯一；
6. 证据：每个机制通过见证覆盖检查与见证重跑（C1）。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping

from ascend.world.evidence.witnesses import (
    run_witnesses,
    witness_coverage_issues,
)
from ascend.world.kernel import digest_object
from ascend.world.meta.declarations import (
    InstanceDecl,
    InvariantDecl,
    MechanismDecl,
    ModulePack,
    ParameterDecl,
    RelationDecl,
    Schedule,
    SlotDecl,
    Witness,
    WorldSpec,
)
from ascend.world.meta.validate import (
    kernel_digest,
    module_digest,
    validate_module,
)

__all__ = ["CompileError", "UpdateGroup", "WorldProgram", "compile_world"]


class CompileError(ValueError):
    """编译失败：携带全部问题（不静默取第一个）。"""

    def __init__(self, issues: tuple[str, ...]) -> None:
        super().__init__("编译失败: " + "; ".join(issues))
        self.issues = tuple(issues)


@dataclass(frozen=True, slots=True)
class UpdateGroup:
    """更新点：同一时间模式/键下一起求值的机制组。"""

    id: str
    order: int
    mode: str
    key: str
    mechanisms: tuple[MechanismDecl, ...]


@dataclass(frozen=True, slots=True)
class WorldProgram:
    """编译产物：不可变、可复现、携带身份。"""

    contract: str
    seed: int
    modules: tuple[ModulePack, ...]
    instances: Mapping[str, InstanceDecl]
    relations: Mapping[str, RelationDecl]
    slots: Mapping[str, SlotDecl]
    mechanisms: Mapping[str, MechanismDecl]
    invariants: tuple[InvariantDecl, ...]
    parameters: Mapping[str, object]
    knobs: Mapping[str, object]
    schedule: Schedule
    update_plan: tuple[UpdateGroup, ...]
    max_lag: int
    identity: str
    module_digests: Mapping[str, str]
    kernel: str

    def writer_of(self, slot_id: str) -> str | None:
        """槽位写者（parameter/external 返回 ``None``）。"""
        return self.slots[slot_id].writer

    def due_groups(
        self,
        tick: int,
        events: tuple[str, ...] = (),
    ) -> tuple[UpdateGroup, ...]:
        """本帧到点的更新组（计划序）。

        阶段组每帧执行；周期组在 ``tick % ticks == 0`` 的帧执行；事件组在
        其 key 出现在 ``events`` 时执行。
        """
        due: list[UpdateGroup] = []
        for group in self.update_plan:
            if group.mode == "phase":
                due.append(group)
            elif group.mode == "period":
                ticks = self.schedule.period_ticks(group.key)
                if ticks is not None and tick % ticks == 0:
                    due.append(group)
            elif group.key in events:
                due.append(group)
        return tuple(due)

    def world_identity(self, seed: int | None = None) -> str:
        """世界身份 = 程序身份 + 种子（WC-1.1）。"""
        return digest_object(
            {"program": self.identity, "seed": self.seed if seed is None else seed}
        )

    def observation_protocol_version(self) -> str:
        """观测协议版本 = 全部槽位观测协议集合的摘要（存档比对用）。"""
        return digest_object({
            slot.id: list(slot.observation_protocols)
            for slot in sorted(self.slots.values(), key=lambda item: item.id)
            if slot.observation_protocols
        })

    def settings(self) -> dict[str, object]:
        """世界设置视图（manifest 记录与读档比对；``identity`` 为准）。

        各分量摘要只作诊断定位：模块摘要、内核摘要、契约版本。
        """
        return {
            "identity": self.identity,
            "contract": self.contract,
            "kernel": self.kernel,
            "module_digests": dict(self.module_digests),
        }

    def declaration_settings(self) -> dict[str, str]:
        """声明视图（manifest 比对：声明 ID + 摘要 + 观测协议版本）。

        声明视图刻意不含槽位/机制明细——摘要已经覆盖全部声明内容，
        存档层不需要认识世界内部结构（``save/settings.py``）。
        """
        return {
            "declaration_id": self.contract,
            "declaration_hash": self.identity,
            "observation_protocol_version": self.observation_protocol_version(),
        }


def compile_world(spec: WorldSpec) -> WorldProgram:
    """把世界装配编译为不可变程序；任何静态校验失败即拒绝。"""
    issues: list[str] = []
    for pack in spec.modules:
        issues.extend(validate_module(pack))
    _check_module_graph(spec.modules, issues)

    instances = _merge(spec.modules, "instances", "实例", issues)
    relations = _merge(spec.modules, "relations", "关系", issues)
    slots = _merge(spec.modules, "slots", "槽位", issues)
    mechanisms = _merge(spec.modules, "mechanisms", "机制", issues)
    invariants = tuple(
        item for pack in spec.modules for item in pack.invariants
    )
    _check_invariants(invariants, slots, issues)

    parameters = _resolve_parameters(
        _merge(spec.modules, "parameters", "参数", issues),
        spec.parameters,
        issues,
    )
    knobs = _resolve_knobs(
        _merge(spec.modules, "knobs", "旋钮", issues),
        spec.knobs,
        issues,
    )

    mechanisms_in_order: list[MechanismDecl] = []
    seen_mechanisms: set[str] = set()
    for pack in spec.modules:
        for item in pack.mechanisms:
            if item.id in seen_mechanisms:
                continue
            seen_mechanisms.add(item.id)
            mechanisms_in_order.append(item)
    plan = _build_plan(spec.schedule, tuple(mechanisms_in_order), issues)

    _check_instances(instances, relations, slots, issues)
    _check_slots(slots, mechanisms, parameters, issues)
    _check_mechanisms(
        tuple(mechanisms_in_order), slots, relations, instances, plan,
        parameters, issues,
    )

    if issues:
        raise CompileError(tuple(issues))

    max_lag = max(
        (parent.lag for item in mechanisms_in_order for parent in item.parents),
        default=0,
    )
    digests = {pack.id: module_digest(pack) for pack in spec.modules}
    kernel = kernel_digest()
    identity = digest_object(
        {
            "contract": spec.contract,
            "modules": [digests[pack.id] for pack in spec.modules],
            "parameters": {key: parameters[key] for key in sorted(parameters)},
            "knobs": {key: knobs[key] for key in sorted(knobs)},
            "schedule": {
                "phases": list(spec.schedule.phases),
                "periods": [list(item) for item in spec.schedule.periods],
                "tick_unit": spec.schedule.tick_unit,
            },
            "kernel": kernel,
        }
    )
    return WorldProgram(
        contract=spec.contract,
        seed=spec.seed,
        modules=spec.modules,
        instances=instances,
        relations=relations,
        slots=slots,
        mechanisms=mechanisms,
        invariants=invariants,
        parameters=parameters,
        knobs=knobs,
        schedule=spec.schedule,
        update_plan=plan,
        max_lag=max_lag,
        identity=identity,
        module_digests=digests,
        kernel=kernel,
    )


# ── 合并与解析 ───────────────────────────────────────────────────


def _merge(
    packs: tuple[ModulePack, ...],
    attribute: str,
    label: str,
    issues: list[str],
) -> dict[str, object]:
    """合并同类声明：**相同声明幂等**（共享原语跨模块复用），不同即冲突。"""
    merged: dict[str, object] = {}
    for pack in packs:
        for item in getattr(pack, attribute):
            existing = merged.get(item.id)
            if existing is not None:
                if existing != item:
                    issues.append(
                        f"{label} id 冲突（声明不同）: {item.id}（{pack.id}）"
                    )
                continue
            merged[item.id] = item
    return merged


def _check_module_graph(
    packs: tuple[ModulePack, ...],
    issues: list[str],
) -> None:
    present = {pack.id for pack in packs}
    if len(present) != len(packs):
        for pack in packs:
            if sum(1 for other in packs if other.id == pack.id) > 1:
                issues.append(f"模块 id 重复: {pack.id}")
                break
    for pack in packs:
        for dependency in pack.depends_on:
            if dependency not in present:
                issues.append(
                    f"模块 {pack.id} 依赖缺失: {dependency}"
                )


def _resolve_parameters(
    declared: Mapping[str, ParameterDecl],
    values: Mapping[str, object],
    issues: list[str],
) -> dict[str, object]:
    resolved: dict[str, object] = {}
    for parameter_id, declaration in declared.items():
        if parameter_id in values:
            value = values[parameter_id]
        elif declaration.default is not None:
            value = declaration.default
        else:
            issues.append(f"参数 {parameter_id} 缺装配值且无默认值")
            continue
        if declaration.minimum is not None and value < declaration.minimum:
            issues.append(
                f"参数 {parameter_id}={value!r} 低于下界 {declaration.minimum!r}"
            )
            continue
        if declaration.maximum is not None and value > declaration.maximum:
            issues.append(
                f"参数 {parameter_id}={value!r} 高于上界 {declaration.maximum!r}"
            )
            continue
        resolved[parameter_id] = value
    for parameter_id in values:
        if parameter_id not in declared:
            issues.append(f"装配值 {parameter_id} 未在任何模块声明")
    return resolved


def _resolve_knobs(
    declared: Mapping[str, object],
    values: Mapping[str, object],
    issues: list[str],
) -> dict[str, object]:
    resolved: dict[str, object] = {}
    for knob_id, declaration in declared.items():
        if knob_id in values:
            value = values[knob_id]
        else:
            value = declaration.default
        if value is None:
            issues.append(f"旋钮 {knob_id} 缺装配值且无默认值")
            continue
        if value not in declaration.values:
            issues.append(
                f"旋钮 {knob_id}={value!r} 不在候选值 {declaration.values!r}"
            )
            continue
        resolved[knob_id] = value
    for knob_id in values:
        if knob_id not in declared:
            issues.append(f"装配值 {knob_id} 未在任何模块声明")
    return resolved


# ── 静态校验 ─────────────────────────────────────────────────────


def _check_instances(
    instances: Mapping[str, InstanceDecl],
    relations: Mapping[str, RelationDecl],
    slots: Mapping[str, SlotDecl],
    issues: list[str],
) -> None:
    for instance in instances.values():
        if instance.parent is not None:
            parent = instances.get(instance.parent)
            if parent is None:
                issues.append(
                    f"实例 {instance.id} 的层级父不存在: {instance.parent}"
                )
            elif parent.kind != "lattice":
                issues.append(
                    f"实例 {instance.id} 的层级父必须是 lattice: {parent.id}"
                )
            elif (
                instance.size is not None
                and parent.size is not None
                and (
                    len(instance.size) != len(parent.size)
                    or any(
                        child != coarse * instance.ratio
                        for child, coarse in zip(instance.size, parent.size)
                    )
                )
            ):
                issues.append(
                    f"实例 {instance.id} 尺寸与层级倍率不符: "
                    f"{instance.size} != {parent.size} × {instance.ratio}"
                )
    for relation in relations.values():
        for endpoint in (relation.source, relation.target):
            if endpoint and endpoint not in instances:
                issues.append(
                    f"关系 {relation.id} 端点未声明: {endpoint}"
                )
        if relation.kind == "level":
            child = instances.get(relation.source)
            parent = instances.get(relation.target)
            if (
                child is not None
                and parent is not None
                and child.parent != relation.target
            ):
                issues.append(
                    f"关系 {relation.id}: 子实例 {child.id} 的层级父必须是 "
                    f"{relation.target}（实际 {child.parent}）"
                )
        elif relation.kind == "link":
            key_slot = slots.get(relation.key_slot)
            if key_slot is None:
                issues.append(
                    f"关系 {relation.id}: 链接键槽位未声明 "
                    f"{relation.key_slot}"
                )
            else:
                if key_slot.on != relation.source:
                    issues.append(
                        f"关系 {relation.id}: 链接键槽位 {relation.key_slot} "
                        f"不在源实例 {relation.source} 上"
                    )
                if key_slot.persist not in ("state", "derived"):
                    issues.append(
                        f"关系 {relation.id}: 链接键槽位必须是 state/derived"
                    )


def _check_invariants(
    invariants: tuple[InvariantDecl, ...],
    slots: Mapping[str, SlotDecl],
    issues: list[str],
) -> None:
    seen: set[str] = set()
    for invariant in invariants:
        if invariant.id in seen:
            issues.append(f"不变量 id 重复: {invariant.id}")
        seen.add(invariant.id)
        for slot_id in invariant.slots:
            slot = slots.get(slot_id)
            if slot is None:
                issues.append(
                    f"不变量 {invariant.id} 槽位未声明: {slot_id}"
                )
            elif slot.persist not in ("state", "derived"):
                issues.append(
                    f"不变量 {invariant.id} 只能作用于 state/derived 槽位: "
                    f"{slot_id}（{slot.persist}）"
            )


def _check_slots(
    slots: Mapping[str, SlotDecl],
    mechanisms: Mapping[str, MechanismDecl],
    parameters: Mapping[str, object],
    issues: list[str],
) -> None:
    for slot in slots.values():
        if slot.persist in ("state", "derived"):
            writer = mechanisms.get(slot.writer) if slot.writer else None
            if writer is None:
                issues.append(
                    f"槽位 {slot.id} 的写者机制不存在: {slot.writer}"
                )
            elif slot.id not in writer.outputs():
                issues.append(
                    f"槽位 {slot.id} 写者 {writer.id} 未声明该输出"
                )
        elif slot.persist == "parameter" and slot.id not in parameters:
            issues.append(
                f"参数槽位 {slot.id} 缺少同名参数声明"
            )


def _check_parent_relation(
    mechanism: MechanismDecl,
    parent: Parent,
    output_slot: SlotDecl | None,
    parent_slot: SlotDecl,
    relation: RelationDecl | None,
    issues: list[str],
) -> None:
    """父引用关系校验：same / spatial / level / link 的方向与聚合规则。"""
    if parent.relation == "same":
        if parent.aggregation != "identity":
            issues.append(f"机制 {mechanism.id}: same 关系不得聚合")
        return
    if relation is None:
        return  # 未声明已在上层报告
    if relation.kind == "spatial":
        if relation.source != parent_slot.on:
            issues.append(
                f"机制 {mechanism.id}: 关系 {relation.id} 源 "
                f"{relation.source} 与槽位载体 {parent_slot.on} 不符"
            )
        return
    owner = output_slot.on if output_slot is not None else None
    if relation.kind == "level":
        # 方向：输出实例 = 父实例（prolong：读子实例聚合）
        #       输出实例 = 子实例（restrict：读父实例对应坐标）
        if owner == relation.target and parent_slot.on == relation.source:
            if parent.aggregation == "identity":
                issues.append(
                    f"机制 {mechanism.id}: prolong（父读子）必须声明聚合 "
                    f"（sum/mean/min/max）"
                )
        elif owner == relation.source and parent_slot.on == relation.target:
            if parent.aggregation != "identity":
                issues.append(
                    f"机制 {mechanism.id}: restrict（子读父）不得聚合"
                )
        else:
            issues.append(
                f"机制 {mechanism.id}: level 关系 {relation.id} 的端点与"
                f"输出实例/父槽位不符（{owner} / {parent_slot.on}）"
            )
        return
    if relation.kind == "link":
        if owner != relation.source:
            issues.append(
                f"机制 {mechanism.id}: link 关系的持有方必须是输出实例 "
                f"{relation.source}（实际 {owner}）"
            )
        if parent_slot.on != relation.target:
            issues.append(
                f"机制 {mechanism.id}: link 关系的父槽位必须挂在目标实例 "
                f"{relation.target}（实际 {parent_slot.on}）"
            )
        if parent.aggregation != "identity":
            issues.append(f"机制 {mechanism.id}: link 关系不得聚合")


def _check_mechanisms(
    mechanisms: tuple[MechanismDecl, ...],
    slots: Mapping[str, SlotDecl],
    relations: Mapping[str, RelationDecl],
    instances: Mapping[str, InstanceDecl],
    plan: tuple[UpdateGroup, ...],
    parameters: Mapping[str, object],
    issues: list[str],
) -> None:
    outputs: set[str] = set()
    addresses: set[tuple[str, str]] = set()
    rank_of = {
        mechanism.id: group.order
        for group in plan
        for mechanism in group.mechanisms
    }
    for mechanism in mechanisms:
        for slot_id in mechanism.outputs():
            if slot_id in outputs:
                issues.append(f"输出槽位双写者: {slot_id}")
            outputs.add(slot_id)
            slot = slots.get(slot_id)
            if slot is not None and slot.writer != mechanism.id:
                issues.append(
                    f"槽位 {slot_id} 声明写者 {slot.writer}，"
                    f"但机制 {mechanism.id} 声明输出"
                )
        if mechanism.address is not None:
            key = (mechanism.address.namespace, mechanism.address.purpose)
            if key in addresses:
                issues.append(
                    f"地址重复声明: {key[0]}/{key[1]}"
                )
            addresses.add(key)

        for parameter_id in mechanism.params:
            if parameter_id not in parameters:
                issues.append(
                    f"机制 {mechanism.id}: 参数未声明 {parameter_id}"
                )

        arguments = [parent.argument for parent in mechanism.parents]
        if len(set(arguments)) != len(arguments):
            issues.append(f"机制 {mechanism.id}: 父引用 argument 重复")

        output_slot = slots.get(mechanism.outputs()[0])
        if mechanism.scope == "field":
            _check_field_scope(
                mechanism, slots, instances, output_slot, issues,
            )
        for parent in mechanism.parents:
            slot = slots.get(parent.slot)
            if slot is None:
                issues.append(
                    f"机制 {mechanism.id}: 父槽位未声明 {parent.slot}"
                )
                continue
            relation = (
                None if parent.relation == "same"
                else relations.get(parent.relation)
            )
            if parent.relation != "same" and relation is None:
                issues.append(
                    f"机制 {mechanism.id}: 父引用关系未声明 "
                    f"{parent.relation}"
                )
            cross_ok = (
                relation is not None and relation.kind in ("level", "link")
            )
            if (
                output_slot is not None
                and slot.on != output_slot.on
                and not cross_ok
            ):
                parent_instance = instances.get(slot.on)
                if not (
                    parent_instance is not None
                    and parent_instance.kind == "global"
                ):
                    issues.append(
                        f"机制 {mechanism.id}: 跨实例类型父引用仅支持 "
                        f"global 广播或 level/link 关系"
                        f"（{slot.on} → {output_slot.on}）"
                    )
            _check_parent_relation(
                mechanism, parent, output_slot, slot, relation, issues,
            )
            if parent.slot in mechanism.outputs() and parent.lag == 0:
                issues.append(
                    f"机制 {mechanism.id}: 自引用必须 lag≥1"
                )
            writer = slot.writer
            if writer is None or writer == mechanism.id:
                continue
            if parent.lag == 0 and rank_of.get(writer, -1) >= rank_of.get(
                mechanism.id, -1
            ):
                issues.append(
                    f"机制 {mechanism.id}: 同帧读取 {parent.slot}"
                    f"（写者 {writer}）必须更早执行"
                )

        issues.extend(witness_coverage_issues(mechanism))
        issues.extend(run_witnesses(mechanism))
        _check_modulus(mechanism, slots, issues)


def _witness_pairs(
    mechanism: MechanismDecl,
    argument: str,
) -> list[tuple[Witness, Witness]]:
    """只变指定 argument 的见证对（与覆盖检查同语义）。"""
    pairs: list[tuple[Witness, Witness]] = []
    for first in mechanism.witnesses:
        for second in mechanism.witnesses:
            if first is second:
                continue
            if first.inputs.get(argument) == second.inputs.get(argument):
                continue
            keys = set(first.inputs) | set(second.inputs)
            if all(
                key == argument
                or first.inputs.get(key) == second.inputs.get(key)
                for key in keys
            ):
                pairs.append((first, second))
    return pairs


def _check_modulus(
    mechanism: MechanismDecl,
    slots: Mapping[str, SlotDecl],
    issues: list[str],
) -> None:
    """模数一致性（G7 语义）：线性边见证差商 ≤ L；跳变边跳幅 ≤ jump_bound。"""
    output_slot = slots.get(mechanism.outputs()[0])
    output_kind = output_slot.domain.kind if output_slot else "any"
    for parent in mechanism.parents:
        pairs = _witness_pairs(mechanism, parent.argument)
        if parent.modulus_kind == "jump":
            bound = parent.jump_bound
            for first, second in pairs:
                values = (first.outputs[0], second.outputs[0])
                if not all(
                    isinstance(value, (int, float))
                    and not isinstance(value, bool)
                    for value in values
                ):
                    continue
                amplitude = abs(values[1] - values[0])
                if bound is not None and amplitude > bound * (1 + 1e-6):
                    issues.append(
                        f"机制 {mechanism.id}←{parent.slot}: "
                        f"jump_bound={bound} < 见证跳幅={amplitude:.6g}"
                    )
            continue
        if parent.metric == "discrete" or output_kind in (
            "enum", "bool", "string",
        ):
            continue
        declared = parent.lipschitz
        if declared is None:
            continue  # 未认证：由研究投影 fail-closed 拒绝
        for first, second in pairs:
            original = first.inputs.get(parent.argument)
            alternate = second.inputs.get(parent.argument)
            values = (first.outputs[0], second.outputs[0])
            if not all(
                isinstance(value, (int, float))
                and not isinstance(value, bool)
                for value in (original, alternate, *values)
            ):
                continue
            delta = abs(float(alternate) - float(original))
            if delta == 0.0:
                continue
            ratio = abs(float(values[1]) - float(values[0])) / delta
            if ratio > declared * (1 + 1e-6) + 1e-9:
                issues.append(
                    f"机制 {mechanism.id}←{parent.slot}: "
                    f"L={declared} < 见证差商={ratio:.6g}"
                )


def _check_field_scope(
    mechanism: MechanismDecl,
    slots: Mapping[str, SlotDecl],
    instances: Mapping[str, InstanceDecl],
    output_slot: SlotDecl | None,
    issues: list[str],
) -> None:
    """field 作用域：整场内核（lattice 输出；父引用由内核自管偏移）。"""
    if output_slot is None:
        return
    instance = instances.get(output_slot.on)
    if instance is None or instance.kind != "lattice":
        issues.append(
            f"机制 {mechanism.id}: field 作用域要求 lattice 输出"
        )
        return
    for slot_id in mechanism.outputs():
        slot = slots.get(slot_id)
        if slot is not None and slot.on != output_slot.on:
            issues.append(
                f"机制 {mechanism.id}: field 作用域多输出必须同实例"
            )
    for parent in mechanism.parents:
        if parent.relation != "same" or parent.aggregation != "identity":
            issues.append(
                f"机制 {mechanism.id}: field 作用域父引用不支持空间偏移"
                f"（内核自管）: {parent.argument}"
            )


def _build_plan(
    schedule: Schedule,
    mechanisms: tuple[MechanismDecl, ...],
    issues: list[str],
) -> tuple[UpdateGroup, ...]:
    plan: list[UpdateGroup] = []
    for phase in schedule.phases:
        group = tuple(
            item
            for item in mechanisms
            if item.when.mode == "phase" and item.when.key == phase
        )
        if group:
            plan.append(
                UpdateGroup(f"phase:{phase}", len(plan), "phase", phase, group)
            )
    for name, _ in schedule.periods:
        group = tuple(
            item
            for item in mechanisms
            if item.when.mode == "period" and item.when.key == name
        )
        if group:
            plan.append(
                UpdateGroup(f"period:{name}", len(plan), "period", name, group)
            )
    event_keys: list[str] = []
    for item in mechanisms:
        if item.when.mode == "event" and item.when.key not in event_keys:
            event_keys.append(item.when.key)
    for key in event_keys:
        group = tuple(
            item
            for item in mechanisms
            if item.when.mode == "event" and item.when.key == key
        )
        plan.append(
            UpdateGroup(f"event:{key}", len(plan), "event", key, group)
        )
    for item in mechanisms:
        if item.when.mode == "phase" and item.when.key not in schedule.phases:
            issues.append(
                f"机制 {item.id}: 未声明的阶段 {item.when.key}"
            )
        if (
            item.when.mode == "period"
            and schedule.period_ticks(item.when.key) is None
        ):
            issues.append(
                f"机制 {item.id}: 未声明的周期 {item.when.key}"
            )
    return tuple(plan)
