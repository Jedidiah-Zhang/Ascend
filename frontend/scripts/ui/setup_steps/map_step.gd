"""创建世界 — 地图生成调参步骤（Issue #8）。

可调参数:
  - 种子：默认随机定案，可「随机」重新掷，或点击数值弹输入框手输
  - 大陆占比：自绘滑块拖拽，10%-90%（步进 1%）
  - 地图尺寸：三档（小 60×36 / 中 100×60 / 大 150×90 km），
    只影响世界规模不影响形状；尺寸改变生成范围，切换刷新预览

预览：
  参数变化即请求 map_preview（后端秒级返回）；后端串行处理，
  响应按到达顺序应用，在途请求自动合并（响应返回后若参数已
  变化则补发最新参数，无需防抖定时器/seq 丢弃）；预览为当前
  尺寸的海拔缩略图按高度着色，叠加实测陆地占比。

气候图层视图：
  请求固定携带 layers（temp/rain/climate，后端一次算全）；
  预览面板顶部可切换 地形 / 温度 / 降雨 / 气候 视图——切换
  仅换着色函数，零往返零重算。响应缺图层字段（旧后端）时
  自动降级为仅地形视图。温度/降雨为海陆全域场（海域温度 =
  海面温度，无深海伪影）；气候带仅陆地有意义，气候视图海域
  保持深蓝。

依赖：无（流程首步）。产出：
{seed, gen_params: {land_ratio, width_km, height_km}}。

测试约定：request_sender 可注入（缺省走 Connection.send），
preview 响应用 on_preview_response(payload) 喂入——纯逻辑可单测。
"""

class_name MapSetupStep
extends SetupStep

## 种子为协议层 hex 字符串（"" / "0" = 随机占位——后端预览时随机定案，
## 响应 payload.seed 回传 hex；与后端存档契约 manifest.SEED_MAX 一致：
## 256-bit → 64 字符小写 hex，见 docs/世界框架/随机系统/设计.md）
const SEED_HEX_MAX_LEN: int = 64

# ── 可调参数范围 ──────────────────────────────────────────

const LAND_RATIO_MIN: float = 0.10
const LAND_RATIO_MAX: float = 0.90
const LAND_RATIO_STEP: float = 0.01

## 地图尺寸档位（与后端 ContinentParams 5:3 比例一致）。
const SIZE_OPTIONS: Array = [
	{"label_key": "ui.map.size_small", "width_km": 60.0, "height_km": 36.0},
	{"label_key": "ui.map.size_medium", "width_km": 100.0, "height_km": 60.0},
	{"label_key": "ui.map.size_large", "width_km": 150.0, "height_km": 90.0},
]

# ── 视觉常量（自绘） ─────────────────────────────────────

const TEXT_COLOR: Color = Color(0.90, 0.90, 0.95)
const DIM_COLOR: Color = Color(0.62, 0.65, 0.72)
const ACCENT_COLOR: Color = Color(0.32, 0.55, 0.85)
const BUTTON_COLOR: Color = Color(0.18, 0.22, 0.32)
const BUTTON_HOVER_COLOR: Color = Color(0.26, 0.34, 0.48)
const PANEL_COLOR: Color = Color(0.09, 0.11, 0.17, 0.97)
const SLIDER_TRACK_COLOR: Color = Color(0.22, 0.26, 0.36)
const SLIDER_KNOB_COLOR: Color = Color(0.45, 0.70, 0.95)
const PREVIEW_BG_COLOR: Color = Color(0.03, 0.04, 0.08)

const LABEL_FONT_SIZE: int = 14
const VALUE_FONT_SIZE: int = 16
const SMALL_FONT_SIZE: int = 12

## 预览渲染单元格（px）；网格尺寸来自响应 payload（随地图尺寸档位变化）
const PREVIEW_CELL: float = 4.0

# ── 气候图层视图 ──────────────────────────────────────────

## 预览视图定义: {key, label_key, field}——field 为预览 payload 字段名，
## 缺该字段（旧后端）时视图不可用（自动降级地形）。
const VIEWS: Array = [
	{"key": "elevation", "label_key": "ui.map.view_elevation", "field": "elevation"},
	{"key": "temp", "label_key": "ui.map.view_temp", "field": "temperature"},
	{"key": "rain", "label_key": "ui.map.view_rain", "field": "rainfall"},
	{"key": "climate", "label_key": "ui.map.view_climate", "field": "climate"},
]
const VIEW_BUTTON_W: float = 64.0
const VIEW_BUTTON_H: float = 26.0
const VIEW_BUTTON_GAP: float = 8.0

## 视图切换按钮选中色
const SELECTED_COLOR: Color = Color(0.32, 0.55, 0.85)

# ── 状态 ──────────────────────────────────────────────────

## 当前种子（"" = 未定案/随机占位；后端预览响应回传后定案为 hex）
var _seed: String = ""
## 目标陆地占比
var _land_ratio: float = 0.55
## 地图尺寸档位索引（SIZE_OPTIONS）
var _size_index: int = 1

## 预览请求发送器（测试注入；缺省 Connection.send）
var request_sender: Callable = _send_default

## 最新预览数据（on_preview_response 写入）
var _preview: Dictionary = {}
## 预览请求已发出待响应（在途至多 1 个）
var _preview_pending: bool = false
## 在途请求发出时携带的种子（响应 seed 回写判定：仅占位才定案）
var _pending_seed: String = ""
## 在途期间参数再次变化：响应到达后补发最新参数
var _preview_dirty: bool = false
## 待发送的最新预览请求（_request_preview 时起草）
var _draft_request: Dictionary = {}

## 种子输入框弹层打开中
var _editing_seed: bool = false
## 种子输入框（容器注入的 LineEdit，测试可留空）
var _seed_input: LineEdit = null
## 悬停命中: "random" / "seed" / "slider" / ""
var _hover: String = ""
## 滑块拖拽中
var _slider_dragging: bool = false

## 当前预览视图 key（VIEWS 数组项；缺图层时自动降级 "elevation"）
var _view_mode: String = "elevation"

## 鼠标悬停的地图格子（(-1,-1) = 不在地图上），信息行取值用
var _hover_cell: Vector2i = Vector2i(-1, -1)
## 最近一次绘制的地图几何（_map_cell_at 输入命中用）
var _map_origin: Vector2 = Vector2.ZERO
var _map_cell: float = 0.0
var _map_w: int = 0
var _map_h: int = 0

## 步骤内容区矩形（draw_page 时记录，输入命中用）
var _page_rect: Rect2 = Rect2()

## 预览调色/信息文本（纯逻辑 RefCounted，绘制前同步状态）
var _palette: MapPreviewPalette = MapPreviewPalette.new()


func _send_default(message: Dictionary) -> void:
	Connection.send(message, func(msg: Dictionary) -> void:
		if msg.get("type", "") == "error":
			on_preview_failed()
		else:
			on_preview_response(msg.get("payload", {})))


# ── 生命周期（SetupStep 契约） ────────────────────────────

func step_id() -> String:
	return "map"


func title() -> String:
	return TranslationServer.tr("ui.map.title")


func setup(params: Dictionary) -> void:
	# 种子 = 协议层 hex 字符串；"" / "0" 视为未定案（随机占位），
	# 由后端预览时随机定案并回传（种子唯一随机源 = 后端）。
	_seed = str(params.get("seed", ""))
	if _seed == "0":
		_seed = ""
	var gen: Dictionary = params.get("gen_params", {})
	var ratio: Variant = gen.get("land_ratio")
	if ratio is float or ratio is int:
		_land_ratio = clampf(float(ratio), LAND_RATIO_MIN, LAND_RATIO_MAX)
	_size_index = _match_size(
		gen.get("width_km"), gen.get("height_km"))
	_preview = {}
	_preview_pending = false
	_preview_dirty = false
	_request_preview()


## 从 gen_params 恢复尺寸档位；缺失/不匹配返回默认（中）。
func _match_size(width_km: Variant, height_km: Variant) -> int:
	if width_km == null or height_km == null:
		return 1
	for i in SIZE_OPTIONS.size():
		var opt: Dictionary = SIZE_OPTIONS[i]
		if is_equal_approx(float(width_km), opt["width_km"]) \
				and is_equal_approx(float(height_km), opt["height_km"]):
			return i
	return 1


func get_params() -> Dictionary:
	var opt: Dictionary = SIZE_OPTIONS[_size_index]
	return {
		"seed": _seed,
		"gen_params": {
			"land_ratio": _land_ratio,
			"width_km": opt["width_km"],
			"height_km": opt["height_km"],
		},
	}


func validate() -> String:
	return ""


# ── 预览请求 ──────────────────────────────────────────────

func _request_preview() -> void:
	"""参数变化即起草最新请求；在途时只标记脏，响应后补发。

	后端串行处理，在途请求至多 1 个——响应按到达顺序应用，
	无需 seq 丢弃或防抖定时器。
	"""
	var opt: Dictionary = SIZE_OPTIONS[_size_index]
	_draft_request = SaveApi.preview_request(
		_seed, _land_ratio, opt["width_km"], opt["height_km"])
	if _preview_pending:
		_preview_dirty = true
	else:
		_send_preview()


func _send_preview() -> void:
	_preview_pending = true
	_pending_seed = _seed
	request_sender.call(_draft_request)


func _on_request_done() -> void:
	_preview_pending = false
	if _preview_dirty:
		_preview_dirty = false
		_send_preview()


func on_preview_response(payload: Dictionary) -> void:
	"""后端 map_preview 响应：应用后补发变化后的最新参数。

	种子定案：payload.seed 为后端回传的 hex 种子。仅在发出请求时
	种子为占位（未定案）才用响应回写——手输/恢复的显式种子由
	用户持有，在途旧响应不得覆盖（竞态防护）。
	"""
	_on_request_done()
	if payload.is_empty():
		return
	_preview = payload
	if _pending_seed.is_empty():
		var seed: Variant = payload.get("seed")
		if seed is String and not str(seed).is_empty():
			_seed = str(seed)


func on_preview_failed() -> void:
	"""预览请求失败（error 消息）：保留旧预览，补发变化后的最新参数。"""
	_on_request_done()


# ── 绘制 ──────────────────────────────────────────────────

func draw_page(canvas: Control, rect: Rect2, font: Font) -> void:
	_page_rect = rect

	# 左侧：参数区
	var left_w: float = rect.size.x * 0.42
	var param_rect := Rect2(rect.position, Vector2(left_w, rect.size.y))
	_draw_params(canvas, param_rect, font)

	# 右侧：预览面板
	var preview_rect := Rect2(
		rect.position + Vector2(left_w + 18.0, 0.0),
		Vector2(rect.size.x - left_w - 18.0, rect.size.y),
	)
	_draw_preview(canvas, preview_rect, font)


func _draw_params(canvas: Control, rect: Rect2, font: Font) -> void:
	# 种子行
	draw_label(canvas, font, rect.position + Vector2(0, 26), TranslationServer.tr("ui.map.seed"), LABEL_FONT_SIZE)
	var seed_rect := Rect2(rect.position + Vector2(0, 40), Vector2(rect.size.x - 130.0, 34))
	_draw_value_box(canvas, seed_rect, font, _display_seed(), _hover == "seed")
	var random_rect := Rect2(
		rect.position + Vector2(rect.size.x - 118.0, 40), Vector2(112.0, 34))
	_draw_button(canvas, random_rect, font, TranslationServer.tr("ui.common.random"), _hover == "random")
	_random_rect = random_rect

	# 大陆占比行
	var ratio_y: float = 40.0 + 34.0 + 34.0
	draw_label(canvas, font, rect.position + Vector2(0, ratio_y + 16),
		TranslationServer.tr("ui.map.land_ratio").format({"percent": int(round(_land_ratio * 100.0))}), LABEL_FONT_SIZE)
	var slider_rect := Rect2(rect.position + Vector2(0, ratio_y + 30),
		Vector2(rect.size.x - 160.0, 24))
	_draw_slider(canvas, slider_rect, font)
	_slider_rect = slider_rect

	# 地图尺寸行（三档选择，切换不刷新预览）
	var size_y: float = ratio_y + 30.0 + 24.0 + 26.0
	draw_label(canvas, font, rect.position + Vector2(0, size_y + 16),
		TranslationServer.tr("ui.map.map_size").format({
			"width": SIZE_OPTIONS[_size_index]["width_km"],
			"height": SIZE_OPTIONS[_size_index]["height_km"],
		}), LABEL_FONT_SIZE)
	var btn_w: float = (rect.size.x - 26.0) / 3.0
	_size_rects.clear()
	for i in SIZE_OPTIONS.size():
		var size_rect := Rect2(
			rect.position + Vector2(i * (btn_w + 13.0), size_y + 30),
			Vector2(btn_w, 34))
		_size_rects.append(size_rect)
		_draw_size_button(canvas, size_rect, font, i)

	# 说明
	draw_label(canvas, font, rect.position + Vector2(0, rect.size.y - 20),
		TranslationServer.tr("ui.map.description"),
		SMALL_FONT_SIZE, DIM_COLOR)


func _draw_preview(canvas: Control, rect: Rect2, font: Font) -> void:
	canvas.draw_rect(rect, PREVIEW_BG_COLOR)
	canvas.draw_rect(rect, Color(1, 1, 1, 0.12), false, 1.0)
	draw_label(canvas, font, rect.position + Vector2(14, 24), TranslationServer.tr("ui.map.preview_title"), LABEL_FONT_SIZE)

	# 视图切换按钮（地形 / 温度 / 降雨 / 气候）——与标题同行，右对齐
	var view_y: float = rect.position.y + 8.0
	_view_rects.clear()
	var view_x: float = rect.position.x + rect.size.x - 14.0 \
			- VIEWS.size() * VIEW_BUTTON_W \
			- (VIEWS.size() - 1) * VIEW_BUTTON_GAP
	for i in VIEWS.size():
		var v_rect := Rect2(
			Vector2(view_x + i * (VIEW_BUTTON_W + VIEW_BUTTON_GAP), view_y),
			Vector2(VIEW_BUTTON_W, VIEW_BUTTON_H))
		_view_rects.append(v_rect)
		_draw_view_button(canvas, v_rect, font, i)

	if _preview.is_empty():
		var waiting: String = TranslationServer.tr("ui.map.generating") if _preview_pending else TranslationServer.tr("ui.map.waiting")
		draw_label(canvas, font,
			rect.position + Vector2(14, rect.size.y * 0.5), waiting, VALUE_FONT_SIZE)
		return

	var elev: Array = _preview.get("elevation", [])
	var w: int = int(_preview.get("width", 0))
	var h: int = int(_preview.get("height", 0))
	if elev.size() < w * h or w <= 0 or h <= 0:
		draw_label(canvas, font, rect.position + Vector2(14, rect.size.y * 0.5),
			TranslationServer.tr("ui.map.preview_error"), SMALL_FONT_SIZE, Color(0.95, 0.50, 0.45))
		return

	# 当前视图数据（缺图层 → 降级地形）
	var view: Dictionary = _current_view()
	var field: Array = _preview.get(view["field"], [])

	# 地图（按比例缩放居中，位于标题行下方）
	var map_top: float = view_y + VIEW_BUTTON_H + 10.0
	var cell: float = minf(
		PREVIEW_CELL,
		minf((rect.size.x - 28.0) / w, (rect.size.y - (map_top - rect.position.y) - 30.0) / h),
	)
	var map_size := Vector2(w * cell, h * cell)
	var map_origin: Vector2 = rect.position + Vector2(
		(rect.size.x - map_size.x) * 0.5, map_top - rect.position.y)
	_map_origin = map_origin
	_map_cell = cell
	_map_w = w
	_map_h = h
	for y in h:
		for x in w:
			var i: int = y * w + x
			var e: float = float(elev[i])
			canvas.draw_rect(Rect2(
				map_origin + Vector2(x * cell, y * cell), Vector2(cell, cell)),
				_color(view, e, field[i]))

	# 信息行：当前视图数值范围 + 鼠标位置值（气候视图为鼠标处分类）
	_sync_palette()
	var info: String = _palette.info_line(view, field)
	draw_label(canvas, font, rect.position + Vector2(14, rect.size.y - 22),
		info, SMALL_FONT_SIZE, DIM_COLOR)


func _current_view() -> Dictionary:
	"""当前视图定义；所请求的图层在响应中缺失（旧后端）时降级地形。"""
	var view: Dictionary = {}
	for v in VIEWS:
		if v["key"] == _view_mode:
			view = v
			break
	if view.is_empty() or not _preview.has(view["field"]):
		return VIEWS[0]
	return view


## 同步调色板状态（地图尺寸/悬停格/预览数据；绘制前调用）。
func _sync_palette() -> void:
	_palette.preview = _preview
	_palette.hover_cell = _hover_cell
	_palette.map_w = _map_w
	_palette.map_h = _map_h


## 单格着色：委托调色板（海域/图层语义见 MapPreviewPalette.cell_color）。
func _color(view: Dictionary, e: float, value: Variant) -> Color:
	return _palette.cell_color(view, e, value)


# ── 自绘控件 ──────────────────────────────────────────────

var _seed_rect: Rect2 = Rect2()
var _random_rect: Rect2 = Rect2()
var _slider_rect: Rect2 = Rect2()
var _size_rects: Array = []
var _view_rects: Array = []


func _draw_view_button(canvas: Control, rect: Rect2, font: Font, index: int) -> void:
	var view: Dictionary = VIEWS[index]
	var selected: bool = view["key"] == _current_view()["key"]
	var hovered: bool = _hover == "view%d" % index
	var fill: Color
	if selected:
		fill = SELECTED_COLOR
	elif hovered:
		fill = BUTTON_HOVER_COLOR
	else:
		fill = BUTTON_COLOR
	canvas.draw_rect(rect, fill)
	canvas.draw_rect(rect, Color(1, 1, 1, 0.12), false, 1.0)
	var w: float = font.get_string_size(TranslationServer.tr(view["label_key"]), HORIZONTAL_ALIGNMENT_LEFT,
		-1, SMALL_FONT_SIZE).x
	draw_label(canvas, font,
		rect.position + Vector2((rect.size.x - w) * 0.5, rect.size.y * 0.5 + 5),
		TranslationServer.tr(view["label_key"]), SMALL_FONT_SIZE, TEXT_COLOR)


func _draw_value_box(canvas: Control, rect: Rect2, font: Font, text: String, hovered: bool) -> void:
	canvas.draw_rect(rect, BUTTON_HOVER_COLOR if hovered else BUTTON_COLOR)
	canvas.draw_rect(rect, Color(1, 1, 1, 0.12), false, 1.0)
	draw_label(canvas, font, rect.position + Vector2(10, rect.size.y * 0.5 + 6),
		text, VALUE_FONT_SIZE)
	_seed_rect = rect


func _draw_button(canvas: Control, rect: Rect2, font: Font, label: String, hovered: bool) -> void:
	canvas.draw_rect(rect, BUTTON_HOVER_COLOR if hovered else BUTTON_COLOR)
	canvas.draw_rect(rect, Color(1, 1, 1, 0.12), false, 1.0)
	var w: float = font.get_string_size(label, HORIZONTAL_ALIGNMENT_LEFT, -1, SMALL_FONT_SIZE).x
	draw_label(canvas, font,
		rect.position + Vector2((rect.size.x - w) * 0.5, rect.size.y * 0.5 + 5),
		label, SMALL_FONT_SIZE, TEXT_COLOR)


func _draw_slider(canvas: Control, rect: Rect2, font: Font) -> void:
	var track := Rect2(rect.position, Vector2(rect.size.x - 22.0, 10.0))
	canvas.draw_rect(track.grow(2.0), SLIDER_TRACK_COLOR)
	var ratio01: float = (_land_ratio - LAND_RATIO_MIN) / (LAND_RATIO_MAX - LAND_RATIO_MIN)
	var knob_x: float = track.position.x + ratio01 * track.size.x
	var knob_rect := Rect2(knob_x - 8.0, rect.position.y - 5.0, 16.0, 20.0)
	canvas.draw_rect(knob_rect, SLIDER_KNOB_COLOR if _slider_dragging or _hover == "slider" else ACCENT_COLOR)
	var pct_text: String = "%d%%" % int(round(_land_ratio * 100.0))
	draw_label(canvas, font,
		rect.position + Vector2(rect.size.x - 14.0, 8.0), pct_text, SMALL_FONT_SIZE)


func _draw_size_button(canvas: Control, rect: Rect2, font: Font, index: int) -> void:
	var selected: bool = index == _size_index
	var hovered: bool = _hover == "size%d" % index
	var fill: Color
	if selected:
		fill = ACCENT_COLOR
	elif hovered:
		fill = BUTTON_HOVER_COLOR
	else:
		fill = BUTTON_COLOR
	canvas.draw_rect(rect, fill)
	canvas.draw_rect(rect, Color(1, 1, 1, 0.12), false, 1.0)
	var label: String = "%s\n%dx%d" % [TranslationServer.tr(SIZE_OPTIONS[index]["label_key"]),
		SIZE_OPTIONS[index]["width_km"], SIZE_OPTIONS[index]["height_km"]]
	var w: float = font.get_string_size(label, HORIZONTAL_ALIGNMENT_LEFT, -1, SMALL_FONT_SIZE).x
	draw_label(canvas, font,
		rect.position + Vector2((rect.size.x - w) * 0.5, rect.size.y * 0.5 + 5),
		label, SMALL_FONT_SIZE, TEXT_COLOR)


func draw_label(canvas: Control, font: Font, pos: Vector2, text: String, size: int, color: Color = TEXT_COLOR) -> void:
	canvas.draw_string(font, pos, text, HORIZONTAL_ALIGNMENT_LEFT, -1, size, color)


# ── 输入 ──────────────────────────────────────────────────

func handle_input(event: InputEvent, _rect: Rect2) -> bool:
	if _editing_seed:
		# 输入框打开期间：回车由容器 LineEdit 回调处理；Esc 关闭
		return false
	if event is InputEventMouseMotion:
		var new_hover: String = _hover_at(event.position)
		if _slider_dragging:
			_set_ratio_from_x(event.position.x)
			return true
		var new_cell: Vector2i = _map_cell_at(event.position)
		if new_hover != _hover or new_cell != _hover_cell:
			_hover = new_hover
			_hover_cell = new_cell
			return true
		return false
	if event is InputEventMouseButton and event.pressed \
			and event.button_index == MOUSE_BUTTON_LEFT:
		var hit: String = _hover_at(event.position)
		match hit:
			"seed":
				return _open_seed_input()
			"random":
				_randomize_seed()
				return true
			"slider":
				_slider_dragging = true
				_set_ratio_from_x(event.position.x)
				return true
			_:
				if hit.begins_with("size"):
					_set_size(int(hit.trim_prefix("size")))
					return true
				if hit.begins_with("view"):
					_set_view_mode(int(hit.trim_prefix("view")))
					return true
	return false


func handle_release(event: InputEvent) -> bool:
	"""拖拽释放（容器在 mouse button 抬起时调用）。"""
	if event is InputEventMouseButton and not event.pressed \
			and event.button_index == MOUSE_BUTTON_LEFT and _slider_dragging:
		_slider_dragging = false
		return true
	return false


func on_escape() -> bool:
	"""Esc：关闭种子输入框则消费，否则不处理。"""
	if _editing_seed:
		_close_seed_input()
		return true
	return false


func _hover_at(pos: Vector2) -> String:
	if _random_rect.has_point(pos):
		return "random"
	if _seed_rect.has_point(pos):
		return "seed"
	if _slider_rect.has_point(pos):
		return "slider"
	for i in _size_rects.size():
		if _size_rects[i].has_point(pos):
			return "size%d" % i
	for i in _view_rects.size():
		if _view_rects[i].has_point(pos):
			return "view%d" % i
	return ""


## 鼠标位置 → 地图格子；不在地图内返回 (-1, -1)。
func _map_cell_at(pos: Vector2) -> Vector2i:
	if _map_cell <= 0.0:
		return Vector2i(-1, -1)
	var rel: Vector2 = pos - _map_origin
	if rel.x < 0.0 or rel.y < 0.0 \
			or rel.x >= _map_w * _map_cell or rel.y >= _map_h * _map_cell:
		return Vector2i(-1, -1)
	return Vector2i(int(rel.x / _map_cell), int(rel.y / _map_cell))


## 切换地图尺寸档位（尺寸改变生成范围 → 重新请求预览）。
func _set_size(index: int) -> void:
	if index < 0 or index >= SIZE_OPTIONS.size() or index == _size_index:
		return
	_size_index = index
	_request_preview()


## 切换预览视图（地形/温度/降雨/气候）：仅换着色，不重请求。
## 响应缺该图层字段（旧后端）时忽略并保留当前视图。
func _set_view_mode(index: int) -> void:
	if index < 0 or index >= VIEWS.size():
		return
	var view: Dictionary = VIEWS[index]
	if view["key"] == _view_mode:
		return
	if not _preview.has(view["field"]):
		return
	_view_mode = view["key"]


func _set_ratio_from_x(x: float) -> void:
	var ratio01: float = clampf(
		(x - _slider_rect.position.x) / maxf(1.0, _slider_rect.size.x - 22.0),
		0.0, 1.0)
	var new_ratio: float = LAND_RATIO_MIN + ratio01 * (LAND_RATIO_MAX - LAND_RATIO_MIN)
	new_ratio = roundf(new_ratio / LAND_RATIO_STEP) * LAND_RATIO_STEP
	new_ratio = clampf(new_ratio, LAND_RATIO_MIN, LAND_RATIO_MAX)
	if not is_equal_approx(new_ratio, _land_ratio):
		_land_ratio = new_ratio
		_request_preview()


func _randomize_seed() -> void:
	# 随机掷种：置回未定案占位，由后端预览时随机定案
	_seed = ""
	_request_preview()


func _open_seed_input() -> bool:
	if _seed_input == null:
		return false
	_editing_seed = true
	_seed_input.text = _seed
	_seed_input.visible = true
	_seed_input.position = _seed_rect.position
	_seed_input.size = _seed_rect.size
	_seed_input.grab_focus()
	return true


func _close_seed_input() -> void:
	_editing_seed = false
	if _seed_input != null:
		_seed_input.visible = false


func _display_seed() -> String:
	"""种子显示：未定案显示"随机"，长 hex 截断（内部保留完整值）。"""
	if _seed.is_empty():
		return TranslationServer.tr("ui.common.random")
	if _seed.length() > 24:
		return _seed.substr(0, 10) + "…" + _seed.substr(_seed.length() - 6)
	return _seed


func on_seed_submitted(text: String) -> void:
	"""容器 LineEdit 提交回调：解析 hex 种子并刷新预览。

	契约：1..64 位 hex（无 0x 前缀，大小写均可），归一为小写。
	空串提交 = 忽略（保留当前种子与预览）；随机掷种请用随机按钮。
	非法输入同样忽略。
	"""
	_close_seed_input()
	var seed_text: String = text.strip_edges()
	if seed_text.is_empty():
		return
	if _is_valid_hex_seed(seed_text):
		_seed = seed_text.to_lower()
		_request_preview()


static func _is_valid_hex_seed(text: String) -> bool:
	"""1..64 位 hex 字符（无 0x 前缀）。"""
	if text.length() == 0 or text.length() > SEED_HEX_MAX_LEN:
		return false
	for i in text.length():
		var c: String = text[i]
		if not ((c >= "0" and c <= "9") \
				or (c >= "a" and c <= "f") \
				or (c >= "A" and c <= "F")):
			return false
	return true
