"""世界状态容器 — 已提交值、帧内影子与 lag 历史（WC-3.5 / WC-7.6）。

- :class:`LatticeField`：定尺寸离散场（P0 小世界；P1 扩展 chunk 流式
  物化与内核绑定）；
- :class:`StateStore`：state/derived 槽位的已提交值 + 帧内影子 + lag 历史。
  **提交原子**：``commit`` 一次性应用全部影子写入；**回滚干净**：``abort``
  丢弃影子；**快照只含已提交值**（派生值不落盘，WC-8.1）。

lag 语义（与 ``WorldProgram`` 文档一致）：``lag=0`` 读本帧更早的写入
（影子），没有则读帧初值；``lag=1`` 读帧初值；``lag=k≥2`` 读 k−1 帧前的
帧初值（需要历史深度，编译期 ``max_lag`` 决定）。
"""

from __future__ import annotations

from collections import deque
from typing import Mapping

__all__ = ["LatticeField", "StateStore"]


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


def export_value(value: object) -> object:
    """槽位值 → 规范载荷（标量原样；场转显式结构）。"""
    if isinstance(value, LatticeField):
        return {"kind": "lattice", **value.export()}
    return value


def load_value(payload: object) -> object:
    """规范载荷 → 槽位值（:func:`export_value` 的逆）。"""
    if isinstance(payload, Mapping) and payload.get("kind") == "lattice":
        data = dict(payload)
        data.pop("kind", None)
        return LatticeField.load(data)
    return payload


class StateStore:
    """state/derived 槽位的已提交值与帧内影子。"""

    def __init__(self, max_lag: int = 0) -> None:
        if type(max_lag) is not int or max_lag < 0:
            raise ValueError(f"历史深度必须为非负整数: {max_lag!r}")
        self._committed: dict[str, object] = {}
        self._shadow: dict[str, object] = {}
        self._history: dict[str, deque[object]] = {}
        self._max_lag = max_lag

    def initialize(self, values: Mapping[str, object]) -> None:
        """装载初始已提交值（覆盖式；运行前调用一次）。

        历史以初始值预热 ``max_lag`` 份：世界"自始如此"的稳态假设，
        使 ``lag≥2`` 的机制在首帧即可读取（否则前若干帧必然失败）。
        已提交值视为不可变：写者必须产出新值，不得原地修改。
        """
        self._committed = dict(values)
        self._shadow.clear()
        self._history.clear()
        self._prefill_history()

    def _prefill_history(self) -> None:
        if self._max_lag < 1:
            return
        for slot_id, value in self._committed.items():
            history: deque[object] = deque(maxlen=self._max_lag)
            for _ in range(self._max_lag):
                history.append(value)
            self._history[slot_id] = history

    def read(self, slot_id: str, lag: int = 0) -> object:
        """读槽位值；lag 语义见模块文档。"""
        if lag < 0:
            raise ValueError(f"lag 必须为非负整数: {lag!r}")
        if lag == 0:
            if slot_id in self._shadow:
                return self._shadow[slot_id]
            return self._committed[slot_id]
        if lag == 1:
            return self._committed[slot_id]
        history = self._history.get(slot_id)
        try:
            return history[-(lag - 1)]  # type: ignore[index]
        except (TypeError, IndexError):
            raise IndexError(
                f"槽位 {slot_id} 缺少 lag={lag} 所需的历史深度"
            ) from None

    def write(self, slot_id: str, value: object) -> None:
        """写影子（提交前不可见）。"""
        self._shadow[slot_id] = value

    def commit(self) -> dict[str, object]:
        """一次性应用全部影子写入；返回本次写入集。"""
        writes = dict(self._shadow)
        if self._max_lag >= 1:
            for slot_id, value in writes.items():
                history = self._history.setdefault(
                    slot_id, deque(maxlen=self._max_lag)
                )
                if slot_id in self._committed:
                    history.append(self._committed[slot_id])
                self._committed[slot_id] = value
        else:
            self._committed.update(writes)
        self._shadow.clear()
        return writes

    def abort(self) -> None:
        """丢弃影子写入（状态不变）。"""
        self._shadow.clear()

    def committed(self, slot_id: str) -> object:
        """读已提交值（忽略影子）。"""
        return self._committed[slot_id]

    def snapshot(self, slot_ids: tuple[str, ...]) -> dict[str, object]:
        """已提交状态快照（只含给定槽位）。"""
        return {
            slot_id: export_value(self._committed[slot_id])
            for slot_id in slot_ids
        }

    def restore(self, payload: Mapping[str, object]) -> None:
        """从快照恢复（历史以恢复值预热；影子必须为空）。"""
        self._committed = {
            slot_id: load_value(value)
            for slot_id, value in payload.items()
        }
        self._shadow.clear()
        self._history.clear()
        self._prefill_history()
