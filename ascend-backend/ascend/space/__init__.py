"""世界生成 — Perlin 噪声、气候、群系、分块生成、详细地形。

双层地图结构：
  第一层（chunk 粒度）：确定群系、气候、海拔 — 用于大地图俯瞰和远行规划。
  第二层（tile 粒度）：生成 200×200 地形网格 — 用于玩家活动和建造。

用法:
    from ascend.space import WorldGenerator, ChunkData, BiomeType, ClimateZone

    gen = WorldGenerator(seed=42)
    chunk = gen.generate_chunk(0, 0)       # 单分块
    chunks = gen.generate_parallel(         # 并行
        [(0,0), (0,1), (1,0)],
        max_workers=4,
    )

    # 详细地形
    from ascend.space import TileGenerator, TileGrid, TerrainType
    tile_gen = TileGenerator(seed=42)
    grid = tile_gen.generate(chunk)
"""

from .noise import PerlinNoise
from .climate import (
    ClimateZone, WeatherParams,
    climate_zone_from_noise, climate_zone_from_values,
    annual_baseline, sea_level_temperature, apply_lapse_rate,
    rainfall_from_noise, LAPSE_RATE, clamp,
)
from .biome import BiomeType, BiomeTemplate, biome_from_climate, get_template
from .chunk import ChunkData, TILE_MAP_SIZE
from .generator import WorldGenerator
from .terrain import (
    TerrainType, TerrainProps,
    get_terrain_props, is_passable, is_buildable,
    movement_cost, fertility,
)
from .tile_grid import TileGrid
from .tile_gen import TileGenerator
from .continent import ContinentParams, ContinentData, ContinentGenerator
from .hydrology import (
    ErosionResult, RiverNode, RiverTree, LakeBasin, HydrologyData,
    fill_depressions, compute_d8, compute_dinf,
    flow_accumulation, flow_accumulation_dinf,
    extract_rivers, extract_rivers_dinf,
    build_river_tree, extract_lake_basins,
    strahler_order, erode, carve_rivers,
)
# from .storage import WorldStore

__all__ = [
    # 第一层：大地图
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
    # 第二层：详细地形
    "TerrainType",
    "TerrainProps",
    "get_terrain_props",
    "is_passable",
    "is_buildable",
    "movement_cost",
    "fertility",
    "TileGrid",
    "TileGenerator",
    # 构造模拟
    "ContinentParams",
    "ContinentData",
    "ContinentGenerator",
    # 水文侵蚀
    "ErosionResult",
    "RiverNode",
    "RiverTree",
    "LakeBasin",
    "HydrologyData",
    "fill_depressions",
    "compute_d8",
    "flow_accumulation",
    "extract_rivers",
    "build_river_tree",
    "extract_lake_basins",
    "strahler_order",
    "erode",
    # 持久化 — 待实现
]
