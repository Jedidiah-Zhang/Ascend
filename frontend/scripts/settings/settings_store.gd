"""设置存储 — settings.cfg 读写、默认值回填与非法值容错。

纯逻辑 RefCounted（GUT 单测约定）：键命名 "section/key"，
如 "display/resolution"；按键绑定整段存于 [input]（见 KeybindMap）。

默认路径与后端存档同级（~/.ascend/settings.cfg，跟随 ASCEND_SAVE_ROOT
重定向）。
"""

class_name SettingsStore
extends RefCounted


## 默认值表：新增设置项先在此登记（load 缺失键按此回填）。
const DEFAULTS: Dictionary = {
	"display/resolution": "1280x720",
	"display/window_mode": "windowed",
	"language/locale": "zh_CN",
}

## 窗口模式白名单：windowed 窗口化 / borderless 无边框全屏 / fullscreen 独占全屏
const WINDOW_MODES: Array[String] = ["windowed", "borderless", "fullscreen"]

var _path: String = default_path()
## 内存值表：键 → Variant（含 "input/<action>" 绑定数组）
var _values: Dictionary = {}


func _init(path: String = default_path()) -> void:
	_path = path


## 默认设置文件路径：与后端存档同级（ASCEND_SAVE_ROOT 指向 saves 目录，
## 取其父目录；未设置时回退 ~/.ascend）。
static func default_path() -> String:
	var save_root := OS.get_environment("ASCEND_SAVE_ROOT")
	if not save_root.is_empty():
		var dir := save_root.get_base_dir()
		if not dir.is_empty():
			return dir.path_join("settings.cfg")
	return _home_dir().path_join(".ascend").path_join("settings.cfg")


## 跨平台用户主目录（Godot 无内置 home API）。
static func _home_dir() -> String:
	var home := OS.get_environment("HOME")
	if home.is_empty():
		home = OS.get_environment("USERPROFILE")
	return home


## 读取设置文件：文件缺失/损坏时静默保留默认值（下次 save 重建）。
func load() -> void:
	_values = DEFAULTS.duplicate(true)
	var cfg := ConfigFile.new()
	if cfg.load(_path) != OK:
		return
	for key in DEFAULTS:
		_values[key] = cfg.get_value(
			key.get_slice("/", 0), key.get_slice("/", 1), DEFAULTS[key])
	if cfg.has_section("input"):
		for action in cfg.get_section_keys("input"):
			var events: Variant = cfg.get_value("input", action, [])
			if events is Array:
				_values["input/" + action] = events
	_sanitize()


## 落盘。返回 ConfigFile.save 的错误码。
func save() -> Error:
	var cfg := ConfigFile.new()
	for key in _values:
		cfg.set_value(key.get_slice("/", 0), key.get_slice("/", 1), _values[key])
	return cfg.save(_path)


## 读取设置值：未登记的键回退默认值表（再缺为 null）。
func get_value(key: String) -> Variant:
	return _values.get(key, DEFAULTS.get(key))


func set_value(key: String, value: Variant) -> void:
	_values[key] = value


## 非法值清洗：窗口模式白名单、分辨率格式、语言格式（xx_XX）。
func _sanitize() -> void:
	if not WINDOW_MODES.has(_values.get("display/window_mode")):
		_values["display/window_mode"] = DEFAULTS["display/window_mode"]
	if parse_resolution(str(_values.get("display/resolution", ""))) == Vector2i.ZERO:
		_values["display/resolution"] = DEFAULTS["display/resolution"]
	if not is_valid_locale(str(_values.get("language/locale", ""))):
		_values["language/locale"] = DEFAULTS["language/locale"]


## 语言格式校验（"xx_XX"）；非法值（如手改 cfg 的 12345）会导致
## 下拉框无匹配与翻译失效，故 load 时回退默认。
static func is_valid_locale(locale: String) -> bool:
	if locale.length() != 5 or locale[2] != "_":
		return false
	return _is_lower_alpha(locale[0]) and _is_lower_alpha(locale[1]) \
		and _is_upper_alpha(locale[3]) and _is_upper_alpha(locale[4])


static func _is_lower_alpha(c: String) -> bool:
	return c >= "a" and c <= "z"


static func _is_upper_alpha(c: String) -> bool:
	return c >= "A" and c <= "Z"


## 解析 "1920x1080" → Vector2i(1920, 1080)；非法/过小返回 Vector2i.ZERO。
static func parse_resolution(text: String) -> Vector2i:
	var parts: PackedStringArray = text.split("x")
	if parts.size() != 2:
		return Vector2i.ZERO
	if not parts[0].is_valid_int() or not parts[1].is_valid_int():
		return Vector2i.ZERO
	var v := Vector2i(parts[0].to_int(), parts[1].to_int())
	if v.x < 640 or v.y < 480:
		return Vector2i.ZERO
	return v


## 分辨率预设档位。
static func resolution_presets() -> Array[String]:
	return ["1280x720", "1600x900", "1920x1080", "2560x1440", "3840x2160"]


## 下拉框档位：预设中不超过屏幕尺寸的 + 恒含当前值；
## screen 为 Vector2i.ZERO（headless 或探测失败）时不过滤。
static func resolution_options(current: String, screen: Vector2i) -> Array[String]:
	var out: Array[String] = []
	for preset in resolution_presets():
		var v := parse_resolution(preset)
		if screen != Vector2i.ZERO and (v.x > screen.x or v.y > screen.y):
			continue
		out.append(preset)
	if parse_resolution(current) != Vector2i.ZERO and not out.has(current):
		out.append(current)
	return out
