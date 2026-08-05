"""存档状态序列化 — 时钟/玩家/天气状态与 dict 的互转。

存档是状态通道（世界外元操作）：不产生历史、不进因果图。
本模块只做纯函数转换，不依赖 GameEngine——由 GameEngine 传入
各子系统实例，避免循环依赖。

读档时钟对齐规则（设计文档）:
    time = max(存档 game_time, 归档最新事件时间戳 archive_max_timestamp)
    防止恢复的世界"时间倒流"——事件归档实时落盘，可能比 state 更新。
"""




def collect_state(clock, player_service, weather_engine, archive_max_timestamp) -> dict:
    """从各子系统采集可持久化的世界状态。

    Args:
        clock: WorldClock 实例。
        player_service: PlayerService 实例。
        weather_engine: WeatherEngine 实例（可为 None）。
        archive_max_timestamp: 事件归档内最新事件时间戳，读档时钟对齐用。

    Returns:
        可 JSON 序列化的状态字典。

    注：天气状态不序列化——由 manifest.seed + 恢复后的时钟确定性重建
    （设计文档：WeatherEngine(clock, seed)）。
    """
    x, y = player_service.position
    entity = player_service.entity
    return {
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
        "archive_max_timestamp": int(archive_max_timestamp or 0),
    }


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
    time = int(clock_state.get("time", 0))
    if time < 0:
        raise ValueError(f"非法时钟时间: {time}")
    clock.restore(
        time=time,
        speed=float(clock_state.get("speed", 1.0)),
        paused=bool(clock_state.get("paused", False)),
    )


def apply_player(state: dict, player_service) -> None:
    """静默恢复玩家实体（读档用，不发布 entity_born / 移动事件）。

    Args:
        state: collect_state 输出的字典（或从存档解密的结果）。
        player_service: PlayerService 实例（未 birth）。
    """
    player = state.get("player") or {}
    entity_id = player.get("entity_id")
    if entity_id:
        player_service.restore(
            entity_id,
            float(player.get("x", 0.0)),
            float(player.get("y", 0.0)),
        )


def apply_state(state: dict, clock, player_service) -> None:
    """把状态恢复到时钟与玩家服务（apply_clock + apply_player 的组合）。

    Args:
        state: collect_state 输出的字典（或从存档解密的结果）。
        clock: WorldClock 实例（未启动）。
        player_service: PlayerService 实例（未 birth）。

    Raises:
        ValueError: 状态字段非法。
    """
    apply_clock(state, clock)
    apply_player(state, player_service)
