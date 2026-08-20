"""chunk 生命周期服务注册器测试。"""

import pytest

from ascend.space.chunk_services import (
    ChunkServiceRegistry,
    WeatherChunkService,
    TileStateChunkService,
)


class _FakeWeather:
    """记录调用参数的 weather_engine 桩。"""

    def __init__(self):
        self.registers = []
        self.unregisters = []

    def register_chunk(self, cx, cy, baseline, climate_zone, sea_level_temp):
        self.registers.append((cx, cy, baseline, climate_zone, sea_level_temp))

    def unregister_chunk(self, cx, cy):
        self.unregisters.append((cx, cy))


class _FakeState:
    """记录调用参数的 tile_state_engine 桩。"""

    def __init__(self):
        self.registers = []
        self.readies = []
        self.unregisters = []

    def register_chunk(self, chunk):
        self.registers.append((chunk.cx, chunk.cy))

    def on_tiles_ready(self, cx, cy):
        self.readies.append((cx, cy))

    def unregister_chunk(self, cx, cy):
        self.unregisters.append((cx, cy))


class _FakeChunk:
    def __init__(self, cx, cy, baseline, climate_zone, sea_level_temp):
        self.cx, self.cy = cx, cy
        self.annual_baseline = baseline
        self.climate_zone = climate_zone
        self.sea_level_temp = sea_level_temp


def test_registry_broadcasts_lifecycle_in_order():
    w, s = _FakeWeather(), _FakeState()
    reg = ChunkServiceRegistry([
        WeatherChunkService(w), TileStateChunkService(s),
    ])
    chunk = _FakeChunk(3, -2, 20.0, "temperate", 18.0)

    reg.register(chunk)
    assert w.registers == [(3, -2, 20.0, "temperate", 18.0)]
    assert s.registers == [(3, -2)]

    reg.on_tiles_ready(3, -2)
    assert s.readies == [(3, -2)]

    reg.unregister(3, -2)
    assert w.unregisters == [(3, -2)]
    assert s.unregisters == [(3, -2)]


def test_weather_adapter_noop_on_tiles_ready():
    """weather 是解析算——tile 就绪无追赶动作。"""
    w, s = _FakeWeather(), _FakeState()
    adapter = WeatherChunkService(w)
    assert adapter.on_tiles_ready(0, 0) is None


def test_registry_empty_services_safe():
    reg = ChunkServiceRegistry([])
    chunk = _FakeChunk(0, 0, 0.0, "temperate", 0.0)
    reg.register(chunk)
    reg.on_tiles_ready(0, 0)
    reg.unregister(0, 0)
