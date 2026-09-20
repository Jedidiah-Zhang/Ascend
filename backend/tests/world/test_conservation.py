"""守恒练兵切片测试— 流量 / 守恒不变量 / 多分辨率。

- 声明自洽：不变量常量与槽位初值一致（防漂移）；
- 守恒：逐帧「地块 + 流域 == 声明总量」（跨槽位 reject 不变量）；
- 多分辨率：restrict（地块读所属流域）与 prolong（流域聚合子地块流出）；
- 流量界：min(rate, 流域库存, 容量余量)，稳态灌满地块、抽干流域；
- 负例：破坏守恒 → 帧拒绝且状态回滚；界内违规（record）→ 提交并留痕。
"""

from __future__ import annotations

import pytest

from ascend.world import Schedule, WorldProcess, WorldSpec, compile_world
from ascend.world.modules import conservation
from ascend.world.runtime import FrameFailure

_PHASES = ("flow", "apply", "drain")


def _program():
    return compile_world(
        WorldSpec(
            modules=(conservation.MODULE,),
            schedule=Schedule(phases=_PHASES),
        )
    )


def _total(process: WorldProcess) -> int:
    return (
        sum(process.committed("water.plot.stock").values())
        + sum(process.committed("water.basin.stock").values())
    )


class TestDeclarationSelfConsistency:
    def test_declared_total_matches_initial_values(self):
        """不变量常量与槽位初值一致（防声明漂移）。"""
        program = _program()
        basins = program.slots["water.basin.stock"]
        plots = program.slots["water.plot.stock"]
        basin_count = program.instances["lattice.basin"].size[0]
        plot_count = program.instances["lattice.plot"].size[0]
        initial = basin_count * basins.initial + plot_count * plots.initial
        assert initial == conservation.TOTAL_WATER

    def test_module_is_declaration_only(self):
        """练兵切片只依赖声明面（无框架钩子）。"""
        assert conservation.MODULE.id == "drill.conservation"
        assert conservation.MODULE.mechanisms
        assert conservation.MODULE.invariants


class TestConservation:
    def test_total_holds_every_frame(self):
        process = WorldProcess(_program())
        assert _total(process) == conservation.TOTAL_WATER
        for _ in range(12):
            process.step()
            assert _total(process) == conservation.TOTAL_WATER

    def test_steady_state_fills_plots_and_drains_basins(self):
        process = WorldProcess(_program())
        for _ in range(12):
            process.step()
        assert process.committed("water.plot.stock").values() == (10, 10, 10, 10)
        assert process.committed("water.basin.stock").values() == (0, 0)
        assert process.committed("water.plot.inflow").values() == (0, 0, 0, 0)

    def test_flow_bounded_by_rate_then_room(self):
        process = WorldProcess(_program())
        process.step()
        assert process.committed("water.plot.inflow").values() == (3, 3, 3, 3)
        for _ in range(3):
            process.step()
        # 第 4 帧：余量仅 1（容量 10 − 库存 9）→ 入流被余量钳制
        assert process.committed("water.plot.inflow").values() == (1, 1, 1, 1)
        process.step()
        # 地块已满：余量为 0 → 入流归零
        assert process.committed("water.plot.inflow").values() == (0, 0, 0, 0)

    def test_violation_rejected_and_rolled_back(self):
        process = WorldProcess(_program())
        process.step()
        before = process.committed("water.plot.stock").values()
        with pytest.raises(FrameFailure, match="不守恒"):
            process.step(interventions={"water.plot.stock": {(0,): 1}})
        assert process.tick == 1
        assert process.committed("water.plot.stock").values() == before

    def test_bounds_violation_recorded(self):
        """界内违规（record）不阻断提交：守恒仍成立时只留痕。

        干预地块 0 为 -1（跳过其求值）并补偿流域 0 为 18（跳过其扣减），
        帧末总量仍为 40：守恒不变量通过，负值由 record 不变量留痕。
        """
        process = WorldProcess(_program())
        result = process.step(interventions={
            "water.plot.stock": {(0,): -1},
            "water.basin.stock": {(0,): 18},
        })
        assert any("负值" in message for message in result.violations)
        assert _total(process) == conservation.TOTAL_WATER


class TestMultiResolution:
    def _asymmetric_world(self, basins: tuple[int, int]):
        """经守恒的干预构造非对称初始态（rate=0 帧，仅搬水不流动）。"""
        process = WorldProcess(_program())
        process.step(
            parameters={"water.flow.rate": 0},
            interventions={
                "water.basin.stock": {(0,): basins[0], (1,): basins[1]},
            },
        )
        assert _total(process) == conservation.TOTAL_WATER
        return process

    def test_restrict_maps_plot_to_its_basin(self):
        """地块只读所属流域（x // ratio），不是全流域求和。"""
        process = self._asymmetric_world((0, 40))
        process.step()
        inflow = process.committed("water.plot.inflow").values()
        assert inflow == (0, 0, 3, 3)

    def test_prolong_aggregates_only_children(self):
        """流域只扣减自己的子地块流出（sum over children）。"""
        process = self._asymmetric_world((40, 0))
        process.step()
        # basin[0]：2 个子地块各流出 3 → 40 - 6 = 34；basin[1]：0 - 0 = 0
        assert process.committed("water.basin.stock").values() == (34, 0)
        assert _total(process) == conservation.TOTAL_WATER
