"""机制求值 — 模板求值（通用实例循环）与父值解析。

P0 支持 ``global`` 与固定尺寸 ``lattice`` 实例；跨实例类型归约（全局归约、
多分辨率下采样）与加速内核绑定在 P1 交付（遇到即显式拒绝，不静默降级）。

父值解析：``same`` 关系取同实例；空间关系按偏移集合 + 边界算子取邻居，
再按聚合规则合成。``lag`` 语义见 :class:`~ascend.world.runtime.state.StateStore`。
"""

from __future__ import annotations

from typing import Mapping

from ascend.world.kernel import round_half_even_div
from ascend.world.meta.context import MechanismContext
from ascend.world.meta.declarations import MechanismDecl, Parent
from ascend.world.runtime.state import LatticeField, StateStore

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
) -> dict[str, object]:
    """求值一个机制模板，返回 ``{槽位: 值}``（不落 store，由调用方暂存）。

    ``scope="field"``：整场内核一次调用（父值为整场序列，输出为整场）；
    ``accelerated`` 与参考实现同协议，可逐位替换（内核对由测试锁定）。
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
        return _collect(mechanism, impl(context))
    if instance.kind == "lattice":
        if instance.size is None:
            raise NotImplementedError(
                f"机制 {mechanism.id}: 流式物化 lattice 在 P1 交付"
            )
        fields = {
            slot: LatticeField(instance.size, 0) for slot in slots
        }
        for coords in fields[slots[0]].coordinates():
            parent_values = _resolve_parents(
                program, store, mechanism, inputs=inputs, instance=coords
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
            for slot, value in collected.items():
                fields[slot].set(coords, value)
        return dict(fields)
    raise NotImplementedError(
        f"机制 {mechanism.id}: 实例类型 {instance.kind} 的运行时支持在 P4 交付"
    )


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
) -> dict[str, object]:
    """整场内核求值：一次调用，父值为整场序列（或 global 广播标量）。"""
    slots = mechanism.outputs()
    output_slot = program.slots[slots[0]]
    instance = program.instances[output_slot.on]
    if instance.kind != "lattice" or instance.size is None:
        raise NotImplementedError(
            f"机制 {mechanism.id}: field 作用域需要固定尺寸 lattice"
        )
    parent_values: dict[str, object] = {}
    for parent in mechanism.parents:
        slot = program.slots[parent.slot]
        if slot.persist == "external":
            parent_values[parent.argument] = _external(inputs, parent.slot)
            continue
        value = store.read(parent.slot, parent.lag)
        if isinstance(value, LatticeField):
            parent_values[parent.argument] = value.values()
        else:
            parent_values[parent.argument] = value
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
    fields: dict[str, object] = {}
    for slot in slots:
        field = LatticeField(instance.size, 0)
        for coords, value in zip(field.coordinates(), result[slot]):
            field.set(coords, value)
        fields[slot] = field
    return fields


# ── 父值解析 ─────────────────────────────────────────────────────


def _resolve_parents(
    program: object,
    store: StateStore,
    mechanism: MechanismDecl,
    *,
    inputs: Mapping[str, object],
    instance: tuple[int, ...] | None,
) -> dict[str, object]:
    values: dict[str, object] = {}
    for parent in mechanism.parents:
        slot = program.slots[parent.slot]
        if slot.persist == "external":
            values[parent.argument] = _external(inputs, parent.slot)
        elif instance is None:
            values[parent.argument] = store.read(parent.slot, parent.lag)
        else:
            values[parent.argument] = _neighbor_value(
                program, store, parent, instance
            )
    return values


def _external(inputs: Mapping[str, object], slot_id: str) -> object:
    try:
        return inputs[slot_id]
    except KeyError:
        raise KeyError(
            f"外部槽位 {slot_id} 本帧未提供输入"
        ) from None


def _neighbor_value(
    program: object,
    store: StateStore,
    parent: Parent,
    coords: tuple[int, ...],
) -> object:
    field = store.read(parent.slot, parent.lag)
    if parent.relation == "same":
        return field.get(coords)
    relation = program.relations[parent.relation]
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
