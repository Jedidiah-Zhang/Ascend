"""SaveApi 协议封装单元测试 — 请求构造与响应解析。

覆盖 scripts/ui/save_api.gd。
"""

extends GutTest


# ── 请求构造 ──────────────────────────────────────────────

func test_list_request_shape() -> void:
	var req: Dictionary = SaveApi.list_request()
	assert_eq(req["type"], "request")
	assert_eq(req["request_type"], SaveApi.LIST)
	assert_eq(req["payload"], {})


func test_create_request_carries_name_and_seed() -> void:
	var req: Dictionary = SaveApi.create_request("我的世界", 42)
	assert_eq(req["request_type"], SaveApi.CREATE)
	assert_eq(req["payload"]["name"], "我的世界")
	assert_eq(req["payload"]["seed"], 42)


func test_create_request_default_random_seed() -> void:
	var req: Dictionary = SaveApi.create_request("x")
	assert_eq(req["payload"]["seed"], 0)


func test_create_request_carries_gen_params() -> void:
	"""调参产出（大陆占比）随创建请求下发（Issue #8）。"""
	var req: Dictionary = SaveApi.create_request(
		"调参世界", 42, {"land_ratio": 0.35})
	assert_eq(req["payload"]["gen_params"], {"land_ratio": 0.35})


func test_create_request_default_gen_params_empty() -> void:
	var req: Dictionary = SaveApi.create_request("x")
	assert_eq(req["payload"]["gen_params"], {})


func test_preview_request_shape() -> void:
	"""地形预览请求（创建世界调参），默认 100×60 km，携带全部气候图层。"""
	var req: Dictionary = SaveApi.preview_request(42, 0.55)
	assert_eq(req["type"], "request")
	assert_eq(req["request_type"], SaveApi.MAP_PREVIEW)
	assert_eq(req["payload"], {
		"seed": 42, "land_ratio": 0.55,
		"width_km": 100.0, "height_km": 60.0,
		"layers": ["temp", "rain", "climate"],
	})


func test_preview_request_custom_size() -> void:
	"""预览请求携带尺寸：采样分辨率固定，网格随尺寸。"""
	var req: Dictionary = SaveApi.preview_request(42, 0.55, 150.0, 90.0)
	assert_eq(req["payload"]["width_km"], 150.0)
	assert_eq(req["payload"]["height_km"], 90.0)


func test_preview_request_layers_override() -> void:
	"""可显式指定图层子集（旧后端兼容 / 按需裁剪）。"""
	var req: Dictionary = SaveApi.preview_request(42, 0.55, 100.0, 60.0, ["temp"])
	assert_eq(req["payload"]["layers"], ["temp"])


func test_snapshot_request() -> void:
	var req: Dictionary = SaveApi.snapshot_request("w1")
	assert_eq(req["request_type"], SaveApi.SNAPSHOT)
	assert_eq(req["payload"]["world_id"], "w1")


func test_snapshot_delete_request_single() -> void:
	var req: Dictionary = SaveApi.snapshot_delete_request("w1", "snap1.ascendsave")
	assert_eq(req["request_type"], SaveApi.SNAPSHOT_DELETE)
	assert_eq(req["payload"], {
		"world_id": "w1",
		"snapshot": "snap1.ascendsave",
		"recursive": false,
	})


func test_snapshot_delete_request_recursive() -> void:
	var req: Dictionary = SaveApi.snapshot_delete_request("w1", "snap1.ascendsave", true)
	assert_eq(req["payload"]["recursive"], true)


func test_parse_deleted() -> void:
	assert_eq(SaveApi.parse_deleted({"deleted": ["a.ascendsave", "b.ascendsave"]}),
		["a.ascendsave", "b.ascendsave"])


func test_parse_deleted_empty_and_junk() -> void:
	assert_eq(SaveApi.parse_deleted({}), [])
	assert_eq(SaveApi.parse_deleted({"deleted": ["ok.ascendsave", 42, "", null]}),
		["ok.ascendsave"])


func test_rename_request() -> void:
	var req: Dictionary = SaveApi.rename_request("w1", "新名")
	assert_eq(req["payload"], {"world_id": "w1", "name": "新名"})


func test_delete_request() -> void:
	assert_eq(SaveApi.delete_request("w1")["payload"], {"world_id": "w1"})


func test_export_request() -> void:
	assert_eq(SaveApi.export_request("w1")["payload"], {"world_id": "w1"})


# ── 响应解析 ──────────────────────────────────────────────

func test_parse_worlds_keeps_fields() -> void:
	var payload := {
		"worlds": [{
			"world_id": "abc",
			"name": "世界",
			"seed": 7,
			"game_time": 172800,
			"play_duration_sec": 3600,
			"snapshot_count": 2,
			"last_played_at": 1000.0,
		}],
	}
	var worlds: Array = SaveApi.parse_worlds(payload)
	assert_eq(worlds.size(), 1)
	var w: Dictionary = worlds[0]
	assert_eq(w["world_id"], "abc")
	assert_eq(w["name"], "世界")
	assert_eq(w["seed"], 7)
	assert_eq(w["game_time"], 172800)
	assert_eq(w["play_duration_sec"], 3600.0)
	assert_eq(w["snapshot_count"], 2)


func test_parse_worlds_fills_missing_fields() -> void:
	var worlds: Array = SaveApi.parse_worlds({"worlds": [{"world_id": "x"}]})
	var w: Dictionary = worlds[0]
	assert_eq(w["name"], "未命名存档")
	assert_eq(w["game_time"], 0)
	assert_eq(w["snapshot_count"], 0)
	assert_eq(w["latest_snapshot_at"], null)


func test_parse_worlds_skips_non_dict() -> void:
	var worlds: Array = SaveApi.parse_worlds({"worlds": ["bad", 42, {"world_id": "ok"}]})
	assert_eq(worlds.size(), 1)
	assert_eq(worlds[0]["world_id"], "ok")


func test_parse_worlds_empty() -> void:
	assert_eq(SaveApi.parse_worlds({}).size(), 0)


func test_parse_worlds_type_coercion() -> void:
	var worlds: Array = SaveApi.parse_worlds({
		"worlds": [{
			"world_id": 123, "name": 456,
			"game_time": "999", "snapshot_count": "3",
		}],
	})
	var w: Dictionary = worlds[0]
	assert_eq(w["world_id"], "123")
	assert_eq(w["name"], "456")
	assert_eq(w["game_time"], 999)
	assert_eq(w["snapshot_count"], 3)


func test_parse_worlds_null_fields_use_defaults() -> void:
	"""字段值为 JSON null 时按默认值兜底（不崩溃）。"""
	var worlds: Array = SaveApi.parse_worlds({
		"worlds": [{
			"world_id": "x",
			"name": null,
			"game_time": null,
			"snapshot_count": null,
			"play_duration_sec": null,
			"live_origin": null,
		}],
	})
	var w: Dictionary = worlds[0]
	assert_eq(w["world_id"], "x", "world_id 非 null 应保留")
	assert_eq(w["name"], "未命名存档", "null 名称兜底为默认")
	assert_eq(w["game_time"], 0, "null 游戏时间兜底为 0")
	assert_eq(w["snapshot_count"], 0, "null 快照数兜底为 0")
	assert_eq(w["play_duration_sec"], 0.0, "null 时长兜底为 0")
	assert_eq(w["live_origin"], "", "null 血缘兜底为空串")


func test_parse_worlds_live_origin() -> void:
	"""live_origin 应透传（回滚后非空）并兜底为空串。"""
	var worlds: Array = SaveApi.parse_worlds({
		"worlds": [{"world_id": "a", "live_origin": "@2026-01-01-000000-aaaaaa-manual.ascendsave"}],
	})
	assert_eq(worlds[0]["live_origin"], "@2026-01-01-000000-aaaaaa-manual.ascendsave")
	var fallback: Array = SaveApi.parse_worlds({"worlds": [{"world_id": "b"}]})
	assert_eq(fallback[0]["live_origin"], "")


func test_parse_snapshots_keeps_lineage_fields() -> void:
	"""快照血缘字段（parent/game_time）应透传。"""
	var snaps: Array = SaveApi.parse_snapshots({
		"snapshots": [{
			"file": "snap1", "parent": "snap0", "game_time": 12345,
			"world_id": "w1",
		}],
	})
	assert_eq(snaps.size(), 1)
	assert_eq(snaps[0]["parent"], "snap0")
	assert_eq(snaps[0]["game_time"], 12345)


func test_parse_current_world_id() -> void:
	"""save_list 顶层 current_world_id 解析（缺失兜底空串）。"""
	assert_eq(SaveApi.parse_current_world_id({"current_world_id": "w-now"}), "w-now")
	assert_eq(SaveApi.parse_current_world_id({}), "")
	assert_eq(SaveApi.parse_current_world_id({"current_world_id": 42}), "42")


func test_parse_snapshots_duplicates() -> void:
	var payload := {"snapshots": [{"file": "a.ascendsave"}, "junk"]}
	var snaps: Array = SaveApi.parse_snapshots(payload)
	assert_eq(snaps.size(), 1)
	assert_eq(snaps[0]["file"], "a.ascendsave")


func test_parse_error_text() -> void:
	assert_eq(SaveApi.parse_error({"error": "存档不存在"}), "存档不存在")
	assert_eq(SaveApi.parse_error({}), "未知错误")
