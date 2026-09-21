extends GutTest

const WorldLoadingOverlay = preload("res://scripts/ui/world_loading_overlay.gd")


## completed 信号断言用（lambda 按值捕获，须走成员变量）
var _completed_fired: bool = false
var _completed_count: int = 0


func _make_overlay() -> WorldLoadingOverlay:
	var overlay: WorldLoadingOverlay = WorldLoadingOverlay.new()
	add_child_autofree(overlay)
	overlay.size = Vector2(1280, 720)
	return overlay


## 按帧模拟推进（1/60s 步进），与真实 _process 语义一致（大单步 delta
## 会把整个 delta 都当快速补间窗口，导致测试进度直接跳到目标刻度）。
func _simulate(overlay: WorldLoadingOverlay, seconds: float) -> void:
	var frames: int = int(seconds * 60.0)
	for i in frames:
		overlay._process(1.0 / 60.0)


func before_each() -> void:
	# 断言中文文案：固定 zh_CN，与用户设置文件 locale 解耦
	TranslationServer.set_locale("zh_CN")


func test_default_text() -> void:
	var overlay: WorldLoadingOverlay = _make_overlay()
	assert_eq(overlay.get_text(), "正在生成世界...", "初始文案应为生成阶段")


func test_set_text_updates_text() -> void:
	var overlay: WorldLoadingOverlay = _make_overlay()
	overlay.set_text("正在加载地形...")
	assert_eq(overlay.get_text(), "正在加载地形...", "set_text 应更新阶段文案")


func test_unknown_stage_ignored() -> void:
	"""未知生成阶段不应推进进度条。"""
	var overlay: WorldLoadingOverlay = _make_overlay()
	overlay.set_stage("bogus")
	_simulate(overlay, 1.0)
	assert_eq(overlay.get_progress_ratio(), 0.0, "未知阶段不应推进进度")


func test_stage_advances_progress_to_cap() -> void:
	"""生成阶段按刻度推进进度条，封顶 90%（余量留给地形加载）。"""
	var overlay: WorldLoadingOverlay = _make_overlay()
	overlay.set_stage("elevation")
	_simulate(overlay, 2.0)
	var first: float = overlay.get_progress_ratio()
	assert_gt(first, 0.0, "收到阶段后进度条应推进")

	overlay.set_stage("chunks")
	_simulate(overlay, 4.0)
	var final: float = overlay.get_progress_ratio()
	assert_gt(final, first, "阶段推进后刻度应增长")
	assert_lt(final, overlay.STAGE_PROGRESS_CAP + 1e-6, "阶段进度应封顶 90%")


func test_progress_is_monotonic() -> void:
	"""阶段切换时进度应单调不减（不越格、不回退）。"""
	var overlay: WorldLoadingOverlay = _make_overlay()
	overlay.set_stage("elevation")
	_simulate(overlay, 0.1)
	var before: float = overlay.get_progress_ratio()
	overlay.set_stage("climate")
	_simulate(overlay, 0.1)
	assert_gt(overlay.get_progress_ratio(), before, "进度应单调不减")


func test_terrain_progress_fills_to_full() -> void:
	"""出生点地形加载进度从 90% 基准补满到 100%。"""
	var overlay: WorldLoadingOverlay = _make_overlay()
	overlay.set_stage("chunks")
	_simulate(overlay, 4.0)
	assert_lt(overlay.get_progress_ratio(), 0.9, "前置：阶段进度停在 90% 以下")

	overlay.set_terrain_progress(1.0)
	_simulate(overlay, 200.0)
	assert_almost_eq(overlay.get_progress_ratio(), 1.0, 0.001, "地形加载完成应补满进度条")


func test_complete_fills_bar_and_emits() -> void:
	"""complete() 后进度条补满到 100% 并发出 completed 信号（满格收尾）。"""
	var overlay: WorldLoadingOverlay = _make_overlay()
	_completed_fired = false
	overlay.completed.connect(func() -> void: _completed_fired = true)

	overlay.set_stage("chunks")
	overlay.complete()
	_simulate(overlay, 200.0)

	assert_almost_eq(overlay.get_progress_ratio(), 1.0, 0.001, "补满动画应到 100%")
	assert_true(_completed_fired, "补满后应发出 completed 信号")


func test_completed_emitted_only_once() -> void:
	"""completed 信号应只发出一次（防重复收尾）。"""
	var overlay: WorldLoadingOverlay = _make_overlay()
	_completed_count = 0
	overlay.completed.connect(func() -> void: _completed_count += 1)

	overlay.complete()
	_simulate(overlay, 200.0)
	_simulate(overlay, 200.0)
	assert_eq(_completed_count, 1, "补满标记后不应重复发出")


func test_reset_clears_progress() -> void:
	"""reset（世界重建/读档）后进度与补满状态应归零。"""
	var overlay: WorldLoadingOverlay = _make_overlay()
	overlay.set_stage("chunks")
	overlay.complete()
	_simulate(overlay, 200.0)
	assert_almost_eq(overlay.get_progress_ratio(), 1.0, 0.001, "前置：进度已补满")

	overlay.reset()
	assert_eq(overlay.get_progress_ratio(), 0.0, "reset 后进度应归零")
	_simulate(overlay, 1.0)
	assert_eq(overlay.get_progress_ratio(), 0.0, "reset 后目标清零，进度不再推进")


func test_process_skips_when_hidden() -> void:
	"""隐藏后动画停走（不浪费每帧重绘）。"""
	var overlay: WorldLoadingOverlay = _make_overlay()
	overlay.visible = false
	overlay.set_stage("elevation")
	_simulate(overlay, 0.5)
	assert_eq(overlay.get_progress_ratio(), 0.0, "隐藏时进度不应推进")


func test_background_is_opaque() -> void:
	"""背景必须全不透明：加载期间完全遮住下方正在构建的地形。"""
	var overlay: WorldLoadingOverlay = _make_overlay()
	assert_eq(overlay.BG_COLOR.a, 1.0, "背景 alpha 应为 1.0，不能透出地形加载过程")


func test_main_world_uses_overlay_for_loading() -> void:
	"""main_world 的加载提示应挂载为全屏覆盖层（而非纯文字 Label）。"""
	var scene: PackedScene = load("res://scenes/main.tscn")
	var main: Node2D = autoqfree(scene.instantiate())
	add_child(main)
	main.process_mode = Node.PROCESS_MODE_DISABLED

	assert_not_null(main._loading_overlay)
	assert_true(main._loading_overlay is WorldLoadingOverlay,
		"加载提示应为 WorldLoadingOverlay，具备不透明背景与进度条")
	assert_eq(main._loading_overlay.anchor_right, 1.0, "覆盖层应铺满视口（FULL_RECT 锚点）")
	assert_eq(main._loading_overlay.anchor_bottom, 1.0)

	# 地形就绪前：覆盖层可见，文案切换为加载地形，进度条钉在 90% 基准
	main._set_birth_chunk(0, 0)
	assert_true(main._loading_overlay.visible)
	assert_eq(main._loading_overlay.get_text(), "正在加载地形...")
	_simulate(main._loading_overlay, 200.0)
	assert_almost_eq(main._loading_overlay.get_progress_ratio(), 0.9, 0.001,
		"出生点阶段应将进度条推进到 90% 基准（余量留给地形加载）")

	# 地形就绪后：补满进度条 → 覆盖层隐藏（露出加载完成的世界与玩家）
	main._check_terrain_ready(true)
	assert_true(main._loading_overlay.visible, "就绪后应先补满进度条收尾")
	_simulate(main._loading_overlay, 10.0)
	assert_false(main._loading_overlay.visible, "补满后应隐藏覆盖层")
	assert_true(main._player.visible, "玩家应随覆盖层隐藏同时可见")
