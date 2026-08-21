"""显示值追赶真值 — 纯逻辑 RefCounted，可独立单元测试。

后端 states（moisture/snow/ice，uint8 0-255）是真值，前端逐帧渐进收敛
（"显示值追赶"）：每帧随机抽样一部分 tile，被抽中的 tile 显示值向真值
步进（不同 tile 错开推进——降雪时雪"一片片铺开"，避免整块同步变白）。

性能设计（真实 chunk 40k tile × 9 块）：热循环用与状态序对齐的
PackedByteArray 数组（无字典查找）；全局每帧抽样预算上限
MAX_SAMPLES_PER_FRAME 防止低帧率/多区块时成本失控；已收敛 chunk
（set_truth 时全量比对，无差异）直接跳过抽样——常态每帧零成本，
仅在真值刷新后的追赶期产生抽样开销。

渲染契约（主线程调用方消费）：
  - set_truth 返回新 chunk 的初始格子（真值已有的雪/冰/湿润立即可见）
  - advance 返回逐帧变化格子 [cell_pos, atlas_col]，col = -1 表示擦除
  - atlas 列 = 状态序（首个 set_truth 的字典键序）× LEVELS + 量化档位；
    档位 0 不渲染（擦除）

天气事件加速（"快下快铺"）：
  - precipitation_start（precip_type == "snow"）→ 初雪：抽样密度 ×2（30s）
  - storm_start → 暴雪：抽样密度 ×3（20s）
  - 加速只影响抽样密度（收敛速度），不改真值——真值刷新由调用方
    （MainWorld2D 周期 get_chunks）驱动。

纯视觉缓存：不进存档、不参与逻辑；随机抽样仅影响视觉分布（无决策）。
"""

class_name StateDisplayChaser
extends RefCounted

## 显示档位数（atlas 每状态列数；档位 0 = 不渲染）
const LEVELS: int = 4
## 每次抽样的收敛步长（0-255 刻度；步长 10 → 单状态 26 次命中铺满）
const CHASE_STEP: int = 10
## 每 tile 每秒抽样次数（基准，无加速时每 tile 约 0.6 次/秒 →
## 9 块真实 chunk 约 2160 次/帧，铺满约 40s）
const SAMPLE_PER_SEC: float = 0.6
## 每帧抽样预算上限（防低帧率 delta 放大 / 大量区块时成本失控；
## 暴雪期 9 块 chunk ≈ 6480 次/帧，25 块 ≈ 1.8 万 → 截断到上限）
const MAX_SAMPLES_PER_FRAME: int = 12000

## 初雪加速：抽样密度倍率 / 持续时长（秒）
const SNOW_BOOST_MULT: float = 2.0
const SNOW_BOOST_DURATION: float = 30.0
## 暴雪加速：抽样密度倍率 / 持续时长（秒）
const STORM_BOOST_MULT: float = 3.0
const STORM_BOOST_DURATION: float = 20.0

## 真值: {Vector2i(cx, cy): {state_name: PackedByteArray(0-255)}}
var _truth: Dictionary = {}
## 显示值: {Vector2i(cx, cy): {state_name: PackedByteArray(0-255)}}
## 注册时以真值初始化（新 chunk 已有状态直接可见），此后向真值收敛。
var _display: Dictionary = {}
## 状态名序（首个 set_truth 的字典键序捕获；atlas 列偏移按此序 × LEVELS）
var _state_order: Array[String] = []

## 真值数组（与 _state_order 对齐）: {key: Array[PackedByteArray]}——
## 热循环只做数组索引（无字典查找）；状态缺失处为 null。
var _truth_arrays: Dictionary = {}
## 显示值数组（与 _state_order 对齐）: {key: Array[PackedByteArray]}
var _display_arrays: Dictionary = {}
## 追赶中标记: {key: bool}——set_truth 时全量比对显示值 vs 真值，
## 有差异才标记追赶（常态全收敛 → 跳过抽样，每帧零开销）。
var _chasing: Dictionary = {}

## 当前抽样密度倍率（加速期 > 1）
var _boost_mult: float = 1.0
## 当前加速剩余时长（秒；归零后倍率回落 1.0）
var _boost_remaining: float = 0.0
## 累计抽样次数（调试/测试用）
var _samples_taken: int = 0

var _rng := RandomNumberGenerator.new()


## 设置一个 chunk 的真值。新 chunk：显示值 = 真值副本，并返回初始格子
## （真值已有的雪/冰/湿润立即渲染）；已存在：只换真值，显示值保持并向
## 新真值收敛（返回空数组）。
##
## Args:
##     key: chunk 坐标。
##     states: {state_name: PackedByteArray}，真值快照（uint8 0-255）。
##
## Returns:
##     需立即应用的格子数组 [cell_pos(Vector2i), atlas_col]，col = -1 擦除。
func set_truth(key: Vector2i, states: Dictionary) -> Array:
	if _state_order.is_empty():
		for name in states.keys():
			_state_order.append(String(name))
	if _truth.has(key):
		# 真值字典与显示值字典都做独立副本（防调用方复用/原地改篡改内部状态）
		_truth[key] = _copy_states(states)
		_truth_arrays[key] = _aligned_arrays(_truth[key])
		_chasing[key] = _scan_dirty(key)
		return []
	var display: Dictionary = {}
	for name in states:
		display[name] = (states[name] as PackedByteArray).duplicate()
	_display[key] = display
	_truth[key] = _copy_states(states)
	_truth_arrays[key] = _aligned_arrays(_truth[key])
	_display_arrays[key] = _aligned_arrays(display)
	_chasing[key] = false  # 新 chunk 显示值 ≡ 真值（初始格子已渲染），无追赶需求
	return _initial_cells(key, states)


## 遗忘一个 chunk（卸载）：清显示/真值，其后 advance 不再触碰。
func forget(key: Vector2i) -> void:
	_truth.erase(key)
	_display.erase(key)
	_truth_arrays.erase(key)
	_display_arrays.erase(key)
	_chasing.erase(key)


## 清空全部状态（世界重置/读档重建用）。
func reset() -> void:
	_truth.clear()
	_display.clear()
	_truth_arrays.clear()
	_display_arrays.clear()
	_chasing.clear()
	_state_order.clear()
	_boost_mult = 1.0
	_boost_remaining = 0.0
	_samples_taken = 0


## 加速钩子：临时提高抽样密度倍率（多次加速取较大者，时长取剩余较长者）。
## 到期后倍率自动回落 1.0（自然衰减，无显式取消）。倍率被拒绝时
## 时长也不延长（弱加速不应续上强加速的剩余时长）。
func add_boost(multiplier: float, duration: float) -> void:
	if multiplier >= _boost_mult:
		_boost_mult = multiplier
		_boost_remaining = maxf(_boost_remaining, duration)


## 天气事件 → 加速映射（"快下快铺"）。payload 同时兼容事件字段直挂
## payload 或嵌套 payload.data 两种形态。
func on_weather_event(event_type: String, payload: Dictionary) -> void:
	var data: Dictionary = payload.get("data", {})
	match event_type:
		"precipitation_start":
			var precip_type: String = str(payload.get(
				"precip_type", data.get("precip_type", "")))
			if precip_type == "snow":
				add_boost(SNOW_BOOST_MULT, SNOW_BOOST_DURATION)
		"storm_start":
			add_boost(STORM_BOOST_MULT, STORM_BOOST_DURATION)
		_:
			pass


## 推进一帧：先衰减加速，再随机抽样收敛显示值，返回本帧变化的格子。
## 抽样预算：总 tile 数 × SAMPLE_PER_SEC × 倍率 × delta，截断到
## MAX_SAMPLES_PER_FRAME，按各 chunk tile 数比例分配（已收敛 chunk 跳过）。
##
## Args:
##     delta: 帧时长（秒）。
##
## Returns:
##     {Vector2i(cx, cy): 格子数组}，格子为
##     [cell_pos(Vector2i), atlas_col]，col = -1 擦除（档位归零）。
func advance(delta: float) -> Dictionary:
	if _boost_remaining > 0.0:
		_boost_remaining -= delta
		if _boost_remaining <= 0.0:
			_boost_remaining = 0.0
			_boost_mult = 1.0
	var chasing_keys: Array = []
	var total_tiles: int = 0
	for key in _truth:
		if _chasing.get(key, false):
			chasing_keys.append(key)
			total_tiles += _tiles_of(_truth[key])
	if chasing_keys.is_empty() or total_tiles <= 0:
		return {}
	var budget: int = mini(
		ceili(total_tiles * SAMPLE_PER_SEC * _boost_mult * delta),
		MAX_SAMPLES_PER_FRAME)
	if budget <= 0:
		return {}
	var changed: Dictionary = {}
	for key in chasing_keys:
		var tiles: int = _tiles_of(_truth[key])
		var samples: int = int(budget * tiles / float(total_tiles))
		if samples <= 0:
			continue
		var truth_arrs: Array = _truth_arrays.get(key, [])
		var display_arrs: Array = _display_arrays.get(key, [])
		var side: int = _chunk_side(tiles)
		for s in samples:
			var idx: int = _rng.randi_range(0, tiles - 1)
			_samples_taken += 1
			for st in truth_arrs.size():
				var t_arr: PackedByteArray = truth_arrs[st]
				var d_arr: PackedByteArray = display_arrs[st]
				if t_arr == null or d_arr == null or idx >= t_arr.size():
					continue
				var old_level: int = _level(d_arr[idx])
				var d: int = d_arr[idx]
				var t: int = t_arr[idx]
				if d < t:
					d = mini(d + CHASE_STEP, t)
				elif d > t:
					d = maxi(d - CHASE_STEP, t)
				var new_level: int = _level(d)
				d_arr[idx] = d
				if new_level != old_level:
					var col: int = -1
					if new_level > 0:
						col = st * LEVELS + new_level
					_changed_cell(changed, key, idx % side, int(idx / float(side)), col)
	return changed


## 新 chunk 的初始格子：真值非零档位的所有 tile（已存在的雪/冰/湿润
## 立即可见，不做渐变动画）。
func _initial_cells(_key: Vector2i, states: Dictionary) -> Array:
	# 状态集须与 _state_order 同构（键 ∈ _state_order，缺失键跳过）：
	# atlas 列偏移 = _state_order.find(name) × LEVELS，契约外的新键会
	# 产生负偏移——main_world 按 BLOB v2 固定版本表构造，恒满足
	var cells: Array = []
	var tiles: int = _tiles_of(states)
	if tiles <= 0:
		return cells
	var side: int = _chunk_side(tiles)
	for name in _state_order:
		if not states.has(name):
			continue
		var arr: PackedByteArray = states[name]
		var offset: int = _state_order.find(name) * LEVELS
		for idx in arr.size():
			var level: int = _level(arr[idx])
			if level > 0:
				cells.append([
					Vector2i(idx % side, int(idx / float(side))),
					offset + level,
				])
	return cells


## 按 _state_order 将状态字典对齐为数组（状态缺失处 null）。
func _aligned_arrays(states: Dictionary) -> Array:
	var out: Array = []
	for name in _state_order:
		out.append(states.get(name, null))
	return out


## 复制 states 字典（{name: PackedByteArray} 各数组独立副本）：内部真值
## 持有独立数据，调用方后续复用/原地改字典不会篡改 chaser 内部状态。
static func _copy_states(states: Dictionary) -> Dictionary:
	var out: Dictionary = {}
	for name in states:
		out[name] = (states[name] as PackedByteArray).duplicate()
	return out


## 全量比对显示值 vs 真值（仅在 set_truth 更新真值时调用，每次刷新
## 一次——常态 8s 一次，成本可忽略）：有任一 tile 差异 → 标记追赶。
func _scan_dirty(key: Vector2i) -> bool:
	var truth_arrs: Array = _truth_arrays.get(key, [])
	var display_arrs: Array = _display_arrays.get(key, [])
	for st in truth_arrs.size():
		var t_arr: PackedByteArray = truth_arrs[st]
		var d_arr: PackedByteArray = display_arrs[st]
		if t_arr == null or d_arr == null:
			continue
		for idx in t_arr.size():
			if t_arr[idx] != d_arr[idx]:
				return true
	return false


## 量化档位（0..LEVELS-1）：档位 0 = 不渲染。
static func _level(v: int) -> int:
	return int(v * LEVELS / 256.0)


## 首状态数组长度 → 该 chunk tile 数（各状态段等长）。
static func _tiles_of(states: Dictionary) -> int:
	for name in states:
		var arr = states[name]
		if arr is PackedByteArray:
			return arr.size()
	return 0


## chunk 边长（正方形，tile 数组行优先）。
static func _chunk_side(tiles: int) -> int:
	var side: int = int(round(sqrt(float(tiles))))
	return maxi(1, side)


## 记录一格变化（同 chunk 合并）。
static func _changed_cell(changed: Dictionary, key: Vector2i, x: int, z: int,
		col: int) -> void:
	if not changed.has(key):
		changed[key] = []
	changed[key].append([Vector2i(x, z), col])


## 调试/测试：显示值查询（无该 chunk/状态返回 -1）。
func display_value(key: Vector2i, state: String, idx: int) -> int:
	var display: Dictionary = _display.get(key, {})
	var arr: PackedByteArray = display.get(state, PackedByteArray())
	if idx >= arr.size():
		return -1
	return arr[idx]


## 调试/测试：当前抽样密度倍率。
func boost_mult() -> float:
	return _boost_mult


## 调试/测试：当前加速剩余时长（秒）。
func boost_remaining() -> float:
	return _boost_remaining


## 调试/测试：当前收敛中的 chunk 数（已注册真值）。
func chunk_count() -> int:
	return _truth.size()


## 调试/测试：累计抽样次数（加速生效验证用）。
func samples_taken() -> int:
	return _samples_taken


## 调试/测试：设置随机种子（抽样确定性，仅视觉分布）。
func seed_rng(seed_value: int) -> void:
	_rng.seed = seed_value
