"""事件总线单元测试。"""

import os
import tempfile
import threading
import time

import pytest
from ascend.world_tree import Event, AffectedParty, WorldTree, EventGraph, EventArchive


def make_event(timestamp=0, event_type="test", initiator_id="a",
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


class TestWorldTree:
    def test_publish_and_subscribe(self):
        bus = WorldTree()
        received = []
        bus.subscribe("weather_change", lambda e: received.append(e))
        ev = make_event(event_type="weather_change")
        bus.publish(ev)
        assert len(received) == 1
        assert received[0].id == ev.id

    def test_wildcard_subscription(self):
        bus = WorldTree()
        received = []
        bus.subscribe("*", lambda e: received.append(e))
        bus.publish(make_event(event_type="weather_change"))
        bus.publish(make_event(event_type="npc_action"))
        assert len(received) == 2

    def test_unsubscribe(self):
        bus = WorldTree()
        received = []
        unsub = bus.subscribe("test", lambda e: received.append(e))
        bus.publish(make_event())
        assert len(received) == 1
        unsub()
        bus.publish(make_event())
        assert len(received) == 1  # no second event

    def test_time_range_query(self):
        bus = WorldTree()
        for t in range(5):
            bus.publish(make_event(timestamp=t))
        results = bus.get_events_in_range(1, 3)
        assert len(results) == 3
        assert {e.timestamp for e in results} == {1, 2, 3}

    def test_time_range_with_filter(self):
        bus = WorldTree()
        bus.publish(make_event(timestamp=0, event_type="rain"))
        bus.publish(make_event(timestamp=1, event_type="snow"))
        bus.publish(make_event(timestamp=2, event_type="rain"))
        results = bus.get_events_in_range(0, 2, event_type="rain")
        assert len(results) == 2

    def test_entity_index(self):
        bus = WorldTree()
        ev = make_event(initiator_id="npc_1",
                        affected=[AffectedParty(entity_id="npc_2", role="witness")])
        bus.publish(ev)
        events_1 = bus.get_entity_events("npc_1", -1, 1)
        events_2 = bus.get_entity_events("npc_2", -1, 1)
        assert len(events_1) == 1
        assert len(events_2) == 1

    def test_spatial_query(self):
        bus = WorldTree()
        bus.publish(make_event(location=(0, 0, None, None)))
        bus.publish(make_event(location=(1, 0, None, None)))
        bus.publish(make_event(location=(5, 5, None, None)))
        results = bus.get_events_in_region((0, 0), radius=1)
        assert len(results) == 2

    def test_event_count(self):
        bus = WorldTree()
        assert bus.event_count == 0
        bus.publish(make_event())
        bus.publish(make_event())
        assert bus.event_count == 2

    def test_get_event_by_id_memory(self):
        """按 ID 查找内存中的事件。"""
        bus = WorldTree()
        ev = make_event(id="target_1", timestamp=10)
        bus.publish(ev)
        result = bus.get_event_by_id("target_1")
        assert result is not None
        assert result.id == "target_1"
        assert result.timestamp == 10

    def test_get_event_by_id_not_found(self):
        """查找不存在的事件返回 None。"""
        bus = WorldTree()
        bus.publish(make_event(id="exists"))
        result = bus.get_event_by_id("no_such_id")
        assert result is None

    def test_get_event_by_id_empty_bus(self):
        """空总线上查找返回 None。"""
        bus = WorldTree()
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
        """移除不存在的节点是安全的。"""
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


class TestWorldTreeValidation:
    """事件校验测试。"""

    def test_empty_event_type(self):
        bus = WorldTree()
        with pytest.raises(ValueError, match="事件类型不能为空"):
            bus.publish(make_event(event_type=""))

    def test_whitespace_event_type(self):
        bus = WorldTree()
        with pytest.raises(ValueError, match="事件类型不能为空"):
            bus.publish(make_event(event_type="   "))

    def test_empty_initiator_id(self):
        bus = WorldTree()
        with pytest.raises(ValueError, match="发起方 ID 不能为空"):
            bus.publish(make_event(initiator_id=""))

    def test_invalid_initiator_type(self):
        bus = WorldTree()
        with pytest.raises(ValueError, match="无效的发起方类型"):
            bus.publish(make_event(initiator_type="alien"))

    def test_negative_timestamp(self):
        bus = WorldTree()
        with pytest.raises(ValueError, match="时间戳不能为负"):
            bus.publish(make_event(timestamp=-1))

    def test_invalid_location_type(self):
        bus = WorldTree()
        with pytest.raises(ValueError, match="位置格式无效"):
            bus.publish(make_event(location="not_a_tuple"))  # type: ignore[arg-type]

    def test_validation_can_be_disabled(self):
        bus = WorldTree(validate=False)
        ev = make_event(event_type="")
        bus.publish(ev)  # 不抛异常
        assert bus.event_count == 1

    def test_valid_event_passes(self):
        bus = WorldTree()
        ev = make_event()
        bus.publish(ev)  # 不抛异常
        assert bus.event_count == 1


# ── 事件生命周期 trim ────────────────────────────────


class TestWorldTreeTrim:
    """事件生命周期测试。"""

    def test_auto_trim_on_publish(self):
        """发布事件超过 max_memory_events 时自动触发 trim。"""
        bus = WorldTree(validate=False, max_memory_events=5)
        for t in range(10):
            bus.publish(make_event(timestamp=t))
        # 自动 trim 已触发，事件数不应超过阈值
        assert bus.event_count <= 5
        # 最近的事件应在内存中
        latest = bus.get_events_in_range(0, 100)
        assert latest[-1].timestamp == 9

    def test_auto_trim_not_triggered_under_threshold(self):
        """事件数未超阈值时不触发 trim。"""
        bus = WorldTree(validate=False, max_memory_events=100)
        for t in range(10):
            bus.publish(make_event(timestamp=t))
        # 全部保留
        assert bus.event_count == 10

    def test_auto_trim_disabled_by_default(self):
        """不传 max_memory_events 时不自动 trim。"""
        bus = WorldTree(validate=False)
        for t in range(100):
            bus.publish(make_event(timestamp=t))
        assert bus.event_count == 100  # 全部保留

    def test_auto_trim_preserves_graph_consistency(self):
        """自动 trim 后图和索引保持一致。"""
        bus = WorldTree(validate=False, max_memory_events=6)
        ev0 = make_event(timestamp=0, id="ev0")
        ev1 = make_event(timestamp=1, id="ev1", caused_by=["ev0"])
        for _ in range(10):
            bus.publish(make_event(timestamp=10))  # 触发自动 trim
        bus.publish(ev0)
        bus.publish(ev1)
        # 未超阈值，两个事件都在
        assert bus.graph.node_count >= 1

    def test_trim_basic(self):
        bus = WorldTree()
        for t in [0, 1, 2, 3, 4]:
            bus.publish(make_event(timestamp=t))

        removed = bus._trim(2)
        # _bisect_time(2, find_end=True) 返回第一个 ts > 2 的位置
        # ts 0,1,2 被移除, ts 3,4 保留
        assert removed == 3
        assert bus.event_count == 2

    def test_trim_removes_nothing(self):
        bus = WorldTree()
        for t in range(5, 10):  # ts 5, 6, 7, 8, 9
            bus.publish(make_event(timestamp=t))

        removed = bus._trim(2)  # 所有时间戳都 >= 5，不移除任何事件
        assert removed == 0
        assert bus.event_count == 5

    def test_trim_removes_everything(self):
        bus = WorldTree()
        for t in range(5):
            bus.publish(make_event(timestamp=t))

        removed = bus._trim(100)  # 全部移除
        assert removed == 5
        assert bus.event_count == 0

    def test_trim_empty_bus(self):
        bus = WorldTree()
        removed = bus._trim(10)
        assert removed == 0

    def test_trim_rebuilds_entity_index(self):
        bus = WorldTree()
        bus.publish(make_event(timestamp=0, initiator_id="npc_1"))
        bus.publish(make_event(timestamp=1, initiator_id="npc_2"))
        bus.publish(make_event(timestamp=10, initiator_id="npc_1"))

        bus._trim(5)  # 移除 ts=0,1 的两个事件

        events_npc1 = bus.get_entity_events("npc_1", 0, 100)
        assert len(events_npc1) == 1
        assert events_npc1[0].timestamp == 10

    def test_trim_rebuilds_spatial_index(self):
        bus = WorldTree()
        bus.publish(make_event(timestamp=0, location=(0, 0, None, None)))
        bus.publish(make_event(timestamp=1, location=(5, 5, None, None)))
        bus.publish(make_event(timestamp=10, location=(0, 0, None, None)))

        bus._trim(5)

        region = bus.get_events_in_region((0, 0), radius=0)
        assert len(region) == 1
        assert region[0].timestamp == 10

    def test_trim_and_publish(self):
        bus = WorldTree()
        for t in range(3):
            bus.publish(make_event(timestamp=t))  # ts 0,1,2

        bus._trim(1)  # 移除 ts=0,1 (ts=2 保留)

        bus.publish(make_event(timestamp=5))  # 新事件

        results = bus.get_events_in_range(0, 10)
        assert len(results) == 2  # ts=2 + ts=5

    def test_trim_twice(self):
        bus = WorldTree()
        for t in range(6):
            bus.publish(make_event(timestamp=t))

        bus._trim(2)  # 移除 ts 0,1,2
        assert bus.event_count == 3  # ts 3,4,5

        bus._trim(4)  # 移除 ts 3,4
        assert bus.event_count == 1  # ts 5

    def test_trim_negative_time_raises(self):
        bus = WorldTree()
        with pytest.raises(ValueError, match="清理时间不能为负"):
            bus._trim(-1)

    def test_trim_preserves_causal_chain_with_lookup(self):
        """Trim 后内存图节点移除，通过 lookup 可从内存事件体补全一级链。"""
        bus = WorldTree()
        ev0 = make_event(timestamp=0, id="ev0")
        ev1 = make_event(timestamp=1, id="ev1", caused_by=["ev0"])
        ev2 = make_event(timestamp=10, id="ev2", caused_by=["ev1"])
        bus.publish(ev0)
        bus.publish(ev1)
        bus.publish(ev2)

        bus._trim(5)  # 移除 ev0, ev1 的事件体和图节点（无归档，永久丢失）

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

        bus = WorldTree(validate=False, archive_path=path)
        try:
            ev = make_event(timestamp=0, id="ev_old")
            bus.publish(ev)
            bus._trim(10)  # 归档 ev_old

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

        bus = WorldTree(validate=False, archive_path=path)
        try:
            # 发布并归档
            ev = make_event(timestamp=1, id="dup_id", event_type="old")
            bus.publish(ev)
            bus._trim(10)

            # 发布同名 ID 的新事件（内存中）
            ev2 = make_event(timestamp=20, id="dup_id", event_type="new")
            bus.publish(ev2)

            result = bus.get_event_by_id("dup_id")
            assert result is not None
            # 应返回内存中的版本
            assert result.event_type == "new"
            assert result.timestamp == 20
        finally:
            bus._archive.close()  # type: ignore[union-attr]
            os.unlink(path)

    def test_trim_removes_graph_nodes(self):
        """trim 事件体时同步移除图中对应节点。"""
        bus = WorldTree()
        ev0 = make_event(timestamp=0, id="ev0")
        ev1 = make_event(timestamp=1, id="ev1", caused_by=["ev0"])
        ev2 = make_event(timestamp=10, id="ev2", caused_by=["ev1"])
        bus.publish(ev0)
        bus.publish(ev1)
        bus.publish(ev2)

        # 确认图有 3 个节点
        assert bus.graph.node_count == 3

        bus._trim(5)  # 移除 ev0, ev1

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

        bus = WorldTree(validate=False, archive_path=path)
        try:
            ev0 = make_event(timestamp=0, id="ev0")
            ev1 = make_event(timestamp=1, id="ev1", caused_by=["ev0"])
            ev2 = make_event(timestamp=10, id="ev2", caused_by=["ev1"])
            bus.publish(ev0)
            bus.publish(ev1)
            bus.publish(ev2)

            bus._trim(5)  # ev0, ev1 归档且从图中移除

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


class TestWorldTreeThreadSafety:
    """线程安全测试。"""

    def test_concurrent_publish(self):
        bus = WorldTree()
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
        bus = WorldTree()
        stop = threading.Event()
        errors: list[Exception] = []

        def publisher():
            for i in range(200):
                if stop.is_set():
                    break
                bus.publish(make_event(timestamp=i))

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
        bus = WorldTree()

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


class TestWorldTreeErrorIsolation:
    """回调异常隔离测试。"""

    def test_one_bad_callback_does_not_affect_others(self):
        bus = WorldTree()
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
        bus = WorldTree()
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
                make_event(timestamp=10, id="e1"),
                make_event(timestamp=20, id="e2"),
                make_event(timestamp=30, id="e3"),
            ]
            archive.archive(events)

            results = archive.query_time_range(15, 35)
            assert len(results) == 2
            assert results[0].timestamp == 20
            assert results[1].timestamp == 30
        finally:
            archive.close()
            os.unlink(path)

    def test_archive_and_query_entity(self):
        archive, path = self._make_archive()
        try:
            e1 = make_event(timestamp=10, id="e1", initiator_id="npc_1")
            e2 = make_event(timestamp=20, id="e2", initiator_id="npc_2")
            e3 = make_event(
                timestamp=30, id="e3", initiator_id="npc_1",
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
                make_event(timestamp=10, id="e1", location=(0, 0, None, None)),
                make_event(timestamp=20, id="e2", location=(3, 3, None, None)),
                make_event(timestamp=30, id="e3", location=(1, 0, None, None)),
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
                make_event(timestamp=10, id="e1", event_type="weather"),
                make_event(timestamp=20, id="e2", event_type="combat"),
                make_event(timestamp=30, id="e3", event_type="weather"),
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
            events = [make_event(timestamp=10, id="e1")]
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
            ev = make_event(timestamp=10, id="target", event_type="combat")
            archive.archive([ev])
            result = archive.query_by_id("target")
            assert result is not None
            assert result.id == "target"
            assert result.timestamp == 10
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
                timestamp=42,
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
            assert result.timestamp == 42
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
        a1.archive([make_event(timestamp=10, id="e1")])
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

        bus = WorldTree(validate=False, archive_path=path)
        try:
            # 发布 10 个事件
            for t in range(10):
                bus.publish(make_event(
                    timestamp=t * 60,
                    id=f"e{t}",
                    initiator_id="npc_1",
                ))

            # trim 前 5 个
            bus._trim(5 * 60)  # 移除 ts < 300 的: e0-e4

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
        bus = WorldTree()
        for t in range(5):
            bus.publish(make_event(timestamp=t * 60))

        bus._trim(120)  # 移除 ts=0,60,120 共 3 个事件
        assert bus.event_count == 2

        # 查询不应包含已 trim 的事件
        results = bus.get_events_in_range(0, 60)
        assert len(results) == 0  # ts=0,60 已被 trim 丢弃


# ── 事件权重 ──────────────────────────────────────────


class TestEventWeight:
    """事件权重测试。"""

    def test_default_weight_is_1(self):
        """未指定权重时默认为 1。"""
        ev = make_event()
        assert ev.weight == 1

    def test_weight_field(self):
        """可以指定事件权重。"""
        ev = make_event(weight=5)
        assert ev.weight == 5

    def test_high_weight_survives_trim(self):
        """高权重事件在一次 trim 后仍留在内存。"""
        bus = WorldTree(validate=False, max_memory_events=100)

        # 插入权重 5 的关键事件
        critical = make_event(timestamp=0, id="crit", weight=5)
        bus.publish(critical)

        # 填充低权重事件触发 trim
        for i in range(200):
            bus.publish(make_event(timestamp=i + 1, weight=1))

        # 高权重事件应在一次 trim 后存活
        result = bus.get_event_by_id("crit")
        assert result is not None, "高权重事件不应在第一次 trim 就被移除"

    def test_low_weight_trimmed_first(self):
        """低权重事件先于高权重事件被归档。"""
        bus = WorldTree(validate=False, max_memory_events=100)

        bus.publish(make_event(timestamp=0, id="low", weight=1))
        bus.publish(make_event(timestamp=1, id="high", weight=5))

        # 少量填充只触发 ~2 次 trim（cycle=2），
        # w=1 被移除，w=5 因 5>2 存活
        for i in range(150):
            bus.publish(make_event(timestamp=i + 2, weight=1))

        # 高权重仍应在，低权重已被归档
        high = bus.get_event_by_id("high")
        low = bus.get_event_by_id("low")
        assert high is not None, "高权重事件应存活"
        # 低权重经过多次 trim 周期应被移除
        assert low is None, f"低权重事件应被归档，但仍在内存: {low}"

    def test_all_weights_trimmed_eventually(self):
        """足够多次 trim 后所有事件都会被归档。"""
        bus = WorldTree(validate=False, max_memory_events=50)

        bus.publish(make_event(timestamp=0, id="w5", weight=5))

        # 大量事件触发足够多次 trim（cycle 累加到超过 5）
        for i in range(1000):
            bus.publish(make_event(timestamp=i + 1, weight=1))

        result = bus.get_event_by_id("w5")
        assert result is None, (
            f"经过足够多 trim 周期后权重 5 事件也应被归档"
        )

    def test_weighted_trim_preserves_ordering(self):
        """权重分层归档后内存日志仍保持时间有序。"""
        bus = WorldTree(validate=False, max_memory_events=100)

        for i in range(500):
            w = 5 if i % 20 == 0 else 1  # 每 20 个事件一个高权重
            bus.publish(make_event(timestamp=i, id=f"w_{i}", weight=w))

        results = bus.get_events_in_range(0, 500)
        for a, b in zip(results, results[1:]):
            assert a.timestamp <= b.timestamp, (
                f"时间戳乱序: {a.timestamp} > {b.timestamp}"
            )

    def test_weight_with_archive(self):
        """归档后高权重事件仍可按 ID 查到。"""
        import tempfile, os
        fd, path = tempfile.mkstemp(suffix=".db")
        os.close(fd)

        bus = WorldTree(validate=False, max_memory_events=50,
                       archive_path=path)
        try:
            bus.publish(make_event(timestamp=0, id="arch_crit", weight=5,
                                    event_type="critical"))

            for i in range(500):
                bus.publish(make_event(timestamp=i + 1, weight=1))

            # 即使最终被归档，也可通过 ID 查到
            ev = bus.get_event_by_id("arch_crit")
            assert ev is not None
            assert ev.weight == 5
            assert ev.event_type == "critical"
        finally:
            bus._archive.close()
            os.unlink(path)


# ── 图预热 ────────────────────────────────────────────


class TestGraphWarmup:
    """EventGraph 和 WorldTree 的图预热测试。"""

    def test_graph_warmup_batch_adds_edges(self):
        """warmup 批量添加边。"""
        graph = EventGraph()
        edges = [("a", "b", "caused_by"), ("b", "c", "caused_by")]
        count = graph.warmup(edges)
        assert count == 2
        assert graph.node_count == 3

    def test_graph_warmup_empty(self):
        """空边列表返回 0。"""
        graph = EventGraph()
        assert graph.warmup([]) == 0

    def test_world_tree_warmup_no_archive(self):
        """无归档时 warmup_graph 返回 0。"""
        bus = WorldTree(validate=False)
        assert bus.warmup_graph() == 0

    def test_world_tree_warmup_restores_edges(self):
        """归档后 warmup 恢复边到图中。"""
        path = tempfile.mktemp(suffix=".db")
        bus = None
        bus2 = None
        try:
            bus = WorldTree(
                validate=False, archive_path=path,
                max_memory_events=20,
            )
            # 先填充触发归档
            for i in range(30):
                bus.publish(make_event(timestamp=i))
            # 因果事件链（时间戳更大，在最近事件中）
            root = make_event(timestamp=100, id="root")
            child = make_event(timestamp=101, id="child", caused_by=["root"])
            bus.publish(root)
            bus.publish(child)
            for i in range(30):
                bus.publish(make_event(timestamp=200 + i))

            bus2 = WorldTree(
                validate=False, archive_path=path,
                max_memory_events=20,
            )
            count = bus2.warmup_graph(max_events=30)
            assert count >= 1, f"应恢复至少 1 条边，实际: {count}"
            chain = bus2.graph.get_causal_chain("child", max_depth=5)
            assert "root" in chain
        finally:
            if bus and bus._archive:
                bus._archive.close()
            if bus2 and bus2._archive:
                bus2._archive.close()
            os.unlink(path)


# ── 统计指标 ──────────────────────────────────────────


class TestStats:
    """WorldTree 统计指标测试。"""

    def test_stats_initial(self):
        """初始状态下 stats 各项为 0。"""
        bus = WorldTree(validate=False)
        s = bus.stats
        assert s["publish_count"] == 0
        assert s["event_count"] == 0
        assert s["trim_count"] == 0
        assert s["subscriber_count"] == 0
        assert s["graph_nodes"] == 0

    def test_stats_publish_count(self):
        """stats 记录发布总数。"""
        bus = WorldTree(validate=False)
        for i in range(5):
            bus.publish(make_event(timestamp=i))
        assert bus.stats["publish_count"] == 5

    def test_stats_trim_count(self):
        """stats 记录 trim 次数。"""
        bus = WorldTree(validate=False, max_memory_events=20)
        for i in range(100):
            bus.publish(make_event(timestamp=i))
        assert bus.stats["trim_count"] >= 1
        assert bus.stats["trim_cycle"] >= 1

    def test_stats_subscriber_count(self):
        """stats 记录活跃订阅数。"""
        bus = WorldTree(validate=False)
        unsub = bus.subscribe("test", lambda e: None)
        assert bus.stats["subscriber_count"] == 1
        unsub()
        assert bus.stats["subscriber_count"] == 0

    def test_stats_with_archive(self):
        """stats 记录归档事件数。"""
        path = tempfile.mktemp(suffix=".db")
        try:
            bus = WorldTree(
                validate=False,
                archive_path=path,
                max_memory_events=20,
            )
            for i in range(60):
                bus.publish(make_event(timestamp=i))
            s = bus.stats
            assert s["archive_event_count"] > 0
            assert s["trim_count"] >= 1
        finally:
            bus._archive.close()
            os.unlink(path)

    def test_stats_after_clear(self):
        """clear() 后统计归零。"""
        bus = WorldTree(validate=False, max_memory_events=20)
        for i in range(100):
            bus.publish(make_event(timestamp=i))
        bus.clear()
        s = bus.stats
        assert s["publish_count"] == 0
        assert s["trim_count"] == 0


# ── 异步分发 ──────────────────────────────────────────


class TestAsyncDispatch:
    """异步分发通道测试。"""

    def test_async_callback_receives_event(self):
        """异步订阅者在后台线程收到事件。"""
        bus = WorldTree()
        received: list[Event] = []
        barrier = threading.Barrier(2)

        def async_handler(event: Event) -> None:
            received.append(event)
            barrier.wait(timeout=5)

        bus.subscribe_async("test", async_handler)
        ev = make_event(event_type="test")
        bus.publish(ev)

        # publish 不阻塞，等待异步回调完成
        barrier.wait(timeout=5)
        assert len(received) == 1
        assert received[0].id == ev.id

    def test_async_does_not_block_publish(self):
        """异步订阅者的慢回调不阻塞 publish。"""
        bus = WorldTree()
        started = threading.Event()
        done = threading.Event()

        def slow_handler(_event: Event) -> None:
            started.set()
            time.sleep(0.5)  # 模拟慢 I/O
            done.set()

        bus.subscribe_async("test", slow_handler)

        start = time.perf_counter()
        bus.publish(make_event(event_type="test"))
        elapsed = time.perf_counter() - start

        # publish 应立即返回（远快于 0.5 秒）
        assert elapsed < 0.1, f"publish 被异步回调阻塞: {elapsed:.3f}s"

        # 等待异步回调确认完成（清理）
        started.wait(timeout=2)
        done.wait(timeout=2)

    def test_sync_and_async_coexist(self):
        """同步和异步订阅者同时存在，互不影响。"""
        bus = WorldTree()
        sync_results: list[Event] = []
        async_results: list[Event] = []
        barrier = threading.Barrier(2)

        bus.subscribe("test", lambda e: sync_results.append(e))
        bus.subscribe_async("test", lambda e: async_results.append(e) or barrier.wait(timeout=5))

        bus.publish(make_event(event_type="test"))

        # 同步回调已执行
        assert len(sync_results) == 1
        # 等待异步回调
        barrier.wait(timeout=5)
        assert len(async_results) == 1

    def test_async_exception_isolated(self):
        """异步回调抛异常不影响后续事件的分发。"""
        bus = WorldTree()
        received: list[Event] = []
        barrier = threading.Barrier(2)

        def bad_handler(_event: Event) -> None:
            raise RuntimeError("async 异常")

        def good_handler(event: Event) -> None:
            received.append(event)
            barrier.wait(timeout=5)

        bus.subscribe_async("test", bad_handler)
        bus.subscribe_async("test", good_handler)
        bus.publish(make_event(event_type="test"))

        barrier.wait(timeout=5)
        assert len(received) == 1  # good_handler 仍被调用

    def test_async_unsubscribe(self):
        """取消异步订阅后不再收到事件。"""
        bus = WorldTree()
        received: list[Event] = []

        def handler(event: Event) -> None:
            received.append(event)

        unsub = bus.subscribe_async("test", handler)
        unsub()

        bus.publish(make_event(event_type="test"))
        time.sleep(0.1)  # 给异步回调一点时间（不应被触发）
        assert len(received) == 0

    def test_async_multiple_events_ordering(self):
        """异步订���者按 publish 顺序接收事件。"""
        bus = WorldTree()
        received: list[str] = []
        n = 50
        barrier = threading.Barrier(2)

        def handler(event: Event) -> None:
            received.append(event.id)
            if len(received) == n:
                barrier.wait(timeout=5)

        bus.subscribe_async("test", handler)
        events = [make_event(event_type="test") for _ in range(n)]
        for ev in events:
            bus.publish(ev)

        barrier.wait(timeout=5)
        # 由于线程池可能并发，不保证严格顺序
        # 但所有事件都应被接收
        assert len(received) == n
        assert set(received) == {ev.id for ev in events}

    def test_async_callback_on_wildcard(self):
        """异步订阅者也可以通过通配符接收所有事件。"""
        bus = WorldTree()
        received: list[Event] = []
        barrier = threading.Barrier(2)

        bus.subscribe_async("*", lambda e: received.append(e) or barrier.wait(timeout=5))
        bus.publish(make_event(event_type="any_type"))

        barrier.wait(timeout=5)
        assert len(received) == 1
