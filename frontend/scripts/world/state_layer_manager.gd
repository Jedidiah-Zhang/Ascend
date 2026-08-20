"""状态动态层管理器 — States_<cx>_<cy> TileMapLayer 生命周期集中封装。

显示值追赶的状态层（覆雪/结冰/湿润）与主世界其他地形层不同：它在 chunk
装载后按 chaser 的增量变化动态增删 cell（非一次性构建）。本类集中管理
这些动态层的创建/填充/遗忘/清空，主世界不再散点操作 _state_layers 表。

节点表按 key 索引（不用 has_node 名查找）——queue_free 延迟释放期间旧节点
仍占用名字，名查找会误中将要销毁的节点。
"""

class_name StateLayerManager
extends RefCounted

const Config = preload("res://scripts/config.gd")

const CHUNK_SIZE: int = Config.TILE_MAP_SIZE
const TILE_PIXEL_SIZE: int = Config.TILE_PIXEL_SIZE

## 状态动态层节点表: {Vector2i: TileMapLayer}
var _layers: Dictionary = {}

var _parent: Node2D


## 绑定挂载父节点（Terrain/StatesPool；_ready 时调用，null 时操作静默跳过）。
func bind(parent: Node2D) -> void:
	_parent = parent


## 取指定 chunk 的状态层（未挂载返回 null）。
func get_layer(key: Vector2i) -> TileMapLayer:
	var layer: Variant = _layers.get(key)
	if layer == null or not is_instance_valid(layer) or layer.is_queued_for_deletion():
		return null
	return layer as TileMapLayer


## 应用 chaser 的变化格子：懒挂载 States_%d_%d 节点（tile_set 由调用方传入，
## 与五信号层共用 TileSet 缓存），cell[1] < 0 擦除、否则 set_cell。
## cells 为空时静默跳过（无变化）。
func apply_cells(key: Vector2i, cells: Array, tile_set: TileSet) -> void:
	if _parent == null or cells.is_empty():
		return
	var layer: TileMapLayer = get_layer(key)
	if layer == null:
		layer = TileMapLayer.new()
		layer.name = "States_%d_%d" % [key.x, key.y]
		layer.tile_set = tile_set
		layer.position = Vector2(
				float(key.x * CHUNK_SIZE), float(key.y * CHUNK_SIZE)) \
				* float(TILE_PIXEL_SIZE)
		_parent.add_child(layer)
		_layers[key] = layer
	for cell in cells:
		if cell[1] < 0:
			layer.erase_cell(cell[0])
		else:
			layer.set_cell(cell[0], 0, Vector2i(cell[1], 0))


## 遗忘 chunk 的状态层（节点释放 + 表项移除；释放延迟期内节点仍占用名字，
## 表项立即移除保证后续重建不撞名）。
func erase(key: Vector2i) -> void:
	var layer: Variant = _layers.get(key)
	if layer == null:
		return
	if layer.is_inside_tree() and not layer.is_queued_for_deletion():
		layer.queue_free()
	_layers.erase(key)


## 清空全部状态层（世界重建/断线后旧层数据失效）。
func clear() -> void:
	for key in _layers.keys():
		var layer: Variant = _layers[key]
		if layer.is_inside_tree() and not layer.is_queued_for_deletion():
			layer.queue_free()
	_layers.clear()