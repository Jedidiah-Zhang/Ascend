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
			DisplayServer.window_set_size(size)
			DisplayServer.window_set_mode(DisplayServer.WINDOW_MODE_EXCLUSIVE_FULLSCREEN)
		"borderless":
			DisplayServer.window_set_mode(DisplayServer.WINDOW_MODE_FULLSCREEN)
		_:  # windowed
			DisplayServer.window_set_mode(DisplayServer.WINDOW_MODE_WINDOWED)
			DisplayServer.window_set_flag(DisplayServer.WINDOW_FLAG_BORDERLESS, false)
			DisplayServer.window_set_size(size)
			_center_window(size)


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
