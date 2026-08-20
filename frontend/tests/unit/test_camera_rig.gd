"""CameraRig 单元测试 — 2D 相机缩放与可视半径纯函数。

process_zoom 依赖 Input 单例（需要节点树），此处验证纯数学：
visible_radius 为纯计算（无绑定相机时用兜底视口）；缩放钳制/节点
操作（bind/apply）由集成测试覆盖。
"""

extends GutTest


func _make() -> CameraRig:
	return CameraRig.new()


# ── 可视半径 ──────────────────────────────────────────────

func test_visible_radius_scales_with_zoom() -> void:
	"""可视半径随缩放增大而减小（zoom 越大看得越近）。"""
	var rig := _make()
	var r1: float = rig.visible_radius(1.0)
	var r2: float = rig.visible_radius(2.0)
	assert_gt(r1, 0.0, "有效缩放应有正半径")
	assert_almost_eq(r2, r1 * 0.5, 0.01, "半径与缩放成反比")


func test_visible_radius_uses_pixel_scale() -> void:
	"""1x 缩放、16px tile：1280x720 视口半径 = 屏幕半对角线/16。"""
	var rig := _make()
	var expected: float = (Vector2(1280, 720) * 0.5).length() / 16.0
	assert_almost_eq(rig.visible_radius(1.0), expected, 0.001)


func test_visible_radius_custom_viewport() -> void:
	"""显式视口尺寸优先于兜底值。"""
	var rig := _make()
	var r_small: float = rig.visible_radius(1.0, Vector2(640, 360))
	var r_default: float = rig.visible_radius(1.0)
	assert_lt(r_small, r_default, "小视口半径应小于默认视口")


func test_visible_radius_zoom_zero_returns_infinity() -> void:
	"""zoom=0 为非法输入（除零）：函数应返回 inf 而非崩溃（由钳制保证不触发）。"""
	var rig := _make()
	assert_eq(is_inf(rig.visible_radius(0.0)), true, "zoom=0 应为 inf（调用方钳制）")
