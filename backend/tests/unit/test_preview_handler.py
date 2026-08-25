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
        seed 为协议层 hex 字符串，响应原样回传（小写归一）。
        """
        resp = handlers["map_preview"](
            _req("map_preview", {"seed": "2a", "land_ratio": 0.55})
        )
        assert resp["type"] == "response"
        assert resp["request_type"] == "map_preview"
        payload = resp["payload"]
        assert payload["seed"] == "2a"
        assert payload["land_ratio"] == 0.55
        assert payload["width"] == 100
        assert payload["height"] == 60
        assert len(payload["elevation"]) == 6000
        assert all(isinstance(v, int) for v in payload["elevation"])
        assert 0.0 < payload["land_percent"] < 1.0

    def test_seed_hex_case_insensitive(self, handlers):
        """hex 大写输入归一为小写回传。"""
        resp = handlers["map_preview"](
            _req("map_preview", {"seed": "2A", "land_ratio": 0.55})
        )
        assert resp["payload"]["seed"] == "2a"

    def test_seed_256bit_round_trip(self, handlers):
        """256-bit 种子（64 hex 字符）请求与回传一致，预览确定性。"""
        big = "a3f9" + "0" * 60
        req = _req("map_preview", {"seed": big, "land_ratio": 0.55})
        r1 = handlers["map_preview"](req)["payload"]
        r2 = handlers["map_preview"](req)["payload"]
        assert r1["seed"] == big
        assert r1["elevation"] == r2["elevation"]

    def test_seed_zero_or_missing_randomizes(self, handlers):
        """seed="" / "0" / 缺省 = 随机占位：预览时定案并回传 hex 种子。

        种子唯一随机源 = 后端（命运织机契约）；回传种子再请求
        须产出同一地形（定案确定性）。
        """
        for payload in ({"seed": ""}, {"seed": "0"}, {}):
            resp = handlers["map_preview"](
                _req("map_preview", {**payload, "land_ratio": 0.55})
            )
            seed = resp["payload"]["seed"]
            assert isinstance(seed, str) and len(seed) > 0
            try:
                int(seed, 16)
            except ValueError:
                raise AssertionError(f"回传 seed 非法 hex: {seed!r}")
            again = handlers["map_preview"](
                _req("map_preview", {"seed": seed, "land_ratio": 0.55})
            )["payload"]
            assert again["elevation"] == resp["payload"]["elevation"]

    def test_seed_invalid_hex_rejected(self, handlers):
        """非法 hex 拒绝（含 0x 前缀、非 hex 字符）。"""
        for bad in ("zz", "0x2a", "g", "2a!", "-1", "1.5"):
            with pytest.raises(ValueError):
                handlers["map_preview"](
                    _req("map_preview", {"seed": bad, "land_ratio": 0.55})
                )

    def test_seed_out_of_range_rejected(self, handlers):
        """seed 越界（> 2^256-1）拒绝。"""
        too_big = hex(2**256)[2:]
        with pytest.raises(ValueError):
            handlers["map_preview"](
                _req("map_preview", {"seed": too_big, "land_ratio": 0.55})
            )

    def test_deterministic_across_requests(self, handlers):
        """同参数两次请求结果一致（预览确定性）。"""
        req = _req("map_preview", {"seed": "7", "land_ratio": 0.4})
        r1 = handlers["map_preview"](req)["payload"]
        r2 = handlers["map_preview"](req)["payload"]
        assert r1 == r2

    def test_land_ratio_changes_shape(self, handlers):
        """不同 land_ratio 产出不同地形（占比贴合目标）。"""
        low = handlers["map_preview"](
            _req("map_preview", {"seed": "2a", "land_ratio": 0.30})
        )["payload"]
        high = handlers["map_preview"](
            _req("map_preview", {"seed": "2a", "land_ratio": 0.80})
        )["payload"]
        assert low["land_percent"] < high["land_percent"]
        assert low["elevation"] != high["elevation"]

    def test_land_ratio_out_of_range_rejected(self, handlers):
        """land_ratio 越界拒绝（[0, 1] 外 / 非有限值）。"""
        for ratio in (0.0, 1.5, -0.1, float("nan"), float("inf")):
            with pytest.raises(ValueError):
                handlers["map_preview"](
                    _req("map_preview", {"seed": "1", "land_ratio": ratio})
                )

    def test_size_scales_grid(self, handlers):
        """尺寸改变生成范围：采样分辨率固定 1000m，网格随尺寸缩放。"""
        small = handlers["map_preview"](
            _req("map_preview", {"seed": "2a", "land_ratio": 0.5,
                                 "width_km": 60.0, "height_km": 36.0})
        )["payload"]
        large = handlers["map_preview"](
            _req("map_preview", {"seed": "2a", "land_ratio": 0.5,
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
            _req("map_preview", {"seed": "5", "land_ratio": 0.55})
        )["payload"]
        assert payload["width"] == 100
        assert payload["height"] == 60

    def test_size_out_of_range_rejected(self, handlers):
        """尺寸越界拒绝（[20, 200] 外 / 非有限值）。"""
        for bad in (10.0, 250.0, 0.0, float("nan"), float("inf")):
            with pytest.raises(ValueError):
                handlers["map_preview"](
                    _req("map_preview", {"seed": "1", "land_ratio": 0.5,
                                         "width_km": bad})
                )

    # ── 气候图层（温度/降雨/气候带）────────────────────────

    def test_layers_omitted_by_default(self, handlers):
        """缺省不计算气候图层（向后兼容：旧客户端仅海拔）。"""
        payload = handlers["map_preview"](
            _req("map_preview", {"seed": "3039", "land_ratio": 0.55})
        )["payload"]
        for key in ("temperature", "rainfall", "climate"):
            assert key not in payload

    def test_layers_return_extra_fields(self, handlers):
        """请求 layers 时响应携带对应气候图层（与海拔同网格）。"""
        payload = handlers["map_preview"](_req(
            "map_preview",
            {"seed": "3039", "land_ratio": 0.55,
             "layers": ["temp", "rain", "climate"]},
        ))["payload"]
        n = payload["width"] * payload["height"]
        assert len(payload["temperature"]) == n
        assert len(payload["rainfall"]) == n
        assert len(payload["climate"]) == n
        assert all(isinstance(v, int) for v in payload["temperature"])
        assert all(isinstance(v, int) for v in payload["rainfall"])
        assert all(isinstance(v, int) for v in payload["climate"])

    def test_layers_partial_selection(self, handlers):
        """只请求部分图层：仅返回被请求的字段。"""
        payload = handlers["map_preview"](_req(
            "map_preview",
            {"seed": "3039", "land_ratio": 0.55, "layers": ["climate"]},
        ))["payload"]
        assert "climate" in payload
        assert "temperature" not in payload
        assert "rainfall" not in payload

    def test_layers_climate_zone_ints(self, handlers):
        """气候带为 0-7 档位（与 ClimateZone 定义一致），陆地覆盖全部 8 档。"""
        payload = handlers["map_preview"](_req(
            "map_preview",
            {"seed": "3039", "land_ratio": 0.55, "layers": ["climate"]},
        ))["payload"]
        zones = set(payload["climate"])
        assert zones <= set(range(8))
        assert zones == set(range(8)), "校准+注入应保证 8 档气候覆盖"

    def test_layers_deterministic(self, handlers):
        """同参数两次请求的气候图层一致。"""
        req = _req("map_preview",
                   {"seed": "7", "land_ratio": 0.4,
                    "layers": ["temp", "rain", "climate"]})
        r1 = handlers["map_preview"](req)["payload"]
        r2 = handlers["map_preview"](req)["payload"]
        assert r1["temperature"] == r2["temperature"]
        assert r1["rainfall"] == r2["rainfall"]
        assert r1["climate"] == r2["climate"]

    def test_layers_seed_sensitive(self, handlers):
        """不同种子气候图层不同（温度梯度方向由 seed 决定）。"""
        def _climate_of(seed: int) -> list:
            return handlers["map_preview"](_req(
                "map_preview",
                {"seed": hex(seed)[2:], "land_ratio": 0.55,
                 "layers": ["temp", "rain", "climate"]},
            ))["payload"]["climate"]

        assert _climate_of(1) != _climate_of(2)

    def test_layers_size_scales_grid(self, handlers):
        """尺寸档位下气候图层随网格缩放（与海拔同尺寸）。"""
        payload = handlers["map_preview"](_req(
            "map_preview",
            {"seed": "2a", "land_ratio": 0.5,
             "width_km": 150.0, "height_km": 90.0,
             "layers": ["temp", "rain", "climate"]},
        ))["payload"]
        assert len(payload["temperature"]) == 150 * 90

    def test_layers_invalid_rejected(self, handlers):
        """非法图层名拒绝。"""
        with pytest.raises(ValueError):
            handlers["map_preview"](_req(
                "map_preview",
                {"seed": "1", "land_ratio": 0.5, "layers": ["humidity"]},
            ))

    def test_layers_non_list_rejected(self, handlers):
        """layers 非数组（null/字符串）拒绝为参数错误而非内部异常。"""
        for bad in (None, "temp", 3):
            with pytest.raises(ValueError):
                handlers["map_preview"](_req(
                    "map_preview",
                    {"seed": "1", "land_ratio": 0.5, "layers": bad},
                ))

    def test_layers_empty_list_is_elevation_only(self, handlers):
        """空 layers 列表 = 仅海拔（合法、向后兼容）。"""
        payload = handlers["map_preview"](_req(
            "map_preview",
            {"seed": "3039", "land_ratio": 0.55, "layers": []},
        ))["payload"]
        assert "temperature" not in payload
