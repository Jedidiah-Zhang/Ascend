"""观测映射 — 主体观测通道（$G^i$ 的最小实现）。

研究日志（:mod:`ascend.causal.trace`）是**研究者通道**，含方程版本、父值、
随机地址与干预记录；智能体数据只能由已声明的观测映射从世界状态生成，
不得因研究日志存在而获得额外真值（第一阶段实施定义 §8）。

当前实现范围（诚实边界）：
- ``observe`` 按协议允许的节点读出世界状态并应用该协议的量化规则；
- 观测载荷只含"节点 ID → 值"，**不含**任何 trace/调试字段；
- "按 `AccessPolicy.observation_protocols` 自动收窄可见集"需要观测主体
  与协议元数据（视野、量化、噪声）先落地——见世界基座 11 篇 §3。
"""

from __future__ import annotations

from typing import Mapping

# 观测协议标识（与节点声明的 ``AccessPolicy.observation_protocols`` 同名）
RESEARCH_PROTOCOL: str = "research.full.v1"
AGENT_WEATHER_PROTOCOL: str = "agent.weather.v1"

#: 协议 → 量化小数位（None = 不量化，原值读出）
_PROTOCOL_QUANTIZATION: dict[str, int | None] = {
    RESEARCH_PROTOCOL: None,
    AGENT_WEATHER_PROTOCOL: 1,
}


def protocol_nodes(protocol: str, allow: tuple[str, ...]) -> tuple[str, ...]:
    """协议可见集的校验视图（调用方给出可见集，本函数只做形状校验）。"""
    if protocol not in _PROTOCOL_QUANTIZATION:
        raise ValueError(f"未声明的观测协议: {protocol}")
    return tuple(allow)


def observe(
    protocol: str,
    world: Mapping[str, object],
    *,
    allow: tuple[str, ...],
) -> dict[str, object]:
    """把世界状态映射为主体观测（$G^i$）。

    Args:
        protocol: 观测协议标识（决定量化规则）。
        world: 世界状态读出（节点 ID → 值）。
        allow: 该主体被允许读取的节点集合。

    Returns:
        观测载荷：节点 ID → 量化后的值。**只含允许节点**，且不含任何
        研究日志/调试字段（本函数只做白名单读出与量化）。

    Raises:
        ValueError: 协议未声明，或允许集合中出现未声明的节点。
    """
    if protocol not in _PROTOCOL_QUANTIZATION:
        raise ValueError(f"未声明的观测协议: {protocol}")
    digits = _PROTOCOL_QUANTIZATION[protocol]
    payload: dict[str, object] = {}
    for node_id in allow:
        if node_id not in world:
            continue
        value = world[node_id]
        if digits is not None and isinstance(value, float):
            value = round(value, digits)
        payload[node_id] = value
    return payload


def leaks_research_truth(payload: Mapping[str, object]) -> bool:
    """主体观测载荷是否含有研究通道专有的真值类型。

    研究通道专有的是**非标量结构**：方程版本、父值映射、随机地址列表、
    干预记录（dict）等。主体观测只能是"节点 → 标量"。
    """
    for value in payload.values():
        if isinstance(value, (dict, list, tuple, set)):
            return True
    return False


__all__ = [
    "AGENT_WEATHER_PROTOCOL",
    "RESEARCH_PROTOCOL",
    "leaks_research_truth",
    "observe",
    "protocol_nodes",
]
