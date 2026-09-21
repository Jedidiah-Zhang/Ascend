"""世界装配 — 游戏进程的声明切片（身份载体 + 驱动周期）。

游戏进程的世界 = 天气引擎求值面（求值子集）+ 地形积分 + 驱动周期
（游戏分钟驱动天气推进、游戏小时驱动地形积分）。生成程序（大陆/水文/
瓦片）仍是声明边界，其身份由存档的生成指纹单独记录。

本装配是**存档身份与调度绑定的事实源**：manifest 记录它的声明视图与
程序视图（``declaration_settings`` / ``settings``），调度器只接受它
声明的周期键（``bind_periods``，fail-closed）。
"""

from __future__ import annotations

from ascend.config import GAME_HOUR, GAME_MINUTE
from ascend.world.compile import WorldProgram, compile_world
from ascend.world.meta.declarations import Schedule, WorldSpec
from ascend.world.modules import terrain
from ascend.world.modules.pipeline import PIPELINE_PHASES
from ascend.world.modules.weather import engine_inputs
from ascend.world.modules.weather.core import engine_eval_pack

__all__ = ["DRIVER_PERIODS", "build_game_program"]

#: 驱动周期键 → tick 数（声明顺序即注册顺序）。
DRIVER_PERIODS: tuple[tuple[str, int], ...] = (
    ("minute", GAME_MINUTE),
    ("hour", GAME_HOUR),
)


def build_game_program() -> WorldProgram:
    """编译游戏世界程序（天气求值子集 + 地形 + 驱动周期）。"""
    return compile_world(
        WorldSpec(
            modules=(
                engine_inputs.MODULE,
                engine_eval_pack(),
                terrain.MODULE,
            ),
            schedule=Schedule(
                phases=PIPELINE_PHASES,
                periods=DRIVER_PERIODS,
            ),
        )
    )
