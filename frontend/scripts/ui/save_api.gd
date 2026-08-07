"""存档协议封装 — 请求构造与响应解析（纯逻辑，UI 负责收发）。

设计原则（Issue #13）：存档是状态通道（request-response）——
世界外的元操作，不产生历史、不进因果图。

本类为纯逻辑 RefCounted：不依赖 Connection 单例，
请求由 UI 层发送，响应由 UI 层转发给解析函数。

协议（docs/世界框架/存档系统/技术.md）:
    save_list   → {worlds: [...], snapshots: [...], current_world_id}
    save_create {name, seed?} → {world_id}
    save_snapshot {world_id} → {file}
    save_snapshot_delete {world_id, snapshot, recursive} → {deleted: [file]}
    save_rename {world_id, name} → {world_id, name}
    save_delete {world_id} → {}
    save_export {world_id} → {world_id}

进入世界 / 回滚不再走 save_load 请求：由 Connection.restart_backend
以 --world-id/--snapshot 参数拉起世界进程完成（进程模型重构）。

快照条目附带血缘字段（时间线分叉视图数据源）:
	parent    创建时活目录来源（回滚目标快照 file，"" = 世界初始）
    game_time 创建时刻的世界时间（tick）
世界摘要附带 live_origin（当前活目录来源）；save_list 顶层
current_world_id = 引擎当前加载的世界（"最后进入"标注）。
"""

class_name SaveApi
extends RefCounted


# ── 请求类型 ──────────────────────────────────────────────

const LIST: String = "save_list"
const CREATE: String = "save_create"
const SNAPSHOT: String = "save_snapshot"
const SNAPSHOT_DELETE: String = "save_snapshot_delete"
const RENAME: String = "save_rename"
const DELETE: String = "save_delete"
const EXPORT: String = "save_export"
const MAP_PREVIEW: String = "map_preview"


# ── 请求构造 ──────────────────────────────────────────────

static func list_request() -> Dictionary:
	"""存档列表请求。"""
	return {"type": "request", "request_type": LIST, "payload": {}}


static func create_request(
	save_name: String, world_seed: int = 0, gen_params: Dictionary = {},
) -> Dictionary:
	"""新建存档位请求（seed=0 后端随机）。

	gen_params 为创建世界流程的调参产出（Issue #8）：目前含
	land_ratio（目标陆地比例），随档定案写入 manifest。
	"""
	return {
		"type": "request", "request_type": CREATE,
		"payload": {
			"name": save_name, "seed": world_seed,
			"gen_params": gen_params,
		},
	}


static func preview_request(
	world_seed: int, land_ratio: float,
	width_km: float = 100.0, height_km: float = 60.0) -> Dictionary:
	"""地图地形预览请求（创建世界调参，Issue #8）。

	采样分辨率固定 1000m：网格随尺寸缩放，地形变化率一致，
	尺寸只影响生成范围。缺省 100×60。
	"""
	return {
		"type": "request", "request_type": MAP_PREVIEW,
		"payload": {
			"seed": world_seed, "land_ratio": land_ratio,
			"width_km": width_km, "height_km": height_km,
		},
	}


static func snapshot_request(world_id: String) -> Dictionary:
	"""手动快照请求。"""
	return {
		"type": "request", "request_type": SNAPSHOT,
		"payload": {"world_id": world_id},
	}


static func snapshot_delete_request(world_id: String, snapshot: String, recursive: bool = false) -> Dictionary:
	"""删除快照请求（单点删除或分支裁剪，Issue #32）。

	recursive=false 单点删除（后代重接到被删节点的父）；
	recursive=true 分支裁剪（节点 + 全部后代一并删除）。
	"""
	return {
		"type": "request", "request_type": SNAPSHOT_DELETE,
		"payload": {"world_id": world_id, "snapshot": snapshot, "recursive": recursive},
	}


static func parse_deleted(payload: Dictionary) -> Array:
	"""解析 save_snapshot_delete 响应的 deleted 列表（已删快照文件名）。"""
	var raw: Array = payload.get("deleted", [])
	var result: Array = []
	for item in raw:
		if item is String and not item.is_empty():
			result.append(item)
	return result


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
	"snapshot_count": 0,
	"latest_snapshot_at": null,
	"live_origin": "",
}


static func parse_worlds(payload: Dictionary) -> Array:
	"""解析 save_list 响应的 worlds 数组为规范化摘要列表。

	字段缺失或为 null 时以默认值兜底（弱后端容错），保证 UI 层可直接读取。
	"""
	var raw: Array = payload.get("worlds", [])
	var result: Array = []
	for item in raw:
		if not item is Dictionary:
			continue
		var w: Dictionary = _WORLD_DEFAULTS.duplicate(true)
		for key in w.keys():
			var v: Variant = item.get(key)
			if v != null:
				w[key] = v
		w["world_id"] = str(w["world_id"])
		w["name"] = str(w["name"])
		w["game_time"] = int(w["game_time"])
		w["play_duration_sec"] = float(w["play_duration_sec"])
		w["snapshot_count"] = int(w["snapshot_count"])
		w["live_origin"] = str(w["live_origin"])
		result.append(w)
	return result


static func parse_current_world_id(payload: Dictionary) -> String:
	"""解析 save_list 顶层的当前加载世界（"最后进入"标注数据源）。"""
	return str(payload.get("current_world_id", ""))


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
