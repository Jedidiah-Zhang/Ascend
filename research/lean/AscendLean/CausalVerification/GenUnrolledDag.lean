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

-- ═══ 第一节 规模常量 ═══

/-- 分量模板数（生产声明节点数）。 -/
def nodeCount : ℕ := 47

/-- 更新阶段数（微步序长度）。 -/
def stepCount : ℕ := 13

/-- 滞后上界（实际用到的最大滞后 + 1）。 -/
def lagCount : ℕ := 2

/-- 本文件使用的父模板结构别名（绑定生产声明的规模）。 -/
abbrev PSpec : Type :=
  AscendLean.CausalVerification.ParentSpec nodeCount stepCount lagCount

-- ═══ 第二节 生产声明的父模板表（有限索引，可计算）═══

/-- 分量模板索引按节点 ID 排序（与注册表 `sorted(nodes)` 同序）。 -/
def nodeweather_astronomy_daylight_hours : ℕ := 0
def nodeweather_astronomy_sunrise_hour : ℕ := 1
def nodeweather_astronomy_sunset_hour : ℕ := 2
def nodeweather_chunk_annual_mean_temperature_c : ℕ := 3
def nodeweather_chunk_annual_rainfall_mm_per_year : ℕ := 4
def nodeweather_chunk_baseline_humidity_percent : ℕ := 5
def nodeweather_chunk_baseline_wind_speed_mps : ℕ := 6
def nodeweather_chunk_diurnal_humidity_amplitude_pp : ℕ := 7
def nodeweather_chunk_diurnal_temperature_amplitude_c : ℕ := 8
def nodeweather_chunk_humidity_sharpness : ℕ := 9
def nodeweather_chunk_mean_precip_intensity_mm_per_hour : ℕ := 10
def nodeweather_chunk_precipitation_threshold : ℕ := 11
def nodeweather_chunk_sea_level_temperature_c : ℕ := 12
def nodeweather_chunk_seasonal_humidity_amplitude_pp : ℕ := 13
def nodeweather_chunk_seasonal_temperature_amplitude_c : ℕ := 14
def nodeweather_chunk_solar_latitude_proxy_deg : ℕ := 15
def nodeweather_field_humidity_perturbation : ℕ := 16
def nodeweather_field_precipitation_signal : ℕ := 17
def nodeweather_field_temperature_perturbation : ℕ := 18
def nodeweather_field_wind_multiplier : ℕ := 19
def nodeweather_field_wind_perturbation : ℕ := 20
def nodeweather_instant_precipitation_intensity_mm_per_hour : ℕ := 21
def nodeweather_instant_precipitation_type : ℕ := 22
def nodeweather_instant_relative_humidity_percent : ℕ := 23
def nodeweather_instant_sunshine_hours_per_day : ℕ := 24
def nodeweather_instant_temperature_c : ℕ := 25
def nodeweather_instant_wind_speed_mps : ℕ := 26
def nodeweather_offset_diurnal_humidity_pp : ℕ := 27
def nodeweather_offset_diurnal_temperature_c : ℕ := 28
def nodeweather_offset_seasonal_humidity_pp : ℕ := 29
def nodeweather_offset_seasonal_temperature_c : ℕ := 30
def nodeweather_tick_day : ℕ := 31
def nodeweather_tick_day_of_year : ℕ := 32
def nodeweather_tick_diurnal_phase_cos : ℕ := 33
def nodeweather_tick_hour_of_day : ℕ := 34
def nodeweather_tick_season : ℕ := 35
def nodeweather_tick_season_phase_cos : ℕ := 36
def nodeweather_tick_solar_declination_rad : ℕ := 37
def nodeworld_clock_tick : ℕ := 38
def nodeworld_gen_altitude_m : ℕ := 39
def nodeworld_gen_biome : ℕ := 40
def nodeworld_gen_climate_zone : ℕ := 41
def nodeworld_gen_humidity_noise : ℕ := 42
def nodeworld_gen_latitude_noise : ℕ := 43
def nodeworld_gen_moisture_noise : ℕ := 44
def nodeworld_gen_rainfall_noise : ℕ := 45
def nodeworld_gen_wind_noise : ℕ := 46

/-- 父模板表：分量索引 → 阶段 → 父模板列表。 -/
def realParents : ℕ → ℕ → List PSpec := fun v r =>
  match v, r with
  | 0, 11 => [({ par := ⟨1, by decide⟩, lag := ⟨0, by decide⟩, pr := ⟨10, by decide⟩ } : PSpec), ({ par := ⟨2, by decide⟩, lag := ⟨0, by decide⟩, pr := ⟨10, by decide⟩ } : PSpec)]
  | 1, 10 => [({ par := ⟨15, by decide⟩, lag := ⟨0, by decide⟩, pr := ⟨5, by decide⟩ } : PSpec), ({ par := ⟨37, by decide⟩, lag := ⟨0, by decide⟩, pr := ⟨9, by decide⟩ } : PSpec)]
  | 2, 10 => [({ par := ⟨15, by decide⟩, lag := ⟨0, by decide⟩, pr := ⟨5, by decide⟩ } : PSpec), ({ par := ⟨37, by decide⟩, lag := ⟨0, by decide⟩, pr := ⟨9, by decide⟩ } : PSpec)]
  | 3, 2 => [({ par := ⟨12, by decide⟩, lag := ⟨0, by decide⟩, pr := ⟨1, by decide⟩ } : PSpec), ({ par := ⟨39, by decide⟩, lag := ⟨0, by decide⟩, pr := ⟨0, by decide⟩ } : PSpec)]
  | 4, 1 => [({ par := ⟨45, by decide⟩, lag := ⟨0, by decide⟩, pr := ⟨0, by decide⟩ } : PSpec)]
  | 5, 4 => [({ par := ⟨41, by decide⟩, lag := ⟨0, by decide⟩, pr := ⟨3, by decide⟩ } : PSpec), ({ par := ⟨42, by decide⟩, lag := ⟨0, by decide⟩, pr := ⟨0, by decide⟩ } : PSpec)]
  | 6, 4 => [({ par := ⟨41, by decide⟩, lag := ⟨0, by decide⟩, pr := ⟨3, by decide⟩ } : PSpec), ({ par := ⟨46, by decide⟩, lag := ⟨0, by decide⟩, pr := ⟨0, by decide⟩ } : PSpec)]
  | 7, 6 => [({ par := ⟨14, by decide⟩, lag := ⟨0, by decide⟩, pr := ⟨5, by decide⟩ } : PSpec)]
  | 8, 6 => [({ par := ⟨14, by decide⟩, lag := ⟨0, by decide⟩, pr := ⟨5, by decide⟩ } : PSpec)]
  | 9, 4 => [({ par := ⟨41, by decide⟩, lag := ⟨0, by decide⟩, pr := ⟨3, by decide⟩ } : PSpec)]
  | 10, 4 => [({ par := ⟨41, by decide⟩, lag := ⟨0, by decide⟩, pr := ⟨3, by decide⟩ } : PSpec)]
  | 11, 5 => [({ par := ⟨4, by decide⟩, lag := ⟨0, by decide⟩, pr := ⟨1, by decide⟩ } : PSpec)]
  | 12, 1 => [({ par := ⟨43, by decide⟩, lag := ⟨0, by decide⟩, pr := ⟨0, by decide⟩ } : PSpec)]
  | 13, 6 => [({ par := ⟨14, by decide⟩, lag := ⟨0, by decide⟩, pr := ⟨5, by decide⟩ } : PSpec)]
  | 14, 5 => [({ par := ⟨3, by decide⟩, lag := ⟨0, by decide⟩, pr := ⟨2, by decide⟩ } : PSpec), ({ par := ⟨4, by decide⟩, lag := ⟨0, by decide⟩, pr := ⟨1, by decide⟩ } : PSpec)]
  | 15, 5 => [({ par := ⟨12, by decide⟩, lag := ⟨0, by decide⟩, pr := ⟨1, by decide⟩ } : PSpec)]
  | 16, 7 => ([] : List PSpec)
  | 17, 7 => ([] : List PSpec)
  | 18, 7 => ([] : List PSpec)
  | 19, 7 => ([] : List PSpec)
  | 20, 7 => ([] : List PSpec)
  | 21, 11 => [({ par := ⟨17, by decide⟩, lag := ⟨0, by decide⟩, pr := ⟨7, by decide⟩ } : PSpec), ({ par := ⟨11, by decide⟩, lag := ⟨0, by decide⟩, pr := ⟨5, by decide⟩ } : PSpec), ({ par := ⟨10, by decide⟩, lag := ⟨0, by decide⟩, pr := ⟨4, by decide⟩ } : PSpec)]
  | 22, 12 => [({ par := ⟨25, by decide⟩, lag := ⟨0, by decide⟩, pr := ⟨11, by decide⟩ } : PSpec)]
  | 23, 11 => [({ par := ⟨5, by decide⟩, lag := ⟨0, by decide⟩, pr := ⟨4, by decide⟩ } : PSpec), ({ par := ⟨29, by decide⟩, lag := ⟨0, by decide⟩, pr := ⟨10, by decide⟩ } : PSpec), ({ par := ⟨27, by decide⟩, lag := ⟨0, by decide⟩, pr := ⟨10, by decide⟩ } : PSpec), ({ par := ⟨16, by decide⟩, lag := ⟨0, by decide⟩, pr := ⟨7, by decide⟩ } : PSpec)]
  | 24, 12 => [({ par := ⟨0, by decide⟩, lag := ⟨0, by decide⟩, pr := ⟨11, by decide⟩ } : PSpec), ({ par := ⟨16, by decide⟩, lag := ⟨0, by decide⟩, pr := ⟨7, by decide⟩ } : PSpec)]
  | 25, 11 => [({ par := ⟨3, by decide⟩, lag := ⟨0, by decide⟩, pr := ⟨2, by decide⟩ } : PSpec), ({ par := ⟨30, by decide⟩, lag := ⟨0, by decide⟩, pr := ⟨10, by decide⟩ } : PSpec), ({ par := ⟨28, by decide⟩, lag := ⟨0, by decide⟩, pr := ⟨10, by decide⟩ } : PSpec), ({ par := ⟨18, by decide⟩, lag := ⟨0, by decide⟩, pr := ⟨7, by decide⟩ } : PSpec)]
  | 26, 11 => [({ par := ⟨6, by decide⟩, lag := ⟨0, by decide⟩, pr := ⟨4, by decide⟩ } : PSpec), ({ par := ⟨20, by decide⟩, lag := ⟨0, by decide⟩, pr := ⟨7, by decide⟩ } : PSpec), ({ par := ⟨19, by decide⟩, lag := ⟨0, by decide⟩, pr := ⟨7, by decide⟩ } : PSpec)]
  | 27, 10 => [({ par := ⟨7, by decide⟩, lag := ⟨0, by decide⟩, pr := ⟨6, by decide⟩ } : PSpec), ({ par := ⟨33, by decide⟩, lag := ⟨0, by decide⟩, pr := ⟨9, by decide⟩ } : PSpec)]
  | 28, 10 => [({ par := ⟨8, by decide⟩, lag := ⟨0, by decide⟩, pr := ⟨6, by decide⟩ } : PSpec), ({ par := ⟨33, by decide⟩, lag := ⟨0, by decide⟩, pr := ⟨9, by decide⟩ } : PSpec)]
  | 29, 10 => [({ par := ⟨13, by decide⟩, lag := ⟨0, by decide⟩, pr := ⟨6, by decide⟩ } : PSpec), ({ par := ⟨36, by decide⟩, lag := ⟨0, by decide⟩, pr := ⟨9, by decide⟩ } : PSpec), ({ par := ⟨9, by decide⟩, lag := ⟨0, by decide⟩, pr := ⟨4, by decide⟩ } : PSpec)]
  | 30, 10 => [({ par := ⟨14, by decide⟩, lag := ⟨0, by decide⟩, pr := ⟨5, by decide⟩ } : PSpec), ({ par := ⟨36, by decide⟩, lag := ⟨0, by decide⟩, pr := ⟨9, by decide⟩ } : PSpec)]
  | 31, 8 => [({ par := ⟨38, by decide⟩, lag := ⟨0, by decide⟩, pr := ⟨7, by decide⟩ } : PSpec)]
  | 32, 8 => [({ par := ⟨38, by decide⟩, lag := ⟨0, by decide⟩, pr := ⟨7, by decide⟩ } : PSpec)]
  | 33, 9 => [({ par := ⟨34, by decide⟩, lag := ⟨0, by decide⟩, pr := ⟨8, by decide⟩ } : PSpec)]
  | 34, 8 => [({ par := ⟨38, by decide⟩, lag := ⟨0, by decide⟩, pr := ⟨7, by decide⟩ } : PSpec)]
  | 35, 9 => [({ par := ⟨31, by decide⟩, lag := ⟨0, by decide⟩, pr := ⟨8, by decide⟩ } : PSpec)]
  | 36, 9 => [({ par := ⟨31, by decide⟩, lag := ⟨0, by decide⟩, pr := ⟨8, by decide⟩ } : PSpec)]
  | 37, 9 => [({ par := ⟨32, by decide⟩, lag := ⟨0, by decide⟩, pr := ⟨8, by decide⟩ } : PSpec)]
  | 38, 7 => ([] : List PSpec)
  | 39, 0 => ([] : List PSpec)
  | 40, 4 => [({ par := ⟨3, by decide⟩, lag := ⟨0, by decide⟩, pr := ⟨2, by decide⟩ } : PSpec), ({ par := ⟨4, by decide⟩, lag := ⟨0, by decide⟩, pr := ⟨1, by decide⟩ } : PSpec), ({ par := ⟨39, by decide⟩, lag := ⟨0, by decide⟩, pr := ⟨0, by decide⟩ } : PSpec), ({ par := ⟨12, by decide⟩, lag := ⟨0, by decide⟩, pr := ⟨1, by decide⟩ } : PSpec), ({ par := ⟨44, by decide⟩, lag := ⟨0, by decide⟩, pr := ⟨0, by decide⟩ } : PSpec)]
  | 41, 3 => [({ par := ⟨3, by decide⟩, lag := ⟨0, by decide⟩, pr := ⟨2, by decide⟩ } : PSpec), ({ par := ⟨4, by decide⟩, lag := ⟨0, by decide⟩, pr := ⟨1, by decide⟩ } : PSpec), ({ par := ⟨39, by decide⟩, lag := ⟨0, by decide⟩, pr := ⟨0, by decide⟩ } : PSpec)]
  | 42, 0 => ([] : List PSpec)
  | 43, 0 => ([] : List PSpec)
  | 44, 0 => ([] : List PSpec)
  | 45, 0 => ([] : List PSpec)
  | 46, 0 => ([] : List PSpec)
  | _, _ => []

/-- **生产声明**（有限索引：`Fin nodeCount` × `Fin stepCount`）。 -/
def realDecl : Decl nodeCount stepCount lagCount := fun v r =>
  realParents v.val r.val

-- ═══ 第三节 机器可判的合法性证明 ═══

/-- **生产声明合法**（判定式在有界索引上穷尽枚举）。 -/
theorem wellFormed_real : WellFormed realDecl :=
  wellFormed_of_check (by native_decide)

/-- **生产声明的时间展开图无环**（C2 的实例见证）。 -/
theorem unroll_acyclic_real : Acyclic (UnrollEdge lagCount realDecl) :=
  unroll_acyclic wellFormed_real

end AscendLean.GenUnrolledDag
