"""动量演练模块测试 — 按接入协议接入（lag≥2）。

- 递推：``v_t = min(cap, v1 + acc·(v1 − v2))``（离散加速度）；
- 历史预热：首帧即可读 lag=2（"自始如此"稳态假设）；
- 快照：携带 lag=2 历史（WC-7.5 检查点充分性）；去掉历史即轨迹分叉
  （证明历史是承重的，不是装饰）；
- 上界：clamp 到 cap（不变量 reject 兜底）。
"""

from __future__ import annotations

import pytest

from olam import Schedule, WorldProcess, WorldSpec, compile_world
from olam.modules import momentum

_PHASES = ("step",)


def _program():
    return compile_world(
        WorldSpec(
            modules=(momentum.MODULE,),
            schedule=Schedule(phases=_PHASES),
        )
    )


class TestRecurrence:
    def test_flat_steady_state(self):
        process = WorldProcess(_program())
        for _ in range(4):
            process.step()
        assert process.committed("momentum.value") == momentum.VALUE_INITIAL

    def test_kick_grows_by_acceleration(self):
        process = WorldProcess(_program())
        process.step(interventions={"momentum.value": 5})
        values = [process.committed("momentum.value")]
        for _ in range(3):
            process.step()
            values.append(process.committed("momentum.value"))
        # acc=1：kick 后 (5, 9, 13, 17)
        assert values == [5, 9, 13, 17]

    def test_acceleration_parameter(self):
        process = WorldProcess(_program())
        process.step(
            parameters={"momentum.acceleration": 2},
            interventions={"momentum.value": 5},
        )
        process.step(parameters={"momentum.acceleration": 2})
        assert process.committed("momentum.value") == 13

    def test_cap_clamps_and_holds(self):
        process = WorldProcess(_program())
        process.step(
            parameters={"momentum.acceleration": 10},
            interventions={"momentum.value": 50},
        )
        for _ in range(4):
            process.step(parameters={"momentum.acceleration": 10})
        assert process.committed("momentum.value") == momentum.VALUE_CAP


class TestCheckpointSufficiency:
    def test_snapshot_round_trip_with_lag2(self):
        process = WorldProcess(_program(), seed=3)
        process.step(interventions={"momentum.value": 5})
        for _ in range(3):
            process.step()
        snapshot = process.snapshot()

        restored = WorldProcess.restore(_program(), snapshot, seed=3)
        for _ in range(3):
            process.step()
            restored.step()
        assert restored.committed("momentum.value") == \
            process.committed("momentum.value")

    def test_history_is_load_bearing(self):
        """去掉快照历史：恢复后轨迹分叉（历史必须随档，WC-7.5）。"""
        process = WorldProcess(_program(), seed=3)
        process.step(interventions={"momentum.value": 5})
        process.step()
        snapshot = process.snapshot()
        assert "momentum.value" in snapshot["history"]

        stripped = dict(snapshot)
        stripped["history"] = {}
        tampered = WorldProcess.restore(_program(), stripped, seed=3)
        process.step()
        tampered.step()
        assert tampered.committed("momentum.value") != \
            process.committed("momentum.value")

    def test_snapshot_binds_identity(self):
        process = WorldProcess(_program(), seed=3)
        snapshot = process.snapshot()
        with pytest.raises(ValueError, match="世界身份"):
            WorldProcess.restore(_program(), snapshot, seed=4)
