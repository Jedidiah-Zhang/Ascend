"""玩家服务 — 后端权威玩家实体（游戏侧）。

实体运行时（Entity / EntityManager / 生命周期事件契约）在
``olam.adapters.entity``；本包只保留游戏侧的 PlayerService：
玩家输入权威、网络上报入口与传送。

用法:
    from miskhak.entity import PlayerService
"""

from .player import PlayerService

__all__ = ["PlayerService"]
