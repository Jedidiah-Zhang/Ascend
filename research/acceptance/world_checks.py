"""新核心验收判据（含守恒与实体切片判据）— C0–C2 / W0–W6 / I0–I1 / L3。

全部判据只依赖新核心（声明/编译/运行时/研究层）与玩具模块，不读旧注册表：
每个判据独立报告输入摘要与结果，失败给出首个分歧（04 §1 风格）。
"""

from __future__ import annotations

import sys
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))

from ascend.world import (  # noqa: E402
    LatticeField,
    Schedule,
    WorldProcess,
    WorldSpec,
    compile_world,
)
from ascend.world.evidence import (  # noqa: E402
    run_acceptance,
    run_witnesses,
    witness_coverage_issues,
)
from ascend.world.meta.declarations import AddressUse  # noqa: E402
from ascend.world.modules import (  # noqa: E402
    conservation,
    harvest,
    toy,
    weather,
    worldgen,
)
from ascend.world.modules.pipeline import PIPELINE_PHASES  # noqa: E402
from ascend.world.research import (  # noqa: E402
    ObservationSpec,
    Oracle,
    i0_control,
    i1_control,
    observe,
)
from ascend.world.research.records import (  # noqa: E402
    TraceLog,
    TraceRecord,
    record_from_trace,
)

__all__ = ["ALL_CHECKS", "CHECK_CODES", "CheckResult"]


@dataclass(frozen=True, slots=True)
class CheckResult:
    """单项判据结果（输入摘要 + 参考/引擎输出 + 首个分歧）。"""

    code: str
    title: str
    passed: bool
    detail: str = ""
    input: dict = field(default_factory=dict)
    first_divergence: str | None = None

    def plain(self) -> dict:
        return {
            "code": self.code,
            "title": self.title,
            "passed": self.passed,
            "detail": self.detail,
            "input": self.input,
            "first_divergence": self.first_divergence,
        }


def _world_program():
    """声明切片：世界生成 + 天气（研究事实源）。"""
    return compile_world(
        WorldSpec(
            modules=(worldgen.MODULE, weather.MODULE),
            schedule=Schedule(phases=PIPELINE_PHASES),
        )
    )


def _spatial_program(seed: int = 0):
    return compile_world(
        WorldSpec(
            modules=(toy.SPATIAL,),
            schedule=Schedule(
                phases=("stage1", "stage2"), periods=(("day", 24),),
            ),
            seed=seed,
        )
    )


def _w1_program():
    return compile_world(
        WorldSpec(
            modules=(toy.W1,),
            schedule=Schedule(phases=("stage1", "stage2", "stage3")),
        )
    )


def _w0_program():
    return compile_world(
        WorldSpec(
            modules=(toy.W0,),
            schedule=Schedule(phases=("stage1", "stage2", "stage3")),
        )
    )


# ── 声明层 ──────────────────────────────────────────────────────


def check_c0() -> CheckResult:
    """声明完整性：编译期静态校验（单写者/阶段序/参数/地址）全通过。"""
    program = _world_program()
    return CheckResult(
        "C0", "声明完整性（编译期静态校验）", True,
        f"{len(program.slots)} 槽位 / {len(program.mechanisms)} 机制 / "
        f"{len(program.parameters)} 参数；编译通过",
        input={"program_identity": program.identity},
    )


def check_c1() -> CheckResult:
    """结构最小性与模数：见证覆盖 + 重跑 + G7 否证全部一致。"""
    program = _world_program()
    issues: list[str] = []
    for mechanism in program.mechanisms.values():
        issues.extend(witness_coverage_issues(mechanism))
        issues.extend(run_witnesses(mechanism))
    witnesses = sum(
        len(mechanism.witnesses)
        for mechanism in program.mechanisms.values()
    )
    edges = sum(len(m.parents) for m in program.mechanisms.values())
    return CheckResult(
        "C1", "结构最小性与模数", not issues,
        f"见证 {witnesses} 条 / 父引用 {edges} 条；覆盖、重跑与模数否证一致"
        if not issues else "; ".join(issues[:3]),
    )


def check_c2() -> CheckResult:
    """时间展开无环：同帧（lag=0）父引用的写者必须更早执行。"""
    program = _world_program()
    rank = {
        mechanism.id: group.order
        for group in program.update_plan
        for mechanism in group.mechanisms
    }
    issues: list[str] = []
    for mechanism in program.mechanisms.values():
        for parent in mechanism.parents:
            if parent.lag != 0:
                continue
            writer = program.slots[parent.slot].writer
            if writer is None or writer == mechanism.id:
                continue
            if rank.get(writer, -1) >= rank.get(mechanism.id, -1):
                issues.append(f"{mechanism.id}←{parent.slot}")
    return CheckResult(
        "C2", "时间展开无环（阶段序）", not issues,
        f"更新组 {len(program.update_plan)} 个；同帧父引用序全部成立"
        if not issues else "; ".join(issues[:3]),
    )


# ── 动态语义（W0–W5）────────────────────────────────────────────


def _acceptance_result(code: str) -> CheckResult:
    results = {item.id: item for item in run_acceptance()}
    item = results[code]
    return CheckResult(
        code.split("-")[0], item.id, item.passed, item.detail,
    )


def check_w0() -> CheckResult:
    return _acceptance_result("W0-帧内顺序")


def check_w1() -> CheckResult:
    return _acceptance_result("W1-节点干预与CRN")


def check_w2() -> CheckResult:
    return _acceptance_result("W2-值干预时长")


def check_w3() -> CheckResult:
    """空间父模板与边界：核支持内传播 + 边界 clamp + 舍入。"""
    program = _spatial_program()
    field = LatticeField((5,), 0)
    for index, value in enumerate((0, 10, 20, 30, 40)):
        field.set((index,), value)
    process = WorldProcess(program, initial_state={"toy.u": field})
    process.step()
    base = process.committed("toy.v").values()

    shifted = LatticeField((5,), 0)
    for index, value in enumerate((0, 10, 20, 30, 50)):
        shifted.set((index,), value)
    other = WorldProcess(program, initial_state={"toy.u": shifted})
    other.step()
    perturbed = other.committed("toy.v").values()
    affected = tuple(
        index for index in range(5) if base[index] != perturbed[index]
    )
    ok = base == (2, 10, 20, 30, 38) and affected == (3, 4)
    return CheckResult(
        "W3", "空间父模板与边界", ok,
        f"v={base}（期望 (2,10,20,30,38)）；单位扰动影响 {affected}"
        f"（期望 (3,4)）",
        first_divergence=None if ok else f"affected={affected}",
    )


def check_w4() -> CheckResult:
    """状态充分性：快照双跑一致 + 身份/缺槽位负例。"""
    program = _spatial_program(seed=9)
    process = WorldProcess(program, seed=9)
    for _ in range(5):
        process.step()
    snapshot = process.snapshot()
    restored = WorldProcess.restore(program, snapshot, seed=9)
    process.step(events=("flash",))
    restored.step(events=("flash",))
    slots = ("toy.u", "toy.v", "toy.daycount", "toy.flash")
    same = all(
        restored.committed(slot) == process.committed(slot)
        for slot in slots
    )
    identity_negative = False
    bad_identity = dict(snapshot)
    bad_identity["identity"] = "sha256:deadbeef"
    try:
        WorldProcess.restore(program, bad_identity, seed=9)
    except ValueError:
        identity_negative = True
    identity_bound = snapshot["identity"] == program.world_identity(9)
    seed_negative = False
    try:
        WorldProcess.restore(program, snapshot, seed=10)
    except ValueError:
        seed_negative = True
    missing_negative = False
    bad_states = dict(snapshot)
    states = dict(bad_states["states"])
    states.pop("toy.u", None)
    bad_states["states"] = states
    try:
        WorldProcess.restore(program, bad_states, seed=9)
    except ValueError:
        missing_negative = True
    ok = (
        same and identity_negative and identity_bound
        and seed_negative and missing_negative
    )
    return CheckResult(
        "W4", "状态充分性（快照双跑）", ok,
        f"双跑一致={same}；身份绑定={identity_bound}；"
        f"身份不符拒绝={identity_negative}；异种子拒绝={seed_negative}；"
        f"缺槽位拒绝={missing_negative}",
    )


def check_w5() -> CheckResult:
    """观测隔离与协议：量化、未授权拒绝、噪声确定性。"""
    program = _w1_program()
    process = WorldProcess(program)
    process.step(interventions={"toy.med": 7})
    quantized = observe(
        program, process,
        ObservationSpec(id="o1", slots=("toy.med",), quantize_shift=1),
    )["values"]["toy.med"] == 3
    audited = False
    try:
        observe(
            program, process, ObservationSpec(id="o2", slots=("toy.out",)),
        )
    except PermissionError:
        audited = True
    noisy = ObservationSpec(
        id="o3", slots=("toy.med",),
        noise_address=AddressUse("obs", "noise"), noise_span=1,
    )
    deterministic = (
        observe(program, process, noisy) == observe(program, process, noisy)
    )
    ok = quantized and audited and deterministic
    return CheckResult(
        "W5", "观测隔离与协议", ok,
        f"量化={quantized}；未授权拒绝={audited}；噪声确定={deterministic}",
    )


# ── 阶段二正控制（I0/I1）────────────────────────────────────────


def check_i0() -> CheckResult:
    """观测等价、干预可分：A=B 观测相同；do(A=0) 后果不同。"""
    template = i0_control()
    u1, u2 = template.programs
    seeds = tuple(range(16))
    baseline, intervened = template.arms
    equivalence = True
    u1_effects: list[object] = []
    u2_effects: list[object] = []
    for program, effects in ((u1, u1_effects), (u2, u2_effects)):
        oracle = Oracle(program, template.observation)
        for seed in seeds:
            frames = oracle.rollout(seed=seed, arm=baseline, horizon=2)
            values = frames[0]["values"]
            if values["i0.a"] != values["i0.b"]:
                equivalence = False
            arm_frames = oracle.rollout(
                seed=seed, arm=intervened, horizon=2,
            )
            effects.append(arm_frames[0]["values"]["i0.b"])
    separated = (
        u1_effects == [0] * len(seeds)
        and any(value != 0 for value in u2_effects)
    )
    ok = equivalence and separated
    return CheckResult(
        "I0", "观测等价、干预可分", ok,
        f"观测恒 A=B={equivalence}；U1 干预后 B 恒 0 / U2 仍为 E="
        f"{separated}",
    )


def check_i1() -> CheckResult:
    """分布预测与配对效应：同 ω 下 O(g=1) − O(g=0) 恒为 10。"""
    template = i1_control()
    program = template.programs[0]
    oracle = Oracle(program, template.observation)
    baseline, arm = template.arms
    ok = True
    for seed in (1, 2, 3):
        base = oracle.rollout(seed=seed, arm=baseline, horizon=4)
        other = oracle.rollout(seed=seed, arm=arm, horizon=4)
        for left, right in zip(base, other):
            if right["values"]["i1.o"] - left["values"]["i1.o"] != 10:
                ok = False
    return CheckResult(
        "I1", "配对效应（CRN）", ok, "O(g=1) − O(g=0) 逐帧恒为 10",
    )


# ── 守恒练兵切片────────────────────────────────────────

_CONSERVATION_PHASES = ("flow", "apply", "drain")


def check_w6() -> CheckResult:
    """守恒练兵切片：逐帧总量守恒 + 多分辨率稳态（跨槽位不变量）。"""
    program = compile_world(
        WorldSpec(
            modules=(conservation.MODULE,),
            schedule=Schedule(phases=_CONSERVATION_PHASES),
        )
    )
    process = WorldProcess(program)
    total_ok = True
    try:
        for _ in range(12):
            process.step()
            total = (
                sum(process.committed("water.plot.stock").values())
                + sum(process.committed("water.basin.stock").values())
            )
            total_ok = total_ok and total == conservation.TOTAL_WATER
    except Exception as exc:
        return CheckResult(
            "W6", "守恒切片（流量/守恒/多分辨率）", False,
            f"帧失败: {type(exc).__name__}: {exc}",
        )
    steady = (
        process.committed("water.plot.stock").values() == (10, 10, 10, 10)
        and process.committed("water.basin.stock").values() == (0, 0)
    )
    ok = total_ok and steady
    return CheckResult(
        "W6", "守恒切片（流量/守恒/多分辨率）", ok,
        f"逐帧总量 == {conservation.TOTAL_WATER}={total_ok}；"
        f"稳态（地块满 10 / 流域空 0）={steady}",
        input={"module": conservation.MODULE.id},
    )


# ── 实体练兵切片────────────────────────────────────────

def check_w7() -> CheckResult:
    """实体练兵切片：事件门控 + 链接 + Γ 指派 + 守恒（实体/事件/资源）。"""
    from ascend.world.research.action import (
        ActionSpec,
        interventions_at,
        resolve,
    )

    program = compile_world(
        WorldSpec(
            modules=(harvest.MODULE,),
            schedule=Schedule(phases=("hold",)),
        )
    )
    process = WorldProcess(program)
    for index in range(harvest.RESOURCE_COUNT):
        process.spawn_entity("entity.resource", f"r{index}")
    process.spawn_entity("entity.harvester", "h0")
    next_tick = process.tick + 1
    actions = (
        ActionSpec(
            id="target", target_slot="harvest.target",
            value={("h0",): "r0"},
        ),
        ActionSpec(
            id="owner", target_slot="harvest.owner",
            value={("r0",): "h0"},
        ),
    )
    process.step(interventions=interventions_at(
        resolve(actions, tick=next_tick), next_tick,
    ))
    stock_before = process.committed("harvest.stock").values()
    process.step()
    gated = (
        process.committed("harvest.stock").values() == stock_before
        and process.committed("harvest.yield").values() == (0,)
    )
    total_ok = True
    try:
        for _ in range(3):
            process.step(events=("harvest", "harvest.settle"))
            total_ok = total_ok and (
                sum(process.committed("harvest.stock").values())
                + sum(process.committed("harvest.carried").values())
                == harvest.TOTAL_RESOURCE
            )
    except Exception as exc:
        return CheckResult(
            "W7", "实体切片（实体/事件/资源/Γ）", False,
            f"帧失败: {type(exc).__name__}: {exc}",
        )
    moved = (
        process.committed("harvest.stock").values() == (0, 5)
        and process.committed("harvest.carried").values() == (5,)
    )
    ok = gated and total_ok and moved
    return CheckResult(
        "W7", "实体切片（实体/事件/资源/Γ）", ok,
        f"事件门控={gated}；逐帧守恒={total_ok}；"
        f"结算后 存量={process.committed('harvest.stock').values()} / "
        f"携带={process.committed('harvest.carried').values()}",
        input={"module": harvest.MODULE.id},
    )


# ── L3：研究记录 / CRN / 状态闭合 ───────────────────────────────


def check_l3_history() -> CheckResult:
    """记录可重算：逐机制记录 replay 逐位一致；坏记录拒绝。"""
    program = _w0_program()
    process = WorldProcess(program)
    result = process.step(trace=True)
    log = TraceLog(program, capacity=16)
    for captured in result.traces:
        log.record(record_from_trace(program, captured, frame=result.tick))
    replay_ok = log.verify_all() == []
    negative = False
    try:
        log.record(TraceRecord(node_id="toy.ghost", frame=1,
                               microstep="stage1"))
    except ValueError:
        negative = True
    ok = replay_ok and negative and log.counts()["eval"] == 3
    return CheckResult(
        "L3-历史", "记录可重算", ok,
        f"记录 {len(log)} 条全部可重算={replay_ok}；坏记录拒绝={negative}",
    )


def check_l3_crn() -> CheckResult:
    """CRN 配对：未干预随机源逐位一致；干预臂效应可复现。"""
    template = i1_control()
    program = template.programs[0]
    oracle = Oracle(
        program,
        ObservationSpec(id="l3.crn", slots=("i1.e", "i1.o")),
    )
    baseline, arm = template.arms
    ok = True
    for seed in (1, 2, 3):
        base = oracle.rollout(seed=seed, arm=baseline, horizon=3)
        other = oracle.rollout(seed=seed, arm=arm, horizon=3)
        for left, right in zip(base, other):
            if left["values"]["i1.e"] != right["values"]["i1.e"]:
                ok = False
    return CheckResult(
        "L3-CRN", "CRN 配对", ok, "未干预随机源 i1.e 逐帧逐位一致",
    )


def check_l3_state() -> CheckResult:
    """状态闭合：state 槽位全部有写者并入快照；派生/外部不入档。"""
    program = _spatial_program()
    state_slots = {
        slot.id for slot in program.slots.values() if slot.persist == "state"
    }
    derived_slots = {
        slot.id for slot in program.slots.values()
        if slot.persist == "derived"
    }
    external_slots = {
        slot.id for slot in program.slots.values()
        if slot.persist == "external"
    }
    writers_ok = all(
        program.slots[slot_id].writer for slot_id in state_slots
    )
    process = WorldProcess(program)
    process.step()
    snapshot = process.snapshot()
    snapshot_ok = set(snapshot["states"]) == state_slots
    derived_excluded = not (derived_slots & set(snapshot["states"]))
    external_excluded = not (external_slots & set(snapshot["states"]))
    ok = writers_ok and snapshot_ok and derived_excluded and external_excluded
    return CheckResult(
        "L3-状态", "状态闭合", ok,
        f"state {len(state_slots)} 槽位有写者={writers_ok}、"
        f"快照一致={snapshot_ok}；派生/外部不入档="
        f"{derived_excluded and external_excluded}",
    )


#: 全部判据码（不执行判据；条款↔证据对账门禁与漂移测试用）。
CHECK_CODES: tuple[str, ...] = (
    "C0", "C1", "C2",
    "W0", "W1", "W2", "W3", "W4", "W5", "W6", "W7",
    "I0", "I1",
    "L3-历史", "L3-CRN", "L3-状态",
)

ALL_CHECKS = (
    check_c0,
    check_c1,
    check_c2,
    check_w0,
    check_w1,
    check_w2,
    check_w3,
    check_w4,
    check_w5,
    check_w6,
    check_w7,
    check_i0,
    check_i1,
    check_l3_history,
    check_l3_crn,
    check_l3_state,
)
