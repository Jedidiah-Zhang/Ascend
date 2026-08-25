"""创建世界流程容器集成测试（Issue #8）。

覆盖 scripts/ui/world_setup.gd：
  - 步骤构建与导航
  - 创建载荷（seed + gen_params 随档定案）
  - CREATE 响应 → 拉起世界进程 + 路由加载进度页
  - 失败与预览响应分发
"""

extends GutTest

const WORLD_SETUP_SCRIPT: String = "res://scripts/ui/world_setup.gd"


## 校验恒失败的步骤桩。
class BlockingStep:
	extends SetupStep

	func step_id() -> String:
		return "blocker"

	func title() -> String:
		return "阻断步骤"

	func setup(_params: Dictionary) -> void:
		pass

	func get_params() -> Dictionary:
		return {}

	func validate() -> String:
		return "参数无效"

	func draw_page(_canvas: Control, _rect: Rect2, _font: Font) -> void:
		pass

	func handle_input(_event: InputEvent, _rect: Rect2) -> bool:
		return false


## 构造页面实例（注入 launcher/router/sender 桩，隔离真实后端与场景切换）。
## 返回 [setup, launched, routed, sent]。
func _make_setup() -> Array:
	var setup: Control = load(WORLD_SETUP_SCRIPT).new()
	setup.size = Vector2(1280, 720)
	autoqfree(setup)
	add_child(setup)
	# 测试隔离：断开 _ready 中建立的真实订阅
	if Connection.connection_lost.is_connected(setup._on_connection_lost):
		Connection.connection_lost.disconnect(setup._on_connection_lost)
	if Connection.backend_failed.is_connected(setup._on_backend_failed):
		Connection.backend_failed.disconnect(setup._on_backend_failed)
	if Connection.connection_established.is_connected(setup._on_connected):
		Connection.connection_established.disconnect(setup._on_connected)

	var launched: Array = []
	var routed: Array = []
	var sent: Array = []
	setup.backend_launcher = func(args: PackedStringArray) -> void:
		launched.append(Array(args))
	setup.scene_router = func() -> void:
		routed.append(true)
	setup.sender = func(message: Dictionary) -> void:
		sent.append(message)
	return [setup, launched, routed, sent]


# ── 步骤构建 ──────────────────────────────────────────────

func before_each() -> void:
	# 断言中文文案：固定 zh_CN，与用户设置文件 locale 解耦
	TranslationServer.set_locale("zh_CN")


func test_ready_builds_map_step() -> void:
	var pair: Array = _make_setup()
	var setup: Control = pair[0]
	assert_eq(setup._steps.size(), 1, "当前注册表：地图生成单步")
	assert_eq(setup._current, 0)
	assert_eq(setup._current_step().step_id(), "map")


func test_seed_input_injected_into_step() -> void:
	"""种子输入框注入：world_setup._ready 必须把 LineEdit 交给步骤。

	回归：此前容器创建了 _seed_input 却从未注入，步骤内 _seed_input
	恒为 null，点击种子框 _open_seed_input 直接返回 false——输入框
	永远打不开（无法输入自定义种子）。
	"""
	var pair: Array = _make_setup()
	var setup: Control = pair[0]
	assert_not_null(setup._seed_input, "容器应创建种子输入框")
	assert_same(setup._current_step()._seed_input, setup._seed_input,
		"步骤应持有容器注入的同一个 LineEdit")
	var opened: bool = setup._current_step()._open_seed_input()
	assert_true(opened, "注入后点击种子框应能打开输入框")
	assert_true(setup._seed_input.visible, "输入框应可见")
	assert_true(setup._seed_input.has_focus(), "输入框应获得焦点")
	setup._current_step()._close_seed_input()


func test_next_step_on_single_step_creates() -> void:
	"""单步流程：下一步 = 创建世界（构造 save_create 载荷）。

	种子定案链：进入步骤发预览 → 后端回传 hex → 创建载荷携带。
	"""
	var pair: Array = _make_setup()
	var setup: Control = pair[0]
	var sent: Array = pair[3]
	setup._current_step().on_preview_response(
		{"seed": "a3f9", "land_ratio": 0.55, "elevation": [1, 2, 3, 4]})
	setup._next_step()
	assert_eq(sent.size(), 1, "最后一步点击应发出创建请求")
	assert_eq(sent[0]["request_type"], SaveApi.CREATE)
	assert_eq(sent[0]["payload"]["name"], setup._default_save_name())
	assert_eq(sent[0]["payload"]["seed"], "a3f9", "预览回传种子随创建载荷定案")


func test_next_step_carries_ratio_in_gen_params() -> void:
	var pair: Array = _make_setup()
	var setup: Control = pair[0]
	var sent: Array = pair[3]
	var step: MapSetupStep = setup._current_step()
	step.setup({"seed": "2a", "gen_params": {"land_ratio": 0.35}})
	setup._next_step()
	assert_eq(sent[0]["payload"]["seed"], "2a")
	assert_eq(sent[0]["payload"]["gen_params"]["land_ratio"], 0.35)
	assert_eq(sent[0]["payload"]["gen_params"]["width_km"], 100.0,
		"尺寸未调参时产出默认档")


func test_validate_error_blocks_create() -> void:
	var pair: Array = _make_setup()
	var setup: Control = pair[0]
	var sent: Array = pair[3]
	setup._steps = [BlockingStep.new()]
	setup._current = 0
	setup._next_step()
	assert_eq(sent.size(), 0, "校验失败不得发创建请求")
	assert_string_contains(setup._status_text, "参数无效")


# ── CREATE 响应 → 进程拉起 + 路由 ──────────────────────────

func test_create_response_launches_and_routes_to_loading() -> void:
	var pair: Array = _make_setup()
	var setup: Control = pair[0]
	var launched: Array = pair[1]
	var routed: Array = pair[2]
	setup._on_request_response({
		"type": "response",
		"request_type": SaveApi.CREATE,
		"payload": {"world_id": "w-123"},
	})
	assert_eq(launched, [["--world-id", "w-123"]], "应以世界模式拉起后端")
	assert_eq(routed.size(), 1, "应路由到加载进度页")
	assert_true(setup._entering_world)


func test_create_response_empty_world_id_shows_error() -> void:
	var pair: Array = _make_setup()
	var setup: Control = pair[0]
	var launched: Array = pair[1]
	var routed: Array = pair[2]
	setup._on_request_response({
		"type": "response",
		"request_type": SaveApi.CREATE,
		"payload": {"world_id": ""},
	})
	assert_eq(launched.size(), 0, "无效 world_id 不得拉起进程")
	assert_eq(routed.size(), 0)
	assert_string_contains(setup._status_text, "创建存档失败")


func test_create_error_message_shows_reason() -> void:
	var pair: Array = _make_setup()
	var setup: Control = pair[0]
	setup._on_request_response({
		"type": "error",
		"request_type": SaveApi.CREATE,
		"error": "磁盘已满",
	})
	assert_string_contains(setup._status_text, "磁盘已满")


func test_connection_lost_during_create_shows_error() -> void:
	var pair: Array = _make_setup()
	var setup: Control = pair[0]
	setup._busy = true
	setup._on_connection_lost()
	assert_false(setup._busy)
	assert_string_contains(setup._status_text, "连接中断")


# ── 预览响应（map_step 回调化后由步骤自身消费） ────────────

func test_preview_response_reaches_step() -> void:
	var pair: Array = _make_setup()
	var setup: Control = pair[0]
	setup._current_step()._preview_pending = true
	setup._current_step().on_preview_response(
		{"seed": "2a", "land_ratio": 0.55, "elevation": [1, 2, 3, 4]})
	assert_false(setup._current_step()._preview_pending, "响应应送达步骤")
	assert_eq(setup._current_step()._preview["elevation"], [1, 2, 3, 4])


func test_preview_error_clears_pending() -> void:
	var pair: Array = _make_setup()
	var setup: Control = pair[0]
	setup._current_step()._preview_pending = true
	setup._current_step().on_preview_failed()
	assert_false(setup._current_step()._preview_pending)
