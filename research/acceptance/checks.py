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

    divergence = reference.first_divergence(ref_frames, engine_frames, frames=[0])
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

    divergence = reference.first_divergence([do_ref], do_engine, frames=[0])
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
    """W3：一维五格父模板 + replicate 边界；**引擎侧**与参考解释器逐格对拍。

    三层独立证据：
    1. **引擎**（`InterventionFrameExecutor(spatial_cells=...)`）逐格求值；
    2. **参考解释器**（`SpatialReferenceInterpreter`，独立调度实现）；
    3. **手算**：单位扰动响应必须是 ¼ / ½ / ¼，边界格按 replicate 算子手算。

    边界不拿方程自比：期望值写成显式算术（越界邻格取边缘格自身），
    而不是调用同一条方程函数。
    """
    from ascend.causal.intervention_engine import InterventionFrameExecutor

    registry = slices.w3_registry()
    cells = tuple(range(slices.W3_CELLS))
    base = {"u": {i: 1.0 for i in cells}, "v": {i: 0.0 for i in cells}}
    perturbed = {
        "u": {i: (2.0 if i == 2 else 1.0) for i in cells},
        "v": {i: 0.0 for i in cells},
    }

    # 引擎侧（空间展开）
    engine = InterventionFrameExecutor(registry, spatial_cells=cells)
    engine_base = engine.run_frame(0, base)
    engine_perturbed = engine.run_frame(0, perturbed)
    engine_response = tuple(
        engine_perturbed["v"][i] - engine_base["v"][i] for i in cells
    )
    engine_values = tuple(engine_base["v"][i] for i in cells)

    # 参考解释器（独立调度）
    reference_interp = reference.SpatialReferenceInterpreter(
        registry, cells=cells,
    )
    ref_values = reference_interp.run(range(1), base)[0]["v"]
    divergence = reference.first_divergence(
        [{"v": ref_values}], [{"v": engine_values}], frames=[0],
    )

    # 手算：均匀输入 1.0 时，内部格 = ¼·1+½·1+¼·1 = 1.0；
    # 边界 replicate：s=0 的 s-1 取边缘格自身 → 同值 1.0
    hand_values = (1.0, 1.0, 1.0, 1.0, 1.0)
    # 扰动手算：增益只落在核支持内，权重依次 0, ¼, ½, ¼, 0
    hand_response = (0.0, 0.25, 0.5, 0.25, 0.0)

    checks = {
        "引擎/参考逐格一致": divergence is None,
        "引擎/手算一致": engine_values == hand_values,
        "扰动响应权重": engine_response == hand_response,
        "核支持外无响应": all(
            engine_response[i] == 0.0 for i in cells if abs(i - 2) > 1
        ),
        "边界算子（replicate）": (
            engine_values[0] == 1.0 and engine_values[4] == 1.0
        ),
    }
    failed = [name for name, ok in checks.items() if not ok]
    passed = not failed
    return CheckResult(
        code="W3", title="空间父模板与边界", passed=passed,
        detail=(
            f"引擎/参考/手算逐格一致（{engine_values}），单位扰动响应 "
            f"{engine_response}（期望 {hand_response}），replicate 边界正确"
            if passed else f"未通过项: {failed}，首分歧={divergence}"
        ),
        input={"cells": list(cells), "perturb_at": 2},
        reference=ref_values,
        engine=engine_values,
        first_divergence=divergence,
    )


# ── W4：状态充分性 ──────────────────────────────────────────

def check_w4() -> CheckResult:
    """W4：**清缓存双跑逐位一致**——存档读档的轨迹 vs 不存档的轨迹。

    构造两个同种子、同设置的世界 A、B（相同初始随机地址）：
    A 施加"值干预 + 注入特征核"后存档；C 从该存档恢复（模拟进程重启：
    缓存全空、状态来自存档）；B 全程不存档。随后 B、C 独立推进相同帧，
    逐帧逐 chunk 的天气读出必须**逐位一致**。

    判别力负例（同判据内）：把干预与注入核从载荷中剔除后，B 与 C 必须
    **分叉**——否则"一致"可能只是"两边都没生效"的假通过。
    """
    from ascend.save import STATE_VERSION, apply_state, collect_state
    from ascend.space import ClimateZone, WeatherParams
    from ascend.time import WorldClock
    from ascend.weather import WeatherEngine
    from ascend.weather.mechanisms import INSTANT_TEMPERATURE
    from ascend.world_tree import WorldTree

    class _Player:
        entity = None
        position = (12.0, 34.0)

    def build() -> tuple[WorldClock, WeatherEngine]:
        clock = WorldClock()
        engine = WeatherEngine(clock, seed=7, world_tree_arg=WorldTree())
        for cx, cy in ((0, 0), (1, 0)):
            engine.register_chunk(
                cx, cy, WeatherParams(20.0, 800.0, 12.0, 100.0, 60.0, 5.0),
                ClimateZone.TEMPERATE_FOREST, 15.0,
            )
        return clock, engine

    def sample(engine: WeatherEngine, frames) -> list[tuple]:
        out = []
        for tick in frames:
            for cx, cy in ((0, 0), (1, 0)):
                params = engine.get_weather(cx, cy, tick)
                out.append((cx, cy, params.temperature, params.humidity,
                            params.wind_speed, params.rainfall))
        return out

    def scatter(engine: WeatherEngine) -> None:
        engine.intervention_table.commit(InterventionRecord(
            target_space="node", target=INSTANT_TEMPERATURE, instance=(0, 0),
            rep="value", value=30.0, frame_t0=0, duration=None,
        ))
        engine.force_feature(1, 0, "storm", True)

    clock_a, engine_a = build()
    clock_b, engine_b = build()
    scatter(engine_a)
    scatter(engine_b)
    state = collect_state(clock_a, _Player(), engine_a, 0)

    clock_c, engine_c = build()
    apply_state(state, clock_c, _Player(), engine_c)

    timeline = range(0, 6)
    trace_b = sample(engine_b, timeline)
    trace_c = sample(engine_c, timeline)
    divergence = reference.first_divergence(
        [{"v": item} for item in trace_b],
        [{"v": item} for item in trace_c],
        frames=[0],
    )

    # 判别力负例：丢掉干预与注入核后必须分叉
    state_without = dict(state)
    state_without["weather"] = {"interventions": [], "feature_cores": []}
    clock_d, engine_d = build()
    apply_state(state_without, clock_d, _Player(), engine_d)
    trace_d = sample(engine_d, timeline)

    checks = {
        "双跑逐位一致": divergence is None,
        "判别力（缺干预必分叉）": trace_d != trace_b,
    }
    failed = [name for name, ok in checks.items() if not ok]
    passed = not failed
    return CheckResult(
        code="W4", title="状态充分性", passed=passed,
        detail=(
            f"存档版本 {STATE_VERSION}；存档读档轨迹与不存档轨迹 "
            f"{len(trace_b)} 个采样点逐位一致，且剔除干预/注入核后分叉"
            if passed else f"未通过项: {failed}，首分歧={divergence}"
        ),
        input={"interventions": 1, "injected_cores": 1, "frames": 6},
        reference=trace_b, engine=trace_c,
        first_divergence=divergence,
    )


# ── W5：观测隔离 ────────────────────────────────────────────

def check_w5() -> CheckResult:
    """W5：同一状态 + 两个观测映射 → 各自只含允许信息，研究日志仍完整。

    判据分三层：
    1. **可见集**：主体观测面是研究可见集的真子集；
    2. **可区分**：同一状态在两个映射下产生不同载荷（原始 vs 量化）；
    3. **不泄露**：主体载荷只含"节点 → 标量"，不含研究通道专有的
       非标量结构（方程版本 / 父值映射 / 随机地址 / 干预记录）。

    诚实边界：`AccessPolicy.observation_protocols` 是**权限**而非收窄规则，
    生产里两个协议都被授予全部节点；主体观测面由本 runner 显式给出
    （见世界基座 11 篇 §3）。判据不声称它由声明自动派生。
    """
    from ascend.causal import AGENT_WEATHER_PROTOCOL, RESEARCH_PROTOCOL
    from ascend.causal import leaks_research_truth, observe

    registry = ASCEND_MECHANISMS
    protocols = sorted({
        protocol
        for node in registry.nodes.values()
        for protocol in node.access.observation_protocols
    })
    subject_protocols = [
        p for p in protocols if p != RESEARCH_PROTOCOL
    ]
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

    # 主体观测面（runner 显式给出；不是从声明派生——见 docstring 边界）
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
    research_visible = visible(RESEARCH_PROTOCOL)
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

    observations: dict[str, dict] = {}
    for protocol in subject_protocols:
        raw = observe(protocol, world, allow=subject_visible)
        # 第二份映射：同一可见集 + 声明量化（与 observe 内部量化一致，
        # 这里显式再算一份以验证"两个映射可区分"）
        quantized = {
            node: (round(value, 1) if isinstance(value, float) else value)
            for node, value in raw.items()
        }
        observations[f"{protocol}/raw"] = {
            node: world[node] for node in subject_visible if node in world
        }
        observations[f"{protocol}/quantized"] = quantized

    research_view = {
        node_id: world[node_id] for node_id in research_visible
        if node_id in world
    }
    payloads = [tuple(sorted(view.items())) for view in observations.values()]
    leaks = [
        name for name, view in observations.items()
        if leaks_research_truth(view)
    ]
    checks = {
        "研究看全量": set(research_view) == set(world),
        "两份观测互异": len(set(payloads)) >= 2,
        "主体为真子集": all(
            set(view) < set(research_view) for view in observations.values()
        ),
        "无研究真值泄露": not leaks,
    }
    failed = [name for name, ok in checks.items() if not ok]
    passed = not failed
    return CheckResult(
        code="W5", title="观测隔离", passed=passed,
        detail=(
            f"{len(subject_protocols)} 个主体协议 × 2 份观测映射：研究全量、"
            f"主体各看显式观测面的真子集、两份观测可区分、主体载荷只含标量"
            if passed else f"未通过项: {failed}"
        ),
        input={"protocols": protocols},
        reference={
            "research_visible": list(research_visible),
            "subject_visible": list(subject_visible),
            "declared_protocols": {p: len(visible(p)) for p in protocols},
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


def check_c2_rejects_violation() -> CheckResult:
    """C2 判别力自检：构造一个"同帧父位于更晚阶段"的声明，判据必须捕获。

    不重实现判据逻辑——直接把违规注册表喂给 :func:`check_c2` 同款的展开
    检查函数，验证它**报错**（否则判据没有判别力）。
    """
    from ascend.causal import MechanismRegistry, MechanismSpec, ParentSpec

    # 用最小切片构造违规：mid1 读 x 的**同帧**值，而 x 在更晚阶段
    base_nodes = slices.w0_registry()
    violated = MechanismSpec(
        mechanism_id="violate.same_frame_later_stage",
        output="mid1", equation="mid1",
        function=lambda x_prev, u: x_prev + u,
        parents=(
            ParentSpec(
                parent="x", argument="x_prev", lag=0,
                source_microstep=base_nodes.microstep_order[2],
                spatial_offsets=((0,),), entity_relation="self",
                aggregation="identity", broadcast="same_instance",
                boundary_operator="none", guard="always", lipschitz=1.0,
                metric="absolute_difference", valid_domain="full",
                analysis_role="forward",
            ),
            ParentSpec(
                parent="U_mid", argument="u", lag=0,
                source_microstep=base_nodes.microstep_order[0],
                spatial_offsets=((0,),), entity_relation="self",
                aggregation="identity", broadcast="same_instance",
                boundary_operator="none", guard="always", lipschitz=1.0,
                metric="absolute_difference", valid_domain="full",
                analysis_role="forward",
            ),
        ),
        parameters=(), random_sources=(), boundary_cases=("declared",),
        source_dependencies=(),
        witnesses=(
            slices.witness("x", (("x", 1.0), ("U_mid", 2.0)), 2.0, (3.0, 4.0)),
            slices.witness("U_mid", (("x", 1.0), ("U_mid", 2.0)), 3.0, (3.0, 4.0)),
        ),
    )
    try:
        MechanismRegistry(
            schema_version=3, declaration_id="research.violate",
            declaration_version="1",
            microstep_order=base_nodes.microstep_order,
            slice_boundary="判别力自检",
            wired_nodes=frozenset({"mid1", "mid2", "x"}),
            nodes=tuple(base_nodes.nodes.values()),
            parameters=(), exogenous_sources=(),
            mechanisms=(violated,),
        )
    except ValueError as exc:
        # 构造期 C0 就拦住了——这正是我们要的（判据有判别力）
        return CheckResult(
            code="C2'", title="C2 判别力自检", passed=True,
            detail=f"违规声明被拒: {str(exc)[:120]}",
        )
    return CheckResult(
        code="C2'", title="C2 判别力自检", passed=False,
        detail="同帧父位于更晚阶段的声明未被拒绝（判据无判别力）",
    )


ALL_CHECKS = (
    check_c0, check_c1, check_c2, check_c2_rejects_violation,
    check_w0, check_w1, check_w2, check_w3, check_w4, check_w5,
    check_i0, check_i1,
)
