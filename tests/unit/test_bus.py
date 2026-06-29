"""事件总线单元测试。"""

import threading

import pytest
from ascend.bus import Event, AffectedParty, EventBus, EventGraph


def make_event(timestamp=0.0, event_type="test", initiator_id="a",
               location=(0, 0, None, None), **kwargs) -> Event:
    affected = kwargs.pop("affected", None)
    if affected is None:
        affected = [AffectedParty(entity_id=initiator_id, role="subject")]
    initiator_type = kwargs.pop("initiator_type", "system")
    return Event(
        timestamp=timestamp,
        location=location,
        initiator_type=initiator_type,
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


# ── 事件校验 ──────────────────────────────────────────


class TestEventBusValidation:
    """事件校验测试。"""

    def test_empty_event_type(self):
        bus = EventBus()
        with pytest.raises(ValueError, match="事件类型不能为空"):
            bus.publish(make_event(event_type=""))

    def test_whitespace_event_type(self):
        bus = EventBus()
        with pytest.raises(ValueError, match="事件类型不能为空"):
            bus.publish(make_event(event_type="   "))

    def test_empty_initiator_id(self):
        bus = EventBus()
        with pytest.raises(ValueError, match="发起方 ID 不能为空"):
            bus.publish(make_event(initiator_id=""))

    def test_invalid_initiator_type(self):
        bus = EventBus()
        with pytest.raises(ValueError, match="无效的发起方类型"):
            bus.publish(make_event(initiator_type="alien"))

    def test_negative_timestamp(self):
        bus = EventBus()
        with pytest.raises(ValueError, match="时间戳不能为负"):
            bus.publish(make_event(timestamp=-1.0))

    def test_invalid_location_type(self):
        bus = EventBus()
        with pytest.raises(ValueError, match="位置格式无效"):
            bus.publish(make_event(location="not_a_tuple"))  # type: ignore[arg-type]

    def test_validation_can_be_disabled(self):
        bus = EventBus(validate=False)
        ev = make_event(event_type="")
        bus.publish(ev)  # 不抛异常
        assert bus.event_count == 1

    def test_valid_event_passes(self):
        bus = EventBus()
        ev = make_event()
        bus.publish(ev)  # 不抛异常
        assert bus.event_count == 1


# ── 事件生命周期 trim ────────────────────────────────


class TestEventBusTrim:
    """事件生命周期测试。"""

    def test_trim_basic(self):
        bus = EventBus()
        for t in [0, 1, 2, 3, 4]:
            bus.publish(make_event(timestamp=float(t)))

        removed = bus.trim(2.0)
        # _bisect_time(2.0, find_end=True) 返回第一个 ts > 2.0 的位置
        # ts 0,1,2 被移除, ts 3,4 保留
        assert removed == 3
        assert bus.event_count == 2

    def test_trim_removes_nothing(self):
        bus = EventBus()
        for t in range(5, 10):  # ts 5, 6, 7, 8, 9
            bus.publish(make_event(timestamp=float(t)))

        removed = bus.trim(2.0)  # 所有时间戳都 >= 5，不移除任何事件
        assert removed == 0
        assert bus.event_count == 5

    def test_trim_removes_everything(self):
        bus = EventBus()
        for t in range(5):
            bus.publish(make_event(timestamp=float(t)))

        removed = bus.trim(100.0)  # 全部移除
        assert removed == 5
        assert bus.event_count == 0

    def test_trim_empty_bus(self):
        bus = EventBus()
        removed = bus.trim(10.0)
        assert removed == 0

    def test_trim_rebuilds_entity_index(self):
        bus = EventBus()
        bus.publish(make_event(timestamp=0.0, initiator_id="npc_1"))
        bus.publish(make_event(timestamp=1.0, initiator_id="npc_2"))
        bus.publish(make_event(timestamp=10.0, initiator_id="npc_1"))

        bus.trim(5.0)  # 移除 ts=0,1 的两个事件

        events_npc1 = bus.get_entity_events("npc_1", 0, 100)
        assert len(events_npc1) == 1
        assert events_npc1[0].timestamp == 10.0

    def test_trim_rebuilds_spatial_index(self):
        bus = EventBus()
        bus.publish(make_event(timestamp=0.0, location=(0, 0, None, None)))
        bus.publish(make_event(timestamp=1.0, location=(5, 5, None, None)))
        bus.publish(make_event(timestamp=10.0, location=(0, 0, None, None)))

        bus.trim(5.0)

        region = bus.get_events_in_region((0, 0), radius=0)
        assert len(region) == 1
        assert region[0].timestamp == 10.0

    def test_trim_and_publish(self):
        bus = EventBus()
        for t in range(3):
            bus.publish(make_event(timestamp=float(t)))  # ts 0,1,2

        bus.trim(1.5)  # 移除 ts=0,1 (ts=2 保留)

        bus.publish(make_event(timestamp=5.0))  # 新事件

        results = bus.get_events_in_range(0, 10)
        assert len(results) == 2  # ts=2 + ts=5

    def test_trim_twice(self):
        bus = EventBus()
        for t in range(6):
            bus.publish(make_event(timestamp=float(t)))

        bus.trim(2.0)  # 移除 ts 0,1,2
        assert bus.event_count == 3  # ts 3,4,5

        bus.trim(4.0)  # 移除 ts 3,4
        assert bus.event_count == 1  # ts 5

    def test_trim_negative_time_raises(self):
        bus = EventBus()
        with pytest.raises(ValueError, match="清理时间不能为负"):
            bus.trim(-1.0)

    def test_trim_preserves_causal_chain(self):
        """Trim 后因果链仍可追溯。"""
        bus = EventBus()
        ev0 = make_event(timestamp=0.0, id="ev0")
        ev1 = make_event(timestamp=1.0, id="ev1", caused_by=["ev0"])
        ev2 = make_event(timestamp=10.0, id="ev2", caused_by=["ev1"])
        bus.publish(ev0)
        bus.publish(ev1)
        bus.publish(ev2)

        bus.trim(5.0)  # 移除 ev0, ev1 的事件体

        # 因果链仍然完整
        chain = bus.graph.get_causal_chain("ev2")
        assert chain == ["ev0", "ev1"]

        # ev2 的后果查询正常
        consequences = bus.graph.get_consequences("ev1")
        assert "ev2" in consequences

        # 但 ev0, ev1 的事件体已不在日志中
        assert bus.event_count == 1
        assert bus.get_events_in_range(0, 5) == []


# ── 线程安全 ──────────────────────────────────────────


class TestEventBusThreadSafety:
    """线程安全测试。"""

    def test_concurrent_publish(self):
        bus = EventBus()
        n_per_thread = 100
        n_threads = 4

        def publisher():
            for _i in range(n_per_thread):
                bus.publish(make_event())

        threads = [threading.Thread(target=publisher) for _ in range(n_threads)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=5)

        assert bus.event_count == n_per_thread * n_threads

    def test_concurrent_publish_and_query(self):
        bus = EventBus()
        stop = threading.Event()
        errors: list[Exception] = []

        def publisher():
            for i in range(200):
                if stop.is_set():
                    break
                bus.publish(make_event(timestamp=float(i)))

        def querier():
            for _ in range(100):
                if stop.is_set():
                    break
                try:
                    bus.get_events_in_range(0, 1000)
                except Exception as e:
                    errors.append(e)

        pub_thread = threading.Thread(target=publisher)
        q_threads = [threading.Thread(target=querier) for _ in range(3)]

        pub_thread.start()
        for t in q_threads:
            t.start()

        pub_thread.join(timeout=5)
        stop.set()
        for t in q_threads:
            t.join(timeout=2)

        assert len(errors) == 0
        assert bus.event_count == 200

    def test_concurrent_subscribe_unsubscribe(self):
        bus = EventBus()

        def subscriber():
            for _ in range(50):
                unsub = bus.subscribe("test", lambda e: None)
                unsub()

        threads = [threading.Thread(target=subscriber) for _ in range(3)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=5)

        # 之后发布应正常工作
        bus.publish(make_event())
        assert bus.event_count == 1


# ── 回调异常隔离 ──────────────────────────────────────


class TestEventBusErrorIsolation:
    """回调异常隔离测试。"""

    def test_one_bad_callback_does_not_affect_others(self):
        bus = EventBus()
        good_results: list[Event] = []

        def bad_callback(_event: Event) -> None:
            raise RuntimeError("假装失败")

        def good_callback(event: Event) -> None:
            good_results.append(event)

        bus.subscribe("test", bad_callback)
        bus.subscribe("test", good_callback)

        ev = make_event()
        bus.publish(ev)  # 不应抛异常

        assert len(good_results) == 1
        assert good_results[0].id == ev.id

    def test_wildcard_and_specific_both_receive(self):
        """一个回调失败，通配符和其他订阅者仍应收到事件。"""
        bus = EventBus()
        results: list[Event] = []

        def bad_callback(_event: Event) -> None:
            raise RuntimeError("fail")

        bus.subscribe("test", bad_callback)
        bus.subscribe("*", lambda e: results.append(e))
        bus.subscribe("test", lambda e: results.append(e))

        bus.publish(make_event(event_type="test"))

        assert len(results) == 2
