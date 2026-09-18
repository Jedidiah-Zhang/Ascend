"""世界验收判据 — C0–C2 / W0–W5 / I0–I1（每项独立报告，产物含首分歧）。

判据来源：[世界验收协议](../../../docs/研究理论/世界基座/04-世界验收协议.md)
与[第一阶段实施定义](../../../docs/研究理论/第一阶段实施定义.md) §9。

设计原则（04 §4.1）：参考计算必须**独立于生产实现**——本模块用
``reference.py`` 的声明解释器与手算表，不复用引擎求值路径；引擎侧只
提供被比较的轨迹。每项判据记录输入、参考输出、引擎输出与首个分歧节点。
"""

from __future__ import annotations

import itertools
import json
import math
from dataclasses import dataclass, field

from ascend.causal import (
    InterventionTimeline,
    PlannedIntervention,
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
            "layer": CHECK_LAYERS.get(self.code, "L3"),
            "passed": self.passed,
            "detail": self.detail,
            "input": _jsonable(self.input),
            "reference": _jsonable(self.reference),
            "engine": _jsonable(self.engine),
            "first_divergence": _jsonable(self.first_divergence),
        }


#: 判据的证据分层（#50）：L0 生成完整性 / L1 数学有效性 /
#: L2 求值一致性 / L3 世界动力学契约（含正控制）。
CHECK_LAYERS: dict[str, str] = {
    "C0": "L0", "C1": "L0",
    "C2": "L1", "C2'": "L1",
    "W0": "L2", "W1": "L2", "W2": "L2", "W3": "L2",
    "W4": "L3", "W5": "L3", "W5'": "L3",
    "L3-历史": "L3", "L3-CRN": "L3", "L3-状态": "L3",
    "I0": "L3", "I1": "L3",
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

    table = InterventionTimeline(registry, now=lambda: 0)
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

    table = InterventionTimeline(registry, now=lambda: 0)
    table.plan(PlannedIntervention(
        target_space="node", target="med", value=target_med,
        start_frame=0, stop_frame=1, source="acceptance",
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


# ── W2：动态干预与机制替换关闭（负例）──────────────────────

def check_w2() -> CheckResult:
    """W2：值(单帧)/值(窗口)两臂互异且可手算；运行内机制替换被拒绝；
    结构变体 = 新世界身份（WC-1.3）：变体世界身份不同、轨迹互异且与
    参考一致（"值干预 vs 结构变体"对照）。"""
    registry = slices.w2_registry()
    variant = slices.w2_variant_registry()
    initial = {"x": 0.0}

    def run_engine(target_registry, entries) -> list[float]:
        table = InterventionTimeline(target_registry, now=lambda: 0)
        for entry in entries:
            table.plan(entry)
        executor = InterventionFrameExecutor(target_registry, table)
        state = dict(initial)
        out = []
        for frame in range(3):
            prev = state
            state = executor.run_frame(frame, state, prev)
            out.append(state["x"])
        return out

    node = run_engine(registry, [PlannedIntervention(
        target_space="node", target="x", value=10.0,
        start_frame=0, stop_frame=1, source="acceptance",
    )])
    window = run_engine(registry, [PlannedIntervention(
        target_space="node", target="x", value=10.0,
        start_frame=0, stop_frame=2, source="acceptance",
    )])

    from ascend.net.handlers.research_handler import make_research_handler
    handler = make_research_handler(
        InterventionTimeline(registry, now=lambda: 0),
    )
    negative = handler["research_do"]({"payload": {
        "space": "node", "target": "x", "value": 10.0,
        "rep": "mechanism", "mechanism_id": "toy.x",
    }})
    closed = negative["payload"].get("success") is False

    # 结构变体世界（WC-1.3）：身份不同、轨迹互异且参考=引擎
    base_engine = run_engine(registry, [])
    # 参考帧 1..3 ↔ 引擎帧 0..2（映射口径同 W0：引擎首帧对应参考首帧）
    base_ref = reference.ReferenceInterpreter(registry).run(
        range(1, 4), initial).frames
    variant_ref = reference.ReferenceInterpreter(variant).run(
        range(1, 4), initial).frames
    variant_engine = run_engine(variant, [])
    divergence = reference.first_divergence(
        variant_ref,
        [{"x": value} for value in variant_engine],
        frames=[0, 1, 2],
    )
    base_values = [frame["x"] for frame in base_ref]
    variant_values = [frame["x"] for frame in variant_ref]
    identity_distinct = registry.declaration_hash != variant.declaration_hash

    hand = {"node": [10.0, 11.0, 12.0], "window": [10.0, 10.0, 11.0],
            "variant": [100.0, 200.0, 300.0]}
    checks = {
        "值(单帧)": node == hand["node"],
        "值(窗口)": window == hand["window"],
        "两臂互异": node != window,
        "机制替换路径关闭": closed,
        "变体身份不同": identity_distinct,
        "变体轨迹互异": variant_values != base_values,
        "变体引擎≠基线引擎": variant_engine != base_engine,
        "变体参考=引擎": divergence is None,
    }
    failed = [name for name, ok in checks.items() if not ok]
    return CheckResult(
        code="W2", title="动态干预（替换关闭 + 结构变体）", passed=not failed,
        detail=(
            f"节点={node} 窗口={window}；变体世界={variant_values}"
            f"（身份不同={identity_distinct}）；运行内机制替换被拒绝"
            if not failed else f"未通过项: {failed}"
        ),
        input={"initial": initial, "hand_computed": hand},
        reference={"node": hand["node"], "window": hand["window"],
                   "variant": variant_values},
        engine={
            "node": node, "window": window, "closed": closed,
            "variant_values": variant_values,
            "variant_engine": variant_engine,
            "baseline_engine": base_engine,
            "variant_identity_distinct": identity_distinct,
            "variant_divergence": divergence,
        },
    )


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

    # 边界敏感性（#50）：扰动边缘格——replicate 与"周期环绕"响应不同。
    # 手算（replicate，左边缘扰动 u(0)+1）：Δv(0)=¼+½=¾（越界邻格取自身）、
    # Δv(1)=¼、其余 0；环绕则 Δu(-1)=Δu(4)=0 → Δv(0)=½、Δv(4)=¼。
    # 断言 replicate 期望值即拒绝"环绕冒充 replicate"的实现。
    perturbed_left = {
        "u": {i: (2.0 if i == 0 else 1.0) for i in cells},
        "v": {i: 0.0 for i in cells},
    }
    perturbed_right = {
        "u": {i: (2.0 if i == 4 else 1.0) for i in cells},
        "v": {i: 0.0 for i in cells},
    }
    engine_left = engine.run_frame(0, perturbed_left)
    engine_right = engine.run_frame(0, perturbed_right)
    left_response = tuple(
        engine_left["v"][i] - engine_base["v"][i] for i in cells
    )
    right_response = tuple(
        engine_right["v"][i] - engine_base["v"][i] for i in cells
    )
    hand_left_replicate = (0.75, 0.25, 0.0, 0.0, 0.0)
    hand_right_replicate = (0.0, 0.0, 0.0, 0.25, 0.75)
    hand_left_wrap = (0.5, 0.25, 0.0, 0.0, 0.25)
    hand_right_wrap = (0.25, 0.0, 0.0, 0.25, 0.5)

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
        "左边缘扰动响应（replicate）": left_response == hand_left_replicate,
        "右边缘扰动响应（replicate）": right_response == hand_right_replicate,
        "边界算子判别（非环绕）": (
            left_response != hand_left_wrap
            and right_response != hand_right_wrap
        ),
    }
    failed = [name for name, ok in checks.items() if not ok]
    passed = not failed
    return CheckResult(
        code="W3", title="空间父模板与边界", passed=passed,
        detail=(
            f"引擎/参考/手算逐格一致（{engine_values}），单位扰动响应 "
            f"{engine_response}，边缘扰动 replicate 期望 "
            f"{hand_left_replicate}/{hand_right_replicate}（拒绝环绕）"
            if passed else f"未通过项: {failed}，首分歧={divergence}"
        ),
        input={"cells": list(cells), "perturb_at": [2, 0, 4]},
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

    判别力负例（同判据内，**单因子**，见 #50）：分别只剔除节点干预、
    只剔除特征核计划（注入核为时间线投影，WC-6.5）、两者都剔除，
    三种变体都必须与不存档轨迹**分叉**——否则"一致"可能只是
    "两边都没生效"的假通过，且单因子避免"多个缺陷一起删"掩盖单项失效。
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
        engine.intervention_table.plan(PlannedIntervention(
            target_space="node", target=INSTANT_TEMPERATURE, instance=(0, 0),
            value=30.0, start_frame=0, stop_frame=None, source="acceptance",
        ))
        engine.force_feature(1, 0, "storm", True)

    clock_a, engine_a = build()
    clock_b, engine_b = build()
    scatter(engine_a)
    scatter(engine_b)
    # 采样窗口必须落在注入核出生之后：把时钟推进到窗口末端，
    # 窗口取 [now, now+6) 的过去帧（否则"缺核"负例早于核出生，
    # 核未生效，判别力形同虚设）。
    window_start = clock_a.time
    for clock in (clock_a, clock_b):
        clock.skip(6)
    state = collect_state(clock_a, _Player(), engine_a, 0)

    clock_c, engine_c = build()
    apply_state(state, clock_c, _Player(), engine_c)

    timeline = range(window_start, window_start + 6)
    trace_b = sample(engine_b, timeline)
    trace_c = sample(engine_c, timeline)
    divergence = reference.first_divergence(
        [{"v": item} for item in trace_b],
        [{"v": item} for item in trace_c],
        frames=[0],
    )

    # 判别力负例（单因子）：仅缺节点干预 / 仅缺特征核计划 / 全缺——各自必须分叉。
    # 注入核是时间线投影（WC-6.5 / #51）：缺核臂 = 剔除 field_feature 计划。
    def _filtered(*, keep_node: bool, keep_feature: bool) -> dict:
        payload = dict(state)
        interventions = state["weather"]["interventions"]

        def keep(item: dict) -> bool:
            is_feature = item.get("target_space") == "field_feature"
            return keep_feature if is_feature else keep_node

        payload["weather"] = {
            "interventions": {
                "plan": [item for item in interventions["plan"] if keep(item)],
                "records": [
                    item for item in interventions["records"] if keep(item)
                ],
            },
        }
        return payload

    arms = {
        "仅缺干预": _filtered(keep_node=False, keep_feature=True),
        "仅缺注入核": _filtered(keep_node=True, keep_feature=False),
        "干预与注入核全缺": _filtered(keep_node=False, keep_feature=False),
    }
    divergences: dict[str, bool] = {}
    for name, payload in arms.items():
        clock_x, engine_x = build()
        apply_state(payload, clock_x, _Player(), engine_x)
        divergences[name] = sample(engine_x, timeline) != trace_b

    checks = {
        "双跑逐位一致": divergence is None,
        "判别力（仅缺干预必分叉）": divergences["仅缺干预"],
        "判别力（仅缺注入核必分叉）": divergences["仅缺注入核"],
        "判别力（干预与注入核全缺必分叉）": divergences["干预与注入核全缺"],
    }
    failed = [name for name, ok in checks.items() if not ok]
    passed = not failed
    return CheckResult(
        code="W4", title="状态充分性", passed=passed,
        detail=(
            f"存档版本 {STATE_VERSION}；存档读档轨迹与不存档轨迹 "
            f"{len(trace_b)} 个采样点逐位一致，单因子负例（仅缺干预/"
            f"仅缺注入核/全缺）均分叉"
            if passed else f"未通过项: {failed}，首分歧={divergence}"
        ),
        input={
            "interventions": 1, "injected_cores": 1, "frames": 6,
            "sabotage": ["drop_node_interventions", "drop_feature_plans", "drop_both"],
        },
        reference=trace_b, engine=trace_c,
        first_divergence=divergence,
    )


# ── W5：观测隔离 ────────────────────────────────────────────

def check_w5(*, observe_fn=None) -> CheckResult:
    """W5：同一状态 + 两个观测映射 → 各自只含允许信息，研究日志仍完整。

    判据分四层（主体通道**唯一来源是 ``observe``**，不再绕过它直读 world）：
    1. **可见集精确**：主体载荷的键恰好等于被授予的可见集
       （空观测 / 越权节点都会变红）；
    2. **量化生效**：主体协议按声明量化（21.53 → 21.5）；
    3. **可区分**：同一状态 + 两个映射（主体协议 vs 研究协议同可见集）
       产生不同载荷；
    4. **不泄露**：主体载荷只含"节点 → 标量"，不含研究通道专有的
       非标量结构（方程版本 / 父值映射 / 随机地址 / 干预记录）。

    判别力由 :func:`check_w5_rejects_broken_observe` 自检（空观测、
    越权泄露必须变红）。

    诚实边界：`AccessPolicy.observation_protocols` 是**权限**而非收窄规则，
    生产里两个协议都被授予全部节点；主体观测面由本 runner 显式给出
    （见世界基座 11 篇 §3）。判据不声称它由声明自动派生。

    Args:
        observe_fn: 观测映射（默认生产 ``observe``；判别力自检注入坏实现）。
    """
    from ascend.causal import AGENT_WEATHER_PROTOCOL, RESEARCH_PROTOCOL
    from ascend.causal import leaks_research_truth, observe

    observe_fn = observe if observe_fn is None else observe_fn

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
    expected_subject_keys = {
        node for node in subject_visible if node in world
    }

    # 全部观测载荷只能经 observe_fn 产生（主体通道唯一来源）
    observations: dict[str, dict] = {}
    for protocol in subject_protocols:
        observations[protocol] = observe_fn(
            protocol, world, allow=subject_visible,
        )
    # 第二份映射：研究协议对同一可见集（不量化）——与主体映射可区分
    observations[f"{RESEARCH_PROTOCOL}/same_allow"] = observe_fn(
        RESEARCH_PROTOCOL, world, allow=subject_visible,
    )

    research_view = {
        node_id: world[node_id] for node_id in research_visible
        if node_id in world
    }
    subject_payloads = [
        view for name, view in observations.items()
        if not name.startswith(RESEARCH_PROTOCOL)
    ]
    payloads = {
        json.dumps(view, sort_keys=True, default=repr)
        for view in observations.values()
    }
    leaks = [
        name for name, view in observations.items()
        if leaks_research_truth(view)
    ]
    checks = {
        "研究看全量": set(research_view) == set(world),
        "主体可见集精确": all(
            set(view) == expected_subject_keys for view in subject_payloads
        ),
        "主体量化生效": any(
            view.get("weather.instant.temperature_c") == 21.5
            for view in subject_payloads
        ),
        "两份观测互异": len(payloads) >= 2,
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
            f"{len(subject_protocols)} 个主体协议：主体载荷键 = 授予可见集、"
            f"量化生效、两份映射可区分、主体载荷只含标量"
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


def check_w5_rejects_broken_observe() -> CheckResult:
    """W5 判别力自检：破坏观测通道必须让 W5 变红（#50）。

    两个单条件破坏（改回即转绿）：
    - 空观测：``observe`` 什么都不返回 → "主体可见集精确"必须失败；
    - 越权 + 真值泄露：把隐藏节点与研究结构塞进载荷 → 可见集与
      泄露判据必须失败。
    """

    def empty_observe(protocol, world, *, allow):
        return {}

    def leaky_observe(protocol, world, *, allow):
        payload = {node: world[node] for node in allow if node in world}
        payload["weather.chunk.annual_mean_temperature_c"] = world.get(
            "weather.chunk.annual_mean_temperature_c"
        )
        payload["trace"] = {"equation_version": "v"}
        return payload

    failures: list[str] = []
    for name, fn in (("空观测", empty_observe), ("越权+泄露", leaky_observe)):
        try:
            outcome = check_w5(observe_fn=fn)
        except Exception as exc:  # 自检自身异常 = 未取得判红证据
            failures.append(f"{name}: 检查异常 {type(exc).__name__}")
            continue
        if outcome.passed:
            failures.append(f"{name}: 未被判红")
    passed = not failures
    return CheckResult(
        code="W5'", title="W5 判别力自检", passed=passed,
        detail=(
            "空观测 / 越权泄露两种破坏均被 W5 判红"
            if passed else "; ".join(failures)
        ),
        input={"sabotage": ["empty_observe", "leaky_observe"]},
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
    """C2 判别力自检：构造**单一条件**违规的声明，判据必须捕获（#50）。

    只违反"同帧父未位于更早微步"一条：父 ``x`` 的 source_microstep
    与其节点实际更新微步一致（s4），但作为同帧父位于目标（s2）之后。
    断言错误信息命中该条件、且不再出现"源微步 != 节点微步"的伴生违规，
    从而证明检出的是目标时间契约而不是"某处报错"。
    """
    from ascend.causal import MechanismRegistry, MechanismSpec, ParentSpec

    base_nodes = slices.w0_registry()
    violated = MechanismSpec(
        mechanism_id="violate.same_frame_later_stage",
        output="mid1", equation="mid1",
        function=lambda x_prev, u: x_prev + u,
        parents=(
            ParentSpec(
                parent="x", argument="x_prev", lag=0,
                source_microstep=base_nodes.microstep_order[3],
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
        message = str(exc)
        hit_target = "未位于更早微步" in message
        # 单条件：不得同时出现"源微步 != 节点微步"伴生违规
        clean = "源微步" not in message
        passed = hit_target and clean
        return CheckResult(
            code="C2'", title="C2 判别力自检", passed=passed,
            detail=(
                f"违规声明被拒且命中单条件（同帧父位于更晚阶段）"
                if passed else
                f"检出信息不符：命中目标={hit_target} 单条件={clean}；"
                f"{message[:120]}"
            ),
        )
    return CheckResult(
        code="C2'", title="C2 判别力自检", passed=False,
        detail="同帧父位于更晚阶段的声明未被拒绝（判据无判别力）",
    )


# ── L3：世界动力学契约（#50）─────────────────────────────────

def check_l3_history_immutable() -> CheckResult:
    """L3：撤销不改写既有记录（时间线只追加；WC-6.2 / WC-7.4）。

    构造：帧 10 登记长期节点干预 → 求值帧 10（材质化记录）→ 帧 12 撤销
    → 重查帧 10 必须逐位相同、历史前缀不变、撤销后帧 12 不再命中。
    """
    from ascend.causal import InterventionTimeline, PlannedIntervention

    registry = slices.w1_registry()
    timeline = InterventionTimeline(registry, now=lambda: 10)
    timeline.plan(PlannedIntervention(
        target_space="node", target="med", instance=(),
        value=42.0, start_frame=10, stop_frame=None, source="l3",
    ))
    first = timeline.resolve_node("med", (), 10)
    before = timeline.history_plain()
    timeline.revoke("node", "med", at_frame=12)
    second = timeline.resolve_node("med", (), 10)
    after = timeline.history_plain()
    later = timeline.resolve_node("med", (), 12)

    checks = {
        "帧 10 记录已材质化": (
            first.record is not None and first.value == 42.0
        ),
        "撤销后同帧重查逐位相同": (
            second.value == first.value
            and second.record is not None
            and second.record.plain() == first.record.plain()
        ),
        "历史前缀未被改写（只追加）": (
            len(after) >= len(before)
            and after[: len(before)] == before
        ),
        "撤销后新帧不再命中": later.record is None,
    }
    failed = [name for name, ok in checks.items() if not ok]
    passed = not failed
    return CheckResult(
        code="L3-历史", title="时间线不可改写历史", passed=passed,
        detail=(
            "撤销前后帧 10 记录逐位一致、前缀只追加、帧 12 不再命中"
            if passed else f"未通过项: {failed}"
        ),
        input={"target": "med", "value": 42.0, "frame": 10, "revoke_at": 12},
        reference=first.record.plain() if first.record else None,
        engine=second.record.plain() if second.record else None,
    )


def check_l3_crn_pairing() -> CheckResult:
    """L3：CRN 配对——未干预分量的读出在配对臂中逐位相同（WC-7.3）。

    两个同种子引擎，B 对 chunk (0,0) 施加长期节点干预；
    (1,0) 的逐帧读出必须逐位相同，(0,0) 必须不同且等于干预值。
    """
    from ascend.causal import PlannedIntervention
    from ascend.space import ClimateZone, WeatherParams
    from ascend.time import WorldClock
    from ascend.weather import WeatherEngine
    from ascend.weather.mechanisms import INSTANT_TEMPERATURE
    from ascend.world_tree import WorldTree

    def build() -> tuple[WorldClock, WeatherEngine]:
        clock = WorldClock()
        engine = WeatherEngine(clock, seed=11, world_tree_arg=WorldTree())
        for cx, cy in ((0, 0), (1, 0)):
            engine.register_chunk(
                cx, cy, WeatherParams(20.0, 800.0, 12.0, 100.0, 60.0, 5.0),
                ClimateZone.TEMPERATE_FOREST, 15.0,
            )
        return clock, engine

    frames = range(1000, 1006)
    clock_a, engine_a = build()
    clock_b, engine_b = build()
    engine_b.intervention_table.plan(PlannedIntervention(
        target_space="node", target=INSTANT_TEMPERATURE, instance=(0, 0),
        value=30.0, start_frame=0, stop_frame=None, source="l3",
    ))

    def sample(engine: WeatherEngine, cx: int, cy: int) -> tuple:
        return tuple(engine.get_weather(cx, cy, t).temperature for t in frames)

    untouched_a = sample(engine_a, 1, 0)
    untouched_b = sample(engine_b, 1, 0)
    touched_a = sample(engine_a, 0, 0)
    touched_b = sample(engine_b, 0, 0)
    checks = {
        "未干预 chunk 逐位相同（CRN）": untouched_a == untouched_b,
        "干预 chunk 两臂不同": touched_a != touched_b,
        "干预值生效": all(abs(value - 30.0) < 1e-9 for value in touched_b),
    }
    failed = [name for name, ok in checks.items() if not ok]
    passed = not failed
    return CheckResult(
        code="L3-CRN", title="CRN 配对", passed=passed,
        detail=(
            f"未干预 chunk 6 帧逐位相同，干预 chunk 两臂互异且 = 30.0"
            if passed else f"未通过项: {failed}"
        ),
        input={"seed": 11, "frames": [frames[0], frames[-1]],
               "target": INSTANT_TEMPERATURE, "value": 30.0},
        reference=list(untouched_a),
        engine=list(untouched_b),
    )


def check_l3_state_closure() -> CheckResult:
    """L3：声明↔载荷闭合审计（WC-3.1 / WC-3.2）。

    每个声明为 state 的槽位必须有落盘归属（state.json 或 chunks.db），
    派生项必须给出重算法则，外部输入不得进入世界状态；任一漏映射即红。
    逐条解析真实载荷/数据库目标，并验证地形非默认状态落盘恢复一致。
    同判据内负例：漏归属映射、保留映射但缺落盘字段均必须报红。
    """
    from ascend.causal.state_schema import load_declaration
    from ascend.save.serializer import STATE_VERSION, collect_state
    from ascend.time import WorldClock

    declaration = load_declaration()
    from state_probe import audit_carriers, persisted_terrain
    state_slots = {slot.id for slot in declaration.slots}
    carriers = {
        "world.clock.tick": ("state.json", "clock.time"),
        "world.terrain.moisture": ("chunks.db", "chunk_tiles.tiles"),
        "world.terrain.snow": ("chunks.db", "chunk_tiles.tiles"),
        "world.terrain.ice": ("chunks.db", "chunk_tiles.tiles"),
        "world.terrain.integrated_through": (
            "chunks.db", "chunk_tiles.integrated_through"
        ),
    }

    class _Player:
        position = (0.0, 0.0)
        entity = None

    clock = WorldClock()
    clock.skip(37)
    payload = collect_state(clock, _Player(), None, 0)
    terrain, expected = persisted_terrain()
    expected["world.clock.tick"] = clock.time

    def audit(table: dict) -> list[str]:
        problems = audit_carriers(table, payload, terrain, expected)
        if set(table) != state_slots:
            problems.append(
                f"槽位↔归属不闭合: 缺={sorted(state_slots - set(table))} "
                f"多={sorted(set(table) - state_slots)}"
            )
        for slot in declaration.slots:
            if not slot.persist:
                problems.append(f"state 槽位未声明落盘: {slot.id}")
        for item in declaration.derived:
            if not item.recompute:
                problems.append(f"派生项缺重算法则: {item.id}")
        for item in declaration.external_inputs:
            if item.enters_world_state:
                problems.append(f"外部输入进入世界状态: {item.id}")
        for item in declaration.removed:
            if not item.reason:
                problems.append(f"removed 项缺原因: {item.id}")
        return problems

    base = audit(carriers)
    broken = audit({
        key: value for key, value in carriers.items()
        if key != "world.clock.tick"
    })
    payload_ok = (
        payload["state_version"] == STATE_VERSION
        and isinstance(payload["clock"]["time"], int)
    )
    checks = {
        "闭合无问题": not base,
        "载荷版本与时钟字段": payload_ok,
        "漏归属即报红（负例）": bool(broken),
        "映射保留但落盘字段缺失即报红": bool(audit_carriers(
            carriers, payload,
            {key: value for key, value in terrain.items()
             if key != "chunk_tiles.tiles"}, expected,
        )),
    }
    failed = [name for name, ok in checks.items() if not ok]
    passed = not failed
    return CheckResult(
        code="L3-状态", title="状态闭合审计", passed=passed,
        detail=(
            f"{len(state_slots)} 个 state 槽位全部有落盘归属；"
            f"{len(declaration.derived)} 个派生项有重算法则；"
            f"地形非默认状态落盘恢复一致；外部输入不入状态；"
            f"负例（漏映射/缺落盘字段）报红"
            if passed else f"未通过项: {failed}；{base[:3]}"
        ),
        input={
            "slots": sorted(state_slots),
            "carriers": {key: list(value) for key, value in carriers.items()},
        },
        reference={"problems": base},
        engine={"problems_broken": broken},
    )


ALL_CHECKS = (
    check_c0, check_c1, check_c2, check_c2_rejects_violation,
    check_w0, check_w1, check_w2, check_w3, check_w4, check_w5,
    check_w5_rejects_broken_observe,
    check_i0, check_i1,
    check_l3_history_immutable, check_l3_crn_pairing,
    check_l3_state_closure,
)
