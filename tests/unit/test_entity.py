"""实体系统单元测试。"""

from ascend.config import TILE_MAP_SIZE
from ascend.entity import (
    Controller, Entity, EntityType, EntityManager, split_coords,
)
from ascend.world_tree import WorldTree, world_tree


class TestEntity:
    """Entity 数据结构测试。"""

    def test_creation(self):
        """创建实体，验证字段和默认值。"""
        e = Entity(
            entity_type=EntityType.CREATURE,
            chunk_x=0, chunk_y=0, tile_x=10, tile_y=5,
            born_at=100.0,
        )
        assert e.entity_type == EntityType.CREATURE
        assert e.chunk_x == 0
        assert e.chunk_y == 0
        assert e.tile_x == 10
        assert e.tile_y == 5
        assert e.born_at == 100.0
        assert e.controller == Controller.NONE
        assert len(e.id) == 32
        assert e.data is None

    def test_position_property(self):
        """验证 position 属性返回元组。"""
        e = Entity(EntityType.ITEM, 1, 2, None, None, born_at=0.0)
        assert e.position == (1, 2, None, None)
        assert e.chunk == (1, 2)

    def test_global_xy_from_tiles(self):
        """global_xy 由 chunk/tile 整数字段推导。"""
        e = Entity(EntityType.ITEM, 1, 2, 10, 5, born_at=0.0)
        assert e.global_xy == (
            float(1 * TILE_MAP_SIZE + 10), float(2 * TILE_MAP_SIZE + 5),
        )

    def test_global_xy_prefers_float_data(self):
        """data 含 fx/fy 时 global_xy 返回精确 float 坐标。"""
        e = Entity(
            EntityType.CREATURE, 0, 0, 10, 5, born_at=0.0,
            data={"fx": 10.4, "fy": 5.7},
        )
        assert e.global_xy == (10.4, 5.7)

    def test_global_xy_no_tile(self):
        """tile 为 None 时 global_xy 取 chunk 原点。"""
        e = Entity(EntityType.STRUCTURE, 3, 5, None, None, born_at=0.0)
        assert e.global_xy == (
            float(3 * TILE_MAP_SIZE), float(5 * TILE_MAP_SIZE),
        )

    def test_controller_field(self):
        """controller 字段可显式指定。"""
        e = Entity(
            EntityType.CREATURE, 0, 0, 0, 0, born_at=0.0,
            controller=Controller.PLAYER,
        )
        assert e.controller == Controller.PLAYER

    def test_custom_data(self):
        """创建带附加数据的实体。"""
        e = Entity(
            entity_type=EntityType.ITEM,
            chunk_x=1, chunk_y=2, tile_x=None, tile_y=None,
            born_at=0.0,
            data={"name": "木材", "stack": 10},
        )
        assert e.data["name"] == "木材"
        assert e.data["stack"] == 10

    def test_id_uniqueness(self):
        """验证每个实体的 ID 是唯一的。"""
        e1 = Entity(EntityType.CREATURE, 0, 0, 0, 0, born_at=0.0)
        e2 = Entity(EntityType.CREATURE, 0, 0, 0, 0, born_at=0.0)
        assert e1.id != e2.id

    def test_hash(self):
        """验证实体可哈希，可放入集合。"""
        e = Entity(EntityType.CREATURE, 0, 0, 0, 0, born_at=0.0)
        s = {e}
        assert e in s

    def test_set_data_lazy(self):
        """set_data 在 data 为 None 时懒创建字典。"""
        e = Entity(EntityType.CREATURE, 0, 0, 0, 0, born_at=0.0)
        assert e.data is None
        e.set_data("name", "张三")
        assert e.data == {"name": "张三"}

    def test_get_data_safe(self):
        """get_data 在 data 为 None 时安全返回默认值。"""
        e = Entity(EntityType.CREATURE, 0, 0, 0, 0, born_at=0.0)
        assert e.get_data("name") is None
        assert e.get_data("name", "无名") == "无名"

    def test_slots_no_dict(self):
        """验证 Entity 使用 __slots__，没有 __dict__。"""
        e = Entity(EntityType.CREATURE, 0, 0, 0, 0, born_at=0.0)
        assert not hasattr(e, "__dict__")
        import pytest
        with pytest.raises(AttributeError):
            e.random_new_attr = 42

    def test_entity_type_int(self):
        """EntityType 是 int 枚举，可以 int 操作。"""
        assert EntityType.CREATURE == 0
        assert EntityType.PLANT == 1
        assert EntityType.CREATURE < EntityType.PLANT

    def test_no_player_entity_type(self):
        """PLAYER 不是实体类型——被玩家控制是 controller 维度。"""
        assert "PLAYER" not in EntityType.__members__
        assert "PLAYER" in Controller.__members__


class TestSplitCoords:
    """split_coords 坐标拆分测试。"""

    def test_positive(self):
        """正坐标拆分,与 chunk/tile 合成互逆。"""
        cx, cy, tx, ty = split_coords(210.7, 405.2)
        assert 0 <= tx < TILE_MAP_SIZE
        assert 0 <= ty < TILE_MAP_SIZE
        assert (cx * TILE_MAP_SIZE + tx, cy * TILE_MAP_SIZE + ty) == (210, 405)

    def test_negative_floor(self):
        """负坐标 floor 语义:chunk 为负,tile 恒在 [0, TILE_MAP_SIZE)。"""
        cx, cy, tx, ty = split_coords(-0.5, -250.0)
        assert (cx, cy) == (-1, -2)
        assert 0 <= tx < TILE_MAP_SIZE
        assert 0 <= ty < TILE_MAP_SIZE

    def test_origin(self):
        """原点落在 chunk (0,0) tile (0,0)。"""
        assert split_coords(0.0, 0.0) == (0, 0, 0, 0)


class TestEntityManager:
    """EntityManager 生灭和查询测试。"""

    def test_birth(self):
        """实体诞生，验证注册到管理器并发布 entity_born 事件。"""
        wt = WorldTree()
        events = []
        wt.subscribe("entity_born", lambda e: events.append(e))

        mgr = EntityManager(world_tree_arg=wt)
        e = mgr.birth(EntityType.CREATURE, 0, 0, 1, 2, game_time=50.0)

        assert e.entity_type == EntityType.CREATURE
        assert mgr.count == 1
        assert mgr.get(e.id) is e
        assert len(events) == 1
        assert events[0].data["entity_id"] == e.id
        assert events[0].data["controller"] == "NONE"
        assert events[0].data["x"] == float(1)
        assert events[0].data["y"] == float(2)

    def test_birth_with_controller(self):
        """带 controller 的诞生，事件携带控制者。"""
        wt = WorldTree()
        events = []
        wt.subscribe("entity_born", lambda e: events.append(e))

        mgr = EntityManager(world_tree_arg=wt)
        e = mgr.birth(
            EntityType.CREATURE, 0, 0, 1, 2, controller=Controller.AI,
        )
        assert e.controller == Controller.AI
        assert events[0].data["controller"] == "AI"

    def test_birth_with_data(self):
        """诞生带数据的实体。"""
        mgr = EntityManager(world_tree_arg=world_tree)
        e = mgr.birth(EntityType.ITEM, 0, 0, data={"name": "石斧"})
        assert e.data["name"] == "石斧"

    def test_birth_no_tile(self):
        """诞生无 tile 坐标的实体。"""
        mgr = EntityManager(world_tree_arg=world_tree)
        e = mgr.birth(EntityType.STRUCTURE, 3, 5)
        assert e.tile_x is None
        assert e.tile_y is None
        assert e.position == (3, 5, None, None)

    def test_death(self):
        """实体死亡，验证从管理器清除并发布 entity_died 事件。"""
        wt = WorldTree()
        events = []
        wt.subscribe("entity_died", lambda e: events.append(e))

        mgr = EntityManager(world_tree_arg=wt)
        e = mgr.birth(EntityType.CREATURE, 0, 0, 0, 0)
        removed = mgr.death(e.id)

        assert removed is e
        assert mgr.count == 0
        assert mgr.get(e.id) is None
        assert len(events) == 1
        assert events[0].data["entity_id"] == e.id

    def test_death_nonexistent(self):
        """移除不存在的实体，返回 None。"""
        mgr = EntityManager(world_tree_arg=world_tree)
        result = mgr.death("nonexistent")
        assert result is None

    def test_move(self):
        """移动实体，验证位置更新和事件发布。"""
        wt = WorldTree()
        events = []
        wt.subscribe("entity_moved", lambda e: events.append(e))

        mgr = EntityManager(world_tree_arg=wt)
        e = mgr.birth(EntityType.CREATURE, 0, 0, 5, 5)
        success = mgr.move(e.id, 1, 0, 10, 10)

        assert success is True
        assert e.chunk_x == 1
        assert e.tile_x == 10
        assert e.chunk == (1, 0)
        assert len(events) == 1
        assert events[0].data["old_position"] == (0, 0, 5, 5)
        assert events[0].data["new_position"] == (1, 0, 10, 10)

    def test_move_nonexistent(self):
        """移动不存在的实体，返回 False。"""
        mgr = EntityManager(world_tree_arg=world_tree)
        assert mgr.move("nonexistent", 0, 0, 0, 0) is False

    def test_move_same_chunk(self):
        """在同一 chunk 内同 sub-cell 内移动，空间索引不变。"""
        mgr = EntityManager(world_tree_arg=world_tree)
        e = mgr.birth(EntityType.CREATURE, 0, 0, 1, 2)
        mgr.move(e.id, 0, 0, 3, 4)
        assert e.chunk == (0, 0)
        assert len(mgr.in_region((0, 0))) == 1

    def test_all_entities(self):
        """all_entities 返回全部活跃实体。"""
        wt = WorldTree()
        mgr = EntityManager(world_tree_arg=wt)
        e1 = mgr.birth(EntityType.CREATURE, 0, 0, 0, 0)
        e2 = mgr.birth(EntityType.ITEM, 0, 0, 1, 1)
        entities = mgr.all_entities()
        assert len(entities) == 2
        assert {e.id for e in entities} == {e1.id, e2.id}
        mgr.death(e1.id)
        assert len(mgr.all_entities()) == 1

    def test_by_type(self):
        """按类型查询实体。"""
        mgr = EntityManager(world_tree_arg=world_tree)
        mgr.birth(EntityType.CREATURE, 0, 0, 0, 0)
        mgr.birth(EntityType.CREATURE, 0, 0, 1, 1)
        mgr.birth(EntityType.ITEM, 0, 0, 2, 2)

        creatures = mgr.by_type(EntityType.CREATURE)
        items = mgr.by_type(EntityType.ITEM)
        structures = mgr.by_type(EntityType.STRUCTURE)

        assert len(creatures) == 2
        assert len(items) == 1
        assert len(structures) == 0

    def test_in_region(self):
        """按空间区域查询实体。"""
        mgr = EntityManager(world_tree_arg=world_tree)
        mgr.birth(EntityType.CREATURE, 0, 0, 0, 0)
        mgr.birth(EntityType.ITEM, 1, 0, 0, 0)
        mgr.birth(EntityType.ITEM, 2, 2, 0, 0)

        nearby = mgr.in_region((0, 0), radius=1)
        assert len(nearby) == 2

        center = mgr.in_region((0, 0), radius=0)
        assert len(center) == 1

        all_in = mgr.in_region((0, 0), radius=2)
        assert len(all_in) == 3

    def test_type_counts(self):
        """验证类型统计正确。"""
        mgr = EntityManager(world_tree_arg=world_tree)
        mgr.birth(EntityType.CREATURE, 0, 0, 0, 0)
        mgr.birth(EntityType.CREATURE, 0, 0, 1, 1)
        mgr.birth(EntityType.ITEM, 0, 0, 2, 2)

        counts = mgr.type_counts()
        assert counts == {"CREATURE": 2, "ITEM": 1}

    def test_death_updates_indices(self):
        """实体死亡后，类型和空间索引也清理。"""
        mgr = EntityManager(world_tree_arg=world_tree)
        e = mgr.birth(EntityType.CREATURE, 0, 0, 0, 0)
        mgr.death(e.id)

        assert mgr.by_type(EntityType.CREATURE) == []
        assert mgr.in_region((0, 0)) == []

    def test_death_removes_empty_index_buckets(self):
        """死亡后空索引桶被删除（防高周转下空集无界累积）。"""
        wt = WorldTree()
        mgr = EntityManager(world_tree_arg=wt)
        e = mgr.birth(EntityType.CREATURE, 3, 4, 10, 10)
        mgr.death(e.id)

        assert mgr._type_index == {}
        assert mgr._spatial_index == {}
        assert mgr.type_counts() == {}

    def test_move_removes_empty_spatial_bucket(self):
        """跨 sub-cell 移动后，旧空间桶为空时被删除。"""
        wt = WorldTree()
        mgr = EntityManager(world_tree_arg=wt)
        e = mgr.birth(EntityType.CREATURE, 0, 0, 0, 0)
        mgr.move(e.id, 5, 5, 0, 0)

        assert len(mgr._spatial_index) == 1
        mgr.death(e.id)
        assert mgr._spatial_index == {}

    def test_shared_bucket_survives_partial_death(self):
        """同桶多实体时，移除一个不删桶。"""
        wt = WorldTree()
        mgr = EntityManager(world_tree_arg=wt)
        e1 = mgr.birth(EntityType.CREATURE, 0, 0, 1, 1)
        e2 = mgr.birth(EntityType.CREATURE, 0, 0, 2, 2)  # 同 sub-cell (0,0)
        mgr.death(e1.id)

        assert len(mgr.by_type(EntityType.CREATURE)) == 1
        assert len(mgr.in_region((0, 0))) == 1
        assert mgr.get(e2.id) is e2

    def test_move_updates_spatial_index(self):
        """跨 chunk 移动后，空间索引正确更新。"""
        mgr = EntityManager(world_tree_arg=world_tree)
        e = mgr.birth(EntityType.CREATURE, 0, 0, 0, 0)
        mgr.move(e.id, 5, 5, 0, 0)

        assert len(mgr.in_region((0, 0), radius=0)) == 0
        assert len(mgr.in_region((5, 5), radius=0)) == 1

    def test_move_crosses_sub_cell(self):
        """在同一 chunk 内跨越 sub-cell 移动，空间索引更新。"""
        wt = WorldTree()
        mgr = EntityManager(world_tree_arg=wt)
        e = mgr.birth(EntityType.CREATURE, 0, 0, 0, 0)
        mgr.move(e.id, 0, 0, 20, 0)  # 从 sub-cell (0,0) 跨到 (1,0)
        # 旧 sub-cell 找不到
        assert len(mgr.in_region((0, 0), radius=0,
            center_tile=(0, 0), sub_radius=0)) == 0
        # 新 sub-cell 能找到
        assert len(mgr.in_region((0, 0), radius=0,
            center_tile=(20, 0), sub_radius=0)) == 1


# ── in_region tile 级查询 ────────────────────────────


class TestEntityManagerTileQuery:
    """in_region 带 center_tile / sub_radius 的测试。"""

    def test_tile_filter_returns_only_matching(self):
        """仅返回中心 chunk 内匹配 sub-cell 的实体。"""
        wt = WorldTree()
        mgr = EntityManager(world_tree_arg=wt)
        mgr.birth(EntityType.CREATURE, 0, 0, 10, 10)
        mgr.birth(EntityType.CREATURE, 0, 0, 50, 50)
        mgr.birth(EntityType.CREATURE, 0, 0, 11, 11)

        results = mgr.in_region((0, 0), radius=0,
            center_tile=(10, 10), sub_radius=0)
        assert len(results) == 2  # tile (10,10) 和 (11,11) 同在 sub-cell (0,0)

    def test_tile_filter_empty(self):
        """没有匹配时返回空列表。"""
        mgr = EntityManager(world_tree_arg=world_tree)
        mgr.birth(EntityType.CREATURE, 0, 0, 50, 50)
        results = mgr.in_region((0, 0), radius=0,
            center_tile=(10, 10), sub_radius=0)
        assert results == []

    def test_no_tile_filter_returns_all_in_chunk(self):
        """不传 center_tile 时返回 chunk 内全部实体。"""
        wt = WorldTree()
        mgr = EntityManager(world_tree_arg=wt)
        mgr.birth(EntityType.CREATURE, 0, 0, 10, 10)
        mgr.birth(EntityType.CREATURE, 0, 0, 50, 50)
        results = mgr.in_region((0, 0), radius=0)
        assert len(results) == 2

    def test_sub_radius_extends_range(self):
        """sub_radius > 0 覆盖相邻 sub-cell。"""
        wt = WorldTree()
        mgr = EntityManager(world_tree_arg=wt)
        mgr.birth(EntityType.CREATURE, 0, 0, 5, 5)
        mgr.birth(EntityType.CREATURE, 0, 0, 30, 5)
        results = mgr.in_region((0, 0), radius=0,
            center_tile=(5, 5), sub_radius=2)
        assert len(results) == 2

    def test_none_tile_not_excluded(self):
        """tile 为 None 的实体在 chunk 级查询中正常返回。"""
        wt = WorldTree()
        mgr = EntityManager(world_tree_arg=wt)
        mgr.birth(EntityType.STRUCTURE, 0, 0)
        results = mgr.in_region((0, 0), radius=0,
            center_tile=(10, 10), sub_radius=0)
        # tile=None 的实体 key 中 sub_cell=(0,0)，center_tile 的 sub_cell 也是 (0,0)
        assert len(results) == 1
