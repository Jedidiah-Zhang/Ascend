"""地图预览处理程序单元测试 — 创建世界调参的地形预览（Issue #8）。

覆盖 ascend/net/handlers/preview_handler.py。
"""

import pytest

from ascend.net.handlers.preview_handler import make_preview_handlers


@pytest.fixture()
def handlers():
    return make_preview_handlers()


def _req(request_type: str, payload: dict | None = None) -> dict:
    return {"type": "request", "request_type": request_type,
            "seq": 1, "payload": payload or {}}


class TestMapPreview:
    """地图预览请求。"""

    def test_handlers_registered(self, handlers):
        """注册 map_preview 处理器。"""
        assert "map_preview" in handlers

    def test_returns_preview_payload(self, handlers):
        """合法请求返回地形预览（海拔场 + 实测陆地占比）。

        采样分辨率 1000m：默认 100×60km → 100×60 网格（6000 格）。
        """
        resp = handlers["map_preview"](
            _req("map_preview", {"seed": 12345, "land_ratio": 0.55})
        )
        assert resp["type"] == "response"
        assert resp["request_type"] == "map_preview"
        payload = resp["payload"]
        assert payload["seed"] == 12345
        assert payload["land_ratio"] == 0.55
        assert payload["width"] == 100
        assert payload["height"] == 60
        assert len(payload["elevation"]) == 6000
        assert all(isinstance(v, int) for v in payload["elevation"])
        assert 0.0 < payload["land_percent"] < 1.0

    def test_deterministic_across_requests(self, handlers):
        """同参数两次请求结果一致（预览确定性）。"""
        req = _req("map_preview", {"seed": 7, "land_ratio": 0.4})
        r1 = handlers["map_preview"](req)["payload"]
        r2 = handlers["map_preview"](req)["payload"]
        assert r1 == r2

    def test_land_ratio_changes_shape(self, handlers):
        """不同 land_ratio 产出不同地形（占比贴合目标）。"""
        low = handlers["map_preview"](
            _req("map_preview", {"seed": 42, "land_ratio": 0.30})
        )["payload"]
        high = handlers["map_preview"](
            _req("map_preview", {"seed": 42, "land_ratio": 0.80})
        )["payload"]
        assert low["land_percent"] < high["land_percent"]
        assert low["elevation"] != high["elevation"]

    def test_seed_zero_rejected(self, handlers):
        """seed=0（随机占位）预览拒绝——调参必须显式指定种子。"""
        with pytest.raises(ValueError):
            handlers["map_preview"](_req("map_preview", {"seed": 0}))

    def test_land_ratio_out_of_range_rejected(self, handlers):
        """land_ratio 越界拒绝（[0, 1] 外 / 非有限值）。"""
        for ratio in (0.0, 1.5, -0.1, float("nan"), float("inf")):
            with pytest.raises(ValueError):
                handlers["map_preview"](
                    _req("map_preview", {"seed": 1, "land_ratio": ratio})
                )

    def test_size_scales_grid(self, handlers):
        """尺寸改变生成范围：采样分辨率固定 1000m，网格随尺寸缩放。"""
        small = handlers["map_preview"](
            _req("map_preview", {"seed": 42, "land_ratio": 0.5,
                                 "width_km": 60.0, "height_km": 36.0})
        )["payload"]
        large = handlers["map_preview"](
            _req("map_preview", {"seed": 42, "land_ratio": 0.5,
                                 "width_km": 150.0, "height_km": 90.0})
        )["payload"]
        assert (small["width"], small["height"]) == (60, 36)
        assert len(small["elevation"]) == 60 * 36
        assert (large["width"], large["height"]) == (150, 90)
        assert len(large["elevation"]) == 150 * 90
        assert large["width_km"] == 150.0
        assert large["height_km"] == 90.0

    def test_size_defaults_to_standard(self, handlers):
        """缺省尺寸回退生成器默认（100×60）。"""
        payload = handlers["map_preview"](
            _req("map_preview", {"seed": 5, "land_ratio": 0.55})
        )["payload"]
        assert payload["width"] == 100
        assert payload["height"] == 60

    def test_size_out_of_range_rejected(self, handlers):
        """尺寸越界拒绝（[20, 200] 外 / 非有限值）。"""
        for bad in (10.0, 250.0, 0.0, float("nan"), float("inf")):
            with pytest.raises(ValueError):
                handlers["map_preview"](
                    _req("map_preview", {"seed": 1, "land_ratio": 0.5,
                                         "width_km": bad})
                )

    def test_missing_seed_rejected(self, handlers):
        """缺少 seed 拒绝。"""
        with pytest.raises(ValueError):
            handlers["map_preview"](_req("map_preview", {}))
