"""存档协议封装 — 请求构造与响应解析（纯逻辑，UI 负责收发）。

设计原则（Issue #13）：存档是状态通道（request-response）——
世界外的元操作，不产生历史、不进因果图。

本类为纯逻辑 RefCounted：不依赖 Connection 单例，
请求由 UI 层发送，响应由 UI 层转发给解析函数。

协议（docs/世界框架/存档系统/技术.md）:
    save_list   → {worlds: [...], snapshots: [...]}
    save_create {name, seed?} → {world_id}
    save_snapshot {world_id} → {file}
    save_load   {world_id?|snapshot?} → {world_id}
    save_rename {world_id, name} → {world_id, name}
    save_delete {world_id} → {}
    save_export {world_id} → {world_id}
"""

class_name SaveApi
extends RefCounted


# ── 请求类型 ──────────────────────────────────────────────

const LIST: String = "save_list"
const CREATE: String = "save_create"
const SNAPSHOT: String = "save_snapshot"
const LOAD: String = "save_load"
const RENAME: String = "save_rename"
const DELETE: String = "save_delete"
const EXPORT: String = "save_export"


# ── 请求构造 ──────────────────────────────────────────────

static func list_request() -> Dictionary:
	"""存档列表请求。"""
	return {"type": "request", "request_type": LIST, "payload": {}}


static func create_request(save_name: String, world_seed: int = 0) -> Dictionary:
	"""新建存档位请求（seed=0 后端随机）。"""
	return {
		"type": "request", "request_type": CREATE,
		"payload": {"name": save_name, "seed": world_seed},
	}


static func snapshot_request(world_id: String) -> Dictionary:
	"""手动快照请求。"""
	return {
		"type": "request", "request_type": SNAPSHOT,
		"payload": {"world_id": world_id},
	}


static func load_request(world_id: String = "", snapshot: String = "") -> Dictionary:
	"""读档请求：world_id 加载活目录，snapshot 回滚快照。"""
	return {
		"type": "request", "request_type": LOAD,
		"payload": {"world_id": world_id, "snapshot": snapshot},
	}


static func rename_request(world_id: String, name: String) -> Dictionary:
	"""重命名请求。"""
	return {
		"type": "request", "request_type": RENAME,
		"payload": {"world_id": world_id, "name": name},
	}


static func delete_request(world_id: String) -> Dictionary:
	"""删除请求。"""
	return {
		"type": "request", "request_type": DELETE,
		"payload": {"world_id": world_id},
	}


static func export_request(world_id: String) -> Dictionary:
	"""复制请求。"""
	return {
		"type": "request", "request_type": EXPORT,
		"payload": {"world_id": world_id},
	}


# ── 响应解析 ──────────────────────────────────────────────

## 世界摘要字段默认值（清洗时兜底）
const _WORLD_DEFAULTS: Dictionary = {
	"world_id": "",
	"name": "未命名存档",
	"seed": 0,
	"birth_chunk": null,
	"created_at": 0.0,
	"last_played_at": 0.0,
	"play_duration_sec": 0.0,
	"game_time": 0,
	"format_version": 1,
	"snapshot_count": 0,
	"latest_snapshot_at": null,
}


static func parse_worlds(payload: Dictionary) -> Array:
	"""解析 save_list 响应的 worlds 数组为规范化摘要列表。

	字段缺失时以默认值兜底（弱后端容错），保证 UI 层可直接读取。
	"""
	var raw: Array = payload.get("worlds", [])
	var result: Array = []
	for item in raw:
		if not item is Dictionary:
			continue
		var w: Dictionary = _WORLD_DEFAULTS.duplicate(true)
		for key in w.keys():
			if item.has(key):
				w[key] = item[key]
		w["world_id"] = str(w["world_id"])
		w["name"] = str(w["name"])
		w["game_time"] = int(w["game_time"])
		w["play_duration_sec"] = float(w["play_duration_sec"])
		w["snapshot_count"] = int(w["snapshot_count"])
		result.append(w)
	return result


static func parse_snapshots(payload: Dictionary) -> Array:
	"""解析 save_list 响应的 snapshots 数组。"""
	var raw: Array = payload.get("snapshots", [])
	var result: Array = []
	for item in raw:
		if not item is Dictionary:
			continue
		result.append(item.duplicate(true))
	return result


static func parse_error(message: Dictionary) -> String:
	"""从 error 消息提取可读错误文本。"""
	return str(message.get("error", "未知错误"))
