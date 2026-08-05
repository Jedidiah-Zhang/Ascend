"""主世界 3D 场景 — 正交等轴视角 + 流式 chunk 地形。
"""
extends Node3D

const Config = preload("res://scripts/config.gd")

# ── 相机常量 ──────────────────────────────────────────────

const CAMERA_FOV: float = Config.CAMERA_3D_FOV
const CAMERA_DISTANCE_DEFAULT: float = Config.CAMERA_3D_DISTANCE_DEFAULT
const CAMERA_ZOOM_DISTANCE_STEP: float = Config.CAMERA_3D_DISTANCE_STEP
const CAMERA_DISTANCE_MIN: float = Config.CAMERA_3D_DISTANCE_MIN
const CAMERA_DISTANCE_MAX: float = Config.CAMERA_3D_DISTANCE_MAX
const PLAYER_SPEED: float = Config.PLAYER_3D_SPEED
const PLAYER_FAST_MULT: float = Config.PLAYER_3D_FAST_MULT

# ── 流式 chunk 常量 ───────────────────────────────────────

const CHUNK_SIZE: int = Config.TILE_MAP_SIZE
const STREAM_MARGIN: int = 1
const UNLOAD_MARGIN: int = 1
const MAX_PENDING: int = 3

## 玩家移动上报节流间隔（秒）
const MOVE_REPORT_INTERVAL: float = 0.2

# ── 阴影常量 ──────────────────────────────────────────────

## 阴影覆盖范围 = 可视半径 × 该余量（max_distance 是半径语义，阴影相机覆盖可视区 + 边缘外遮挡物余量）
const SHADOW_COVERAGE_MARGIN: float = 1.35
## 太阳高度角低于该值时关闭阴影
const SHADOW_CUTOFF: float = 0.1
## 低角度区间上限：低于该值开始放大覆盖范围、压扁 pancake
const SHADOW_LOW_ANGLE_CEIL: float = 0.25
## 低角度时覆盖范围的最大放大倍率（低角度阴影被拉长）
const SHADOW_LOW_ANGLE_EXPAND: float = 3.0
## 低角度时 pancake 尺寸（压缩阴影相机深度视锥）
const SHADOW_LOW_ANGLE_PANCAKE: float = 80.0
const SHADOW_BASE_PANCAKE: float = 20.0
const SHADOW_BIAS_BASE: float = 0.07
const SHADOW_NORMAL_BIAS: float = 0.2
## 相机近/远平面紧贴地形 slab 时的余量：最高物体高度 + 安全边距
## （正交投影下阴影范围 = 相机视锥，slab 越薄阴影精度越高）
const SHADOW_TALL_ALLOWANCE: float = 60.0
const SHADOW_SLAB_MARGIN: float = 20.0

# ── 光照调参常量（日出日落平滑曲线共用） ────────────────────

## 太阳高度角渐入上界：0→0.35 间平滑过渡，消除亮度跳变
const SUN_RAMP_CEIL: float = 0.35
## 阴影透明度渐变带宽（围绕 SHADOW_CUTOFF）
const SHADOW_FADE_BAND: float = 0.05
## 低角度时 shadow_bias 放大保护的分母下限（防除零/过小偏置）
const SHADOW_BIAS_MIN_ALT: float = 0.1
## 直射光强度倍率（后端日照 0~1 → 场景光强）
const SUN_ENERGY_SCALE: float = 1.2
## 天气调制中环境光的基量/天气占比（env_t = sun_ramp × (BASE + WEATHER×intensity)）
const ENV_BASE_WEIGHT: float = 0.4
const ENV_WEATHER_WEIGHT: float = 0.6

## 日间环境光/背景色（_configure_environment 与 _update_lighting 共用）
const DAY_AMBIENT: Color = Color(0.55, 0.55, 0.6, 1.0)
const NIGHT_AMBIENT: Color = Color(0.14, 0.15, 0.32, 1.0)
const DAY_BG: Color = Color(0.15, 0.15, 0.5, 1.0)
const NIGHT_BG: Color = Color(0.02, 0.02, 0.08, 1.0)

## 与 terrain_mesh_builder.gd 的 TERRAIN_TO_MESH 对齐的材质表
## （item_id → 纹理路径）；死纹理（top_shallow_water/top_snow）已移除
const TERRAIN_TEXTURES: Dictionary = {
	2: "res://assets/terrain/textures/top_sand.png",
	3: "res://assets/terrain/textures/top_plains.png",
	4: "res://assets/terrain/textures/top_hills.png",
	5: "res://assets/terrain/textures/top_rock.png",
	6: "res://assets/terrain/textures/top_mountain.png",
	8: "res://assets/terrain/textures/top_fertile.png",
	9: "res://assets/terrain/textures/top_underwater_floor.png",
}


static func _world_to_chunk(wx: float, wz: float) -> Vector2i:
	"""世界坐标 → chunk 坐标（全文件单一换算实现）。

	注意：前端 3D 世界 Z 轴对应后端 2D 坐标的 Y 轴。
	"""
	return Vector2i(floori(wx / float(CHUNK_SIZE)), floori(wz / float(CHUNK_SIZE)))


static func _tile_index(tx: int, tz: int) -> int:
	"""chunk 内 tile 行优先索引（与后端 tile 数组布局一致）。"""
	return tz * CHUNK_SIZE + tx

## 终端节点
@onready var _terminal: TerminalWidget = $TerminalLayer/TerminalWidget
## 3D 伪正交相机（极小 FOV 近似正交）
@onready var _camera: Camera3D = $World/Camera3D
## 调试信息覆盖层
@onready var _debug_overlay: DebugOverlay = $DebugLayer/DebugOverlay
## 事件日志面板
@onready var _event_log: EventLog = $DebugLayer/EventLog
## ESC 暂停菜单
@onready var _pause_menu: PauseMenu = $PauseLayer/PauseMenu
## WorldEnvironment 节点
@onready var _world_env: WorldEnvironment = $World/WorldEnvironment
## 方向光（太阳）
@onready var _sun_light: DirectionalLight3D = $World/SunLight

## 相机焦点（世界空间中的观察目标点）
var _camera_focus: Vector3 = Vector3(0, 0, 0)
## 当前相机距离
var _camera_distance: float = CAMERA_DISTANCE_DEFAULT
## 地形 chunk 容器
var _terrain_parent: Node3D
## 是否已对齐相机到地形表面
var _camera_grounded: bool = false

## chunk 状态追踪: {Vector2i(cx, cy): chunk_data_dict}
var _chunks: Dictionary = {}
## 正在请求中的 chunk: {Vector2i(cx, cy): true}
var _pending: Dictionary = {}
## 正在请求字段数据（不含 tile）的 chunk: {Vector2i(cx, cy): true}
## 与 _pending 分开记账：字段响应到达时不清 tile 请求的标记
var _batch_pending: Dictionary = {}
## 已渲染的 chunk: {Vector2i(cx, cy): true}
var _loaded: Dictionary = {}
## 待请求 tile 数据的队列（Dict 做 O(1) 去重）
var _tile_queue: Dictionary = {}
## 缓存从 MeshLibrary 提取的材质: item_id → Material
var _terrain_materials: Dictionary = {}

## 性能计时（微秒）
var _stream_us: int = 0

## 玩家实体占位（世界就绪后才创建，见 _ensure_player）
var _player: Node3D
## 玩家世界位置（XZ 平面移动，Y 由地形决定）
var _player_pos: Vector3 = Vector3.ZERO
## 出生 chunk（后端权威）
var _birth_chunk: Vector2i = Vector2i.ZERO
var _has_birth: bool = false
## 本地控制的玩家实体 ID（player_state 提供，后端权威）
var _player_entity_id: String = ""
## 当前存档位 ID（world_initialized 事件提供；手动存档用，空 = 未就绪）
var _world_id: String = ""
## 待计算节点编号的快照文件（save_snapshot 响应后经 save_list 回查）
var _save_file: String = ""
## 移动上报计时器（节流）
var _move_report_timer: float = 0.0

## 世界生成中提示（地形就绪前显示，_world_visible 后隐藏）
var _loading_label: Label

## 出生点附近地形是否已加载完成（就绪后才显示玩家/隐藏加载提示）
var _world_visible: bool = false
## 地形就绪等待计时（超时强制显示，防后端异常时玩家永久卡住）
var _terrain_ready_timer: float = 0.0
## 就绪判定半径：出生 chunk 周围 radius×radius 圈全部加载视为就绪
const TERRAIN_READY_RADIUS: int = 1
## 地形就绪等待超时（秒）
const TERRAIN_READY_TIMEOUT: float = 8.0

## 当前游戏时间
var _game_hour: float = 6.0
var _game_minute: int = 0
## 日出日落时间（从后端天气查询获取）
var _sunrise: float = 6.0
var _sunset: float = 18.0
## 太阳方位角（0-360，从后端种子派生）
var _sun_azimuth: float = 45.0
## 日照强度（0-1，来自后端）
var _sunshine_intensity: float = 0.5
## 最近一次计算的太阳高度角（供阴影覆盖计算缓存）
var _last_sun_altitude: float = 0.5
## 天气轮询计时器
var _weather_query_timer: float = 0.0
const WEATHER_QUERY_INTERVAL: float = 1.0


func _ready() -> void:
	_terminal.remote_command_submitted.connect(_on_terminal_command)

	Connection.connection_established.connect(_on_connected)
	Connection.connection_lost.connect(_on_disconnected)
	Connection.message_received.connect(_on_message)

	if _pause_menu:
		_pause_menu.save_requested.connect(_on_pause_save_requested)
		_pause_menu.set_terminal(_terminal)

	_terrain_parent = $World/Terrain/ChunkPool

	# 玩家节点不在 _ready 创建：须等后端世界就绪（出生点到达）后才创建，
	# 与后端语义一致（服务模式/读档重建期间不存在世界与玩家）
	_create_loading_label()

	_setup_debug_overlay()
	_configure_camera()
	_configure_environment()


func _exit_tree() -> void:
	if Connection.connection_established.is_connected(_on_connected):
		Connection.connection_established.disconnect(_on_connected)
	if Connection.connection_lost.is_connected(_on_disconnected):
		Connection.connection_lost.disconnect(_on_disconnected)
	if Connection.message_received.is_connected(_on_message):
		Connection.message_received.disconnect(_on_message)
	if _pause_menu and _pause_menu.save_requested.is_connected(_on_pause_save_requested):
		_pause_menu.save_requested.disconnect(_on_pause_save_requested)


func _configure_camera() -> void:
	if _camera == null:
		push_error("MainWorld3D: Camera3D not found!")
		return
	# 真正交投影：size 控制可视范围，near/far 紧贴地形 slab。
	# 正交相机下阴影范围 = 相机视锥 → 阴影精度全图一致，缩放自然控制精度。
	_camera.projection = Camera3D.PROJECTION_ORTHOGONAL
	_camera_distance = CAMERA_DISTANCE_DEFAULT
	_camera_focus = _player_pos
	_apply_camera_transform()

func _configure_environment() -> void:
	if _world_env == null:
		push_error("MainWorld3D: WorldEnvironment not found!")
		return

	var env := _world_env.environment
	if env == null:
		env = Environment.new()
		_world_env.environment = env

	env.background_mode = Environment.BG_COLOR
	env.background_color = DAY_BG
	env.ambient_light_source = Environment.AMBIENT_SOURCE_COLOR
	env.ambient_light_color = DAY_AMBIENT
	env.ambient_light_energy = 1.0
	env.tonemap_mode = Environment.TONE_MAPPER_LINEAR

	# ── 阴影配置 ──
	if _sun_light:
		_sun_light.shadow_enabled = true
		_sun_light.directional_shadow_mode = DirectionalLight3D.SHADOW_ORTHOGONAL
		_sun_light.shadow_bias = SHADOW_BIAS_BASE
		_sun_light.shadow_normal_bias = SHADOW_NORMAL_BIAS
		_sun_light.shadow_blur = 0.4
		_sun_light.directional_shadow_pancake_size = SHADOW_BASE_PANCAKE
		_sun_light.directional_shadow_fade_start = 0.85
		_sun_light.directional_shadow_max_distance = _compute_shadow_coverage(0.5)

	print("MainWorld3D: Environment configured — ambient=%.1f, bg=%s" % [env.ambient_light_energy, env.background_color])


func _ensure_player() -> void:
	"""玩家节点惰性创建：仅在世界就绪（出生点已知）后调用，幂等。"""
	if _player != null:
		return
	_create_player()


func _create_player() -> void:
	var mesh := BoxMesh.new()
	mesh.size = Vector3(0.8, 1.8, 0.8)

	var player_body := MeshInstance3D.new()
	player_body.name = "PlayerBody"
	player_body.mesh = mesh
	player_body.position = Vector3(0, 0.9, 0)

	var mat := StandardMaterial3D.new()
	mat.albedo_color = Color(0.9, 0.3, 0.3, 1)
	player_body.material_override = mat

	_player = Node3D.new()
	_player.name = "Player"
	_player.add_child(player_body)
	_player.visible = false  # 等出生点和地形就绪后再显示
	$World.add_child(_player)
	# 注：不复位 _player_pos——惰性创建可能发生在权威位置已写入之后
	# （回归：_apply_authoritative_position → _ensure_player 时位置被清零）
	_player.position = _player_pos
	print("MainWorld3D: player created")


func _create_loading_label() -> void:
	"""世界生成中的居中提示（出生点到达后隐藏）。"""
	var layer := CanvasLayer.new()
	layer.name = "LoadingLayer"
	layer.layer = 50
	var label := Label.new()
	label.name = "WorldLoadingLabel"
	label.text = "正在生成世界..."
	label.add_theme_font_override("font", FontUtils.get_mono_font())
	label.add_theme_font_size_override("font_size", 18)
	label.add_theme_color_override("font_color", Color(0.9, 0.9, 0.95))
	label.horizontal_alignment = HORIZONTAL_ALIGNMENT_CENTER
	label.vertical_alignment = VERTICAL_ALIGNMENT_CENTER
	label.set_anchors_and_offsets_preset(Control.PRESET_FULL_RECT)
	layer.add_child(label)
	add_child(layer)
	_loading_label = label


func _get_ground_elevation_at(pos: Vector3) -> float:
	var chunk_pos := _world_to_chunk(pos.x, pos.z)
	var key := chunk_pos
	var chunk = _chunks.get(key)
	if not (chunk is Dictionary):
		return NAN
	var elev: Array = chunk.get("elevation", [])
	if elev.size() < CHUNK_SIZE * CHUNK_SIZE:
		return NAN
	var tx: int = int(pos.x) - chunk_pos.x * CHUNK_SIZE
	var tz: int = int(pos.z) - chunk_pos.y * CHUNK_SIZE
	if tx < 0 or tx >= CHUNK_SIZE or tz < 0 or tz >= CHUNK_SIZE:
		return NAN
	return float(elev[_tile_index(tx, tz)])


func _set_birth_chunk(cx: int, cy: int) -> void:
	if _has_birth:
		return
	_has_birth = true
	_birth_chunk = Vector2i(cx, cy)
	# 与后端权威出生点约定一致：出生 chunk 原点（PlayerService.birth_position）
	_player_pos.x = float(cx * CHUNK_SIZE)
	_player_pos.z = float(cy * CHUNK_SIZE)
	if _player:
		_player.position = _player_pos
	_camera_focus = _player_pos
	_apply_camera_transform()
	# 地形就绪前保持加载提示（玩家节点在 _check_terrain_ready 就绪后创建）
	if _loading_label:
		_loading_label.text = "正在加载地形..."
		_loading_label.visible = true
	print("MainWorld3D: birth chunk (%d,%d), player at (%.0f, %.0f)" % [cx, cy, _player_pos.x, _player_pos.z])


func _check_terrain_ready(force: bool = false) -> void:
	"""出生点周围地形加载完成后切换为可见世界。

	判定：出生 chunk 的 TERRAIN_READY_RADIUS 邻域全部加载（_loaded）；
	force=true（超时兜底）跳过判定直接就绪，防后端异常时玩家永久卡住。
	"""
	if _world_visible or not _has_birth:
		return
	if not force:
		for dx in range(-TERRAIN_READY_RADIUS, TERRAIN_READY_RADIUS + 1):
			for dy in range(-TERRAIN_READY_RADIUS, TERRAIN_READY_RADIUS + 1):
				if not _loaded.has(_birth_chunk + Vector2i(dx, dy)):
					return
	_world_visible = true
	if _loading_label:
		_loading_label.visible = false
	_ensure_player()
	_player.position = _player_pos
	_player.visible = true
	_update_player_ground()
	_camera_focus = _player_pos
	_apply_camera_transform()
	print("MainWorld3D: 出生点地形就绪，世界可见")


# ── 世界就绪事件（服务器与世界观解耦后的就绪信号） ────────

func _on_world_reloading(_data: Dictionary) -> void:
	"""后端开始读档重建（连接保持，旧世界数据即将失效）：复位并提示。"""
	_world_id = str(_data.get("world_id", ""))
	_reset_world_state()
	if _loading_label:
		_loading_label.text = "正在生成世界..."
		_loading_label.visible = true


func _on_world_reloading_failed(_data: Dictionary) -> void:
	"""读档重建失败（后端已自动恢复/转入服务模式）：结束加载提示。"""
	_reset_world_state()
	if _loading_label:
		_loading_label.text = "读档失败"
		_loading_label.visible = false


## 世界生成阶段 → 加载提示文案（与后端 ContinentGenerator.STAGE_* 对齐）
const WORLD_STAGE_LABELS: Dictionary = {
	"elevation": "正在生成地形...",
	"climate": "正在生成气候...",
	"erosion": "正在侵蚀塑形...",
	"water": "正在汇聚湖泊河流...",
	"width": "正在雕刻河道...",
	"chunks": "正在准备出生区域...",
	"done": "正在进入世界...",
}


func _on_world_progress(data: Dictionary) -> void:
	"""世界生成阶段进度（大陆生成 5-30s 期间逐阶段更新提示）。"""
	if not _has_birth and _loading_label:
		var stage: String = str(data.get("stage", ""))
		_loading_label.text = str(WORLD_STAGE_LABELS.get(stage, "正在生成世界..."))


func _on_world_initialized(data: Dictionary) -> void:
	"""新世界就绪（后端每完成一次读档/新建发布）：接入并拉取权威状态。

	连接全程不断线：必须主动重新请求玩家实体/状态
	（entity_snapshot / player_state 原由 _on_connected 触发）。
	"""
	_reset_world_state()
	_world_id = str(data.get("world_id", _world_id))
	var bc: Array = data.get("birth_chunk", [])
	if bc.size() >= 2:
		_set_birth_chunk(int(bc[0]), int(bc[1]))
	Connection.send({
		"type": "request",
		"request_type": "entity_snapshot",
		"payload": {},
	})
	Connection.send({
		"type": "request",
		"request_type": "player_state",
		"payload": {},
	})


func _reset_world_state() -> void:
	"""清空旧世界的 chunk 缓存与地形节点（世界重建后旧数据失效）。"""
	_has_birth = false
	_world_visible = false
	_terrain_ready_timer = 0.0
	_birth_chunk = Vector2i.ZERO
	_chunks.clear()
	_pending.clear()
	_batch_pending.clear()
	_tile_queue.clear()
	_loaded.clear()
	if _player:
		_player.visible = false
	if _terrain_parent:
		for child in _terrain_parent.get_children():
			child.queue_free()


# ── 调试数据 getter（供 DebugSection 自行拉取）────────────

func get_debug_camera_info() -> Dictionary:
	if _camera == null:
		return {}
	return {
		"position": Vector2(_camera.position.x, _camera.position.z),
		"camera_display": "距离: %.0f m" % _camera_distance,
	}


func get_debug_player_info() -> Dictionary:
	return {
		"world_pos": Vector2(_player_pos.x, _player_pos.z),
		"chunk": _world_to_chunk(_player_pos.x, _player_pos.z),
		"elevation": _player_pos.y - 1.0,
	}


func get_debug_terrain_at(world_pos: Vector2) -> Dictionary:
	var key := _world_to_chunk(world_pos.x, world_pos.y)
	var chunk = _chunks.get(key)
	if chunk == null or not (chunk is Dictionary):
		return {}
	var elev: Array = chunk.get("elevation", [])
	var slope: Array = chunk.get("slope", [])
	if elev.size() < CHUNK_SIZE * CHUNK_SIZE:
		return {}
	var tx: int = int(world_pos.x) - key.x * CHUNK_SIZE
	var tz: int = int(world_pos.y) - key.y * CHUNK_SIZE
	if tx < 0 or tx >= CHUNK_SIZE or tz < 0 or tz >= CHUNK_SIZE:
		return {}
	var idx: int = _tile_index(tx, tz)
	var result: Dictionary = {}
	if idx < elev.size():
		result["elevation"] = int(elev[idx])
	if idx < slope.size():
		result["slope"] = float(slope[idx])
	return result


func get_debug_climate_at(world_pos: Vector2) -> Dictionary:
	var key := _world_to_chunk(world_pos.x, world_pos.y)
	var chunk = _chunks.get(key)
	if chunk == null or not (chunk is Dictionary):
		return {}
	var result: Dictionary = {}
	if chunk.has("temperature"):
		result["temperature"] = float(chunk["temperature"])
	if chunk.has("humidity"):
		result["humidity"] = float(chunk["humidity"])
	if chunk.has("climate"):
		result["climate_zone"] = int(chunk["climate"])
	return result


func get_debug_chunk_stats() -> Dictionary:
	var cached: int = 0
	for key in _chunks:
		if _chunks[key] is Dictionary and not _loaded.has(key):
			cached += 1
	return {
		"loaded": _loaded.size(),
		"cached": cached,
		"pending": _pending.size(),
	}


func get_debug_timing() -> Dictionary:
	return {
		"stream": _stream_us,
		"conn": Connection.last_process_us,
	}


func _update_player_ground() -> void:
	var ground_y := _get_ground_elevation_at(_player_pos)
	if not is_nan(ground_y):
		_player_pos.y = maxf(ground_y, 0.0) + 1.0
		_ensure_player()
		_player.position = _player_pos


func _build_terrain_chunk(cx: int, cy: int, terrain: Array, elevation: Array) -> void:
	const CS: int = CHUNK_SIZE
	var key := Vector2i(cx, cy)
	if _loaded.has(key) or _terrain_parent.has_node(NodePath("Chunk_%d_%d" % [cx, cy])):
		return

	var materials := _lazy_load_materials()
	if materials.is_empty():
		return

	var mesh: ArrayMesh = TerrainMeshBuilder.build(terrain, elevation, materials)

	if mesh.get_surface_count() == 0:
		_loaded[key] = true
		_check_terrain_ready()
		return

	var mi := MeshInstance3D.new()
	mi.name = "Chunk_%d_%d" % [cx, cy]
	mi.mesh = mesh
	mi.position = Vector3(float(cx * CS), 0.0, float(cy * CS))

	_terrain_parent.add_child(mi)
	_loaded[key] = true
	print("MainWorld3D: chunk (%d,%d) — %d surfaces" % [cx, cy, mesh.get_surface_count()])

	# 首次 chunk 覆盖玩家时，记录地面高度（玩家节点统一由
	# _check_terrain_ready 在出生点地形就绪后创建显示）
	if not _camera_grounded:
		if _world_to_chunk(_player_pos.x, _player_pos.z) == Vector2i(cx, cy):
			_camera_grounded = true
			var ground_y := _get_ground_elevation_at(_player_pos)
			if not is_nan(ground_y):
				_player_pos.y = maxf(ground_y, 0.0) + 1.0
				print("MainWorld3D: player ground y=%.1f" % _player_pos.y)

	_check_terrain_ready()


func _lazy_load_materials() -> Dictionary:
	if _terrain_materials.is_empty():
		for item_id in TERRAIN_TEXTURES:
			var tex_path: String = TERRAIN_TEXTURES[item_id]
			if tex_path.is_empty():
				continue
			var tex: Texture2D = load(tex_path)
			if tex == null:
				push_error("MainWorld3D: failed to load texture: %s" % tex_path)
				continue
			var mat := StandardMaterial3D.new()
			mat.albedo_texture = tex
			mat.texture_filter = BaseMaterial3D.TEXTURE_FILTER_NEAREST
			# 顶点色 AO（悬崖接触阴影）依赖此开关，默认 false 会直接忽略顶点色
			mat.vertex_color_use_as_albedo = true
			_terrain_materials[item_id] = mat
	return _terrain_materials


func _process(delta: float) -> void:
	_process_camera(delta)

	if Connection.status != Connection.Status.CONNECTED:
		if _debug_overlay and _debug_overlay.is_shown():
			_debug_overlay.process_sections(delta)
		return

	if _terminal and _terminal.is_open():
		if _debug_overlay and _debug_overlay.is_shown():
			_debug_overlay.process_sections(delta)
		return

	# 世界未就绪（读档重建中）：不流式加载/创建玩家，等待 world_initialized
	if not _has_birth:
		if _debug_overlay and _debug_overlay.is_shown():
			_debug_overlay.process_sections(delta)
		return

	if _event_log:
		_event_log.set_player_chunk(_world_to_chunk(_player_pos.x, _player_pos.z))

	# 出生点地形就绪超时兜底：后端异常导致区块永不就绪时强制显示
	if _has_birth and not _world_visible:
		_terrain_ready_timer += delta
		if _terrain_ready_timer >= TERRAIN_READY_TIMEOUT:
			_check_terrain_ready(true)

	_stream_chunks()
	_process_input(delta)

	_weather_query_timer += delta
	if _weather_query_timer >= WEATHER_QUERY_INTERVAL:
		_weather_query_timer = 0.0
		_query_weather()
	_update_lighting()

	if _debug_overlay and _debug_overlay.is_shown():
		_debug_overlay.process_sections(delta)


func _process_camera(_delta: float) -> void:
	if _camera == null:
		return

	var zoom_delta: float = 0.0
	if Input.is_action_just_pressed("zoom_in"):
		zoom_delta = -CAMERA_ZOOM_DISTANCE_STEP
	elif Input.is_action_just_pressed("zoom_out"):
		zoom_delta = CAMERA_ZOOM_DISTANCE_STEP

	if zoom_delta != 0.0:
		_camera_distance = clampf(
			_camera_distance + zoom_delta,
			CAMERA_DISTANCE_MIN,
			CAMERA_DISTANCE_MAX)
		_apply_camera_transform()


func _apply_camera_transform() -> void:
	var dir := Vector3(1, 1, 1).normalized()
	_camera.position = _camera_focus + dir * _camera_distance
	_camera.look_at(_camera_focus, Vector3.UP)

	# 正交投影 size = 距离 × tan(FOV/2)，保持原缩放手感；
	# near/far 紧贴可视地形 slab（含最高物体余量），阴影范围随之精确覆盖屏幕。
	var half_perp: float = _camera_distance * tan(deg_to_rad(CAMERA_FOV * 0.5))
	_camera.size = half_perp
	var elevation: float = asin(1.0 / sqrt(3.0))
	var ground_half: float = half_perp / sin(elevation)
	_camera.near = maxf(
		_camera_distance - ground_half - SHADOW_TALL_ALLOWANCE - SHADOW_SLAB_MARGIN, 1.0)
	_camera.far = _camera_distance + ground_half + SHADOW_SLAB_MARGIN

	if _sun_light:
		_sun_light.directional_shadow_max_distance = _compute_shadow_coverage(_last_sun_altitude)
		_sun_light.position = _camera_focus + Vector3(0, 200, 0)


func _process_input(delta: float) -> void:
	var move_input := Input.get_vector("move_left", "move_right", "move_up", "move_down")
	if move_input != Vector2.ZERO:
		var forward: Vector3 = -_camera.global_transform.basis.z
		var right: Vector3 = _camera.global_transform.basis.x
		forward.y = 0.0
		right.y = 0.0
		if forward.length_squared() > 0.0:
			forward = forward.normalized()
		if right.length_squared() > 0.0:
			right = right.normalized()

		var speed := PLAYER_SPEED
		if Input.is_key_pressed(KEY_SHIFT):
			speed *= PLAYER_FAST_MULT

		_player_pos.x += (forward * -move_input.y + right * move_input.x).x * speed * delta
		_player_pos.z += (forward * -move_input.y + right * move_input.x).z * speed * delta
		_update_player_ground()
		_camera_focus = _player_pos
		_apply_camera_transform()

		# 节流上报权威位置（后端裁决并回传，越界等非法位置由后端钳制）
		_move_report_timer += delta
		if _move_report_timer >= MOVE_REPORT_INTERVAL:
			_move_report_timer = 0.0
			_send_player_move()

	if Input.is_action_just_pressed("interact"):
		Connection.send({
			"type": "request",
			"request_type": "player_interact",
			"payload": {}
		})


func _on_pause_save_requested() -> void:
	"""暂停菜单「手动存档」：世界未就绪时拒绝，否则发快照请求。

	请求是异步的（save_snapshot 为状态通道），结果在
	_handle_response / _handle_error 中回填到暂停菜单。
	"""
	if _world_id.is_empty():
		if _pause_menu:
			_pause_menu.show_status("当前世界未就绪，无法手动存档", true)
		return
	Connection.send(SaveApi.snapshot_request(_world_id))


func _on_terminal_command(command: String) -> void:
	Connection.send({
		"type": "request",
		"request_type": "terminal_cmd",
		"payload": {"command": command},
	})


func _send_player_move() -> void:
	if not _has_birth:
		return
	Connection.send({
		"type": "request",
		"request_type": "player_move",
		"payload": {"x": _player_pos.x, "y": _player_pos.z},
	})


# ── 调试覆盖层 ──────────────────────────────────────────────

func _setup_debug_overlay() -> void:
	_debug_overlay.setup_default_sections(self)


# ── Connection 信号处理 ───────────────────────────────────

func _on_connected(host: String, port: int) -> void:
	print("MainWorld3D: connected to %s:%d" % [host, port])
	Connection.send({
		"type": "request",
		"request_type": "entity_snapshot",
		"payload": {},
	})
	Connection.send({
		"type": "request",
		"request_type": "player_state",
		"payload": {},
	})


func _on_disconnected() -> void:
	print("MainWorld3D: disconnected")
	# 服务器与世界观解耦后，读档重建不再断连——此处仅处理真实断线：
	# 在途请求的响应永远不会到达：清空请求状态，重连后 _stream_chunks
	# 会为所有未加载 chunk 重新入队请求（已加载的地形节点保留）
	_pending.clear()
	_batch_pending.clear()
	_tile_queue.clear()
	_save_file = ""


func _on_message(message: Dictionary) -> void:
	var msg_type: String = message.get("type", "")

	match msg_type:
		"event":
			_handle_event(message)
		"response":
			_handle_response(message)
		"error":
			_handle_error(message)
		_:
			push_warning("MainWorld3D: unknown message type: %s" % msg_type)


func _handle_event(message: Dictionary) -> void:
	var event_type: String = message.get("event_type", "")
	var payload: Dictionary = message.get("payload", {})
	var data: Dictionary = payload.get("data", {})

	# 世界重建信号：世界外元操作，不进事件日志
	if event_type == "world_reloading":
		_on_world_reloading(data)
		return
	if event_type == "world_reloading_failed":
		_on_world_reloading_failed(data)
		return
	if event_type == "world_progress":
		_on_world_progress(data)
		return
	if event_type == "world_initialized":
		_on_world_initialized(data)
		return

	if event_type == "minute_change":
		_game_hour = float(payload.get("game_hour", _game_hour))
		_game_minute = int(payload.get("game_minute", _game_minute))

	if event_type == "player_teleported":
		var tx: float = float(data.get("x", _player_pos.x))
		var tz: float = float(data.get("y", _player_pos.z))
		_player_pos.x = tx
		_player_pos.z = tz
		_update_player_ground()
		_camera_focus = _player_pos
		_apply_camera_transform()
		if _event_log:
			_event_log.push_event("[%s] 传送至 (%.0f, %.0f)" % [
				SaveInfoFormatter.hhmm_string(
					int(payload.get("game_hour", 0)),
					int(payload.get("game_minute", 0))),
				tx, tz])
		return

	if _debug_overlay:
		_debug_overlay.broadcast_event(event_type, payload)

	if _event_log:
		_event_log.on_world_event(event_type, payload)


func _handle_response(message: Dictionary) -> void:
	var request_type: String = message.get("request_type", "")
	var payload: Dictionary = message.get("payload", {})

	# 广播到所有调试分区（Section 按其关心的 request_type 自行过滤）
	if _debug_overlay:
		_debug_overlay.broadcast_response(request_type, payload)

	match request_type:
		"get_chunks":
			var chunks: Array = payload.get("chunks", [])
			# 请求参数回显：include_tiles=true = 完整版（含地形数组），
			# false = 字段版。不再用数组长度等形状启发式判型
			var has_tiles: bool = payload.get("include_tiles", false)
			for chunk in chunks:
				var cx: int = int(chunk.get("cx", 0))
				var cy: int = int(chunk.get("cy", 0))
				var key := Vector2i(cx, cy)
				_chunks[key] = chunk
				# 按响应类型分别清除标记：仅含字段的响应不动 tile 请求的
				# _pending 标记，否则会每帧重发 tile 请求直到其响应到达
				if has_tiles:
					_pending.erase(key)
				else:
					_batch_pending.erase(key)

				var player_chunk := _world_to_chunk(_player_pos.x, _player_pos.z)
				var unload_r: int = _stream_radius() + UNLOAD_MARGIN
				if abs(cx - player_chunk.x) > unload_r or abs(cy - player_chunk.y) > unload_r:
					_chunks.erase(key)
					continue

				var elev: Array = chunk.get("elevation", [])
				var terr: Array = chunk.get("terrain", [])
				if elev.size() == CHUNK_SIZE * CHUNK_SIZE and terr.size() == CHUNK_SIZE * CHUNK_SIZE:
					if not _loaded.has(key):
						_build_terrain_chunk(cx, cy, terr, elev)
		"get_weather":
			var weathers: Array = payload.get("weathers", [])
			if weathers.size() > 0:
				var w: Dictionary = weathers[0]
				if w.has("sunrise"):
					_sunrise = float(w["sunrise"])
				if w.has("sunset"):
					_sunset = float(w["sunset"])
				if w.has("sun_azimuth"):
					_sun_azimuth = float(w["sun_azimuth"])
				if w.has("sunshine_intensity"):
					_sunshine_intensity = float(w["sunshine_intensity"])
		"player_state":
			# 权威玩家实体 ID + 位置（吸附初始位置）
			_player_entity_id = str(payload.get("entity_id", _player_entity_id))
			_apply_authoritative_position(payload)
		"entity_snapshot":
			# 全量实体快照：仅消费本地控制的玩家实体（其余实体渲染后续接入）
			var entities: Array = payload.get("entities", [])
			for ent in entities:
				if ent.get("controller", "") != "PLAYER":
					continue
				var ent_id: String = str(ent.get("id", ""))
				if not _player_entity_id.is_empty() and ent_id != _player_entity_id:
					continue
				_player_entity_id = ent_id
				_apply_authoritative_position(ent)
		"player_move":
			# 权威裁决结果：后端可能钳制越界坐标，本地据此纠正
			_apply_authoritative_position(payload)
		"terminal_cmd":
			if _terminal:
				_terminal.write(payload.get("output", ""))
		"save_snapshot":
			# 回查 save_list 计算节点编号（与存档选择页编号一致），
			# 期间暂停菜单保持「正在保存...」
			_save_file = str(payload.get("file", ""))
			if not _save_file.is_empty():
				Connection.send(SaveApi.list_request())
		"save_list":
			_resolve_save_number(payload)
		_:
			pass


func _resolve_save_number(payload: Dictionary) -> void:
	"""save_snapshot 后的 save_list 回查：计算新快照的节点编号并回填菜单。"""
	if _save_file.is_empty():
		return
	var file: String = _save_file
	_save_file = ""
	var snaps: Array = []
	if not _world_id.is_empty():
		for s in payload.get("snapshots", []):
			if s is Dictionary and str(s.get("world_id", "")) == _world_id:
				var suffix: String = str(s.get("suffix", ""))
				if suffix == "manual" or suffix == "auto":
					snaps.append(s)
	var number: int = TimelineLayout.save_order_ids(snaps).find(file) + 1
	if _pause_menu:
		_pause_menu.show_save_complete(number)


func _apply_authoritative_position(payload: Dictionary) -> void:
	"""按后端权威位置吸附玩家（player_state / player_move 响应 / 快照）。"""
	var ax: float = float(payload.get("x", _player_pos.x))
	var az: float = float(payload.get("y", _player_pos.z))
	if ax == _player_pos.x and az == _player_pos.z:
		return
	_player_pos.x = ax
	_player_pos.z = az
	_update_player_ground()
	_camera_focus = _player_pos
	_apply_camera_transform()
	_ensure_player()
	# 地形就绪前不显示玩家（出生点加载完成后由 _check_terrain_ready 统一显示）
	_player.visible = _world_visible


## ── 流式 chunk 管理 ──────────────────────────────────────

func _stream_chunks() -> void:
	if Connection.status != Connection.Status.CONNECTED:
		return

	var t0: int = Time.get_ticks_usec()

	var center := _world_to_chunk(_player_pos.x, _player_pos.z)
	var center_cx: int = center.x
	var center_cy: int = center.y
	var stream_r := _stream_radius()

	_unload_distant_chunks(center_cx, center_cy, stream_r)

	var coords: Array[Array] = []
	for dx in range(-stream_r, stream_r + 1):
		for dy in range(-stream_r, stream_r + 1):
			var key := Vector2i(center_cx + dx, center_cy + dy)
			if not _chunks.has(key):
				_chunks[key] = null
				coords.append([key.x, key.y])
			elif not _loaded.has(key) and not _pending.has(key):
				_tile_queue[key] = true

	if not coords.is_empty():
		for c in coords:
			_batch_pending[Vector2i(c[0], c[1])] = true
		_send_chunk_request(coords, false)

	var pending_count: int = _pending.size()
	for key in _tile_queue.keys():
		if pending_count >= MAX_PENDING:
			break
		if _pending.has(key):
			continue
		_pending[key] = true
		_tile_queue.erase(key)
		_send_chunk_request([[key.x, key.y]], true)
		pending_count += 1

	_stream_us = Time.get_ticks_usec() - t0


func _stream_radius() -> int:
	var visible_radius: float = _compute_visible_radius() * 1.5
	var radius: int = ceili(visible_radius / float(CHUNK_SIZE))
	return maxi(STREAM_MARGIN, radius)


func _compute_visible_radius() -> float:
	"""相机在 (1,1,1) 方向、FOV 5° 下可视地面的对角线半径。"""
	var half_perp: float = _camera_distance * tan(deg_to_rad(CAMERA_FOV * 0.5))
	var elevation: float = asin(1.0 / sqrt(3.0))
	var ground_depth: float = half_perp / sin(elevation)
	return sqrt(half_perp * half_perp + ground_depth * ground_depth)


func _compute_shadow_coverage(sun_altitude: float) -> float:
	"""阴影覆盖半径 = 可视半径 × 余量（含边缘遮挡物投射余量）；低角度太阳时按比例放大。

	注意 directional_shadow_max_distance 是"距相机半径"语义：过大的余量会白白稀释
	8192 texel 阴影分辨率，因此正午仅保留 1.35 倍，低角度拉长阴影由 3 倍放大兜底。
	"""
	var coverage: float = _compute_visible_radius() * SHADOW_COVERAGE_MARGIN
	if sun_altitude < SHADOW_LOW_ANGLE_CEIL:
		var t: float = clampf(
			(sun_altitude - SHADOW_CUTOFF) / (SHADOW_LOW_ANGLE_CEIL - SHADOW_CUTOFF),
			0.0, 1.0)
		coverage *= lerpf(SHADOW_LOW_ANGLE_EXPAND, 1.0, t)
	return coverage


func _send_chunk_request(coords: Array[Array], include_tiles: bool) -> void:
	Connection.send({
		"type": "request",
		"request_type": "get_chunks",
		"payload": {
			"chunks": coords,
			"include_tiles": include_tiles,
			"force_fields": true,
		},
	})


func _unload_distant_chunks(center_cx: int, center_cy: int, stream_r: int) -> void:
	var unload_r := stream_r + UNLOAD_MARGIN
	for key in _loaded.keys():
		var cx: int = key.x
		var cy: int = key.y
		if abs(cx - center_cx) > unload_r or abs(cy - center_cy) > unload_r:
			var node_name := "Chunk_%d_%d" % [cx, cy]
			if _terrain_parent.has_node(NodePath(node_name)):
				_terrain_parent.get_node(NodePath(node_name)).queue_free()
			_loaded.erase(key)
			_chunks.erase(key)
			_pending.erase(key)
			_batch_pending.erase(key)
			print("MainWorld3D: unloaded chunk (%d,%d)" % [cx, cy])


func _handle_error(message: Dictionary) -> void:
	var error_msg: String = message.get("error", "unknown error")
	push_error("MainWorld3D: server error: %s" % error_msg)
	if message.get("request_type", "") == SaveApi.SNAPSHOT and _pause_menu:
		_save_file = ""
		_pause_menu.show_status("存档失败：%s" % error_msg, true)


func _query_weather() -> void:
	if Connection.status != Connection.Status.CONNECTED:
		return
	var chunk: Vector2i = _world_to_chunk(_player_pos.x, _player_pos.z)
	Connection.send({
		"type": "request",
		"request_type": "get_weather",
		"payload": {"chunks": [[chunk.x, chunk.y]]},
	})


func _update_lighting() -> void:
	if _sun_light == null or _world_env == null:
		return

	var hour_float: float = _game_hour + _game_minute / 60.0
	var daylight: float = _sunset - _sunrise
	if daylight <= 0.0:
		return

	var is_day: bool = hour_float >= _sunrise and hour_float < _sunset
	var day_progress: float = clampf((hour_float - _sunrise) / daylight, 0.0, 1.0)
	var sun_altitude: float = sin(day_progress * PI) if is_day else 0.0
	_last_sun_altitude = sun_altitude

	# 日出日落平滑 ramp：高度角 0→SUN_RAMP_CEIL 间渐入渐出，所有亮度量共用，消除跳变
	var sun_ramp: float = smoothstep(0.0, SUN_RAMP_CEIL, sun_altitude)

	# 阴影：低角度时 opacity 渐变淡出，避免阴影瞬间出现/消失
	var shadow_t: float = clampf((sun_altitude - SHADOW_CUTOFF) / SHADOW_FADE_BAND, 0.0, 1.0)
	_sun_light.shadow_opacity = shadow_t
	if sun_altitude < SHADOW_CUTOFF - SHADOW_FADE_BAND:
		_sun_light.shadow_enabled = false
	else:
		_sun_light.shadow_enabled = true
		var low_angle_t: float = clampf(
			(sun_altitude - SHADOW_CUTOFF) / (SHADOW_LOW_ANGLE_CEIL - SHADOW_CUTOFF),
			0.0, 1.0)
		_sun_light.directional_shadow_pancake_size = lerpf(
			SHADOW_LOW_ANGLE_PANCAKE, SHADOW_BASE_PANCAKE, low_angle_t)
		_sun_light.shadow_bias = SHADOW_BIAS_BASE / maxf(sun_altitude, SHADOW_BIAS_MIN_ALT)
	_sun_light.directional_shadow_max_distance = _compute_shadow_coverage(sun_altitude)

	# 太阳方向：全天连续曲线，夜间延续地平线角度（此时能量为 0，方向无关）
	_sun_light.rotation_degrees.x = lerpf(0.0, -90.0, sun_altitude)
	_sun_light.rotation_degrees.y = _sun_azimuth + day_progress * 180.0

	var intensity: float = _sunshine_intensity
	var warmth: float = 1.0 - sun_altitude
	_sun_light.light_color = Color(1.0, 1.0 - warmth * 0.3, 1.0 - warmth * 0.7, 1.0)
	# 直射光 = 后端日照（含降雨衰减）× 高度角平滑渐入
	_sun_light.light_energy = intensity * SUN_ENERGY_SCALE * sun_ramp

	var env: Environment = _world_env.environment
	if env:
		# 环境光/背景由高度角 ramp 驱动（时间平滑），再乘天气调制（雨天天光略暗）
		var env_t: float = sun_ramp * (ENV_BASE_WEIGHT + ENV_WEATHER_WEIGHT * intensity)
		env.ambient_light_color = NIGHT_AMBIENT.lerp(DAY_AMBIENT, env_t)
		env.ambient_light_energy = lerpf(0.5, 1.0, env_t)

		env.background_color = NIGHT_BG.lerp(DAY_BG, env_t)
