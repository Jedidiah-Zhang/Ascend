"""研究侧审查回归：python -m unittest discover -s research/acceptance。"""

import contextlib
import inspect
import io
import unittest
from unittest.mock import patch

import run_acceptance as runner
from state_probe import audit_carriers


class ReviewRegressions(unittest.TestCase):
    def test_mutation_restores_static_descriptor(self):
        from ascend.causal.intervention_engine import InterventionFrameExecutor

        original = inspect.getattr_static(InterventionFrameExecutor, "_neighbours")
        for _ in range(2):
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(runner.run_mutation(), 0)
            self.assertIs(
                inspect.getattr_static(InterventionFrameExecutor, "_neighbours"),
                original,
            )
            self.assertTrue(runner.checks.check_w3().passed)

    def test_state_round_trip(self):
        self.assertTrue(runner.checks.check_l3_state_closure().passed)

    def test_mutation_restores_descriptor_after_check_exception(self):
        from ascend.causal.intervention_engine import InterventionFrameExecutor

        original = inspect.getattr_static(InterventionFrameExecutor, "_neighbours")

        def broken_check():
            raise RuntimeError("模拟判据异常")

        mutations = (("异常路径", runner._mutation_boundary_wrap, broken_check),)
        with patch.object(runner, "MUTATIONS", mutations):
            with contextlib.redirect_stdout(io.StringIO()):
                runner.run_mutation()
        self.assertIs(
            inspect.getattr_static(InterventionFrameExecutor, "_neighbours"), original,
        )
        self.assertTrue(runner.checks.check_w3().passed)

    def test_state_serialization_loss_is_rejected(self):
        from ascend.space import TileGrid

        # 数据库列与归属表完全不变，仅将真实写盘状态段变为空白。
        blank = TileGrid().to_bytes()
        with patch.object(TileGrid, "to_bytes", return_value=blank):
            result = runner.checks.check_l3_state_closure()
        self.assertFalse(result.passed)
        self.assertIn("恢复值不一致", result.detail)

    def test_existing_mapping_requires_real_target_and_value(self):
        slot = "world.terrain.snow"
        table = {slot: ("chunks.db", "chunk_tiles.tiles")}
        expected = {slot: [23, 24]}
        for terrain in ({}, {"chunk_tiles.tiles": {}},
                        {"chunk_tiles.tiles": {slot: [0, 0]}}):
            with self.subTest(terrain=terrain):
                self.assertTrue(audit_carriers(table, {}, terrain, expected))
        self.assertFalse(audit_carriers(
            table, {}, {"chunk_tiles.tiles": {slot: [23, 24]}}, expected,
        ))
        self.assertTrue(audit_carriers(
            {slot: ("chunks.db", "chunk_tiles.typo")}, {},
            {"chunk_tiles.tiles": {slot: [23, 24]}}, expected,
        ))


if __name__ == "__main__":
    unittest.main()
