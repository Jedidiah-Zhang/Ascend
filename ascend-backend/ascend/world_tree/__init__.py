"""世界树 — 连接所有模块的骨干。

使用方式:
    from ascend.world_tree import world_tree, Event, AffectedParty

    world_tree.subscribe("weather_change", handle_weather)
    world_tree.publish(Event(...))
"""

from .affected import AffectedParty
from .archive import EventArchive
from .event import Event
from .graph import EventGraph
from .registry import SchemaRegistry
from .schema import EventSchema
from .tree import WorldTree

# 模块级世界树单例，各模块通过此实例通信
# validate=False：内部事件全由代码构造，类型正确性由测试+类型标注保证，
# 运行时校验是纯开销。外部输入（网络/脚本）需验证时在入口处显式校验。
world_tree = WorldTree(validate=False)

__all__ = ["world_tree", "Event", "AffectedParty", "EventGraph",
           "WorldTree", "EventArchive", "EventSchema",
           "SchemaRegistry"]
