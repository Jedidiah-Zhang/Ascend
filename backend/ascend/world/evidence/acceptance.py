"""P0 验收 — W0/W1/W2 在新核心的等价构造（世界架构 01 §P0）。

- **W0 帧内顺序**：三阶段链；机制必须读前序阶段本帧写入，而不是旧帧缓存；
- **W1 节点干预与 CRN**：同一单位（种子）下干预臂与基线臂的未干预地址
  逐位一致、目标槽位入边断开、后代继续演化；
- **W2 值干预时长语义**：持续干预与单帧干预轨迹不同（WC-6.2；
  机制替换按 WC-1.3 属换世界，不在此验收）。

验收对象是框架自身（编译/运行时/研究层），只使用玩具模块。
"""

from __future__ import annotations

from dataclasses import dataclass

__all__ = ["AcceptanceResult", "run_acceptance"]

_PHASES = ("stage1", "stage2", "stage3")


@dataclass(frozen=True, slots=True)
class AcceptanceResult:
    """单项验收结果。"""

    id: str
    passed: bool
    detail: str


def run_acceptance() -> tuple[AcceptanceResult, ...]:
    """运行全部 P0 验收（不抛错：失败进结果明细）。"""
    return (_w0(), _w1(), _w2())


def _w0() -> AcceptanceResult:
    from ascend.world.compile import compile_world
    from ascend.world.meta.declarations import Schedule, WorldSpec
    from ascend.world.modules import toy
    from ascend.world.runtime import WorldProcess

    program = compile_world(
        WorldSpec(
            modules=(toy.W0,),
            schedule=Schedule(phases=_PHASES),
        )
    )
    process = WorldProcess(program)
    process.step()
    first = (
        process.committed("toy.mid1"),
        process.committed("toy.mid2"),
        process.committed("toy.x"),
    )
    process.step()
    second = (
        process.committed("toy.mid1"),
        process.committed("toy.mid2"),
        process.committed("toy.x"),
    )
    passed = first == (3, 6, 6) and second == (8, 16, 16)
    return AcceptanceResult(
        "W0-帧内顺序",
        passed,
        f"帧1 {first}（期望 (3, 6, 6)）/ 帧2 {second}（期望 (8, 16, 16)）",
    )


def _w1() -> AcceptanceResult:
    from ascend.world.compile import compile_world
    from ascend.world.meta.declarations import Schedule, WorldSpec
    from ascend.world.modules import toy
    from ascend.world.runtime import WorldProcess

    program = compile_world(
        WorldSpec(
            modules=(toy.W1,),
            schedule=Schedule(phases=_PHASES),
            seed=7,
        )
    )
    baseline = WorldProcess(program, seed=7)
    intervened = WorldProcess(program, seed=7)
    ok = True
    details: list[str] = []
    for frame in (1, 2, 3):
        baseline.step()
        replacements = {"toy.med": 100} if frame <= 2 else None
        intervened.step(interventions=replacements)
        base_values = _snapshot_values(baseline)
        arm_values = _snapshot_values(intervened)
        # 未干预的随机地址逐位一致（CRN）
        if base_values["toy.rt"] != arm_values["toy.rt"]:
            ok = False
            details.append(f"帧{frame}: rt 漂移")
        if base_values["toy.ind"] != arm_values["toy.ind"]:
            ok = False
            details.append(f"帧{frame}: ind 漂移")
        # 干预帧：med 被替换，out 用同地址继续演化
        if frame <= 2:
            if arm_values["toy.med"] != 100:
                ok = False
                details.append(f"帧{frame}: med 未被替换")
            expected_out = 100 + (base_values["toy.out"] - base_values["toy.med"])
            if arm_values["toy.out"] != expected_out:
                ok = False
                details.append(
                    f"帧{frame}: out {arm_values['toy.out']} != {expected_out}"
                )
        else:
            # 干预结束后 med 自行恢复（入边重新生效）
            if arm_values["toy.med"] != base_values["toy.med"]:
                ok = False
                details.append(f"帧{frame}: 干预结束后 med 未恢复")
    return AcceptanceResult(
        "W1-节点干预与CRN",
        ok,
        "; ".join(details) if details else "两臂未干预地址一致、后代按干预值演化",
    )


def _w2() -> AcceptanceResult:
    from ascend.world.compile import compile_world
    from ascend.world.meta.declarations import Schedule, WorldSpec
    from ascend.world.modules import toy
    from ascend.world.runtime import WorldProcess

    program = compile_world(
        WorldSpec(
            modules=(toy.W1,),
            schedule=Schedule(phases=_PHASES),
            seed=11,
        )
    )
    single = WorldProcess(program, seed=11)
    windowed = WorldProcess(program, seed=11)
    single_med: list[object] = []
    windowed_med: list[object] = []
    for frame in (1, 2, 3):
        single.step(interventions={"toy.med": 100} if frame == 1 else None)
        windowed.step(
            interventions={"toy.med": 100} if frame <= 2 else None
        )
        single_med.append(single.committed("toy.med"))
        windowed_med.append(windowed.committed("toy.med"))
    passed = single_med != windowed_med and windowed_med[1] == 100
    return AcceptanceResult(
        "W2-值干预时长",
        passed,
        f"单帧 {single_med} / 持续 {windowed_med}",
    )


def _snapshot_values(process: object) -> dict[str, object]:
    return {
        slot: process.committed(slot)
        for slot in ("toy.rt", "toy.med", "toy.out", "toy.ind")
    }
