"""生成程序声明 — 大陆 / 水文 / 瓦片 / 统一天气场。

生成程序是**声明的边界生产者**：不是世界模块（无槽位/机制），而是
"种子 + 参数 + 坐标 → 内容场"的确定性程序。本模块把四个程序声明为
一等事实源：

- ``generation.continent``：噪声 → 大陆（land_mask / elevation / 气候属性 / 构造块）；
- ``generation.hydrology``：大陆场 → 水文（侵蚀 / 汇流 / 河流 / 湖泊 / 降雨 / 气候档）；
- ``generation.tile``：chunk 坐标 + 气候/群系模板 → 瓦片场（群系 / 材质 / 高度 / 坡度 / 覆盖）；
- ``generation.weather_field``：种子 + 坐标 + 时刻 → 统一天气场扰动通道（含特征核）。

每个程序声明：输入、输出（内容名 + ``feeds``：喂给的声明槽位）、内容身份
（源码文件 + 配置常量 + 版本 → 指纹）、确定性说明、采样协议（证据如何
取样）。**清单对齐**：``compute_gen_fingerprint`` 的旧清单（源码文件 +
配置常量）必须被四个程序完整覆盖——任何新增文件/常量未归属即红（漂移
测试 ``tests/world/test_generation.py``）。

``feeds`` 与游戏程序的**声明外部槽位**一一对应（时钟/驱动槽位除外），
这是 ``slice_boundary`` 消解的可执行证据：边界输入全部有声明来源。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from ascend.world.kernel import digest_object, file_digest

__all__ = [
    "BACKEND_ROOT",
    "DRIVER_INPUT_SLOTS",
    "GENERATION_PROGRAMS",
    "GenerationDecl",
    "generation_fingerprint",
    "generation_identity",
]

BACKEND_ROOT = Path(__file__).resolve().parents[2]

_IDENT = re.compile(r"^[a-z][a-z0-9_]*(?:\.[a-z0-9_]+)*$")

#: 由时钟/驱动/引擎桥接提供的声明外部槽位（非生成程序产出）。
DRIVER_INPUT_SLOTS: tuple[str, ...] = (
    "world.clock.tick",
    "terrain.dt",
    "weather.precip_moisture",
    "weather.precip_snow",
    "weather.step_temp",
)


@dataclass(frozen=True, slots=True)
class GenerationDecl:
    """生成程序声明（输入 / 输出 / 身份 / 确定性 / 采样协议）。

    Attributes:
        id: 程序标识（如 ``generation.continent``）。
        version: 声明版本（打包模式无源码时的身份退位锚点）。
        inputs: 声明的输入（种子/参数/上游内容）。
        outputs: 产出的内容名（不含槽位 id）。
        feeds: 喂给的声明槽位（游戏程序的 external 槽位；边界来源证据）。
        source_files: 参与指纹的源码文件（backend 根相对路径）。
        constants: 参与指纹的配置常量名（``ascend.config``）。
        determinism: 确定性说明（同输入同输出）。
        sampling: 采样协议（证据取样方式）。
        notes: 边界与说明。
    """

    id: str
    version: str
    inputs: tuple[str, ...]
    outputs: tuple[str, ...]
    feeds: tuple[str, ...] = ()
    source_files: tuple[str, ...] = ()
    constants: tuple[str, ...] = ()
    determinism: str = ""
    sampling: tuple[str, ...] = ()
    notes: str = ""

    def __post_init__(self) -> None:
        if _IDENT.fullmatch(self.id) is None:
            raise ValueError(f"生成程序 id 必须为点分小写标识: {self.id!r}")
        if not self.version:
            raise ValueError(f"生成程序 {self.id} 必须给出声明版本")
        if not self.inputs or not self.outputs:
            raise ValueError(f"生成程序 {self.id} 必须声明输入与输出")
        if not self.determinism:
            raise ValueError(f"生成程序 {self.id} 必须给出确定性说明")
        if not self.sampling:
            raise ValueError(f"生成程序 {self.id} 必须给出采样协议")


GENERATION_PROGRAMS: tuple[GenerationDecl, ...] = (
    GenerationDecl(
        id="generation.continent",
        version="1",
        inputs=("seed", "continent.params(width_km,height_km,sample_resolution)"),
        outputs=(
            "continent.land_mask",
            "continent.elevation_m",
            "continent.climate_attrs",
            "continent.terrain_blocks",
            "continent.glacial",
            "continent.volcanism",
            "continent.coast",
        ),
        feeds=(
            "weather.chunk.solar_latitude_proxy_deg",
            "weather.chunk.annual_mean_temperature_c",
            "weather.chunk.annual_rainfall_mm_per_year",
            "weather.chunk.seasonal_temperature_amplitude_c",
            "weather.chunk.diurnal_temperature_amplitude_c",
        ),
        source_files=(
            "ascend/space/continent.py",
            "ascend/space/continent_data.py",
            "ascend/space/noise.py",
            "ascend/space/_perlin.c",
        ),
        constants=(
            "CONTINENT_WIDTH_KM", "CONTINENT_HEIGHT_KM",
            "CONTINENT_SAMPLE_RESOLUTION_M", "CONTINENT_LAND_RATIO",
            "ELEVATION_SCALE_FACTOR", "NOISE_FREQ_DERIVED",
            "CONTINENT_NOISE_OCTAVES", "CONTINENT_OUTLINE_OCTAVES",
            "CONTINENT_BLEND_WEIGHT", "TERRAIN_BLEND_WEIGHT",
            "CENTER_BIAS_WEIGHT", "SEA_LEVEL_ELEV",
            "TERRAIN_NOISE_FREQUENCY", "TERRAIN_NOISE_OCTAVES",
            "TERRAIN_NOISE_AMPLITUDE", "ELEVATION_TARGET_P99",
        ),
        determinism=(
            "纯函数：同 (seed, ContinentParams) 同输出；无 IO、无时钟、"
            "不依赖加载/物化顺序"
        ),
        sampling=(
            "同 (seed, params) 重生成 → 逐位一致",
            "同一点重复采样 → 逐位一致（与采样顺序无关）",
            "缓存命中与重生成 → 逐位一致",
        ),
        notes="层1 全局低分辨率大陆；输出经气候模板组装为 chunk 基线。",
    ),
    GenerationDecl(
        id="generation.hydrology",
        version="1",
        inputs=(
            "continent.elevation_m", "continent.climate_attrs",
            "hydrology.params(erosion,flow,rainshadow)",
        ),
        outputs=(
            "hydrology.erosion",
            "hydrology.flow_accumulation",
            "hydrology.rivers",
            "hydrology.lakes",
            "hydrology.rainfall_mm_per_year",
            "hydrology.climate_classes",
        ),
        feeds=(
            "weather.chunk.baseline_humidity_percent",
            "weather.chunk.baseline_wind_speed_mps",
            "weather.chunk.humidity_sharpness",
            "weather.chunk.mean_precip_intensity_mm_per_hour",
            "weather.chunk.seasonal_humidity_amplitude_pp",
            "weather.chunk.diurnal_humidity_amplitude_pp",
        ),
        source_files=(
            "ascend/space/hydrology.py",
            "ascend/space/streamlines.py",
            "ascend/space/climate.py",
            "ascend/space/_hydrology.c",
            "ascend/space/_streamlines.c",
        ),
        constants=(
            "EROSION_ITERATIONS", "EROSION_ERODIBILITY", "EROSION_TOLERANCE",
            "EROSION_MIN_ITERATIONS", "RIVER_FLOW_THRESHOLD",
            "RIVER_MIN_LENGTH", "RIVER_WIDTH_THRESHOLD", "RIVER_WIDTH_MIN",
            "RIVER_WIDTH_MAX", "LAKE_MIN_PIXELS", "LAKE_WETLAND_DEPTH_MAX",
            "RAINSHADOW_DECAY_KM", "RAINSHADOW_SECONDARY_WEIGHT",
            "RAINSHADOW_MIN_FACTOR", "CONTINENTALITY_K",
            "CONTINENTALITY_D0_KM", "CLIMATE_CALIB_RAINFALL_REF",
            "CLIMATE_CALIB_TEMP_MIN", "CLIMATE_CALIB_TEMP_MAX",
            "CLIMATE_CALIB_HOT_THRESHOLD", "CLIMATE_CALIB_COLD_RANGE",
            "CLIMATE_CALIB_HOT_RAINFALL_TARGET",
            "CLIMATE_CALIB_HOT_STRETCH_PARAM",
            "CLIMATE_CALIB_COLD_RAINFALL_TARGET",
            "CLIMATE_CALIB_COLD_STRETCH_PARAM", "LAPSE_RATE",
            "RAINFALL_MIN", "RAINFALL_MAX", "ALPINE_ALTITUDE", "POLAR_TEMP",
            "DESERT_RAINFALL", "STEPPE_RAINFALL", "STEPPE_MIN_TEMP",
            "TROPICAL_TEMP", "TEMPERATE_TEMP", "RAINFOREST_RAINFALL",
            "TAIGA_RAINFALL", "OCEAN_COLD_CUTOFF", "OCEAN_WARM_CUTOFF",
            "OCEAN_DEEP_THRESHOLD",
        ),
        determinism=(
            "纯函数：同大陆场 + 参数同输出；C 内核与 Python 参考逐位一致"
        ),
        sampling=(
            "固定大陆场重复生成 → 逐位一致",
            "同一点重复取样 → 逐位一致",
            "内核对（C ↔ Python）逐位一致（tests/unit/test_hydrology.py）",
        ),
        notes="层2 水文：侵蚀/汇流/河湖/降雨阴影/气候档；海洋档由沿海分类给出。",
    ),
    GenerationDecl(
        id="generation.tile",
        version="1",
        inputs=(
            "seed", "chunk.coords(cx,cy)", "climate.template",
            "biome.template", "continent.terrain_blocks",
        ),
        outputs=(
            "tile.biome_id",
            "tile.terrain_id",
            "tile.height_m",
            "tile.slope",
            "tile.cover",
            "tile.moisture_noise",
        ),
        feeds=(
            "terrain.terrain_id",
            "terrain.slope",
            "terrain.cover",
        ),
        source_files=(
            "ascend/space/tile_gen.py",
            "ascend/space/tile_grid.py",
            "ascend/space/biome.py",
            "ascend/space/terrain.py",
            "ascend/space/noise.py",
            "ascend/space/_perlin.c",
            "ascend/space/_state.c",
        ),
        constants=(
            "TILE_MAP_SIZE", "MOISTURE_TILE_FREQUENCY", "ROCK_LINE_ELEV",
            "BARE_ROCK_SLOPE", "GRAVEL_ALT_BAND", "FERTILE_LOW_ELEV",
            "WETLAND_BAND_M", "ALLUVIAL_BAND_M", "ARID_RAINFALL_MM",
            "PERMAFROST_TEMP_C", "SAND_BEACH_BAND_M",
        ),
        determinism=(
            "纯函数：同 (seed, cx, cy, 模板) 同瓦片场；chunk 边界由隶属度"
            "混合保持连续，不依赖邻居是否已生成"
        ),
        sampling=(
            "同 (seed, cx, cy) 重复生成 → 逐位一致",
            "与生成顺序无关（单块 vs 多块顺序生成）→ 逐位一致",
            "相邻 chunk 边界值连续（隶属度混合）",
        ),
        notes="层3 瓦片：材质/群系/高度/坡度/覆盖；_state.c 为瓦片状态内核（随包）。",
    ),
    GenerationDecl(
        id="generation.weather_field",
        version="1",
        inputs=("seed", "world.coords(x,y)", "tick", "feature.cores"),
        outputs=(
            "field.temperature_perturbation",
            "field.humidity_perturbation",
            "field.wind_perturbation",
            "field.wind_multiplier",
            "field.precipitation_signal",
        ),
        feeds=(
            "weather.field.temperature_perturbation",
            "weather.field.humidity_perturbation",
            "weather.field.wind_perturbation",
            "weather.field.wind_multiplier",
            "weather.field.precipitation_signal",
        ),
        source_files=(
            "ascend/weather/field.py",
            "ascend/weather/atmosphere.py",
            "ascend/weather/features.py",
            "ascend/space/noise.py",
            "ascend/space/_perlin.c",
        ),
        constants=(
            "WEATHER_FIELD_GRID_SIZE",
            "WEATHER_FIELD_TILE_NOISE_WAVELENGTH",
            "WEATHER_FIELD_TILE_NOISE_SCALE",
            "FEATURE_BLOCK_SIZE", "FEATURE_MAX_RADIUS",
            "CLIMATE_PROXY_TEMP_WAVELENGTH",
            "CLIMATE_PROXY_RAIN_WAVELENGTH", "CLIMATE_PROXY_OCTAVES",
        ),
        determinism=(
            "解析算：同 (seed, 坐标, 时刻) 同值；特征核为外部输入（干预/"
            "投影），不改变场函数本身"
        ),
        sampling=(
            "同 (seed, 坐标, 时刻) 重复采样 → 逐位一致",
            "与采样顺序/批量形状无关（单点 vs 批量逐点）→ 逐位一致",
        ),
        notes="统一天气场（噪声通道 + 特征核扰动）；输出喂给引擎边界槽位。",
    ),
)


def generation_fingerprint(decl: GenerationDecl) -> str:
    """生成程序内容指纹 = 版本 + 配置常量值 + 源码文件内容摘要。

    打包模式（源码缺失）退位到声明版本 + 常量（与旧管线同策略）；
    源码在场时任一文件/常量变化即变（WC-1.2 生成身份）。
    """
    from ascend import config

    constants: dict[str, object] = {}
    for name in decl.constants:
        if not hasattr(config, name):
            raise ValueError(f"生成程序 {decl.id}: 常量未声明 {name}")
        constants[name] = getattr(config, name)
    sources: dict[str, str] = {}
    for rel in decl.source_files:
        try:
            sources[rel] = file_digest(BACKEND_ROOT / rel)
        except OSError:
            # 打包模式：源码缺失 → 身份退位（版本锚点）
            return digest_object({
                "id": decl.id,
                "version": decl.version,
                "constants": constants,
                "sources": "packaged",
            })
    return digest_object({
        "id": decl.id,
        "version": decl.version,
        "constants": constants,
        "sources": sources,
    })


def generation_identity() -> dict[str, str]:
    """全部生成程序的指纹表（进 manifest / 世界设置）。"""
    return {
        decl.id: generation_fingerprint(decl)
        for decl in GENERATION_PROGRAMS
    }
