"""地图数据请求处理程序单元测试。

使用 WorldGenerator(seed=42) 生成真实 ChunkData，
验证 make_map_handlers 创建的 get_chunks 处理函数行为。
"""

import base64
import pytest
from ascend.space import WorldGenerator, BiomeType, ClimateZone
from ascend.net.handlers.map_handler import make_map_handlers


# ── 固件（模块级共享：大陆生成昂贵，整个模块只做一次）──────────────

@pytest.fixture(scope="module")
def continent():
    """seed=42 的共享大陆宏观场。"""
    from ascend.space.continent import ContinentGenerator
    return ContinentGenerator(seed=42).generate()


@pytest.fixture(scope="module")
def gen(continent):
    """seed=42 的 WorldGenerator 固件（注入共享大陆，避免重复生成）。"""
    world = WorldGenerator(seed=42)
    world._continent = continent
    return world


@pytest.fixture(scope="module")
def handlers(gen):
    """由 make_map_handlers 创建的处理器字典。"""
    return make_map_handlers(gen)


class TestMapHandlers:
    """地图处理程序测试。"""

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

    def test_get_chunks_over_limit_truncated(self, handlers):
        """超过 MAX_CHUNK_QUERY 的请求被截断，不生成全部 chunk。"""
        from ascend.config import MAX_CHUNK_QUERY

        handle = handlers["get_chunks"]
        coords = [[i, i] for i in range(MAX_CHUNK_QUERY + 100)]

        msg = {
            "type": "request",
            "request_type": "get_chunks",
            "seq": 7,
            "payload": {"chunks": coords},
        }

        response = handle(msg)
        chunks = response["payload"]["chunks"]

        assert len(chunks) == MAX_CHUNK_QUERY

    # ── include_tiles：完整版（tiles_b64 BLOB） ──────────────────────

    def test_get_chunks_include_tiles_returns_tiles_b64(self, gen, continent):
        """include_tiles=true 时返回 tiles_b64 BLOB（与 TileGrid.to_bytes 一致）。"""
        from ascend.space import TileGenerator

        tile_gen = TileGenerator(seed=42, continent=continent)
        handlers = make_map_handlers(gen, tile_gen=tile_gen)
        handle = handlers["get_chunks"]

        msg = {
            "type": "request",
            "request_type": "get_chunks",
            "seq": 8,
            "payload": {"chunks": [[2, -1]], "include_tiles": True},
        }

        response = handle(msg)
        chunks = response["payload"]["chunks"]
        assert len(chunks) == 1
        entry = chunks[0]
        assert "tiles_b64" in entry and entry["tiles_b64"]

        raw = base64.b64decode(entry["tiles_b64"])
        grid = tile_gen.generate_chunk_for(gen.generate_chunk(2, -1))
        assert raw == grid.to_bytes(), "BLOB 应与 TileGrid.to_bytes 逐字节一致"

    def test_get_chunks_include_tiles_second_request_served_from_tiles(self, gen, continent):
        """同一 chunk 二次请求：已有 tiles 直接复用（不重新生成，无竞态）。"""
        from ascend.space import TileGenerator

        tile_gen = TileGenerator(seed=42, continent=continent)
        handlers = make_map_handlers(gen, tile_gen=tile_gen)
        handle = handlers["get_chunks"]
        msg = {
            "type": "request",
            "request_type": "get_chunks",
            "seq": 9,
            "payload": {"chunks": [[1, 1]], "include_tiles": True},
        }

        r1 = handle(msg)
        r2 = handle(msg)

        assert r1["payload"]["chunks"][0]["tiles_b64"] == \
            r2["payload"]["chunks"][0]["tiles_b64"], "重复请求应返回一致数据"


class TestMapHandlersWithStateEngine:
    """include_tiles + tile_state_engine 组合（Issue #37 装配契约）。

    回归：_generate_tiles 无返回值时 future.result() 为 None，
    on_tiles_ready(None.cx) 崩溃（reviewer 实测）；注册-生成-就绪
    时序必须闭环。
    """

    def test_include_tiles_with_state_engine_ready(self, gen, continent):
        """注入状态引擎：tile 生成后就绪并结算，chunk 状态可查。"""
        from ascend.space import TileGenerator
        from ascend.space.tile_state import TileStateEngine
        from ascend.time import WorldClock
        from ascend.weather.weather_engine import WeatherEngine, WeatherParams
        from ascend.space.climate import ClimateZone

        clock = WorldClock()
        weather = WeatherEngine(clock, seed=42)
        engine = TileStateEngine(clock, weather)
        tile_gen = TileGenerator(seed=42, continent=continent)
        from ascend.space.chunk_services import (
            ChunkServiceRegistry, WeatherChunkService, TileStateChunkService,
        )
        registry = ChunkServiceRegistry([
            WeatherChunkService(weather), TileStateChunkService(engine),
        ])
        handlers = make_map_handlers(
            gen, tile_gen=tile_gen, chunk_services=registry,
        )
        handle = handlers["get_chunks"]

        coord = (3, -2)
        msg = {
            "type": "request",
            "request_type": "get_chunks",
            "seq": 20,
            "payload": {"chunks": [list(coord)], "include_tiles": True},
        }

        response = handle(msg)
        chunks = response["payload"]["chunks"]
        assert len(chunks) == 1 and "tiles_b64" in chunks[0], "BLOB 返回"
        assert (coord[0], coord[1]) in engine._chunks, "就绪后已注册并持有网格"
        chunk, grid = engine._chunks[(coord[0], coord[1])]
        assert grid is not None, "快照网格非 None（动态生成后回写）"
        agg = engine.aggregates(coord[0], coord[1])
        assert set(agg) == {"water_frozen", "mean_snow", "mean_moisture"}
