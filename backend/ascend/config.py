"""集中配置 — 所有可调参数的单一定义源。

按领域组织，各模块通过 `from ascend.config import XXX` 引用。

**内容参数（World / Climate / Weather / Tile）的有效值来自
`data/world.json`**（改内容只改数据文件、不用改代码，也为未来 Mod
提供数据层修改入口；本文件中的赋值仅为类型声明与兜底默认）。修改
内容参数请改 `data/world.json`，配置文件于模块末尾加载并覆盖。
Server / Time / Storage / UI 为引擎/基础设施常量，仅在本文件定义。

类别:
    Server    — 网络与连接（代码）
    Time      — 时间常量（代码）
    World     — 世界生成（数据，data/world.json）
    Climate   — 气候判定阈值（数据，data/world.json）
    Weather   — 天气参数（数据，data/world.json）
    Tile      — 瓦片生成阈值（数据，data/world.json）
    Storage   — 持久化与缓存（代码）
    UI        — 终端与调试（代码）
"""

# ═══════════════════════════════════════════════════════════════
# Server — 网络与连接
# ═══════════════════════════════════════════════════════════════

SERVER_HOST: str = "127.0.0.1"
SERVER_PORT: int = 9081

MAX_MESSAGE_SIZE: int = 16 * 1024 * 1024  # 16 MiB

# 地图瓦片生成线程池大小
TILE_WORKERS: int = 8

# ═══════════════════════════════════════════════════════════════
# Time — 时间常量（本模块为 TICK_RATE 唯一定义源）
# ═══════════════════════════════════════════════════════════════

TICK_RATE: int = 24           # 1 真实秒 = 24 tick
GAME_MINUTE: int = 120        # 1 游戏分钟 = 120 tick（5 真实秒）
GAME_HOUR: int = 7200         # 1 游戏小时 = 7200 tick
GAME_DAY: int = 172800        # 1 游戏天 = 172800 tick
GAME_YEAR: int = 62208000     # 1 游戏年 = 360 游戏天

TICK_DT: float = 1.0 / TICK_RATE

# ═══════════════════════════════════════════════════════════════
# World — 世界生成
# ═══════════════════════════════════════════════════════════════

# 每个 chunk 的 tile 分辨率（200×200）
TILE_MAP_SIZE: int = 200

# 出生点周边预生成 chunk 半径（2 → 5×5 共 25 个）
INITIAL_CHUNK_RADIUS: int = 2

# 出生点海拔范围（m）— 海岸低地，沙滩/草地带
BIRTH_ELEV_MIN: float = 0.0
BIRTH_ELEV_MAX: float = 50.0

# 大陆参数默认值
CONTINENT_WIDTH_KM: float = 100.0
CONTINENT_HEIGHT_KM: float = 60.0
CONTINENT_SAMPLE_RESOLUTION_M: float = 100.0  # 层1 采样分辨率
CONTINENT_LAND_RATIO: float = 0.55            # 目标陆地比例

# 构造海拔缩放倍率
ELEVATION_SCALE_FACTOR: float = 4000.0  # (归一化值 - 海平面) 缩放至米

# 侵蚀参数
EROSION_ITERATIONS: int = 10
EROSION_ERODIBILITY: float = 0.01
EROSION_TOLERANCE: float = 0.05
EROSION_MIN_ITERATIONS: int = 3

# 河流参数
RIVER_FLOW_THRESHOLD: float = 500.0   # 河流生成的水流累积阈值
RIVER_MIN_LENGTH: int = 20            # 最小河流长度（网格点数）
RIVER_WIDTH_THRESHOLD: float = 20.0   # 河流宽度计算阈值
RIVER_WIDTH_MIN: float = 2.0          # 最小河流宽度 (m)
RIVER_WIDTH_MAX: float = 80.0         # 最大河流宽度 (m)

# 湖泊参数
LAKE_MIN_PIXELS: int = 5              # 湖泊盆地最小像素
LAKE_WETLAND_DEPTH_MAX: float = 2.0   # 湿地边缘范围 (湖面以上 0-2m)

# 雨影参数
RAINSHADOW_DECAY_KM: float = 4.0      # 抬升衰减距离 (km)
RAINSHADOW_SECONDARY_WEIGHT: float = 0.2  # 次风向权重
RAINSHADOW_MIN_FACTOR: float = 0.15   # 最小雨影因子

# 大陆度系数
CONTINENTALITY_K: float = 3.0
CONTINENTALITY_D0_KM: float = 200.0

# 噪声频率
NOISE_FREQ_DERIVED: float = 0.005     # 派生参数噪声（中频，日照/湿度/风速）

# 群系细分 moisture 噪声（tile 级世界坐标频率——与 chunk 级 NOISE_FREQ_DERIVED
# 同空间尺度：chunk 级在块坐标用 0.005，tile 级换算到世界坐标后频率除以
# TILE_MAP_SIZE，保证 chunk 标签与 tile 隶属度来自同一噪声场）
MOISTURE_TILE_FREQUENCY: float = NOISE_FREQ_DERIVED / TILE_MAP_SIZE

# 地形噪声
TERRAIN_NOISE_FREQUENCY: float = 0.005
TERRAIN_NOISE_OCTAVES: int = 4
TERRAIN_NOISE_AMPLITUDE: float = 50.0  # ±50m 细节噪声幅度

# 大陆轮廓噪声
CONTINENT_NOISE_OCTAVES: int = 5
CONTINENT_OUTLINE_OCTAVES: int = 2

# 大陆混合权重
CONTINENT_BLEND_WEIGHT: float = 0.7    # 大陆场权重
TERRAIN_BLEND_WEIGHT: float = 0.3      # 地形场权重
CENTER_BIAS_WEIGHT: float = 0.12       # 中心增强权重

# 海洋判定
SEA_LEVEL_ELEV: float = 0.0
OCEAN_COLD_CUTOFF: float = 5.0         # 冷水海洋分界 (sea temp < 此值)
OCEAN_WARM_CUTOFF: float = 20.0        # 暖水海洋分界 (sea temp >= 此值)
OCEAN_DEEP_THRESHOLD: float = -100.0   # 深海深度阈值 (m)

# 气候校准
CLIMATE_CALIB_RAINFALL_REF: float = 100.0
CLIMATE_CALIB_TEMP_MIN: float = -12.0
CLIMATE_CALIB_TEMP_MAX: float = 30.0
CLIMATE_CALIB_HOT_THRESHOLD: float = 20.0
CLIMATE_CALIB_COLD_RANGE: tuple[float, float] = (-5.0, 5.0)
CLIMATE_CALIB_HOT_RAINFALL_TARGET: float = 1500.0
CLIMATE_CALIB_HOT_STRETCH_PARAM: tuple[float, float] = (200.0, 1600.0)
CLIMATE_CALIB_COLD_RAINFALL_TARGET: float = 500.0
CLIMATE_CALIB_COLD_STRETCH_PARAM: tuple[float, float] = (300.0, 300.0)

# 海拔校准
ELEVATION_TARGET_P99: float = 2500.0   # 陆地 P99 目标海拔

# ═══════════════════════════════════════════════════════════════
# Climate — 气候判定阈值
# ═══════════════════════════════════════════════════════════════

LAPSE_RATE: float = 9.0                # 气温直减率 (°C/1000m)，游戏性放大值


RAINFALL_MIN: float = 50.0             # 年降雨量下限 (mm)
RAINFALL_MAX: float = 3500.0           # 年降雨量上限 (mm)

# 气候档位判定阈值
ALPINE_ALTITUDE: float = 2000.0        # 高山海拔阈值 (m)
POLAR_TEMP: float = -5.0               # 极地温度阈值 (°C)
DESERT_RAINFALL: float = 200.0         # 沙漠降雨阈值 (mm/年)
STEPPE_RAINFALL: float = 600.0         # 草原降雨阈值 (mm/年)
STEPPE_MIN_TEMP: float = 5.0           # 草原温度下限 (°C)
TROPICAL_TEMP: float = 20.0            # 热带温度阈值 (°C)
TEMPERATE_TEMP: float = 5.0            # 温带温度下限 (°C)
RAINFOREST_RAINFALL: float = 1500.0    # 雨林降雨阈值 (mm/年)
TAIGA_RAINFALL: float = 400.0          # 针叶林降雨阈值 (mm/年)

# 气象参数物理边界
PARAM_BOUNDS: dict[str, tuple[float, float]] = {
    "temperature": (-30.0, 50.0),
    "rainfall": (0.0, 5000.0),
    "sunshine": (0.0, 24.0),
    "altitude":    (-500.0, 5000.0),
    "humidity": (0.0, 100.0),
    "wind_speed": (0.0, 50.0),
}

# ═══════════════════════════════════════════════════════════════
# Weather — 天气参数
# ═══════════════════════════════════════════════════════════════

# 统一天气场（特征 + 纹理双分量）
WEATHER_FIELD_GRID_SIZE: float = 1000.0  # 采样网格间距 (m)，≈5 chunk
WEATHER_FIELD_TILE_NOISE_WAVELENGTH: float = 10.0  # tile 级确定性噪声波长 (m)
WEATHER_FIELD_TILE_NOISE_SCALE: float = 0.06  # tile 级噪声幅度（归一化值）

# 纹理分量 — 波长按参数独立（多 octave Perlin）
TEXTURE_WAVELENGTH_TEMP: float = 7500.0  # 温度波长 (m)，5-10km
TEXTURE_WAVELENGTH_WIND: float = 2500.0  # 风波长 (m)，2-3km
TEXTURE_WAVELENGTH_PRECIP: float = 1500.0  # 降水波长 (m)，1-2km
TEXTURE_OCTAVES: int = 4                 # 纹理多八度层数
TEXTURE_PERSISTENCE: float = 0.5         # 多八度振幅衰减率
TEXTURE_LACUNARITY: float = 2.0          # 多八度频率倍增率
TEXTURE_DRIFT_RATE: float = 1e-5         # 场沿风向漂移线速度（噪声单位/tick）

# 特征分量 — 空间块生成（各特征类型配置见 weather/features.py 注册表）
FEATURE_BLOCK_SIZE: float = 16000.0      # 特征生成空间块边长 (m)
FEATURE_MAX_RADIUS: float = 6000.0       # 特征核半径上限 (m)

# 气候代理场（特征生成频率 + 降水校准用，低频近似）
CLIMATE_PROXY_TEMP_WAVELENGTH: float = 100000.0  # 温度代理波长 (m)
CLIMATE_PROXY_RAIN_WAVELENGTH: float = 60000.0   # 降雨代理波长 (m)
CLIMATE_PROXY_OCTAVES: int = 3

# 季节
SEASONS_PER_YEAR: int = 4
SEASON_LENGTH_DAYS: int = 90            # 每季节 90 天
SEASON_LENGTH: int = SEASON_LENGTH_DAYS * GAME_DAY  # 每季节 tick 数

# 昼夜
DIURNAL_PEAK_HOUR: int = 14             # 最热时刻 (14:00)
DIURNAL_TROUGH_HOUR: int = 2            # 最冷时刻 (02:00)
SUNRISE_HOUR: int = 6                   # 日出时刻
SUNSET_HOUR: int = 18                   # 日落时刻
OBLIQUITY_DEG: float = 23.44            # 黄赤交角

# 大气扰动缩放
TEMP_PERTURB_SCALE: float = 5.0         # 温度扰动幅度 (±5°C)
HUMIDITY_PERTURB_SCALE: float = 15.0    # 湿度扰动幅度 (±15%)
WIND_PERTURB_SCALE: float = 4.0         # 风速扰动幅度 (±4 m/s)
SUNSHINE_PERTURB_SCALE: float = 1.5     # 日照扰动幅度 (±1.5 小时)
DIURNAL_TO_SEASONAL_RATIO: float = 0.5  # 昼夜振幅 vs 季节振幅
HUMIDITY_DIURNAL_SCALE: float = 0.8     # 湿度昼夜偏移缩放
HUMIDITY_SEASONAL_SCALE: float = 0.4    # 湿度季节偏移缩放

# 降水标定（场信号 → mm/h，气候带校准）
PRECIP_SIGNAL_MIN: float = 0.2          # 场信号下界（低于此永不降雨）
PRECIP_SIGNAL_MAX: float = 1.2          # 场信号上界（校准归一化用）
PRECIP_THRESHOLD_DRY: float = 0.55      # 最干旱气候带的越阈水平
PRECIP_THRESHOLD_WET: float = 0.25      # 最湿润气候带的越阈水平
PRECIP_ANNUAL_DRY: float = 50.0         # 越阈水平推导的干旱参考年降雨 (mm)
PRECIP_ANNUAL_WET: float = 3500.0       # 越阈水平推导的湿润参考年降雨 (mm)
PRECIP_INTENSITY_SCALE: float = 2.0     # 信号超阈 → 强度放大系数（基准强度 ×）

# 天气查询 API
MAX_WEATHER_QUERY_CHUNKS: int = 64      # get_weather 单请求最大 chunk 数（防超大请求卡游戏线程）

# 地图请求 API
MAX_CHUNK_QUERY: int = 512              # get_chunks 单请求最大 chunk 数（防超大请求卡游戏线程）

# ═══════════════════════════════════════════════════════════════
# Save — 存档（实时写入频率）
# ═══════════════════════════════════════════════════════════════

SAVE_PULSE_INTERVAL: float = 5.0        # 统一保存脉搏间隔（真实秒）


# 天气分级阈值 — 按数值升序排列，返回值为区间索引（0-based）
# 事件仅在等级变化时发布（含 prev_tier 用于判定趋势）
TEMP_TIER_BOUNDARIES: tuple[float, ...] = (
    -10.0, -3.0, 5.0, 13.0, 20.0, 25.0, 30.0, 36.0, 43.0,
)

HUMIDITY_TIER_BOUNDARIES: tuple[float, ...] = (
    25.0, 50.0, 72.0, 88.0,
)

WIND_TIER_BOUNDARIES: tuple[float, ...] = (
    1.5, 4.0, 8.0, 14.0, 23.0,
)

SUNSHINE_TIER_BOUNDARIES: tuple[float, ...] = (
    1.5, 4.5, 8.0, 12.0, 15.5,
)

# 日照强度分级 (0~1 归一化，0=黑夜 1=正午烈日)
SUNLIGHT_INTENSITY_TIER_BOUNDARIES: tuple[float, ...] = (
    0.01, 0.25, 0.55, 0.80,
)

# 物理边界
TEMP_BOUNDS: tuple[float, float] = (-30.0, 50.0)
HUMIDITY_BOUNDS: tuple[float, float] = (0.0, 100.0)
WIND_BOUNDS: tuple[float, float] = (0.0, 50.0)
SUNSHINE_BOUNDS: tuple[float, float] = (0.0, 24.0)
RAIN_INTENSITY_BOUNDS: tuple[float, float] = (0.0, 100.0)  # mm/小时

# 纬度推导
LATITUDE_T_MIN: float = -5.0            # 年均温下界（极地）
LATITUDE_T_MAX: float = 35.0            # 年均温上界（赤道）
LATITUDE_MIN: float = 0.0               # 赤道纬度
LATITUDE_MAX: float = 80.0              # 极地边缘纬度

# 季节振幅
SEASONAL_AMP_T_MIN: float = -5.0        # 振幅最大时的年均温
SEASONAL_AMP_T_MAX: float = 35.0        # 振幅最小时的年均温
SEASONAL_AMP_MAX: float = 28.0          # 低温端最大季节振幅 (°C)
SEASONAL_AMP_MIN: float = 2.0           # 高温端最小季节振幅 (°C)
SEASONAL_AMP_R_REF: float = 2000.0      # 降雨参考值（海洋调节基准）
SEASONAL_AMP_R_BONUS: float = 4.0       # 干旱区大陆性修正幅度
SEASONAL_AMP_BOUNDS: tuple[float, float] = (1.0, 30.0)

# ═══════════════════════════════════════════════════════════════
# Tile — 瓦片生成阈值
# ═══════════════════════════════════════════════════════════════

# 地形材质分布阈值
# 全部输入为低频连续场，不参与 layer1 大陆生成——不入指纹名单。
# 距水距离带 (m)：按到最近水体（海/河/湖）的平面距离划分材质
SAND_BEACH_BAND_M: float = 40.0         # 沙滩带：距水 < 此值 → SAND
ALLUVIAL_BAND_M: float = 120.0          # 冲积带：距水 < 此值 且 低海拔 → FERTILE_SOIL
WETLAND_BAND_M: float = 200.0           # 湿地带：距水 < 此值 且 湿度高 → MARSH
# 岩线基准 (m)：海拔高于此 → ROCK（群系偏移；与 ALPINE_ALTITUDE 同尺度）
ROCK_LINE_ELEV: float = 2000.0
# 裸岩坡度阈值 (m/m)：面内坡度高于此 → ROCK（裸岩，无土被）
BARE_ROCK_SLOPE: float = 0.6
# 干旱降雨阈值 (mm/年)：低于此视为干旱群系（GRAVEL/SAND 判定入口；
# 与 DESERT_RAINFALL 同尺度）
ARID_RAINFALL_MM: float = 250.0
# 干旱群系海拔区间 (m)：[lo, hi) 内 高/中海拔 → GRAVEL，低海拔 → SAND
GRAVEL_ALT_BAND: tuple[float, float] = (200.0, 2000.0)
# 冻土线 (°C)：年均温低于此 → PERMAFROST（寒带非岩非坡）
PERMAFROST_TEMP_C: float = -5.0
# 冲积沃土低海拔上限 (m)：距水 < 冲积带 且 海拔 < 此值 → FERTILE_SOIL
FERTILE_LOW_ELEV: float = 400.0
# 水体通行（实时按水深推导，无 depth 通道——水深 = −海拔）：
# 水深 ≤ 此值可涉水通行，否则不可通行
WATER_WADE_DEPTH_M: float = 2.0
# 涉水移动成本倍率
WATER_WADE_COST: float = 3.0

# ═══════════════════════════════════════════════════════════════
# Storage — 持久化与缓存
# ═══════════════════════════════════════════════════════════════

import os as _os
import tempfile as _tempfile

_PROJECT_ROOT: str = _os.path.normpath(_os.path.join(_os.path.dirname(__file__), "..", ".."))

# 无存档模式（测试/调试，world_id=None）的数据根：系统临时目录，
# 调试数据不污染项目根，随 /tmp 系统清理
_DEV_DATA_ROOT: str = _os.path.join(_tempfile.gettempdir(), "ascend-dev")

# ChunkStore
CHUNK_STORE_DB_PATH: str = _os.path.join(_DEV_DATA_ROOT, "chunks.db")
CHUNK_STORE_MAX_SIZE: int = 49          # LRU 缓存最大 chunk 数

# WorldTree 归档
WT_MAX_MEMORY_EVENTS: int = 100_000     # 内存最大事件数
WT_GRAPH_WARMUP_EVENTS: int = 10_000    # 图预热事件数
WT_ARCHIVE_PATH: str = _os.path.join(_DEV_DATA_ROOT, "events.db")

# SQLite 性能参数
SQLITE_JOURNAL_MODE: str = "WAL"
SQLITE_SYNCHRONOUS: str = "NORMAL"
SQLITE_MMAP_SIZE: int = 268435456       # 256MB 内存映射
SQLITE_CACHE_SIZE: int = -8000          # 8MB 页缓存（负数 = KB）

# 存档根目录（用户主目录 .ascend/saves；ASCEND_SAVE_ROOT 环境变量
# 覆盖——测试隔离用，进程级测试写入临时目录）
SAVE_ROOT: str = _os.environ.get(
    "ASCEND_SAVE_ROOT",
    _os.path.join(_os.path.expanduser("~"), ".ascend", "saves"),
)

# ═══════════════════════════════════════════════════════════════
# UI — 终端与调试
# ═══════════════════════════════════════════════════════════════

# 终端输出行限制
TERMINAL_OUTPUT_LINE_LIMIT: int = 500
TERMINAL_HISTORY_LIMIT: int = 100

# ═══════════════════════════════════════════════════════════════
# Generation fingerprint — 生成环境指纹（continent.bin 漂移诊断）
# ═══════════════════════════════════════════════════════════════

# 指纹覆盖的生成相关常量名（compute_gen_fingerprint 经 getattr 解析，
# 测试保证名单内名字全部存在）。修改影响大陆宏观场输出的常量时，
# 应将其加入此元组——漂移才能被 continent status 与加载告警发现。
CONTINENT_GEN_CONSTANT_NAMES: tuple[str, ...] = (
    "CONTINENT_WIDTH_KM", "CONTINENT_HEIGHT_KM", "CONTINENT_SAMPLE_RESOLUTION_M",
    "CONTINENT_LAND_RATIO",
    "ELEVATION_SCALE_FACTOR",
    "EROSION_ITERATIONS", "EROSION_ERODIBILITY", "EROSION_TOLERANCE",
    "EROSION_MIN_ITERATIONS",
    "RIVER_FLOW_THRESHOLD", "RIVER_MIN_LENGTH", "RIVER_WIDTH_THRESHOLD",
    "RIVER_WIDTH_MIN", "RIVER_WIDTH_MAX",
    "LAKE_MIN_PIXELS", "LAKE_WETLAND_DEPTH_MAX",
    "RAINSHADOW_DECAY_KM", "RAINSHADOW_SECONDARY_WEIGHT", "RAINSHADOW_MIN_FACTOR",
    "CONTINENTALITY_K", "CONTINENTALITY_D0_KM",
    "NOISE_FREQ_DERIVED",
    "CONTINENT_NOISE_OCTAVES", "CONTINENT_OUTLINE_OCTAVES",
    "CONTINENT_BLEND_WEIGHT", "TERRAIN_BLEND_WEIGHT", "CENTER_BIAS_WEIGHT",
    "SEA_LEVEL_ELEV", "OCEAN_COLD_CUTOFF", "OCEAN_WARM_CUTOFF", "OCEAN_DEEP_THRESHOLD",
    "CLIMATE_CALIB_RAINFALL_REF", "CLIMATE_CALIB_TEMP_MIN", "CLIMATE_CALIB_TEMP_MAX",
    "CLIMATE_CALIB_HOT_THRESHOLD", "CLIMATE_CALIB_COLD_RANGE",
    "CLIMATE_CALIB_HOT_RAINFALL_TARGET", "CLIMATE_CALIB_HOT_STRETCH_PARAM",
    "CLIMATE_CALIB_COLD_RAINFALL_TARGET", "CLIMATE_CALIB_COLD_STRETCH_PARAM",
    "ELEVATION_TARGET_P99",
    "LAPSE_RATE", "RAINFALL_MIN", "RAINFALL_MAX",
    "ALPINE_ALTITUDE", "POLAR_TEMP", "DESERT_RAINFALL", "STEPPE_RAINFALL",
    "STEPPE_MIN_TEMP", "TROPICAL_TEMP", "TEMPERATE_TEMP",
    "RAINFOREST_RAINFALL", "TAIGA_RAINFALL",
)

# 打包环境（生成管线源码缺失，无法哈希源码）的生成版本号：
# 发布时若生成算法/相关常量变化，递增此值——打包指纹随之变化。
# 开发环境指纹含源码哈希，日常无需维护；仅进发布清单（build/README.md）。
CONTINENT_GEN_VERSION: int = 1

# ═══════════════════════════════════════════════════════════════
# Content 覆盖 — 从 data/world.json 加载内容参数（Mod 第 1 层）
# ═══════════════════════════════════════════════════════════════
# 在全部常量定义之后执行：覆盖 World/Climate/Weather/Tile 内容参数，
# 保留本模块常量名与类型（35+ 处 `from config import X` 与 C 注入零改动）。
# 派生值（MOISTURE_TILE_FREQUENCY / SEASON_LENGTH / TICK_DT 等）不在
# 数据文件中，保持代码计算。加载失败（未知键/类型不匹配）import 期 fail fast。

from ascend.data import load_content as _load_content


def _coerce_content(name: str, value: object, existing: object) -> object:
    """把 JSON 值按既有常量类型转换；不匹配则报错（fail fast）。"""
    if isinstance(existing, bool):
        if not isinstance(value, bool):
            raise ValueError(f"config 内容 {name}: 需要 bool，got {value!r}")
        return value
    if isinstance(existing, int):
        if not isinstance(value, int) or isinstance(value, bool):
            raise ValueError(f"config 内容 {name}: 需要 int，got {value!r}")
        return value
    if isinstance(existing, float):
        if not isinstance(value, (int, float)) or isinstance(value, bool):
            raise ValueError(f"config 内容 {name}: 需要 float，got {value!r}")
        return float(value)
    if isinstance(existing, str):
        if not isinstance(value, str):
            raise ValueError(f"config 内容 {name}: 需要 str，got {value!r}")
        return value
    if isinstance(existing, tuple):
        if not isinstance(value, list):
            raise ValueError(f"config 内容 {name}: 需要数组，got {value!r}")
        return tuple(value)
    if isinstance(existing, dict):
        if not isinstance(value, dict):
            raise ValueError(f"config 内容 {name}: 需要对象，got {value!r}")
        return {
            k: (_coerce_content(f"{name}.{k}", v, existing[k])
                if k in existing else v)
            for k, v in value.items()
        }
    raise TypeError(f"config 内容 {name}: 不支持的类型 {type(existing)}")


def _apply_content(doc: dict) -> None:
    """把 data/world.json 的内容常量覆盖到模块全局。"""
    for section, values in doc.items():
        if section == "version":
            continue
        if not isinstance(values, dict):
            raise ValueError(f"data/world.json: 段 {section!r} 必须是对象")
        for name, value in values.items():
            if name not in globals():
                raise ValueError(f"data/world.json: 未知配置键 {name}（{section}）")
            existing = globals()[name]
            if callable(existing):
                raise ValueError(
                    f"data/world.json: 键 {name} 与函数/类同名，拒绝覆盖"
                )
            globals()[name] = _coerce_content(name, value, existing)


_apply_content(_load_content("world"))
