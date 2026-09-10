"""完整世界状态序列化 — 时钟、玩家、干预表、注入核与格式版本。

存档是状态通道（世界外元操作）：不产生历史、不进因果图。本模块只做
纯函数转换，不依赖 GameEngine——由调用方传入各子系统实例，避免循环依赖。

**完整状态**（issue #46 P4，世界验收协议 W4）只包含"无法由世界设置重算"
的部分：

| 分量 | 处置 | 原因 |
| --- | --- | --- |
| 时钟时间/速度/暂停 | 落盘 | 世界时间不可重算 |
| 玩家实体与位置 | 落盘 | 实体状态不可重算 |
| 干预表（生效记录） | 落盘 | 研究者施加的替换，不在声明内 |
| 注入的特征核 | 落盘 | 研究者施加的运行时核，非 seed 派生 |
| 自然特征核时间线 | 重建 | 由 seed + 块坐标 + 段索引确定性派生 |
| 天气参数/区域跟踪器 | 重建 | 由 seed + 时钟 + 声明确定性重算 |
| 区块与事件归档 | 各自通道 | chunks.db / events.db（本模块不碰） |

天气侧两项由 ``WeatherEngine.persist_state`` / ``restore_state`` 提供
（本模块只做搬运，不认识天气内部结构）。

漏掉任何一项，读档后的未来轨迹就可能与未存档的对照世界分叉；把可重算量
也存进来，则会让"删除缓存并由状态重算"这条纪律失去检验意义。

读档时钟对齐规则: ``time = max(存档 game_time, 归档最新事件时间戳)``，
防止恢复的世界"时间倒流"——事件归档实时落盘，可能比 state 更新。
"""

from __future__ import annotations

import math
from typing import Mapping

# 状态载荷格式版本。**无向后兼容**：读档只接受本版本，旧格式与未来格式
# 一律拒绝（fail-closed），不做静默兜底——"看起来能跑"比拒绝加载更危险。
STATE_VERSION: int = 1


def collect_state(
    clock,
    player_service,
    weather_engine,
    archive_max_timestamp,
) -> dict:
    """采集完整世界状态 W_t（保存脉搏的 state 载荷）。

    Args:
        clock: WorldClock 实例。
        player_service: PlayerService 实例。
        weather_engine: WeatherEngine 实例（可为 None：无天气的测试场景）。
        archive_max_timestamp: 事件归档内最新事件时间戳，读档时钟对齐用。

    Returns:
        可 JSON 序列化的状态字典（``state_version`` 为当前格式版本）。
    """
    x, y = player_service.position
    entity = player_service.entity
    return {
        "state_version": STATE_VERSION,
        "clock": {
            "time": clock.time,
            "speed": clock.speed,
            "paused": clock.paused,
        },
        "player": {
            "entity_id": entity.id if entity else None,
            "x": x,
            "y": y,
        },
        "weather": (
            weather_engine.persist_state()
            if weather_engine is not None
            else {"interventions": [], "feature_cores": []}
        ),
        "archive_max_timestamp": int(archive_max_timestamp or 0),
    }


def require_state_version(state: Mapping) -> int:
    """校验状态载荷版本并返回它（fail-closed）。

    读档前调用：版本字段缺失、非整数或与 :data:`STATE_VERSION` 不符时
    抛 ValueError。旧档没有该字段即"未声明格式"，属于必须拒绝的输入。
    """
    if not isinstance(state, Mapping):
        raise ValueError(f"状态载荷必须为映射: {type(state).__name__}")
    raw = state.get("state_version")
    if raw is None:
        raise ValueError("状态载荷缺少 state_version（旧格式存档，不支持）")
    if not isinstance(raw, int) or isinstance(raw, bool):
        raise ValueError(f"state_version 必须为整数: {raw!r}")
    if raw != STATE_VERSION:
        raise ValueError(
            f"状态格式版本不符: 存档 {raw}，当前支持 {STATE_VERSION}"
        )
    return raw


def aligned_time(state: dict) -> int:
    """读档时钟对齐：max(state 时钟, 归档最新事件时间戳)。

    事件实时落盘（trim 写归档），可能比周期写入的 state 更新；
    恢复时钟取两者较大值，防止世界时间倒流。

    Args:
        state: collect_state 输出的状态字典。

    Returns:
        对齐后的世界时间（tick）。
    """
    clock = state.get("clock") or {}
    return max(
        int(clock.get("time", 0) or 0),
        int(state.get("archive_max_timestamp", 0) or 0),
    )


def apply_state(
    state: dict, clock, player_service, weather_engine=None,
    *, instance_loader=None,
) -> None:
    """把完整状态恢复到各子系统（读档路径）。

    版本校验在前：``state_version`` 不符时立即拒绝，不产生任何副作用。
    干预表与注入核的恢复各自 fail-closed（逐条重新校验，见
    ``InterventionTable.restore`` / ``FeatureField.restore_injected``）。

    Args:
        state: collect_state 输出的状态字典（或从存档解密的结果）。
        clock: WorldClock 实例（未启动）。
        player_service: PlayerService 实例（未 birth）。
        weather_engine: WeatherEngine 实例；None = 无天气状态可恢复。
        instance_loader: 可选实例装载器 ``(节点, 实例) -> 是否可用``；
            读档时把被 LRU 淘汰的干预目标 chunk 拉回来再校验
            （世界接线方提供，见 ``GameEngine._ensure_intervention_instance``）。

    Raises:
        ValueError: 版本不符或任一状态字段非法。
    """
    require_state_version(state)
    apply_clock(state, clock)
    apply_player(state, player_service)
    if weather_engine is not None:
        weather_engine.restore_state(
            state.get("weather") or {"interventions": [], "feature_cores": []},
            instance_loader=instance_loader,
        )


def apply_clock(state: dict, clock) -> None:
    """恢复时钟（读档用，不触发任何回调）。

    日历无需单独恢复——它由时钟派生（GameCalendar(clock) 在引擎
    启动时以恢复后的 epoch 创建）。

    Args:
        state: collect_state 输出的字典（或从存档解密的结果）。
        clock: WorldClock 实例（未启动）。

    Raises:
        ValueError: 时钟字段非法。
    """
    clock_state = state.get("clock") or {}
    time = int(_validate_finite(clock_state.get("time", 0), "clock.time"))
    if time < 0:
        raise ValueError(f"非法时钟时间: {time}")
    clock.restore(
        time=time,
        speed=_validate_finite(clock_state.get("speed", 1.0), "clock.speed"),
        paused=bool(clock_state.get("paused", False)),
    )


def apply_player(state: dict, player_service) -> None:
    """静默恢复玩家实体（读档用，不发布 entity_born / 移动事件）。

    Args:
        state: collect_state 输出的字典（或从存档解密的结果）。
        player_service: PlayerService 实例（未 birth）。

    Raises:
        ValueError: 玩家字段非法（NaN/Inf）。
    """
    player = state.get("player") or {}
    entity_id = player.get("entity_id")
    if entity_id:
        player_service.restore(
            entity_id,
            _validate_finite(player.get("x", 0.0), "player.x"),
            _validate_finite(player.get("y", 0.0), "player.y"),
        )


def _validate_finite(value, name: str) -> float:
    """校验数值为有限浮点（NaN 熔断：NaN 恒 False 的比较会绕过 < 0 校验）。"""
    v = float(value)
    if not math.isfinite(v):
        raise ValueError(f"非法数值 {name}: {value!r}")
    return v
