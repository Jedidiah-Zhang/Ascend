"""地形状态内容数据 — 状态注册表与内核参数表（读 ``data/terrain.json``）。

内容仍是数据：改 ``terrain.json`` 不改代码。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping

from ascend.data import load_content

__all__ = ["STATE_TYPES", "StateConfig", "build_param_tables", "state_keys"]


@dataclass(frozen=True, slots=True)
class StateConfig:
    """单个地形状态通道的注册项（值域 + 内核激活门限）。"""

    key: str
    bounds: tuple[int, int]
    precip_trigger: str | None = None
    freeze_below: float | None = None
    melt_above: float | None = None


# 状态注册表（顺序 = 载荷布局契约）
STATE_TYPES: dict[str, StateConfig] = {
    "moisture": StateConfig("moisture", (0, 100), precip_trigger="rain"),
    "snow": StateConfig(
        "snow", (0, 255), precip_trigger="snow", melt_above=0.0,
    ),
    "ice": StateConfig(
        "ice", (0, 255), freeze_below=0.0, melt_above=0.0,
    ),
}


def state_keys() -> tuple[str, ...]:
    """状态注册顺序（blob 布局与前端 STATE_KEYS 的契约基准）。"""
    return tuple(STATE_TYPES)


def build_param_tables() -> tuple[
    list[float], list[float], list[float], list[float],
    list[float], list[float], list[float],
]:
    """``data/terrain.json`` → 内核参数表（纯数据，只读复用）。

    Returns:
        (deposit, drain, melt, freeze, freeze_below, melt_above, state_max)；
        前四表为 n_states × 256（terrain id 索引，不适用组合系数全 0 →
        delta 恒 0，内核无需适用性分支），后三表为 n_states。
    """
    doc = load_content("terrain")
    raw_map = doc.get("terrain")
    if not isinstance(raw_map, Mapping) or not raw_map:
        raise ValueError("data/terrain.json: 缺少 terrain 注册表")
    count = len(STATE_TYPES)
    deposit = [0.0] * (count * 256)
    drain = [0.0] * (count * 256)
    melt = [0.0] * (count * 256)
    freeze = [0.0] * (count * 256)
    for entry in raw_map.values():
        value = int(entry["value"])
        states = entry.get("states", {})
        for index, key in enumerate(state_keys()):
            params = states.get(key)
            if not params:
                continue
            base = index * 256 + value
            deposit[base] = float(params.get("deposit", 0.0))
            drain[base] = float(params.get("drain", 0.0))
            melt[base] = float(params.get("melt", 0.0))
            freeze[base] = float(params.get("freeze", 0.0))
    freeze_below = [
        config.freeze_below if config.freeze_below is not None else -99999.0
        for config in STATE_TYPES.values()
    ]
    melt_above = [
        config.melt_above or 0.0 for config in STATE_TYPES.values()
    ]
    state_max = [
        float(config.bounds[1]) for config in STATE_TYPES.values()
    ]
    return deposit, drain, melt, freeze, freeze_below, melt_above, state_max
