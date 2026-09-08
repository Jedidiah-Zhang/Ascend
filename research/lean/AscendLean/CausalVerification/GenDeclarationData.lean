import AscendLean.CausalVerification.Declarations

/-! AUTO-GENERATED — 本文件由工具生成，禁止手改。

生成器：research/equations/gen_lean.py（issue #44/#46 防漂移机制）
生成命令：.venv/bin/python research/equations/gen_lean.py
巡检命令：.venv/bin/python research/equations/gen_lean.py --check
巡检接入：research/equations/verify_equations.py 主流程 V0 步

来源与指纹（sha256 前 16 位）：
- research/equations/equations.json   sha256:dc7e2e241bfa9495
- backend/ascend/config.py            sha256:023c2028c5d2d2e6

防漂移三层闭环：
① 注册表快照/配置改动 → 数据段字面量/本头指纹变化 → --check 失败；
② 手改 Declarations.lean 实例 → 第四节对账定理失败 → lake build 红；
③ 边 L 与 config 解析斜率不一致 → 本文件新生成版本直接编译失败。

修复流程：跑生成命令刷新本文件，再于 research/lean 下执行
`~/.elan/bin/lake env lean AscendLean/CausalVerification/GenDeclarationData.lean`
确认对账定理仍绿；若红，说明手写侧与声明真值漂移，修手写侧。

角色边界：本文件只搬运**声明数据**并锚定其与手写镜像的一致性；
数学性质（界/单调/Lipschitz）的证明仍在手写的 Declarations.lean。 -/
namespace AscendLean.GenDeclarationData

open AscendLean.Declarations

-- ═══ 第一节 config 常量真值（backend/ascend/config.py）═══

-- 纬度推导 LATITUDE_*
def cfgLatTMin : ℝ := -5
def cfgLatTMax : ℝ := 35
def cfgLatMin : ℝ := 0
def cfgLatMax : ℝ := 80

-- 季节振幅推导 SEASONAL_AMP_*（BOUNDS 元组拆 LO/HI）
def cfgAmpTMin : ℝ := -5
def cfgAmpTMax : ℝ := 35
def cfgAmpMax : ℝ := 28
def cfgAmpMin : ℝ := 2
def cfgAmpRRef : ℝ := 2000
def cfgAmpRBonus : ℝ := 4
def cfgAmpBLo : ℝ := 1
def cfgAmpBHi : ℝ := 30

-- ═══ 第二节 声明边表（equations.json "edges"，保持声明原序）═══

-- weather.chunk.annual_mean_temperature_c → weather.chunk.seasonal_temperature_amplitude_c | role=structural | equation=weather.chunk.derive_seasonal_temperature_amplitude.v1
def edgeTemperatureSeasonalAmpL : ℝ := 0.65
-- weather.chunk.annual_rainfall_mm_per_year → weather.chunk.seasonal_temperature_amplitude_c | role=structural | equation=weather.chunk.derive_seasonal_temperature_amplitude.v1
def edgeRainfallSeasonalAmpL : ℝ := 0.002
-- weather.chunk.sea_level_temperature_c → weather.chunk.solar_latitude_proxy_deg | role=structural | equation=weather.chunk.derive_solar_latitude_proxy.v1
def edgeSeaLevelTempLatitudeL : ℝ := 2
-- weather.instant.temperature_c → weather.instant.precipitation_type | role=structural | equation=weather.instant.classify_precipitation_type.v1
def edgeTemperaturePrecipTypeL : ℝ := 0

-- ═══ 第三节 声明变量界（equations.json "variables" 的 bounds）═══

-- weather.chunk.annual_mean_temperature_c: bounds=[-30, 50]
def varWeatherChunkAnnualMeanTemperatureCLo : ℝ := -30
def varWeatherChunkAnnualMeanTemperatureCHi : ℝ := 50
-- weather.chunk.annual_rainfall_mm_per_year: bounds=[0, 5000]
def varRainfallLo : ℝ := 0
def varRainfallHi : ℝ := 5000
-- weather.chunk.sea_level_temperature_c: bounds=[-30, 50]
def varWeatherChunkSeaLevelTemperatureCLo : ℝ := -30
def varWeatherChunkSeaLevelTemperatureCHi : ℝ := 50
-- weather.chunk.seasonal_temperature_amplitude_c: bounds=[1, 30]
def varSeasonalAmpLo : ℝ := 1
def varSeasonalAmpHi : ℝ := 30
-- weather.chunk.solar_latitude_proxy_deg: bounds=[0, 80]
def varLatitudeLo : ℝ := 0
def varLatitudeHi : ℝ := 80
-- weather.instant.temperature_c: bounds=[-30, 50]
def varWeatherInstantTemperatureCLo : ℝ := -30
def varWeatherInstantTemperatureCHi : ℝ := 50

-- ═══ 第四节 对账定理（防漂移核心）═══

-- 形状统一为：手写 Declarations.lean 实例的相关量 = 本文件数据段字面量。
-- 任何一侧改动都会使本节某条定理失败（lake build 红）或触发 --check diff。
-- 协议耦合说明：本节模板引用 LatCfg/AmpCfg 的字段名，若手写侧重构字段，
-- 需同步修改 gen_lean.py 的对账模板。

-- 4.1 纬度斜率对账（V2 判据；声明 L=2）
theorem gen_latitude_L_matches :
    (latitudeConfig.latMax - latitudeConfig.latMin)
      / (latitudeConfig.tMax - latitudeConfig.tMin)
      = edgeSeaLevelTempLatitudeL := by
  show ((80:ℝ) - 0) / (35 - (-5)) = 2
  norm_num

-- 4.2 振幅温度向斜率对账（V2 判据；声明 L=0.65）
theorem gen_amp_L_temp_matches :
    (ampConfig.ampMax - ampConfig.ampMin)
      / (ampConfig.tMax - ampConfig.tMin)
      = edgeTemperatureSeasonalAmpL := by
  show ((28:ℝ) - 2) / (35 - (-5)) = 0.65
  norm_num

-- 4.3 振幅降雨向对账（V2 判据；声明 L=0.002）
theorem gen_amp_L_rain_matches :
    ampConfig.rBonus / ampConfig.rRef
      = edgeRainfallSeasonalAmpL := by
  show ((4:ℝ) / 2000 = 0.002)
  norm_num

-- 4.4 离散边退化对账（temperature->precip_type，01 篇 margin 条件处理）
theorem gen_precip_edge_L_zero : edgeTemperaturePrecipTypeL = 0 := by
  rfl

-- 4.5 纬度配置全字段对账：手写实例 ↔ config 真值 ↔ 声明 bounds 三方绑定
theorem gen_latitude_fields_match :
    latitudeConfig.tMin = cfgLatTMin ∧ latitudeConfig.tMax = cfgLatTMax
      ∧ latitudeConfig.latMin = cfgLatMin ∧ latitudeConfig.latMax = cfgLatMax
      ∧ latitudeConfig.latMin = varLatitudeLo ∧ latitudeConfig.latMax = varLatitudeHi := by
  refine ⟨?_, ?_, ?_, ?_, ?_, ?_⟩ <;> rfl

-- 4.6 振幅配置全字段对账：手写实例 ↔ config 真值 ↔ 声明 bounds 三方绑定
theorem gen_amp_fields_match :
    ampConfig.tMin = cfgAmpTMin ∧ ampConfig.tMax = cfgAmpTMax
      ∧ ampConfig.ampMax = cfgAmpMax ∧ ampConfig.ampMin = cfgAmpMin
      ∧ ampConfig.rRef = cfgAmpRRef ∧ ampConfig.rBonus = cfgAmpRBonus
      ∧ ampConfig.bLo = cfgAmpBLo ∧ ampConfig.bHi = cfgAmpBHi
      ∧ ampConfig.bLo = varSeasonalAmpLo ∧ ampConfig.bHi = varSeasonalAmpHi := by
  refine ⟨?_, ?_, ?_, ?_, ?_, ?_, ?_, ?_, ?_, ?_⟩ <;> rfl

-- 4.7 声明 bounds 良序（防 bounds 写反；rainfall 无手写镜像，仅自洽）
theorem gen_bounds_wellformed :
    varLatitudeLo ≤ varLatitudeHi ∧ varRainfallLo ≤ varRainfallHi
      ∧ varSeasonalAmpLo ≤ varSeasonalAmpHi := by
  refine ⟨?_, ?_, ?_⟩ <;>
    norm_num [varLatitudeLo, varLatitudeHi,
      varRainfallLo, varRainfallHi, varSeasonalAmpLo, varSeasonalAmpHi]

end AscendLean.GenDeclarationData
