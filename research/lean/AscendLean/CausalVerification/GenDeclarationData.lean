import AscendLean.CausalVerification.Declarations

/-! AUTO-GENERATED — 本文件由工具生成，禁止手改。

生成器：research/equations/gen_lean.py（issue #44/#46 防漂移机制）
生成命令：.venv/bin/python research/equations/gen_lean.py
巡检命令：.venv/bin/python research/equations/gen_lean.py --check
巡检接入：research/equations/verify_equations.py 主流程 V0 步

来源与指纹（sha256 前 16 位）：
- research/equations/equations.json   sha256:0bbbb800a1f22fb9
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

-- weather.astronomy.sunrise_hour → weather.astronomy.daylight_hours | role=structural | equation=weather.astronomy.derive_daylight.v1
def edgeWeatherAstronomySunriseHourWeatherAstronomyDaylightHoursL : ℝ := 1
-- weather.astronomy.sunset_hour → weather.astronomy.daylight_hours | role=structural | equation=weather.astronomy.derive_daylight.v1
def edgeWeatherAstronomySunsetHourWeatherAstronomyDaylightHoursL : ℝ := 1
-- weather.chunk.solar_latitude_proxy_deg → weather.astronomy.sunrise_hour | role=structural | equation=weather.astronomy.derive_sunrise.v1
def edgeWeatherChunkSolarLatitudeProxyDegWeatherAstronomySunriseHourL : ℝ := 0.5
-- weather.tick.solar_declination_rad → weather.astronomy.sunrise_hour | role=structural | equation=weather.astronomy.derive_sunrise.v1
def edgeWeatherTickSolarDeclinationRadWeatherAstronomySunriseHourL : ℝ := 1
-- weather.chunk.solar_latitude_proxy_deg → weather.astronomy.sunset_hour | role=structural | equation=weather.astronomy.derive_sunset.v1
def edgeWeatherChunkSolarLatitudeProxyDegWeatherAstronomySunsetHourL : ℝ := 0.5
-- weather.tick.solar_declination_rad → weather.astronomy.sunset_hour | role=structural | equation=weather.astronomy.derive_sunset.v1
def edgeWeatherTickSolarDeclinationRadWeatherAstronomySunsetHourL : ℝ := 1
-- weather.chunk.seasonal_temperature_amplitude_c → weather.chunk.diurnal_humidity_amplitude_pp | role=structural | equation=weather.chunk.derive_diurnal_humidity_amplitude.v1
def edgeWeatherChunkSeasonalTemperatureAmplitudeCWeatherChunkDiurnalHumidityAmplitudePpL : ℝ := 0.4
-- weather.chunk.seasonal_temperature_amplitude_c → weather.chunk.diurnal_temperature_amplitude_c | role=structural | equation=weather.chunk.derive_diurnal_temperature_amplitude.v1
def edgeWeatherChunkSeasonalTemperatureAmplitudeCWeatherChunkDiurnalTemperatureAmplitudeCL : ℝ := 0.5
-- weather.chunk.annual_rainfall_mm_per_year → weather.chunk.precipitation_threshold | role=structural | equation=weather.chunk.derive_precipitation_threshold.v1
def edgeWeatherChunkAnnualRainfallMmPerYearWeatherChunkPrecipitationThresholdL : ℝ := 0
-- weather.chunk.seasonal_temperature_amplitude_c → weather.chunk.seasonal_humidity_amplitude_pp | role=structural | equation=weather.chunk.derive_seasonal_humidity_amplitude.v1
def edgeWeatherChunkSeasonalTemperatureAmplitudeCWeatherChunkSeasonalHumidityAmplitudePpL : ℝ := 0.4
-- weather.chunk.annual_mean_temperature_c → weather.chunk.seasonal_temperature_amplitude_c | role=structural | equation=weather.chunk.derive_seasonal_temperature_amplitude.v1
def edgeTemperatureSeasonalAmpL : ℝ := 0.65
-- weather.chunk.annual_rainfall_mm_per_year → weather.chunk.seasonal_temperature_amplitude_c | role=structural | equation=weather.chunk.derive_seasonal_temperature_amplitude.v1
def edgeRainfallSeasonalAmpL : ℝ := 0.002
-- weather.chunk.sea_level_temperature_c → weather.chunk.solar_latitude_proxy_deg | role=structural | equation=weather.chunk.derive_solar_latitude_proxy.v1
def edgeSeaLevelTempLatitudeL : ℝ := 2
-- weather.instant.temperature_c → weather.instant.precipitation_type | role=structural | equation=weather.instant.classify_precipitation_type.v1
def edgeTemperaturePrecipTypeL : ℝ := 0
-- weather.chunk.baseline_humidity_percent → weather.instant.relative_humidity_percent | role=structural | equation=weather.instant.compose_humidity.v1
def edgeWeatherChunkBaselineHumidityPercentWeatherInstantRelativeHumidityPercentL : ℝ := 1
-- weather.offset.seasonal_humidity_pp → weather.instant.relative_humidity_percent | role=structural | equation=weather.instant.compose_humidity.v1
def edgeWeatherOffsetSeasonalHumidityPpWeatherInstantRelativeHumidityPercentL : ℝ := 1
-- weather.offset.diurnal_humidity_pp → weather.instant.relative_humidity_percent | role=structural | equation=weather.instant.compose_humidity.v1
def edgeWeatherOffsetDiurnalHumidityPpWeatherInstantRelativeHumidityPercentL : ℝ := 1
-- weather.field.humidity_perturbation → weather.instant.relative_humidity_percent | role=structural | equation=weather.instant.compose_humidity.v1
def edgeWeatherFieldHumidityPerturbationWeatherInstantRelativeHumidityPercentL : ℝ := 15
-- weather.field.precipitation_signal → weather.instant.precipitation_intensity_mm_per_hour | role=structural | equation=weather.instant.compose_precipitation_intensity.v1
def edgeWeatherFieldPrecipitationSignalWeatherInstantPrecipitationIntensityMmPerHourL : ℝ := 0
-- weather.chunk.precipitation_threshold → weather.instant.precipitation_intensity_mm_per_hour | role=structural | equation=weather.instant.compose_precipitation_intensity.v1
def edgeWeatherChunkPrecipitationThresholdWeatherInstantPrecipitationIntensityMmPerHourL : ℝ := 0
-- weather.chunk.mean_precip_intensity_mm_per_hour → weather.instant.precipitation_intensity_mm_per_hour | role=structural | equation=weather.instant.compose_precipitation_intensity.v1
def edgeWeatherChunkMeanPrecipIntensityMmPerHourWeatherInstantPrecipitationIntensityMmPerHourL : ℝ := 0
-- weather.astronomy.daylight_hours → weather.instant.sunshine_hours_per_day | role=structural | equation=weather.instant.compose_sunshine.v1
def edgeWeatherAstronomyDaylightHoursWeatherInstantSunshineHoursPerDayL : ℝ := 1
-- weather.field.humidity_perturbation → weather.instant.sunshine_hours_per_day | role=structural | equation=weather.instant.compose_sunshine.v1
def edgeWeatherFieldHumidityPerturbationWeatherInstantSunshineHoursPerDayL : ℝ := 1.5
-- weather.chunk.annual_mean_temperature_c → weather.instant.temperature_c | role=structural | equation=weather.instant.compose_temperature.v1
def edgeWeatherChunkAnnualMeanTemperatureCWeatherInstantTemperatureCL : ℝ := 1
-- weather.offset.seasonal_temperature_c → weather.instant.temperature_c | role=structural | equation=weather.instant.compose_temperature.v1
def edgeWeatherOffsetSeasonalTemperatureCWeatherInstantTemperatureCL : ℝ := 1
-- weather.offset.diurnal_temperature_c → weather.instant.temperature_c | role=structural | equation=weather.instant.compose_temperature.v1
def edgeWeatherOffsetDiurnalTemperatureCWeatherInstantTemperatureCL : ℝ := 1
-- weather.field.temperature_perturbation → weather.instant.temperature_c | role=structural | equation=weather.instant.compose_temperature.v1
def edgeWeatherFieldTemperaturePerturbationWeatherInstantTemperatureCL : ℝ := 5
-- weather.chunk.baseline_wind_speed_mps → weather.instant.wind_speed_mps | role=structural | equation=weather.instant.compose_wind_speed.v1
def edgeWeatherChunkBaselineWindSpeedMpsWeatherInstantWindSpeedMpsL : ℝ := 1
-- weather.field.wind_perturbation → weather.instant.wind_speed_mps | role=structural | equation=weather.instant.compose_wind_speed.v1
def edgeWeatherFieldWindPerturbationWeatherInstantWindSpeedMpsL : ℝ := 4
-- weather.field.wind_multiplier → weather.instant.wind_speed_mps | role=structural | equation=weather.instant.compose_wind_speed.v1
def edgeWeatherFieldWindMultiplierWeatherInstantWindSpeedMpsL : ℝ := 0
-- weather.chunk.diurnal_humidity_amplitude_pp → weather.offset.diurnal_humidity_pp | role=structural | equation=weather.offset.derive_diurnal_humidity.v1
def edgeWeatherChunkDiurnalHumidityAmplitudePpWeatherOffsetDiurnalHumidityPpL : ℝ := 1
-- weather.tick.diurnal_phase_cos → weather.offset.diurnal_humidity_pp | role=structural | equation=weather.offset.derive_diurnal_humidity.v1
def edgeWeatherTickDiurnalPhaseCosWeatherOffsetDiurnalHumidityPpL : ℝ := 20
-- weather.chunk.diurnal_temperature_amplitude_c → weather.offset.diurnal_temperature_c | role=structural | equation=weather.offset.derive_diurnal_temperature.v1
def edgeWeatherChunkDiurnalTemperatureAmplitudeCWeatherOffsetDiurnalTemperatureCL : ℝ := 1
-- weather.tick.diurnal_phase_cos → weather.offset.diurnal_temperature_c | role=structural | equation=weather.offset.derive_diurnal_temperature.v1
def edgeWeatherTickDiurnalPhaseCosWeatherOffsetDiurnalTemperatureCL : ℝ := 20
-- weather.chunk.seasonal_humidity_amplitude_pp → weather.offset.seasonal_humidity_pp | role=structural | equation=weather.offset.derive_seasonal_humidity.v1
def edgeWeatherChunkSeasonalHumidityAmplitudePpWeatherOffsetSeasonalHumidityPpL : ℝ := 1
-- weather.tick.season_phase_cos → weather.offset.seasonal_humidity_pp | role=structural | equation=weather.offset.derive_seasonal_humidity.v1
def edgeWeatherTickSeasonPhaseCosWeatherOffsetSeasonalHumidityPpL : ℝ := 20
-- weather.chunk.humidity_sharpness → weather.offset.seasonal_humidity_pp | role=structural | equation=weather.offset.derive_seasonal_humidity.v1
def edgeWeatherChunkHumiditySharpnessWeatherOffsetSeasonalHumidityPpL : ℝ := 0
-- weather.chunk.seasonal_temperature_amplitude_c → weather.offset.seasonal_temperature_c | role=structural | equation=weather.offset.derive_seasonal_temperature.v1
def edgeWeatherChunkSeasonalTemperatureAmplitudeCWeatherOffsetSeasonalTemperatureCL : ℝ := 1
-- weather.tick.season_phase_cos → weather.offset.seasonal_temperature_c | role=structural | equation=weather.offset.derive_seasonal_temperature.v1
def edgeWeatherTickSeasonPhaseCosWeatherOffsetSeasonalTemperatureCL : ℝ := 30
-- world.clock.tick → weather.tick.day | role=structural | equation=weather.tick.derive_day.v1
def edgeWorldClockTickWeatherTickDayL : ℝ := 0
-- world.clock.tick → weather.tick.day_of_year | role=structural | equation=weather.tick.derive_day_of_year.v1
def edgeWorldClockTickWeatherTickDayOfYearL : ℝ := 0
-- weather.tick.hour_of_day → weather.tick.diurnal_phase_cos | role=structural | equation=weather.tick.derive_diurnal_phase_cos.v1
def edgeWeatherTickHourOfDayWeatherTickDiurnalPhaseCosL : ℝ := 0
-- world.clock.tick → weather.tick.hour_of_day | role=structural | equation=weather.tick.derive_hour_of_day.v1
def edgeWorldClockTickWeatherTickHourOfDayL : ℝ := 0
-- weather.tick.day → weather.tick.season | role=structural | equation=weather.tick.derive_season.v1
def edgeWeatherTickDayWeatherTickSeasonL : ℝ := 0
-- weather.tick.day → weather.tick.season_phase_cos | role=structural | equation=weather.tick.derive_season_phase_cos.v1
def edgeWeatherTickDayWeatherTickSeasonPhaseCosL : ℝ := 0
-- weather.tick.day_of_year → weather.tick.solar_declination_rad | role=structural | equation=weather.tick.derive_solar_declination.v1
def edgeWeatherTickDayOfYearWeatherTickSolarDeclinationRadL : ℝ := 0
-- weather.chunk.annual_mean_temperature_c → world.gen.biome | role=structural | equation=world.gen.classify_biome.v1
def edgeWeatherChunkAnnualMeanTemperatureCWorldGenBiomeL : ℝ := 0
-- weather.chunk.annual_rainfall_mm_per_year → world.gen.biome | role=structural | equation=world.gen.classify_biome.v1
def edgeWeatherChunkAnnualRainfallMmPerYearWorldGenBiomeL : ℝ := 0
-- world.gen.altitude_m → world.gen.biome | role=structural | equation=world.gen.classify_biome.v1
def edgeWorldGenAltitudeMWorldGenBiomeL : ℝ := 0
-- weather.chunk.sea_level_temperature_c → world.gen.biome | role=structural | equation=world.gen.classify_biome.v1
def edgeWeatherChunkSeaLevelTemperatureCWorldGenBiomeL : ℝ := 0
-- world.gen.moisture_noise → world.gen.biome | role=structural | equation=world.gen.classify_biome.v1
def edgeWorldGenMoistureNoiseWorldGenBiomeL : ℝ := 0
-- weather.chunk.annual_mean_temperature_c → world.gen.climate_zone | role=structural | equation=world.gen.classify_climate_zone.v1
def edgeWeatherChunkAnnualMeanTemperatureCWorldGenClimateZoneL : ℝ := 0
-- weather.chunk.annual_rainfall_mm_per_year → world.gen.climate_zone | role=structural | equation=world.gen.classify_climate_zone.v1
def edgeWeatherChunkAnnualRainfallMmPerYearWorldGenClimateZoneL : ℝ := 0
-- world.gen.altitude_m → world.gen.climate_zone | role=structural | equation=world.gen.classify_climate_zone.v1
def edgeWorldGenAltitudeMWorldGenClimateZoneL : ℝ := 0
-- weather.chunk.sea_level_temperature_c → weather.chunk.annual_mean_temperature_c | role=structural | equation=world.gen.derive_annual_mean_temperature.v1
def edgeWeatherChunkSeaLevelTemperatureCWeatherChunkAnnualMeanTemperatureCL : ℝ := 1
-- world.gen.altitude_m → weather.chunk.annual_mean_temperature_c | role=structural | equation=world.gen.derive_annual_mean_temperature.v1
def edgeWorldGenAltitudeMWeatherChunkAnnualMeanTemperatureCL : ℝ := 0.009
-- world.gen.rainfall_noise → weather.chunk.annual_rainfall_mm_per_year | role=structural | equation=world.gen.derive_annual_rainfall.v1
def edgeWorldGenRainfallNoiseWeatherChunkAnnualRainfallMmPerYearL : ℝ := 1725
-- world.gen.climate_zone → weather.chunk.baseline_humidity_percent | role=structural | equation=world.gen.derive_baseline_humidity.v1
def edgeWorldGenClimateZoneWeatherChunkBaselineHumidityPercentL : ℝ := 0
-- world.gen.humidity_noise → weather.chunk.baseline_humidity_percent | role=structural | equation=world.gen.derive_baseline_humidity.v1
def edgeWorldGenHumidityNoiseWeatherChunkBaselineHumidityPercentL : ℝ := 0
-- world.gen.climate_zone → weather.chunk.baseline_wind_speed_mps | role=structural | equation=world.gen.derive_baseline_wind_speed.v1
def edgeWorldGenClimateZoneWeatherChunkBaselineWindSpeedMpsL : ℝ := 0
-- world.gen.wind_noise → weather.chunk.baseline_wind_speed_mps | role=structural | equation=world.gen.derive_baseline_wind_speed.v1
def edgeWorldGenWindNoiseWeatherChunkBaselineWindSpeedMpsL : ℝ := 0
-- world.gen.climate_zone → weather.chunk.humidity_sharpness | role=structural | equation=world.gen.derive_humidity_sharpness.v1
def edgeWorldGenClimateZoneWeatherChunkHumiditySharpnessL : ℝ := 0
-- world.gen.climate_zone → weather.chunk.mean_precip_intensity_mm_per_hour | role=structural | equation=world.gen.derive_mean_precip_intensity.v1
def edgeWorldGenClimateZoneWeatherChunkMeanPrecipIntensityMmPerHourL : ℝ := 0
-- world.gen.latitude_noise → weather.chunk.sea_level_temperature_c | role=structural | equation=world.gen.derive_sea_level_temperature.v1
def edgeWorldGenLatitudeNoiseWeatherChunkSeaLevelTemperatureCL : ℝ := 25

-- ═══ 第三节 声明变量界（equations.json "variables" 的 bounds）═══

-- weather.astronomy.daylight_hours: bounds=[0, 24]
def varWeatherAstronomyDaylightHoursLo : ℝ := 0
def varWeatherAstronomyDaylightHoursHi : ℝ := 24
-- weather.astronomy.sunrise_hour: bounds=[0, 12]
def varWeatherAstronomySunriseHourLo : ℝ := 0
def varWeatherAstronomySunriseHourHi : ℝ := 12
-- weather.astronomy.sunset_hour: bounds=[12, 24]
def varWeatherAstronomySunsetHourLo : ℝ := 12
def varWeatherAstronomySunsetHourHi : ℝ := 24
-- weather.chunk.baseline_humidity_percent: bounds=[0, 100]
def varWeatherChunkBaselineHumidityPercentLo : ℝ := 0
def varWeatherChunkBaselineHumidityPercentHi : ℝ := 100
-- weather.chunk.baseline_wind_speed_mps: bounds=[0, 50]
def varWeatherChunkBaselineWindSpeedMpsLo : ℝ := 0
def varWeatherChunkBaselineWindSpeedMpsHi : ℝ := 50
-- weather.chunk.diurnal_humidity_amplitude_pp: bounds=[0, 20]
def varWeatherChunkDiurnalHumidityAmplitudePpLo : ℝ := 0
def varWeatherChunkDiurnalHumidityAmplitudePpHi : ℝ := 20
-- weather.chunk.diurnal_temperature_amplitude_c: bounds=[0, 20]
def varWeatherChunkDiurnalTemperatureAmplitudeCLo : ℝ := 0
def varWeatherChunkDiurnalTemperatureAmplitudeCHi : ℝ := 20
-- weather.chunk.humidity_sharpness: bounds=[0, 10]
def varWeatherChunkHumiditySharpnessLo : ℝ := 0
def varWeatherChunkHumiditySharpnessHi : ℝ := 10
-- weather.chunk.mean_precip_intensity_mm_per_hour: bounds=[0, 100]
def varWeatherChunkMeanPrecipIntensityMmPerHourLo : ℝ := 0
def varWeatherChunkMeanPrecipIntensityMmPerHourHi : ℝ := 100
-- weather.chunk.precipitation_threshold: bounds=[0.25, 0.55]
def varWeatherChunkPrecipitationThresholdLo : ℝ := 0.25
def varWeatherChunkPrecipitationThresholdHi : ℝ := 0.55
-- weather.chunk.sea_level_temperature_c: bounds=[-20, 38]
def varWeatherChunkSeaLevelTemperatureCLo : ℝ := -20
def varWeatherChunkSeaLevelTemperatureCHi : ℝ := 38
-- weather.chunk.seasonal_humidity_amplitude_pp: bounds=[0, 20]
def varWeatherChunkSeasonalHumidityAmplitudePpLo : ℝ := 0
def varWeatherChunkSeasonalHumidityAmplitudePpHi : ℝ := 20
-- weather.chunk.seasonal_temperature_amplitude_c: bounds=[1, 30]
def varSeasonalAmpLo : ℝ := 1
def varSeasonalAmpHi : ℝ := 30
-- weather.chunk.solar_latitude_proxy_deg: bounds=[0, 80]
def varLatitudeLo : ℝ := 0
def varLatitudeHi : ℝ := 80
-- weather.field.humidity_perturbation: bounds=[-2, 2]
def varWeatherFieldHumidityPerturbationLo : ℝ := -2
def varWeatherFieldHumidityPerturbationHi : ℝ := 2
-- weather.field.precipitation_signal: bounds=[0, 10]
def varWeatherFieldPrecipitationSignalLo : ℝ := 0
def varWeatherFieldPrecipitationSignalHi : ℝ := 10
-- weather.field.temperature_perturbation: bounds=[-20, 20]
def varWeatherFieldTemperaturePerturbationLo : ℝ := -20
def varWeatherFieldTemperaturePerturbationHi : ℝ := 20
-- weather.field.wind_multiplier: bounds=[1, 100]
def varWeatherFieldWindMultiplierLo : ℝ := 1
def varWeatherFieldWindMultiplierHi : ℝ := 100
-- weather.field.wind_perturbation: bounds=[-2, 2]
def varWeatherFieldWindPerturbationLo : ℝ := -2
def varWeatherFieldWindPerturbationHi : ℝ := 2
-- weather.instant.precipitation_intensity_mm_per_hour: bounds=[0, 100]
def varWeatherInstantPrecipitationIntensityMmPerHourLo : ℝ := 0
def varWeatherInstantPrecipitationIntensityMmPerHourHi : ℝ := 100
-- weather.instant.relative_humidity_percent: bounds=[0, 100]
def varWeatherInstantRelativeHumidityPercentLo : ℝ := 0
def varWeatherInstantRelativeHumidityPercentHi : ℝ := 100
-- weather.instant.sunshine_hours_per_day: bounds=[0, 24]
def varWeatherInstantSunshineHoursPerDayLo : ℝ := 0
def varWeatherInstantSunshineHoursPerDayHi : ℝ := 24
-- weather.instant.temperature_c: bounds=[-30, 50]
def varWeatherInstantTemperatureCLo : ℝ := -30
def varWeatherInstantTemperatureCHi : ℝ := 50
-- weather.instant.wind_speed_mps: bounds=[0, 50]
def varWeatherInstantWindSpeedMpsLo : ℝ := 0
def varWeatherInstantWindSpeedMpsHi : ℝ := 50
-- weather.offset.diurnal_humidity_pp: bounds=[-20, 20]
def varWeatherOffsetDiurnalHumidityPpLo : ℝ := -20
def varWeatherOffsetDiurnalHumidityPpHi : ℝ := 20
-- weather.offset.diurnal_temperature_c: bounds=[-20, 20]
def varWeatherOffsetDiurnalTemperatureCLo : ℝ := -20
def varWeatherOffsetDiurnalTemperatureCHi : ℝ := 20
-- weather.offset.seasonal_humidity_pp: bounds=[-20, 20]
def varWeatherOffsetSeasonalHumidityPpLo : ℝ := -20
def varWeatherOffsetSeasonalHumidityPpHi : ℝ := 20
-- weather.offset.seasonal_temperature_c: bounds=[-30, 30]
def varWeatherOffsetSeasonalTemperatureCLo : ℝ := -30
def varWeatherOffsetSeasonalTemperatureCHi : ℝ := 30
-- weather.tick.day_of_year: bounds=[0, 360]
def varWeatherTickDayOfYearLo : ℝ := 0
def varWeatherTickDayOfYearHi : ℝ := 360
-- weather.tick.diurnal_phase_cos: bounds=[-1, 1]
def varWeatherTickDiurnalPhaseCosLo : ℝ := -1
def varWeatherTickDiurnalPhaseCosHi : ℝ := 1
-- weather.tick.hour_of_day: bounds=[0, 24]
def varWeatherTickHourOfDayLo : ℝ := 0
def varWeatherTickHourOfDayHi : ℝ := 24
-- weather.tick.season_phase_cos: bounds=[-1, 1]
def varWeatherTickSeasonPhaseCosLo : ℝ := -1
def varWeatherTickSeasonPhaseCosHi : ℝ := 1
-- weather.tick.solar_declination_rad: bounds=[-0.5, 0.5]
def varWeatherTickSolarDeclinationRadLo : ℝ := -0.5
def varWeatherTickSolarDeclinationRadHi : ℝ := 0.5
-- world.gen.humidity_noise: bounds=[-1, 1]
def varWorldGenHumidityNoiseLo : ℝ := -1
def varWorldGenHumidityNoiseHi : ℝ := 1
-- world.gen.latitude_noise: bounds=[-1, 1]
def varWorldGenLatitudeNoiseLo : ℝ := -1
def varWorldGenLatitudeNoiseHi : ℝ := 1
-- world.gen.moisture_noise: bounds=[-1, 1]
def varWorldGenMoistureNoiseLo : ℝ := -1
def varWorldGenMoistureNoiseHi : ℝ := 1
-- world.gen.rainfall_noise: bounds=[-1, 1]
def varWorldGenRainfallNoiseLo : ℝ := -1
def varWorldGenRainfallNoiseHi : ℝ := 1
-- world.gen.wind_noise: bounds=[-1, 1]
def varWorldGenWindNoiseLo : ℝ := -1
def varWorldGenWindNoiseHi : ℝ := 1

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

-- 4.7 声明 bounds 良序（防 bounds 写反；仅覆盖有界声明变量）
theorem gen_bounds_wellformed :
    varLatitudeLo ≤ varLatitudeHi ∧ varSeasonalAmpLo ≤ varSeasonalAmpHi := by
  refine ⟨?_, ?_⟩ <;>
    norm_num [varLatitudeLo, varLatitudeHi, varSeasonalAmpLo, varSeasonalAmpHi]

end AscendLean.GenDeclarationData
