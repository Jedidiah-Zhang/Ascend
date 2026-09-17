"""实现内容摘要表（构建期嵌入，运行期校验；issue #49）。

机制身份必须等于**实现内容**，而不是函数名。源码模式直接哈希方程函数
源码与文件依赖；打包（Nuitka，无源码）模式无法读取源码，因此由构建期
生成的 ``declarations/impl_digests.json`` 提供每个机制的
``equation_version``——该表与源码模式逐值一致，随包配送。

规则（fail-closed）：

- 打包模式：表必须存在且覆盖每个机制；缺表/缺条目即拒绝构造注册表，
  不再有"按名称降级"的回退；
- 源码模式：实时计算方程版本；若机制在表中有条目，必须与实时值一致
  （漂移 = 忘记重新生成，直接拒绝）；
- 表由 ``research/equations/export_impl_digests.py`` 生成并有 ``--check``
  漂移门禁；条目按 ``mechanism_id`` 排序，语义投影即全部字段。

本模块只负责加载与校验；摘要计算在 ``causal/registry.py``。
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from functools import lru_cache
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

IMPL_DIGESTS_PATH: Path = (
    Path(__file__).resolve().parent
    / "declarations"
    / "impl_digests.json"
)

SCHEMA_VERSION: int = 1

_TOP_LEVEL_REQUIRED = ("schema_version", "contract_version", "digests")
_ENTRY_REQUIRED = ("mechanism_id", "output", "equation_version")
_IDENT_PATTERN = re.compile(r"^[a-z][a-z0-9_]*(?:\.[a-z0-9_]+)*$")
_DIGEST_PATTERN = re.compile(r"^sha256:[0-9a-f]{64}$")


@dataclass(frozen=True, slots=True)
class ImplementationDigest:
    """单个机制的实现内容摘要条目。

    Attributes:
        mechanism_id: 机制标识（与注册表一致）。
        output: 输出节点 ID（交叉校验，防止表与声明错位）。
        equation_version: 方程 + 显式依赖的**内容**摘要
            （与源码模式实时计算值逐位一致）。
    """

    mechanism_id: str
    output: str
    equation_version: str


@dataclass(frozen=True, slots=True)
class ImplementationDigestTable:
    """已校验的实现摘要表（按 mechanism_id 排序）。"""

    schema_version: int
    contract_version: str
    entries: tuple[ImplementationDigest, ...]

    def get(self, mechanism_id: str) -> ImplementationDigest | None:
        """按标识查询；未登记返回 None。"""
        for entry in self.entries:
            if entry.mechanism_id == mechanism_id:
                return entry
        return None

    def require(self, mechanism_id: str) -> ImplementationDigest:
        """按标识查询；未登记抛 DeclarationError（fail-closed）。"""
        entry = self.get(mechanism_id)
        if entry is None:
            _fail(
                "impl_digests",
                f"实现内容摘要表缺少机制 {mechanism_id}（构建期未嵌入，"
                f"重新生成: research/equations/export_impl_digests.py）",
            )
        return entry

    def digest(self) -> str:
        """全表的规范摘要（门禁与测试用）。"""
        return hashlib.sha256(canonical_bytes(self._projection())).hexdigest()

    def _projection(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "contract_version": self.contract_version,
            "digests": [
                {
                    "mechanism_id": entry.mechanism_id,
                    "output": entry.output,
                    "equation_version": entry.equation_version,
                }
                for entry in self.entries
            ],
        }


def load_impl_digests(path: Path | None = None) -> ImplementationDigestTable:
    """加载并严格校验实现摘要表；任何违规抛 DeclarationError。"""
    source = Path(path) if path is not None else IMPL_DIGESTS_PATH
    try:
        raw = source.read_text(encoding="utf-8")
    except OSError as exc:
        raise DeclarationError(
            f"实现内容摘要表不可读: {source}；构建期须随包配送 "
            f"declarations/impl_digests.json"
        ) from exc
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise DeclarationError(f"实现内容摘要表不是合法 JSON: {source}") from exc
    return _parse_table(_require_mapping(data, str(source)), str(source))


@lru_cache(maxsize=1)
def get_impl_digests() -> ImplementationDigestTable:
    """进程内惰性加载并缓存生产实现摘要表。"""
    return load_impl_digests()


def _parse_table(data: dict, where: str) -> ImplementationDigestTable:
    _expect_keys(data, _TOP_LEVEL_REQUIRED, (), where)
    schema_version = _require_int(
        data["schema_version"], f"{where}.schema_version",
    )
    if schema_version != SCHEMA_VERSION:
        _fail(
            f"{where}.schema_version",
            f"不支持 {schema_version}（本实现只读 {SCHEMA_VERSION}，不写迁移）",
        )
    contract_version = _require_str(
        data["contract_version"], f"{where}.contract_version",
    )
    entries = tuple(
        _parse_entry(entry, f"{where}.digests[{index}]")
        for index, entry in enumerate(
            _require_list(data["digests"], f"{where}.digests")
        )
    )
    seen: set[str] = set()
    for entry in entries:
        if entry.mechanism_id in seen:
            _fail(f"{where}.digests", f"重复机制: {entry.mechanism_id}")
        seen.add(entry.mechanism_id)
    return ImplementationDigestTable(
        schema_version=schema_version,
        contract_version=contract_version,
        entries=tuple(sorted(entries, key=lambda item: item.mechanism_id)),
    )


def _parse_entry(entry: object, where: str) -> ImplementationDigest:
    raw = _require_mapping(entry, where)
    _expect_keys(raw, _ENTRY_REQUIRED, (), where)
    mechanism_id = _require_str(raw["mechanism_id"], f"{where}.mechanism_id")
    if _IDENT_PATTERN.fullmatch(mechanism_id) is None:
        _fail(f"{where}.mechanism_id", f"机制标识格式非法: {mechanism_id!r}")
    output = _require_str(raw["output"], f"{where}.output")
    equation_version = _require_str(
        raw["equation_version"], f"{where}.equation_version",
    )
    if _DIGEST_PATTERN.fullmatch(equation_version) is None:
        _fail(
            f"{where}.equation_version",
            f"方程版本必须为 sha256:<64 hex>：{equation_version!r}",
        )
    return ImplementationDigest(
        mechanism_id=mechanism_id,
        output=output,
        equation_version=equation_version,
    )
