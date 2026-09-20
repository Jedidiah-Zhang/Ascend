"""机制求值 — 模板求值（实例循环 / 整场内核）与父值解析。

支持四类载体：

- ``global``：单次调用；
- 定尺寸 ``lattice``（``size`` 给定）：逐实例模板求值，空间关系按偏移 +
  边界算子 + 聚合解析；
- 动态 ``lattice``（``size=None``，chunk 流式物化）：只对已物化实例求值，
  逐实例读写（``read_at`` / ``write_at``），外部输入按实例映射提供；
- ``field`` 作用域（``scope="field"``）：整场内核一次调用，父值为整场
  序列（定尺寸）或实例映射（动态）。

``accelerated`` 与参考实现同协议，可逐位替换（内核对由测试锁定）。
"""

from __future__ import annotations

import itertools
from typing import Mapping

from ascend.world.kernel import round_half_even_div
from ascend.world.meta.context import MechanismContext
from ascend.world.meta.declarations import MechanismDecl, Parent
from ascend.world.runtime.state import (
    DynamicField,
    LatticeField,
    StateStore,
)

__all__ = ["evaluate_mechanism"]


def evaluate_mechanism(
    program: object,
    store: StateStore,
    mechanism: MechanismDecl,
    *,
    root_seed: int,
    tick: int,
    inputs: Mapping[str, object],
    params: Mapping[str, object],
    skip: Mapping[str, frozenset[tuple[int, ...]]] | None = None,
    trace: list | None = None,
) -> dict[str, object]:
    """求值一个机制模板，返回 ``{槽位: 值}``（不落 store，由调用方暂存）。

    ``skip``：被逐实例干预替换的坐标（按槽位），对应实例不参与求值。
    ``trace``：给定列表时按实例追加 ``(机制, 实例, 父值, 结果)``（研究记录用）。
    """
    impl = mechanism.accelerated or mechanism.impl
    if mechanism.scope == "field":
        return _evaluate_field(
            program,
            store,
            mechanism,
            impl=impl,
            root_seed=root_seed,
            tick=tick,
            inputs=inputs,
            params=params,
            trace=trace,
        )
    slots = mechanism.outputs()
    instance = program.instances[program.slots[slots[0]].on]
    if instance.kind == "global":
        parent_values = _resolve_parents(
            program, store, mechanism, inputs=inputs, instance=None
        )
        context = MechanismContext(
            mechanism=mechanism,
            root_seed=root_seed,
            tick=tick,
            instance=(),
            parent_values=parent_values,
            params=params,
        )
        result = _collect(mechanism, impl(context))
        if trace is not None:
            trace.append((mechanism, (), parent_values, result))
        return result
    if instance.kind == "entity":
        return _evaluate_instances(
            program,
            store,
            mechanism,
            impl=impl,
            root_seed=root_seed,
            tick=tick,
            inputs=inputs,
            params=params,
            owner=instance,
            keys=tuple(
                (entity_id,) for entity_id in store.entities(instance.id)
            ),
            skip=skip or {},
            trace=trace,
        )
    if instance.kind == "lattice" and instance.size is None:
        return _evaluate_instances(
            program,
            store,
            mechanism,
            impl=impl,
            root_seed=root_seed,
            tick=tick,
            inputs=inputs,
            params=params,
            owner=instance,
            keys=store.materialized(instance.id),
            skip=skip or {},
            trace=trace,
        )
    if instance.kind == "lattice":
        # 场初值取当前值（影子优先）：被干预实例跳过求值、保持干预值
        fields = {}
        for slot in slots:
            current = store.read(slot, 0)
            fields[slot] = (
                current.copy()
                if isinstance(current, LatticeField)
                else LatticeField(instance.size, 0)
            )
        for coords in fields[slots[0]].coordinates():
            if all(coords in skip.get(slot, frozenset()) for slot in slots):
                continue
            parent_values = _resolve_parents(
                program, store, mechanism, inputs=inputs,
                instance=coords, owner=instance,
            )
            context = MechanismContext(
                mechanism=mechanism,
                root_seed=root_seed,
                tick=tick,
                instance=coords,
                parent_values=parent_values,
                params=params,
            )
            collected = _collect(mechanism, impl(context))
            if trace is not None:
                trace.append((mechanism, coords, parent_values, collected))
            for slot, value in collected.items():
                fields[slot].set(coords, value)
        return dict(fields)
    raise NotImplementedError(
        f"机制 {mechanism.id}: 实例类型 {instance.kind} 的运行时支持尚未交付"
    )


def _evaluate_instances(
    program: object,
    store: StateStore,
    mechanism: MechanismDecl,
    *,
    impl: object,
    root_seed: int,
    tick: int,
    inputs: Mapping[str, object],
    params: Mapping[str, object],
    owner: object,
    keys: tuple[tuple[object, ...], ...],
    skip: Mapping[str, frozenset[tuple[int, ...]]],
    trace: list | None = None,
) -> dict[str, object]:
    """逐实例求值（动态 lattice 的已物化集合 / 实体的存活集合）。

    被干预实例（``skip``）跳过；``owner`` 是输出槽位的实例类型声明
    （level/link 关系解析方向用）。
    """
    slots = mechanism.outputs()
    fields = {slot: DynamicField() for slot in slots}
    for coords in keys:
        if all(coords in skip.get(slot, frozenset()) for slot in slots):
            continue
        parent_values = _resolve_parents(
            program, store, mechanism, inputs=inputs,
            instance=coords, owner=owner,
        )
        context = MechanismContext(
            mechanism=mechanism,
            root_seed=root_seed,
            tick=tick,
            instance=coords,
            parent_values=parent_values,
            params=params,
        )
        collected = _collect(mechanism, impl(context))
        if trace is not None:
            trace.append((mechanism, coords, parent_values, collected))
        for slot, value in collected.items():
            if coords in skip.get(slot, frozenset()):
                continue
            fields[slot].set(coords, value)
    return dict(fields)


def _evaluate_field(
    program: object,
    store: StateStore,
    mechanism: MechanismDecl,
    *,
    impl: object,
    root_seed: int,
    tick: int,
    inputs: Mapping[str, object],
    params: Mapping[str, object],
    trace: list | None = None,
) -> dict[str, object]:
    """整场内核求值：一次调用，父值为整场序列（或 global 广播标量）。

    定尺寸 lattice：一次求值覆盖全场；动态 lattice：逐实例求值，实例值
    本身可以是场（chunk 内 tile 场）。
    """
    slots = mechanism.outputs()
    output_slot = program.slots[slots[0]]
    instance = program.instances[output_slot.on]
    if instance.kind != "lattice":
        raise NotImplementedError(
            f"机制 {mechanism.id}: field 作用域要求 lattice 输出"
        )
    if instance.size is None:
        return _evaluate_field_dynamic(
            program,
            store,
            mechanism,
            impl=impl,
            root_seed=root_seed,
            tick=tick,
            inputs=inputs,
            params=params,
            instance_id=instance.id,
            trace=trace,
        )
    parent_values: dict[str, object] = {}
    for parent in mechanism.parents:
        slot = program.slots[parent.slot]
        parent_kind = program.instances[slot.on].kind
        if slot.persist == "external":
            parent_values[parent.argument] = _external(inputs, parent.slot)
        elif parent_kind == "global":
            parent_values[parent.argument] = store.read(
                parent.slot, parent.lag
            )
        else:
            value = store.read(parent.slot, parent.lag)
            parent_values[parent.argument] = (
                value.values() if isinstance(value, LatticeField) else value
            )
    context = MechanismContext(
        mechanism=mechanism,
        root_seed=root_seed,
        tick=tick,
        instance=(),
        parent_values=parent_values,
        params=params,
    )
    result = impl(context)  # type: ignore[operator]
    if not isinstance(result, Mapping):
        raise TypeError(
            f"机制 {mechanism.id} 为 field 作用域，必须返回槽位映射"
        )
    if trace is not None:
        trace.append((mechanism, (), parent_values, result))
    fields: dict[str, object] = {}
    for slot in slots:
        field = LatticeField(instance.size, 0)
        for coords, value in zip(field.coordinates(), result[slot]):
            field.set(coords, value)
        fields[slot] = field
    return fields


def _evaluate_field_dynamic(
    program: object,
    store: StateStore,
    mechanism: MechanismDecl,
    *,
    impl: object,
    root_seed: int,
    tick: int,
    inputs: Mapping[str, object],
    params: Mapping[str, object],
    instance_id: str,
    trace: list | None = None,
) -> dict[str, object]:
    """动态 lattice 的整场内核：逐实例求值，实例值可为场。"""
    slots = mechanism.outputs()
    fields = {slot: DynamicField() for slot in slots}
    for coords in store.materialized(instance_id):
        parent_values: dict[str, object] = {}
        for parent in mechanism.parents:
            slot = program.slots[parent.slot]
            parent_kind = program.instances[slot.on].kind
            if slot.persist == "external":
                parent_values[parent.argument] = _read_external(
                    inputs, parent.slot, parent_kind, coords
                )
            elif parent_kind == "global":
                parent_values[parent.argument] = store.read(
                    parent.slot, parent.lag
                )
            else:
                parent_values[parent.argument] = store.read_at(
                    parent.slot, coords, parent.lag
                )
        context = MechanismContext(
            mechanism=mechanism,
            root_seed=root_seed,
            tick=tick,
            instance=coords,
            parent_values=parent_values,
            params=params,
        )
        result = impl(context)  # type: ignore[operator]
        if not isinstance(result, Mapping):
            raise TypeError(
                f"机制 {mechanism.id} 为 field 作用域，必须返回槽位映射"
            )
        if trace is not None:
            trace.append((mechanism, coords, parent_values, result))
        for slot in slots:
            fields[slot].set(
                coords, _wrap_field(result[slot], parent_values)
            )
    return dict(fields)


def _wrap_field(values: object, parent_values: Mapping[str, object]) -> LatticeField:
    """内核返回序列 → 场（形状取自同实例场父值；无则按长度一维）。"""
    if isinstance(values, LatticeField):
        return values
    data = list(values)  # type: ignore[arg-type]
    shape: tuple[int, ...] | None = None
    for value in parent_values.values():
        if isinstance(value, LatticeField):
            shape = value.size
            break
    if shape is None:
        shape = (len(data),)
    total = 1
    for extent in shape:
        total *= extent
    if total != len(data):
        shape = (len(data),)
    return LatticeField.from_values(data, shape)


def evaluate_direct(
    program: object,
    mechanism_id: str,
    inputs: Mapping[str, object],
    *,
    tick: int = 0,
    params: Mapping[str, object] | None = None,
) -> object:
    """按机制 ID 用父值直接求值（研究/领域适配器用；无状态、无 store）。

    输入按父槽位 ID 给出；参数缺省取程序解析值。输入按声明值域做边界
    校验（fail-closed；越界即 ``ValueError``），与旧注册表求值语义一致。
    """
    mechanism = program.mechanisms.get(mechanism_id)
    if mechanism is None:
        for candidate in program.mechanisms.values():
            if mechanism_id in candidate.outputs():
                mechanism = candidate
                break
    if mechanism is None:
        raise KeyError(f"未声明的机制/输出: {mechanism_id}")
    argument_of = {parent.slot: parent.argument for parent in mechanism.parents}
    parent_values = {
        argument_of[slot]: value for slot, value in inputs.items()
    }
    _validate_inputs(program, mechanism, parent_values)
    context = MechanismContext(
        mechanism=mechanism,
        root_seed=program.seed,
        tick=tick,
        parent_values=parent_values,
        params=dict(program.parameters if params is None else params),
    )
    return mechanism.impl(context)


def _validate_inputs(
    program: object,
    mechanism: MechanismDecl,
    parent_values: Mapping[str, object],
) -> None:
    """父值边界校验（仅数值；越界即拒绝，与旧注册表 fail-closed 一致）。"""
    for parent in mechanism.parents:
        if parent.argument not in parent_values:
            continue
        value = parent_values[parent.argument]
        if not isinstance(value, (int, float)) or isinstance(value, bool):
            continue
        domain = program.slots[parent.slot].domain
        if domain.minimum is not None and value < domain.minimum:
            raise ValueError(
                f"{mechanism.id}/{parent.argument}: {value!r} 低于下界 "
                f"{domain.minimum!r}"
            )
        if domain.maximum is not None and value > domain.maximum:
            raise ValueError(
                f"{mechanism.id}/{parent.argument}: {value!r} 高于上界 "
                f"{domain.maximum!r}"
            )


# ── 父值解析 ─────────────────────────────────────────────────────


def _resolve_parents(
    program: object,
    store: StateStore,
    mechanism: MechanismDecl,
    *,
    inputs: Mapping[str, object],
    instance: tuple[int, ...] | None,
    owner: object | None = None,
) -> dict[str, object]:
    values: dict[str, object] = {}
    for parent in mechanism.parents:
        slot = program.slots[parent.slot]
        parent_kind = program.instances[slot.on].kind
        if slot.persist == "external":
            values[parent.argument] = _read_external(
                inputs, parent.slot, parent_kind, instance
            )
        elif instance is None or parent_kind == "global":
            values[parent.argument] = store.read(parent.slot, parent.lag)
        else:
            values[parent.argument] = _neighbor_value(
                program, store, parent, instance, owner
            )
    return values


def _read_external(
    inputs: Mapping[str, object],
    slot_id: str,
    parent_kind: str,
    instance: tuple[int, ...] | None,
) -> object:
    value = _external(inputs, slot_id)
    if parent_kind == "global" or instance is None:
        return value
    if isinstance(value, LatticeField):
        return value.get(instance)
    if isinstance(value, Mapping):
        key = tuple(instance)
        if key in value:
            return value[key]  # type: ignore[index]
        if len(key) == 1 and key[0] in value:
            return value[key[0]]  # type: ignore[index]
        raise KeyError(
            f"外部槽位 {slot_id} 缺少实例 {instance!r} 输入"
        )
    raise TypeError(
        f"外部 lattice 槽位 {slot_id} 的输入必须是 LatticeField 或坐标映射"
    )


def _external(inputs: Mapping[str, object], slot_id: str) -> object:
    try:
        return inputs[slot_id]
    except KeyError:
        raise KeyError(
            f"外部槽位 {slot_id} 本帧未提供输入"
        ) from None


def _instance_key(key: object, relation: object) -> tuple[object, ...]:
    """链接键 → 目标实例坐标（str/int 实体 ID 视作一元组）。"""
    if isinstance(key, tuple):
        return key
    if isinstance(key, str) and key:
        return (key,)
    if isinstance(key, int) and not isinstance(key, bool):
        return (key,)
    raise TypeError(
        f"关系 {relation.id} 的链接键必须是 str/int/坐标元组: {key!r}"
    )


def _level_value(
    program: object,
    store: StateStore,
    parent: Parent,
    relation: object,
    coords: tuple[object, ...],
    owner: object | None,
) -> object:
    """层级关系取值：restrict（子读父）或 prolong（父读子后聚合）。"""
    child = program.instances[relation.source]
    ratio = child.ratio
    if owner is not None and owner.id == relation.source:
        target = tuple(int(coord) // ratio for coord in coords)
        return store.read_at(parent.slot, target, parent.lag)
    values: list[object] = []
    for offset in itertools.product(range(ratio), repeat=len(coords)):
        child_coords = tuple(
            int(coord) * ratio + step
            for coord, step in zip(coords, offset)
        )
        values.append(store.read_at(parent.slot, child_coords, parent.lag))
    return _aggregate(values, parent.aggregation, relation.id)


def _neighbor_value(
    program: object,
    store: StateStore,
    parent: Parent,
    coords: tuple[object, ...],
    owner: object | None = None,
) -> object:
    if parent.relation == "same":
        return store.read_at(parent.slot, coords, parent.lag)
    relation = program.relations[parent.relation]
    if relation.kind == "link":
        key = store.read_at(relation.key_slot, coords, 0)
        if key == "" or key is None:
            # 链接未指派：按父槽位声明的缺失值处理（未声明即拒绝）
            missing = program.slots[parent.slot].domain.missing
            if missing is None:
                raise ValueError(
                    f"关系 {relation.id}: 链接键未指派，且父槽位 "
                    f"{parent.slot} 未声明缺失值"
                )
            return missing
        return store.read_at(
            parent.slot, _instance_key(key, relation), parent.lag,
        )
    if relation.kind == "level":
        return _level_value(
            program, store, parent, relation, coords, owner,
        )
    field = store.read(parent.slot, parent.lag)
    collected: list[object] = []
    for offset in relation.offsets:
        neighbor = tuple(c + d for c, d in zip(coords, offset))
        if not _in_bounds(neighbor, field.size):
            if relation.boundary == "reject":
                raise ValueError(
                    f"关系 {relation.id} 越界: {neighbor!r} 不在 {field.size!r}"
                )
            if relation.boundary == "clamp":
                neighbor = tuple(
                    max(0, min(extent - 1, coord))
                    for coord, extent in zip(neighbor, field.size)
                )
            elif relation.boundary == "wrap":
                neighbor = tuple(
                    coord % extent
                    for coord, extent in zip(neighbor, field.size)
                )
            else:  # identity：越界邻居不参与
                continue
        collected.append(field.get(neighbor))
    if not collected:
        raise ValueError(
            f"关系 {relation.id} 在 {coords!r} 处没有可用邻居"
        )
    return _aggregate(collected, parent.aggregation, relation.id)


def _in_bounds(coords: tuple[int, ...], size: tuple[int, ...]) -> bool:
    return all(0 <= coord < extent for coord, extent in zip(coords, size))


def _aggregate(
    values: list[object],
    aggregation: str,
    relation_id: str,
) -> object:
    if aggregation == "identity":
        if len(values) != 1:
            raise ValueError(
                f"关系 {relation_id} 的 identity 聚合要求单偏移"
            )
        return values[0]
    if aggregation == "sum":
        return sum(values)  # type: ignore[arg-type]
    if aggregation == "mean":
        total = sum(values)  # type: ignore[arg-type]
        if all(isinstance(value, int) for value in values):
            return round_half_even_div(total, len(values))
        return total / len(values)
    if aggregation == "min":
        return min(values)  # type: ignore[type-var]
    if aggregation == "max":
        return max(values)  # type: ignore[type-var]
    raise ValueError(f"未知聚合: {aggregation!r}")


def _collect(
    mechanism: MechanismDecl,
    result: object,
) -> dict[str, object]:
    slots = mechanism.outputs()
    if len(slots) == 1:
        return {slots[0]: result}
    if not isinstance(result, Mapping):
        raise TypeError(
            f"机制 {mechanism.id} 声明多输出，实现必须返回槽位映射"
        )
    return {slot: result[slot] for slot in slots}
