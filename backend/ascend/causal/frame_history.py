"""跨帧历史窗口 — lag≥1 父引用的统一解析（WC-4.2；issue #49）。

节点在帧 ``f`` 的取值是帧末状态；父引用 ``lag=L`` 读 ``f−L`` 的帧末
状态（``lag=0`` 读本帧已求值值）。首帧之前的读取按 ``initial`` 稳态
取值（lag=1 的既有语义向任意 lag 推广：初始快照视为既往帧的静默
历史）；已求值帧的窗口只保留最近 ``max_lag+1`` 个——三个执行路径
（参考解释器 / 研究切片执行器 / 空间参考）共用本模块，避免各自解释
lag 语义，也避免把 lag>1 静默当作 lag=1。

只保留最近 ``max_lag+1`` 个帧状态；``max_lag`` 由声明的最大父 lag
决定，声明增长即窗口增长，无上限假设。
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any


class FrameHistory:
    """按绝对帧号索引的帧末状态窗口。

    Args:
        first_frame: 将要推演的第一帧（帧号）。
        initial: 首帧之前（``first_frame−1``）的状态快照。
        max_lag: 声明的最大父 lag（窗口保留 ``max_lag+1`` 个状态）。

    Raises:
        ValueError: ``max_lag`` 为负。
    """

    __slots__ = ("_states", "_max_lag", "_first_frame", "_initial")

    def __init__(
        self,
        first_frame: int,
        initial: Mapping[str, Any],
        *,
        max_lag: int,
    ) -> None:
        if type(max_lag) is not int or max_lag < 0:
            raise ValueError(f"max_lag 必须为非负整数: {max_lag!r}")
        self._max_lag = max_lag
        self._first_frame = first_frame
        self._initial: dict[str, Any] = dict(initial)
        self._states: dict[int, dict[str, Any]] = {
            first_frame - 1: dict(initial),
        }

    @property
    def max_lag(self) -> int:
        """窗口支持的最大 lag。"""
        return self._max_lag

    @property
    def earliest_frame(self) -> int:
        """最早已求值帧号（含 initial 的 ``first_frame−1``）。"""
        return min(self._states)

    def commit(self, frame: int, state: Mapping[str, Any]) -> None:
        """登记帧 ``frame`` 的帧末状态并修剪窗口外历史。"""
        self._states[frame] = dict(state)
        cutoff = frame - self._max_lag
        for key in [key for key in self._states if key < cutoff]:
            del self._states[key]

    def lookup(self, node_id: str, lag: int, frame: int) -> Any:
        """解析帧 ``frame`` 处 lag 父值（lag≥1）。

        首帧之前的读取按 ``initial`` 稳态取值（lag=1 的既有语义向任意
        lag 推广：初始快照视为既往帧的静默历史）。

        Raises:
            ValueError: lag 超出窗口/为负。
            KeyError: 历史帧缺节点值（fail-closed）。
        """
        if type(lag) is not int or lag < 1:
            raise ValueError(f"lookup 只接受 lag≥1: {lag!r}")
        if lag > self._max_lag:
            raise ValueError(
                f"lag={lag} 超出声明窗口 max_lag={self._max_lag}: {node_id}"
            )
        target = frame - lag
        if target < self._first_frame:
            if node_id not in self._initial:
                raise KeyError(
                    f"初始快照缺少父值: {node_id}（frame={frame}, lag={lag}）"
                )
            return self._initial[node_id]
        source = self._states.get(target)
        if source is None:
            raise KeyError(
                f"历史窗口不足: {node_id} lag={lag} frame={frame}"
                f"（最早已求值帧 {self.earliest_frame}）"
            )
        if node_id not in source:
            raise KeyError(
                f"历史帧缺少父值: {node_id} frame={target}"
            )
        return source[node_id]


__all__ = ["FrameHistory"]
