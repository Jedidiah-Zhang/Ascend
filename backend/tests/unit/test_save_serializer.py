"""存档序列化单元测试 — 状态采集/恢复与读档时钟对齐。

覆盖 ascend/save/serializer.py，以及 WorldClock.restore /
EntityManager.restore / PlayerService.restore 的静默恢复语义。
"""

import pytest

from ascend.save.serializer import (
    collect_state, apply_state, apply_clock, apply_player, aligned_time,
)
from ascend.time import WorldClock
from ascend.entity import EntityManager, PlayerService
from ascend.world_tree import world_tree as _real_wt


@pytest.fixture()
def clock() -> WorldClock:
    return WorldClock()


@pytest.fixture()
def player(clock: WorldClock) -> PlayerService:
    """已诞生的玩家服务（真实实体管理器，注入独立世界树避免污染单例）。"""
    from ascend.world_tree.tree import WorldTree
    wt = WorldTree()
    manager = EntityManager(world_tree_arg=wt)
    service = PlayerService(
        manager, clock, birth_chunk=(3, 5), world_tree_arg=wt,
    )
    service.birth()
    return service


class TestCollectState:
    """状态采集。"""

    def test_collects_clock_and_player(self, clock, player):
        """采集时钟与玩家位置/实体 ID。"""
        clock.tick()
        player.move_to(100.0, 200.0)
        state = collect_state(clock, player, None, archive_max_timestamp=42)
        assert state["clock"]["time"] == clock.time
        assert state["clock"]["speed"] == 1.0
        assert state["player"]["x"] == 100.0
        assert state["player"]["y"] == 200.0
        assert state["player"]["entity_id"] == player.entity.id
        assert state["archive_max_timestamp"] == 42

    def test_unborn_player_has_position(self, clock):
        """未诞生的玩家也能采集（位置=出生点）。"""
        from ascend.world_tree.tree import WorldTree
        wt = WorldTree()
        service = PlayerService(
            EntityManager(world_tree_arg=wt), clock, birth_chunk=(3, 5),
            world_tree_arg=wt,
        )
        state = collect_state(clock, service, None, archive_max_timestamp=0)
        assert state["player"]["entity_id"] is None
        assert state["player"]["x"] == 3 * 200


class TestApplyState:
    """状态恢复。"""

    def test_restores_clock(self, clock, player):
        """恢复时钟时间/速度/暂停。"""
        state = {
            "clock": {"time": 172800, "speed": 2.0, "paused": True},
            "player": {"entity_id": player.entity.id, "x": 5.0, "y": 6.0},
            "weather": {"seed": 1},
            "archive_max_timestamp": 0,
        }
        apply_state(state, clock, player)
        assert clock.time == 172800
        assert clock.speed == 2.0
        assert clock.paused is True

    def test_restores_player_position(self, clock, player):
        """恢复玩家位置（静默，不发布事件）。"""
        entity_id = player.entity.id
        player.move_to(10.0, 20.0)
        state = {
            "clock": {"time": 0, "speed": 1.0, "paused": False},
            "player": {"entity_id": entity_id, "x": 10.0, "y": 20.0},
            "weather": {"seed": 1},
            "archive_max_timestamp": 0,
        }
        # 模拟读档：新管理器 + 新服务
        from ascend.world_tree.tree import WorldTree
        wt = WorldTree()
        restored_service = PlayerService(
            EntityManager(world_tree_arg=wt), clock, birth_chunk=(3, 5),
            world_tree_arg=wt,
        )
        apply_state(state, clock, restored_service)
        assert restored_service.position == (10.0, 20.0)
        assert restored_service.entity.id == entity_id

    def test_negative_time_rejected(self, clock, player):
        """负时间恢复被拒绝。"""
        state = {
            "clock": {"time": -1, "speed": 1.0, "paused": False},
            "player": {"entity_id": None, "x": 0.0, "y": 0.0},
            "weather": {"seed": 1},
            "archive_max_timestamp": 0,
        }
        with pytest.raises(ValueError):
            apply_state(state, clock, player)


class TestApplyClock:
    """时钟恢复（拆分的 apply_clock）。"""

    def test_restores_clock_only(self, clock):
        """仅恢复时钟，不触碰玩家。"""
        state = {
            "clock": {"time": 999, "speed": 3.0, "paused": True},
            "player": {"entity_id": "x", "x": 5.0, "y": 6.0},
            "archive_max_timestamp": 0,
        }
        apply_clock(state, clock)
        assert clock.time == 999
        assert clock.speed == 3.0
        assert clock.paused is True

    def test_missing_clock_defaults(self, clock):
        """缺 clock 字段时默认 0/1.0/False。"""
        apply_clock({}, clock)
        assert clock.time == 0
        assert clock.speed == 1.0
        assert clock.paused is False


class TestApplyPlayer:
    """玩家恢复（拆分的 apply_player）。"""

    def test_restores_player_only(self, clock, player):
        """仅恢复玩家实体（静默）。"""
        entity_id = player.entity.id
        state = {
            "player": {"entity_id": entity_id, "x": 11.0, "y": 22.0},
        }
        apply_player(state, player)
        assert player.position == (11.0, 22.0)
        assert player.entity.id == entity_id

    def test_no_entity_id_is_noop(self, clock, player):
        """无 entity_id 时不操作。"""
        apply_player({"player": {"entity_id": None, "x": 0.0, "y": 0.0}}, player)
        assert player.entity.id is not None  # 保持原实体


class TestAlignedTime:
    """读档时钟对齐。"""

    def test_state_ahead(self):
        """state 时钟比归档新时取 state。"""
        state = {
            "clock": {"time": 500},
            "archive_max_timestamp": 400,
        }
        assert aligned_time(state) == 500

    def test_archive_ahead(self):
        """归档比 state 新时取归档（防时间倒流）。"""
        state = {
            "clock": {"time": 300},
            "archive_max_timestamp": 450,
        }
        assert aligned_time(state) == 450

    def test_missing_fields_default_zero(self):
        """缺字段时默认 0。"""
        assert aligned_time({}) == 0
        assert aligned_time({"clock": {}}) == 0


class TestClockRestore:
    """WorldClock.restore。"""

    def test_restore_sets_state(self):
        """恢复时间/速度/暂停。"""
        clock = WorldClock()
        clock.restore(time=999, speed=3.0, paused=True)
        assert clock.time == 999
        assert clock.speed == 3.0
        assert clock.paused is True

    def test_restore_negative_time_rejected(self):
        """负时间拒绝。"""
        clock = WorldClock()
        with pytest.raises(ValueError):
            clock.restore(time=-5)

    def test_restore_then_tick_advances(self):
        """恢复后 tick 正常推进。"""
        clock = WorldClock()
        clock.restore(time=100)
        clock.tick()
        assert clock.time == 101


class TestEntityRestore:
    """EntityManager.restore 静默恢复语义。"""

    def test_restore_does_not_publish_born(self, clock):
        """restore 不发布 entity_born 事件。"""
        from ascend.world_tree.tree import WorldTree
        wt = WorldTree()
        manager = EntityManager(world_tree_arg=wt)
        manager.restore(
            "eid-1", __import__("ascend.entity", fromlist=["EntityType"]).EntityType.CREATURE,
            0, 0, 5, 5, controller=__import__(
                "ascend.entity", fromlist=["Controller"],
            ).Controller.PLAYER, data={"fx": 5.0, "fy": 5.0},
        )
        assert manager.count == 1
        assert wt._publish_count == 0

    def test_restore_preserves_identity(self, clock):
        """restore 保持实体 ID 与索引一致。"""
        from ascend.world_tree.tree import WorldTree
        from ascend.entity import EntityType, Controller
        wt = WorldTree()
        manager = EntityManager(world_tree_arg=wt)
        entity = manager.restore(
            "eid-keep", EntityType.CREATURE, 1, 2, 3, 4,
            controller=Controller.AI, data={"fx": 203.0, "fy": 404.0},
        )
        assert entity.id == "eid-keep"
        assert manager.get("eid-keep") is entity
        assert len(manager.by_type(EntityType.CREATURE)) == 1
        assert entity.global_xy == (203.0, 404.0)
