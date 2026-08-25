"""地图生成调参步骤单元测试（Issue #8）。

覆盖 scripts/ui/setup_steps/map_step.gd 的纯逻辑部分：
种子定案、大陆占比范围、参数产出、预览请求与响应应用。

UI 绘制与输入命中不在此测（自绘命中由集成测试覆盖）。
"""

extends GutTest


func _make_step() -> MapSetupStep:
	var step: MapSetupStep = MapSetupStep.new()
	return step


func _make_sender() -> Array:
	"""返回 [step, sent]：sent = 记录已发送的预览请求。"""
	var sent: Array = []
	var sender := func(message: Dictionary) -> void:
		sent.append(message)
	var step: MapSetupStep = _make_step()
	step.request_sender = sender
	return [step, sent]


# ── 种子定案 ──────────────────────────────────────────────

func before_each() -> void:
	# 断言中文文案：固定 zh_CN，与用户设置文件 locale 解耦
	TranslationServer.set_locale("zh_CN")


func test_setup_leaves_seed_pending_when_empty() -> void:
	"""seed 空/0（未定案）保持占位——由后端预览响应回传 hex 定案。"""
	var step: MapSetupStep = _make_step()
	step.setup({"seed": "", "gen_params": {}})
	assert_eq(step.get_params()["seed"], "", "未定案保持占位")
	step.on_preview_response({"seed": "a3f9"})
	assert_eq(step.get_params()["seed"], "a3f9", "预览响应回传 hex 种子定案")


func test_setup_keeps_explicit_seed() -> void:
	var step: MapSetupStep = _make_step()
	step.setup({"seed": "2a", "gen_params": {}})
	assert_eq(step.get_params()["seed"], "2a")


func test_seed_submitted_hex_normalized() -> void:
	"""手输种子：hex 校验通过并归一为小写，刷新预览。"""
	var pair: Array = _make_sender()
	var step: MapSetupStep = pair[0]
	var sent: Array = pair[1]
	step.setup({"seed": "2a", "gen_params": {}})
	step.on_preview_response({"seed": "2a"})  # 清在途，允许补发
	step.on_seed_submitted("A3F9")
	assert_eq(step.get_params()["seed"], "a3f9", "hex 大小写归一")
	assert_eq(sent[sent.size() - 1]["payload"]["seed"], "a3f9")


func test_seed_submitted_arbitrary_text_maps_to_256bit() -> void:
	"""任意文本（非 hex）→ SHA-256 映射为 64 字符 hex 种子。

	契约：合法 hex 直通；其余输入统一映射为 256-bit 规格种子
	（与 manifest.SEED_MAX 一致），同文本恒同种子可复现。
	"""
	for seed_text in ["my world", "0x2a", "zz", "g", "1.5", "-1", "中文种子"]:
		var pair: Array = _make_sender()
		var step: MapSetupStep = pair[0]
		var sent: Array = pair[1]
		step.setup({"seed": "", "gen_params": {}})
		step.on_preview_response({"seed": ""})  # 清在途，允许补发
		step.on_seed_submitted(seed_text)
		var seed: String = str(step.get_params()["seed"])
		assert_eq(seed.length(), 64, "文本 %s 应映射为 64 字符 hex" % seed_text)
		assert_eq(seed, seed.to_lower(), "映射结果小写")
		assert_eq(seed, seed_text.sha256_text(), "映射 = SHA-256(文本)")
		assert_eq(sent[sent.size() - 1]["payload"]["seed"], seed,
			"预览请求携带映射后种子")


func test_seed_text_mapping_deterministic() -> void:
	"""同文本两次映射结果一致（可复现世界）。"""
	var step: MapSetupStep = _make_step()
	step.on_seed_submitted("fate word")
	var first: String = str(step.get_params()["seed"])
	step.on_seed_submitted("fate word")
	assert_eq(str(step.get_params()["seed"]), first, "同文本恒同种子")


func test_seed_submitted_empty_keeps_seed() -> void:
	"""空串提交 = 不修改（占位语义由随机按钮负责）。"""
	var step: MapSetupStep = _make_step()
	step.setup({"seed": "2a", "gen_params": {}})
	step.on_seed_submitted("")
	assert_eq(step.get_params()["seed"], "2a")


func test_inflight_response_does_not_override_explicit_seed() -> void:
	"""竞态防护：在途旧响应（随机定案）不得覆盖用户手输的显式种子。

	时序：占位预览 R1 在途 → 用户手输 "2a"（dirty 不发送）→ R1 响应
	（携带随机 seed "b7e1"）到达并补发 R2("2a")——此时 _seed 必须
	保持 "2a"，R2 响应（echo "2a"）到达后仍为 "2a"。
	"""
	var pair: Array = _make_sender()
	var step: MapSetupStep = pair[0]
	var sent: Array = pair[1]
	step.setup({"seed": "", "gen_params": {}})  # R1("") 在途
	assert_eq(sent.size(), 1)
	step.on_seed_submitted("2a")  # 手输：dirty，不发送
	assert_eq(step.get_params()["seed"], "2a")
	step.on_preview_response({"seed": "b7e1"})  # R1 响应 + 补发 R2
	assert_eq(sent.size(), 2, "响应后补发 R2")
	assert_eq(step.get_params()["seed"], "2a", "旧响应不得覆盖显式种子")
	assert_eq(sent[1]["payload"]["seed"], "2a", "R2 携带手输种子")
	step.on_preview_response({"seed": "2a"})  # R2 响应（echo）
	assert_eq(step.get_params()["seed"], "2a")


func test_display_seed_truncates_long_hex() -> void:
	"""64 字符 hex 显示截断（内部保留完整值）。"""
	var step: MapSetupStep = _make_step()
	step.setup({"seed": "", "gen_params": {}})
	assert_eq(step._display_seed(), "随机", "未定案显示随机")
	var long_hex: String = "a3f9" + "0".repeat(60)
	step.setup({"seed": long_hex, "gen_params": {}})
	assert_eq(step._display_seed(),
		"a3f9" + "0".repeat(6) + "…" + "0".repeat(6), "长 hex 截断显示")
	assert_eq(step.get_params()["seed"], long_hex, "内部保留完整值")


func test_setup_restores_land_ratio() -> void:
	"""返回本步骤时恢复此前调参的占比。"""
	var step: MapSetupStep = _make_step()
	step.setup({"seed": "1", "gen_params": {"land_ratio": 0.35}})
	assert_eq(step.get_params()["gen_params"]["land_ratio"], 0.35)
	assert_eq(step.get_params()["gen_params"]["width_km"], 100.0,
		"未调参尺寸仍产出默认值")


func test_setup_clamps_out_of_range_ratio() -> void:
	var step: MapSetupStep = _make_step()
	step.setup({"seed": "1", "gen_params": {"land_ratio": 1.5}})
	var ratio: float = step.get_params()["gen_params"]["land_ratio"]
	assert_lt(ratio, 0.91)


# ── 参数产出 ──────────────────────────────────────────────

func test_get_params_shape() -> void:
	var step: MapSetupStep = _make_step()
	step.setup({"seed": "5", "gen_params": {}})
	var params: Dictionary = step.get_params()
	assert_eq(params["seed"], "5")
	assert_eq(params["gen_params"], {
		"land_ratio": 0.55, "width_km": 100.0, "height_km": 60.0,
	})


# ── 地图尺寸 ──────────────────────────────────────────────

func test_setup_restores_size() -> void:
	"""返回本步骤时恢复此前调参的尺寸档位。"""
	var step: MapSetupStep = _make_step()
	step.setup({"seed": "1", "gen_params": {"width_km": 60.0, "height_km": 36.0}})
	assert_eq(step._size_index, 0, "小档应被恢复")
	var gen: Dictionary = step.get_params()["gen_params"]
	assert_eq(gen["width_km"], 60.0)
	assert_eq(gen["height_km"], 36.0)


func test_setup_defaults_to_medium() -> void:
	var step: MapSetupStep = _make_step()
	step.setup({"seed": "1", "gen_params": {}})
	assert_eq(step._size_index, 1, "未调参尺寸默认中档")


func test_setup_partial_size_falls_back_to_default() -> void:
	"""只调一项尺寸（不匹配任何档位）回退默认中档。"""
	var step: MapSetupStep = _make_step()
	step.setup({"seed": "1", "gen_params": {"width_km": 60.0}})
	assert_eq(step._size_index, 1)


func test_set_size_refreshes_preview() -> void:
	"""切换尺寸重新请求预览，且请求携带新尺寸（范围变化）。"""
	var pair: Array = _make_sender()
	var step: MapSetupStep = pair[0]
	var sent: Array = pair[1]
	step.setup({"seed": "2a", "gen_params": {}})
	step.on_preview_response({"seed": "2a"})
	var before: int = sent.size()
	step._set_size(2)
	assert_eq(step._size_index, 2)
	assert_eq(sent.size(), before + 1, "尺寸切换应刷新预览")
	assert_eq(sent[before]["payload"]["width_km"], 150.0)
	assert_eq(sent[before]["payload"]["height_km"], 90.0)
	assert_eq(step.get_params()["gen_params"]["width_km"], 150.0)


func test_set_size_same_index_ignored() -> void:
	var step: MapSetupStep = _make_step()
	step.setup({"seed": "1", "gen_params": {}})
	step._set_size(1)
	assert_eq(step._size_index, 1)
	step._set_size(99)
	assert_eq(step._size_index, 1, "越界索引忽略")


func test_step_identity() -> void:
	assert_eq(_make_step().step_id(), "map")
	assert_true(_make_step().title() != "")


func test_validate_always_passes() -> void:
	"""滑块/种子输入本身有范围约束，无额外校验失败。"""
	assert_eq(_make_step().validate(), "")


# ── 预览请求 ──────────────────────────────────────────────

func test_setup_sends_preview_request() -> void:
	var pair: Array = _make_sender()
	var step: MapSetupStep = pair[0]
	var sent: Array = pair[1]
	step.setup({"seed": "2a", "gen_params": {}})
	assert_eq(sent.size(), 1, "进入步骤即请求一次预览")
	assert_eq(sent[0]["request_type"], SaveApi.MAP_PREVIEW)
	assert_eq(sent[0]["payload"], {
		"seed": "2a", "land_ratio": 0.55,
		"width_km": 100.0, "height_km": 60.0,
		"layers": ["temp", "rain", "climate"],
	})


func test_randomize_seed_sends_new_preview() -> void:
	"""随机掷种：置回占位（seed=""），后端预览时随机定案。"""
	var pair: Array = _make_sender()
	var step: MapSetupStep = pair[0]
	var sent: Array = pair[1]
	step.setup({"seed": "2a", "gen_params": {}})
	step.on_preview_response({"seed": "2a"})
	var before: int = sent.size()
	step._randomize_seed()
	assert_eq(sent.size(), before + 1)
	assert_eq(sent[sent.size() - 1]["payload"]["seed"], "",
		"随机掷种 = 发送占位，由后端定案")
	assert_eq(step.get_params()["seed"], "", "定案前保持占位")
	step.on_preview_response({"seed": "b7e1"})
	assert_eq(step.get_params()["seed"], "b7e1", "响应后定案为新种子")


func test_ratio_change_requests_preview() -> void:
	var pair: Array = _make_sender()
	var step: MapSetupStep = pair[0]
	var sent: Array = pair[1]
	step.setup({"seed": "2a", "gen_params": {}})
	# 滑块 rect(100,100,200,24)：比例区宽 178，x=189 → 占比 0.50
	step._slider_rect = Rect2(100.0, 100.0, 200.0, 24.0)
	step.on_preview_response({"seed": "2a"})
	var before: int = sent.size()
	step._set_ratio_from_x(189.0)
	assert_eq(sent.size(), before + 1, "占比变化应刷新预览")
	assert_eq(step.get_params()["gen_params"]["land_ratio"], 0.5)
	assert_eq(sent[sent.size() - 1]["payload"]["land_ratio"], 0.5)


func test_same_ratio_no_duplicate_request() -> void:
	var pair: Array = _make_sender()
	var step: MapSetupStep = pair[0]
	var sent: Array = pair[1]
	step.setup({"seed": "2a", "gen_params": {}})
	step._slider_rect = Rect2(100.0, 100.0, 200.0, 24.0)
	step.on_preview_response({"seed": "2a"})
	step._set_ratio_from_x(189.0)
	step.on_preview_response({"seed": "2a"})
	var after_first: int = sent.size()
	step._set_ratio_from_x(189.0)  # 同值（取整后）不重复请求
	assert_eq(sent.size(), after_first)
	assert_gt(after_first, 1)


func test_inflight_changes_coalesced_and_resent() -> void:
	"""在途期间多次变化合并：不追加请求，响应后仅补发一次最新参数。"""
	var pair: Array = _make_sender()
	var step: MapSetupStep = pair[0]
	var sent: Array = pair[1]
	step.setup({"seed": "2a", "gen_params": {}})
	# 滑块 rect(100,100,200,24)：比例区宽 178，x=189 → 0.50，x=178 → 0.45
	step._slider_rect = Rect2(100.0, 100.0, 200.0, 24.0)
	step._set_ratio_from_x(189.0)  # 在途：标记脏不发送
	step._set_ratio_from_x(178.0)  # 再次变化：仍不发送
	assert_eq(sent.size(), 1, "在途期间不追加请求")
	assert_true(step._preview_dirty)
	step.on_preview_response({"seed": "2a"})
	assert_eq(sent.size(), 2, "响应后补发最新参数")
	assert_eq(sent[1]["payload"]["land_ratio"], 0.45)


# ── 预览响应 ──────────────────────────────────────────────

func test_preview_response_applied() -> void:
	var step: MapSetupStep = _make_step()
	step.setup({"seed": "2a", "gen_params": {}})
	var payload := {
		"seed": "2a", "land_ratio": 0.55,
		"width": 2, "height": 2,
		"elevation": [-100, -50, 100, 200],
		"land_percent": 0.5,
	}
	step.on_preview_response(payload)
	assert_eq(step._preview, payload)
	assert_false(step._preview_pending, "响应到达后复位等待态")


func test_preview_failed_clears_pending() -> void:
	var step: MapSetupStep = _make_step()
	step.setup({"seed": "2a", "gen_params": {}})
	assert_true(step._preview_pending, "请求发出后处于等待态")
	step.on_preview_failed()
	assert_false(step._preview_pending)


# ── 气候图层视图 ──────────────────────────────────────────

func _full_preview_payload() -> Dictionary:
	"""含全部气候图层的 2×2 预览（地形 + 温度 + 降雨 + 气候带）。"""
	return {
		"seed": "2a", "land_ratio": 0.55,
		"width": 2, "height": 2,
		"elevation": [-100, -50, 100, 200],
		"temperature": [5, 8, 22, 28],
		"rainfall": [100, 400, 900, 1800],
		"climate": [6, 6, 0, 1],
		"land_percent": 0.5,
	}


func test_view_defaults_to_elevation() -> void:
	var step: MapSetupStep = _make_step()
	step.setup({"seed": "2a", "gen_params": {}})
	assert_eq(step._view_mode, "elevation", "进入步骤默认地形视图")
	assert_eq(step._current_view()["key"], "elevation")


func test_view_switch_requires_layer_data() -> void:
	"""响应缺图层字段（旧后端）时切换被忽略，视图降级地形。"""
	var step: MapSetupStep = _make_step()
	step.setup({"seed": "2a", "gen_params": {}})
	step.on_preview_response({
		"seed": "2a", "width": 2, "height": 2,
		"elevation": [-100, -50, 100, 200],
	})
	step._set_view_mode(1)  # 温度（缺字段）
	assert_eq(step._view_mode, "elevation", "缺图层不切换")
	assert_eq(step._current_view()["key"], "elevation", "绘制降级地形")


func test_view_switch_with_layers() -> void:
	var step: MapSetupStep = _make_step()
	step.setup({"seed": "2a", "gen_params": {}})
	step.on_preview_response(_full_preview_payload())
	step._set_view_mode(2)  # 降雨
	assert_eq(step._view_mode, "rain")
	assert_eq(step._current_view()["key"], "rain")
	step._set_view_mode(3)  # 气候
	assert_eq(step._view_mode, "climate")
	assert_eq(step._current_view()["key"], "climate")
	step._set_view_mode(0)  # 回到地形
	assert_eq(step._view_mode, "elevation")


func test_view_switch_does_not_resend_request() -> void:
	"""切视图只换着色，不重新请求预览（零往返）。"""
	var pair: Array = _make_sender()
	var step: MapSetupStep = pair[0]
	var sent: Array = pair[1]
	step.setup({"seed": "2a", "gen_params": {}})
	step.on_preview_response(_full_preview_payload())
	var before: int = sent.size()
	step._set_view_mode(1)
	step._set_view_mode(2)
	step._set_view_mode(3)
	assert_eq(sent.size(), before, "视图切换不产生新请求")


func test_view_same_index_ignored() -> void:
	var step: MapSetupStep = _make_step()
	step.setup({"seed": "2a", "gen_params": {}})
	step.on_preview_response(_full_preview_payload())
	step._set_view_mode(1)
	step._set_view_mode(1)
	assert_eq(step._view_mode, "temp")


## 预览视图定义（与 MapSetupStep.VIEWS 同构；调色板测试不构建步骤实例）
const PREVIEW_VIEWS: Array = [
	{"key": "elevation", "label_key": "ui.map.view_elevation", "field": "elevation"},
	{"key": "temp", "label_key": "ui.map.view_temp", "field": "temperature"},
	{"key": "rain", "label_key": "ui.map.view_rain", "field": "rainfall"},
	{"key": "climate", "label_key": "ui.map.view_climate", "field": "climate"},
]


func test_info_line_ranges() -> void:
	"""信息行：地形/温度/降雨显示数值范围，气候视图显示悬停提示。"""
	var pal: MapPreviewPalette = MapPreviewPalette.new()
	assert_eq(pal.info_line(PREVIEW_VIEWS[0], [-100, -50, 100, 200]), "海拔 -100~200m")
	assert_eq(pal.info_line(PREVIEW_VIEWS[1], [5, 8, 22, 28]), "温度 5~28°C")
	assert_eq(pal.info_line(PREVIEW_VIEWS[2], [100, 400, 900, 1800]), "降雨 100~1800mm")
	assert_eq(pal.info_line(PREVIEW_VIEWS[3], [6, 6, 0, 1]), "移入地图查看气候")


func test_info_line_hover_cell() -> void:
	"""鼠标悬停格子：显示位置值；气候视图显示分类，海域显示海洋。"""
	var pal: MapPreviewPalette = MapPreviewPalette.new()
	pal.preview = _full_preview_payload()
	pal.map_w = 2
	pal.map_h = 2
	pal.hover_cell = Vector2i(1, 1)  # idx=3, 28°C
	assert_eq(pal.info_line(PREVIEW_VIEWS[1], pal.preview["temperature"]),
		"温度 5~28°C    当前位置 28°C")
	pal.hover_cell = Vector2i(0, 0)  # idx=0, 海拔 -100 海域
	assert_eq(pal.info_line(PREVIEW_VIEWS[3], pal.preview["climate"]), "当前位置 海洋")
	pal.hover_cell = Vector2i(1, 1)  # idx=3, 气候 1 = 热带草原
	assert_eq(pal.info_line(PREVIEW_VIEWS[3], pal.preview["climate"]), "当前位置 热带草原")
	pal.hover_cell = Vector2i(-1, -1)  # 移出地图 → 恢复悬停提示
	assert_eq(pal.info_line(PREVIEW_VIEWS[3], pal.preview["climate"]), "移入地图查看气候")


func test_hover_value_text_out_of_bounds() -> void:
	"""鼠标格越界（数据异常/地图未生成）时返回空串。"""
	var pal: MapPreviewPalette = MapPreviewPalette.new()
	pal.map_w = 2
	pal.map_h = 2
	pal.hover_cell = Vector2i(5, 5)
	assert_eq(pal.hover_value_text(PREVIEW_VIEWS[0], [1, 2, 3, 4]), "")
	pal.hover_cell = Vector2i(-1, -1)
	assert_eq(pal.hover_value_text(PREVIEW_VIEWS[0], [1, 2, 3, 4]), "")


func test_field_range() -> void:
	"""数值范围取整；空字段返回 [0, 0]。"""
	var pal: MapPreviewPalette = MapPreviewPalette.new()
	assert_eq(pal.field_range([5, 8, 22, 28]), [5, 28])
	assert_eq(pal.field_range([100, 400, 900, 1800]), [100, 1800])
	assert_eq(pal.field_range([]), [0, 0])


func test_layer_colors_plausible() -> void:
	"""各视图着色函数：地形按海拔分色、气候图层数值对应渐变色、气候档固定色。"""
	var pal: MapPreviewPalette = MapPreviewPalette.new()
	assert_ne(pal.layer_color(PREVIEW_VIEWS[0], 100.0, 0.0),
		pal.layer_color(PREVIEW_VIEWS[0], 5000.0, 0.0), "地形按海拔分色")
	assert_ne(pal.layer_color(PREVIEW_VIEWS[1], 100.0, -10.0),
		pal.layer_color(PREVIEW_VIEWS[1], 100.0, 30.0), "温度高低色不同")
	assert_ne(pal.layer_color(PREVIEW_VIEWS[2], 100.0, 50.0),
		pal.layer_color(PREVIEW_VIEWS[2], 100.0, 2200.0), "降雨干湿色不同")
	assert_eq(pal.layer_color(PREVIEW_VIEWS[3], 100.0, 0.0),
		MapPreviewPalette.CLIMATE_ZONE_COLORS[0], "气候档位固定色")
	assert_eq(pal.layer_color(PREVIEW_VIEWS[3], 100.0, 7.0),
		MapPreviewPalette.CLIMATE_ZONE_COLORS[7])


func test_cell_color_sea_branches() -> void:
	"""海域着色：地形保留深度渐变、气候统一深蓝、温度/降雨显示海域数据。"""
	var pal: MapPreviewPalette = MapPreviewPalette.new()
	# 地形视图：海域 = 深度渐变（随深度变化，非恒定）
	assert_ne(pal.cell_color(PREVIEW_VIEWS[0], -50.0, 0.0),
		pal.cell_color(PREVIEW_VIEWS[0], -2000.0, 0.0), "地形海域按深度渐变")
	# 气候视图：海域恒定深蓝（气候带仅陆地有意义）
	assert_eq(pal.cell_color(PREVIEW_VIEWS[3], -100.0, 6), MapPreviewPalette.SEA_COLOR)
	# 温度/降雨视图：海域用数值着色（海面温度/全域降雨场）
	assert_eq(pal.cell_color(PREVIEW_VIEWS[1], -100.0, -10.0),
		pal.layer_color(PREVIEW_VIEWS[1], 100.0, -10.0), "海域温度用数值着色")
	assert_eq(pal.cell_color(PREVIEW_VIEWS[2], -100.0, 2200.0),
		pal.layer_color(PREVIEW_VIEWS[2], 100.0, 2200.0), "海域降雨用数值着色")


func test_cell_color_land_uses_layer() -> void:
	"""陆地：任何视图都按图层数值着色。"""
	var pal: MapPreviewPalette = MapPreviewPalette.new()
	assert_eq(pal.cell_color(PREVIEW_VIEWS[1], 200.0, 25.0),
		pal.layer_color(PREVIEW_VIEWS[1], 200.0, 25.0))
	assert_eq(pal.cell_color(PREVIEW_VIEWS[3], 200.0, 4),
		MapPreviewPalette.CLIMATE_ZONE_COLORS[4])


func test_map_cell_at() -> void:
	"""鼠标坐标 → 地图格子；地图外或未绘制返回 (-1, -1)。"""
	var step: MapSetupStep = _make_step()
	step._map_origin = Vector2(100, 100)
	step._map_cell = 4.0
	step._map_w = 10
	step._map_h = 10
	assert_eq(step._map_cell_at(Vector2(100, 100)), Vector2i(0, 0))
	assert_eq(step._map_cell_at(Vector2(135, 115)), Vector2i(8, 3))
	assert_eq(step._map_cell_at(Vector2(99, 100)), Vector2i(-1, -1))
	assert_eq(step._map_cell_at(Vector2(140, 100)), Vector2i(-1, -1))
	step._map_cell = 0.0  # 地图未绘制
	assert_eq(step._map_cell_at(Vector2(100, 100)), Vector2i(-1, -1))
