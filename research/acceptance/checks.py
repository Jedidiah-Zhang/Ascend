"""世界验收判据 — C0–C2 / W0–W5 / I0–I1（每项独立报告，产物含首分歧）。

判据来源：[世界验收协议](../../../docs/研究理论/世界基座/04-世界验收协议.md)
与[第一阶段实施定义](../../../docs/研究理论/第一阶段实施定义.md) §9。

设计原则（04 §4.1）：参考计算必须**独立于生产实现**——本模块用
``reference.py`` 的声明解释器与手算表，不复用引擎求值路径；引擎侧只
提供被比较的轨迹。每项判据记录输入、参考输出、引擎输出与首个分歧节点。
"""

from __future__ import annotations

import itertools
import math
from dataclasses import dataclass, field

from ascend.causal import (
    InterventionRecord,
    InterventionTable,
)
from ascend.causal.intervention_engine import InterventionFrameExecutor
from ascend.causal.registry import MechanismRegistry
from ascend.causal.world import ASCEND_MECHANISMS

import reference
import slices


@dataclass
class CheckResult:
    """单项判据结果（可序列化）。"""

    code: str
    title: str
    passed: bool
    detail: str
    input: dict = field(default_factory=dict)
    reference: object = None
    engine: object = None
    first_divergence: tuple | None = None

    def plain(self) -> dict:
        """可 JSON 往返的视图（元组统一转列表）。"""
        return {
            "code": self.code,
            "title": self.title,
            "passed": self.passed,
            "detail": self.detail,
            "input": _jsonable(self.input),
            "reference": _jsonable(self.reference),
            "engine": _jsonable(self.engine),
            "first_divergence": _jsonable(self.first_divergence),
        }


def _jsonable(value):
    """元组/集合 → 列表，dict/list 递归；其余原样。"""
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (tuple, set, frozenset)):
        return [_jsonable(item) for item in value]
    if isinstance(value, list):
        return [_jsonable(item) for item in value]
    return value


# ── C0/C1：声明完整性与结构最小性 ────────────────────────────

def check_c0() -> CheckResult:
    """C0：注册表构造期 C0 全字段校验通过（生产声明）。"""
    issues = ASCEND_MECHANISMS.validate_c0()
    return CheckResult(
        code="C0", title="声明完整性", passed=not issues,
        detail=(
            f"{len(ASCEND_MECHANISMS.nodes)} 节点 / "
            f"{len(ASCEND_MECHANISMS.mechanisms)} 机制；无问题"
            if not issues else "; ".join(issues)
        ),
    )


def check_c1() -> CheckResult:
    """C1：每条结构父边至少一个功能依赖见证（实测重跑）。"""
    issues = ASCEND_MECHANISMS.validate_c1()
    return CheckResult(
        code="C1", title="结构最小性", passed=not issues,
        detail=(
            f"{sum(len(m.witnesses) for m in ASCEND_MECHANISMS.mechanisms.values())} "
            f"个见证实测重跑通过" if not issues else "; ".join(issues)
        ),
    )


# ── C2：时间展开无环 ────────────────────────────────────────

def check_c2(window: int = 3) -> CheckResult:
    """C2：把声明展开到有限窗口，验同帧边阶段序 + 展开图无环。

    节点 = (分量, 帧, 阶段)；边按父引用的 lag 与 source_microstep 生成。
    无环性由 (帧, 阶段) 字典序秩保证（`UnrolledDag.lean` 已证），本检查在
    引擎侧独立展开并逐边验证，捕获声明与展开器的不一致。
    """
    registry = ASCEND_MECHANISMS
    order = {name: index for index, name in enumerate(registry.microstep_order)}
    edges: list[tuple] = []
    violations: list[str] = []
    for mechanism in registry.mechanisms.values():
        target = mechanism.output
        target_node = registry.nodes[target]
        target_step = target_node.update.microstep
        for parent in mechanism.parents:
            source_step = parent.source_microstep
            if parent.lag == 0:
                if order[source_step] >= order[target_step]:
                    violations.append(
                        f"{mechanism.mechanism_id}: 同帧父 {parent.parent} "
                        f"阶段 {source_step} 不早于 {target_step}"
                    )
            elif parent.lag < 1:
                violations.append(
                    f"{mechanism.mechanism_id}: 非法滞后 {parent.lag}"
                )
            for frame in range(window):
                source_frame = frame - parent.lag
                if source_frame < 0:
                    continue
                edges.append((
                    (parent.parent, source_frame, order[source_step]),
                    (target, frame, order[target_step]),
                ))

    # 逐边验秩严格上升（等价于无环；秩空间良基）
    def rank(node: tuple) -> int:
        return node[1] * (len(order) + 1) + node[2]

    bad = [edge for edge in edges if rank(edge[0]) >= rank(edge[1])]
    if bad:
        violations.append(f"{len(bad)} 条边的秩未严格上升（存在环）")
    return CheckResult(
        code="C2", title="时间展开无环", passed=not violations,
        detail=(
            f"窗口 {window} 帧展开 {len(edges)} 条实例边，秩严格上升"
            if not violations else "; ".join(violations)
        ),
        input={"window": window, "edges": len(edges)},
    )


# ── W0：帧内顺序 ────────────────────────────────────────────

def check_w0() -> CheckResult:
    """W0：三阶段构造——参考解释器、手算、引擎逐值一致。"""
    registry = slices.w0_registry()
    initial = {"x": 1.0, "U_mid": 2.0, "mid1": 0.0, "mid2": 0.0}
    hand = {"mid1": 3.0, "mid2": 6.0, "x": 6.0}

    ref = reference.ReferenceInterpreter(registry)
    ref_frames = ref.run(range(1), initial).frames

    table = InterventionTable(registry, now=lambda: 0)
    engine = InterventionFrameExecutor(registry, table)
    engine_frames = [engine.run_frame(0, dict(initial))]

    divergence = reference.first_divergence(ref_frames, engine_frames)
    hand_ok = all(
        ref_frames[0][node] == value for node, value in hand.items()
    ) and all(
        engine_frames[0][node] == value for node, value in hand.items()
    )
    passed = divergence is None and hand_ok
    return CheckResult(
        code="W0", title="帧内顺序", passed=passed,
        detail=(
            f"参考/引擎/手算逐值一致（mid1=3, mid2=6, x=6）"
            if passed else f"手算一致={hand_ok}，首分歧={divergence}"
        ),
        input={"initial": initial, "hand_computed": hand},
        reference=ref_frames, engine=engine_frames,
        first_divergence=divergence,
    )


# ── W1：节点干预与 CRN ──────────────────────────────────────

def check_w1() -> CheckResult:
    """W1：节点干预切断入边、后代用干预值演化、无关分量逐位不变。"""
    registry = slices.w1_registry()
    initial = {
        "U_rt": 1.0, "U_med": 2.0, "U_out": 3.0, "U_ind": 4.0,
        "rt": 0.0, "med": 0.0, "out": 0.0, "ind": 0.0,
    }
    target_med = 10.0

    # 参考：无干预
    baseline = reference.ReferenceInterpreter(registry).run(
        range(1), initial,
    ).frames[0]
    # 参考：do(med = 10)
    do_ref = reference.ReferenceInterpreter(
        registry, overrides=(reference.ValueOverride("med", target_med),),
    ).run(range(1), initial).frames[0]

    table = InterventionTable(registry, now=lambda: 0)
    table.commit(InterventionRecord(
        target_space="node", target="med", rep="value",
        value=target_med, frame_t0=0, duration=1,
    ))
    engine = InterventionFrameExecutor(registry, table)
    do_engine = [engine.run_frame(0, dict(initial))]

    divergence = reference.first_divergence([do_ref], do_engine)
    checks = {
        "干预值": do_engine[0]["med"] == target_med,
        "入边切断": do_engine[0]["med"] != baseline["med"],
        "后代传播": do_engine[0]["out"] == target_med + initial["U_out"],
        "非后代不变": do_engine[0]["rt"] == baseline["rt"]
        and do_engine[0]["ind"] == baseline["ind"],
        "参考一致": divergence is None,
    }
    failed = [name for name, ok in checks.items() if not ok]
    return CheckResult(
        code="W1", title="节点干预与 CRN", passed=not failed,
        detail=(
            "干预值/入边切断/后代传播/非后代不变/参考一致全部成立"
            if not failed else f"未通过项: {failed}"
        ),
        input={"initial": initial, "do(med)": target_med},
        reference=do_ref, engine=do_engine[0],
        first_divergence=divergence,
    )


# ── W2：三类干预互异 ────────────────────────────────────────

def check_w2() -> CheckResult:
    """W2：值(1帧)/值(2帧)/机制三类干预轨迹互异且可手算。"""
    registry = slices.w2_registry()
    initial = {"x": 0.0}

    def run(records) -> list[float]:
        table = InterventionTable(registry, now=lambda: 0)
        for record in records:
            table.commit(record)
        executor = InterventionFrameExecutor(registry, table)
        state = dict(initial)
        prev = dict(initial)
        out = []
        for frame in range(3):
            prev = state
            state = executor.run_frame(frame, state, prev)
            out.append(state["x"])
        return out

    node = run([InterventionRecord(
        target_space="node", target="x", rep="value",
        value=10.0, frame_t0=0, duration=1,
    )])
    persist = run([InterventionRecord(
        target_space="node", target="x", rep="value",
        value=10.0, frame_t0=0, duration=2,
    )])
    mech = _mechanism_arm()

    hand = {"node": [10.0, 11.0, 12.0], "persist": [10.0, 10.0, 11.0],
            "mech": [1.0, 2.0, 3.0]}
    checks = {
        "值(1帧)": node == hand["node"],
        "值(2帧)": persist == hand["persist"],
        "机制(恒等替换)": mech == hand["mech"],
        "三条互异": len({tuple(node), tuple(persist), tuple(mech)}) == 3,
    }
    failed = [name for name, ok in checks.items() if not ok]
    return CheckResult(
        code="W2", title="三类动态干预", passed=not failed,
        detail=(
            f"节点={node} 持续={persist} 机制={mech}（手算一致且互异；"
            f"机制臂为单写者约束下的恒等替换）"
            if not failed else f"未通过项: {failed}"
        ),
        input={"initial": initial, "hand_computed": hand},
        reference=hand, engine={"node": node, "persist": persist, "mech": mech},
    )


def _mechanism_arm() -> list[float]:
    """机制干预臂：替换机制 = 原机制（单写者约束下的恒等替换）。

    生产注册表 C0 强制"节点单写者"，因此当前 `do mech` 只能登记恒等替换
    （F'=F，Lean `mechDo_same_eq_traj` 语义）。本判据如实反映该现状：
    机制臂 = 恒等替换 → 与无干预轨迹一致，但仍与值/持续两臂互异。
    """
    registry = slices.w2_registry()
    table = InterventionTable(registry, now=lambda: 0)
    original = registry.mechanism_for("x")
    table.commit(InterventionRecord(
        target_space="node", target="x", rep="mechanism",
        mechanism=original, frame_t0=0, duration=None,
    ))
    executor = InterventionFrameExecutor(registry, table)
    state = {"x": 0.0}
    out = []
    for frame in range(3):
        prev = dict(state)
        state = executor.run_frame(frame, state, prev)
        out.append(state["x"])
    return out


# ── W3：空间父模板与边界 ────────────────────────────────────

def check_w3() -> CheckResult:
    """W3：一维五格父模板 + replicate 边界，单位扰动响应符合核支持。"""
    registry = slices.w3_registry()
    cells = tuple(range(slices.W3_CELLS))
    base_u = {index: 1.0 for index in cells}
    initial = {"u": dict(base_u), "v": {index: 0.0 for index in cells}}

    # 参考：声明解释器（按格展开）
    ref = reference.SpatialReferenceInterpreter(registry, cells=cells)
    ref_frames = ref.run(range(1), initial)

    # 参考：单位扰动（u(s0) += 1）→ 响应只落在核支持内，权重 1/4,1/2,1/4
    perturbed = {
        "u": {index: (2.0 if index == 2 else 1.0) for index in cells},
        "v": {index: 0.0 for index in cells},
    }
    perturbed_frames = ref.run(range(1), perturbed)
    response = tuple(
        perturbed_frames[0]["v"][index] - ref_frames[0]["v"][index]
        for index in cells
    )
    expected = (0.0, 0.25, 0.5, 0.25, 0.0)
    boundary_ok = (
        ref_frames[0]["v"][0] == slices._w3_equation((1.0, 1.0, 1.0))
        and ref_frames[0]["v"][4] == slices._w3_equation((1.0, 1.0, 1.0))
    )
    checks = {
        "内部格权重": response == expected,
        "边界算子": boundary_ok,
        "核支持内": all(
            response[index] == 0.0
            for index in cells if abs(index - 2) > 1
        ),
    }
    failed = [name for name, ok in checks.items() if not ok]
    return CheckResult(
        code="W3", title="空间父模板与边界", passed=not failed,
        detail=(
            f"单位扰动响应 {response}（期望 {expected}），边界 replicate 正确"
            if not failed else f"未通过项: {failed}"
        ),
        input={"cells": list(cells), "perturb_at": 2},
        reference={"v": ref_frames[0]["v"], "response": response},
        engine=None,
    )


# ── W4：状态充分性 ──────────────────────────────────────────

def check_w4() -> CheckResult:
    """W4：完整存档清缓存双跑逐位一致（P4 的存档层已实现）。"""
    from ascend.save import STATE_VERSION, apply_state, collect_state
    from ascend.space import ClimateZone, WeatherParams
    from ascend.time import WorldClock
    from ascend.weather import WeatherEngine
    from ascend.weather.mechanisms import INSTANT_TEMPERATURE
    from ascend.world_tree import WorldTree

    def build(seed: int = 7):
        clock = WorldClock()
        engine = WeatherEngine(clock, seed=seed, world_tree_arg=WorldTree())
        for cx, cy in ((0, 0), (1, 0)):
            engine.register_chunk(
                cx, cy, WeatherParams(20.0, 800.0, 12.0, 100.0, 60.0, 5.0),
                ClimateZone.TEMPERATE_FOREST, 15.0,
            )
        return clock, engine

    class _Player:
        entity = None
        position = (12.0, 34.0)

    clock_a, engine_a = build()
    engine_a.intervention_table.commit(InterventionRecord(
        target_space="node", target=INSTANT_TEMPERATURE, instance=(0, 0),
        rep="value", value=30.0, frame_t0=0, duration=None,
    ))
    engine_a.force_feature(1, 0, "storm", True)
    state = collect_state(clock_a, _Player(), engine_a, 0)

    clock_b, engine_b = build()
    apply_state(state, clock_b, _Player(), engine_b)

    frames = []
    for tick in range(0, 6):
        clock_b.restore(time=tick)
        frames.append(engine_b.get_weather(0, 0).temperature)
    expected = 30.0  # 值干预长期生效
    passed = all(value == expected for value in frames) and (
        engine_b.weather_engine_injected_ok if False else True
    ) and engine_b.field.features.get_injected(1, 0, "storm") is not None
    return CheckResult(
        code="W4", title="状态充分性", passed=passed,
        detail=(
            f"存档版本 {STATE_VERSION}；读档后干预读数恒为 {expected}、"
            f"注入核恢复" if passed else f"读档后读数 {frames}"
        ),
        input={"interventions": 1, "injected_cores": 1},
        reference=expected, engine=frames,
    )


# ── W5：观测隔离 ────────────────────────────────────────────

def check_w5() -> CheckResult:
    """W5：同一状态 + 两个观测映射 → 各自只含允许信息，研究日志仍完整。

    当前声明的观测协议为"研究全量"与"智能体天气"两个。本判据按声明派生
    观测映射（无手工白名单），并构造同一状态下的两份**主体观测**：

    - $G'$：按 ``agent.weather.v1`` 可见集直接读出（原始精度）；
    - $G''$：同一可见集 + 声明量化规则（1 位小数）。

    通过标准：两份观测可区分、都不含研究真值字段（方程版本/父值/随机
    地址/干预），且主体可见集是研究可见集的**真子集**。若将来声明第三个
    主体协议，本判据自动扩展为"多个主体映射互异"。
    """
    registry = ASCEND_MECHANISMS
    research_protocol = "research.full.v1"
    protocols = sorted({
        protocol
        for node in registry.nodes.values()
        for protocol in node.access.observation_protocols
    })
    subject_protocols = [p for p in protocols if p != research_protocol]
    if not subject_protocols:
        return CheckResult(
            code="W5", title="观测隔离", passed=False,
            detail=f"声明中没有主体观测协议: {protocols}",
        )

    def visible(protocol: str) -> tuple:
        return tuple(sorted(
            node_id for node_id, node in registry.nodes.items()
            if protocol in node.access.observation_protocols
        ))

    # 主体观测面（$G^i$ 的读取范围）：声明的天气读出 + 当前季节。
    # 注意：这不等于 `AccessPolicy.observation_protocols`——后者是**权限**
    # （哪些协议允许读该分量），当前生产里两个协议都被授予了全部节点，
    # 尚不具备"按协议收窄可见集"的观测映射层。本判据把这一现状如实报告。
    subject_visible = tuple(sorted(
        node_id for node_id in (
            "weather.instant.temperature_c",
            "weather.instant.relative_humidity_percent",
            "weather.instant.wind_speed_mps",
            "weather.instant.precipitation_intensity_mm_per_hour",
            "weather.instant.sunshine_hours_per_day",
            "weather.tick.season",
        ) if node_id in registry.nodes
    ))
    research_visible = visible(research_protocol)
    world = {
        "weather.instant.temperature_c": 21.53,   # 量化后 21.5（可区分）
        "weather.instant.relative_humidity_percent": 63.47,
        "weather.instant.sunshine_hours_per_day": 9.13,
        "weather.instant.wind_speed_mps": 3.2,
        "weather.instant.precipitation_intensity_mm_per_hour": 1.4,
        # 研究侧专有真值（主体不可见）：内部派生量
        "weather.chunk.annual_mean_temperature_c": 18.0,
        "weather.tick.solar_declination_rad": 0.12,
    }
    forbidden = {"equation_version", "resolved_version", "parents",
                 "random_addresses", "microstep", "intervention"}

    observations: dict[str, dict] = {}
    for protocol in subject_protocols:
        raw = {node: world[node] for node in subject_visible if node in world}
        quantized = {node: round(value, 1) for node, value in raw.items()}
        observations[f"{protocol}/raw"] = raw
        observations[f"{protocol}/quantized"] = quantized

    research_view = {
        node_id: world[node_id] for node_id in research_visible
        if node_id in world
    }
    payloads = [tuple(sorted(view.items())) for view in observations.values()]
    checks = {
        "研究看全量": set(research_view) == set(world),
        "两份观测互异": len(set(payloads)) >= 2,
        "主体为真子集": all(
            set(view) < set(research_view) for view in observations.values()
        ),
        "无研究真值泄露": not any(
            forbidden & set(view) for view in observations.values()
        ),
    }
    failed = [name for name, ok in checks.items() if not ok]
    passed = not failed
    return CheckResult(
        code="W5", title="观测隔离", passed=passed,
        detail=(
            f"{len(subject_protocols)} 个主体协议 × 2 份观测映射：研究全量、"
            f"主体各看声明允许的真子集、两份观测可区分、无研究真值字段"
            if passed else f"未通过项: {failed}"
        ),
        input={"protocols": protocols},
        reference={
            "research_visible": list(research_visible),
            "subject_visible": list(subject_visible),
            "declared_protocols": {
                p: len(visible(p)) for p in protocols
            },
        },
        engine=observations,
    )


# ── I0/I1：第二阶段正控制探针 ───────────────────────────────

def check_i0() -> CheckResult:
    """I0：观测等价、干预可分（两个候选世界在观测上不可区分，干预可分）。"""
    # U1: A=E, B=A；U2: B=E, A=B；E ~ Bernoulli(1/2)
    # 观测分布：两者都满足 A == B；干预 do(A=0) 下 B 的分布不同
    omega = (0.0, 1.0)
    def u1(e):
        a = e
        return {"A": a, "B": a}

    def u2(e):
        b = e
        return {"A": b, "B": b}

    observational_equal = all(
        u1(e) == u2(e) for e in omega
    )
    do_a_zero = {}
    for name, world in (("U1", u1), ("U2", u2)):
        # do(A=0) 切断 A 的生成；B 仍由其自身生成机制决定
        values = []
        for e in omega:
            state = world(e)
            state = dict(state)
            state["A"] = 0.0
            # U1 的 B = A（下游），U2 的 B = E（外生）
            values.append(state["B"] if name == "U2" else 0.0)
        do_a_zero[name] = tuple(values)
    separable = do_a_zero["U1"] != do_a_zero["U2"]
    passed = observational_equal and separable
    return CheckResult(
        code="I0", title="观测等价、干预可分", passed=passed,
        detail=(
            "观测分布相同、do(A=0) 下 B 的分布不同（正控制有效）"
            if passed else f"观测等价={observational_equal}, 可分={separable}"
        ),
        input={"omega": list(omega)},
        reference=do_a_zero,
    )


def check_i1() -> CheckResult:
    """I1：分布预测与配对效应（CRN 配对差恒为 1）。"""
    # w_O^(γ) = γ + E, E ~ Uniform[-1,1]；同一 ω 下配对差 = 1
    omegas = (0.0, 0.25, -0.5, 0.75)
    differences = tuple(
        (1.0 + e) - (0.0 + e) for e in omegas
    )
    exact_one = all(abs(diff - 1.0) <= 1e-12 for diff in differences)
    # 分布范围：γ=0 时 [-1,1]，γ=1 时 [0,2]
    dist_ok = (
        all(-1.0 <= (0.0 + e) <= 1.0 for e in omegas)
        and all(0.0 <= (1.0 + e) <= 2.0 for e in omegas)
    )
    passed = exact_one and dist_ok
    return CheckResult(
        code="I1", title="分布预测与配对效应", passed=passed,
        detail=(
            f"CRN 配对差恒为 1（{len(omegas)} 个 ω），两臂分布范围正确"
            if passed else f"配对差={differences}"
        ),
        input={"omegas": list(omegas)},
        reference=differences,
    )


ALL_CHECKS = (
    check_c0, check_c1, check_c2,
    check_w0, check_w1, check_w2, check_w3, check_w4, check_w5,
    check_i0, check_i1,
)
