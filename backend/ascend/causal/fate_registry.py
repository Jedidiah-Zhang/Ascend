"""命运命名空间登记（WC-5.5 / 附录 D.3）。

全部随机命名空间与用途登记为机器可读数据；其摘要进入世界身份。
本模块只负责加载、校验与摘要；消费点接线由后续批次完成。
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path

from .declaration import (
    DeclarationError,
    canonical_bytes,
    expect_keys as _expect_keys,
    fail as _fail,
    require_int as _require_int,
    require_list as _require_list,
    require_mapping as _require_mapping,
    require_str as _require_str,
)

FATE_NAMESPACES_PATH: Path = (
    Path(__file__).resolve().parent
    / "declarations"
    / "fate_namespaces.json"
)

SCHEMA_VERSION: int = 1

_TOP_LEVEL_REQUIRED = ("schema_version", "contract_version", "namespaces")
_ENTRY_REQUIRED = ("namespace", "purpose", "consumer", "address_shape", "note")

_IDENT_PATTERN = re.compile(r"^[a-z][a-z0-9_]*(?:\.[a-z0-9_]+)*$")
_COMPONENT_PATTERN = re.compile(r"^[a-z][a-z0-9_]*$")


@dataclass(frozen=True, slots=True)
class FateNamespaceEntry:
    """一条命名空间登记。

    Attributes:
        namespace: 命名空间（地址第一分量）。
        purpose: 用途（同一命名空间内的抽取类别）。
        consumer: 消费方模块（相对 backend 的路径）。
        address_shape: 实例分量名列表（空 = 无实例分量）。
        note: 说明（不参与摘要）。
    """

    namespace: str
    purpose: str
    consumer: str
    address_shape: tuple[str, ...]
    note: str


@dataclass(frozen=True, slots=True)
class FateNamespaceRegistry:
    """已校验的命名空间登记表。"""

    schema_version: int
    contract_version: str
    entries: tuple[FateNamespaceEntry, ...]

    def lookup(self, namespace: str, purpose: str) -> FateNamespaceEntry | None:
        """按 (namespace, purpose) 查询；未登记返回 None。"""
        for entry in self.entries:
            if entry.namespace == namespace and entry.purpose == purpose:
                return entry
        return None

    def require(self, namespace: str, purpose: str) -> FateNamespaceEntry:
        """按 (namespace, purpose) 查询；未登记抛 KeyError（fail-closed）。"""
        entry = self.lookup(namespace, purpose)
        if entry is None:
            raise KeyError(f"未登记的随机地址命名空间: {namespace}/{purpose}")
        return entry

    def digest(self) -> str:
        """登记语义投影的规范摘要（note 不参与）。"""
        return hashlib.sha256(
            canonical_bytes(self._projection()),
        ).hexdigest()

    def _projection(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "contract_version": self.contract_version,
            "namespaces": sorted(
                (
                    {
                        "namespace": entry.namespace,
                        "purpose": entry.purpose,
                        "consumer": entry.consumer,
                        "address_shape": list(entry.address_shape),
                    }
                    for entry in self.entries
                ),
                key=lambda item: (item["namespace"], item["purpose"]),
            ),
        }


def load_fate_namespaces(path: Path | None = None) -> FateNamespaceRegistry:
    """加载并严格校验命名空间登记；任何违规抛 DeclarationError。"""
    source = Path(path) if path is not None else FATE_NAMESPACES_PATH
    try:
        raw = source.read_text(encoding="utf-8")
    except OSError as exc:
        raise DeclarationError(f"声明文件不可读: {source}") from exc
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise DeclarationError(f"声明文件不是合法 JSON: {source}") from exc
    return _parse_registry(_require_mapping(data, str(source)), str(source))


def _parse_registry(data: dict, where: str) -> FateNamespaceRegistry:
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
    entries = tuple(
        _parse_entry(entry, f"{where}.namespaces[{index}]")
        for index, entry in enumerate(
            _require_list(data["namespaces"], f"{where}.namespaces")
        )
    )
    seen: set[tuple[str, str]] = set()
    for entry in entries:
        key = (entry.namespace, entry.purpose)
        if key in seen:
            _fail(
                f"{where}.namespaces",
                f"重复登记 {entry.namespace}/{entry.purpose}",
            )
        seen.add(key)
    return FateNamespaceRegistry(
        schema_version=schema_version,
        contract_version=contract_version,
        entries=entries,
    )


def _parse_entry(entry: object, where: str) -> FateNamespaceEntry:
    raw = _require_mapping(entry, where)
    _expect_keys(raw, _ENTRY_REQUIRED, (), where)
    namespace = _require_str(raw["namespace"], f"{where}.namespace")
    if _IDENT_PATTERN.fullmatch(namespace) is None:
        _fail(f"{where}.namespace", f"命名空间格式非法: {namespace!r}")
    purpose = _require_str(raw["purpose"], f"{where}.purpose")
    if _IDENT_PATTERN.fullmatch(purpose) is None:
        _fail(f"{where}.purpose", f"用途格式非法: {purpose!r}")
    shape_raw = _require_list(raw["address_shape"], f"{where}.address_shape")
    address_shape: list[str] = []
    for index, item in enumerate(shape_raw):
        component = _require_str(item, f"{where}.address_shape[{index}]")
        if _COMPONENT_PATTERN.fullmatch(component) is None:
            _fail(
                f"{where}.address_shape[{index}]",
                f"分量名格式非法: {component!r}",
            )
        address_shape.append(component)
    return FateNamespaceEntry(
        namespace=namespace,
        purpose=purpose,
        consumer=_require_str(raw["consumer"], f"{where}.consumer"),
        address_shape=tuple(address_shape),
        note=_require_str(raw["note"], f"{where}.note"),
    )
