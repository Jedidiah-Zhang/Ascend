"""世界生成 — Perlin 噪声、气候、群系、分块生成与并行协调。

用法:
    from ascend.world import WorldGenerator, ChunkData, BiomeType, ClimateZone

    gen = WorldGenerator(seed=42)
    chunk = gen.generate_chunk(0, 0)       # 单分块
    chunks = gen.generate_parallel(         # 并行
        [(0,0), (0,1), (1,0)],
        max_workers=4,
    )
"""

from .noise import PerlinNoise
from .climate import ClimateZone, WeatherParams, climate_zone_from_noise, annual_baseline
from .biome import BiomeType, BiomeTemplate, biome_from_climate, get_template
from .chunk import ChunkData, TILE_MAP_SIZE
from .generator import WorldGenerator
from .render import render_map, render_region_detail

__all__ = [
    "WorldGenerator",
    "PerlinNoise",
    "ClimateZone",
    "WeatherParams",
    "climate_zone_from_noise",
    "annual_baseline",
    "BiomeType",
    "BiomeTemplate",
    "biome_from_climate",
    "get_template",
    "ChunkData",
    "TILE_MAP_SIZE",
    "render_map",
    "render_region_detail",
]
