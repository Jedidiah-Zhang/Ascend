"""Settings 自动加载 — 设置门面：启动时加载并应用，对 UI 暴露读写 API。

纯逻辑在 scripts/settings/ 下的 RefCounted 类（SettingsStore /
KeybindMap / LocaleCatalog / SettingsApplier）；本壳只负责装配、
应用与信号广播。注意不声明 class_name（与 autoload 名冲突）。

后端语言同步不在此处：main_world 监听 locale_changed / world_initialized
后经 terminal_cmd 幂等发送（本门面不感知进程模型）。
"""

extends Node

signal display_changed
signal locale_changed(locale: String)
signal keybinds_changed

var store: SettingsStore = null
var keybinds: KeybindMap = null
var catalog: LocaleCatalog = null
var _initialized: bool = false


func _ready() -> void:
	if _initialized:
		return
	setup(SettingsStore.new(), LocaleCatalog.new())


## 装配并应用（测试可注入临时路径 store / 空 catalog 跳过翻译注册）。
func setup(p_store: SettingsStore, p_catalog: LocaleCatalog = null) -> void:
	store = p_store
	store.load()
	catalog = p_catalog
	if catalog != null:
		catalog.load_all()
	keybinds = KeybindMap.new(_stored_binds())
	SettingsApplier.apply_locale(get_locale())
	SettingsApplier.apply_keybinds(keybinds.to_dict())
	var d: Dictionary = get_display()
	SettingsApplier.apply_display(d["resolution"], d["window_mode"])
	_initialized = true


## 从 store 捞出 [input] 段绑定（缺失动作由 KeybindMap 默认表兜底）。
func _stored_binds() -> Dictionary:
	var out: Dictionary = {}
	for action in KeybindMap.ACTIONS:
		var v: Variant = store.get_value("input/" + action)
		if v is Array:
			out[action] = v
	return out


# ── 语言 ──────────────────────────────────────────────────

func get_locale() -> String:
	return str(store.get_value("language/locale"))


func set_locale(locale: String) -> void:
	if locale == get_locale():
		return
	store.set_value("language/locale", locale)
	store.save()
	SettingsApplier.apply_locale(locale)
	locale_changed.emit(locale)


# ── 显示 ──────────────────────────────────────────────────

func get_display() -> Dictionary:
	return {
		"resolution": str(store.get_value("display/resolution")),
		"window_mode": str(store.get_value("display/window_mode")),
	}


func set_display(resolution: String, window_mode: String) -> void:
	if get_display() == {"resolution": resolution, "window_mode": window_mode}:
		return
	store.set_value("display/resolution", resolution)
	store.set_value("display/window_mode", window_mode)
	store.save()
	SettingsApplier.apply_display(resolution, window_mode)
	display_changed.emit()


# ── 按键 ──────────────────────────────────────────────────

## 添加绑定；result.ok 时立即生效并落盘。返回结构见 KeybindMap.add_bind。
func add_bind(action: String, event_dict: Dictionary) -> Dictionary:
	var result: Dictionary = keybinds.add_bind(action, event_dict)
	if result.get("ok", false):
		_persist_binds()
	return result


func remove_bind(action: String, index: int) -> void:
	keybinds.remove_bind(action, index)
	_persist_binds()


func reset_keybinds() -> void:
	keybinds.reset()
	_persist_binds()


## 绑定写入 store + 落盘 + 应用到 InputMap + 广播。
func _persist_binds() -> void:
	for action in KeybindMap.ACTIONS:
		store.set_value("input/" + action, keybinds.get_binds(action))
	store.save()
	SettingsApplier.apply_keybinds(keybinds.to_dict())
	keybinds_changed.emit()
