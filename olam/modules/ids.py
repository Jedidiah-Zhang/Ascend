"""天气/世界生成链节点与参数 ID — 单一事实源。

本模块集中定义节点/参数 ID 常量；引擎（``olam/adapters/weather``）、派生层与
区域追踪、各模块声明统一从这里取 ID。
"""

from __future__ import annotations

__all__ = [
    "CLOCK_TICK",
    "ANNUAL_TEMPERATURE",
    "ANNUAL_RAINFALL",
    "SEA_LEVEL_TEMPERATURE",
    "SOLAR_LATITUDE_PROXY",
    "SEASONAL_TEMPERATURE_AMPLITUDE",
    "DIURNAL_TEMPERATURE_AMPLITUDE",
    "SEASONAL_HUMIDITY_AMPLITUDE",
    "DIURNAL_HUMIDITY_AMPLITUDE",
    "PRECIPITATION_THRESHOLD",
    "BASELINE_HUMIDITY",
    "BASELINE_WIND_SPEED",
    "MEAN_PRECIP_INTENSITY",
    "HUMIDITY_SHARPNESS",
    "FIELD_TEMPERATURE_PERTURBATION",
    "FIELD_HUMIDITY_PERTURBATION",
    "FIELD_WIND_PERTURBATION",
    "FIELD_PRECIPITATION_SIGNAL",
    "FIELD_WIND_MULTIPLIER",
    "DAY",
    "DAY_OF_YEAR",
    "HOUR_OF_DAY",
    "SEASON",
    "SEASON_PHASE_COS",
    "DIURNAL_PHASE_COS",
    "SOLAR_DECLINATION",
    "SEASONAL_TEMPERATURE_OFFSET",
    "DIURNAL_TEMPERATURE_OFFSET",
    "SEASONAL_HUMIDITY_OFFSET",
    "DIURNAL_HUMIDITY_OFFSET",
    "SUNRISE_HOUR",
    "SUNSET_HOUR",
    "DAYLIGHT_HOURS",
    "INSTANT_TEMPERATURE",
    "INSTANT_HUMIDITY",
    "INSTANT_WIND_SPEED",
    "INSTANT_SUNSHINE",
    "INSTANT_PRECIPITATION_INTENSITY",
    "INSTANT_PRECIPITATION_TYPE",
    "_P_LAT_T_MIN",
    "_P_LAT_T_MAX",
    "_P_LAT_MIN",
    "_P_LAT_MAX",
    "_P_AMP_T_MIN",
    "_P_AMP_T_MAX",
    "_P_AMP_MAX",
    "_P_AMP_MIN",
    "_P_AMP_R_REF",
    "_P_AMP_R_BONUS",
    "_P_AMP_BOUND_MIN",
    "_P_AMP_BOUND_MAX",
    "_P_GAME_DAY",
    "_P_GAME_HOUR",
    "_P_DAYS_PER_YEAR",
    "_P_SEASON_LENGTH_DAYS",
    "_P_SEASONS_PER_YEAR",
    "_P_DIURNAL_PEAK_HOUR",
    "_P_OBLIQUITY_DEG",
    "_P_DIURNAL_TO_SEASONAL_RATIO",
    "_P_HUMIDITY_DIURNAL_SCALE",
    "_P_HUMIDITY_SEASONAL_SCALE",
    "_P_TEMP_PERTURB_SCALE",
    "_P_HUMIDITY_PERTURB_SCALE",
    "_P_WIND_PERTURB_SCALE",
    "_P_SUNSHINE_PERTURB_SCALE",
    "_P_TEMP_BOUND_LO",
    "_P_TEMP_BOUND_HI",
    "_P_HUMIDITY_BOUND_LO",
    "_P_HUMIDITY_BOUND_HI",
    "_P_WIND_BOUND_LO",
    "_P_WIND_BOUND_HI",
    "_P_SUNSHINE_BOUND_LO",
    "_P_SUNSHINE_BOUND_HI",
    "_P_PRECIP_SIGNAL_MAX",
    "_P_PRECIP_INTENSITY_SCALE",
    "_P_PRECIP_ANNUAL_DRY",
    "_P_PRECIP_ANNUAL_WET",
    "_P_PRECIP_THRESHOLD_DRY",
    "_P_PRECIP_THRESHOLD_WET",
]

CLOCK_TICK = "world.clock.tick"
ANNUAL_TEMPERATURE = "weather.chunk.annual_mean_temperature_c"
ANNUAL_RAINFALL = "weather.chunk.annual_rainfall_mm_per_year"
SEA_LEVEL_TEMPERATURE = "weather.chunk.sea_level_temperature_c"
SOLAR_LATITUDE_PROXY = "weather.chunk.solar_latitude_proxy_deg"
SEASONAL_TEMPERATURE_AMPLITUDE = "weather.chunk.seasonal_temperature_amplitude_c"
DIURNAL_TEMPERATURE_AMPLITUDE = "weather.chunk.diurnal_temperature_amplitude_c"
SEASONAL_HUMIDITY_AMPLITUDE = "weather.chunk.seasonal_humidity_amplitude_pp"
DIURNAL_HUMIDITY_AMPLITUDE = "weather.chunk.diurnal_humidity_amplitude_pp"
PRECIPITATION_THRESHOLD = "weather.chunk.precipitation_threshold"
BASELINE_HUMIDITY = "weather.chunk.baseline_humidity_percent"
BASELINE_WIND_SPEED = "weather.chunk.baseline_wind_speed_mps"
MEAN_PRECIP_INTENSITY = "weather.chunk.mean_precip_intensity_mm_per_hour"
HUMIDITY_SHARPNESS = "weather.chunk.humidity_sharpness"
FIELD_TEMPERATURE_PERTURBATION = "weather.field.temperature_perturbation"
FIELD_HUMIDITY_PERTURBATION = "weather.field.humidity_perturbation"
FIELD_WIND_PERTURBATION = "weather.field.wind_perturbation"
FIELD_PRECIPITATION_SIGNAL = "weather.field.precipitation_signal"
FIELD_WIND_MULTIPLIER = "weather.field.wind_multiplier"
DAY = "weather.tick.day"
DAY_OF_YEAR = "weather.tick.day_of_year"
HOUR_OF_DAY = "weather.tick.hour_of_day"
SEASON = "weather.tick.season"
SEASON_PHASE_COS = "weather.tick.season_phase_cos"
DIURNAL_PHASE_COS = "weather.tick.diurnal_phase_cos"
SOLAR_DECLINATION = "weather.tick.solar_declination_rad"
SEASONAL_TEMPERATURE_OFFSET = "weather.offset.seasonal_temperature_c"
DIURNAL_TEMPERATURE_OFFSET = "weather.offset.diurnal_temperature_c"
SEASONAL_HUMIDITY_OFFSET = "weather.offset.seasonal_humidity_pp"
DIURNAL_HUMIDITY_OFFSET = "weather.offset.diurnal_humidity_pp"
SUNRISE_HOUR = "weather.astronomy.sunrise_hour"
SUNSET_HOUR = "weather.astronomy.sunset_hour"
DAYLIGHT_HOURS = "weather.astronomy.daylight_hours"
INSTANT_TEMPERATURE = "weather.instant.temperature_c"
INSTANT_HUMIDITY = "weather.instant.relative_humidity_percent"
INSTANT_WIND_SPEED = "weather.instant.wind_speed_mps"
INSTANT_SUNSHINE = "weather.instant.sunshine_hours_per_day"
INSTANT_PRECIPITATION_INTENSITY = "weather.instant.precipitation_intensity_mm_per_hour"
INSTANT_PRECIPITATION_TYPE = "weather.instant.precipitation_type"
_P_LAT_T_MIN = "weather.parameter.latitude.input_min_c"
_P_LAT_T_MAX = "weather.parameter.latitude.input_max_c"
_P_LAT_MIN = "weather.parameter.latitude.output_min_deg"
_P_LAT_MAX = "weather.parameter.latitude.output_max_deg"
_P_AMP_T_MIN = "weather.parameter.seasonal_amplitude.input_min_c"
_P_AMP_T_MAX = "weather.parameter.seasonal_amplitude.input_max_c"
_P_AMP_MAX = "weather.parameter.seasonal_amplitude.cold_endpoint_c"
_P_AMP_MIN = "weather.parameter.seasonal_amplitude.hot_endpoint_c"
_P_AMP_R_REF = "weather.parameter.seasonal_amplitude.rain_reference_mm_per_year"
_P_AMP_R_BONUS = "weather.parameter.seasonal_amplitude.rain_bonus_c"
_P_AMP_BOUND_MIN = "weather.parameter.seasonal_amplitude.output_min_c"
_P_AMP_BOUND_MAX = "weather.parameter.seasonal_amplitude.output_max_c"
_P_GAME_DAY = "world.parameter.game_day_ticks"
_P_GAME_HOUR = "world.parameter.game_hour_ticks"
_P_DAYS_PER_YEAR = "world.parameter.days_per_year"
_P_SEASON_LENGTH_DAYS = "world.parameter.season_length_days"
_P_SEASONS_PER_YEAR = "world.parameter.seasons_per_year"
_P_DIURNAL_PEAK_HOUR = "world.parameter.diurnal_peak_hour"
_P_OBLIQUITY_DEG = "world.parameter.obliquity_deg"
_P_DIURNAL_TO_SEASONAL_RATIO = "world.parameter.diurnal_to_seasonal_ratio"
_P_HUMIDITY_DIURNAL_SCALE = "world.parameter.humidity_diurnal_scale"
_P_HUMIDITY_SEASONAL_SCALE = "world.parameter.humidity_seasonal_scale"
_P_TEMP_PERTURB_SCALE = "world.parameter.temp_perturb_scale_c"
_P_HUMIDITY_PERTURB_SCALE = "world.parameter.humidity_perturb_scale_pp"
_P_WIND_PERTURB_SCALE = "world.parameter.wind_perturb_scale_mps"
_P_SUNSHINE_PERTURB_SCALE = "world.parameter.sunshine_perturb_scale_h"
_P_TEMP_BOUND_LO = "world.parameter.temp_bound_min_c"
_P_TEMP_BOUND_HI = "world.parameter.temp_bound_max_c"
_P_HUMIDITY_BOUND_LO = "world.parameter.humidity_bound_min_pp"
_P_HUMIDITY_BOUND_HI = "world.parameter.humidity_bound_max_pp"
_P_WIND_BOUND_LO = "world.parameter.wind_bound_min_mps"
_P_WIND_BOUND_HI = "world.parameter.wind_bound_max_mps"
_P_SUNSHINE_BOUND_LO = "world.parameter.sunshine_bound_min_h"
_P_SUNSHINE_BOUND_HI = "world.parameter.sunshine_bound_max_h"
_P_PRECIP_SIGNAL_MAX = "world.parameter.precip_signal_max"
_P_PRECIP_INTENSITY_SCALE = "world.parameter.precip_intensity_scale"
_P_PRECIP_ANNUAL_DRY = "world.parameter.precip_annual_dry_mm"
_P_PRECIP_ANNUAL_WET = "world.parameter.precip_annual_wet_mm"
_P_PRECIP_THRESHOLD_DRY = "world.parameter.precip_threshold_dry"
_P_PRECIP_THRESHOLD_WET = "world.parameter.precip_threshold_wet"

LATITUDE_NOISE = "world.gen.latitude_noise"
RAINFALL_NOISE = "world.gen.rainfall_noise"
ALTITUDE = "world.gen.altitude_m"
HUMIDITY_NOISE = "world.gen.humidity_noise"
WIND_NOISE = "world.gen.wind_noise"
MOISTURE_NOISE = "world.gen.moisture_noise"
CLIMATE_ZONE = "world.gen.climate_zone"
BIOME = "world.gen.biome"
_P_LAPSE_RATE = "world.parameter.lapse_rate_c_per_1000m"
_P_RAINFALL_MIN = "world.parameter.rainfall_min_mm"
_P_RAINFALL_MAX = "world.parameter.rainfall_max_mm"
_P_ALPINE_ALTITUDE = "world.parameter.alpine_altitude_m"
_P_POLAR_TEMP = "world.parameter.polar_temp_c"
_P_DESERT_RAINFALL = "world.parameter.desert_rainfall_mm"
_P_STEPPE_RAINFALL = "world.parameter.steppe_rainfall_mm"
_P_STEPPE_MIN_TEMP = "world.parameter.steppe_min_temp_c"
_P_TROPICAL_TEMP = "world.parameter.tropical_temp_c"
_P_TEMPERATE_TEMP = "world.parameter.temperate_temp_c"
_P_RAINFOREST_RAINFALL = "world.parameter.rainforest_rainfall_mm"
_P_TAIGA_RAINFALL = "world.parameter.taiga_rainfall_mm"
_P_SEA_TEMP_SCALE = "world.parameter.sea_temperature_scale_c"
_P_SEA_TEMP_OFFSET = "world.parameter.sea_temperature_offset_c"
_P_SEA_TEMP_MIN = "world.parameter.sea_temperature_min_c"
_P_SEA_TEMP_MAX = "world.parameter.sea_temperature_max_c"
