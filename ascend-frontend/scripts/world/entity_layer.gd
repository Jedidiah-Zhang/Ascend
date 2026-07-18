"""实体视图工厂 — entity_id → EntityView 的注册表（Issue #20）。

前后端实体管线的前端消费端：
  - 状态通道：apply_snapshot() 全量对齐（连接/读档后初始化）
  - 因果通道：on_entity_born / on_entity_died / on_entity_moved 增量维护

现阶段视野 = 全部（后端 birth 即前端 spawn 视图、death 即 despawn 视图）；
视野剔除与概率模拟降级引入后，本层是实现渲染域 spawn/despawn 的位置。

本地控制实体（player_state 的 entity_id）同样由本工厂创建视图——
Player 与其他实体走同一条渲染管线，仅位置驱动不同：本地实体由
MapDisplay 输入预测驱动，其余实体由后端事件驱动。
"""
extends Node2D

class_name EntityLayer

## 实体视图统一挂在 layer 0（地表层）；layer_id ≠ 0 的实体由区域地图处理。
## 脚下海拔由 MapDisplay.get_elevation_at 实时查询，无数据时回退 0。

## entity_id → EntityView
var _views: Dictionary = {}
## 本地控制的实体 ID（player_state 告知）
var _local_entity_id: String = ""

@onready var _map: MapDisplay = get_parent() as MapDisplay


func _ready() -> void:
	assert(_map != null, "EntityLayer 必须是 MapDisplay 的直接子节点")


func apply_snapshot(entities: Array) -> void:
	"""全量对齐实体视图（状态通道）。

	diff 式同步：新增缺失视图、更新已有位置、移除不在快照中的视图。

	Args:
		entities: [{id, entity_type, controller, x, y, layer_id}, ...]。
	"""
	var alive: Dictionary = {}
	for entry_v in entities:
		var entry: Dictionary = entry_v
		var id: String = str(entry.get("id", ""))
		if id.is_empty():
			continue
		alive[id] = true
		var pos := Vector2(float(entry.get("x", 0.0)), float(entry.get("y", 0.0)))
		if _views.has(id):
			_update_view_position(id, pos)
		else:
			_create_view(id, str(entry.get("entity_type", "")),
				str(entry.get("controller", "NONE")), pos)

	for id in _views.keys():
		if not alive.has(id):
			_remove_view(id)


func on_entity_born(data: Dictionary) -> void:
	"""处理 entity_born 事件：创建实体视图。

	Args:
		data: 事件 data（entity_id/entity_type/controller/x/y）。
	"""
	var id: String = str(data.get("entity_id", ""))
	if id.is_empty() or _views.has(id):
		return
	_create_view(id, str(data.get("entity_type", "")),
		str(data.get("controller", "NONE")),
		Vector2(float(data.get("x", 0.0)), float(data.get("y", 0.0))))


func on_entity_died(data: Dictionary) -> void:
	"""处理 entity_died 事件：移除实体视图。

	Args:
		data: 事件 data（entity_id）。
	"""
	var id: String = str(data.get("entity_id", ""))
	if id.is_empty():
		return
	_remove_view(id)


func on_entity_moved(data: Dictionary) -> void:
	"""处理 entity_moved 事件：更新实体视图位置。

	本地控制实体忽略——它的移动是本地预测上报的回声，
	以本地预测为准（权威纠偏走 player_teleported / player_move 响应）。

	Args:
		data: 事件 data（entity_id/x/y）。
	"""
	var id: String = str(data.get("entity_id", ""))
	if id.is_empty() or id == _local_entity_id:
		return
	if not _views.has(id):
		return
	_update_view_position(id,
		Vector2(float(data.get("x", 0.0)), float(data.get("y", 0.0))))


func set_local_entity(entity_id: String) -> void:
	"""标记本地控制的实体（player_state 响应）。

	视图尚未创建时仅记录 ID，创建时自动应用玩家外观。

	Args:
		entity_id: 玩家控制的实体 ID。
	"""
	_local_entity_id = entity_id
	var view: EntityView = _views.get(entity_id)
	if view:
		view.setup(view.entity_id, view.entity_type, "PLAYER")
		_map.bind_local_entity_view(view)


func get_local_view() -> EntityView:
	"""返回本地控制实体的视图（不存在时为 null）。"""
	return _views.get(_local_entity_id)


func view_count() -> int:
	"""当前实体视图数量（调试面板用）。"""
	return _views.size()


# ── 内部 ──────────────────────────────────────────────────

func _create_view(id: String, type_name: String, controller_name: String,
		pos: Vector2) -> void:
	"""创建实体视图并放置到地图。"""
	var view := EntityView.new()
	view.name = "Entity_%s" % id.substr(0, 8)
	view.setup(id, type_name, controller_name)
	_views[id] = view
	_map.place_entity_node(view, pos, _ground_elevation_m(pos))
	if id == _local_entity_id or view.is_player_controlled():
		_map.bind_local_entity_view(view)
	print("[entity] view + %s %s %s (%.1f, %.1f)" % [
		id.substr(0, 8), type_name, controller_name, pos.x, pos.y])


func _remove_view(id: String) -> void:
	"""移除实体视图并释放节点。"""
	var view: EntityView = _views.get(id)
	if view == null:
		return
	_views.erase(id)
	# 无条件解绑：bind 有两条路径（local_id 匹配 / PLAYER controller），
	# unbind 内部按 view 实例比较，非绑定视图调用无副作用
	_map.unbind_local_entity_view(view)
	if view.get_parent():
		view.get_parent().remove_child(view)
	view.queue_free()
	print("[entity] view - %s" % id.substr(0, 8))


func _update_view_position(id: String, pos: Vector2) -> void:
	"""更新实体视图的世界位置。"""
	var view: EntityView = _views.get(id)
	if view:
		_map.place_entity_node(view, pos, _ground_elevation_m(pos))


func _ground_elevation_m(pos: Vector2) -> float:
	"""查询实体脚下海拔（米），chunk 未加载时回退 0（地表层）。

	非玩家实体的海拔在 chunk 后续加载时不会自动校正，需等下次
	entity_moved 事件触发重放。玩家由 MapDisplay 专门校正。
	"""
	var m: float = _map.get_elevation_at(pos)
	if m <= -998.0:
		return 0.0
	return m
