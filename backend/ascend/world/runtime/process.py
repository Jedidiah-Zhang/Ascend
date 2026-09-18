"""世界进程 — 帧事务求值与提交（WC-7.6 / WC-9.2）。

一帧 = 一次 ``step``：

1. 按更新点计划求值到**影子状态**（本帧更早的写入对后序机制可见）；
2. 不变量检查（``reject`` 级失败即帧失败）；
3. **整帧提交**（一次性发布；外部读者只能在帧边界看到新状态）；
4. 提交后记录回调（失败即世界失效）。

失败语义（三相位）：

- **提交前失败**：状态不变、可重试（:class:`FrameFailure`）；
- **提交中失败**：可能部分发布且不可回滚 → :class:`WorldInvalidatedError`，
  轨迹作废，不重试不重放；
- **提交后失败**（记录回调）：状态已可见 → 同样世界失效，不重放。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Mapping

from ascend.world.runtime.evaluate import evaluate_mechanism
from ascend.world.runtime.state import DynamicField, LatticeField, StateStore

__all__ = [
    "FrameFailure",
    "FrameResult",
    "WorldInvalidatedError",
    "WorldProcess",
]

_CLOCK_SLOT = "world.clock.tick"


class FrameFailure(RuntimeError):
    """提交前失败：状态不变，可重试。"""


class WorldInvalidatedError(RuntimeError):
    """提交中/提交后失败：轨迹作废（WC-9.2），不重试不重放。"""


@dataclass(frozen=True, slots=True)
class FrameResult:
    """一次成功提交的帧结果（记录回调用）。"""

    tick: int
    groups: tuple[str, ...]
    writes: Mapping[str, object]
    violations: tuple[str, ...] = ()


class WorldProcess:
    """编译产物的运行实例：状态容器 + 帧事务。"""

    def __init__(
        self,
        program: object,
        *,
        seed: int | None = None,
        initial_state: Mapping[str, object] | None = None,
        tick: int = 0,
    ) -> None:
        if type(tick) is not int or tick < 0:
            raise ValueError(f"初始 tick 必须为非负整数: {tick!r}")
        self._program = program
        self._seed = program.seed if seed is None else seed
        self._store = StateStore(program.max_lag)
        self._tick = tick
        self._invalidated = False
        self._store.initialize(self._initial_values(initial_state))

    # ── 只读视图 ────────────────────────────────────────────────

    @property
    def program(self) -> object:
        return self._program

    @property
    def seed(self) -> int:
        return self._seed

    @property
    def tick(self) -> int:
        return self._tick

    @property
    def invalidated(self) -> bool:
        """世界是否已失效（WC-9.2）：失效后只允许读，不允许推进。"""
        return self._invalidated

    def committed(self, slot_id: str) -> object:
        """读已提交值（研究层观测/报告用；帧内影子不可见）。"""
        return self._store.committed(slot_id)

    def field(self, slot_id: str) -> LatticeField:
        """读已提交场（标量槽位即拒绝）。"""
        value = self._store.committed(slot_id)
        if not isinstance(value, LatticeField):
            raise TypeError(f"槽位 {slot_id} 不是场")
        return value

    # ── 物化（运行时视图；物化无关，WC-2.3）────────────────────

    def materialize(self, kind: str, coords: tuple[int, ...]) -> None:
        """物化一个实例（如 chunk）：为动态槽位补初值。"""
        for slot in self._program.slots.values():
            if slot.on != kind:
                continue
            if slot.persist in ("state", "derived"):
                self._store.ensure_value(slot.id, coords, slot.initial)
        self._store.materialize(kind, coords)

    def dematerialize(self, kind: str, coords: tuple[int, ...]) -> None:
        """卸载实例：丢弃其值（持久化由存档层负责，P2-3）。"""
        self._store.dematerialize(kind, coords)

    def materialized(self, kind: str) -> tuple[tuple[int, ...], ...]:
        """已物化实例坐标（升序）。"""
        return self._store.materialized(kind)

    def seed_at(
        self,
        slot_id: str,
        coords: tuple[int, ...],
        value: object,
    ) -> None:
        """装载外部状态到已物化实例（适配器导入引擎数组用；提交语义）。"""
        slot = self._program.slots.get(slot_id)
        if slot is None:
            raise ValueError(f"槽位未声明: {slot_id}")
        instance = self._program.instances[slot.on]
        if instance.kind != "lattice" or instance.size is not None:
            raise ValueError(f"槽位 {slot_id} 不是动态实例槽位")
        if tuple(coords) not in self._store.materialized(instance.id):
            raise ValueError(f"实例未物化: {slot_id}@{coords!r}")
        self._store.set_committed_at(slot_id, tuple(coords), value)

    # ── 帧推进 ──────────────────────────────────────────────────

    def step(
        self,
        *,
        inputs: Mapping[str, object] | None = None,
        interventions: Mapping[str, object] | None = None,
        parameters: Mapping[str, object] | None = None,
        events: tuple[str, ...] = (),
        record: Callable[[FrameResult], None] | None = None,
    ) -> FrameResult:
        """推进一帧。

        ``interventions`` 为本帧值替换（WC-6.1）：标量槽位给值，动态槽位给
        ``{坐标: 值}`` 映射。被替换实例断开原入边（其写者本帧不为该实例
        求值；不消费随机地址，地址纯函数保证其他机制不受影响）。
        ``parameters`` 为本帧参数覆盖（环境变化，独立报告）。
        """
        tick = self._tick + 1
        if self._invalidated:
            raise WorldInvalidatedError(
                "世界已失效（WC-9.2）：拒绝推进"
            )
        frame_inputs = dict(inputs or {})
        if _CLOCK_SLOT in self._program.slots:
            frame_inputs.setdefault(_CLOCK_SLOT, tick)
        replaced = dict(interventions or {})
        scalar_replaced = {
            slot: value
            for slot, value in replaced.items()
            if not isinstance(value, Mapping)
        }
        dynamic_replaced = {
            slot: {tuple(coords): item for coords, item in value.items()}
            for slot, value in replaced.items()
            if isinstance(value, Mapping)
        }
        self._validate_interventions(scalar_replaced, dynamic_replaced)
        frame_params = dict(self._program.parameters)
        frame_params.update(parameters or {})
        due = self._program.due_groups(tick, tuple(events))
        writes: dict[str, object] = {}
        skip = {
            slot: frozenset(mapping)
            for slot, mapping in dynamic_replaced.items()
        }
        try:
            for slot, value in scalar_replaced.items():
                self._store.write(slot, value)
            for slot, mapping in dynamic_replaced.items():
                for coords, item in mapping.items():
                    self._store.write_at(slot, coords, item)
            for group in due:
                for mechanism in group.mechanisms:
                    if all(
                        slot in scalar_replaced
                        for slot in mechanism.outputs()
                    ):
                        continue
                    for slot, value in evaluate_mechanism(
                        self._program,
                        self._store,
                        mechanism,
                        root_seed=self._seed,
                        tick=tick,
                        inputs=frame_inputs,
                        params=frame_params,
                        skip=skip,
                    ).items():
                        writes[slot] = value
                        self._store.write(slot, value)
            for slot, value in scalar_replaced.items():
                writes[slot] = value
            for slot, mapping in dynamic_replaced.items():
                field = writes.get(slot)
                if not isinstance(field, DynamicField):
                    field = DynamicField()
                for coords, item in mapping.items():
                    field.set(coords, item)
                writes[slot] = field
            violations = self._check_invariants(writes)
        except FrameFailure:
            self._store.abort()
            raise
        except Exception as exc:  # noqa: BLE001 - 帧内异常统一转可重试失败
            self._store.abort()
            raise FrameFailure(f"帧 {tick} 提交前失败: {exc!r}") from exc

        try:
            committed = self._store.commit()
        except Exception as exc:  # noqa: BLE001 - 提交中失败即世界失效
            self._store.abort()
            self._invalidated = True
            raise WorldInvalidatedError(
                f"帧 {tick} 提交中失败，世界失效: {exc!r}"
            ) from exc
        self._tick = tick
        result = FrameResult(
            tick=tick,
            groups=tuple(group.id for group in due),
            writes=committed,
            violations=violations,
        )
        if record is not None:
            try:
                record(result)
            except Exception as exc:  # noqa: BLE001 - 提交后失败即世界失效
                self._invalidated = True
                raise WorldInvalidatedError(
                    f"帧 {tick} 提交后记录失败，世界失效: {exc!r}"
                ) from exc
        return result

    # ── 快照与恢复 ──────────────────────────────────────────────

    def snapshot(self) -> dict[str, object]:
        """完整状态快照（只含 state 槽位；派生值重算，WC-8.1）。"""
        state_slots = tuple(
            sorted(
                slot.id
                for slot in self._program.slots.values()
                if slot.persist == "state"
            )
        )
        snapshot = self._store.snapshot(state_slots)
        return {
            "identity": self._program.world_identity(self._seed),
            "seed": self._seed,
            "tick": self._tick,
            "materialized": self._store.materialized_sets(),
            "states": snapshot["states"],
            "history": snapshot["history"],
        }

    @classmethod
    def restore(
        cls,
        program: object,
        snapshot: Mapping[str, object],
        *,
        seed: int | None = None,
    ) -> "WorldProcess":
        """读档（WC-8.3）：身份不符、槽位缺失或多余即拒绝。"""
        unit_seed = program.seed if seed is None else seed
        if snapshot.get("identity") != program.world_identity(unit_seed):
            raise ValueError("世界身份不符，拒绝加载")
        state_slots = {
            slot.id
            for slot in program.slots.values()
            if slot.persist == "state"
        }
        states = dict(snapshot.get("states", {}))  # type: ignore[arg-type]
        missing = state_slots - set(states)
        if missing:
            raise ValueError(f"快照缺少状态槽位: {sorted(missing)}")
        extra = set(states) - state_slots
        if extra:
            raise ValueError(f"快照含未声明状态槽位: {sorted(extra)}")
        process = cls(
            program,
            seed=unit_seed,
            tick=int(snapshot["tick"]),  # type: ignore[arg-type]
        )
        process._store.restore(
            states,
            snapshot.get("history"),  # type: ignore[arg-type]
        )
        process._store.restore_materialized(
            dict(snapshot.get("materialized", {}))  # type: ignore[arg-type]
        )
        return process

    # ── 内部 ────────────────────────────────────────────────────

    def _validate_interventions(
        self,
        scalar_replaced: Mapping[str, object],
        dynamic_replaced: Mapping[str, Mapping[tuple[int, ...], object]],
    ) -> None:
        """干预目标校验（WC-6.3）：存在、状态/派生槽位、允许干预、实例已物化。"""
        for slot_id in scalar_replaced:
            self._check_target(slot_id)
        for slot_id, mapping in dynamic_replaced.items():
            slot = self._check_target(slot_id)
            instance = self._program.instances[slot.on]
            if instance.kind != "lattice" or instance.size is not None:
                raise FrameFailure(
                    f"槽位 {slot_id} 不是动态实例槽位，不能按实例替换"
                )
            materialized = set(self._store.materialized(instance.id))
            for coords in mapping:
                if coords not in materialized:
                    raise FrameFailure(
                        f"干预实例未物化: {slot_id}@{coords!r}"
                    )

    def _check_target(self, slot_id: str) -> object:
        slot = self._program.slots.get(slot_id)
        if slot is None:
            raise FrameFailure(f"干预目标未声明: {slot_id}")
        if slot.persist not in ("state", "derived"):
            raise FrameFailure(
                f"干预目标必须是状态/派生槽位: {slot_id}（{slot.persist}）"
            )
        if not slot.permissions.intervene:
            raise FrameFailure(f"槽位不允许干预: {slot_id}")
        return slot

    def _initial_values(
        self,
        provided: Mapping[str, object] | None,
    ) -> dict[str, object]:
        remaining = dict(provided or {})
        values: dict[str, object] = {}
        for slot in self._program.slots.values():
            if slot.persist not in ("state", "derived"):
                continue
            if slot.id in remaining:
                values[slot.id] = remaining.pop(slot.id)
                continue
            instance = self._program.instances[slot.on]
            if instance.kind == "lattice":
                if instance.size is None:
                    continue  # 动态实例：物化时补初值
                values[slot.id] = LatticeField(instance.size, slot.initial)
            elif instance.kind == "global":
                values[slot.id] = slot.initial
            else:
                raise NotImplementedError(
                    f"槽位 {slot.id}: 实例类型 {instance.kind} 在 P4 交付"
                )
        if remaining:
            raise ValueError(
                f"初始状态含未声明/非状态槽位: {sorted(remaining)}"
            )
        return values

    def _check_invariants(
        self,
        writes: Mapping[str, object],
    ) -> tuple[str, ...]:
        violations: list[str] = []
        for invariant in self._program.invariants:
            if invariant.slot in writes:
                value = writes[invariant.slot]
            else:
                value = self._store.read(invariant.slot, 0)
            if invariant.check(value):
                continue
            message = invariant.message or (
                f"不变量 {invariant.id} 失败: {invariant.slot}"
            )
            if invariant.severity == "reject":
                raise FrameFailure(message)
            violations.append(message)
        return tuple(violations)
