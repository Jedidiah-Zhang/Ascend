"""CameraRig 单元测试 — 相机几何纯函数。

visible_radius / shadow_coverage 为纯数学计算（不依赖相机节点绑定），
可独立验证；节点操作（bind/apply）由集成测试覆盖。
"""

extends GutTest


func _make() -> CameraRig:
	return CameraRig.new()


# ── 可视半径 ──────────────────────────────────────────────

func test_visible_radius_scales_with_distance() -> void:
	"""可视半径随相机距离增大而增大（正比关系）。"""
	var rig := _make()
	var r1: float = rig.visible_radius(400.0)
	var r2: float = rig.visible_radius(800.0)
	assert_gt(r1, 0.0, "正距离应有正半径")
	assert_almost_eq(r2, r1 * 2.0, 0.01, "正交投影下半径与距离成正比")


# ── 阴影覆盖 ──────────────────────────────────────────────

func test_shadow_coverage_normal_sun_is_margin_scaled_visible() -> void:
	"""正常太阳高度角：覆盖 = 可视半径 × 余量（1.35）。"""
	var rig := _make()
	var coverage: float = rig.shadow_coverage(400.0, 0.5)
	assert_almost_eq(
		coverage, rig.visible_radius(400.0) * CameraRig.SHADOW_COVERAGE_MARGIN,
		0.001)


func test_shadow_coverage_low_sun_expands() -> void:
	"""低角度太阳：覆盖范围放大（最长阴影 3 倍余量）。"""
	var rig := _make()
	var normal: float = rig.shadow_coverage(400.0, 0.5)
	var low: float = rig.shadow_coverage(400.0, 0.05)
	assert_gt(low, normal, "低角度应放大覆盖范围")


func test_shadow_coverage_monotonic_in_sun_altitude() -> void:
	"""覆盖随高度角单调递减（低角度放大，高角度收敛 1.35 倍）。"""
	var rig := _make()
	var prev := 1e9
	for alt in [0.01, 0.05, 0.1, 0.15, 0.2, 0.25, 0.3, 0.5, 0.9]:
		var c: float = rig.shadow_coverage(400.0, alt)
		assert_lte(c, prev, "高度角 %f 覆盖应不超过低角度值" % alt)
		prev = c
