"""事件总线 — 连接所有模块的骨干。

使用方式:
    from ascend.bus import bus, Event, AffectedParty

    bus.subscribe("weather_change", handle_weather)
    bus.publish(Event(...))
"""

from .affected import AffectedParty
from .event import Event
from .graph import EventGraph
from .bus import EventBus

# 模块级总线单例，各模块通过此实例通信
bus = EventBus()

__all__ = ["bus", "Event", "AffectedParty", "EventGraph", "EventBus"]
