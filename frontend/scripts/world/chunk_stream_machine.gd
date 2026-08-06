"""chunk 流式加载状态机 — 纯逻辑 RefCounted 类，可独立单元测试。

状态转移（结构性无双请求）:
    UNKNOWN → FIELD_REQUESTED（收集并发出字段请求）
    → 字段响应到达存数据 → FIELD_REQUESTED（保持，等待限流）
    → TILE_REQUESTED（限流后发出完整请求）
    → 完整响应解码 → RECEIVED → 材质就绪建网格 → BUILT

异常路径:
  - 完整响应损坏（长度不符）→ 降级 FIELD_REQUESTED 重新入队
  - 服务器对完整请求回字段版 → 降级 FIELD_REQUESTED 重发完整请求
  - 断线：无数据的 FIELD_REQUESTED → UNKNOWN（重连后重新字段请求）；
    有数据的 FIELD_REQUESTED / TILE_REQUESTED → 保留数据降级
  - 卸载/世界重置后到达的响应（UNKNOWN）→ 陈旧丢弃

数据字典（chunk 数据本体）由调用方持有；本类只管理状态与转移。
"""

class_name ChunkStreamMachine
extends RefCounted


## chunk 生命周期状态（单一状态机）
enum ChunkState { UNKNOWN, FIELD_REQUESTED, TILE_REQUESTED, RECEIVED, BUILT }


## 状态映射: {Vector2i(cx, cy): ChunkState}
var _states: Dictionary = {}


## 查询状态；未登记 key 视为 UNKNOWN。
func get_state(key: Vector2i) -> int:
	return _states.get(key, ChunkState.UNKNOWN)


## 清空全部状态（世界重置/读档重建用）。
func reset() -> void:
	_states.clear()


## 收集半径内 UNKNOWN 坐标并标记 FIELD_REQUESTED。
##
## Args:
##     center: 中心 chunk 坐标。
##     radius: 流式半径（chunk 格数）。
##
## Returns:
##     需发出字段请求的坐标数组 [[cx, cy], ...]。
func collect_field_requests(center: Vector2i, radius: int) -> Array[Array]:
	var coords: Array[Array] = []
	for dx in range(-radius, radius + 1):
		for dy in range(-radius, radius + 1):
			var key := center + Vector2i(dx, dy)
			if get_state(key) == ChunkState.UNKNOWN:
				_states[key] = ChunkState.FIELD_REQUESTED
				coords.append([key.x, key.y])
	return coords


## 返回所有 RECEIVED（完整数据已到、待构建）的 key 列表。
func collect_build_candidates() -> Array:
	var candidates: Array = []
	for key in _states:
		if _states[key] == ChunkState.RECEIVED:
			candidates.append(key)
	return candidates


## 限流选取完整请求：FIELD_REQUESTED 且字段数据已到 → TILE_REQUESTED。
##
## Args:
##     has_field_data: Callable(key) -> bool，判定字段数据是否已缓存。
##     max_pending: 在途完整请求上限。
##
## Returns:
##     选中的 key 列表（调用方据此发送完整请求）。
func select_full_requests(has_field_data: Callable, max_pending: int) -> Array:
	var inflight: int = 0
	for key in _states:
		if _states[key] == ChunkState.TILE_REQUESTED:
			inflight += 1
	var selected: Array = []
	for key in _states:
		if inflight + selected.size() >= max_pending:
			break
		if _states[key] == ChunkState.FIELD_REQUESTED and has_field_data.call(key):
			_states[key] = ChunkState.TILE_REQUESTED
			selected.append(key)
	return selected


## 字段版响应到达：保持 FIELD_REQUESTED；若此前在 TILE_REQUESTED
## （服务器对完整请求回字段版，异常）→ 降级重发完整请求。
func on_field_response(key: Vector2i) -> void:
	if _states.get(key) == ChunkState.TILE_REQUESTED:
		_states[key] = ChunkState.FIELD_REQUESTED


## 完整版响应到达。
##
## Args:
##     key: chunk 坐标。
##     data_valid: 数据完整性校验结果（长度/解码）。有效 → RECEIVED；
##         无效 → 降级 FIELD_REQUESTED 重新入队。
func on_full_response(key: Vector2i, data_valid: bool) -> void:
	_states[key] = ChunkState.RECEIVED if data_valid else ChunkState.FIELD_REQUESTED


## 响应陈旧判定：状态为 UNKNOWN（已卸载/世界重置后到达）→ 应丢弃。
func should_drop_response(key: Vector2i) -> bool:
	return get_state(key) == ChunkState.UNKNOWN


## 断线降级（连接恢复后状态可续）：
##   - FIELD_REQUESTED 且无字段数据（字段在途）→ 移除（UNKNOWN，重连后重新请求）
##   - FIELD_REQUESTED 有数据 / TILE_REQUESTED → 降级 FIELD_REQUESTED（重连后重发完整请求）
##
## Args:
##     has_field_data: Callable(key) -> bool，判定字段数据是否已缓存。
##
## Returns:
##     被移除（降为 UNKNOWN）的 key 列表，调用方应同步清理数据字典。
func on_disconnect(has_field_data: Callable) -> Array:
	var dropped: Array = []
	for key in _states.keys():
		match _states[key]:
			ChunkState.FIELD_REQUESTED:
				if not has_field_data.call(key):
					_states.erase(key)
					dropped.append(key)
			ChunkState.TILE_REQUESTED:
				_states[key] = ChunkState.FIELD_REQUESTED
			_:
				pass
	return dropped


## 标记构建完成（网格已挂载）。
func mark_built(key: Vector2i) -> void:
	_states[key] = ChunkState.BUILT


## 遗忘一个 chunk（卸载）：状态 → UNKNOWN。
func forget(key: Vector2i) -> void:
	_states.erase(key)


## 就绪判定：center 的 radius 邻域全部 BUILT。
func all_built(center: Vector2i, radius: int) -> bool:
	for dx in range(-radius, radius + 1):
		for dy in range(-radius, radius + 1):
			if get_state(center + Vector2i(dx, dy)) != ChunkState.BUILT:
				return false
	return true


## 调试统计：{loaded: BUILT 数, cached: RECEIVED 数, pending: 请求中数}。
func counts() -> Dictionary:
	var loaded: int = 0
	var cached: int = 0
	var pending: int = 0
	for key in _states:
		match _states[key]:
			ChunkState.BUILT:
				loaded += 1
			ChunkState.RECEIVED:
				cached += 1
			ChunkState.FIELD_REQUESTED, ChunkState.TILE_REQUESTED:
				pending += 1
	return {
		"loaded": loaded,
		"cached": cached,
		"pending": pending,
	}


## 当前登记的状态数（含请求中/已加载；不含 UNKNOWN）。
func size() -> int:
	return _states.size()


## 全部已登记 key（卸载遍历等迭代用）。
func keys() -> Array:
	return _states.keys()
