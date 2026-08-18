"""世界树 — 连接所有模块的骨干。

使用方式:
    from ascend.world_tree import world_tree, Event, AffectedParty

    world_tree.subscribe("weather_change", handle_weather)
    world_tree.publish(Event(...))

事件 data 契约由各领域的事件类声明（见 WorldEvent 子类），
发布时经 as_dict() 产出 JSON 安全 dict。
"""

from .affected import AffectedParty
from .archive import EventArchive
from .event import Event, LocationFilter, WorldEvent
from .graph import EventGraph
from .tree import WorldTree

# 模块级世界树单例，各模块通过此实例通信
world_tree = WorldTree()

__all__ = ["world_tree", "Event", "LocationFilter", "AffectedParty",
           "WorldEvent", "EventGraph", "WorldTree", "EventArchive"]
