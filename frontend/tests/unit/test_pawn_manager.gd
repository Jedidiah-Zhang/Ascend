"""PawnManager 单元测试 — 实体 pawn 生命周期封装（非玩家实体）。

部件铺设/名称浮层/朝向镜像复用 PawnRenderer（已测）；本类测表管理：
生成/重复 upsert/移动/死亡移除/清空/可见性回调/玩家节点注册。
"""

extends GutTest

const PawnManager = preload("res://scripts/world/pawn_manager.gd")


func _make_manager(parent: Node2D = null) -> PawnManager:
	var mgr := PawnManager.new()
	if parent != null:
		mgr.bind(parent, func() -> bool: return true)
	return mgr


func test_ensure_spawns_pawn_with_parts_and_nameplate() -> void:
	var root := Node2D.new()
	add_child(root)
	var mgr := _make_manager(root)
	var pawn: Node2D = mgr.ensure("e1", "PLANT", 3.0, 4.0)
	assert_eq(pawn.name, "Pawn_e1")
	assert_eq(pawn.get_parent(), root)
	assert_true(pawn.get_node_or_null("crown") != null, "植物应有冠部件")
	assert_true(pawn.get_node_or_null("Nameplate") != null, "应有名称浮层")
	assert_eq(pawn.position, Vector2(3.0 * 16.0, 4.0 * 16.0), "tile → 世界像素")
	assert_eq(mgr._pawns.size(), 1)


func test_ensure_existing_upserts_position() -> void:
	var root := Node2D.new()
	add_child(root)
	var mgr := _make_manager(root)
	mgr.ensure("e1", "PLANT", 1.0, 1.0)
	mgr.ensure("e1", "PLANT", 5.0, 6.0)
	assert_eq(mgr._pawns.size(), 1, "重复实体不应新建节点")
	assert_eq(mgr._pawns["e1"].position, Vector2(5.0 * 16.0, 6.0 * 16.0))


func test_despawn_removes_node_and_tables() -> void:
	var root := Node2D.new()
	add_child(root)
	var mgr := _make_manager(root)
	var pawn: Node2D = mgr.ensure("e1", "PLANT", 0.0, 0.0)
	mgr.despawn("e1")
	assert_false(mgr._pawns.has("e1"))
	assert_false(mgr._pawn_specs.has(pawn))
	assert_false(mgr._pawn_facing.has(pawn))
	assert_true(pawn.is_queued_for_deletion(), "节点应释放")


func test_clear_removes_all() -> void:
	var root := Node2D.new()
	add_child(root)
	var mgr := _make_manager(root)
	mgr.ensure("e1", "PLANT", 0.0, 0.0)
	mgr.ensure("e2", "STRUCTURE", 1.0, 1.0)
	mgr.clear()
	assert_true(mgr._pawns.is_empty())
	assert_true(mgr._pawn_specs.is_empty())
	assert_true(mgr._pawn_facing.is_empty())


func test_place_hides_before_world_visible() -> void:
	"""可见性回调返回 false（世界未就绪）时新 pawn 应隐藏。"""
	var root := Node2D.new()
	add_child(root)
	var mgr := PawnManager.new()
	mgr.bind(root, func() -> bool: return false)
	var pawn: Node2D = mgr.ensure("e1", "PLANT", 0.0, 0.0)
	assert_false(pawn.visible, "世界未就绪 pawn 应隐藏")
	mgr.show_all()
	assert_true(pawn.visible, "世界就绪后应显示")


func test_set_facing_rebuilds_only_registered_nodes() -> void:
	var root := Node2D.new()
	add_child(root)
	var mgr := _make_manager(root)
	var pawn: Node2D = mgr.ensure("e1", "CREATURE", 0.0, 0.0)
	var stray := Node2D.new()
	add_child(stray)
	mgr.set_facing(stray, true)  # 未注册节点：静默跳过，不崩溃
	assert_false(mgr._pawn_facing.has(stray))
	mgr.set_facing(pawn, true)
	assert_eq(mgr._pawn_facing[pawn], true)
	var left_x := 0.0
	var right_x := 0.0
	for child in pawn.get_children():
		if child is Sprite2D and child.name == "limb_left":
			left_x = child.position.x
		if child is Sprite2D and child.name == "limb_right":
			right_x = child.position.x
	assert_true(left_x > right_x, "朝左时左附肢应镜像到右侧")


func test_register_node_tracks_player_without_pawn_table() -> void:
	"""玩家节点注册进规格/朝向表但不入 pawn 表（防双渲染）。"""
	var root := Node2D.new()
	add_child(root)
	var mgr := _make_manager(root)
	var player := Node2D.new()
	var spec := PawnRenderer.default_spec("CREATURE")
	mgr.register_node(player, spec, false)
	assert_true(mgr._pawn_facing.has(player), "玩家朝向应被跟踪")
	assert_true(mgr._pawns.is_empty(), "玩家不应进 pawn 表")
	mgr.set_facing(player, true)
	assert_eq(mgr._pawn_facing[player], true)