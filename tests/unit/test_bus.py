"""事件总线单元测试。"""

import pytest
from ascend.bus import Event, AffectedParty, EventBus, EventGraph


def make_event(timestamp=0.0, event_type="test", initiator_id="a",
               location=(0, 0, None, None), **kwargs) -> Event:
    affected = kwargs.pop("affected", None)
    if affected is None:
        affected = [AffectedParty(entity_id=initiator_id, role="subject")]
    return Event(
        timestamp=timestamp,
        location=location,
        initiator_type="system",
        initiator_id=initiator_id,
        affected=affected,
        event_type=event_type,
        **kwargs,
    )


class TestEventBus:
    def test_publish_and_subscribe(self):
        bus = EventBus()
        received = []
        bus.subscribe("weather_change", lambda e: received.append(e))
        ev = make_event(event_type="weather_change")
        bus.publish(ev)
        assert len(received) == 1
        assert received[0].id == ev.id

    def test_wildcard_subscription(self):
        bus = EventBus()
        received = []
        bus.subscribe("*", lambda e: received.append(e))
        bus.publish(make_event(event_type="weather_change"))
        bus.publish(make_event(event_type="npc_action"))
        assert len(received) == 2

    def test_unsubscribe(self):
        bus = EventBus()
        received = []
        unsub = bus.subscribe("test", lambda e: received.append(e))
        bus.publish(make_event())
        assert len(received) == 1
        unsub()
        bus.publish(make_event())
        assert len(received) == 1  # no second event

    def test_time_range_query(self):
        bus = EventBus()
        for t in range(5):
            bus.publish(make_event(timestamp=float(t)))
        results = bus.get_events_in_range(1.0, 3.0)
        assert len(results) == 3
        assert {e.timestamp for e in results} == {1.0, 2.0, 3.0}

    def test_time_range_with_filter(self):
        bus = EventBus()
        bus.publish(make_event(timestamp=0.0, event_type="rain"))
        bus.publish(make_event(timestamp=1.0, event_type="snow"))
        bus.publish(make_event(timestamp=2.0, event_type="rain"))
        results = bus.get_events_in_range(0.0, 2.0, event_type="rain")
        assert len(results) == 2

    def test_entity_index(self):
        bus = EventBus()
        ev = make_event(initiator_id="npc_1",
                        affected=[AffectedParty(entity_id="npc_2", role="witness")])
        bus.publish(ev)
        events_1 = bus.get_entity_events("npc_1", -1.0, 1.0)
        events_2 = bus.get_entity_events("npc_2", -1.0, 1.0)
        assert len(events_1) == 1
        assert len(events_2) == 1

    def test_spatial_query(self):
        bus = EventBus()
        bus.publish(make_event(location=(0, 0, None, None)))
        bus.publish(make_event(location=(1, 0, None, None)))
        bus.publish(make_event(location=(5, 5, None, None)))
        results = bus.get_events_in_region((0, 0), radius=1)
        assert len(results) == 2

    def test_event_count(self):
        bus = EventBus()
        assert bus.event_count == 0
        bus.publish(make_event())
        bus.publish(make_event())
        assert bus.event_count == 2


class TestEventGraph:
    def test_causal_chain(self):
        g = EventGraph()
        root = make_event(id="root")
        mid = make_event(id="mid", caused_by=["root"])
        leaf = make_event(id="leaf", caused_by=["mid"])
        g.add_event(root)
        g.add_event(mid)
        g.add_event(leaf)
        chain = g.get_causal_chain("leaf")
        assert chain == ["root", "mid"]

    def test_consequences(self):
        g = EventGraph()
        cause = make_event(id="cause")
        effect1 = make_event(id="effect1", caused_by=["cause"])
        effect2 = make_event(id="effect2", caused_by=["cause"])
        g.add_event(cause)
        g.add_event(effect1)
        g.add_event(effect2)
        cons = g.get_consequences("cause")
        assert set(cons) == {"effect1", "effect2"}

    def test_observers(self):
        g = EventGraph()
        rain = make_event(id="rain", event_type="weather_change")
        obs1 = make_event(id="obs1", event_type="observation", observes="rain")
        obs2 = make_event(id="obs2", event_type="observation", observes="rain")
        g.add_event(rain)
        g.add_event(obs1)
        g.add_event(obs2)
        observers = g.get_observers("rain")
        assert set(observers) == {"obs1", "obs2"}

    def test_has_path(self):
        g = EventGraph()
        a = make_event(id="a")
        b = make_event(id="b", caused_by=["a"])
        c = make_event(id="c", caused_by=["b"])
        for ev in [a, b, c]:
            g.add_event(ev)
        assert g.has_path("a", "c")
        assert not g.has_path("c", "a")

    def test_no_infinite_loop(self):
        g = EventGraph()
        a = make_event(id="a", caused_by=["b"])
        b = make_event(id="b", caused_by=["a"])
        g.add_edge("a", "b", "caused_by")
        g.add_edge("b", "a", "caused_by")
        # 不应死循环
        chain = g.get_causal_chain("a", max_depth=5)
        assert len(chain) <= 5


class TestModuleSingleton:
    def test_bus_singleton(self):
        from ascend.bus import bus
        assert isinstance(bus, EventBus)
        # 单例应该是同一个对象
        from ascend.bus import bus as bus2
        assert bus is bus2
