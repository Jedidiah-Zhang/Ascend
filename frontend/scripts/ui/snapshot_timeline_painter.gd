"""内嵌时间线视图 — 存档选择页展开行的快照分叉树绘制 + 几何命中。

输入 TimelineLayout.build 的树（nodes/edges）与保存顺序编号，
在展开行下方绘制：节点圆圈（编号）、连线、右侧可滚动图例，
支持拖拽平移 / 滚轮缩放（以光标为锚）/ 图例滚动。

两阶段设计（可单测）:
  - layout(row_rect)：纯几何——面板/树视口/图例矩形、编号、平移钳制
    与居中、节点位置与命中矩形、边可见性裁剪、图例行与滚动。无任何
    draw_* 调用（draw_* 只能在 Control._draw 阶段执行）。
  - paint()：只绘制 layout 产物（背景/标题/连线/节点/图例行）。
    必须在调 layout 之后同帧调用，且输入（nodes/edges/zoom/pan/悬停/
    选中）不得改动——否则几何与绘制不同步。
命中检测只依赖 layout 产物（node_rects / legend_rects / body_rect）。

职责边界：树数据构建（TimelineLayout）、交互决策（点击=选中？
面板开闭？在 save_select 的 _handle_click/hover 中），本类只做
「几何 + 绘制 + 纯命中 + 缩放/平移适配」。
"""

class_name SnapshotTimelinePainter
extends RefCounted


# ── 常量 ────────────────────────────────────────────────────

const INLINE_H: float = 230.0
const GAP: float = 10.0
const HEADER_H: float = 26.0
const H_STEP: float = 110.0
const V_STEP: float = 54.0
const NODE_R: float = 12.0
const LIVE_COLOR: Color = Color(0.85, 0.72, 0.30)
const LIVE_HOVER_COLOR: Color = Color(0.98, 0.88, 0.50)
const NODE_COLOR: Color = Color(0.32, 0.55, 0.85)
const NODE_HOVER_COLOR: Color = Color(0.45, 0.70, 0.95)
const AUTO_COLOR: Color = Color(0.38, 0.48, 0.60)
const AUTO_HOVER_COLOR: Color = Color(0.52, 0.64, 0.78)
const SELECT_COLOR: Color = Color(1.0, 1.0, 1.0, 0.9)
const EDGE_COLOR: Color = Color(0.55, 0.60, 0.72, 0.85)
const HINT_COLOR: Color = Color(0.55, 0.62, 0.75)
const BG_COLOR: Color = Color(0.07, 0.09, 0.14, 0.95)
const LEGEND_W: float = 250.0
const LEGEND_ROW_H: float = 18.0
const ZOOM_MIN: float = 0.35
const ZOOM_MAX: float = 2.5

## 左右边距（与 save_select.MARGIN 同步：树体宽度推算用）
const MARGIN_X: float = 24.0
const TITLE_COLOR: Color = Color(0.90, 0.93, 1.0)
const DIM_TEXT_COLOR: Color = Color(0.62, 0.65, 0.72)


# ── 视图模型（setup/set_tree 注入，layout 消费）────────────

var _canvas: Control = null
var _font: Font = null
var _world: Dictionary = {}
var _current_world_id: String = ""
var nodes: Array = []
var edges: Array = []
var sorted_ids: Array = []
var numbers: Dictionary = {}

# ── 交互状态（调用方持久化于自身，可经对接口读回）──────────
var zoom: float = 1.0
var pan: Vector2 = Vector2.ZERO
var legend_scroll: float = 0.0
var hover_id: String = ""
var selected_id: String = ""

# ── 布局产物：绘制计划（paint 消费；layout 重建）────────────
var _node_draws: Array = []
var _edge_draws: Array = []
var _legend_draws: Array = []

# ── 布局输出（layout 后有效，供命中检测与测试校验）──────────
var origin: Vector2 = Vector2.ZERO
var tree_rect: Rect2 = Rect2()
var body_rect: Rect2 = Rect2()
var legend_rect: Rect2 = Rect2()
var node_rects: Dictionary = {}
var legend_rects: Dictionary = {}


## 注入树数据（节点/边/保存顺序编号），展开/收起时间线后不再变。
##
## Args:
##     p_nodes: 节点列表（TimelineLayout.build 的 nodes）。
##     p_edges: 边列表（TimelineLayout.build 的 edges）。
##     p_sorted_ids: 保存顺序编号数据源（save_order_ids 输出）。
func set_tree(p_nodes: Array, p_edges: Array, p_sorted_ids: Array) -> void:
	nodes = p_nodes
	edges = p_edges
	sorted_ids = p_sorted_ids


## 注入渲染上下文：画布/字体/世界视图数据/交互状态镜像。
##
## Args:
##     canvas: 绘制的 Control（树体宽度推算 + draw_* 调用处）。
##     font: 等宽字体。
##     world: 当前展开行的世界摘要（含 game_time/live_origin）。
##     current_world_id: 引擎当前加载的世界 id（"最后进入"标注）。
##     p_zoom: 缩放倍率（调用方持久化的上一帧值）。
##     p_pan: 平移偏移（同步持久化值）。
##     p_legend_scroll: 图例滚动（同步持久化值）。
##     p_hover_id: 悬停节点 id。
##     p_selected_id: 选中节点 id（操作面板目标）。
func setup(
	canvas: Control, font: Font, world: Dictionary,
	current_world_id: String,
	p_zoom: float = 1.0, p_pan: Vector2 = Vector2.ZERO,
	p_legend_scroll: float = 0.0, p_hover_id: String = "",
	p_selected_id: String = "",
) -> void:
	_canvas = canvas
	_font = font
	_world = world
	_current_world_id = current_world_id
	zoom = p_zoom
	pan = p_pan
	legend_scroll = p_legend_scroll
	hover_id = p_hover_id
	selected_id = p_selected_id


## 展开时自动适配：长树缩小到视口（短树保持 1.0 上限）。
func fit_zoom() -> void:
	zoom = 1.0
	if _canvas == null or _canvas.size.x <= 10.0:
		return
	var max_depth: float = 0.0
	var max_slot: float = 0.0
	for n in nodes:
		max_depth = maxf(max_depth, float(n["depth"]))
		max_slot = maxf(max_slot, float(n["slot"]))
	var body_w: float = _canvas.size.x - MARGIN_X * 2 - 28 - LEGEND_W
	var body_h: float = INLINE_H - HEADER_H - 8
	var fit_w: float = body_w / maxf(1.0, (max_depth + 1) * H_STEP)
	var fit_h: float = body_h / maxf(1.0, (max_slot + 1) * V_STEP)
	zoom = clampf(minf(1.0, minf(fit_w, fit_h)), ZOOM_MIN, 1.0)


## 收起时间线时清空命中几何（防止陈旧矩形参与命中判定）。
func reset() -> void:
	origin = Vector2.ZERO
	tree_rect = Rect2()
	body_rect = Rect2()
	legend_rect = Rect2()
	node_rects.clear()
	legend_rects.clear()
	_node_draws.clear()
	_edge_draws.clear()
	_legend_draws.clear()


## 滚轮：图例列滚动列表；树区缩放（以光标为锚，保持光标下树点不动）。
##
## Args:
##     dir: 滚轮方向（<0 放大、>0 缩小）。
##     pos: 光标位置（树体坐标判定）。
func wheel(dir: float, pos: Vector2) -> void:
	if legend_rect.has_point(pos):
		legend_scroll += dir
		return
	var factor: float = 1.15 if dir < 0.0 else 1.0 / 1.15
	var old_zoom: float = zoom
	zoom = clampf(zoom * factor, ZOOM_MIN, ZOOM_MAX)
	if zoom != old_zoom and body_rect.has_point(pos):
		var rel: Vector2 = pos - body_rect.position
		pan = rel - (rel - pan) * (zoom / old_zoom)



## 布局计算（纯几何，可单测；须在绘制阶段调用）。
##
## 面板高度 = min(INLINE_H, 列表视口剩余空间)（调用方计算 row_rect
## 时已裁剪出界）；节点/边/图例逐元素剔除面板可见区之外元素——
## 内容永远不溢出列表区压到页脚。paint() 只消费布局结果。
##
## Args:
##     row_rect: 展开行矩形（行下方 + GAP 起算）。
func layout(row_rect: Rect2) -> void:
	if _canvas == null or _font == null:
		return
	var avail_h: float = (_canvas.size.y - 56.0) - (row_rect.end.y + GAP)
	var panel_h: float = minf(INLINE_H, avail_h)
	if panel_h <= 0.0:
		return
	tree_rect = Rect2(
		row_rect.position.x, row_rect.end.y + GAP,
		row_rect.size.x, panel_h)

	# 图例列
	legend_rect = Rect2(tree_rect.end.x - LEGEND_W, tree_rect.position.y,
		LEGEND_W, tree_rect.size.y)
	var legend_body := Rect2(
		legend_rect.position + Vector2(8, HEADER_H),
		Vector2(LEGEND_W - 16, legend_rect.size.y - HEADER_H))

	# 树视口（图例左侧）
	body_rect = Rect2(tree_rect.position + Vector2(14, HEADER_H),
		Vector2(tree_rect.size.x - 28 - LEGEND_W, tree_rect.size.y - HEADER_H - 8))

	# 编号：按保存顺序（saved_at 真实创建时刻，同秒游戏时间兜底）
	numbers.clear()
	for i in sorted_ids.size():
		numbers[str(sorted_ids[i])] = i + 1

	# 几何：缩放 + 平移（树小于视口时居中不可拖，大于时钳制可拖）
	var max_depth: float = 0.0
	var max_slot: float = 0.0
	for n in nodes:
		max_depth = maxf(max_depth, float(n["depth"]))
		max_slot = maxf(max_slot, float(n["slot"]))
	var tree_w: float = (max_depth + 1) * H_STEP * zoom
	var tree_h: float = (max_slot + 1) * V_STEP * zoom
	if tree_w >= body_rect.size.x:
		pan.x = clampf(pan.x, body_rect.size.x - tree_w, 0.0)
	else:
		pan.x = (body_rect.size.x - tree_w) * 0.5
	if tree_h >= body_rect.size.y:
		pan.y = clampf(pan.y, body_rect.size.y - tree_h, 0.0)
	else:
		pan.y = (body_rect.size.y - tree_h) * 0.5
	origin = body_rect.position + pan

	# 节点位置表 + 边/节点绘制计划（逐元素剔除面板可见区之外元素）
	var visible_rect: Rect2 = tree_rect.grow(NODE_R)
	var pos_map: Dictionary = {}
	for n in nodes:
		pos_map[n["id"]] = origin + Vector2(
			float(n["depth"]) * H_STEP * zoom,
			float(n["slot"]) * V_STEP * zoom)
	node_rects.clear()
	_node_draws.clear()
	_edge_draws.clear()
	for e in edges:
		if not pos_map.has(e[0]) or not pos_map.has(e[1]):
			continue
		var a: Vector2 = pos_map[e[0]]
		var b: Vector2 = pos_map[e[1]]
		if not visible_rect.intersects(Rect2(a, b - a).abs()):
			continue
		_edge_draws.append({"a": a, "b": b})
	for n in nodes:
		var pos: Vector2 = pos_map[n["id"]]
		if not visible_rect.has_point(pos):
			continue
		var hovered: bool = str(n["id"]) == hover_id
		var selected: bool = str(n["id"]) == selected_id
		var fill: Color
		if bool(n["is_live"]):
			fill = LIVE_HOVER_COLOR if hovered else LIVE_COLOR
		elif str(n.get("suffix", "")) != "manual":
			fill = AUTO_HOVER_COLOR if hovered else AUTO_COLOR
		else:
			fill = NODE_HOVER_COLOR if hovered else NODE_COLOR
		var glyph: String = "★" if bool(n["is_live"]) else str(numbers.get(n["id"], ""))
		_node_draws.append({
			"id": n["id"], "pos": pos, "fill": fill, "glyph": glyph,
			"is_live": bool(n["is_live"]), "selected": selected,
		})
		node_rects[n["id"]] = Rect2(pos - Vector2(30, 30), Vector2(60, 60))

	# 图例（可滚动，全部节点可达；顺序 = 保存顺序编号）。
	# LIVE 行仅当树中确实存在独立当前点（auto 来源时省略）
	var legend_rows: Array = []
	for n in nodes:
		if str(n["id"]) == TimelineLayout.LIVE_ID:
			legend_rows.append(TimelineLayout.LIVE_ID)
			break
	legend_rows.append_array(sorted_ids)
	var visible_rows: int = maxi(0, int(legend_body.size.y / LEGEND_ROW_H))
	legend_scroll = clampf(legend_scroll, 0.0,
		maxf(0.0, legend_rows.size() - visible_rows))
	legend_rects.clear()
	_legend_draws.clear()
	for i in range(visible_rows):
		var row_idx: int = i + int(legend_scroll)
		if row_idx >= legend_rows.size():
			break
		var id: String = String(legend_rows[row_idx])
		var row_rc := Rect2(legend_body.position.x,
			legend_body.position.y + i * LEGEND_ROW_H,
			legend_body.size.x, LEGEND_ROW_H - 2)
		legend_rects[id] = row_rc
		# 当前时间点（LIVE 或当前 auto 记录）行显示 ★
		var is_current: bool = false
		for n in nodes:
			if str(n["id"]) == id and bool(n["is_live"]):
				is_current = true
				break
		var num: String = "★" if is_current else str(numbers.get(id, ""))
		_legend_draws.append({
			"rc": row_rc, "text": "%s  %s" % [num, _legend_text(id)],
			"hovered": id == hover_id,
		})


## 绘制布局产物（须在 _draw 阶段调用；与 layout 之间输入不变）。
func paint() -> void:
	if _canvas == null or _font == null or tree_rect.size.y <= 0.0:
		return
	_canvas.draw_rect(tree_rect, BG_COLOR)
	_canvas.draw_rect(tree_rect, Color(1, 1, 1, 0.10), false, 1.0)

	var title: String = TranslationServer.tr("ui.saves.timeline")
	if str(_world.get("world_id", "")) == _current_world_id:
		title += TranslationServer.tr("ui.saves.last_entered")
	_canvas.draw_string(_font, tree_rect.position + Vector2(14, 18), title,
		HORIZONTAL_ALIGNMENT_LEFT, -1, 14, TITLE_COLOR)

	_canvas.draw_rect(legend_rect, Color(0, 0, 0, 0.25))
	_canvas.draw_string(_font, legend_rect.position + Vector2(10, 18),
		TranslationServer.tr("ui.saves.snapshot_list"),
		HORIZONTAL_ALIGNMENT_LEFT, -1, 13, TITLE_COLOR)

	for e in _edge_draws:
		var a: Vector2 = e["a"]
		var b: Vector2 = e["b"]
		var dir: Vector2 = (b - a).normalized()
		_canvas.draw_line(a + dir * NODE_R, b - dir * NODE_R,
			EDGE_COLOR, 2.0)

	for nd in _node_draws:
		var pos: Vector2 = nd["pos"]
		_canvas.draw_circle(pos, NODE_R, nd["fill"])
		_canvas.draw_arc(pos, NODE_R, 0.0, TAU, 32, Color(1, 1, 1, 0.35), 1.0)
		if bool(nd["selected"]):
			_canvas.draw_arc(pos, NODE_R + 3.0, 0.0, TAU, 32, SELECT_COLOR, 2.0)
		_canvas.draw_string(_font, pos + Vector2(-12, 5), str(nd["glyph"]),
			HORIZONTAL_ALIGNMENT_CENTER, 24, 11, Color(0.05, 0.06, 0.10, 0.95))
		if bool(nd["is_live"]):
			_canvas.draw_string(_font, pos + Vector2(-34, NODE_R + 15),
				TranslationServer.tr("ui.saves.current_point"),
				HORIZONTAL_ALIGNMENT_LEFT, -1, 13, LIVE_COLOR)
			var gt: String = SaveInfoFormatter.game_time_string(
				int(_world.get("game_time", 0)))
			_canvas.draw_string(_font, pos + Vector2(-34, NODE_R + 30), gt,
				HORIZONTAL_ALIGNMENT_LEFT, -1, 12, HINT_COLOR)

	for ld in _legend_draws:
		var row_rc: Rect2 = ld["rc"]
		if bool(ld["hovered"]):
			_canvas.draw_rect(row_rc, Color(1, 1, 1, 0.12))
		_canvas.draw_string(_font, row_rc.position + Vector2(4, 13), str(ld["text"]),
			HORIZONTAL_ALIGNMENT_LEFT, -1, 12, DIM_TEXT_COLOR)

## 命中树视口内的节点，返回节点 id（未命中返回空串）。
##
## Args:
##     pos: 光标位置。
func hit_node(pos: Vector2) -> String:
	if not body_rect.has_point(pos):
		return ""
	for id in node_rects:
		if node_rects[id].has_point(pos):
			return String(id)
	return ""


## 命中图例行，返回节点 id（未命中返回空串）。
##
## Args:
##     pos: 光标位置。
func hit_legend(pos: Vector2) -> String:
	if not legend_rect.has_point(pos):
		return ""
	for id in legend_rects:
		if legend_rects[id].has_point(pos):
			return String(id)
	return ""


## 图例行文本：来源标识 + 时间标签。
func _legend_text(id: String) -> String:
	for n in nodes:
		if str(n["id"]) != id:
			continue
		if bool(n["is_live"]):
			return TranslationServer.tr("ui.saves.current_point_line").format({
				"time": SaveInfoFormatter.game_time_string(
					int(_world.get("game_time", 0))),
			})
		var prefix: String = TranslationServer.tr("ui.saves.auto_prefix") \
			if str(n.get("suffix", "")) != "manual" else ""
		return prefix + node_label(n)
	return ""


## 快照节点标签：优先游戏时间，缺失时用真实保存时间（面板标题复用）。
func node_label(node: Dictionary) -> String:
	var gt: int = int(node.get("time", 0))
	if gt > 0:
		return SaveInfoFormatter.game_time_string(gt)
	return SaveInfoFormatter.datetime_string(float(node.get("saved_at", 0.0)))
