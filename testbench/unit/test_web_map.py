"""开发地图工具与生产世界生成器同源，二进制图层布局保持稳定。"""

import struct

import pytest

from miskhak.tools.web import server
from olam.constants import TILE_MAP_SIZE
from olam.generation.generator import WorldGenerator
from olam.generation.tile_gen import TileGenerator


@pytest.fixture(scope="module")
def world():
    return WorldGenerator(seed=42, width_km=2, height_km=2)


def test_map_layers_match_world_and_chunk_labels(monkeypatch, world):
    monkeypatch.setattr(server, "_get_generator", lambda seed: world)
    payload = server._tile_worker(42, 1, 2, 2, 2, "biome")
    values = struct.unpack("<20f", payload)
    # 四个 chunk 行优先；五层依次为海拔、温度、降雨、气候、群系。
    for index, (cx, cy) in enumerate(((1, 2), (2, 2), (1, 3), (2, 3))):
        chunk = world.generate_chunk(cx, cy)
        assert values[index] == pytest.approx(chunk.annual_baseline.altitude)
        assert values[4 + index] == pytest.approx(chunk.annual_baseline.temperature)
        assert values[8 + index] == pytest.approx(chunk.annual_baseline.rainfall)
        assert values[12 + index] == int(chunk.climate_zone)
        assert values[16 + index] == int(chunk.biome)
    detail = server._build_chunk_detail(world, 1, 2)
    assert detail["biome"]["label"] != world.get_biome(1, 2).label_key
    assert detail["climate_zone"]["label"] != world.get_climate(1, 2).label_key


def test_terrain_image_matches_production_materials(monkeypatch, world):
    monkeypatch.setattr(server, "_get_generator", lambda seed: world)
    payload = server._terrain_worker(42, 0, 0)
    assert len(payload) == TILE_MAP_SIZE ** 2 * 4
    grid = TileGenerator(42, world.ensure_continent()).generate_chunk(0, 0)
    for index, material in enumerate(grid.raw_data()):
        assert payload[4 * index:4 * index + 4] == bytes((*server.TERRAIN_COLORS[material], 255))
