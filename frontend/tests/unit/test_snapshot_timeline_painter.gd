"""SnapshotTimelinePainter 单元测试 — 时间线绘制器几何/命中/缩放。

必须注入画布（Control）满足 layout 的尺寸推算；断言锁定几何/命中等纯逻辑
几何/命中/缩放等纯逻辑结果，不含具体渲染像素。
"""

extends GutTest


const TM: Vector2i = Vector2i.ZERO


func _make_canvas() -> Control:
	var canvas: Control = Control.new()
	canvas.size = Vector2(1280, 720)
	autoqfree(canvas)
	add_child(canvas)
	return canvas


func _painter(canvas: Control) -> SnapshotTimelinePainter:
	var painter: SnapshotTimelinePainter = SnapshotTimelinePainter.new()
	painter.setup(canvas, FontUtils.get_mono_font(), {"world_id": "w1", "game_time": 300}, "")
	return painter


## 构造单链树（节点数参数化；time 按 index 递增）。
func _chain_snapshots(count: int) -> Array:
	var snaps: Array = []
	for i in range(count):
		snaps.append({"file": "s%d" % i, "parent": "" if i == 0 else "s%d" % (i - 1),
			"game_time": (i + 1) * 100, "suffix": "manual"})
	return snaps


## 注入单链树并执行一次完整渲染。
func _layout_chain(painter: SnapshotTimelinePainter, count: int, row_rect: Rect2) -> void:
	var tree: Dictionary = TimelineLayout.build(_chain_snapshots(count), 300, "s%d" % (count - 1))
	painter.set_tree(tree["nodes"], tree["edges"], TimelineLayout.save_order_ids(_chain_snapshots(count)))
	painter.layout(row_rect)


func test_layout_builds_geometry() -> void:
	"""渲染后：节点矩形与树节点一一对应、树/图例视口有效。"""
	var painter: SnapshotTimelinePainter = _painter(_make_canvas())
	_layout_chain(painter, 3, Rect2(24, 300, 1232, 76))

	assert_true(painter.tree_rect.size.y > 0.0, "面板应布局")
	assert_true(painter.body_rect.size.x > 0.0, "树视口应有效")
	assert_true(painter.legend_rect.size.x > 0.0, "图例列应有效")
	assert_eq(painter.node_rects.size(), painter.nodes.size(), "每个节点一个命中矩形")
	assert_eq(painter.numbers.size(), 3, "保存顺序编号应齐全")


func test_hit_node_and_legend() -> void:
	"""节点/图例命中：矩形中心命中，树区外未命中。"""
	var painter: SnapshotTimelinePainter = _painter(_make_canvas())
	_layout_chain(painter, 3, Rect2(24, 300, 1232, 76))

	var center: Vector2 = painter.node_rects["s1"].get_center()
	assert_eq(painter.hit_node(center), "s1", "节点中心应命中")
	assert_eq(painter.hit_node(Vector2(-500, -500)), "", "树区外未命中")

	var lc: Vector2 = painter.legend_rects["s2"].get_center()
	assert_eq(painter.hit_legend(lc), "s2", "图例行中心应命中")
	assert_eq(painter.hit_legend(Vector2(-500, -500)), "", "图例区外未命中")


func test_fit_zoom_long_tree_fits() -> void:
	"""长树自动缩小适配（>= 下限）；短树保持 1.0。"""
	var canvas: Control = _make_canvas()
	var painter: SnapshotTimelinePainter = _painter(canvas)
	_layout_chain(painter, 3, Rect2(24, 300, 1232, 76))
	painter.fit_zoom()
	assert_eq(painter.zoom, 1.0, "短树保持 1.0")

	painter.set_tree([], [], [])
	_layout_chain(painter, 20, Rect2(24, 300, 1232, 76))
	painter.fit_zoom()
	assert_lt(painter.zoom, 1.0, "长树应缩小")
	assert_true(painter.zoom >= SnapshotTimelinePainter.ZOOM_MIN, "不得低于下限")


func test_wheel_zoom_clamps_and_anchor() -> void:
	"""树区滚轮缩放有上下限；图例区滚轮只滚动列表。"""
	var painter: SnapshotTimelinePainter = _painter(_make_canvas())
	for i in range(25):
		painter.set_tree([{"id": "s%d" % i, "depth": i, "slot": 0, "is_live": false,
			"suffix": "manual", "time": i * 100, "saved_at": 0.0}], [], [])
	painter.layout(Rect2(24, 300, 1232, 76))
	painter.fit_zoom()
	var zoom_before: float = painter.zoom

	painter.wheel(-1.0, painter.body_rect.get_center())
	assert_gt(painter.zoom, zoom_before, "滚轮应放大")
	for i in range(40):
		painter.wheel(-1.0, painter.body_rect.get_center())
	assert_eq(painter.zoom, SnapshotTimelinePainter.ZOOM_MAX, "放大钳制上限")
	for i in range(80):
		painter.wheel(1.0, painter.body_rect.get_center())
	assert_eq(painter.zoom, SnapshotTimelinePainter.ZOOM_MIN, "缩小钳制下限")

	var legend_scroll: float = painter.legend_scroll
	painter.wheel(1.0, painter.legend_rect.get_center())
	assert_eq(painter.legend_scroll, legend_scroll + 1.0, "图例区滚轮滚动列表")


func test_node_label_priority() -> void:
	"""节点标签：优先游戏时间，缺失时用真实保存时间。"""
	var painter: SnapshotTimelinePainter = _painter(_make_canvas())
	var label: String = painter.node_label({
		"time": 300, "saved_at": 0.0,
	})
	assert_eq(label, SaveInfoFormatter.game_time_string(300), "time 优先")
	var fallback: String = painter.node_label({
		"time": 0, "saved_at": 0.0,
	})
	assert_eq(fallback, "—", "无时间且无 saved_at 时返回占位")


func test_reset_clears_geometry() -> void:
	"""收起后命中几何清空，防陈旧矩形命中。"""
	var painter: SnapshotTimelinePainter = _painter(_make_canvas())
	_layout_chain(painter, 3, Rect2(24, 300, 1232, 76))
	assert_true(painter.node_rects.size() > 0)
	painter.reset()
	assert_eq(painter.node_rects.size(), 0, "reset 应清空节点矩形")
	assert_eq(painter.hit_node(painter.body_rect.get_center()), "", "旧矩形不应参与命中")
