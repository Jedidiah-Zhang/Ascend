"""内置事件 Schema 注册。

集中定义项目中所有事件类型的 data 字段规范，
既是校验规则，也是活文档。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .tree import WorldTree


def register_all(world_tree: "WorldTree") -> None:
    """注册所有内置事件类型的 schema。

    在应用启动时调用一次，将项目中所有已知事件类型
    及其 data 字段的期望类型注册到 WorldTree。

    Args:
        world_tree: WorldTree 实例。
    """

    # ── 时间系统 (clock.py) ──────────────────────────

    world_tree.register_event_schema(
        "game_minute",
        required={
            "step": (int, float),       # 本次 tick 的时间步长（秒）
            "mode": str,                # 时钟模式键
            "tick_count": int,          # 累计 tick 次数
            "game_time": (int, float),  # 当前游戏时间（秒，可能为整数）
        },
        description="每 tick 发布一次，驱动日历等时间相关模块",
    )

    world_tree.register_event_schema(
        "time_skip",
        required={
            "skipped": (int, float),    # 跳过的时间量
            "game_time": (int, float),  # 跳转后的游戏时间
            "mode": str,                # 时钟模式键
            "tick_count": int,          # 累计 tick 次数
        },
        description="快进/跳转时发布，通知模块时间发生了跃迁",
    )

    # ── 日历系统 (calendar.py) ────────────────────────

    world_tree.register_event_schema(
        "day_end",
        required={
            "day": int,                 # 结束的日期编号
            "elapsed_days": int,        # 自起始日起累计经过的天数
        },
        description="每日结束时发布（day_change 之前），用于日终结算",
    )

    world_tree.register_event_schema(
        "day_change",
        required={
            "day": int,                 # 新的日期编号
            "previous_day": int,        # 前一天的日期编号
            "elapsed_days": int,        # 自起始日起累计经过的天数
            "day_change_count": int,    # 累计日期变更次数
        },
        description="日期变更时发布，触发群体/生态等日更模块",
    )

    world_tree.register_event_schema(
        "hour_change",
        required={
            "day": int,                 # 当前日期编号
            "hour": int,                # 新的小时数（0-23）
            "previous_hour": int,       # 前一小时
            "hour_change_count": int,   # 累计整点变更次数
        },
        description="整点变更时发布，用于高频定期任务",
    )

    # ── 实体管理 (manager.py) ─────────────────────────

    world_tree.register_event_schema(
        "entity_spawned",
        required={
            "entity_id": str,           # 实体唯一标识
            "entity_type": str,         # 实体类型名称
            "position": tuple,          # 生成位置 (chunk_x, chunk_y, tile_x?, tile_y?)
        },
        description="新实体生成时发布",
    )

    world_tree.register_event_schema(
        "entity_despawned",
        required={
            "entity_id": str,           # 实体唯一标识
            "entity_type": str,         # 实体类型名称
        },
        description="实体销毁时发布",
    )

    world_tree.register_event_schema(
        "entity_moved",
        required={
            "entity_id": str,           # 实体唯一标识
            "old_position": tuple,      # 移动前位置
            "new_position": tuple,      # 移动后位置
        },
        description="实体位置变更时发布",
    )

    # ── 事务系统补偿 (txn.py) ─────────────────────────
    # 补偿事件的 event_type 由 TxnTemplate 动态决定，
    # 不在此注册，由各事务模板自行定义。
