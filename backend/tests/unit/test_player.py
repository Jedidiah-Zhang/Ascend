"""PlayerService 单元测试。

后端权威玩家实体（壳子版）：birth / 位置读写 / 传送 / 事件发布。
使用隔离 WorldTree 避免污染全局总线。
"""

import pytest

from ascend.config import TILE_MAP_SIZE
from ascend.entity import Controller, EntityManager, EntityType, PlayerService
from ascend.time import WorldClock
from ascend.world_tree import WorldTree


@pytest.fixture
def wt():
    """隔离的 WorldTree 固件。"""
    return WorldTree()


@pytest.fixture
def clock():
    """默认起始时间的 WorldClock 固件。"""
    return WorldClock()


@pytest.fixture
def service(wt, clock):
    """出生 chunk (3, 5) 的 PlayerService 固件（未 birth）。"""
    manager = EntityManager(world_tree_arg=wt)
    return PlayerService(manager, clock, birth_chunk=(3, 5), world_tree_arg=wt)


class TestBirth:
    """birth 生命周期测试。"""

    def test_birth_at_birth_origin(self, service):
        """birth 后实体位于出生 chunk 原点。

        Arrange:
            出生 chunk (3, 5) 的 PlayerService。
        Act:
            birth()。
        Assert:
            实体为 controller=PLAYER 的 CREATURE，位置 = (3*200, 5*200)，
            chunk/tile 字段一致。
        """
        entity = service.birth()
        assert entity.entity_type == EntityType.CREATURE
        assert entity.controller == Controller.PLAYER
        assert service.position == (3.0 * TILE_MAP_SIZE, 5.0 * TILE_MAP_SIZE)
        assert entity.chunk == (3, 5)
        assert (entity.tile_x, entity.tile_y) == (0, 0)

    def test_birth_idempotent(self, service):
        """重复 birth 返回同一实体。

        Arrange:
            PlayerService 已 birth。
        Act:
            再次 birth()。
        Assert:
            返回同一实体对象，实体总数不变。
        """
        e1 = service.birth()
        e2 = service.birth()
        assert e1 is e2
        assert service._manager.count == 1

    def test_birth_emits_entity_born(self, wt, clock):
        """birth 经 EntityManager 发布 entity_born 事件。

        Arrange:
            订阅 entity_born 的隔离 WorldTree。
        Act:
            birth()。
        Assert:
            收到 1 条事件，entity_type 为 CREATURE，controller 为 PLAYER。
        """
        events = []
        wt.subscribe("entity_born", lambda e: events.append(e))
        manager = EntityManager(world_tree_arg=wt)
        service = PlayerService(manager, clock, birth_chunk=(0, 0), world_tree_arg=wt)
        service.birth()
        assert len(events) == 1
        assert events[0].data["entity_type"] == "CREATURE"
        assert events[0].data["controller"] == "PLAYER"

    def test_position_before_birth_is_birth_point(self, service):
        """未 birth 时 position 返回出生点坐标。"""
        assert service.position == service.birth_position


class TestMoveTo:
    """move_to 权威移动测试。"""

    def test_move_accepts_reported_position(self, service):
        """壳子实现：move_to 无条件接受上报坐标（含小数）。

        Arrange:
            已 birth 的 PlayerService。
        Act:
            move_to(612.4, 1000.8)。
        Assert:
            返回并存储精确 float 位置。
        """
        service.birth()
        result = service.move_to(612.4, 1000.8)
        assert result == (612.4, 1000.8)
        assert service.position == (612.4, 1000.8)

    def test_move_updates_entity_indices(self, service):
        """跨 chunk 移动同步整数 chunk/tile 索引。

        Arrange:
            已 birth 的 PlayerService（chunk (3,5)）。
        Act:
            move_to 到 chunk (0,0) 内 (10.5, 20.5)。
        Assert:
            entity.chunk 与 tile 为 floor 派生值。
        """
        service.birth()
        service.move_to(10.5, 20.5)
        entity = service.entity
        assert entity.chunk == (0, 0)
        assert (entity.tile_x, entity.tile_y) == (10, 20)

    def test_move_negative_coords(self, service):
        """负坐标正确拆分 chunk/tile（floor 语义）。

        Arrange:
            已 birth 的 PlayerService。
        Act:
            move_to(-0.5, -250.0)。
        Assert:
            chunk = (-1, -2)，tile 在 [0, 200) 内。
        """
        service.birth()
        service.move_to(-0.5, -250.0)
        entity = service.entity
        assert entity.chunk == (-1, -2)
        assert 0 <= entity.tile_x < TILE_MAP_SIZE
        assert 0 <= entity.tile_y < TILE_MAP_SIZE

    def test_subtile_move_no_entity_moved(self, wt, clock):
        """tile 内小数移动不发布 entity_moved（避免高频刷总线）。

        Arrange:
            已 birth 并订阅 entity_moved。
        Act:
            同一 tile 内两次微移。
        Assert:
            无 entity_moved 事件，float 位置仍精确更新。
        """
        events = []
        wt.subscribe("entity_moved", lambda e: events.append(e))
        manager = EntityManager(world_tree_arg=wt)
        service = PlayerService(manager, clock, birth_chunk=(0, 0), world_tree_arg=wt)
        service.birth()
        service.move_to(0.2, 0.3)
        service.move_to(0.8, 0.9)
        assert events == []
        assert service.position == (0.8, 0.9)

    def test_cross_tile_move_emits_entity_moved(self, wt, clock):
        """跨 tile 移动发布 entity_moved。"""
        events = []
        wt.subscribe("entity_moved", lambda e: events.append(e))
        manager = EntityManager(world_tree_arg=wt)
        service = PlayerService(manager, clock, birth_chunk=(0, 0), world_tree_arg=wt)
        service.birth()
        service.move_to(5.5, 0.0)
        assert len(events) == 1

    def test_move_before_birth_auto_births(self, service):
        """未 birth 时 move_to 自动 birth。"""
        service.move_to(7.0, 8.0)
        assert service.entity is not None
        assert service.position == (7.0, 8.0)


class TestTeleport:
    """teleport 强制传送测试。"""

    def test_teleport_publishes_event(self, wt, clock):
        """teleport 发布 player_teleported 事件（含目标 float 坐标）。

        Arrange:
            已 birth 并订阅 player_teleported。
        Act:
            teleport(100.0, 200.0)。
        Assert:
            事件 data 携带精确坐标。
        """
        events = []
        wt.subscribe("player_teleported", lambda e: events.append(e))
        manager = EntityManager(world_tree_arg=wt)
        service = PlayerService(manager, clock, birth_chunk=(0, 0), world_tree_arg=wt)
        service.birth()
        result = service.teleport(100.0, 200.0)
        assert result == (100.0, 200.0)
        assert len(events) == 1
        assert events[0].data == {"x": 100.0, "y": 200.0}

    def test_teleport_home(self, wt, clock):
        """teleport_home 回出生点并发布事件。"""
        events = []
        wt.subscribe("player_teleported", lambda e: events.append(e))
        manager = EntityManager(world_tree_arg=wt)
        service = PlayerService(manager, clock, birth_chunk=(2, 4), world_tree_arg=wt)
        service.birth()
        service.move_to(9999.0, 9999.0)
        result = service.teleport_home()
        assert result == service.birth_position
        assert service.position == service.birth_position
        assert len(events) == 1

    def test_teleport_timestamp_uses_clock(self, wt, clock):
        """事件时间戳来自世界时钟。"""
        events = []
        wt.subscribe("player_teleported", lambda e: events.append(e))
        manager = EntityManager(world_tree_arg=wt)
        service = PlayerService(manager, clock, birth_chunk=(0, 0), world_tree_arg=wt)
        service.birth()
        clock.skip(500)
        service.teleport(1.0, 2.0)
        assert events[0].timestamp == clock.time


class TestRepr:
    """__repr__ 测试。"""

    def test_repr_states(self, service):
        """birth 前后 repr 均含关键信息。"""
        assert "born=False" in repr(service)
        service.birth()
        assert "pos=" in repr(service)
