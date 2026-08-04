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


func test_snapshot_request() -> void:
	var req: Dictionary = SaveApi.snapshot_request("w1")
	assert_eq(req["request_type"], SaveApi.SNAPSHOT)
	assert_eq(req["payload"]["world_id"], "w1")


func test_load_request_world() -> void:
	var req: Dictionary = SaveApi.load_request("w1")
	assert_eq(req["request_type"], SaveApi.LOAD)
	assert_eq(req["payload"]["world_id"], "w1")
	assert_eq(req["payload"]["snapshot"], "")


func test_load_request_snapshot() -> void:
	var req: Dictionary = SaveApi.load_request("", "snap.ascendsave")
	assert_eq(req["payload"]["world_id"], "")
	assert_eq(req["payload"]["snapshot"], "snap.ascendsave")


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


func test_parse_snapshots_duplicates() -> void:
	var payload := {"snapshots": [{"file": "a.ascendsave"}, "junk"]}
	var snaps: Array = SaveApi.parse_snapshots(payload)
	assert_eq(snaps.size(), 1)
	assert_eq(snaps[0]["file"], "a.ascendsave")


func test_parse_error_text() -> void:
	assert_eq(SaveApi.parse_error({"error": "存档不存在"}), "存档不存在")
	assert_eq(SaveApi.parse_error({}), "未知错误")
