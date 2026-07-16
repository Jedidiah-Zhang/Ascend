"""集中配置 — 所有可调参数的单一定义源。

按领域组织，各模块通过 `from ascend.config import XXX` 引用。
命令行参数 / 配置文件覆盖在未来版本中实现。

类别:
    Server    — 网络与连接
    Time      — 时间常量
    World     — 世界生成
    Climate   — 气候判定阈值
    Weather   — 天气参数
    Storage   — 持久化与缓存
    UI        — 终端与调试
"""

# ═══════════════════════════════════════════════════════════════
# Server — 网络与连接
# ═══════════════════════════════════════════════════════════════

SERVER_HOST: str = "127.0.0.1"
SERVER_PORT: int = 9081

MAX_MESSAGE_SIZE: int = 16 * 1024 * 1024  # 16 MiB

# 地图瓦片生成线程池大小
TILE_WORKERS: int = 4

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
LAKE_DEEP_AREA_KM2: float = 1.0       # 深水区面积阈值 (>1km²)
LAKE_DEEP_DEPTH_M: float = 3.0        # 深水区深度阈值 (m)
LAKE_WETLAND_DEPTH_MAX: float = 2.0   # 湿地边缘范围 (湖面以上 0-2m)

# 雨影参数
RAINSHADOW_DECAY_KM: float = 4.0      # 抬升衰减距离 (km)
RAINSHADOW_SECONDARY_WEIGHT: float = 0.2  # 次风向权重
RAINSHADOW_MIN_FACTOR: float = 0.15   # 最小雨影因子

# 大陆度系数
CONTINENTALITY_K: float = 3.0
CONTINENTALITY_D0_KM: float = 200.0

# 噪声频率
NOISE_FREQ_LATITUDE: float = 0.0003   # 纬度噪声（超低频，暖/冷带宽 ~3000 chunk）
NOISE_FREQ_RAINFALL: float = 0.004    # 降雨噪声（低频，区域降水模式）
NOISE_FREQ_DERIVED: float = 0.005     # 派生参数噪声（中频，日照/湿度/风速）

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

# 海拔校准
ELEVATION_TARGET_P99: float = 2500.0   # 陆地 P99 目标海拔

# ═══════════════════════════════════════════════════════════════
# Climate — 气候判定阈值
# ═══════════════════════════════════════════════════════════════

LAPSE_RATE: float = 9.0                # 气温直减率 (°C/1000m)，游戏性放大值

SEA_LEVEL_TEMP_MIN: float = -5.0       # 海平面温度下限（极地）
SEA_LEVEL_TEMP_MAX: float = 35.0       # 海平面温度上限（赤道）

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

# 全局大气场
ATMOSPHERE_RESOLUTION: float = 2000.0   # 大气噪声采样间距 (m)
ATMOSPHERE_DRIFT_RATE: float = 1e-5     # 气团漂移率（世界单位/tick）

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

# 降雨调度
RAIN_FORECAST_DEPTH: int = 4            # 预排未来 N 场雨
RAIN_REPLENISH_THRESHOLD: int = 2       # 低于 N 场时补算

# 天气修改器调度
MODIFIER_FORECAST_DEPTH: int = 2
MODIFIER_REPLENISH_THRESHOLD: int = 1

# per-parameter 事件发布阈值（已弃用，保留兼容性）
TEMP_CHANGE_THRESHOLD: float = 0.3      # 温度变化 (°C)
HUMIDITY_CHANGE_THRESHOLD: float = 1.5  # 湿度变化 (%)
WIND_CHANGE_THRESHOLD: float = 0.3      # 风速变化 (m/s)
SUNSHINE_CHANGE_THRESHOLD: float = 0.2  # 日照变化 (小时/天)

# 感知层天气分类阈值 — (上限, 标签)，按数值升序排列
# 事件仅在感知类别变化时发布，不再按固定数值间隔
TEMP_PERCEPTION_BOUNDARIES: tuple[tuple[float, str], ...] = (
    (-10.0, "bitter_cold"),
    (-3.0,  "freezing"),
    (5.0,   "cold"),
    (13.0,  "chilly"),
    (20.0,  "cool"),
    (25.0,  "mild"),
    (30.0,  "warm"),
    (36.0,  "hot"),
    (43.0,  "scorching"),
    (float("inf"), "extreme_heat"),
)

HUMIDITY_PERCEPTION_BOUNDARIES: tuple[tuple[float, str], ...] = (
    (25.0, "dry"),
    (50.0, "comfortable"),
    (72.0, "humid"),
    (88.0, "very_humid"),
    (float("inf"), "oppressive"),
)

WIND_PERCEPTION_BOUNDARIES: tuple[tuple[float, str], ...] = (
    (1.5,  "calm"),
    (4.0,  "light_breeze"),
    (8.0,  "breezy"),
    (14.0, "windy"),
    (23.0, "strong"),
    (float("inf"), "gale"),
)

SUNSHINE_PERCEPTION_BOUNDARIES: tuple[tuple[float, str], ...] = (
    (1.5,  "very_short"),
    (4.5,  "short"),
    (8.0,  "moderate"),
    (12.0, "long"),
    (15.5, "very_long"),
    (float("inf"), "extreme"),
)

# 日照强度感知分类 (0~1 归一化，0=黑夜 1=正午烈日)
SUNLIGHT_INTENSITY_BOUNDARIES: tuple[tuple[float, str], ...] = (
    (0.01, "dark"),
    (0.25, "dim"),
    (0.55, "moderate"),
    (0.80, "bright"),
    (float("inf"), "intense"),
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

# 基线地形分类阈值（温带落叶林基线，bias=0）
BASE_SAND_CAP: float = 10.0             # SAND 海拔上限 (m)
BASE_FERTILE_LO: float = 100.0          # FERTILE_SOIL 下限 (m)
BASE_FERTILE_HI: float = 300.0          # FERTILE_SOIL 上限 (m)
BASE_GRASSLAND_CAP: float = 600.0       # GRASSLAND 上限 (m)
BASE_ROCK_THRESHOLD: float = 600.0      # ROCK 起始海拔 (m)
BASE_PEAK_THRESHOLD: float = 2000.0     # MOUNTAIN_PEAK 起始海拔 (m)

STEEP_GRADIENT: float = 1.0             # 陡坡梯度阈值 (m/m)

# ═══════════════════════════════════════════════════════════════
# Storage — 持久化与缓存
# ═══════════════════════════════════════════════════════════════

# ChunkStore
CHUNK_STORE_DB_PATH: str = "save/chunks.db"
CHUNK_STORE_MAX_SIZE: int = 49          # LRU 缓存最大 chunk 数

# WorldTree 归档
WT_MAX_MEMORY_EVENTS: int = 100_000     # 内存最大事件数
WT_GRAPH_WARMUP_EVENTS: int = 10_000    # 图预热事件数
WT_ARCHIVE_PATH: str = "save/events.db"

# SQLite 性能参数
SQLITE_JOURNAL_MODE: str = "WAL"
SQLITE_SYNCHRONOUS: str = "NORMAL"
SQLITE_MMAP_SIZE: int = 268435456       # 256MB 内存映射
SQLITE_CACHE_SIZE: int = -8000          # 8MB 页缓存（负数 = KB）

# ═══════════════════════════════════════════════════════════════
# UI — 终端与调试
# ═══════════════════════════════════════════════════════════════

# 终端输出行限制
TERMINAL_OUTPUT_LINE_LIMIT: int = 500
TERMINAL_HISTORY_LIMIT: int = 100
