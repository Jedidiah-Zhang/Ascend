"""按键绑定映射 — 8 个可绑定动作的元数据、冲突检测与事件序列化。

纯逻辑 RefCounted。事件字典格式：
  {"type": "key", "keycode": 87} / {"type": "mouse", "button": 4}
默认表镜像 project.godot [input] 段（test_keybind_map 有对账测试）。
"""

class_name KeybindMap
extends RefCounted

## 可绑定动作（顺序即设置界面行序）
const ACTIONS: Array[String] = [
	"move_up", "move_down", "move_left", "move_right",
	"interact", "menu", "zoom_in", "zoom_out",
]

const TYPE_KEY: String = "key"
const TYPE_MOUSE: String = "mouse"

var _binds: Dictionary = {}


## binds 为空 → 默认表；非法动作/事件静默丢弃。
func _init(binds: Dictionary = {}) -> void:
	reset()
	for action in binds:
		if not ACTIONS.has(action) or not binds[action] is Array:
			continue
		var cleaned: Array = []
		for e in binds[action]:
			var d: Dictionary = sanitize_event(e)
			if not d.is_empty():
				cleaned.append(d)
		_binds[action] = cleaned


## 默认绑定表（镜像 project.godot [input] 段；改动需同步）。
static func default_binds() -> Dictionary:
	return {
		"move_up": [
			{"type": TYPE_KEY, "keycode": KEY_W},
			{"type": TYPE_KEY, "keycode": KEY_UP},
		],
		"move_down": [
			{"type": TYPE_KEY, "keycode": KEY_S},
			{"type": TYPE_KEY, "keycode": KEY_DOWN},
		],
		"move_left": [
			{"type": TYPE_KEY, "keycode": KEY_A},
			{"type": TYPE_KEY, "keycode": KEY_LEFT},
		],
		"move_right": [
			{"type": TYPE_KEY, "keycode": KEY_D},
			{"type": TYPE_KEY, "keycode": KEY_RIGHT},
		],
		"interact": [{"type": TYPE_KEY, "keycode": KEY_E}],
		"menu": [{"type": TYPE_KEY, "keycode": KEY_ESCAPE}],
		"zoom_in": [{"type": TYPE_MOUSE, "button": MOUSE_BUTTON_WHEEL_UP}],
		"zoom_out": [{"type": TYPE_MOUSE, "button": MOUSE_BUTTON_WHEEL_DOWN}],
	}


## 事件 → 字典；不支持的类型（手柄等）返回 {}。
static func event_to_dict(event: InputEvent) -> Dictionary:
	if event is InputEventKey and event.keycode != 0:
		return {"type": TYPE_KEY, "keycode": int(event.keycode)}
	if event is InputEventMouseButton:
		return {"type": TYPE_MOUSE, "button": int(event.button_index)}
	return {}


## 字典 → 事件；非法返回 null。
static func dict_to_event(d: Dictionary) -> InputEvent:
	match str(d.get("type", "")):
		TYPE_KEY:
			var keycode: int = int(d.get("keycode", 0))
			if keycode == 0:
				return null
			var key_event := InputEventKey.new()
			key_event.keycode = keycode as Key
			return key_event
		TYPE_MOUSE:
			var button: int = int(d.get("button", 0))
			if button == 0:
				return null
			var mouse_event := InputEventMouseButton.new()
			mouse_event.button_index = button as MouseButton
			return mouse_event
	return null


## 清洗事件字典：类型白名单 + 数值归一（INI 读出的 float 转 int）。
static func sanitize_event(e: Variant) -> Dictionary:
	if not e is Dictionary:
		return {}
	var d: Dictionary = e
	match str(d.get("type", "")):
		TYPE_KEY:
			var raw: Variant = d.get("keycode", 0)
			if raw is float and raw != floor(raw):
				return {}
			var keycode: int = int(raw)
			if keycode > 0:
				return {"type": TYPE_KEY, "keycode": keycode}
		TYPE_MOUSE:
			var raw_button: Variant = d.get("button", 0)
			if raw_button is float and raw_button != floor(raw_button):
				return {}
			var button: int = int(raw_button)
			if button > 0:
				return {"type": TYPE_MOUSE, "button": button}
	return {}


## 添加绑定。返回 {"ok": bool, "reason": String, "conflict": String}；
## reason 取值："ok" / "unknown_action" / "unsupported" / "duplicate" /
## "conflict"（此时 conflict 字段为占用动作名）。
func add_bind(action: String, event_dict: Dictionary) -> Dictionary:
	var result := {"ok": false, "reason": "ok", "conflict": ""}
	if not ACTIONS.has(action):
		result["reason"] = "unknown_action"
		return result
	var d: Dictionary = sanitize_event(event_dict)
	if d.is_empty():
		result["reason"] = "unsupported"
		return result
	for e in _binds[action]:
		if e == d:
			result["reason"] = "duplicate"
			return result
	var holder: String = find_conflict(d, action)
	if not holder.is_empty():
		result["reason"] = "conflict"
		result["conflict"] = holder
		return result
	_binds[action].append(d)
	result["ok"] = true
	return result


## 查找事件占用者（exclude_action 之外的动作）；无冲突返回空串。
func find_conflict(d: Dictionary, exclude_action: String = "") -> String:
	for action in ACTIONS:
		if action == exclude_action:
			continue
		for e in _binds.get(action, []):
			if e == d:
				return action
	return ""


func remove_bind(action: String, index: int) -> void:
	if not _binds.has(action):
		return
	if index >= 0 and index < _binds[action].size():
		_binds[action].remove_at(index)


func clear_action(action: String) -> void:
	if _binds.has(action):
		_binds[action].clear()


## 动作绑定列表（深拷贝，防外部改写内部状态）。
func get_binds(action: String) -> Array:
	return _binds.get(action, []).duplicate(true)


## 全量导出（存 SettingsStore [input] 段 / 喂 SettingsApplier）。
func to_dict() -> Dictionary:
	return _binds.duplicate(true)


## 恢复默认表。
func reset() -> void:
	_binds = default_binds()


## 事件显示名：方向键用箭头；鼠标键走翻译；其余按键用系统键名。
static func event_label(d: Dictionary) -> String:
	match str(d.get("type", "")):
		TYPE_KEY:
			match int(d.get("keycode", 0)):
				KEY_UP:
					return "↑"
				KEY_DOWN:
					return "↓"
				KEY_LEFT:
					return "←"
				KEY_RIGHT:
					return "→"
				_:
					return OS.get_keycode_string(int(d.get("keycode", 0)))
		TYPE_MOUSE:
			match int(d.get("button", 0)):
				MOUSE_BUTTON_LEFT:
					return TranslationServer.tr("ui.settings.key.mouse_left")
				MOUSE_BUTTON_MIDDLE:
					return TranslationServer.tr("ui.settings.key.mouse_middle")
				MOUSE_BUTTON_RIGHT:
					return TranslationServer.tr("ui.settings.key.mouse_right")
				MOUSE_BUTTON_WHEEL_UP:
					return TranslationServer.tr("ui.settings.key.mwheel_up")
				MOUSE_BUTTON_WHEEL_DOWN:
					return TranslationServer.tr("ui.settings.key.mwheel_down")
				_:
					return "Mouse %d" % int(d.get("button", 0))
	return "?"
