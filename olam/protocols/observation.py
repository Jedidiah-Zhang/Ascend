"""观测协议 G — 帧边界、白名单、视野、量化与缺失（WC-10.1 / WC-10.2）。

观测是**已提交状态**的纯函数：只在帧边界取值、只读声明为可观测的槽位、
按协议量化/置缺失、按视野（相对观察者的偏移集合）读取实例。

泄露审计 fail-closed：协议引用未声明或未授权槽位即拒绝；研究记录与调试
字段不构成观测来源。

输出形状：

- 全局槽位：``values[slot] = 标量``；
- 实例槽位（chunk/场）：``values[(slot, coords)] = 值``（视野内每个偏移）。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping

from olam.kernel import Address, address_value
from olam.meta.declarations import AddressUse
from olam.runtime.state import DynamicField, LatticeField

__all__ = ["ObservationSpec", "observe"]

_MISSING_POLICIES = ("raise", "omit", "default")


@dataclass(frozen=True, slots=True)
class ObservationSpec:
    """一份观测协议：读哪些槽位、视野、量化、缺失与声明的观测噪声。

    Attributes:
        id: 协议标识。
        slots: 允许读取的槽位（须声明 ``observe`` 权限）。
        version: 协议版本（随训练数据记录）。
        viewport: 相对观察者位置的实例偏移集合（默认自身）。
        sample_every: 时间下采样（帧；1=每帧）。
        quantize_shift: 量化右移位数（0=不量化）。
        missing: 缺失值表示（``missing_policy="default"`` 时使用）。
        missing_policy: 实例未物化/越界时的行为（raise/omit/default）。
        noise_address: 观测噪声地址（命名空间 + 用途）。
        noise_span: 噪声幅度（对称整数区间；0=无噪声）。
    """

    id: str
    slots: tuple[str, ...]
    version: str = "1"
    viewport: tuple[tuple[int, ...], ...] = ((0, 0),)
    sample_every: int = 1
    quantize_shift: int = 0
    missing: object | None = None
    missing_policy: str = "raise"
    noise_address: AddressUse | None = None
    noise_span: int = 0

    def __post_init__(self) -> None:
        if not self.slots:
            raise ValueError("观测协议至少引用一个槽位")
        if not self.viewport:
            raise ValueError("视野至少一个偏移")
        if type(self.sample_every) is not int or self.sample_every <= 0:
            raise ValueError(f"采样间隔必须为正整数: {self.sample_every!r}")
        if type(self.quantize_shift) is not int or self.quantize_shift < 0:
            raise ValueError(f"量化位移必须为非负整数: {self.quantize_shift!r}")
        if self.missing_policy not in _MISSING_POLICIES:
            raise ValueError(f"未知缺失策略: {self.missing_policy!r}")
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
    position: tuple[int, ...] = (0, 0),
) -> dict[str, object]:
    """按协议生成一份观测（已提交状态 + 帧边界 + 白名单 + 视野）。"""
    _audit(program, spec)
    values: dict[object, object] = {}
    for slot_id in spec.slots:
        slot = program.slots[slot_id]
        instance = program.instances[slot.on]
        if instance.kind == "global":
            values[slot_id] = _render(
                process.committed(slot_id), spec, observer, process.seed,
                process.tick, slot_id, (),
            )
            continue
        _observe_instances(
            process, spec, slot_id, instance, position, observer, values,
        )
    return {
        "observer": observer,
        "tick": process.tick,
        "version": spec.version,
        "position": tuple(position),
        "values": values,
    }


def _observe_instances(
    process: object,
    spec: ObservationSpec,
    slot_id: str,
    instance: object,
    position: tuple[int, ...],
    observer: str,
    values: dict[object, object],
) -> None:
    field = process.committed(slot_id)
    for offset in spec.viewport:
        coords = tuple(
            int(a) + int(b) for a, b in zip(position, offset)
        )
        if isinstance(field, DynamicField):
            if not field.contains(coords):
                _handle_missing(spec, slot_id, coords, values)
                continue
            raw = field.get(coords)
        elif isinstance(field, LatticeField):
            try:
                raw = field.get(coords)
            except (ValueError, IndexError):
                _handle_missing(spec, slot_id, coords, values)
                continue
        else:
            raw = field
        values[(slot_id, coords)] = _render(
            raw, spec, observer, process.seed, process.tick, slot_id, coords,
        )


def _handle_missing(
    spec: ObservationSpec,
    slot_id: str,
    coords: tuple[int, ...],
    values: dict[object, object],
) -> None:
    if spec.missing_policy == "raise":
        raise KeyError(f"观测实例不可用: {slot_id}@{coords!r}")
    if spec.missing_policy == "omit":
        return
    values[(slot_id, coords)] = spec.missing


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
    coords: tuple[int, ...],
) -> object:
    if value is None:
        return spec.missing
    if isinstance(value, int) and not isinstance(value, bool):
        if spec.quantize_shift > 0:
            value = value >> spec.quantize_shift
        if spec.noise_span > 0 and spec.noise_address is not None:
            address = Address(
                namespace=spec.noise_address.namespace,
                purpose=spec.noise_address.purpose,
                instance=(observer, slot_id, *coords),
                time=tick,
                draw_index=0,
            )
            value = value + address_value(
                root_seed,
                address,
                minimum=-spec.noise_span,
                maximum=spec.noise_span,
            )
    return value
