"""设置应用器 — 把设置值落到 DisplayServer / InputMap / TranslationServer。

纯静态方法 RefCounted；窗口操作在 headless（测试）环境直接跳过。
"""

class_name SettingsApplier
extends RefCounted


## 应用显示设置：windowed 窗口化（设尺寸 + 居中）/ borderless 无边框
## 全屏（桌面分辨率）/ fullscreen 独占全屏（先设尺寸再切模式）。
static func apply_display(resolution: String, window_mode: String) -> void:
	if DisplayServer.get_name() == "headless":
		return
	var size: Vector2i = SettingsStore.parse_resolution(resolution)
	if size == Vector2i.ZERO:
		size = SettingsStore.parse_resolution(SettingsStore.DEFAULTS["display/resolution"])
	match window_mode:
		"fullscreen":
			_set_window_size(size)
			DisplayServer.window_set_mode(DisplayServer.WINDOW_MODE_EXCLUSIVE_FULLSCREEN)
		"borderless":
			DisplayServer.window_set_mode(DisplayServer.WINDOW_MODE_FULLSCREEN)
		_:  # windowed
			DisplayServer.window_set_mode(DisplayServer.WINDOW_MODE_WINDOWED)
			DisplayServer.window_set_flag(DisplayServer.WINDOW_FLAG_BORDERLESS, false)
			_set_window_size(size)
			_center_window(size)


## 运行时改窗口尺寸必须走 Window.size 而非 DisplayServer.window_set_size：
## X11 下后者的 OS 窗口实际会变，但 Godot 内部 Window 收不到尺寸事件，
## 视口拉伸矩形（stretch 缩放）不会重算 → UI 不随窗口缩放。
static func _set_window_size(size: Vector2i) -> void:
	var window: Window = _main_window()
	if window != null:
		window.size = size
	else:
		DisplayServer.window_set_size(size)


## 主窗口（运行期 SceneTree.root；非 SceneTree 环境回退 DisplayServer）。
static func _main_window() -> Window:
	var loop: MainLoop = Engine.get_main_loop()
	if loop is SceneTree:
		return (loop as SceneTree).root
	return null


## 窗口居中到当前屏幕可用区域（任务栏等已排除）。
static func _center_window(size: Vector2i) -> void:
	var screen: int = DisplayServer.window_get_current_screen()
	var usable: Rect2i = DisplayServer.screen_get_usable_rect(screen)
	if usable.size == Vector2i.ZERO:
		return
	DisplayServer.window_set_position(
		usable.position + Vector2i(((usable.size - size) / 2.0).round()))


## 应用按键绑定：逐动作清空重建（InputMap 无动作时补建，防御性）。
## 非字典条目直接跳过（损坏 cfg 的数据不应触发运行时错误）。
static func apply_keybinds(binds: Dictionary) -> void:
	for action in KeybindMap.ACTIONS:
		if not InputMap.has_action(action):
			InputMap.add_action(action)
		InputMap.action_erase_events(action)
		for d in binds.get(action, []):
			if not d is Dictionary:
				continue
			var event: InputEvent = KeybindMap.dict_to_event(d)
			if event != null:
				InputMap.action_add_event(action, event)


static func apply_locale(locale: String) -> void:
	TranslationServer.set_locale(locale)


## 应用帧率上限：0 = 不限。headless 下同样生效（引擎属性，非窗口）。
static func apply_fps_limit(limit: int) -> void:
	Engine.max_fps = limit
