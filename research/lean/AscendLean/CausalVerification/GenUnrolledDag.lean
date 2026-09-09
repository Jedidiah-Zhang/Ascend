import AscendLean.CausalVerification.UnrolledDag

/-! AUTO-GENERATED — 本文件由工具生成，禁止手改。

生成器：research/acceptance/gen_unrolled_dag.py（issue #46 P5）
生成命令：.venv/bin/python research/acceptance/gen_unrolled_dag.py
巡检命令：.venv/bin/python research/acceptance/gen_unrolled_dag.py --check

来源：backend/ascend/causal/world.py 的 ASCEND_MECHANISMS 生产声明
规模：47 节点 / 35 机制 / 64 条父引用

角色边界：本文件只把声明**数据**实例化为 UnrolledDag.Decl 并给出
`WellFormed` 的机器可判证明；无环定理本身在 UnrolledDag.lean。 -/

namespace AscendLean.GenUnrolledDag

open AscendLean.CausalVerification

-- ═══ 第一节 微步序（更新阶段 r_v 的全序）═══

/-- world.gen_input -/
def step0 : ℕ := 0
/-- world.gen_derived_a -/
def step1 : ℕ := 1
/-- world.gen_derived_b -/
def step2 : ℕ := 2
/-- world.gen_derived_c -/
def step3 : ℕ := 3
/-- world.gen_derived_d -/
def step4 : ℕ := 4
/-- weather.chunk_derived_a -/
def step5 : ℕ := 5
/-- weather.chunk_derived_b -/
def step6 : ℕ := 6
/-- weather.frame_input -/
def step7 : ℕ := 7
/-- weather.instant_tick_input -/
def step8 : ℕ := 8
/-- weather.instant_tick_derived -/
def step9 : ℕ := 9
/-- weather.instant_offset -/
def step10 : ℕ := 10
/-- weather.instant_composite -/
def step11 : ℕ := 11
/-- weather.instant_readout -/
def step12 : ℕ := 12

def stepCount : ℕ := 13

-- ═══ 第二节 节点索引（分量模板 v）═══

/-- weather.astronomy.daylight_hours -/
def nodeweather_astronomy_daylight_hours : ℕ := 0
/-- weather.astronomy.sunrise_hour -/
def nodeweather_astronomy_sunrise_hour : ℕ := 1
/-- weather.astronomy.sunset_hour -/
def nodeweather_astronomy_sunset_hour : ℕ := 2
/-- weather.chunk.annual_mean_temperature_c -/
def nodeweather_chunk_annual_mean_temperature_c : ℕ := 3
/-- weather.chunk.annual_rainfall_mm_per_year -/
def nodeweather_chunk_annual_rainfall_mm_per_year : ℕ := 4
/-- weather.chunk.baseline_humidity_percent -/
def nodeweather_chunk_baseline_humidity_percent : ℕ := 5
/-- weather.chunk.baseline_wind_speed_mps -/
def nodeweather_chunk_baseline_wind_speed_mps : ℕ := 6
/-- weather.chunk.diurnal_humidity_amplitude_pp -/
def nodeweather_chunk_diurnal_humidity_amplitude_pp : ℕ := 7
/-- weather.chunk.diurnal_temperature_amplitude_c -/
def nodeweather_chunk_diurnal_temperature_amplitude_c : ℕ := 8
/-- weather.chunk.humidity_sharpness -/
def nodeweather_chunk_humidity_sharpness : ℕ := 9
/-- weather.chunk.mean_precip_intensity_mm_per_hour -/
def nodeweather_chunk_mean_precip_intensity_mm_per_hour : ℕ := 10
/-- weather.chunk.precipitation_threshold -/
def nodeweather_chunk_precipitation_threshold : ℕ := 11
/-- weather.chunk.sea_level_temperature_c -/
def nodeweather_chunk_sea_level_temperature_c : ℕ := 12
/-- weather.chunk.seasonal_humidity_amplitude_pp -/
def nodeweather_chunk_seasonal_humidity_amplitude_pp : ℕ := 13
/-- weather.chunk.seasonal_temperature_amplitude_c -/
def nodeweather_chunk_seasonal_temperature_amplitude_c : ℕ := 14
/-- weather.chunk.solar_latitude_proxy_deg -/
def nodeweather_chunk_solar_latitude_proxy_deg : ℕ := 15
/-- weather.field.humidity_perturbation -/
def nodeweather_field_humidity_perturbation : ℕ := 16
/-- weather.field.precipitation_signal -/
def nodeweather_field_precipitation_signal : ℕ := 17
/-- weather.field.temperature_perturbation -/
def nodeweather_field_temperature_perturbation : ℕ := 18
/-- weather.field.wind_multiplier -/
def nodeweather_field_wind_multiplier : ℕ := 19
/-- weather.field.wind_perturbation -/
def nodeweather_field_wind_perturbation : ℕ := 20
/-- weather.instant.precipitation_intensity_mm_per_hour -/
def nodeweather_instant_precipitation_intensity_mm_per_hour : ℕ := 21
/-- weather.instant.precipitation_type -/
def nodeweather_instant_precipitation_type : ℕ := 22
/-- weather.instant.relative_humidity_percent -/
def nodeweather_instant_relative_humidity_percent : ℕ := 23
/-- weather.instant.sunshine_hours_per_day -/
def nodeweather_instant_sunshine_hours_per_day : ℕ := 24
/-- weather.instant.temperature_c -/
def nodeweather_instant_temperature_c : ℕ := 25
/-- weather.instant.wind_speed_mps -/
def nodeweather_instant_wind_speed_mps : ℕ := 26
/-- weather.offset.diurnal_humidity_pp -/
def nodeweather_offset_diurnal_humidity_pp : ℕ := 27
/-- weather.offset.diurnal_temperature_c -/
def nodeweather_offset_diurnal_temperature_c : ℕ := 28
/-- weather.offset.seasonal_humidity_pp -/
def nodeweather_offset_seasonal_humidity_pp : ℕ := 29
/-- weather.offset.seasonal_temperature_c -/
def nodeweather_offset_seasonal_temperature_c : ℕ := 30
/-- weather.tick.day -/
def nodeweather_tick_day : ℕ := 31
/-- weather.tick.day_of_year -/
def nodeweather_tick_day_of_year : ℕ := 32
/-- weather.tick.diurnal_phase_cos -/
def nodeweather_tick_diurnal_phase_cos : ℕ := 33
/-- weather.tick.hour_of_day -/
def nodeweather_tick_hour_of_day : ℕ := 34
/-- weather.tick.season -/
def nodeweather_tick_season : ℕ := 35
/-- weather.tick.season_phase_cos -/
def nodeweather_tick_season_phase_cos : ℕ := 36
/-- weather.tick.solar_declination_rad -/
def nodeweather_tick_solar_declination_rad : ℕ := 37
/-- world.clock.tick -/
def nodeworld_clock_tick : ℕ := 38
/-- world.gen.altitude_m -/
def nodeworld_gen_altitude_m : ℕ := 39
/-- world.gen.biome -/
def nodeworld_gen_biome : ℕ := 40
/-- world.gen.climate_zone -/
def nodeworld_gen_climate_zone : ℕ := 41
/-- world.gen.humidity_noise -/
def nodeworld_gen_humidity_noise : ℕ := 42
/-- world.gen.latitude_noise -/
def nodeworld_gen_latitude_noise : ℕ := 43
/-- world.gen.moisture_noise -/
def nodeworld_gen_moisture_noise : ℕ := 44
/-- world.gen.rainfall_noise -/
def nodeworld_gen_rainfall_noise : ℕ := 45
/-- world.gen.wind_noise -/
def nodeworld_gen_wind_noise : ℕ := 46

def nodeCount : ℕ := 47

-- ═══ 第三节 声明（父模板表）═══

/-- 生产声明的父模板表（可计算：节点索引 → 阶段 → 父模板列表）。 -/
def realParents : ℕ → ℕ → List ParentSpec := fun v r =>
  match v, r with
  | 0, 11 => [⟨1, 0, 10⟩, ⟨2, 0, 10⟩]
  | 1, 10 => [⟨15, 0, 5⟩, ⟨37, 0, 9⟩]
  | 2, 10 => [⟨15, 0, 5⟩, ⟨37, 0, 9⟩]
  | 3, 2 => [⟨12, 0, 1⟩, ⟨39, 0, 0⟩]
  | 4, 1 => [⟨45, 0, 0⟩]
  | 5, 4 => [⟨41, 0, 3⟩, ⟨42, 0, 0⟩]
  | 6, 4 => [⟨41, 0, 3⟩, ⟨46, 0, 0⟩]
  | 7, 6 => [⟨14, 0, 5⟩]
  | 8, 6 => [⟨14, 0, 5⟩]
  | 9, 4 => [⟨41, 0, 3⟩]
  | 10, 4 => [⟨41, 0, 3⟩]
  | 11, 5 => [⟨4, 0, 1⟩]
  | 12, 1 => [⟨43, 0, 0⟩]
  | 13, 6 => [⟨14, 0, 5⟩]
  | 14, 5 => [⟨3, 0, 2⟩, ⟨4, 0, 1⟩]
  | 15, 5 => [⟨12, 0, 1⟩]
  | 16, 7 => ([] : List ParentSpec)
  | 17, 7 => ([] : List ParentSpec)
  | 18, 7 => ([] : List ParentSpec)
  | 19, 7 => ([] : List ParentSpec)
  | 20, 7 => ([] : List ParentSpec)
  | 21, 11 => [⟨17, 0, 7⟩, ⟨11, 0, 5⟩, ⟨10, 0, 4⟩]
  | 22, 12 => [⟨25, 0, 11⟩]
  | 23, 11 => [⟨5, 0, 4⟩, ⟨29, 0, 10⟩, ⟨27, 0, 10⟩, ⟨16, 0, 7⟩]
  | 24, 12 => [⟨0, 0, 11⟩, ⟨16, 0, 7⟩]
  | 25, 11 => [⟨3, 0, 2⟩, ⟨30, 0, 10⟩, ⟨28, 0, 10⟩, ⟨18, 0, 7⟩]
  | 26, 11 => [⟨6, 0, 4⟩, ⟨20, 0, 7⟩, ⟨19, 0, 7⟩]
  | 27, 10 => [⟨7, 0, 6⟩, ⟨33, 0, 9⟩]
  | 28, 10 => [⟨8, 0, 6⟩, ⟨33, 0, 9⟩]
  | 29, 10 => [⟨13, 0, 6⟩, ⟨36, 0, 9⟩, ⟨9, 0, 4⟩]
  | 30, 10 => [⟨14, 0, 5⟩, ⟨36, 0, 9⟩]
  | 31, 8 => [⟨38, 0, 7⟩]
  | 32, 8 => [⟨38, 0, 7⟩]
  | 33, 9 => [⟨34, 0, 8⟩]
  | 34, 8 => [⟨38, 0, 7⟩]
  | 35, 9 => [⟨31, 0, 8⟩]
  | 36, 9 => [⟨31, 0, 8⟩]
  | 37, 9 => [⟨32, 0, 8⟩]
  | 38, 7 => ([] : List ParentSpec)
  | 39, 0 => ([] : List ParentSpec)
  | 40, 4 => [⟨3, 0, 2⟩, ⟨4, 0, 1⟩, ⟨39, 0, 0⟩, ⟨12, 0, 1⟩, ⟨44, 0, 0⟩]
  | 41, 3 => [⟨3, 0, 2⟩, ⟨4, 0, 1⟩, ⟨39, 0, 0⟩]
  | 42, 0 => ([] : List ParentSpec)
  | 43, 0 => ([] : List ParentSpec)
  | 44, 0 => ([] : List ParentSpec)
  | 45, 0 => ([] : List ParentSpec)
  | 46, 0 => ([] : List ParentSpec)
  | _, _ => []

/-- 展开声明：父模板列表 → 有限集合（可计算，无 choice）。 -/
def realDecl : Decl := fun v r => (realParents v r).toFinset

-- ═══ 第四节 规模与待办 ═══

/-! 待办（P5 后续）：把 `realDecl` 的 `WellFormed` 证明补上，
即可直接套用 `unroll_acyclic` 得到生产声明的时间展开无环。
当前卡点：`v r : ℕ` 上的全称量词不可判定（无 `Fintype ℕ`），
`fin_cases`/`omega` 的组合在大匹配上超出心跳预算；可行的路径是
为每个节点单独生成 `WellFormed` 的逐节点引理，或把 `Decl` 换成
`Fin nodeCount → Fin stepCount → Finset ParentSpec` 的有限索引版本。 -/

end AscendLean.GenUnrolledDag
