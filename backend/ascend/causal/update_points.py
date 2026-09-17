"""帧内更新点声明（WC-4；世界基座 13 篇）。

世界状态的更新只能由**声明的更新点**触发（ADR-12）。本模块加载、
严格校验并摘要更新点表 ``declarations/update_points.json``：

- 周期为符号刻度（``game_minute`` / ``game_hour`` / ``game_day``），
  编译期由世界程序映射为 tick，声明本身不依赖 config；
- ``order`` 决定同一推进内的执行顺序（升序，唯一）；
- ``slots`` 声明本更新点写入的状态槽位，编译期与槽位表交叉校验
  （互相覆盖，不得漂移）。

本模块只负责加载、校验与摘要；执行接入由世界程序与运行时完成。
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

UPDATE_POINTS_PATH: Path = (
    Path(__file__).resolve().parent
    / "declarations"
    / "update_points.json"
)

SCHEMA_VERSION: int = 1

# 周期符号刻度（编译期映射为 tick；未知刻度拒绝）
PERIODS: tuple[str, ...] = ("game_minute", "game_hour", "game_day")

_TOP_LEVEL_REQUIRED = ("schema_version", "contract_version", "points")
_ENTRY_REQUIRED = ("id", "owner", "period", "order", "slots")
_IDENT_PATTERN = re.compile(r"^[a-z][a-z0-9_]*(?:\.[a-z0-9_]+)*$")


@dataclass(frozen=True, slots=True)
class UpdatePointDeclaration:
    """一条更新点声明。

    Attributes:
        id: 全局唯一标识（也是槽位 ``update.stage`` 引用名）。
        owner: 归属声明（世界槽位 owner 语义）。
        period: 符号周期（见 ``PERIODS``）。
        order: 执行顺序（升序，唯一）。
        slots: 本更新点写入的状态槽位（可为空 = 只产出派生的观察）。
    """

    id: str
    owner: str
    period: str
    order: int
    slots: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class UpdatePointTable:
    """已校验的更新点表（按 order 升序）。"""

    schema_version: int
    contract_version: str
    points: tuple[UpdatePointDeclaration, ...]

    def require(self, point_id: str) -> UpdatePointDeclaration:
        """按标识查询；未登记抛 KeyError（fail-closed）。"""
        for point in self.points:
            if point.id == point_id:
                return point
        raise KeyError(f"未登记的更新点: {point_id}")

    def digest(self) -> str:
        """声明语义投影的规范摘要。"""
        return hashlib.sha256(canonical_bytes(self._projection())).hexdigest()

    def _projection(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "contract_version": self.contract_version,
            "points": [
                {
                    "id": point.id,
                    "owner": point.owner,
                    "period": point.period,
                    "order": point.order,
                    "slots": list(point.slots),
                }
                for point in self.points
            ],
        }


def load_update_points(path: Path | None = None) -> UpdatePointTable:
    """加载并严格校验更新点声明；任何违规抛 DeclarationError。"""
    source = Path(path) if path is not None else UPDATE_POINTS_PATH
    try:
        raw = source.read_text(encoding="utf-8")
    except OSError as exc:
        raise DeclarationError(f"声明文件不可读: {source}") from exc
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise DeclarationError(f"声明文件不是合法 JSON: {source}") from exc
    return _parse_table(_require_mapping(data, str(source)), str(source))


def _parse_table(data: dict, where: str) -> UpdatePointTable:
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
        _parse_point(entry, f"{where}.points[{index}]")
        for index, entry in enumerate(
            _require_list(data["points"], f"{where}.points")
        )
    )
    seen_ids: set[str] = set()
    seen_orders: set[int] = set()
    for entry in entries:
        if entry.id in seen_ids:
            _fail(f"{where}.points", f"重复更新点: {entry.id}")
        seen_ids.add(entry.id)
        if entry.order in seen_orders:
            _fail(f"{where}.points", f"重复执行顺序: {entry.order}")
        seen_orders.add(entry.order)
    return UpdatePointTable(
        schema_version=schema_version,
        contract_version=contract_version,
        points=tuple(sorted(entries, key=lambda item: item.order)),
    )


def _parse_point(entry: object, where: str) -> UpdatePointDeclaration:
    raw = _require_mapping(entry, where)
    _expect_keys(raw, _ENTRY_REQUIRED, (), where)
    point_id = _require_str(raw["id"], f"{where}.id")
    if _IDENT_PATTERN.fullmatch(point_id) is None:
        _fail(f"{where}.id", f"更新点标识格式非法: {point_id!r}")
    period = _require_str(raw["period"], f"{where}.period")
    if period not in PERIODS:
        _fail(f"{where}.period", f"未知周期刻度: {period!r}")
    order = _require_int(raw["order"], f"{where}.order")
    if order <= 0:
        _fail(f"{where}.order", f"执行顺序必须为正整数: {order!r}")
    slots_raw = _require_list(raw["slots"], f"{where}.slots")
    slots: list[str] = []
    for index, item in enumerate(slots_raw):
        slot_id = _require_str(item, f"{where}.slots[{index}]")
        if slot_id in slots:
            _fail(f"{where}.slots", f"重复槽位: {slot_id}")
        slots.append(slot_id)
    return UpdatePointDeclaration(
        id=point_id,
        owner=_require_str(raw["owner"], f"{where}.owner"),
        period=period,
        order=order,
        slots=tuple(slots),
    )
