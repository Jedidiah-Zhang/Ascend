"""FrameHistory 单元测试（issue #49：lag 历史窗口统一语义）。"""

from __future__ import annotations

import pytest

from ascend.causal.frame_history import FrameHistory


class TestFrameHistory:
    def test_invalid_max_lag_rejected(self):
        with pytest.raises(ValueError, match="max_lag"):
            FrameHistory(1, {"x": 0.0}, max_lag=-1)
        with pytest.raises(ValueError, match="max_lag"):
            FrameHistory(1, {"x": 0.0}, max_lag=True)

    def test_pre_history_reads_initial_steady_state(self):
        """首帧之前按 initial 稳态取值（lag=1 语义推广到任意 lag）。"""
        history = FrameHistory(1, {"x": 7.0}, max_lag=3)
        assert history.lookup("x", 1, 1) == 7.0   # 帧 0 = initial
        assert history.lookup("x", 3, 1) == 7.0   # 帧 −2 → initial 稳态
        history.commit(1, {"x": 1.0})
        assert history.lookup("x", 1, 2) == 1.0   # 帧 1
        assert history.lookup("x", 2, 2) == 7.0   # 帧 0 = initial
        assert history.lookup("x", 3, 3) == 7.0   # 帧 0 = initial
        assert history.lookup("x", 3, 4) == 1.0   # 帧 1

    def test_arbitrary_lag_resolves_committed_frames(self):
        history = FrameHistory(10, {"x": 0.0}, max_lag=3)
        for frame, value in ((10, 1.0), (11, 2.0), (12, 3.0)):
            history.commit(frame, {"x": value})
        assert history.lookup("x", 1, 13) == 3.0
        assert history.lookup("x", 2, 13) == 2.0
        assert history.lookup("x", 3, 13) == 1.0

    def test_window_trimming_keeps_max_lag(self):
        history = FrameHistory(1, {"x": 0.0}, max_lag=2)
        for frame in range(1, 8):
            history.commit(frame, {"x": float(frame)})
        # 帧 8 读 lag=2 → 帧 6 仍在窗口；窗口保留 max_lag+1 个状态
        assert history.lookup("x", 2, 8) == 6.0
        assert history.earliest_frame == 5
        with pytest.raises(ValueError, match="超出声明窗口"):
            history.lookup("x", 3, 8)

    def test_missing_node_fail_closed(self):
        history = FrameHistory(1, {"x": 0.0}, max_lag=1)
        with pytest.raises(KeyError, match="初始快照缺少父值"):
            history.lookup("y", 1, 1)
        history.commit(1, {"x": 1.0})
        with pytest.raises(KeyError, match="历史帧缺少父值"):
            history.lookup("y", 1, 2)

    def test_lag_zero_rejected(self):
        history = FrameHistory(1, {"x": 0.0}, max_lag=1)
        with pytest.raises(ValueError, match="只接受 lag≥1"):
            history.lookup("x", 0, 1)
