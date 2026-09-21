"""实例接入位测试 — 实体实例、层级实例与链接/层级关系。

- 实体：存活集合是世界状态（spawn/despawn 进帧事务）；机制按存活实体逐实例
  求值；快照/恢复携带实体集合与逐实体值；干预要求实体存活；
- 链接（link）：源实例经键槽位指向目标实例，父值在目标实例上读取；
- 层级（level）：restrict（子读父，坐标下取整）与 prolong（父读子，聚合）；
- 声明/编译期校验：entity 生命期、层级倍率一致、prolong 必须聚合、
  link 必须给出键槽位。
"""

from __future__ import annotations

import pytest

from olam import (
    Schedule,
    WorldProcess,
    WorldSpec,
    compile_world,
)
from olam.runtime import StateStore
from olam.meta.declarations import (
    InstanceDecl,
    MechanismDecl,
    ModulePack,
    Parent,
    Permissions,
    RelationDecl,
    SlotDecl,
    ValueDomain,
    When,
    Witness,
)
from olam.runtime import FrameFailure

_INT = ValueDomain(kind="int", bits=64)
_KEY = ValueDomain(kind="any")
_WRITE = Permissions(intervene=True, observe=True, record=True)

_GLOBAL = InstanceDecl(id="global", kind="global", identity="singleton")

_PHASES = ("grow", "hold", "gather", "step", "sum", "read")


def _grow(ctx):
    return ctx.parent("wood") + 1


def _hold(ctx):
    return ctx.parent("key")


def _gather(ctx):
    return ctx.parent("stock") + ctx.parent("wood")


def _step(ctx):
    return ctx.parent("v") + 1


def _passthrough(ctx):
    return ctx.parent("parts")


def _passthrough_total(ctx):
    return ctx.parent("total")


_TREE = ModulePack(
    id="toy.forest",
    version="1",
    instances=(
        _GLOBAL,
        InstanceDecl(
            id="entity.tree", kind="entity", identity="derived_id",
            lifecycle="驱动层 spawn/despawn（演练模块）",
        ),
    ),
    slots=(
        SlotDecl(
            id="tree.wood", on="entity.tree", persist="state",
            domain=_INT, permissions=_WRITE, writer="tree.grow",
            initial=0,
        ),
    ),
    mechanisms=(
        MechanismDecl(
            id="tree.grow", output="tree.wood",
            parents=(Parent("tree.wood", "wood", lag=1),),
            impl=_grow, when=When("phase", "grow"),
            witnesses=(
                Witness("w0", {"wood": 0}, (1,)),
                Witness("w1", {"wood": 1}, (2,)),
            ),
        ),
    ),
)

_HUT = ModulePack(
    id="toy.hut",
    version="1",
    instances=(
        InstanceDecl(
            id="entity.hut", kind="entity", identity="derived_id",
            lifecycle="驱动层 spawn/despawn（演练模块）",
        ),
    ),
    relations=(
        RelationDecl(
            id="hut.target", kind="link", source="entity.hut",
            target="entity.tree", key_slot="hut.link_key",
        ),
    ),
    slots=(
        SlotDecl(
            id="hut.link_key", on="entity.hut", persist="state",
            domain=_KEY, permissions=_WRITE, writer="hut.hold",
            initial="",
        ),
        SlotDecl(
            id="hut.stock", on="entity.hut", persist="state",
            domain=_INT, permissions=_WRITE, writer="hut.gather",
            initial=0,
        ),
    ),
    mechanisms=(
        MechanismDecl(
            id="hut.hold", output="hut.link_key",
            parents=(Parent("hut.link_key", "key", lag=1),),
            impl=_hold, when=When("phase", "hold"),
            witnesses=(
                Witness("k0", {"key": "a"}, ("a",)),
                Witness("k1", {"key": "b"}, ("b",)),
            ),
        ),
        MechanismDecl(
            id="hut.gather", output="hut.stock",
            parents=(
                Parent("hut.stock", "stock", lag=1),
                Parent("tree.wood", "wood", relation="hut.target"),
            ),
            impl=_gather, when=When("phase", "gather"),
            witnesses=(
                Witness("g0", {"stock": 0, "wood": 0}, (0,)),
                Witness("g1", {"stock": 1, "wood": 0}, (1,)),
                Witness("g2", {"stock": 0, "wood": 2}, (2,)),
            ),
        ),
    ),
)

_REGION = ModulePack(
    id="toy.region",
    version="1",
    instances=(
        InstanceDecl(
            id="lattice.region", kind="lattice", identity="xy",
            size=(2,), axes=("x",),
        ),
        InstanceDecl(
            id="lattice.cell", kind="lattice", identity="xy",
            size=(4,), axes=("x",),
            parent="lattice.region", ratio=2,
        ),
    ),
    relations=(
        RelationDecl(
            id="cell.of_region", kind="level",
            source="lattice.cell", target="lattice.region",
        ),
    ),
    slots=(
        SlotDecl(
            id="cell.value", on="lattice.cell", persist="state",
            domain=_INT, permissions=_WRITE, writer="cell.step",
            initial=0,
        ),
        SlotDecl(
            id="region.total", on="lattice.region", persist="state",
            domain=_INT, permissions=_WRITE, writer="region.sum",
            initial=0,
        ),
        SlotDecl(
            id="cell.region_total", on="lattice.cell", persist="state",
            domain=_INT, permissions=_WRITE, writer="cell.read_region",
            initial=0,
        ),
    ),
    mechanisms=(
        MechanismDecl(
            id="cell.step", output="cell.value",
            parents=(Parent("cell.value", "v", lag=1),),
            impl=_step, when=When("phase", "step"),
            witnesses=(
                Witness("c0", {"v": 0}, (1,)),
                Witness("c1", {"v": 1}, (2,)),
            ),
        ),
        MechanismDecl(
            id="region.sum", output="region.total",
            parents=(
                Parent(
                    "cell.value", "parts",
                    relation="cell.of_region", aggregation="sum",
                ),
            ),
            impl=_passthrough, when=When("phase", "sum"),
            witnesses=(
                Witness("s0", {"parts": 0}, (0,)),
                Witness("s1", {"parts": 3}, (3,)),
            ),
        ),
        MechanismDecl(
            id="cell.read_region", output="cell.region_total",
            parents=(
                Parent("region.total", "total", relation="cell.of_region"),
            ),
            impl=_passthrough_total, when=When("phase", "read"),
            witnesses=(
                Witness("r0", {"total": 0}, (0,)),
                Witness("r1", {"total": 5}, (5,)),
            ),
        ),
    ),
)

_PROGRAM = None


def _program():
    global _PROGRAM
    if _PROGRAM is None:
        _PROGRAM = compile_world(
            WorldSpec(
                modules=(_TREE, _HUT, _REGION),
                schedule=Schedule(phases=_PHASES),
            )
        )
    return _PROGRAM


class TestEntityLifecycle:
    def test_spawn_and_despawn_are_transactional(self):
        store = StateStore()
        store.spawn("entity.tree", "t1", {"tree.wood": 0})
        assert store.entities("entity.tree") == ("t1",)
        store.abort()
        assert store.entities("entity.tree") == ()

        store.spawn("entity.tree", "t1", {"tree.wood": 0})
        store.commit()
        assert store.entities("entity.tree") == ("t1",)
        store.despawn("entity.tree", "t1", ("tree.wood",))
        assert store.entities("entity.tree") == ()
        store.commit()
        assert store.entities("entity.tree") == ()

    def test_despawn_drops_values_at_commit(self):
        store = StateStore()
        store.spawn("entity.tree", "t1", {"tree.wood": 7})
        store.commit()
        store.despawn("entity.tree", "t1", ("tree.wood",))
        store.commit()
        assert store.committed("tree.wood").contains(("t1",)) is False

    def test_duplicate_spawn_rejected(self):
        process = WorldProcess(_program())
        process.spawn_entity("entity.tree", "t1")
        with pytest.raises(ValueError, match="已存在"):
            process.spawn_entity("entity.tree", "t1")

    def test_despawn_missing_rejected(self):
        process = WorldProcess(_program())
        with pytest.raises(ValueError, match="不存在"):
            process.despawn_entity("entity.tree", "ghost")

    def test_spawn_requires_entity_kind(self):
        process = WorldProcess(_program())
        with pytest.raises(ValueError, match="未知实体实例类型"):
            process.spawn_entity("global", "g1")


class TestEntityEvaluation:
    def test_mechanism_runs_per_live_entity(self):
        process = WorldProcess(_program())
        process.spawn_entity("entity.tree", "t1")
        process.spawn_entity("entity.tree", "t2")
        process.step()
        wood = process.committed("tree.wood")
        assert wood.get(("t1",)) == 1
        assert wood.get(("t2",)) == 1

    def test_despawned_entity_leaves_evaluation(self):
        process = WorldProcess(_program())
        process.spawn_entity("entity.tree", "t1")
        process.spawn_entity("entity.tree", "t2")
        process.step()
        process.despawn_entity("entity.tree", "t1")
        process.step()
        assert process.entities("entity.tree") == ("t2",)
        wood = process.committed("tree.wood")
        assert not wood.contains(("t1",))
        assert wood.get(("t2",)) == 2

    def test_entity_intervention_on_live_entity(self):
        process = WorldProcess(_program())
        process.spawn_entity("entity.tree", "t1")
        process.step()
        process.step(interventions={"tree.wood": {("t1",): 9}})
        assert process.committed("tree.wood").get(("t1",)) == 9
        process.step()
        assert process.committed("tree.wood").get(("t1",)) == 10

    def test_entity_intervention_on_missing_entity_rejected(self):
        process = WorldProcess(_program())
        with pytest.raises(FrameFailure, match="干预实体不存在"):
            process.step(interventions={"tree.wood": {("ghost",): 1}})

    def test_entity_snapshot_round_trip(self):
        process = WorldProcess(_program(), seed=5)
        process.spawn_entity("entity.tree", "t1")
        process.spawn_entity("entity.tree", "t2")
        for _ in range(3):
            process.step()
        snapshot = process.snapshot()
        assert snapshot["entities"] == {"entity.tree": ["t1", "t2"]}

        restored = WorldProcess.restore(_program(), snapshot, seed=5)
        assert restored.entities("entity.tree") == ("t1", "t2")
        process.step()
        restored.step()
        assert (
            restored.committed("tree.wood").items()
            == process.committed("tree.wood").items()
        )

    def test_materialize_rejects_entity_kind(self):
        process = WorldProcess(_program())
        with pytest.raises(ValueError, match="spawn_entity"):
            process.materialize("entity.tree", ("t1",))


class TestLinkRelation:
    def test_link_reads_target_slot(self):
        process = WorldProcess(_program())
        process.spawn_entity("entity.tree", "t1")
        process.spawn_entity("entity.hut", "h1")
        # 键槽位指向 t1（干预写入状态），目标树的木材值同时设定
        process.step(interventions={
            "hut.link_key": {("h1",): "t1"},
            "tree.wood": {("t1",): 5},
        })
        # 首帧：stock = 0 + 链接目标 5
        assert process.committed("hut.stock").get(("h1",)) == 5
        # 下一帧：tree.grow 先写 6（同帧因果），链接读到 6
        process.step(interventions={
            "hut.link_key": {("h1",): "t1"},
        })
        assert process.committed("tree.wood").get(("t1",)) == 6
        assert process.committed("hut.stock").get(("h1",)) == 11


class TestLevelRelation:
    def test_restrict_and_prolong(self):
        process = WorldProcess(_program())
        process.step()
        values = process.committed("cell.value").values()
        assert values == (1, 1, 1, 1)
        region = process.committed("region.total").values()
        assert region == (2, 2)
        totals = process.committed("cell.region_total").values()
        assert totals == (2, 2, 2, 2)

    def test_prolong_reads_previous_frame_lag(self):
        """prolong 子值在本帧先更新：区域求和读到本帧子值（同帧因果）。"""
        process = WorldProcess(_program())
        process.step()
        process.step()
        assert process.committed("region.total").values() == (4, 4)
        assert process.committed("cell.region_total").values() == (4, 4, 4, 4)


class TestDeclarationChecks:
    def _compile(self, pack: ModulePack):
        return compile_world(
            WorldSpec(modules=(pack,), schedule=Schedule(phases=_PHASES))
        )

    def test_entity_requires_lifecycle(self):
        with pytest.raises(ValueError, match="lifecycle"):
            InstanceDecl(id="entity.x", kind="entity", identity="derived_id")

    def test_hierarchy_size_mismatch_rejected(self):
        pack = ModulePack(
            id="toy.bad_level",
            version="1",
            instances=(
                InstanceDecl(
                    id="lattice.region", kind="lattice", identity="xy",
                    size=(2,), axes=("x",),
                ),
                InstanceDecl(
                    id="lattice.cell", kind="lattice", identity="xy",
                    size=(5,), axes=("x",),
                    parent="lattice.region", ratio=2,
                ),
            ),
        )
        with pytest.raises(Exception, match="层级倍率"):
            self._compile(pack)

    def test_prolong_requires_aggregation(self):
        pack = ModulePack(
            id="toy.bad_prolong",
            version="1",
            instances=_REGION.instances,
            relations=_REGION.relations,
            slots=_REGION.slots,
            mechanisms=(
                _REGION.mechanisms[0],
                _REGION.mechanisms[2],
                MechanismDecl(
                    id="region.sum", output="region.total",
                    parents=(
                        Parent("cell.value", "parts",
                               relation="cell.of_region"),
                    ),
                    impl=_passthrough, when=When("phase", "sum"),
                    witnesses=(
                        Witness("s0", {"parts": 0}, (0,)),
                        Witness("s1", {"parts": 3}, (3,)),
                    ),
                ),
            ),
        )
        with pytest.raises(Exception, match="必须声明聚合"):
            self._compile(pack)

    def test_link_requires_key_slot(self):
        with pytest.raises(ValueError, match="key_slot"):
            RelationDecl(
                id="hut.target", kind="link", source="entity.hut",
                target="entity.tree",
            )

    def test_level_requires_parent_match(self):
        pack = ModulePack(
            id="toy.bad_level_rel",
            version="1",
            instances=(
                InstanceDecl(
                    id="lattice.region", kind="lattice", identity="xy",
                    size=(2,), axes=("x",),
                ),
                InstanceDecl(
                    id="lattice.other", kind="lattice", identity="xy",
                    size=(2,), axes=("x",),
                ),
                InstanceDecl(
                    id="lattice.cell", kind="lattice", identity="xy",
                    size=(4,), axes=("x",),
                    parent="lattice.region", ratio=2,
                ),
            ),
            relations=(
                RelationDecl(
                    id="cell.of_other", kind="level",
                    source="lattice.cell", target="lattice.other",
                ),
            ),
        )
        with pytest.raises(Exception, match="层级父"):
            self._compile(pack)

    def test_link_key_slot_must_be_on_source(self):
        pack = ModulePack(
            id="toy.bad_link",
            version="1",
            instances=_HUT.instances,
            relations=(
                RelationDecl(
                    id="hut.target", kind="link", source="entity.hut",
                    target="entity.tree", key_slot="tree.wood",
                ),
            ),
            slots=_HUT.slots,
            mechanisms=_HUT.mechanisms,
        )
        with pytest.raises(Exception, match="链接键槽位"):
            self._compile(pack)
