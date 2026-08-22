"""实体 pawn 管理器 — 分层 Sprite2D pawn 生命周期集中封装。

从 main_world.gd 拆出（实体快照/事件 → 生成 pawn、移动/朝向、死亡移除、
世界重建/断线清理）：本类持有全部非玩家 pawn 的节点与规格/朝向状态，
玩家 pawn 由调用方持有节点、复用本类的部件铺设/名称浮层/朝向/放置助手。
协作方向：main_world（世界编排）→ 本类；本类不感知输入/相机/对账。
"""

class_name PawnManager
extends RefCounted

const PawnRenderer = preload("res://scripts/world/pawn_renderer.gd")

const TILE_PIXEL_SIZE: int = Config.TILE_PIXEL_SIZE

## 实体 pawn 表：entity_id -> Node2D（含玩家外的生物/植物/建筑）
var _pawns: Dictionary = {}
## 各 pawn 的体型规格/朝向（实体数量少，字典开销可忽略）
var _pawn_specs: Dictionary = {}
var _pawn_facing: Dictionary = {}

var _parent: Node2D
## 世界可见性回调（世界就绪前隐藏 pawn）：调用方喂入（返回 bool）。
var _visible_fn: Callable = Callable()


## 绑定挂载父节点（$World/Entities Y-sort 层）与可见性回调。
func bind(parent: Node2D, visible_fn: Callable) -> void:
	_parent = parent
	_visible_fn = visible_fn


## 注册外部节点到规格/朝向表（玩家专用：节点由调用方持有，不入 _pawns
## 表——玩家由 player_state/快照独占消费，避免与实体事件双渲染）。
func register_node(node: Node2D, spec: Dictionary, facing_left: bool) -> void:
	_pawn_specs[node] = spec
	_pawn_facing[node] = facing_left


## 实体快照/事件 → 生成 pawn（实体不存在则创建并铺部件/名称浮层）。
func ensure(entity_id: String, entity_type: String,
		x: float, y: float) -> Node2D:
	if _pawns.has(entity_id):
		var existing: Node2D = _pawns[entity_id]
		place(existing, x, y)
		return existing
	var spec: Dictionary = PawnRenderer.default_spec(entity_type)
	var pawn := Node2D.new()
	pawn.name = "Pawn_%s" % entity_id
	_pawn_specs[pawn] = spec
	_pawn_facing[pawn] = false
	apply_parts(pawn, spec, false)
	add_nameplate(pawn, spec, entity_type)
	_parent.add_child(pawn)
	place(pawn, x, y)
	_pawns[entity_id] = pawn
	return pawn


## 放置 pawn 到全局 tile 坐标（世界像素换算 + 世界未就绪时隐藏）。
## 只被非玩家 pawn 路径调用（玩家位置由 main_world 直接设置）。
func place(node: Node2D, x: float, y: float) -> void:
	node.position = Vector2(x, y) * float(TILE_PIXEL_SIZE)
	if _visible_fn.is_valid() and not _visible_fn.call():
		node.visible = false


## 设置 pawn 朝向（facing_left）：朝向变化时重建部件偏移（镜像）。
## 无此 pawn 的规格记录时静默跳过（外部节点未注册）。
func set_facing(node: Node2D, facing_left: bool) -> void:
	if not _pawn_facing.has(node) or _pawn_facing[node] == facing_left:
		return
	_pawn_facing[node] = facing_left
	var spec: Dictionary = _pawn_specs.get(node, {})
	if spec.is_empty():
		return
	for child in node.get_children():
		if child is Sprite2D:
			node.remove_child(child)
			child.queue_free()
	apply_parts(node, spec, facing_left)


## 移除实体 pawn（节点释放 + 表项清理）。
func despawn(entity_id: String) -> void:
	if not _pawns.has(entity_id):
		return
	var node: Node2D = _pawns[entity_id]
	_pawn_specs.erase(node)
	_pawn_facing.erase(node)
	_pawns.erase(entity_id)
	if node.is_inside_tree():
		node.queue_free()


## 世界就绪后显示全部非玩家 pawn（_place_pawn 在未就绪时隐藏过）。
func show_all() -> void:
	for node in _pawns.values():
		node.visible = true


## 清空全部实体 pawn（世界重建/断线后旧实体数据失效）。
func clear() -> void:
	for entity_id in _pawns.keys():
		var node: Node2D = _pawns[entity_id]
		_pawn_specs.erase(node)
		_pawn_facing.erase(node)
		if node.is_inside_tree():
			node.queue_free()
		elif node.get_parent() != null:
			node.get_parent().remove_child(node)
			node.queue_free()
	_pawns.clear()


# ── 静态助手（玩家与普通 pawn 共用） ────────────────────────

## 按规格为 pawn 节点铺设 Sprite2D 部件（PawnRenderer.build_parts 输出；
## 部件顺序即叠层 z）。facing_left 时镜像偏移（朝向反转、左右附肢换位）。
static func apply_parts(node: Node2D, spec: Dictionary, facing_left: bool) -> void:
	for part in PawnRenderer.build_parts(spec, facing_left):
		var sprite := Sprite2D.new()
		sprite.name = part[PawnRenderer.PART_NAME]
		sprite.texture = PawnRenderer.make_part_texture(
			part[PawnRenderer.PART_SIZE], part[PawnRenderer.PART_COLOR])
		# 精灵锚点在左上（1:1 色块），部件偏移即像素偏移（原点=脚底中心）
		sprite.centered = false
		sprite.position = Vector2(part[PawnRenderer.PART_OFFSET])
		sprite.z_index = part[PawnRenderer.PART_Z]
		node.add_child(sprite)


## 头顶名称浮层：独立 Label 悬浮在头部上方（不占身体像素预算），
## 显示本地化实体类型名。
static func add_nameplate(node: Node2D, spec: Dictionary,
		entity_type: String) -> void:
	var label := Label.new()
	label.name = "Nameplate"
	# tr() 是 Node 实例方法，静态函数（RefCounted）用 TranslationServer 单例
	label.text = TranslationServer.translate(
		"entity.type.%s" % entity_type.to_lower())
	# 固定 64px 宽度并以锚点水平居中：内容宽随语言/字号变化，
	# 锚点即头部中心上方（左端 = 锚点.x - 32）
	label.custom_minimum_size = Vector2(64, 0)
	label.position = PawnRenderer.nameplate_offset(spec) - Vector2(32, 0)
	label.horizontal_alignment = HORIZONTAL_ALIGNMENT_CENTER
	label.add_theme_font_size_override("font_size", 10)
	label.add_theme_color_override("font_color", Color(1, 1, 1, 0.95))
	label.add_theme_color_override(
		"font_outline_color", Color(0, 0, 0, 0.9))
	label.add_theme_constant_override("outline_size", 3)
	node.add_child(label)
