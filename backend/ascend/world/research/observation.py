"""观测协议 G — 帧边界、白名单、量化与缺失（WC-10.1 / WC-10.2）。

观测是**已提交状态**的纯函数：只在帧边界取值、只读声明为可观测的槽位、
按协议量化/置缺失。泄露审计 fail-closed：协议引用未声明或未授权槽位即
拒绝；研究记录与调试字段不构成观测来源。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping

from ascend.world.kernel import Address, address_value
from ascend.world.meta.declarations import AddressUse
from ascend.world.runtime.state import LatticeField

__all__ = ["ObservationSpec", "observe"]


@dataclass(frozen=True, slots=True)
class ObservationSpec:
    """一份观测协议：读哪些槽位、如何量化、是否加声明的观测噪声。"""

    id: str
    slots: tuple[str, ...]
    version: str = "1"
    quantize_shift: int = 0
    missing: object | None = None
    noise_address: AddressUse | None = None
    noise_span: int = 0

    def __post_init__(self) -> None:
        if not self.slots:
            raise ValueError("观测协议至少引用一个槽位")
        if type(self.quantize_shift) is not int or self.quantize_shift < 0:
            raise ValueError(f"量化位移必须为非负整数: {self.quantize_shift!r}")
        if type(self.noise_span) is not int or self.noise_span < 0:
            raise ValueError(f"噪声幅度必须为非负整数: {self.noise_span!r}")
        if self.noise_span > 0 and self.noise_address is None:
            raise ValueError("声明观测噪声必须给出噪声地址")


def observe(
    program: object,
    process: object,
    spec: ObservationSpec,
    *,
    observer: str = "observer",
) -> dict[str, object]:
    """按协议生成一份观测（已提交状态 + 帧边界 + 白名单）。"""
    _audit(program, spec)
    values: dict[str, object] = {}
    for slot_id in spec.slots:
        rendered = _render(
            process.committed(slot_id),
            spec,
            observer,
            process.seed,
            process.tick,
            slot_id,
        )
        values[slot_id] = rendered
    return {
        "observer": observer,
        "tick": process.tick,
        "version": spec.version,
        "values": values,
    }


def _audit(program: object, spec: ObservationSpec) -> None:
    for slot_id in spec.slots:
        slot = program.slots.get(slot_id)
        if slot is None:
            raise PermissionError(f"观测协议引用未声明槽位: {slot_id}")
        if not slot.permissions.observe:
            raise PermissionError(f"观测协议读取未授权槽位: {slot_id}")


def _render(
    value: object,
    spec: ObservationSpec,
    observer: str,
    root_seed: int,
    tick: int,
    slot_id: str,
) -> object:
    if isinstance(value, LatticeField):
        return tuple(
            _render_item(item, spec, observer, root_seed, tick, slot_id, index)
            for index, item in enumerate(value.values())
        )
    return _render_item(value, spec, observer, root_seed, tick, slot_id, 0)


def _render_item(
    value: object,
    spec: ObservationSpec,
    observer: str,
    root_seed: int,
    tick: int,
    slot_id: str,
    index: int,
) -> object:
    if value is None:
        return spec.missing
    if isinstance(value, int) and spec.quantize_shift > 0:
        value = value >> spec.quantize_shift
    if spec.noise_span > 0 and spec.noise_address is not None:
        address = Address(
            namespace=spec.noise_address.namespace,
            purpose=spec.noise_address.purpose,
            instance=(observer, slot_id, index),
            time=tick,
            draw_index=0,
        )
        value = value + address_value(  # type: ignore[operator]
            root_seed,
            address,
            minimum=-spec.noise_span,
            maximum=spec.noise_span,
        )
    return value
