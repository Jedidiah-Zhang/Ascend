"""验收测试 — 新核心的 W0/W1/W2 构造。"""

from __future__ import annotations

from ascend.world.evidence import run_acceptance


class TestAcceptance:
    def test_all_pass(self):
        results = run_acceptance()
        assert len(results) == 3
        failures = [
            f"{result.id}: {result.detail}"
            for result in results
            if not result.passed
        ]
        assert not failures, "; ".join(failures)

    def test_ids(self):
        assert [result.id for result in run_acceptance()] == [
            "W0-帧内顺序",
            "W1-节点干预与CRN",
            "W2-值干预时长",
        ]
