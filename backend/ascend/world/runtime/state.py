"""世界状态容器 — 已提交值、帧内影子与 lag 历史（WC-3.5 / WC-7.6）。

- :class:`LatticeField`：定尺寸离散场（P0 小世界 / P1 内核绑定）；
- :class:`DynamicField`：稀疏动态场（chunk 流式物化：只存已物化实例）；
- :class:`StateStore`：状态/派生槽位的已提交值 + 帧内影子 + lag 历史。
  **提交原子**、**回滚干净**、**快照只含已提交值**（WC-8.1）。

**物化无关**（WC-2.3）：物化集合只决定"哪些实例被计算"，不改变任何已
物化实例的求值结果；快照记录物化集合以便恢复运行时视图（视图不是世界
语义）。``dematerialize`` 丢弃实例值：持久化由存档层负责（P2-3）。

lag 语义（与 ``WorldProgram`` 文档一致）：``lag=0`` 读本帧更早的写入
（影子），没有则读帧初值；``lag=1`` 读帧初值；``lag=k≥2`` 读 k−1 帧前的
帧初值。动态槽位用 ``read_at`` 逐实例读取。
"""

from __future__ import annotations

from collections import deque
from typing import Mapping

__all__ = [
    "DynamicField",
    "LatticeField",
    "StateStore",
    "export_value",
    "load_value",
]


class LatticeField:
    """定尺寸离散场（行主序：最后一个维度变化最快）。"""

    def __init__(
        self,
        size: tuple[int, ...],
        fill: int | float = 0,
    ) -> None:
        if not size or any(type(n) is not int or n <= 0 for n in size):
            raise ValueError(f"场尺寸必须为正整数元组: {size!r}")
        self.size = tuple(size)
        total = 1
        for extent in self.size:
            total *= extent
        self._values: list[int | float] = [fill] * total

    def index(self, coords: tuple[int, ...]) -> int:
        """坐标 → 扁平下标；维数不符即拒绝。"""
        if len(coords) != len(self.size):
            raise ValueError(
                f"坐标维数不符: {coords!r} vs {self.size!r}"
            )
        index = 0
        for coord, extent in zip(coords, self.size):
            index = index * extent + coord
        return index

    def coordinates(self) -> list[tuple[int, ...]]:
        """全部坐标（行主序）。"""
        result: list[tuple[int, ...]] = [()]
        for extent in self.size:
            result = [
                (*prefix, value)
                for prefix in result
                for value in range(extent)
            ]
        return result

    def get(self, coords: tuple[int, ...]) -> int | float:
        value = self._values[self.index(coords)]
        return value

    def set(self, coords: tuple[int, ...], value: int | float) -> None:
        self._values[self.index(coords)] = value

    def copy(self) -> "LatticeField":
        clone = LatticeField(self.size)
        clone._values = list(self._values)
        return clone

    def values(self) -> tuple[int | float, ...]:
        return tuple(self._values)

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, LatticeField):
            return NotImplemented
        return self.size == other.size and self._values == other._values

    def __repr__(self) -> str:
        return f"LatticeField(size={self.size!r}, values={self._values!r})"

    def export(self) -> dict[str, object]:
        """规范载荷（快照/摘要用）。"""
        return {"size": list(self.size), "values": list(self._values)}

    @classmethod
    def load(cls, payload: Mapping[str, object]) -> "LatticeField":
        size = tuple(int(n) for n in payload["size"])  # type: ignore[arg-type]
        field = cls(size)
        field._values = list(payload["values"])  # type: ignore[arg-type]
        return field


class DynamicField:
    """稀疏动态场：``{坐标: 值}``，只存已物化实例（chunk 流式物化）。"""

    def __init__(
        self,
        values: Mapping[tuple[int, ...], object] | None = None,
    ) -> None:
        self._values: dict[tuple[int, ...], object] = dict(values or {})

    def get(self, coords: tuple[int, ...]) -> object:
        return self._values[coords]

    def set(self, coords: tuple[int, ...], value: object) -> None:
        self._values[coords] = value

    def contains(self, coords: tuple[int, ...]) -> bool:
        return coords in self._values

    def items(self) -> tuple[tuple[tuple[int, ...], object], ...]:
        return tuple(self._values.items())

    def coords(self) -> tuple[tuple[int, ...], ...]:
        return tuple(sorted(self._values))

    def values(self) -> tuple[object, ...]:
        return tuple(self._values[coords] for coords in self.coords())

    def copy(self) -> "DynamicField":
        return DynamicField(self._values)

    def merged(self, other: "DynamicField") -> "DynamicField":
        """已提交视图叠加影子写入（读语义）。"""
        combined = dict(self._values)
        combined.update(other._values)
        return DynamicField(combined)

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, DynamicField):
            return NotImplemented
        return self._values == other._values

    def __repr__(self) -> str:
        return f"DynamicField({self._values!r})"

    def export(self) -> dict[str, object]:
        return {
            "kind": "dynamic",
            "entries": [
                [list(coords), self._values[coords]]
                for coords in self.coords()
            ],
        }

    @classmethod
    def load(cls, payload: Mapping[str, object]) -> "DynamicField":
        return cls(
            {
                tuple(int(part) for part in coords): value
                for coords, value in payload["entries"]  # type: ignore[union-attr]
            }
        )


def export_value(value: object) -> object:
    """槽位值 → 规范载荷（标量原样；场转显式结构）。"""
    if isinstance(value, LatticeField):
        return {"kind": "lattice", **value.export()}
    if isinstance(value, DynamicField):
        return value.export()
    return value


def load_value(payload: object) -> object:
    """规范载荷 → 槽位值（:func:`export_value` 的逆）。"""
    if isinstance(payload, Mapping) and payload.get("kind") == "lattice":
        data = dict(payload)
        data.pop("kind", None)
        return LatticeField.load(data)
    if isinstance(payload, Mapping) and payload.get("kind") == "dynamic":
        return DynamicField.load(payload)
    return payload


class StateStore:
    """状态/派生槽位的已提交值与帧内影子（含物化集合）。"""

    def __init__(self, max_lag: int = 0) -> None:
        if type(max_lag) is not int or max_lag < 0:
            raise ValueError(f"历史深度必须为非负整数: {max_lag!r}")
        self._committed: dict[str, object] = {}
        self._shadow: dict[str, object] = {}
        self._history: dict[str, object] = {}
        self._max_lag = max_lag
        self._materialized: dict[str, set[tuple[int, ...]]] = {}

    # ── 物化（视图，不是世界语义）───────────────────────────────

    def materialize(self, kind: str, coords: tuple[int, ...]) -> None:
        self._materialized.setdefault(kind, set()).add(tuple(coords))

    def dematerialize(self, kind: str, coords: tuple[int, ...]) -> None:
        self._materialized.get(kind, set()).discard(tuple(coords))
        for slot_id, field in list(self._committed.items()):
            if isinstance(field, DynamicField) and field.contains(coords):
                remaining = dict(field.items())
                remaining.pop(coords, None)
                self._committed[slot_id] = DynamicField(remaining)
        for slot_id, shadow in list(self._shadow.items()):
            if isinstance(shadow, DynamicField) and shadow.contains(coords):
                remaining = dict(shadow.items())
                remaining.pop(coords, None)
                self._shadow[slot_id] = DynamicField(remaining)

    def materialized(self, kind: str) -> tuple[tuple[int, ...], ...]:
        return tuple(sorted(self._materialized.get(kind, ())))

    def materialized_sets(self) -> dict[str, list[list[int]]]:
        """物化集合的规范载荷（JSON 友好：坐标列表）。"""
        return {
            kind: [list(coords) for coords in sorted(coords_set)]
            for kind, coords_set in self._materialized.items()
            if coords_set
        }

    def restore_materialized(
        self,
        payload: Mapping[str, object],
    ) -> None:
        self._materialized = {
            kind: {tuple(int(part) for part in coords) for coords in items}
            for kind, items in payload.items()
        }

    def ensure_value(
        self,
        slot_id: str,
        coords: tuple[int, ...],
        default: object,
    ) -> None:
        """动态槽位在实例物化时补初值（state/derived 用）。

        历史同 ``initialize`` 以初值预热：新物化实例同样满足"自始如此"
        的稳态假设，``lag≥2`` 的机制首帧即可读取。
        """
        field = self._committed.get(slot_id)
        if not isinstance(field, DynamicField):
            field = DynamicField()
            self._committed[slot_id] = field
        if not field.contains(coords):
            field.set(coords, default)
            if self._max_lag >= 1:
                history = self._history.get(slot_id)
                if not isinstance(history, dict):
                    history = {}
                    self._history[slot_id] = history
                history[coords] = deque(
                    [default] * self._max_lag, maxlen=self._max_lag,
                )

    # ── 读写 ────────────────────────────────────────────────────

    def initialize(self, values: Mapping[str, object]) -> None:
        """装载初始已提交值（覆盖式；运行前调用一次）。

        历史以初始值预热 ``max_lag`` 份：世界"自始如此"的稳态假设，
        使 ``lag≥2`` 的机制在首帧即可读取。已提交值视为不可变：写者必须
        产出新值，不得原地修改。
        """
        self._committed = dict(values)
        self._shadow.clear()
        self._history.clear()
        self._prefill_history()

    def _prefill_history(self) -> None:
        if self._max_lag < 1:
            return
        for slot_id, value in self._committed.items():
            if isinstance(value, DynamicField):
                self._history[slot_id] = {
                    coords: deque(
                        [item] * self._max_lag, maxlen=self._max_lag,
                    )
                    for coords, item in value.items()
                }
                continue
            history: deque[object] = deque(maxlen=self._max_lag)
            for _ in range(self._max_lag):
                history.append(value)
            self._history[slot_id] = history

    def read(self, slot_id: str, lag: int = 0) -> object:
        """读槽位值；动态槽位返回合并视图（逐实例读用 ``read_at``）。"""
        if lag < 0:
            raise ValueError(f"lag 必须为非负整数: {lag!r}")
        if lag == 0:
            committed = self._committed[slot_id]
            shadow = self._shadow.get(slot_id)
            if shadow is None:
                return committed
            if isinstance(committed, DynamicField):
                return committed.merged(shadow)  # type: ignore[arg-type]
            return shadow
        if lag == 1:
            return self._committed[slot_id]
        history = self._history.get(slot_id)
        if isinstance(history, dict):
            raise ValueError("动态槽位请用 read_at 逐实例读取历史")
        try:
            return history[-(lag - 1)]  # type: ignore[index]
        except (TypeError, IndexError):
            raise IndexError(
                f"槽位 {slot_id} 缺少 lag={lag} 所需的历史深度"
            ) from None

    def read_at(
        self,
        slot_id: str,
        coords: tuple[int, ...],
        lag: int = 0,
    ) -> object:
        """动态槽位逐实例读取（lag 语义同 ``read``）。"""
        if lag == 0:
            shadow = self._shadow.get(slot_id)
            if isinstance(shadow, DynamicField) and shadow.contains(coords):
                return shadow.get(coords)
            return self._committed[slot_id].get(coords)  # type: ignore[union-attr]
        if lag == 1:
            return self._committed[slot_id].get(coords)  # type: ignore[union-attr]
        history = self._history.get(slot_id)
        try:
            return history[coords][-(lag - 1)]  # type: ignore[index]
        except (TypeError, KeyError, IndexError):
            raise IndexError(
                f"槽位 {slot_id} 实例 {coords!r} 缺少 lag={lag} 历史"
            ) from None

    def write(self, slot_id: str, value: object) -> None:
        """写影子（提交前不可见）。"""
        self._shadow[slot_id] = value

    def write_at(
        self,
        slot_id: str,
        coords: tuple[int, ...],
        value: object,
    ) -> None:
        """动态槽位逐实例写影子。"""
        field = self._shadow.get(slot_id)
        if not isinstance(field, DynamicField):
            field = DynamicField()
            self._shadow[slot_id] = field
        field.set(coords, value)

    def commit(self) -> dict[str, object]:
        """一次性应用全部影子写入；返回本次写入集。"""
        writes = dict(self._shadow)
        for slot_id, value in writes.items():
            if isinstance(value, DynamicField):
                self._commit_dynamic(slot_id, value)
                continue
            if self._max_lag >= 1:
                history = self._history.setdefault(
                    slot_id, deque(maxlen=self._max_lag)
                )
                if slot_id in self._committed:
                    history.append(self._committed[slot_id])  # type: ignore[union-attr]
                self._committed[slot_id] = value
            else:
                self._committed[slot_id] = value
        self._shadow.clear()
        return writes

    def _commit_dynamic(self, slot_id: str, shadow: DynamicField) -> None:
        committed = self._committed.get(slot_id)
        if not isinstance(committed, DynamicField):
            committed = DynamicField()
        if self._max_lag >= 1:
            history = self._history.get(slot_id)
            if not isinstance(history, dict):
                history = {}
                self._history[slot_id] = history
            for coords, value in shadow.items():
                entries = history.setdefault(
                    coords, deque(maxlen=self._max_lag)
                )
                if committed.contains(coords):
                    entries.append(committed.get(coords))
                committed.set(coords, value)
        else:
            for coords, value in shadow.items():
                committed.set(coords, value)
        self._committed[slot_id] = committed

    def abort(self) -> None:
        """丢弃影子写入（状态不变）。"""
        self._shadow.clear()

    def committed(self, slot_id: str) -> object:
        """读已提交值（忽略影子）。"""
        return self._committed[slot_id]

    def snapshot(self, slot_ids: tuple[str, ...]) -> dict[str, object]:
        """已提交状态快照（只含给定槽位）+ lag 历史（WC-7.5 检查点充分）。

        ``lag≥2`` 的机制读取历史；历史不进快照会使读档后的轨迹与不中断
        运行分叉。历史载荷有界：每槽位至多 ``max_lag`` 个值。
        """
        states = {
            slot_id: export_value(self._committed[slot_id])
            for slot_id in slot_ids
        }
        history: dict[str, object] = {}
        for slot_id in slot_ids:
            entries = self._history.get(slot_id)
            if isinstance(entries, dict):
                history[slot_id] = {
                    "kind": "dynamic",
                    "entries": [
                        [list(coords), list(values)]
                        for coords, values in sorted(entries.items())
                    ],
                }
            elif entries:
                history[slot_id] = {
                    "kind": "scalar",
                    "values": list(entries),
                }
        return {"states": states, "history": history}

    def restore(
        self,
        payload: Mapping[str, object],
        history: Mapping[str, object] | None = None,
    ) -> None:
        """从快照恢复（历史按载荷重建；影子必须为空）。"""
        self._committed = {
            slot_id: load_value(value)
            for slot_id, value in payload.items()
        }
        self._shadow.clear()
        self._history.clear()
        self._prefill_history()
        for slot_id, entry in dict(history or {}).items():
            if entry.get("kind") == "scalar":  # type: ignore[union-attr]
                self._history[slot_id] = deque(
                    entry["values"],  # type: ignore[index]
                    maxlen=self._max_lag,
                )
            else:
                self._history[slot_id] = {
                    tuple(int(part) for part in coords): deque(
                        values, maxlen=self._max_lag,
                    )
                    for coords, values in entry["entries"]  # type: ignore[index]
                }
