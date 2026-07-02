"""地图数据请求处理程序单元测试。

使用 WorldGenerator(seed=42) 生成真实 ChunkData，
验证 make_map_handlers 创建的 get_chunks 处理函数行为。
"""

import pytest
from ascend.space import WorldGenerator, BiomeType, ClimateZone
from ascend.net.handlers.map_handler import make_map_handlers


class TestMapHandlers:
    """地图处理程序测试。"""

    # ── 固件 ───────────────────────────────────────────────────────────

    @pytest.fixture
    def gen(self):
        """seed=42 的 WorldGenerator 固件。"""
        return WorldGenerator(seed=42)

    @pytest.fixture
    def handlers(self, gen):
        """由 make_map_handlers 创建的处理器字典。"""
        return make_map_handlers(gen)

    # ── T5: get_chunks 返回正确的块数据 ────────────────────────────────

    def test_T5_get_chunks_returns_valid_data(self, handlers):
        """get_chunks 返回正确的块数据（biome/climate/passable）。

        Arrange:
            handlers 字典包含 "get_chunks" 处理器。
        Act:
            发送包含 3 个块坐标的请求。
        Assert:
            返回的 payload 包含 chunks 列表，每个条目包含
            cx/cy/biome/climate/passable 字段。
        """
        handle = handlers["get_chunks"]

        msg = {
            "type": "request",
            "request_type": "get_chunks",
            "seq": 1,
            "payload": {
                "chunks": [[0, 0], [1, 0], [0, 1]],
            },
        }

        response = handle(msg)

        # 验证响应结构
        assert response["type"] == "response"
        assert response["request_type"] == "get_chunks"
        assert "payload" in response

        chunks = response["payload"]["chunks"]
        assert len(chunks) == 3

        for entry in chunks:
            # 必需字段
            assert "cx" in entry
            assert "cy" in entry
            assert "biome" in entry
            assert "climate" in entry
            assert "passable" in entry

            # 字段类型
            assert isinstance(entry["cx"], int)
            assert isinstance(entry["cy"], int)
            assert isinstance(entry["biome"], int)
            assert isinstance(entry["climate"], int)
            assert isinstance(entry["passable"], bool)

        # 验证坐标正确
        assert chunks[0]["cx"] == 0
        assert chunks[0]["cy"] == 0
        assert chunks[1]["cx"] == 1
        assert chunks[1]["cy"] == 0
        assert chunks[2]["cx"] == 0
        assert chunks[2]["cy"] == 1

    # ── T6: get_chunks 空请求返回空 chunks 列表 ──────────────────────

    def test_T6_get_chunks_empty_request(self, handlers):
        """空请求返回空 chunks 列表。

        Arrange:
            payload 的 chunks 字段为空列表。
        Act:
            调用 handle_get_chunks。
        Assert:
            返回 payload.chunks 为空列表。
        """
        handle = handlers["get_chunks"]

        msg = {
            "type": "request",
            "request_type": "get_chunks",
            "seq": 2,
            "payload": {"chunks": []},
        }

        response = handle(msg)

        assert response["payload"]["chunks"] == []

    # ── T7: force_fields=true 包含完整气象数据 ────────────────────────

    def test_T7_get_chunks_force_fields(self, handlers):
        """force_fields=true 包含 altitude/temperature/rainfall。

        Arrange:
            payload 包含 force_fields: true。
        Act:
            调用 handle_get_chunks。
        Assert:
            每个块条目包含 altitude/temperature/rainfall 字段。
        """
        handle = handlers["get_chunks"]

        msg = {
            "type": "request",
            "request_type": "get_chunks",
            "seq": 3,
            "payload": {
                "chunks": [[5, -3]],
                "force_fields": True,
            },
        }

        response = handle(msg)
        chunks = response["payload"]["chunks"]
        assert len(chunks) == 1

        entry = chunks[0]
        assert "altitude" in entry
        assert "temperature" in entry
        assert "rainfall" in entry

        # 值应在合理范围内
        assert isinstance(entry["altitude"], float)
        assert isinstance(entry["temperature"], float)
        assert isinstance(entry["rainfall"], float)
        assert -5000.0 <= entry["altitude"] <= 6000.0
        assert -50.0 <= entry["temperature"] <= 60.0
        assert 0.0 <= entry["rainfall"] <= 5000.0

    # ── 辅助测试：force_fields=false 不包含气象数据 ──────────────────

    def test_get_chunks_no_force_fields(self, handlers):
        """force_fields=false 时不包含额外气象字段。"""
        handle = handlers["get_chunks"]

        msg = {
            "type": "request",
            "request_type": "get_chunks",
            "seq": 4,
            "payload": {
                "chunks": [[2, 2]],
                "force_fields": False,
            },
        }

        response = handle(msg)
        entry = response["payload"]["chunks"][0]

        assert "altitude" not in entry
        assert "temperature" not in entry
        assert "rainfall" not in entry

    # ── 辅助测试：get_chunks 响应中 passable 字段为 bool ──────────

    def test_get_chunks_passable_field(self, gen, handlers):
        """passable 字段为 bool 类型。"""
        handle = handlers["get_chunks"]

        msg = {
            "type": "request",
            "request_type": "get_chunks",
            "seq": 5,
            "payload": {"chunks": [[0, 0], [1, 1], [-1, -1]]},
        }

        response = handle(msg)
        chunks = response["payload"]["chunks"]
        assert len(chunks) == 3

        # 验证 passable 是 bool 类型
        for entry in chunks:
            assert isinstance(entry["passable"], bool)

    # ── 辅助测试：handlers 字典仅含 get_chunks ──────────────────────

    def test_handlers_registered(self, handlers):
        """make_map_handlers 返回的字典包含预期的 key。"""
        assert "get_chunks" in handlers
        assert callable(handlers["get_chunks"])

    # ── 辅助测试：多个坐标顺序正确 ──────────────────────────────────

    def test_get_chunks_order_preserved(self, handlers):
        """chunks 顺序与输入一致。"""
        handle = handlers["get_chunks"]
        coords = [[3, 1], [-1, -2], [0, 5], [7, -3]]

        msg = {
            "type": "request",
            "request_type": "get_chunks",
            "seq": 6,
            "payload": {"chunks": coords},
        }

        response = handle(msg)
        chunks = response["payload"]["chunks"]

        assert len(chunks) == len(coords)
        for i, (cx, cy) in enumerate(coords):
            assert chunks[i]["cx"] == cx
            assert chunks[i]["cy"] == cy
