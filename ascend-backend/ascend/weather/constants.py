"""天气常量 — 解析算参数、大气场分辨率、季节划分、事件阈值。

天气参数解析算（无快照）：
  - 温度 = baseline + 季节偏移 + 昼夜偏移 + 大气扰动（每刻连续）
  - 湿度/风速 = baseline + 大气扰动
  - 降雨 = 事件调度（RainSchedule）
"""

from ascend.time.constants import GAME_DAY, GAME_HOUR, GAME_YEAR

# ── 全局大气场 ──────────────────────────────────────────────

# 采样间距（世界坐标单位，1 单位 = 1m）：2km → 一个大气特征覆盖 10×10 chunks
ATMOSPHERE_RESOLUTION: float = 2000.0

# 气团漂移率：每 tick 噪声坐标沿风向漂移的世界坐标量
ATMOSPHERE_DRIFT_RATE: float = 1e-5

# ── 季节 ────────────────────────────────────────────────────

SEASONS_PER_YEAR: int = 4

# 季节长度（天）= 年天数(360) / 4 = 90
SEASON_LENGTH_DAYS: int = (GAME_YEAR // GAME_DAY) // SEASONS_PER_YEAR

# 季节长度（tick）= 季节天数 × 游戏日 = 15552000
SEASON_LENGTH: int = SEASON_LENGTH_DAYS * GAME_DAY

# ── 昼夜曲线 ────────────────────────────────────────────────

DIURNAL_PEAK_HOUR: int = 14      # 一天中最热时刻
DIURNAL_TROUGH_HOUR: int = 2     # 一天中最冷时刻

# 日出/日落时刻（小时，首期固定；后续可随季节+纬度浮动）
SUNRISE_HOUR: int = 6
SUNSET_HOUR: int = 18

# ── 大气扰动缩放 ────────────────────────────────────────────
# AtmosphereField.sample 返回 [-1, 1]，乘以下列系数得各参数扰动幅度

TEMP_PERTURB_SCALE: float = 5.0        # 温度 ±5°C
HUMIDITY_PERTURB_SCALE: float = 15.0   # 湿度 ±15%
WIND_PERTURB_SCALE: float = 4.0        # 风速 ±4 m/s

# 昼夜振幅 = 季节振幅 × 此系数（昼夜变化小于季节变化）
DIURNAL_TO_SEASONAL_RATIO: float = 0.5

# 日照大气扰动缩放 — AtmosphereField.sample [-1,1] × 此系数得日照扰动幅度（小时）
# 体现云量随机变化：多云地区日照减少，晴朗地区日照增加
SUNSHINE_PERTURB_SCALE: float = 1.5       # 日照 ±1.5 小时

# 湿度偏移缩放 — 分别作用于昼夜和季节温度振幅，得湿度振幅 (pp)
# 湿度昼夜偏移反比于温度（02:00 最高，14:00 最低）；季节偏移同向（夏湿冬干）
HUMIDITY_DIURNAL_SCALE: float = 0.8
HUMIDITY_SEASONAL_SCALE: float = 0.4

# ── 降雨事件调度 ────────────────────────────────────────────

RAIN_FORECAST_DEPTH: int = 4        # 预排未来 4 场雨
RAIN_REPLENISH_THRESHOLD: int = 2   # 低于 2 场时补算

# ── per-parameter 事件发布阈值 ─────────────────────────────
# 每 tick 检查各参数变化，超阈值才发对应事件（控制事件频率）

TEMP_CHANGE_THRESHOLD: float = 0.3       # °C
HUMIDITY_CHANGE_THRESHOLD: float = 1.5   # %
WIND_CHANGE_THRESHOLD: float = 0.3       # m/s
SUNSHINE_CHANGE_THRESHOLD: float = 0.2   # 小时/天
