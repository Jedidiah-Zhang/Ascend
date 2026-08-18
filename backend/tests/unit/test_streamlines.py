"""流线模块测试 — C 扩展退化路径 + 小网格冒烟。

退化路径锁定（P2-13）：w/h=1 时 _bilinear 的 w-2=-1 曾导致越界读，
现退化为单点采样（返回 arr[0]），trace 安全返回（源头点仍被记录，
与文档"返回点数包含源头"一致）。
"""

from array import array

import pytest

from ascend.space.streamlines import _trace_downstream_c


class TestTraceDegenerateGrids:
    def test_single_cell_no_crash(self):
        """1×1 网格：_bilinear 退化路径（修复前 w-2=-1 越界读）。"""
        dem = array('d', [5.0])
        smooth = array('d', [5.0])
        flow = array('d', [1.0])
        dist = array('d', [0.0])
        pts = _trace_downstream_c(0, dem, smooth, flow, dist, 1, 1)
        assert pts == [(0.0, 0.0)]

    def test_single_row_no_crash(self):
        """1×h 网格（h>1）：w<2 退化，不越界。"""
        h = 4
        dem = array('d', [0.0] * h)
        smooth = array('d', [0.0] * h)
        flow = array('d', [1.0] * h)
        dist = array('d', [0.0] * h)
        pts = _trace_downstream_c(0, dem, smooth, flow, dist, 1, h)
        assert pts == [(0.0, 0.0)]

    def test_single_column_no_crash(self):
        """w×1 网格（w>1）：h<2 退化，不越界。"""
        w = 4
        dem = array('d', [0.0] * w)
        smooth = array('d', [0.0] * w)
        flow = array('d', [1.0] * w)
        dist = array('d', [0.0] * w)
        pts = _trace_downstream_c(0, dem, smooth, flow, dist, w, 1)
        assert pts == [(0.0, 0.0)]


class TestTraceSmallGrid:
    def test_slope_grid_traces_downhill(self):
        """斜坡 DEM + 同向 dist：Tier 1 生效，trace 沿下坡返回点列。"""
        w = h = 3
        dem = array('d', [10.0, 10.0, 10.0,
                          5.0, 5.0, 5.0,
                          0.0, 0.0, 0.0])
        dist = array('d', [10.0, 10.0, 10.0,
                           5.0, 5.0, 5.0,
                           0.0, 0.0, 0.0])
        flow = array('d', [1.0] * (w * h))
        pts = _trace_downstream_c(1, dem, dem, flow, dist, w, h)
        # 起点 = 源头；沿 x=1 垂直下落（dist 下降方向与 dem 同向）
        assert pts[0] == pytest.approx((1.0, 0.0))
        assert len(pts) >= 3
        for x, y in pts:
            assert x == pytest.approx(1.0)
            assert -0.1 <= y <= 3.0  # 末步可越过底边（写入点按截断索引判界）

    def test_flat_grid_source_only(self):
        """三场全平无梯度：方向场全失效 → 仅源头点（安全终止）。"""
        w = h = 3
        dem = array('d', [1.0] * (w * h))
        flow = array('d', [1.0] * (w * h))
        dist = array('d', [0.0] * (w * h))
        pts = _trace_downstream_c(4, dem, dem, flow, dist, w, h)
        assert pts == [(1.0, 1.0)]