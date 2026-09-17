"""世界程序编译测试：波次、内核绑定、更新点、身份与 fail-closed 校验。"""

from dataclasses import replace
from types import SimpleNamespace

import pytest

from ascend.causal.fate_registry import load_fate_namespaces
from ascend.causal.program import (
    WorldProgram,
    compile_default_program,
    compile_world_program,
)
from ascend.causal.state_schema import SlotUpdate, load_declaration
from ascend.causal.update_points import load_update_points
from ascend.causal.world import ASCEND_MECHANISMS
from ascend.config import GAME_DAY, GAME_HOUR, GAME_MINUTE

TICKS = {
    "game_minute": GAME_MINUTE,
    "game_hour": GAME_HOUR,
    "game_day": GAME_DAY,
}


def _identity_fn(**kwargs) -> None:
    """合成机制的占位实现（稳定名称，供身份投影）。"""
    return None


def _node(microstep: str) -> SimpleNamespace:
    return SimpleNamespace(update=SimpleNamespace(microstep=microstep))


def _parent(
    name: str, *, lag: int = 0, source_microstep: str = "a",
) -> SimpleNamespace:
    return SimpleNamespace(
        parent=name, lag=lag, source_microstep=source_microstep,
    )


def _mechanism(
    mechanism_id: str,
    output: str,
    parents: tuple = (),
) -> SimpleNamespace:
    return SimpleNamespace(
        mechanism_id=mechanism_id,
        output=output,
        parents=tuple(parents),
        function=_identity_fn,
    )


def _stub_registry(
    mechanisms: list,
    nodes: dict,
    microsteps: tuple = ("a", "b", "c"),
) -> SimpleNamespace:
    return SimpleNamespace(
        microstep_order=microsteps,
        nodes={node_id: _node(step) for node_id, step in nodes.items()},
        mechanisms={m.mechanism_id: m for m in mechanisms},
        declaration_hash="sha256:" + "ab" * 32,
        wired_nodes=frozenset(),
        equation_version=lambda output: f"eq:{output}",
        resolved_version=lambda output: f"res:{output}",
    )


def _compile(registry, **overrides) -> WorldProgram:
    kwargs = {
        "state": load_declaration(),
        "addresses": load_fate_namespaces(),
        "points": load_update_points(),
        "ticks": dict(TICKS),
    }
    kwargs.update(overrides)
    return compile_world_program(registry, **kwargs)


class TestDefaultProgram:
    def test_identity_format_and_stable(self):
        first = compile_default_program()
        second = compile_default_program()
        assert isinstance(first, WorldProgram)
        assert first.identity == second.identity
        assert first.identity.startswith("sha256:")
        assert len(first.identity) == 71

    def test_waves_cover_every_mechanism_once(self):
        program = compile_default_program()
        in_waves = [m for wave in program.waves for m in wave.mechanisms]
        assert sorted(in_waves) == sorted(ASCEND_MECHANISMS.mechanisms)
        assert len(in_waves) == len(set(in_waves))

    def test_waves_follow_microstep_order(self):
        program = compile_default_program()
        positions = {
            name: index for index, name in enumerate(program.microsteps)
        }
        seen = [positions[wave.microstep] for wave in program.waves]
        assert seen == sorted(seen)
        for index, wave in enumerate(program.waves):
            assert wave.index == index
            for output in wave.outputs:
                assert program.kernels[output].microstep == wave.microstep

    def test_waves_have_no_intra_wave_same_frame_edge(self):
        program = compile_default_program()
        wave_of = {
            output: wave.index
            for wave in program.waves
            for output in wave.outputs
        }
        for spec in ASCEND_MECHANISMS.mechanisms.values():
            for parent in spec.parents:
                if parent.lag != 0 or parent.parent not in wave_of:
                    continue
                assert wave_of[parent.parent] < wave_of[spec.output], (
                    f"{spec.mechanism_id} 的同帧父 {parent.parent} "
                    "必须位于更早波次"
                )

    def test_kernels_bind_reference_and_placeholder(self):
        program = compile_default_program()
        for output, kernel in program.kernels.items():
            assert callable(kernel.reference)
            assert kernel.accelerated is None
            assert kernel.equation_version
            assert kernel.resolved_version
            assert kernel.wired == (
                output in ASCEND_MECHANISMS.wired_nodes
            )

    def test_update_points_resolved_to_ticks(self):
        program = compile_default_program()
        weather = program.update_point("weather.evaluate")
        assert weather.order == 10
        assert weather.period_ticks == GAME_MINUTE
        assert weather.slots == ()
        terrain = program.update_point("terrain.integrate")
        assert terrain.order == 20
        assert terrain.period_ticks == GAME_HOUR
        assert terrain.slots == (
            "world.terrain.moisture",
            "world.terrain.snow",
            "world.terrain.ice",
            "world.terrain.integrated_through",
        )
        with pytest.raises(KeyError, match="无此更新点"):
            program.update_point("unknown.point")

    def test_world_identity_seed_sensitive(self):
        program = compile_default_program()
        assert program.world_identity(1) != program.world_identity(2)
        assert program.world_identity(0).startswith("sha256:")
        with pytest.raises(ValueError, match="非负整数"):
            program.world_identity(-1)
        with pytest.raises(ValueError, match="非负整数"):
            program.world_identity("1")


class TestIdentitySensitivity:
    def test_calendar_change_changes_identity(self):
        first = compile_default_program()
        second = _compile(
            ASCEND_MECHANISMS,
            ticks={**TICKS, "game_hour": GAME_HOUR * 2},
            state=load_declaration(),
        )
        assert first.identity != second.identity

    def test_update_point_change_changes_identity(self):
        points = load_update_points()
        changed = replace(points, points=(
            replace(points.require("weather.evaluate"), period="game_hour"),
            points.require("terrain.integrate"),
        ))
        first = compile_default_program()
        second = _compile(ASCEND_MECHANISMS, points=changed)
        assert first.identity != second.identity

    def test_unknown_tick_key_rejected(self):
        with pytest.raises(ValueError, match="ticks 键不匹配"):
            _compile(
                ASCEND_MECHANISMS,
                ticks={**TICKS, "game_week": 1},
            )

    def test_ticks_not_mapping_rejected(self):
        with pytest.raises(ValueError, match="ticks 必须为映射"):
            _compile(ASCEND_MECHANISMS, ticks=[("game_minute", 1)])

    def test_missing_tick_rejected(self):
        with pytest.raises(ValueError, match="缺少"):
            _compile(
                ASCEND_MECHANISMS,
                ticks={"game_minute": GAME_MINUTE, "game_hour": GAME_HOUR},
            )

    def test_nonpositive_tick_rejected(self):
        with pytest.raises(ValueError, match="正整数"):
            _compile(ASCEND_MECHANISMS, ticks={**TICKS, "game_day": 0})

    def test_kernel_for_unknown_raises(self):
        program = compile_default_program()
        with pytest.raises(KeyError, match="无此内核"):
            program.kernel_for("nope")


class TestStaticValidation:
    def test_double_writer_rejected(self):
        registry = _stub_registry(
            [
                _mechanism("m.a", "n.one"),
                _mechanism("m.b", "n.one"),
            ],
            {"n.one": "a"},
        )
        with pytest.raises(ValueError, match="多个写者"):
            _compile(registry)

    def test_same_frame_parent_must_be_earlier(self):
        registry = _stub_registry(
            [
                _mechanism(
                    "m.a", "n.one",
                    [_parent("n.two", source_microstep="a")],
                ),
            ],
            {"n.one": "a", "n.two": "a"},
        )
        with pytest.raises(ValueError, match="更早微步"):
            _compile(registry)

    def test_lagged_parent_may_share_microstep(self):
        registry = _stub_registry(
            [
                _mechanism(
                    "m.a", "n.one",
                    [_parent("n.two", lag=1, source_microstep="a")],
                ),
            ],
            {"n.one": "a", "n.two": "a"},
        )
        program = _compile(registry)
        assert "n.one" in program.kernels

    def test_parent_microstep_lie_rejected(self):
        """机制声明的源微步与父节点实际更新微步不一致：编译兜底拒绝。"""
        registry = _stub_registry(
            [
                _mechanism(
                    "m.a", "n.one",
                    [_parent("n.two", source_microstep="b")],
                ),
            ],
            {"n.one": "b", "n.two": "a"},
        )
        with pytest.raises(ValueError, match="源微步"):
            _compile(registry)

    def test_parent_actual_microstep_used_for_order(self):
        """同一实际微步的父即使声明的源微步更早，也拒绝（防御深度）。"""
        registry = _stub_registry(
            [
                _mechanism(
                    "m.a", "n.one",
                    [_parent("n.two", source_microstep="b")],
                ),
            ],
            {"n.one": "b", "n.two": "b"},
        )
        with pytest.raises(ValueError, match="更早微步"):
            _compile(registry)

    def test_duplicate_microstep_rejected(self):
        registry = _stub_registry(
            [_mechanism("m.a", "n.one")],
            {"n.one": "a"},
            microsteps=("a", "a"),
        )
        with pytest.raises(ValueError, match="微步重复"):
            _compile(registry)

    def test_empty_microstep_rejected(self):
        registry = _stub_registry(
            [_mechanism("m.a", "n.one")],
            {"n.one": "a"},
            microsteps=("", "b"),
        )
        with pytest.raises(ValueError, match="微步名非法"):
            _compile(registry)

    def test_unknown_output_microstep_rejected(self):
        registry = _stub_registry(
            [_mechanism("m.a", "n.one")],
            {"n.one": "zz"},
        )
        with pytest.raises(ValueError, match="输出微步未声明"):
            _compile(registry)

    def test_unknown_parent_rejected(self):
        registry = _stub_registry(
            [
                _mechanism(
                    "m.a", "n.one",
                    [_parent("n.missing", source_microstep="a")],
                ),
            ],
            {"n.one": "b"},
        )
        with pytest.raises(ValueError, match="父节点未声明"):
            _compile(registry)

    def test_point_slot_stage_mismatch_rejected(self):
        points = load_update_points()
        changed = replace(points, points=(
            points.require("weather.evaluate"),
            replace(points.require("terrain.integrate"),
                    id="terrain.integrate.v2"),
        ))
        with pytest.raises(ValueError, match="与更新点不符"):
            _compile(ASCEND_MECHANISMS, points=changed)

    def test_slot_stage_without_point_rejected(self):
        state = load_declaration()
        slots = tuple(
            replace(
                slot,
                update=SlotUpdate(kind="mechanism", stage="terrain.other"),
            )
            if slot.id == "world.terrain.moisture"
            else slot
            for slot in state.slots
        )
        with pytest.raises(ValueError, match="无对应更新点"):
            _compile(ASCEND_MECHANISMS, state=replace(state, slots=slots))

    def test_slot_missing_from_point_slots_rejected(self):
        """反向覆盖：槽位声明的阶段存在，但更新点 slots 漏列该槽位。"""
        points = load_update_points()
        terrain = points.require("terrain.integrate")
        changed = replace(points, points=(
            points.require("weather.evaluate"),
            replace(
                terrain,
                slots=tuple(
                    slot_id for slot_id in terrain.slots
                    if slot_id != "world.terrain.ice"
                ),
            ),
        ))
        with pytest.raises(ValueError, match="未在更新点 slots 中声明"):
            _compile(ASCEND_MECHANISMS, points=changed)

    def test_driver_slot_claimed_by_point_rejected(self):
        points = load_update_points()
        changed = replace(points, points=(
            replace(
                points.require("weather.evaluate"),
                slots=("world.clock.tick",),
            ),
            points.require("terrain.integrate"),
        ))
        with pytest.raises(ValueError, match="不是 mechanism"):
            _compile(ASCEND_MECHANISMS, points=changed)

    def test_duplicate_order_rejected(self):
        points = load_update_points()
        changed = replace(points, points=(
            points.require("weather.evaluate"),
            replace(points.require("terrain.integrate"), order=10),
        ))
        with pytest.raises(ValueError, match="重复执行顺序"):
            _compile(ASCEND_MECHANISMS, points=changed)
