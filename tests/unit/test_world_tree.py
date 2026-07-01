"""事件总线单元测试。"""

import os
import tempfile
import threading

import pytest
from ascend.world_tree import Event, AffectedParty, EventBus, EventGraph, EventArchive


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

    def test_get_event_by_id_memory(self):
        """按 ID 查找内存中的事件。"""
        bus = EventBus()
        ev = make_event(id="target_1", timestamp=10.0)
        bus.publish(ev)
        result = bus.get_event_by_id("target_1")
        assert result is not None
        assert result.id == "target_1"
        assert result.timestamp == 10.0

    def test_get_event_by_id_not_found(self):
        """查找不存在的事件返回 None。"""
        bus = EventBus()
        bus.publish(make_event(id="exists"))
        result = bus.get_event_by_id("no_such_id")
        assert result is None

    def test_get_event_by_id_empty_bus(self):
        """空总线上查找返回 None。"""
        bus = EventBus()
        result = bus.get_event_by_id("anything")
        assert result is None


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

    def test_node_count(self):
        """空图节点数为 0。"""
        g = EventGraph()
        assert g.node_count == 0
        g.add_event(make_event(id="a"))
        g.add_event(make_event(id="b"))
        assert g.node_count == 2

    def test_remove_nodes_basic(self):
        """移除部分节点，剩余节点和边正确。"""
        g = EventGraph()
        a = make_event(id="a")
        b = make_event(id="b", caused_by=["a"])
        c = make_event(id="c", caused_by=["b"])
        for ev in [a, b, c]:
            g.add_event(ev)

        g.remove_nodes({"a", "b"})

        assert g.node_count == 1
        # c 的 causals 已无上游
        assert g.get_causal_chain("c") == []
        # a, b 不再存在于图中
        assert g.get_consequences("a") == []
        assert g.get_consequences("b") == []

    def test_remove_nodes_cleans_reverse_edges(self):
        """移除节点时同时清理反向邻接表中的边。"""
        g = EventGraph()
        cause = make_event(id="cause")
        effect = make_event(id="effect", caused_by=["cause"])
        g.add_event(cause)
        g.add_event(effect)

        g.remove_nodes({"cause"})

        # effect 的入边应被清理
        assert g.get_causal_chain("effect") == []
        # 不应有残留引用
        assert "cause" not in g._reverse.get("effect", [])

    def test_remove_nodes_empty_set(self):
        """移除空集合是安全的。"""
        g = EventGraph()
        g.add_event(make_event(id="a"))
        g.remove_nodes(set())
        assert g.node_count == 1

    def test_remove_nodes_nonexistent(self):
        """移��不存在的节点是安全的。"""
        g = EventGraph()
        g.add_event(make_event(id="a"))
        g.remove_nodes({"nonexistent"})
        assert g.node_count == 1
        assert g.get_consequences("a") == []

    def test_remove_nodes_idempotent(self):
        """重复移除相同节点是安全的。"""
        g = EventGraph()
        g.add_event(make_event(id="a"))
        g.remove_nodes({"a"})
        g.remove_nodes({"a"})  # 不抛异常
        assert g.node_count == 0


class TestModuleSingleton:
    def test_world_tree_singleton(self):
        from ascend.world_tree import world_tree
        from ascend.world_tree import WorldTree
        assert isinstance(world_tree, WorldTree)
        # 单例应该是同一个对象
        from ascend.world_tree import world_tree as wt2
        assert world_tree is wt2


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

    def test_trim_preserves_causal_chain_with_lookup(self):
        """Trim 后内存图节点移除，通过 lookup 可从内存事件体补全一级链。"""
        bus = EventBus()
        ev0 = make_event(timestamp=0.0, id="ev0")
        ev1 = make_event(timestamp=1.0, id="ev1", caused_by=["ev0"])
        ev2 = make_event(timestamp=10.0, id="ev2", caused_by=["ev1"])
        bus.publish(ev0)
        bus.publish(ev1)
        bus.publish(ev2)

        bus.trim(5.0)  # 移除 ev0, ev1 的事件体和图节点（无归档，永久丢失）

        # 无 lookup 时因果链已断裂
        chain_no_lookup = bus.graph.get_causal_chain("ev2")
        assert chain_no_lookup == []

        # 带 lookup 时从 ev2 的内存事件体可恢复 ev1 的边，
        # 但 ev1 已被丢弃，其 caused_by 无法恢复
        chain = bus.graph.get_causal_chain("ev2", lookup=bus.get_event_by_id)
        assert chain == ["ev1"]

        # ev2 仍在内存；lookup 追溯时 ev1 被懒加载回图中
        assert bus.graph.node_count == 2
        assert bus.event_count == 1
        assert bus.get_events_in_range(0, 5) == []

    def test_get_event_by_id_archive_fallback(self):
        """trim 后按 ID 查找从归档中取回事件体。"""
        fd, path = tempfile.mkstemp(suffix=".db")
        os.close(fd)

        bus = EventBus(validate=False, archive_path=path)
        try:
            ev = make_event(timestamp=0.0, id="ev_old")
            bus.publish(ev)
            bus.trim(10.0)  # 归档 ev_old

            result = bus.get_event_by_id("ev_old")
            assert result is not None
            assert result.id == "ev_old"
        finally:
            bus._archive.close()  # type: ignore[union-attr]
            os.unlink(path)

    def test_get_event_by_id_memory_first(self):
        """内存中有同 ID 事件时优先返回内存版本（不查归档）。"""
        fd, path = tempfile.mkstemp(suffix=".db")
        os.close(fd)

        bus = EventBus(validate=False, archive_path=path)
        try:
            # 发布并归档
            ev = make_event(timestamp=1.0, id="dup_id", event_type="old")
            bus.publish(ev)
            bus.trim(10.0)

            # 发布同名 ID 的新事件（内存中）
            ev2 = make_event(timestamp=20.0, id="dup_id", event_type="new")
            bus.publish(ev2)

            result = bus.get_event_by_id("dup_id")
            assert result is not None
            # 应返回内存中的版本
            assert result.event_type == "new"
            assert result.timestamp == 20.0
        finally:
            bus._archive.close()  # type: ignore[union-attr]
            os.unlink(path)

    def test_trim_removes_graph_nodes(self):
        """trim 事件体时同步移除图中对应节点。"""
        bus = EventBus()
        ev0 = make_event(timestamp=0.0, id="ev0")
        ev1 = make_event(timestamp=1.0, id="ev1", caused_by=["ev0"])
        ev2 = make_event(timestamp=10.0, id="ev2", caused_by=["ev1"])
        bus.publish(ev0)
        bus.publish(ev1)
        bus.publish(ev2)

        # 确认图有 3 个节点
        assert bus.graph.node_count == 3

        bus.trim(5.0)  # 移除 ev0, ev1

        # ev0, ev1 从图中移除，ev2 保留
        assert bus.graph.node_count == 1
        # ev0 不再在图中有出边
        assert bus.graph.get_consequences("ev0") == []
        # ev1 不再在图中有入边
        assert bus.graph.get_causal_chain("ev2") == []

    def test_trim_with_archive_causal_chain_still_works(self):
        """trim 后因果链从内存图消失，通过 lookup 从归档补全。"""
        fd, path = tempfile.mkstemp(suffix=".db")
        os.close(fd)

        bus = EventBus(validate=False, archive_path=path)
        try:
            ev0 = make_event(timestamp=0.0, id="ev0")
            ev1 = make_event(timestamp=1.0, id="ev1", caused_by=["ev0"])
            ev2 = make_event(timestamp=10.0, id="ev2", caused_by=["ev1"])
            bus.publish(ev0)
            bus.publish(ev1)
            bus.publish(ev2)

            bus.trim(5.0)  # ev0, ev1 归档且从图中移除

            # 无 lookup 时因果链断裂
            chain_no_lookup = bus.graph.get_causal_chain("ev2")
            assert chain_no_lookup == []

            # 带 lookup（覆盖内存+归档）时因果链完整恢复
            chain = bus.graph.get_causal_chain("ev2", lookup=bus.get_event_by_id)
            assert chain == ["ev0", "ev1"]
        finally:
            bus._archive.close()  # type: ignore[union-attr]
            os.unlink(path)


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


# ── 事件归档 ──────────────────────────────────────────


class TestEventArchive:
    """事件归档测试。"""

    def _make_archive(self) -> tuple[EventArchive, str]:
        """创建临时归档文件。"""
        fd, path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        return EventArchive(path), path

    def test_archive_and_query_time_range(self):
        archive, path = self._make_archive()
        try:
            events = [
                make_event(timestamp=10.0, id="e1"),
                make_event(timestamp=20.0, id="e2"),
                make_event(timestamp=30.0, id="e3"),
            ]
            archive.archive(events)

            results = archive.query_time_range(15.0, 35.0)
            assert len(results) == 2
            assert results[0].timestamp == 20.0
            assert results[1].timestamp == 30.0
        finally:
            archive.close()
            os.unlink(path)

    def test_archive_and_query_entity(self):
        archive, path = self._make_archive()
        try:
            e1 = make_event(timestamp=10.0, id="e1", initiator_id="npc_1")
            e2 = make_event(timestamp=20.0, id="e2", initiator_id="npc_2")
            e3 = make_event(
                timestamp=30.0, id="e3", initiator_id="npc_1",
                affected=[
                    AffectedParty("npc_1", "subject"),
                    AffectedParty("npc_3", "witness"),
                ],
            )
            archive.archive([e1, e2, e3])

            # 按 npc_1 查询
            results = archive.query_entity("npc_1", 0, 100)
            assert len(results) == 2
            ids = {r.id for r in results}
            assert ids == {"e1", "e3"}

            # 按 npc_3（仅作为 affected 出现）查询
            results = archive.query_entity("npc_3", 0, 100)
            assert len(results) == 1
            assert results[0].id == "e3"
        finally:
            archive.close()
            os.unlink(path)

    def test_archive_and_query_region(self):
        archive, path = self._make_archive()
        try:
            events = [
                make_event(timestamp=10.0, id="e1", location=(0, 0, None, None)),
                make_event(timestamp=20.0, id="e2", location=(3, 3, None, None)),
                make_event(timestamp=30.0, id="e3", location=(1, 0, None, None)),
            ]
            archive.archive(events)

            # 查询 (0,0) 半径 1
            results = archive.query_region((0, 0), radius=1)
            assert len(results) == 2
            ids = {r.id for r in results}
            assert ids == {"e1", "e3"}

            # 查询 (3,3) 半径 0
            results = archive.query_region((3, 3), radius=0)
            assert len(results) == 1
            assert results[0].id == "e2"
        finally:
            archive.close()
            os.unlink(path)

    def test_archive_query_with_type_filter(self):
        archive, path = self._make_archive()
        try:
            events = [
                make_event(timestamp=10.0, id="e1", event_type="weather"),
                make_event(timestamp=20.0, id="e2", event_type="combat"),
                make_event(timestamp=30.0, id="e3", event_type="weather"),
            ]
            archive.archive(events)

            results = archive.query_time_range(0, 100, event_type="weather")
            assert len(results) == 2
            assert {r.id for r in results} == {"e1", "e3"}
        finally:
            archive.close()
            os.unlink(path)

    def test_archive_idempotent(self):
        archive, path = self._make_archive()
        try:
            events = [make_event(timestamp=10.0, id="e1")]
            archive.archive(events)
            archive.archive(events)  # 重复归档同 ID

            results = archive.query_time_range(0, 100)
            assert len(results) == 1  # 不重复
        finally:
            archive.close()
            os.unlink(path)

    def test_empty_archive_query(self):
        archive, path = self._make_archive()
        try:
            assert archive.query_time_range(0, 100) == []
            assert archive.query_entity("nobody", 0, 100) == []
            assert archive.query_region((0, 0), radius=1) == []
        finally:
            archive.close()
            os.unlink(path)

    def test_query_by_id_found(self):
        """按 ID 点查归档中已存在的事件。"""
        archive, path = self._make_archive()
        try:
            ev = make_event(timestamp=10.0, id="target", event_type="combat")
            archive.archive([ev])
            result = archive.query_by_id("target")
            assert result is not None
            assert result.id == "target"
            assert result.timestamp == 10.0
            assert result.event_type == "combat"
        finally:
            archive.close()
            os.unlink(path)

    def test_query_by_id_not_found(self):
        """查询不存在的事件 ID 返回 None。"""
        archive, path = self._make_archive()
        try:
            result = archive.query_by_id("nonexistent")
            assert result is None
        finally:
            archive.close()
            os.unlink(path)

    def test_query_by_id_empty_archive(self):
        """空归档查询返回 None。"""
        archive, path = self._make_archive()
        try:
            result = archive.query_by_id("anything")
            assert result is None
        finally:
            archive.close()
            os.unlink(path)

    def test_query_by_id_roundtrip_all_fields(self):
        """归档事件按 ID 取回后所有字段完整还原。"""
        archive, path = self._make_archive()
        try:
            ev = make_event(
                timestamp=42.5,
                id="full_test",
                event_type="test_type",
                initiator_id="npc_x",
                location=(3, 7, 10, 20),
                affected=[
                    AffectedParty(entity_id="npc_y", role="witness"),
                    AffectedParty(entity_id="npc_z", role="recipient"),
                ],
                caused_by=["cause_1", "cause_2"],
                observes="observed_event",
                co_participants=["partner_1"],
                data={"key": "value", "num": 99},
            )
            archive.archive([ev])
            result = archive.query_by_id("full_test")
            assert result is not None
            assert result.timestamp == 42.5
            assert result.event_type == "test_type"
            assert result.initiator_id == "npc_x"
            assert result.location == (3, 7, 10, 20)
            assert len(result.affected) == 2
            assert result.caused_by == ["cause_1", "cause_2"]
            assert result.observes == "observed_event"
            assert result.co_participants == ["partner_1"]
            assert result.data == {"key": "value", "num": 99}
        finally:
            archive.close()
            os.unlink(path)

    def test_archive_persistence(self):
        fd, path = tempfile.mkstemp(suffix=".db")
        os.close(fd)

        # 写入
        a1 = EventArchive(path)
        a1.archive([make_event(timestamp=10.0, id="e1")])
        a1.close()

        # 重新打开
        a2 = EventArchive(path)
        try:
            results = a2.query_time_range(0, 100)
            assert len(results) == 1
            assert results[0].id == "e1"
        finally:
            a2.close()
            os.unlink(path)

    def test_trim_with_archive_transparent_query(self):
        """trim 后查询自动合并内存和归档结果。"""
        fd, path = tempfile.mkstemp(suffix=".db")
        os.close(fd)

        bus = EventBus(validate=False, archive_path=path)
        try:
            # 发布 10 个事件
            for t in range(10):
                bus.publish(make_event(
                    timestamp=float(t * 60),
                    id=f"e{t}",
                    initiator_id="npc_1",
                ))

            # trim 前 5 个
            bus.trim(5.0 * 60)  # 移除 ts < 300 的: e0-e4

            # 查询全部时间范围 — 应透明合并
            results = bus.get_events_in_range(0, 1000)
            assert len(results) == 10

            # 查询实体 — 应透明合并
            results = bus.get_entity_events("npc_1", 0, 1000)
            assert len(results) == 10

            # 确保归档中的事件按时间顺序排在前面
            for i in range(9):
                assert results[i].timestamp <= results[i + 1].timestamp
        finally:
            bus._archive.close()  # type: ignore[union-attr]
            os.unlink(path)

    def test_no_archive_path_behavior_unchanged(self):
        """不传 archive_path 时行为与之前完全一致。"""
        bus = EventBus()
        for t in range(5):
            bus.publish(make_event(timestamp=float(t * 60)))

        bus.trim(120.0)  # 移除 ts=0,60,120 共 3 个事件
        assert bus.event_count == 2

        # 查询不应包含已 trim 的事件
        results = bus.get_events_in_range(0, 60)
        assert len(results) == 0  # ts=0,60 已被 trim 丢弃
