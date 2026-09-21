"""设置存储 — SettingsStore 单元测试。

覆盖 scripts/settings/settings_store.gd：默认值、存取 roundtrip、
坏文件容错、非法值清洗与分辨率解析/档位过滤。
"""

extends GutTest

const TEST_PATH: String = "user://test_settings_store.cfg"

var _prev_save_root: String = ""


func before_each() -> void:
	_prev_save_root = OS.get_environment("ASCEND_SAVE_ROOT")


func after_each() -> void:
	DirAccess.remove_absolute(TEST_PATH)
	OS.set_environment("ASCEND_SAVE_ROOT", _prev_save_root)


func _write_raw(text: String) -> void:
	var f := FileAccess.open(TEST_PATH, FileAccess.WRITE)
	f.store_string(text)
	f.close()


# ── 默认路径与迁移 ──────────────────────────────────────────

func test_default_path_follows_save_root_env() -> void:
	OS.set_environment("ASCEND_SAVE_ROOT", "/tmp/opencode/fake_saves")
	assert_eq(SettingsStore.default_path(), "/tmp/opencode/settings.cfg",
		"设置应与存档父目录同级（跟随 ASCEND_SAVE_ROOT）")


func test_default_path_falls_back_to_home_ascend() -> void:
	OS.set_environment("ASCEND_SAVE_ROOT", "")
	var path: String = SettingsStore.default_path()
	assert_true(path.ends_with("/.ascend/settings.cfg"), "默认回退 ~/.ascend，实际 %s" % path)


# ── 默认值与加载 ────────────────────────────────────────────

func test_load_missing_file_keeps_defaults() -> void:
	var store := SettingsStore.new(TEST_PATH)
	store.load()
	assert_eq(store.get_value("display/resolution"), "1280x720")
	assert_eq(store.get_value("display/window_mode"), "windowed")
	assert_eq(store.get_value("display/fps_limit"), 0, "默认帧率上限应是不限")
	assert_eq(store.get_value("language/locale"), "zh_CN")
	assert_eq(store.get_value("debug/debug_mode"), true, "默认调试模式应开启")


func test_load_corrupt_file_falls_back_to_defaults() -> void:
	_write_raw("这不是合法的 INI {{{ not a config")
	var store := SettingsStore.new(TEST_PATH)
	store.load()
	assert_eq(store.get_value("display/resolution"), "1280x720",
		"损坏文件应回退默认值（下次 save 覆盖重建）")


func test_save_creates_missing_parent_dir() -> void:
	var nested := TEST_PATH.get_base_dir().path_join("nested/settings.cfg")
	DirAccess.remove_absolute(nested.get_base_dir())
	var store := SettingsStore.new(nested)
	store.set_value("display/resolution", "1600x900")
	assert_eq(store.save(), OK, "父目录不存在时 save 应自动创建并成功")
	assert_true(FileAccess.file_exists(nested))
	var reloaded := SettingsStore.new(nested)
	reloaded.load()
	assert_eq(reloaded.get_value("display/resolution"), "1600x900")
	DirAccess.remove_absolute(nested.get_base_dir())


func test_save_then_load_roundtrip() -> void:
	var store := SettingsStore.new(TEST_PATH)
	store.load()
	store.set_value("display/resolution", "1920x1080")
	store.set_value("display/window_mode", "fullscreen")
	store.set_value("language/locale", "en_US")
	store.set_value("input/move_up", [
		{"type": "key", "keycode": 87},
		{"type": "key", "keycode": 4194320},
	])
	assert_eq(store.save(), OK)

	var reloaded := SettingsStore.new(TEST_PATH)
	reloaded.load()
	assert_eq(reloaded.get_value("display/resolution"), "1920x1080")
	assert_eq(reloaded.get_value("display/window_mode"), "fullscreen")
	assert_eq(reloaded.get_value("language/locale"), "en_US")
	assert_eq(reloaded.get_value("input/move_up").size(), 2, "按键绑定应整段往返")


func test_input_section_loads_arbitrary_actions() -> void:
	var store := SettingsStore.new(TEST_PATH)
	store.load()
	store.set_value("input/move_up", [{"type": "key", "keycode": 87}])
	store.set_value("input/future_action", [{"type": "key", "keycode": 72}])
	store.save()

	var reloaded := SettingsStore.new(TEST_PATH)
	reloaded.load()
	assert_eq(reloaded.get_value("input/move_up").size(), 1)
	assert_eq(reloaded.get_value("input/future_action").size(), 1,
		"未登记 action 也应保留（前向兼容）")


# ── 非法值清洗 ──────────────────────────────────────────────

func test_sanitize_invalid_window_mode() -> void:
	var store := SettingsStore.new(TEST_PATH)
	store.load()
	store.set_value("display/window_mode", "floating")
	store.save()

	var reloaded := SettingsStore.new(TEST_PATH)
	reloaded.load()
	assert_eq(reloaded.get_value("display/window_mode"), "windowed",
		"白名单外窗口模式应回退默认")


func test_sanitize_invalid_resolution() -> void:
	var store := SettingsStore.new(TEST_PATH)
	store.load()
	store.set_value("display/resolution", "banana")
	store.save()

	var reloaded := SettingsStore.new(TEST_PATH)
	reloaded.load()
	assert_eq(reloaded.get_value("display/resolution"), "1280x720",
		"非法分辨率应回退默认")


func test_sanitize_invalid_locale() -> void:
	var store := SettingsStore.new(TEST_PATH)
	store.load()
	store.set_value("language/locale", "12345")
	store.save()

	var reloaded := SettingsStore.new(TEST_PATH)
	reloaded.load()
	assert_eq(reloaded.get_value("language/locale"), "zh_CN",
		"非法语言（手改 cfg）应回退默认")


func test_debug_mode_roundtrip() -> void:
	var store := SettingsStore.new(TEST_PATH)
	store.load()
	store.set_value("debug/debug_mode", false)
	assert_eq(store.save(), OK)

	var reloaded := SettingsStore.new(TEST_PATH)
	reloaded.load()
	assert_eq(reloaded.get_value("debug/debug_mode"), false, "关闭状态应往返落盘")


func test_sanitize_invalid_debug_mode() -> void:
	var store := SettingsStore.new(TEST_PATH)
	store.load()
	store.set_value("debug/debug_mode", "banana")
	store.save()

	var reloaded := SettingsStore.new(TEST_PATH)
	reloaded.load()
	assert_eq(reloaded.get_value("debug/debug_mode"), true,
		"非布尔调试模式（手改 cfg）应回退默认开启")


# ── 帧率上限 ────────────────────────────────────────────────

func test_fps_limit_roundtrip() -> void:
	var store := SettingsStore.new(TEST_PATH)
	store.load()
	store.set_value("display/fps_limit", 144)
	assert_eq(store.save(), OK)

	var reloaded := SettingsStore.new(TEST_PATH)
	reloaded.load()
	assert_eq(reloaded.get_value("display/fps_limit"), 144, "帧率上限应往返落盘")


func test_sanitize_invalid_fps_limit() -> void:
	var store := SettingsStore.new(TEST_PATH)
	store.load()
	store.set_value("display/fps_limit", "banana")
	store.save()
	var reloaded := SettingsStore.new(TEST_PATH)
	reloaded.load()
	assert_eq(reloaded.get_value("display/fps_limit"), 0, "非整数（手改 cfg）应回退默认（不限）")

	store.set_value("display/fps_limit", -5)
	store.save()
	reloaded = SettingsStore.new(TEST_PATH)
	reloaded.load()
	assert_eq(reloaded.get_value("display/fps_limit"), 0, "负值应回退默认（不限）")

	store.set_value("display/fps_limit", 99999)
	store.save()
	reloaded = SettingsStore.new(TEST_PATH)
	reloaded.load()
	assert_eq(reloaded.get_value("display/fps_limit"), 0, "超过上限应回退默认（不限）")


func test_is_valid_locale() -> void:
	assert_true(SettingsStore.is_valid_locale("zh_CN"))
	assert_true(SettingsStore.is_valid_locale("en_US"))
	assert_false(SettingsStore.is_valid_locale("12345"))
	assert_false(SettingsStore.is_valid_locale("zhCN"))
	assert_false(SettingsStore.is_valid_locale("zh_cn"))
	assert_false(SettingsStore.is_valid_locale(""))
	assert_false(SettingsStore.is_valid_locale("zh-CN"))


# ── 分辨率解析 ──────────────────────────────────────────────

func test_parse_resolution_valid() -> void:
	assert_eq(SettingsStore.parse_resolution("1920x1080"), Vector2i(1920, 1080))
	assert_eq(SettingsStore.parse_resolution("1280x720"), Vector2i(1280, 720))


func test_parse_resolution_invalid() -> void:
	assert_eq(SettingsStore.parse_resolution("banana"), Vector2i.ZERO)
	assert_eq(SettingsStore.parse_resolution("1920"), Vector2i.ZERO)
	assert_eq(SettingsStore.parse_resolution("1920x"), Vector2i.ZERO)
	assert_eq(SettingsStore.parse_resolution("320x240"), Vector2i.ZERO,
		"低于 640x480 视为非法")
	assert_eq(SettingsStore.parse_resolution(""), Vector2i.ZERO)


func test_resolution_options_filters_by_screen() -> void:
	var options: Array = SettingsStore.resolution_options("1280x720", Vector2i(1920, 1080))
	for res in options:
		var v := SettingsStore.parse_resolution(res)
		assert_lt(v.x, 1921, "超过屏幕尺寸的档位应被过滤")
		assert_lt(v.y, 1081)


func test_resolution_options_always_contains_current() -> void:
	var options: Array = SettingsStore.resolution_options("1366x768", Vector2i(1920, 1080))
	assert_true(options.has("1366x768"), "非预设分辨率也应可选（当前值恒含）")


func test_resolution_options_headless_no_filter() -> void:
	var options: Array = SettingsStore.resolution_options("1280x720", Vector2i.ZERO)
	assert_eq(options.size(), SettingsStore.resolution_presets().size(),
		"屏幕尺寸未知（headless）时不过滤")
