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

依赖：无（流程首步）。产出：
{seed, gen_params: {land_ratio, width_km, height_km}}。

测试约定：request_sender 可注入（缺省走 Connection.send），
preview 响应用 on_preview_response(payload) 喂入——纯逻辑可单测。
"""

class_name MapSetupStep
extends SetupStep

## 种子上限（与后端存档契约 manifest.SEED_MAX 一致：0 = 随机占位）
const MAX_SEED: int = 2147483647

# ── 可调参数范围 ──────────────────────────────────────────

const LAND_RATIO_MIN: float = 0.10
const LAND_RATIO_MAX: float = 0.90
const LAND_RATIO_STEP: float = 0.01

## 地图尺寸档位（与后端 ContinentParams 5:3 比例一致）。
const SIZE_OPTIONS: Array = [
	{"label": "小", "width_km": 60.0, "height_km": 36.0},
	{"label": "中", "width_km": 100.0, "height_km": 60.0},
	{"label": "大", "width_km": 150.0, "height_km": 90.0},
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

# ── 状态 ──────────────────────────────────────────────────

## 当前种子（0 = 未定案；setup 时随机定案，保证预览有据可依）
var _seed: int = 0
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

## 步骤内容区矩形（draw_page 时记录，输入命中用）
var _page_rect: Rect2 = Rect2()


func _send_default(message: Dictionary) -> void:
	Connection.send(message)


# ── 生命周期（SetupStep 契约） ────────────────────────────

func step_id() -> String:
	return "map"


func title() -> String:
	return "地图生成"


func setup(params: Dictionary) -> void:
	_seed = int(params.get("seed", 0))
	if _seed <= 0:
		_seed = randi_range(1, MAX_SEED)
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
	request_sender.call(_draft_request)


func _on_request_done() -> void:
	_preview_pending = false
	if _preview_dirty:
		_preview_dirty = false
		_send_preview()


func on_preview_response(payload: Dictionary) -> void:
	"""后端 map_preview 响应：应用后补发变化后的最新参数。"""
	_on_request_done()
	if payload.is_empty():
		return
	_preview = payload


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
	draw_label(canvas, font, rect.position + Vector2(0, 26), "世界种子", LABEL_FONT_SIZE)
	var seed_rect := Rect2(rect.position + Vector2(0, 40), Vector2(rect.size.x - 130.0, 34))
	_draw_value_box(canvas, seed_rect, font, str(_seed), _hover == "seed")
	var random_rect := Rect2(
		rect.position + Vector2(rect.size.x - 118.0, 40), Vector2(112.0, 34))
	_draw_button(canvas, random_rect, font, "随机", _hover == "random")
	_random_rect = random_rect

	# 大陆占比行
	var ratio_y: float = 40.0 + 34.0 + 34.0
	draw_label(canvas, font, rect.position + Vector2(0, ratio_y + 16),
		"大陆占比  %d%%" % int(round(_land_ratio * 100.0)), LABEL_FONT_SIZE)
	var slider_rect := Rect2(rect.position + Vector2(0, ratio_y + 30),
		Vector2(rect.size.x - 160.0, 24))
	_draw_slider(canvas, slider_rect, font)
	_slider_rect = slider_rect

	# 地图尺寸行（三档选择，切换不刷新预览）
	var size_y: float = ratio_y + 30.0 + 24.0 + 26.0
	draw_label(canvas, font, rect.position + Vector2(0, size_y + 16),
		"地图尺寸  %dx%d km" % [SIZE_OPTIONS[_size_index]["width_km"],
			SIZE_OPTIONS[_size_index]["height_km"]], LABEL_FONT_SIZE)
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
		"种子决定整个世界的确定性生成；占比影响海陆面积，尺寸影响世界规模。",
		SMALL_FONT_SIZE, DIM_COLOR)


func _draw_preview(canvas: Control, rect: Rect2, font: Font) -> void:
	canvas.draw_rect(rect, PREVIEW_BG_COLOR)
	canvas.draw_rect(rect, Color(1, 1, 1, 0.12), false, 1.0)
	draw_label(canvas, font, rect.position + Vector2(14, 24), "地形预览", LABEL_FONT_SIZE)

	if _preview.is_empty():
		var waiting: String = "生成中..." if _preview_pending else "等待参数..."
		draw_label(canvas, font,
			rect.position + Vector2(14, rect.size.y * 0.5), waiting, VALUE_FONT_SIZE)
		return

	var elev: Array = _preview.get("elevation", [])
	var w: int = int(_preview.get("width", 0))
	var h: int = int(_preview.get("height", 0))
	if elev.size() < w * h or w <= 0 or h <= 0:
		draw_label(canvas, font, rect.position + Vector2(14, rect.size.y * 0.5),
			"预览数据异常", SMALL_FONT_SIZE, Color(0.95, 0.50, 0.45))
		return

	# 地图（按比例缩放居中）
	var cell: float = minf(
		PREVIEW_CELL,
		minf((rect.size.x - 28.0) / w, (rect.size.y - 70.0) / h),
	)
	var map_size := Vector2(w * cell, h * cell)
	var map_origin: Vector2 = rect.position + Vector2(
		(rect.size.x - map_size.x) * 0.5, 56.0)
	for y in h:
		for x in w:
			var e: float = float(elev[y * w + x])
			canvas.draw_rect(Rect2(
				map_origin + Vector2(x * cell, y * cell), Vector2(cell, cell)),
				_elevation_color(e))

	# 信息行
	var land_pct: float = float(_preview.get("land_percent", 0.0))
	var info: String = "陆地占比 %.0f%%    种子 %d" % [land_pct * 100.0, int(_preview.get("seed", 0))]
	draw_label(canvas, font, rect.position + Vector2(14, rect.size.y - 22),
		info, SMALL_FONT_SIZE, DIM_COLOR)


func _elevation_color(e: float) -> Color:
	if e <= 0.0:
		var depth: float = clampf(-e / 2000.0, 0.0, 1.0)
		return Color(0.08 + depth * 0.05, 0.12 + depth * 0.12, 0.32 + depth * 0.18)
	if e < 300.0:
		return Color(0.30, 0.48, 0.24)
	if e < 1000.0:
		return Color(0.42, 0.52, 0.28)
	if e < 2000.0:
		return Color(0.55, 0.50, 0.34)
	if e < 3500.0:
		return Color(0.48, 0.42, 0.40)
	return Color(0.92, 0.92, 0.94)


# ── 自绘控件 ──────────────────────────────────────────────

var _seed_rect: Rect2 = Rect2()
var _random_rect: Rect2 = Rect2()
var _slider_rect: Rect2 = Rect2()
var _size_rects: Array = []


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
	var label: String = "%s\n%dx%d" % [SIZE_OPTIONS[index]["label"],
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
		if new_hover != _hover:
			_hover = new_hover
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
	return ""


## 切换地图尺寸档位（尺寸改变生成范围 → 重新请求预览）。
func _set_size(index: int) -> void:
	if index < 0 or index >= SIZE_OPTIONS.size() or index == _size_index:
		return
	_size_index = index
	_request_preview()


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
	_seed = randi_range(1, MAX_SEED)
	_request_preview()


func _open_seed_input() -> bool:
	if _seed_input == null:
		return false
	_editing_seed = true
	_seed_input.text = str(_seed)
	_seed_input.visible = true
	_seed_input.position = _seed_rect.position
	_seed_input.size = _seed_rect.size
	_seed_input.grab_focus()
	return true


func _close_seed_input() -> void:
	_editing_seed = false
	if _seed_input != null:
		_seed_input.visible = false


func on_seed_submitted(text: String) -> void:
	"""容器 LineEdit 提交回调：解析种子并刷新预览。"""
	_close_seed_input()
	var seed_text: String = text.strip_edges()
	if seed_text.is_empty():
		return
	if seed_text.is_valid_int():
		var value: int = int(seed_text)
		if value > 0:
			_seed = value
			_request_preview()
