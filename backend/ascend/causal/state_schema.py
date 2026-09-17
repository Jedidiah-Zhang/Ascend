"""世界状态声明的加载、校验与规范编码（WC-3 / 附录 D.1）。

- 严格加载：未知字段、重复标识、自由文本占位与非法取值一律拒绝（fail-closed）；
- 声明摘要：文档指针不参与，语义投影的规范 JSON SHA-256；
- 规范编码：相等、序列化与摘要共用同一字节串（WC-3.5）。

槽位值语义（更新位置、干预权限）尚未裁决的字段以 ``null`` + ``pending``
登记；本模块只实现声明与编码，不接入运行时演化。
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .declaration import (
    DeclarationError,
    canonical_bytes,
    expect_keys as _expect_keys,
    fail as _fail,
    require_bool as _require_bool,
    require_int as _require_int,
    require_list as _require_list,
    require_mapping as _require_mapping,
    require_str as _require_str,
)

DECLARATION_PATH: Path = (
    Path(__file__).resolve().parent / "declarations" / "state_slots.json"
)

SCHEMA_VERSION: int = 3
ENCODING_MAGIC: bytes = b"ASCW"
ENCODING_VERSION: int = 2

_CARRIERS = ("global", "field", "entity")
_UPDATE_KINDS = ("driver_frame_advance", "mechanism")
_MISSING_RULES = ("none",)
_BITS = (8, 16, 32, 64)
_SPACE_KINDS = ("chunked_grid",)
_SCOPES = ("tile", "chunk")
_PENDING_PATHS = ("update.stage", "permissions.intervene")

_TOP_LEVEL_REQUIRED = (
    "schema_version",
    "contract_version",
    "slice",
    "refs",
    "space",
    "slots",
    "derived",
    "external_inputs",
    "records",
    "removed",
)

_ID_PATTERN = re.compile(r"^[a-z][a-z0-9_]*(?:\.[a-z0-9_]+)+$")
_OWNER_PATTERN = re.compile(r"^[a-z][a-z0-9_]*(?:\.[a-z0-9_]+)*$")

_CHUNK_COORD_MAX = 0xFFFFFFFF


def _require_id(value: object, where: str) -> str:
    text = _require_str(value, where)
    if _ID_PATTERN.fullmatch(text) is None:
        _fail(where, f"标识格式非法（需命名空间 id）: {text!r}")
    return text


# ── 声明数据类 ────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class SlotDomain:
    """槽位值域（整数刻度）。

    ``scope`` 仅场槽位使用：tile = 每格 1 个值（chunk_size² 长度），
    chunk = 每 chunk 1 个值（标量，如积分游标）。
    """

    type: str
    bits: int
    min: int
    max: int
    unit: str
    missing: str
    scope: str | None = None


@dataclass(frozen=True, slots=True)
class SlotUpdate:
    """槽位更新位置；stage 未裁决时为 None（见 pending）。"""

    kind: str
    stage: str | None


@dataclass(frozen=True, slots=True)
class SlotPermissions:
    """槽位权限；intervene 未裁决时为 None（见 pending）。"""

    intervene: bool | None
    observe: bool
    record: bool


@dataclass(frozen=True, slots=True)
class SlotDeclaration:
    """状态槽位声明（W_t 的组成部分）。"""

    id: str
    carrier: str
    domain: SlotDomain
    role: str
    update: SlotUpdate
    permissions: SlotPermissions
    owner: str
    persist: bool
    pending: tuple[tuple[str, str], ...]
    audit_ref: str


@dataclass(frozen=True, slots=True)
class DerivedDeclaration:
    """派生量声明（W_t 的纯函数，不得影响未来）。"""

    id: str
    recompute: str
    cache_ok: bool
    audit_ref: str


@dataclass(frozen=True, slots=True)
class ExternalInputDeclaration:
    """外部输入声明（不进入世界状态）。"""

    id: str
    description: str
    enters_world_state: bool
    audit_ref: str


@dataclass(frozen=True, slots=True)
class RecordDeclaration:
    """记录通道声明（只作证据）。"""

    id: str
    description: str
    audit_ref: str


@dataclass(frozen=True, slots=True)
class RemovedDeclaration:
    """待消除的隐藏状态 / 非法路径声明。"""

    id: str
    reason: str
    audit_ref: str | None
    contract_ref: str | None


@dataclass(frozen=True, slots=True)
class SpaceDeclaration:
    """空间声明（WC-2.1；进入世界身份）。"""

    kind: str
    chunk_size: int


@dataclass(frozen=True, slots=True)
class StateSlice:
    """规范编码输入：时钟 tick + 各场槽位的分块缓冲。

    ``fields`` 必须覆盖全部场槽位且各通道 chunk 集合一致；
    单个缓冲为 chunk_size² 的 u8 字节串（行优先 ``y·chunk_size + x``）。
    chunk 集合的实例域完整性待实例域声明落地后校验。
    """

    tick: int
    fields: Mapping[str, Mapping[tuple[int, int], bytes]]


@dataclass(frozen=True, slots=True)
class StateDeclaration:
    """state_slots.json 的已校验语义视图。"""

    schema_version: int
    contract_version: str
    slice_id: str
    slice_description: str
    space: SpaceDeclaration
    slots: tuple[SlotDeclaration, ...]
    derived: tuple[DerivedDeclaration, ...]
    external_inputs: tuple[ExternalInputDeclaration, ...]
    records: tuple[RecordDeclaration, ...]
    removed: tuple[RemovedDeclaration, ...]

    @property
    def field_slots(self) -> tuple[SlotDeclaration, ...]:
        """场槽位（carrier=field）。"""
        return tuple(slot for slot in self.slots if slot.carrier == "field")

    @property
    def global_slots(self) -> tuple[SlotDeclaration, ...]:
        """全局槽位（carrier=global）。"""
        return tuple(slot for slot in self.slots if slot.carrier == "global")

    def digest(self) -> str:
        """声明语义投影的规范摘要（文档指针与 pending 原因不参与）。"""
        return hashlib.sha256(canonical_bytes(self._projection())).hexdigest()

    def _projection(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "contract_version": self.contract_version,
            "slice_id": self.slice_id,
            "space": {
                "kind": self.space.kind,
                "chunk_size": self.space.chunk_size,
            },
            "slots": sorted(
                (
                    {
                        "id": slot.id,
                        "carrier": slot.carrier,
                        "role": slot.role,
                        "domain": {
                            "type": slot.domain.type,
                            "bits": slot.domain.bits,
                            "min": slot.domain.min,
                            "max": slot.domain.max,
                            "unit": slot.domain.unit,
                            "missing": slot.domain.missing,
                            "scope": slot.domain.scope,
                        },
                        "update": {
                            "kind": slot.update.kind,
                            "stage": slot.update.stage,
                        },
                        "permissions": {
                            "intervene": slot.permissions.intervene,
                            "observe": slot.permissions.observe,
                            "record": slot.permissions.record,
                        },
                        "owner": slot.owner,
                        "persist": slot.persist,
                        "pending": sorted(path for path, _ in slot.pending),
                    }
                    for slot in self.slots
                ),
                key=lambda item: item["id"],
            ),
            "derived": sorted(
                (
                    {
                        "id": item.id,
                        "recompute": item.recompute,
                        "cache_ok": item.cache_ok,
                    }
                    for item in self.derived
                ),
                key=lambda item: item["id"],
            ),
            "external_inputs": sorted(
                (
                    {
                        "id": item.id,
                        "description": item.description,
                        "enters_world_state": item.enters_world_state,
                    }
                    for item in self.external_inputs
                ),
                key=lambda item: item["id"],
            ),
            "records": sorted(
                (
                    {"id": item.id, "description": item.description}
                    for item in self.records
                ),
                key=lambda item: item["id"],
            ),
            "removed": sorted(
                (
                    {"id": item.id, "reason": item.reason}
                    for item in self.removed
                ),
                key=lambda item: item["id"],
            ),
        }


# ── 加载与校验 ────────────────────────────────────────────


def load_declaration(path: Path | None = None) -> StateDeclaration:
    """加载并严格校验声明数据；任何违规抛 DeclarationError。"""
    source = Path(path) if path is not None else DECLARATION_PATH
    try:
        raw = source.read_text(encoding="utf-8")
    except OSError as exc:
        raise DeclarationError(f"声明文件不可读: {source}") from exc
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise DeclarationError(f"声明文件不是合法 JSON: {source}") from exc
    return _parse_declaration(
        _require_mapping(data, str(source)),
        str(source),
    )


def _parse_declaration(data: dict[str, Any], where: str) -> StateDeclaration:
    _expect_keys(data, _TOP_LEVEL_REQUIRED, (), where)

    schema_version = _require_int(data["schema_version"], f"{where}.schema_version")
    if schema_version != SCHEMA_VERSION:
        _fail(
            f"{where}.schema_version",
            f"不支持 {schema_version}（本实现只读 {SCHEMA_VERSION}，不写迁移）",
        )
    contract_version = _require_str(
        data["contract_version"], f"{where}.contract_version",
    )

    slice_raw = _require_mapping(data["slice"], f"{where}.slice")
    _expect_keys(slice_raw, ("id", "description"), (), f"{where}.slice")
    slice_id = _require_str(slice_raw["id"], f"{where}.slice.id")
    slice_description = _require_str(
        slice_raw["description"], f"{where}.slice.description",
    )

    refs_raw = _require_mapping(data["refs"], f"{where}.refs")
    _expect_keys(refs_raw, ("audit", "contract"), (), f"{where}.refs")
    _require_str(refs_raw["audit"], f"{where}.refs.audit")
    _require_str(refs_raw["contract"], f"{where}.refs.contract")

    space_raw = _require_mapping(data["space"], f"{where}.space")
    _expect_keys(space_raw, ("kind", "chunk_size"), (), f"{where}.space")
    space_kind = _require_str(space_raw["kind"], f"{where}.space.kind")
    if space_kind not in _SPACE_KINDS:
        _fail(f"{where}.space.kind", f"未知空间类型: {space_kind!r}")
    chunk_size = _require_int(space_raw["chunk_size"], f"{where}.space.chunk_size")
    if chunk_size <= 0:
        _fail(f"{where}.space.chunk_size", "必须为正整数")

    slots = tuple(
        _parse_slot(entry, f"{where}.slots[{index}]")
        for index, entry in enumerate(_require_list(data["slots"], f"{where}.slots"))
    )
    if not slots:
        _fail(f"{where}.slots", "不得为空（声明切片至少一个状态槽位）")
    if len(slots) > 255:
        _fail(f"{where}.slots", "槽位数量超出规范编码上限 255")

    derived = tuple(
        _parse_derived(entry, f"{where}.derived[{index}]")
        for index, entry in enumerate(
            _require_list(data["derived"], f"{where}.derived")
        )
    )
    external_inputs = tuple(
        _parse_external_input(entry, f"{where}.external_inputs[{index}]")
        for index, entry in enumerate(
            _require_list(data["external_inputs"], f"{where}.external_inputs")
        )
    )
    records = tuple(
        _parse_record(entry, f"{where}.records[{index}]")
        for index, entry in enumerate(
            _require_list(data["records"], f"{where}.records")
        )
    )
    removed = tuple(
        _parse_removed(entry, f"{where}.removed[{index}]")
        for index, entry in enumerate(
            _require_list(data["removed"], f"{where}.removed")
        )
    )

    seen: dict[str, str] = {}
    for section, entries in (
        ("slots", slots),
        ("derived", derived),
        ("external_inputs", external_inputs),
        ("records", records),
        ("removed", removed),
    ):
        for entry in entries:
            if entry.id in seen:
                _fail(
                    f"{where}.{section}",
                    f"重复标识 {entry.id!r}（已出现于 {seen[entry.id]}）",
                )
            seen[entry.id] = section

    return StateDeclaration(
        schema_version=schema_version,
        contract_version=contract_version,
        slice_id=slice_id,
        slice_description=slice_description,
        space=SpaceDeclaration(kind=space_kind, chunk_size=chunk_size),
        slots=slots,
        derived=derived,
        external_inputs=external_inputs,
        records=records,
        removed=removed,
    )


def _parse_slot(entry: object, where: str) -> SlotDeclaration:
    raw = _require_mapping(entry, where)
    _expect_keys(
        raw,
        (
            "id",
            "carrier",
            "domain",
            "role",
            "update",
            "permissions",
            "owner",
            "persist",
            "audit_ref",
        ),
        ("pending",),
        where,
    )

    slot_id = _require_id(raw["id"], f"{where}.id")
    carrier = _require_str(raw["carrier"], f"{where}.carrier")
    if carrier not in _CARRIERS:
        _fail(f"{where}.carrier", f"未知载体: {carrier!r}")
    role = _require_str(raw["role"], f"{where}.role")
    if role != "state":
        _fail(
            f"{where}.role",
            "slots[] 只登记持久类为 state 的槽位（派生入 derived[]）",
        )
    persist = _require_bool(raw["persist"], f"{where}.persist")
    if not persist:
        _fail(f"{where}.persist", "状态槽位必须持久（WC-3.1）")
    owner = _require_str(raw["owner"], f"{where}.owner")
    if _OWNER_PATTERN.fullmatch(owner) is None:
        _fail(f"{where}.owner", f"所有者标识格式非法: {owner!r}")

    update_raw = _require_mapping(raw["update"], f"{where}.update")
    _expect_keys(update_raw, ("kind",), ("stage",), f"{where}.update")
    update_kind = _require_str(update_raw["kind"], f"{where}.update.kind")
    if update_kind not in _UPDATE_KINDS:
        _fail(f"{where}.update.kind", f"未知更新类型: {update_kind!r}")

    pending = _parse_pending(raw, where, update_kind)
    update = _parse_update(update_raw, pending, where)
    permissions = _parse_permissions(raw["permissions"], pending, where)

    domain = _parse_domain(raw["domain"], f"{where}.domain")
    if carrier == "field":
        if domain.scope is None:
            _fail(
                f"{where}.domain.scope",
                "场槽位必须声明粒度（tile / chunk）",
            )
    elif domain.scope is not None:
        _fail(f"{where}.domain.scope", "scope 仅场槽位适用")

    return SlotDeclaration(
        id=slot_id,
        carrier=carrier,
        domain=domain,
        role=role,
        update=update,
        permissions=permissions,
        owner=owner,
        persist=persist,
        pending=pending,
        audit_ref=_require_str(raw["audit_ref"], f"{where}.audit_ref"),
    )


def _parse_pending(
    raw: Mapping[str, Any],
    where: str,
    update_kind: str,
) -> tuple[tuple[str, str], ...]:
    if "pending" not in raw:
        return ()
    pending_raw = _require_mapping(raw["pending"], f"{where}.pending")
    if not pending_raw:
        _fail(f"{where}.pending", "不得为空对象（无待定字段即删除该键）")
    entries: list[tuple[str, str]] = []
    for path, reason in pending_raw.items():
        if path not in _PENDING_PATHS:
            _fail(f"{where}.pending", f"未知待定字段路径: {path!r}")
        if path == "update.stage" and update_kind != "mechanism":
            _fail(
                f"{where}.pending",
                "update.stage 不适用于 driver_frame_advance 槽位",
            )
        entries.append((path, _require_str(reason, f"{where}.pending.{path}")))
    return tuple(entries)


def _parse_update(
    raw: Mapping[str, Any],
    pending: tuple[tuple[str, str], ...],
    where: str,
) -> SlotUpdate:
    kind = raw["kind"]
    stage = raw.get("stage")
    stage_pending = any(path == "update.stage" for path, _ in pending)
    if stage_pending:
        if stage is not None:
            _fail(f"{where}.update.stage", "已登记 pending 的字段必须为 null")
    elif kind == "mechanism":
        stage = _require_str(stage, f"{where}.update.stage")
        if _OWNER_PATTERN.fullmatch(stage) is None:
            _fail(f"{where}.update.stage", f"阶段标识格式非法: {stage!r}")
    elif stage is not None:
        _fail(f"{where}.update.stage", "driver_frame_advance 不接受阶段")
    return SlotUpdate(kind=kind, stage=stage)


def _parse_permissions(
    entry: object,
    pending: tuple[tuple[str, str], ...],
    where: str,
) -> SlotPermissions:
    raw = _require_mapping(entry, f"{where}.permissions")
    _expect_keys(raw, ("observe", "record"), ("intervene",), f"{where}.permissions")
    intervene_pending = any(
        path == "permissions.intervene" for path, _ in pending
    )
    intervene_raw = raw.get("intervene")
    if intervene_pending:
        if intervene_raw is not None:
            _fail(
                f"{where}.permissions.intervene",
                "已登记 pending 的字段必须为 null",
            )
        intervene: bool | None = None
    else:
        intervene = _require_bool(
            intervene_raw, f"{where}.permissions.intervene",
        )
    return SlotPermissions(
        intervene=intervene,
        observe=_require_bool(raw["observe"], f"{where}.permissions.observe"),
        record=_require_bool(raw["record"], f"{where}.permissions.record"),
    )


def _parse_domain(entry: object, where: str) -> SlotDomain:
    raw = _require_mapping(entry, where)
    _expect_keys(
        raw,
        ("type", "bits", "min", "max", "unit", "missing"),
        ("scope",),
        where,
    )
    domain_type = _require_str(raw["type"], f"{where}.type")
    if domain_type != "int":
        _fail(f"{where}.type", "编码只支持 int 值域")
    bits = _require_int(raw["bits"], f"{where}.bits")
    if bits not in _BITS:
        _fail(f"{where}.bits", f"不支持 {bits} 位")
    min_value = _require_int(raw["min"], f"{where}.min")
    max_value = _require_int(raw["max"], f"{where}.max")
    if min_value < 0:
        _fail(f"{where}.min", "无符号刻度不得为负")
    if min_value > max_value:
        _fail(f"{where}.min", "min 不得大于 max")
    if max_value > (1 << bits) - 1:
        _fail(f"{where}.max", f"超出 {bits} 位无符号范围")
    missing = _require_str(raw["missing"], f"{where}.missing")
    if missing not in _MISSING_RULES:
        _fail(f"{where}.missing", f"未知缺失值规则: {missing!r}")
    scope = raw.get("scope")
    if scope is not None:
        scope = _require_str(scope, f"{where}.scope")
        if scope not in _SCOPES:
            _fail(f"{where}.scope", f"未知场粒度: {scope!r}")
    return SlotDomain(
        type=domain_type,
        bits=bits,
        min=min_value,
        max=max_value,
        unit=_require_str(raw["unit"], f"{where}.unit"),
        missing=missing,
        scope=scope,
    )


def _parse_derived(entry: object, where: str) -> DerivedDeclaration:
    raw = _require_mapping(entry, where)
    _expect_keys(raw, ("id", "recompute", "cache_ok", "audit_ref"), (), where)
    return DerivedDeclaration(
        id=_require_id(raw["id"], f"{where}.id"),
        recompute=_require_str(raw["recompute"], f"{where}.recompute"),
        cache_ok=_require_bool(raw["cache_ok"], f"{where}.cache_ok"),
        audit_ref=_require_str(raw["audit_ref"], f"{where}.audit_ref"),
    )


def _parse_external_input(entry: object, where: str) -> ExternalInputDeclaration:
    raw = _require_mapping(entry, where)
    _expect_keys(
        raw,
        ("id", "description", "enters_world_state", "audit_ref"),
        (),
        where,
    )
    enters = _require_bool(
        raw["enters_world_state"], f"{where}.enters_world_state",
    )
    if enters:
        _fail(
            f"{where}.enters_world_state",
            "外部输入不得进入世界状态（进入即为槽位；WC-6.5）",
        )
    return ExternalInputDeclaration(
        id=_require_id(raw["id"], f"{where}.id"),
        description=_require_str(raw["description"], f"{where}.description"),
        enters_world_state=enters,
        audit_ref=_require_str(raw["audit_ref"], f"{where}.audit_ref"),
    )


def _parse_record(entry: object, where: str) -> RecordDeclaration:
    raw = _require_mapping(entry, where)
    _expect_keys(raw, ("id", "description", "audit_ref"), (), where)
    return RecordDeclaration(
        id=_require_id(raw["id"], f"{where}.id"),
        description=_require_str(raw["description"], f"{where}.description"),
        audit_ref=_require_str(raw["audit_ref"], f"{where}.audit_ref"),
    )


def _parse_removed(entry: object, where: str) -> RemovedDeclaration:
    raw = _require_mapping(entry, where)
    _expect_keys(
        raw,
        ("id", "reason"),
        ("audit_ref", "contract_ref"),
        where,
    )
    audit_ref = raw.get("audit_ref")
    contract_ref = raw.get("contract_ref")
    if audit_ref is None and contract_ref is None:
        _fail(where, "audit_ref 与 contract_ref 至少声明其一")
    return RemovedDeclaration(
        id=_require_id(raw["id"], f"{where}.id"),
        reason=_require_str(raw["reason"], f"{where}.reason"),
        audit_ref=(
            _require_str(audit_ref, f"{where}.audit_ref")
            if audit_ref is not None
            else None
        ),
        contract_ref=(
            _require_str(contract_ref, f"{where}.contract_ref")
            if contract_ref is not None
            else None
        ),
    )


# ── 规范编码 ──────────────────────────────────────────────


def encode_state(declaration: StateDeclaration, state: StateSlice) -> bytes:
    """按 WC-3.5 规范编码状态；违规（越界 / 缺通道 / 坏缓冲）即拒绝。"""
    entity_slots = [
        slot.id for slot in declaration.slots if slot.carrier == "entity"
    ]
    if entity_slots:
        _fail(
            "declaration",
            f"规范编码 v1 不支持 entity 槽位: {', '.join(entity_slots)}",
        )
    global_slots = declaration.global_slots
    if len(global_slots) != 1:
        _fail("declaration", "规范编码要求恰好一个全局状态槽位")
    global_slot = global_slots[0]

    tick = _validate_global_value(state.tick, global_slot)
    buffers = _validate_fields(declaration, state)

    slots = sorted(declaration.slots, key=lambda slot: slot.id)
    out = bytearray()
    out += ENCODING_MAGIC
    out.append(ENCODING_VERSION)
    out.append(len(slots))
    for slot in slots:
        id_bytes = slot.id.encode("utf-8")
        out.append(len(id_bytes))
        out += id_bytes
        if slot.carrier == "global":
            out += tick.to_bytes(global_slot.domain.bits // 8, "little")
        else:
            out += _encode_field(slot, buffers[slot.id])
    return bytes(out)


def _validate_global_value(value: object, slot: SlotDeclaration) -> int:
    where = f"state.{slot.id}"
    if type(value) is not int:
        _fail(where, f"应为整数，实际为 {value!r}")
    if not slot.domain.min <= value <= slot.domain.max:
        _fail(
            where,
            f"越界: {value} 不在 [{slot.domain.min}, {slot.domain.max}]",
        )
    return value


def _validate_fields(
    declaration: StateDeclaration,
    state: StateSlice,
) -> dict[str, dict[tuple[int, int], bytes]]:
    field_slots = {slot.id: slot for slot in declaration.field_slots}
    provided = state.fields
    if not isinstance(provided, Mapping):
        _fail("state.fields", "应为映射")
    missing = sorted(set(field_slots) - set(provided))
    extra = sorted(set(provided) - set(field_slots))
    if missing or extra:
        _fail(
            "state.fields",
            f"场槽位集合与声明不一致（缺: {missing or '无'}，多: {extra or '无'}）",
        )

    cells = declaration.space.chunk_size * declaration.space.chunk_size
    buffers: dict[str, dict[tuple[int, int], bytes]] = {}
    expected_keys: set[tuple[int, int]] | None = None
    for slot_id in sorted(field_slots):
        slot = field_slots[slot_id]
        width = slot.domain.bits // 8
        if slot.domain.scope == "tile":
            if slot.domain.bits != 8:
                _fail(
                    f"declaration.{slot_id}",
                    "tile 场槽位只支持 u8（每格 1 字节）",
                )
            expected_length = cells
        else:
            expected_length = width
        chunks = provided[slot_id]
        if not isinstance(chunks, Mapping):
            _fail(f"state.fields.{slot_id}", "应为 chunk → 缓冲的映射")
        parsed: dict[tuple[int, int], bytes] = {}
        for key, buffer in chunks.items():
            parsed[_validate_chunk_key(key, slot_id)] = _validate_buffer(
                buffer, slot, expected_length,
            )
        keys = set(parsed)
        if expected_keys is None:
            expected_keys = keys
        elif keys != expected_keys:
            _fail(
                f"state.fields.{slot_id}",
                "各场槽位的 chunk 集合必须一致",
            )
        buffers[slot_id] = parsed
    return buffers


def _validate_chunk_key(key: object, slot_id: str) -> tuple[int, int]:
    where = f"state.fields.{slot_id}"
    if (
        not isinstance(key, tuple)
        or len(key) != 2
        or any(type(coord) is not int for coord in key)
    ):
        _fail(where, f"chunk 键必须是 (cx, cy) 整数对，实际为 {key!r}")
    x, y = key
    if not (0 <= x <= _CHUNK_COORD_MAX and 0 <= y <= _CHUNK_COORD_MAX):
        _fail(where, f"chunk 坐标越界: {key!r}")
    return x, y


def _validate_buffer(
    buffer: object,
    slot: SlotDeclaration,
    expected_length: int,
) -> bytes:
    where = f"state.fields.{slot.id}"
    if isinstance(buffer, (bytes, bytearray, memoryview)):
        data = bytes(buffer)
    else:
        _fail(where, f"缓冲应为 bytes，实际为 {type(buffer).__name__}")
    if len(data) != expected_length:
        _fail(
            where,
            f"缓冲长度应为 {expected_length}，实际为 {len(data)}",
        )
    low, high = slot.domain.min, slot.domain.max
    if slot.domain.scope == "chunk":
        value = int.from_bytes(data, "little")
        if not low <= value <= high:
            _fail(where, f"标量越界: {value} 不在 [{low}, {high}]")
    elif (low, high) != (0, 255) and any(
        value < low or value > high for value in data
    ):
        _fail(where, f"缓冲含越界值（值域 [{low}, {high}]）")
    return data


def _encode_field(
    slot: SlotDeclaration,
    chunks: Mapping[tuple[int, int], bytes],
) -> bytes:
    out = bytearray()
    keys = sorted(chunks)
    out += len(keys).to_bytes(4, "little")
    for x, y in keys:
        out += x.to_bytes(4, "little")
        out += y.to_bytes(4, "little")
        out += chunks[(x, y)]
    return bytes(out)


def state_digest(declaration: StateDeclaration, state: StateSlice) -> str:
    """状态规范编码的 SHA-256（"同初态"判定基准，WC-3.5）。"""
    return hashlib.sha256(encode_state(declaration, state)).hexdigest()
