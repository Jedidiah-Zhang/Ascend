"""运行时层 — 帧调度与执行入口。

见 docs/研究理论/世界基座/12-世界树角色与帧调度.md、
docs/研究理论/世界基座/13-世界程序编译.md。
"""

from .execution import apply_update_points
from .frame_scheduler import FrameScheduler, UpdatePoint
from .state_store import FrameStateStore, WorldInvalidatedError
from .wave_executor import execute_waves

__all__ = [
    "FrameScheduler",
    "FrameStateStore",
    "UpdatePoint",
    "WorldInvalidatedError",
    "apply_update_points",
    "execute_waves",
]
