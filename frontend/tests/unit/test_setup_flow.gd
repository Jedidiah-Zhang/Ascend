"""创建世界流程 — 步骤注册与参数汇总单元测试（Issue #8）。

覆盖 scripts/ui/setup_flow.gd 与 setup_step.gd 契约。
"""

extends GutTest


# ── 测试桩步骤 ────────────────────────────────────────────

class StubStep:
	extends SetupStep

	var _id: String
	var _params: Dictionary

	func _init(id: String, params: Dictionary) -> void:
		_id = id
		_params = params

	func step_id() -> String:
		return _id

	func get_params() -> Dictionary:
		return _params


func _make_steps() -> Array:
	return [
		StubStep.new("map", {"seed": 7, "gen_params": {"land_ratio": 0.55}}),
		StubStep.new("colony", {"gen_params": {"colony_count": 3}}),
	]


# ── 注册表 ────────────────────────────────────────────────

func test_build_steps_contains_map_first() -> void:
	"""当前注册表：地图生成步骤为流程首步。"""
	var steps: Array = SetupFlow.build_steps()
	assert_false(steps.is_empty(), "必须至少注册一个步骤")
	assert_eq(steps[0].step_id(), "map", "地图生成是流程首步（依赖链起点）")


func test_build_steps_all_are_setup_step() -> void:
	var steps: Array = SetupFlow.build_steps()
	for step in steps:
		assert_true(step is SetupStep, "步骤必须实现 SetupStep 契约")


func test_build_steps_unique_ids() -> void:
	var ids: Array = SetupFlow.step_ids(SetupFlow.build_steps())
	assert_eq(ids.size(), ids.duplicate().size(), "步骤 ID 不得重复")
	for id in ids:
		assert_false(id.is_empty(), "步骤 ID 不得为空")


# ── 参数汇总 ──────────────────────────────────────────────

func test_merge_params_accumulates_in_order() -> void:
	"""多步骤产出合并：后者覆盖同名键，gen_params 内键合并。"""
	var merged: Dictionary = SetupFlow.merge_params(_make_steps(), {
		"seed": 0, "gen_params": {},
	})
	assert_eq(merged["seed"], 7)
	var gen: Dictionary = merged["gen_params"]
	assert_eq(gen["land_ratio"], 0.55)
	assert_eq(gen["colony_count"], 3)


func test_merge_params_later_wins() -> void:
	"""后步骤覆盖先步骤同名产出。"""
	var steps: Array = [
		StubStep.new("a", {"seed": 1}),
		StubStep.new("b", {"seed": 2}),
	]
	assert_eq(SetupFlow.merge_params(steps, {})["seed"], 2)


func test_merge_params_skips_non_step_entries() -> void:
	var merged: Dictionary = SetupFlow.merge_params(
		[StubStep.new("a", {"seed": 5}), null, "junk"], {})
	assert_eq(merged["seed"], 5)


func test_merge_params_base_duplicated() -> void:
	"""不修改传入的 base 字典。"""
	var base := {"seed": 0, "gen_params": {}}
	var merged: Dictionary = SetupFlow.merge_params(
		[StubStep.new("a", {"seed": 9})], base)
	assert_eq(merged["seed"], 9)
	assert_eq(base["seed"], 0, "base 不得被原地修改")
