"""PlayerService 单元测试。

后端权威玩家实体（壳子版）：spawn / 位置读写 / 传送 / 事件发布。
使用隔离 WorldTree 避免污染全局总线。
"""

import pytest

from ascend.config import TILE_MAP_SIZE
from ascend.entity import EntityManager, EntityType, PlayerService
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
    """出生 chunk (3, 5) 的 PlayerService 固件（未 spawn）。"""
    manager = EntityManager(world_tree_arg=wt)
    return PlayerService(manager, clock, birth_chunk=(3, 5), world_tree_arg=wt)


class TestSpawn:
    """spawn 生命周期测试。"""

    def test_spawn_at_birth_origin(self, service):
        """spawn 后实体位于出生 chunk 原点。

        Arrange:
            出生 chunk (3, 5) 的 PlayerService。
        Act:
            spawn()。
        Assert:
            实体类型 PLAYER，位置 = (3*200, 5*200)，chunk/tile 字段一致。
        """
        entity = service.spawn()
        assert entity.entity_type == EntityType.PLAYER
        assert service.position == (3.0 * TILE_MAP_SIZE, 5.0 * TILE_MAP_SIZE)
        assert entity.chunk == (3, 5)
        assert (entity.tile_x, entity.tile_y) == (0, 0)

    def test_spawn_idempotent(self, service):
        """重复 spawn 返回同一实体。

        Arrange:
            PlayerService 已 spawn。
        Act:
            再次 spawn()。
        Assert:
            返回同一实体对象，实体总数不变。
        """
        e1 = service.spawn()
        e2 = service.spawn()
        assert e1 is e2
        assert service._manager.count == 1

    def test_spawn_emits_entity_spawned(self, wt, clock):
        """spawn 经 EntityManager 发布 entity_spawned 事件。

        Arrange:
            订阅 entity_spawned 的隔离 WorldTree。
        Act:
            spawn()。
        Assert:
            收到 1 条事件，entity_type 为 PLAYER。
        """
        events = []
        wt.subscribe("entity_spawned", lambda e: events.append(e))
        manager = EntityManager(world_tree_arg=wt)
        service = PlayerService(manager, clock, birth_chunk=(0, 0), world_tree_arg=wt)
        service.spawn()
        assert len(events) == 1
        assert events[0].data["entity_type"] == "PLAYER"

    def test_position_before_spawn_is_birth(self, service):
        """未 spawn 时 position 返回出生点坐标。"""
        assert service.position == service.birth_position


class TestMoveTo:
    """move_to 权威移动测试。"""

    def test_move_accepts_reported_position(self, service):
        """壳子实现：move_to 无条件接受上报坐标（含小数）。

        Arrange:
            已 spawn 的 PlayerService。
        Act:
            move_to(612.4, 1000.8)。
        Assert:
            返回并存储精确 float 位置。
        """
        service.spawn()
        result = service.move_to(612.4, 1000.8)
        assert result == (612.4, 1000.8)
        assert service.position == (612.4, 1000.8)

    def test_move_updates_entity_indices(self, service):
        """跨 chunk 移动同步整数 chunk/tile 索引。

        Arrange:
            已 spawn 的 PlayerService（chunk (3,5)）。
        Act:
            move_to 到 chunk (0,0) 内 (10.5, 20.5)。
        Assert:
            entity.chunk 与 tile 为 floor 派生值。
        """
        service.spawn()
        service.move_to(10.5, 20.5)
        entity = service.entity
        assert entity.chunk == (0, 0)
        assert (entity.tile_x, entity.tile_y) == (10, 20)

    def test_move_negative_coords(self, service):
        """负坐标正确拆分 chunk/tile（floor 语义）。

        Arrange:
            已 spawn 的 PlayerService。
        Act:
            move_to(-0.5, -250.0)。
        Assert:
            chunk = (-1, -2)，tile 在 [0, 200) 内。
        """
        service.spawn()
        service.move_to(-0.5, -250.0)
        entity = service.entity
        assert entity.chunk == (-1, -2)
        assert 0 <= entity.tile_x < TILE_MAP_SIZE
        assert 0 <= entity.tile_y < TILE_MAP_SIZE

    def test_subtile_move_no_entity_moved(self, wt, clock):
        """tile 内小数移动不发布 entity_moved（避免高频刷总线）。

        Arrange:
            已 spawn 并订阅 entity_moved。
        Act:
            同一 tile 内两次微移。
        Assert:
            无 entity_moved 事件，float 位置仍精确更新。
        """
        events = []
        wt.subscribe("entity_moved", lambda e: events.append(e))
        manager = EntityManager(world_tree_arg=wt)
        service = PlayerService(manager, clock, birth_chunk=(0, 0), world_tree_arg=wt)
        service.spawn()
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
        service.spawn()
        service.move_to(5.5, 0.0)
        assert len(events) == 1

    def test_move_before_spawn_auto_spawns(self, service):
        """未 spawn 时 move_to 自动 spawn。"""
        service.move_to(7.0, 8.0)
        assert service.entity is not None
        assert service.position == (7.0, 8.0)


class TestTeleport:
    """teleport 强制传送测试。"""

    def test_teleport_publishes_event(self, wt, clock):
        """teleport 发布 player_teleported 事件（含目标 float 坐标）。

        Arrange:
            已 spawn 并订阅 player_teleported。
        Act:
            teleport(100.0, 200.0)。
        Assert:
            事件 data 携带精确坐标。
        """
        events = []
        wt.subscribe("player_teleported", lambda e: events.append(e))
        manager = EntityManager(world_tree_arg=wt)
        service = PlayerService(manager, clock, birth_chunk=(0, 0), world_tree_arg=wt)
        service.spawn()
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
        service.spawn()
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
        service.spawn()
        clock.skip(500)
        service.teleport(1.0, 2.0)
        assert events[0].timestamp == clock.time


class TestRepr:
    """__repr__ 测试。"""

    def test_repr_states(self, service):
        """spawn 前后 repr 均含关键信息。"""
        assert "spawned=False" in repr(service)
        service.spawn()
        assert "pos=" in repr(service)
