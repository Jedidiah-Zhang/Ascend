"""事件日志面板 — 右侧实时事件流，最近 20 条。

自行处理后端事件（天气格式化、视野过滤、日期分隔），
世界脚本只需调用 on_world_event() 和 set_player_chunk()。
显示在屏幕右侧，与左侧 DebugOverlay 对称。
"""

extends Control

class_name EventLog


# ── 常量 ────────────────────────────────────────────────────

const BG_COLOR: Color = Color(0.0, 0.0, 0.0, 0.55)
const HEADER_COLOR: Color = Color(0.55, 1.0, 0.55)
const TEXT_COLOR: Color = Color(0.85, 0.85, 0.90)
const FONT_SIZE: int = 12
const LINE_HEIGHT: int = 15
const PADDING: int = 8
const PANEL_WIDTH: float = 280.0

const REFRESH_INTERVAL: float = 0.5

const VIEW_RADIUS: int = 1


# ── 属性 ────────────────────────────────────────────────────

var _lines: PackedStringArray = PackedStringArray()
var _font: Font = null
var _pending_redraw: bool = false
var _refresh_accum: float = 0.0

var _current_game_day: int = -1
var _player_chunk: Vector2i = Vector2i(0, 0)


# ── 生命周期 ────────────────────────────────────────────────

func _ready() -> void:
	anchor_left = 0.0
	anchor_top = 0.0
	anchor_right = 1.0
	anchor_bottom = 1.0
	mouse_filter = Control.MOUSE_FILTER_IGNORE
	_font = FontUtils.get_mono_font()
	hide()
	var overlay := get_node_or_null("../DebugOverlay")
	if overlay and overlay is DebugOverlay:
		overlay.toggled.connect(_on_debug_toggled)


func _process(delta: float) -> void:
	if not _pending_redraw:
		return
	_refresh_accum += delta
	if _refresh_accum >= REFRESH_INTERVAL:
		_refresh_accum = 0.0
		_pending_redraw = false
		queue_redraw()


func _draw() -> void:
	if _font == null or _lines.is_empty():
		return

	var header_text := "── 事件日志 ──"
	var bg_x: float = size.x - PANEL_WIDTH
	var bg_h: float = (_lines.size() + 1) * LINE_HEIGHT + PADDING * 2

	draw_rect(Rect2(Vector2(bg_x, 0.0), Vector2(PANEL_WIDTH, bg_h)), BG_COLOR)

	var y: float = PADDING
	var x: float = bg_x + PADDING
	draw_string(_font, Vector2(x, y + FONT_SIZE), header_text,
			HORIZONTAL_ALIGNMENT_LEFT, -1, FONT_SIZE, HEADER_COLOR)
	y += LINE_HEIGHT
	for line in _lines:
		draw_string(_font, Vector2(x, y + FONT_SIZE), line,
				HORIZONTAL_ALIGNMENT_LEFT, -1, FONT_SIZE, TEXT_COLOR)
		y += LINE_HEIGHT


# ── 公共接口 ────────────────────────────────────────────────

func push_event(line: String) -> void:
	var arr: Array[String] = []
	arr.append(line)
	arr.append_array(_lines)
	_lines = arr
	if _lines.size() > 20:
		_lines.resize(20)
	_pending_redraw = true


func set_player_chunk(chunk: Vector2i) -> void:
	_player_chunk = chunk


func on_world_event(event_type: String, payload: Dictionary) -> void:
	var data: Dictionary = payload.get("data", {})
	var ts := "%02d:%02d" % [
		payload.get("game_hour", 0),
		payload.get("game_minute", 0),
	]

	match event_type:
		"minute_change":
			var day: int = int(data.get("day", 0))
			if day != _current_game_day:
				_current_game_day = day
				push_event("[%s] ── 第%d天 ──" % [ts, day])

		"temperature_change", "humidity_change", "wind_change", "sunshine_change", \
		"precipitation_start", "precipitation_stop":
			_push_weather_event(event_type, payload, data, ts)


# ── 内部实现 ────────────────────────────────────────────────

func _on_debug_toggled(shown: bool) -> void:
	if shown:
		show()
	else:
		hide()


func _push_weather_event(event_type: String, payload: Dictionary, data: Dictionary, ts: String) -> void:
	var loc: Array = payload.get("location", [])
	if not _is_within_view(loc):
		return
	var cx: int = int(loc[0]) if loc.size() >= 1 else 0
	var cy: int = int(loc[1]) if loc.size() >= 2 else 0

	var body: String
	match event_type:
		"temperature_change":
			body = "温度 %.1f°C" % data.get("temperature", 0.0)
		"humidity_change":
			body = "湿度 %.0f%%" % data.get("humidity", 0.0)
		"wind_change":
			body = "风速 %.1f m/s" % data.get("wind_speed", 0.0)
		"sunshine_change":
			body = "日照 %.1fh" % data.get("sunshine", 0.0)
		"precipitation_start":
			body = "%s %.1fmm/h" % [data.get("precip_type", ""), data.get("intensity", 0.0)]
		"precipitation_stop":
			body = "雨停"
		_:
			return
	push_event("[%s] [区块 %d,%d] %s" % [ts, cx, cy, body])


func _is_within_view(location_array: Array) -> bool:
	if location_array.size() < 2:
		return true
	var ev_cx: int = int(location_array[0])
	var ev_cy: int = int(location_array[1])
	var dx: int = abs(ev_cx - _player_chunk.x)
	var dy: int = abs(ev_cy - _player_chunk.y)
	return dx <= VIEW_RADIUS and dy <= VIEW_RADIUS
