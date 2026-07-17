"""实体快照处理程序单元测试。

验证 make_entity_handlers 创建的 entity_snapshot 处理函数：
状态通道全量查询，序列化格式与事件坐标系一致。
"""

import pytest

from ascend.entity import Controller, EntityManager, EntityType
from ascend.net.handlers.entity_handler import make_entity_handlers, serialize_entity
from ascend.world_tree import WorldTree


@pytest.fixture
def manager():
    """隔离 WorldTree 的 EntityManager 固件。"""
    return EntityManager(world_tree_arg=WorldTree())


@pytest.fixture
def handlers(manager):
    """make_entity_handlers 返回的处理程序映射。"""
    return make_entity_handlers(manager)


class TestRegistration:
    """工厂函数注册测试。"""

    def test_returns_snapshot_handler(self, handlers):
        """返回 entity_snapshot 处理程序。"""
        assert set(handlers.keys()) == {"entity_snapshot"}
        assert all(callable(h) for h in handlers.values())


class TestSerializeEntity:
    """serialize_entity 序列化测试。"""

    def test_serializes_all_fields(self, manager):
        """序列化条目包含 id/entity_type/controller/x/y/layer_id。"""
        e = manager.birth(
            EntityType.CREATURE, 0, 0, 10, 5,
            controller=Controller.AI,
        )
        entry = serialize_entity(e)
        assert entry == {
            "id": e.id,
            "entity_type": "CREATURE",
            "controller": "AI",
            "x": 10.0,
            "y": 5.0,
            "layer_id": 0,
        }

    def test_prefers_float_position(self, manager):
        """fx/fy 存在时序列化精确 float 坐标。"""
        e = manager.birth(
            EntityType.CREATURE, 0, 0, 10, 5,
            controller=Controller.PLAYER,
            data={"fx": 10.4, "fy": 5.7},
        )
        entry = serialize_entity(e)
        assert (entry["x"], entry["y"]) == (10.4, 5.7)


class TestEntitySnapshot:
    """entity_snapshot 请求测试。"""

    def test_empty_manager(self, handlers):
        """无实体时返回空列表。"""
        resp = handlers["entity_snapshot"]({"payload": {}})
        assert resp["type"] == "response"
        assert resp["request_type"] == "entity_snapshot"
        assert resp["payload"] == {"entities": []}

    def test_returns_all_alive_entities(self, handlers, manager):
        """返回全部存活实体，死亡实体不出现。"""
        e1 = manager.birth(EntityType.CREATURE, 0, 0, 1, 1)
        e2 = manager.birth(EntityType.ITEM, 0, 0, 2, 2)
        e3 = manager.birth(EntityType.PLANT, 0, 0, 3, 3)
        manager.death(e2.id)

        entities = handlers["entity_snapshot"]({})["payload"]["entities"]
        ids = {entry["id"] for entry in entities}
        assert ids == {e1.id, e3.id}

    def test_snapshot_matches_event_coordinate_system(self, manager, handlers):
        """快照坐标与 entity_born 事件坐标一致（同一坐标系约定）。"""
        wt = manager._world_tree
        events = []
        wt.subscribe("entity_born", lambda e: events.append(e))
        manager.birth(
            EntityType.CREATURE, 1, 2, 10, 5, data={"fx": 210.5, "fy": 405.2},
        )
        entry = handlers["entity_snapshot"]({})["payload"]["entities"][0]
        assert (entry["x"], entry["y"]) == (
            events[0].data["x"], events[0].data["y"],
        )
