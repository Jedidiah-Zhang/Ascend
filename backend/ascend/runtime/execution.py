"""世界程序的运行时装配（runtime 层消费声明编译产物）。

调度器只接受程序里声明过的更新点：绑定缺失或多余一律拒绝
（fail-closed）——"执行权只来自声明"，不存在运行时偷偷注册的更新。
"""

from __future__ import annotations

from typing import Callable, Mapping


def apply_update_points(
    program,
    scheduler,
    bindings: Mapping[str, Callable[[int], None]],
) -> tuple[str, ...]:
    """把世界程序的更新点绑定到调度器（按声明顺序注册）。

    Args:
        program: ``WorldProgram``（结构接口：``update_points``，每项含
            ``id`` / ``period_ticks``）。
        scheduler: ``FrameScheduler``（``register(name, period=, callback=)``）。
        bindings: 更新点标识 → 推进回调 ``callback(now)``。

    Returns:
        按声明顺序注册的更新点标识。

    Raises:
        ValueError: 绑定与程序声明不匹配（缺少或多余）。
    """
    expected = [point.id for point in program.update_points]
    missing = sorted(set(expected) - set(bindings))
    extra = sorted(set(bindings) - set(expected))
    if missing or extra:
        raise ValueError(f"更新点绑定不匹配: 缺少={missing}, 多余={extra}")
    for point in program.update_points:
        scheduler.register(
            point.id,
            period=point.period_ticks,
            callback=bindings[point.id],
        )
    return tuple(expected)
