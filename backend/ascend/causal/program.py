"""世界程序 — 声明编译为确定性执行计划（世界基座 13 篇 / ADR-12）。

编译把声明数据凝固为不可变 ``WorldProgram``：

- **波次计划**：机制按输出节点的微步分组；同组机制没有同帧依赖
  （同帧父必须位于更早微步），因此组内可并行、组间按序；
- **内核绑定**：机制 → 参考实现（Python）；加速实现位当前占位（None），
  由数值内核批次填入（两实现须对拍）；
- **更新点计划**：声明序 + 周期（tick）+ 槽位交叉校验；
- **世界身份**：以上全部与契约版本的规范摘要（含日历 tick 刻度）。

静态校验 fail-closed（编译期拒绝，不静默放行）：

- 同一输出节点至多一个机制（单写者）；
- 同帧父依赖必须位于更早微步（无环 / 无跨波倒置）；
- 更新点与槽位声明必须互相覆盖（双向，不漂移）。

编译输入（结构性鸭子类型，便于测试与多世界变体）：

- ``registry``：``microstep_order`` / ``nodes`` / ``mechanisms`` /
  ``declaration_hash`` / ``wired_nodes`` / ``equation_version`` /
  ``resolved_version``；
- ``state``：``StateDeclaration``；
- ``addresses``：``FateNamespaceRegistry``；
- ``points``：``UpdatePointTable``；
- ``ticks``：周期符号刻度 → tick 映射。

编译产物不可变；同一组声明编译两次身份逐位一致。
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from functools import lru_cache
from types import MappingProxyType
from typing import Callable, Mapping

from .declaration import canonical_bytes
from .fate_registry import FateNamespaceRegistry, load_fate_namespaces
from .kernels import KernelPair
from .state_schema import StateDeclaration, load_declaration
from .update_points import (
    PERIODS,
    UpdatePointTable,
    load_update_points,
)

PROGRAM_SCHEMA_VERSION: int = 1


@dataclass(frozen=True, slots=True)
class KernelBinding:
    """机制的内核绑定（参考实现 + 加速实现位 + 版本摘要）。

    Attributes:
        mechanism_id: 机制标识。
        output: 输出节点（内核表键）。
        microstep: 输出节点所在微步。
        reference: 参考实现（Python，可执行）。
        accelerated: 加速实现（当前为 None；填后须与参考实现逐位对拍）。
        equation_version: 方程源码版本摘要（注册表构造期预计算）。
        resolved_version: 方程 + 参数 + 边界的组合摘要。
        wired: 当前引擎是否真正求值该节点（干预可达性事实源）。
    """

    mechanism_id: str
    output: str
    microstep: str
    reference: Callable[..., object]
    equation_version: str
    resolved_version: str
    wired: bool
    accelerated: Callable[..., object] | None = None
    kernel_version: str | None = None

    @property
    def reference_name(self) -> str:
        """参考实现的稳定名称（身份投影用；不含对象身份）。"""
        return _callable_name(self.reference)

    @property
    def accelerated_name(self) -> str | None:
        """加速实现的稳定名称；未绑定为 None。"""
        if self.accelerated is None:
            return None
        return _callable_name(self.accelerated)


@dataclass(frozen=True, slots=True)
class Wave:
    """一个波次（同一微步内的全部机制）。

    Attributes:
        index: 波次序号（按微步顺序，从 0 连续）。
        microstep: 对应微步名。
        mechanisms: 机制标识（字典序）。
        outputs: 输出节点（与 ``mechanisms`` 同序）。
    """

    index: int
    microstep: str
    mechanisms: tuple[str, ...]
    outputs: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class UpdatePointPlan:
    """更新点的执行计划（周期已解析为 tick）。"""

    id: str
    order: int
    period_ticks: int
    slots: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class PointKernelBinding:
    """更新点的内核绑定（参考实现 + 加速实现位 + 内核版本）。"""

    anchor: str
    version: str
    reference: Callable[..., object]
    accelerated: Callable[..., object] | None

    @property
    def reference_name(self) -> str:
        """参考实现的稳定名称（身份投影用）。"""
        return _callable_name(self.reference)

    @property
    def accelerated_name(self) -> str | None:
        """加速实现的稳定名称；未绑定为 None。"""
        if self.accelerated is None:
            return None
        return _callable_name(self.accelerated)


@dataclass(frozen=True, slots=True)
class WorldProgram:
    """不可变世界程序：声明编译产物（运行一个世界 = 程序 + 种子 + 状态）。"""

    identity: str
    schema_version: int
    contract_version: str
    registry_digest: str
    slots_digest: str
    address_digest: str
    update_points_digest: str
    calendar: Mapping[str, int]
    microsteps: tuple[str, ...]
    waves: tuple[Wave, ...]
    kernels: Mapping[str, KernelBinding]
    point_kernels: Mapping[str, PointKernelBinding]
    update_points: tuple[UpdatePointPlan, ...]

    def kernel_for(self, output: str) -> KernelBinding:
        """按输出节点取内核绑定；未声明抛 KeyError（fail-closed）。"""
        try:
            return self.kernels[output]
        except KeyError as exc:
            raise KeyError(f"世界程序无此内核: {output}") from exc

    def point_kernel_for(self, anchor: str) -> PointKernelBinding:
        """按更新点取内核绑定；未声明抛 KeyError（fail-closed）。"""
        try:
            return self.point_kernels[anchor]
        except KeyError as exc:
            raise KeyError(f"世界程序无此更新点内核: {anchor}") from exc

    def update_point(self, point_id: str) -> UpdatePointPlan:
        """按标识取更新点计划；未声明抛 KeyError（fail-closed）。"""
        for point in self.update_points:
            if point.id == point_id:
                return point
        raise KeyError(f"世界程序无此更新点: {point_id}")

    def world_identity(self, seed: int) -> str:
        """世界身份 = 程序身份 + 种子（同一程序不同种子 = 不同世界）。

        Raises:
            ValueError: 种子不是非负整数。
        """
        if type(seed) is not int or seed < 0:
            raise ValueError(f"种子必须为非负整数: {seed!r}")
        return _digest({"program": self.identity, "seed": seed})

    def settings(self) -> dict[str, object]:
        """世界设置视图（manifest 记录与读档比对用）。

        identity 是全部声明、内核与日历刻度的组合摘要，读档一致性以它
        为准；各分量摘要只作诊断定位。
        """
        return {
            "identity": self.identity,
            "schema_version": self.schema_version,
            "contract_version": self.contract_version,
            "registry_digest": self.registry_digest,
            "slots_digest": self.slots_digest,
            "address_digest": self.address_digest,
            "update_points_digest": self.update_points_digest,
            "kernels_digest": _digest([
                {
                    "anchor": anchor,
                    "version": binding.version,
                    "reference": binding.reference_name,
                    "accelerated": binding.accelerated_name,
                }
                for anchor, binding in sorted(self._all_kernel_pairs().items())
            ]),
        }

    def _all_kernel_pairs(
        self,
    ) -> dict[str, PointKernelBinding]:
        """全部内核锚点（机制输出 + 更新点）的统一视图（身份/摘要用）。"""
        pairs: dict[str, PointKernelBinding] = {}
        for output, kernel in self.kernels.items():
            if kernel.kernel_version is None:
                continue
            pairs[output] = PointKernelBinding(
                anchor=output,
                version=kernel.kernel_version,
                reference=kernel.reference,
                accelerated=kernel.accelerated,
            )
        pairs.update(self.point_kernels)
        return pairs


def compile_world_program(
    registry: object,
    *,
    state: StateDeclaration,
    addresses: FateNamespaceRegistry,
    points: UpdatePointTable,
    ticks: Mapping[str, int],
    kernels: tuple[KernelPair, ...] | None = None,
) -> WorldProgram:
    """把声明编译为世界程序；静态校验失败抛 ValueError。

    Args:
        registry: 机制注册表（见模块 docstring 的结构性接口）。
        state: 已校验的状态声明（槽位表）。
        addresses: 已校验的随机地址表。
        points: 已校验的更新点表。
        ticks: 周期符号刻度 → tick 映射（键必须恰为 ``PERIODS``）。
        kernels: 内核对（None = 生产默认 ``default_kernel_pairs()``）。

    Raises:
        ValueError: 任一静态校验失败（错误全量汇总）。
    """
    issues: list[str] = []
    calendar = _validate_ticks(ticks, issues)
    registry_digest = _declaration_digest(
        getattr(registry, "declaration_hash", ""), "registry", issues,
    )
    slots_digest = _declaration_digest(state.digest(), "slots", issues)
    address_digest = _declaration_digest(
        addresses.digest(), "addresses", issues,
    )
    update_digest = _declaration_digest(points.digest(), "points", issues)

    pair_map = _validate_kernel_pairs(
        kernels if kernels is not None else _default_pairs(), registry,
        points, issues,
    )
    microsteps, waves, kernels_by_output = _compile_waves(
        registry, pair_map, issues,
    )
    plan = _compile_update_points(state, points, calendar, issues)
    point_kernels = {
        anchor: PointKernelBinding(
            anchor=anchor,
            version=pair.version,
            reference=pair.reference,
            accelerated=pair.accelerated,
        )
        for anchor, pair in pair_map.items()
        if anchor in {point.id for point in points.points}
    }

    if issues:
        raise ValueError("世界程序编译失败: " + "; ".join(issues))

    projection = {
        "schema_version": PROGRAM_SCHEMA_VERSION,
        "contract_version": state.contract_version,
        "slice": {
            "id": state.slice_id,
            "chunk_size": state.space.chunk_size,
        },
        "declarations": {
            "registry": registry_digest,
            "slots": slots_digest,
            "addresses": address_digest,
            "update_points": update_digest,
        },
        "calendar": dict(calendar),
        "microsteps": list(microsteps),
        "waves": [
            {
                "microstep": wave.microstep,
                "kernels": [
                    {
                        "output": output,
                        "mechanism": kernels_by_output[output].mechanism_id,
                        "microstep": kernels_by_output[output].microstep,
                        "reference": kernels_by_output[output].reference_name,
                        "accelerated": (
                            kernels_by_output[output].accelerated_name
                        ),
                        "kernel_version": (
                            kernels_by_output[output].kernel_version
                        ),
                        "equation_version": (
                            kernels_by_output[output].equation_version
                        ),
                        "resolved_version": (
                            kernels_by_output[output].resolved_version
                        ),
                        "wired": kernels_by_output[output].wired,
                    }
                    for output in wave.outputs
                ],
            }
            for wave in waves
        ],
        "point_kernels": [
            {
                "anchor": anchor,
                "version": binding.version,
                "reference": binding.reference_name,
                "accelerated": binding.accelerated_name,
            }
            for anchor, binding in sorted(point_kernels.items())
        ],
        "update_points": [
            {
                "id": plan_point.id,
                "order": plan_point.order,
                "period_ticks": plan_point.period_ticks,
                "slots": list(plan_point.slots),
            }
            for plan_point in plan
        ],
    }
    return WorldProgram(
        identity=_digest(projection),
        schema_version=PROGRAM_SCHEMA_VERSION,
        contract_version=state.contract_version,
        registry_digest=registry_digest,
        slots_digest=slots_digest,
        address_digest=address_digest,
        update_points_digest=update_digest,
        calendar=MappingProxyType(dict(calendar)),
        microsteps=microsteps,
        waves=waves,
        kernels=MappingProxyType(kernels_by_output),
        point_kernels=MappingProxyType(point_kernels),
        update_points=plan,
    )


def compile_default_program() -> WorldProgram:
    """按生产声明编译世界程序（天气 + 空间生成切片）。

    每次调用都重新读取声明并编译；生产路径用 ``get_default_program``
    的进程内缓存（声明在进程存续期内视为不可变）。
    """
    from ascend.config import GAME_DAY, GAME_HOUR, GAME_MINUTE

    from .world import ASCEND_MECHANISMS

    return compile_world_program(
        ASCEND_MECHANISMS,
        state=load_declaration(),
        addresses=load_fate_namespaces(),
        points=load_update_points(),
        ticks={
            "game_minute": GAME_MINUTE,
            "game_hour": GAME_HOUR,
            "game_day": GAME_DAY,
        },
    )


@lru_cache(maxsize=1)
def get_default_program() -> WorldProgram:
    """生产世界程序的进程内缓存（编译只按身份缓存一次）。

    声明文件在进程存续期内不可变；测试若改写声明/日历刻度，
    调用 ``get_default_program.cache_clear()`` 后重新取用。
    """
    return compile_default_program()


# ── 编译内部 ──────────────────────────────────────────────


def _default_pairs() -> tuple[KernelPair, ...]:
    """生产内核对（惰性导入领域模块）。"""
    from .kernels import default_kernel_pairs

    return default_kernel_pairs()


def _validate_kernel_pairs(
    kernels: tuple[KernelPair, ...],
    registry: object,
    points: UpdatePointTable,
    issues: list[str],
) -> dict[str, KernelPair]:
    """校验内核对锚点并建映射（fail-closed）。

    机制输出锚点的 ``reference`` 必须与注册表声明的实现为**同一可调用
    对象**（注册表是机制参考实现的单一事实源）——声明了却被静默忽略的
    参考实现是陷阱，编译期拒绝。更新点锚点的 ``reference`` 即实现本身。
    """
    output_specs = {
        spec.output: spec
        for spec in getattr(registry, "mechanisms", {}).values()
    }
    point_ids = {point.id for point in points.points}
    pair_map: dict[str, KernelPair] = {}
    for pair in kernels:
        anchor = getattr(pair, "anchor", "")
        if not isinstance(anchor, str) or not anchor:
            issues.append(f"内核对锚点非法: {anchor!r}")
            continue
        if anchor in pair_map:
            issues.append(f"内核对重复锚点: {anchor}")
            continue
        if anchor not in output_specs and anchor not in point_ids:
            issues.append(
                f"内核对锚点未声明（既非机制输出也非更新点）: {anchor}"
            )
            continue
        if not getattr(pair, "version", ""):
            issues.append(f"内核对缺少语义版本: {anchor}")
            continue
        if not callable(pair.reference):
            issues.append(f"内核对缺少参考实现: {anchor}")
            continue
        if pair.accelerated is not None and not callable(pair.accelerated):
            issues.append(f"内核对加速实现不可调用: {anchor}")
            continue
        if (anchor in output_specs
                and pair.reference is not output_specs[anchor].function):
            issues.append(
                f"内核对参考实现与注册表不一致（机制输出以注册表为单一"
                f"事实源）: {anchor}"
            )
            continue
        pair_map[anchor] = pair
    return pair_map


def _validate_ticks(
    ticks: Mapping[str, int], issues: list[str],
) -> dict[str, int]:
    """校验周期刻度映射；返回规范化的字典。"""
    if not isinstance(ticks, Mapping):
        issues.append(f"ticks 必须为映射: {ticks!r}")
        return {}
    missing = sorted(set(PERIODS) - set(ticks))
    extra = sorted(set(ticks) - set(PERIODS))
    if missing or extra:
        issues.append(f"ticks 键不匹配: 缺少={missing}, 多余={extra}")
    calendar: dict[str, int] = {}
    for name in PERIODS:
        value = ticks.get(name)
        if type(value) is not int or value <= 0:
            issues.append(f"ticks[{name}] 必须为正整数 tick: {value!r}")
            continue
        calendar[name] = value
    return calendar


def _declaration_digest(
    value: object, label: str, issues: list[str],
) -> str:
    """规范化声明摘要为 ``sha256:<hex>``；缺失/非法即拒绝。"""
    if not isinstance(value, str) or not value:
        issues.append(f"{label}: 声明摘要缺失")
        return ""
    if value.startswith("sha256:"):
        return value
    if len(value) != 64 or any(ch not in "0123456789abcdef" for ch in value):
        issues.append(f"{label}: 声明摘要非法 {value!r}")
        return ""
    return f"sha256:{value}"


def _compile_waves(
    registry: object,
    pair_map: Mapping[str, KernelPair],
    issues: list[str],
) -> tuple[tuple[str, ...], tuple[Wave, ...], dict[str, KernelBinding]]:
    """按微步分组机制并校验同帧依赖序；返回 (微步序, 波次, 内核表)。"""
    raw_microsteps = tuple(getattr(registry, "microstep_order", ()) or ())
    index: dict[str, int] = {}
    for position, name in enumerate(raw_microsteps):
        if not isinstance(name, str) or not name:
            issues.append(f"微步名非法: {name!r}")
            continue
        if name in index:
            issues.append(f"微步重复: {name}")
            continue
        index[name] = position
    nodes = getattr(registry, "nodes", {}) or {}
    mechanisms = getattr(registry, "mechanisms", {}) or {}
    wired = frozenset(getattr(registry, "wired_nodes", frozenset()) or ())

    grouped: dict[str, list[tuple[str, str]]] = {name: [] for name in index}
    writers: dict[str, str] = {}
    kernels: dict[str, KernelBinding] = {}
    for mechanism_id, spec in mechanisms.items():
        output = spec.output
        if output in writers:
            issues.append(
                f"节点存在多个写者: {output} "
                f"({writers[output]}, {mechanism_id})"
            )
            continue
        writers[output] = mechanism_id
        node = nodes.get(output)
        if node is None:
            issues.append(f"{mechanism_id}: 输出节点未声明 {output}")
            continue
        microstep = node.update.microstep
        if microstep not in index:
            issues.append(f"{mechanism_id}: 输出微步未声明 {microstep!r}")
            continue
        for parent in spec.parents:
            parent_node = nodes.get(parent.parent)
            if parent_node is None:
                issues.append(
                    f"{mechanism_id}: 父节点未声明 {parent.parent}"
                )
                continue
            parent_step = parent_node.update.microstep
            if parent.source_microstep != parent_step:
                issues.append(
                    f"{mechanism_id}: 父 {parent.parent} 源微步 "
                    f"{parent.source_microstep!r} != 父节点更新微步 "
                    f"{parent_step!r}"
                )
            if parent.lag != 0:
                continue
            source = index.get(parent_step)
            if source is None:
                issues.append(
                    f"{mechanism_id}: 父 {parent.parent} 的更新微步未声明 "
                    f"{parent_step!r}"
                )
                continue
            if source >= index[microstep]:
                issues.append(
                    f"{mechanism_id}: 同帧父 {parent.parent} 未位于更早微步"
                    f"（{parent_step} >= {microstep}）"
                )
        grouped[microstep].append((mechanism_id, output))
        pair = pair_map.get(output)
        kernels[output] = KernelBinding(
            mechanism_id=mechanism_id,
            output=output,
            microstep=microstep,
            reference=spec.function,
            equation_version=registry.equation_version(output),
            resolved_version=registry.resolved_version(output),
            wired=output in wired,
            accelerated=pair.accelerated if pair is not None else None,
            kernel_version=pair.version if pair is not None else None,
        )

    waves: list[Wave] = []
    for name in index:
        entries = sorted(grouped[name], key=lambda item: item[0])
        if not entries:
            continue
        waves.append(Wave(
            index=len(waves),
            microstep=name,
            mechanisms=tuple(item[0] for item in entries),
            outputs=tuple(item[1] for item in entries),
        ))
    return tuple(index), tuple(waves), kernels


def _compile_update_points(
    state: StateDeclaration,
    points: UpdatePointTable,
    calendar: Mapping[str, int],
    issues: list[str],
) -> tuple[UpdatePointPlan, ...]:
    """校验更新点与槽位互相覆盖，并解析周期为 tick（按 order 升序）。"""
    slots = {slot.id: slot for slot in state.slots}
    point_ids = {point.id for point in points.points}
    declared = {point.id: set(point.slots) for point in points.points}
    seen_ids: set[str] = set()
    seen_orders: set[int] = set()
    for point in points.points:
        if point.id in seen_ids:
            issues.append(f"重复更新点: {point.id}")
        seen_ids.add(point.id)
        if point.order in seen_orders:
            issues.append(f"重复执行顺序: {point.order}")
        seen_orders.add(point.order)
    plan: list[UpdatePointPlan] = []
    for point in sorted(points.points, key=lambda item: item.order):
        for slot_id in point.slots:
            slot = slots.get(slot_id)
            if slot is None:
                issues.append(f"{point.id}: 槽位未声明 {slot_id}")
                continue
            if slot.update.kind != "mechanism":
                issues.append(
                    f"{point.id}: 槽位 {slot_id} 更新类型为 "
                    f"{slot.update.kind}，不是 mechanism"
                )
                continue
            if slot.update.stage != point.id:
                issues.append(
                    f"{point.id}: 槽位 {slot_id} 声明的阶段为 "
                    f"{slot.update.stage!r}，与更新点不符"
                )
        period_ticks = calendar.get(point.period)
        if period_ticks is None:
            issues.append(f"{point.id}: 周期刻度未解析 {point.period!r}")
            continue
        plan.append(UpdatePointPlan(
            id=point.id,
            order=point.order,
            period_ticks=period_ticks,
            slots=point.slots,
        ))
    for slot in state.slots:
        if slot.update.kind != "mechanism":
            continue
        stage = slot.update.stage
        if stage not in point_ids:
            issues.append(
                f"槽位 {slot.id} 的更新阶段 {stage!r} 无对应更新点"
            )
            continue
        if slot.id not in declared[stage]:
            issues.append(
                f"槽位 {slot.id} 的更新阶段 {stage!r} 未在更新点 "
                "slots 中声明"
            )
    return tuple(plan)


def _digest(value: object) -> str:
    """规范 JSON 摘要（与声明层同编码）。"""
    return "sha256:" + hashlib.sha256(canonical_bytes(value)).hexdigest()


def _callable_name(function: Callable[..., object]) -> str:
    """可调用对象的稳定名称（身份投影用）。"""
    module = getattr(function, "__module__", "?")
    qualname = getattr(function, "__qualname__", repr(function))
    return f"{module}.{qualname}"
