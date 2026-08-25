"""主世界 2D 场景 — 正俯视扁平化地形 + 流式 chunk（Issue #38）。

职责划分（2026-08 拆分）:
  - 本脚本（MainWorld2D）：世界编排——连接/消息路由/流式 chunk/玩家输入/
    就绪收尾的副作用执行。相机跟随与缩放 → CameraRig；昼夜色调 →
    LightingController；位置对账判定 → PlayerSync；地形/信号层数据 →
    TerrainTileBuilder；显示值追赶 → StateDisplayChaser + StateLayerManager；
    实体 pawn 生命周期 → PawnManager；加载流程计时/幂等闸 → WorldLoadingFlow。
    协作类均为纯逻辑 RefCounted（可单测），主世界只做编排与场景副作用。
  - 地形为 TileMapLayer（每 chunk 一个地形层 + 一个水面层 + 五信号层 +
    状态动态层），海拔保留为数据不作几何（五信号表达在 TerrainTileBuilder）。
"""
extends Node2D

# ── 相机常量（CameraRig 同源；测试引用，保留在此） ────────

const CAMERA_ZOOM_DEFAULT: float = Config.CAMERA_ZOOM_DEFAULT
const CAMERA_ZOOM_MIN: float = Config.CAMERA_ZOOM_MIN
const CAMERA_ZOOM_MAX: float = Config.CAMERA_ZOOM_MAX
const PLAYER_SPEED: float = Config.PLAYER_2D_SPEED
const PLAYER_FAST_MULT: float = Config.PLAYER_2D_FAST_MULT
const TILE_PIXEL_SIZE: int = Config.TILE_PIXEL_SIZE

# ── 流式 chunk 常量 ───────────────────────────────────────

const CHUNK_SIZE: int = Config.TILE_MAP_SIZE
const STREAM_MARGIN: int = 1
const UNLOAD_MARGIN: int = 1
## 在途响应缓冲（chunk 格数）：玩家快速移动时，已发出请求的 chunk
## 在此缓冲圈内不卸载——响应到达后仍能缓存/构建，避免「请求 → 玩家跑出
## 卸载圈 → 响应被丢弃」的白请求循环（区块加载不出来）。
const UNLOAD_BUFFER: int = 1
const MAX_PENDING: int = 3

## tile 二进制 BLOB 布局（与后端 ascend/space/tile_grid.py to_bytes 契约同步）：
## 4B 版本头 + uint16 LE 地形 + float32 LE 高程 + float32 LE 坡度 +
## 状态段（uint8 LE，各 40KB；顺序 = 后端 STATE_TYPES 注册顺序）
const _TILE_BLOB_VERSION: int = Config.TILE_BLOB_VERSION
const _TILE_BLOB_HEADER: int = 4
const _TILE_BLOB_TERRAIN: int = CHUNK_SIZE * CHUNK_SIZE * 2
const _TILE_BLOB_ELEV: int = CHUNK_SIZE * CHUNK_SIZE * 4
## 协商后的 tile 数据 BLOB 版本：握手时服务端在 hello_ack 下发其
## _TILEGRID_VERSION（后端数据格式权威版本），前端以其解码/校验；
## 握手前默认客户端已知版本 Config.TILE_BLOB_VERSION。
var _blob_version: int = Config.TILE_BLOB_VERSION
## 状态段按 BLOB 版本强关联（后端 STATE_TYPES 增删状态必须 bump 版本，
## 前端解码按版本查表，防止分段错位）：v1 无状态段；v2 = moisture/snow/ice
const _STATE_NAMES_BY_VERSION: Dictionary = {
	2: ["moisture", "snow", "ice"],
}

## 玩家移动上报节流间隔（秒）
const MOVE_REPORT_INTERVAL: float = 0.2

## ── 状态显示值追赶（StateDisplayChaser）──────────────

## 真值刷新间隔（秒）：后端 states（雪/冰/湿润）随时间演化，已加载 chunk
## 周期拉取完整响应换真值（显示值逐帧追赶，见 _process_state_chase）。
const STATE_REFRESH_INTERVAL: float = 8.0
## 真值刷新半径（chunk 格数）：玩家 chunk 周围 (2r+1)² 内已 BUILT 的
## 区块进入刷新圈（其余区块加载即快，无需刷新）。
const STATE_REFRESH_RADIUS: int = 1
## 单周期真值刷新上限（条）：限流网络，防 8s 周期瞬时拉爆
const STATE_REFRESH_MAX_PER_CYCLE: int = 3


static func _world_to_chunk(wx: float, wz: float) -> Vector2i:
	"""世界坐标 → chunk 坐标（全文件单一换算实现）。

	注意：前端 XZ 平面坐标对应后端 2D 坐标的 XY 轴（wx→x、wz→y）。
	"""
	return Vector2i(floori(wx / float(CHUNK_SIZE)), floori(wz / float(CHUNK_SIZE)))


static func _tile_index(tx: int, tz: int) -> int:
	"""chunk 内 tile 行优先索引（与后端 tile 数组布局一致）。"""
	return tz * CHUNK_SIZE + tx


static func _state_names_for_version(version: int) -> Array:
	"""按 BLOB 版本取状态名序（未知版本返回空——版本漂移已先行拒绝）。"""
	var names: Array = _STATE_NAMES_BY_VERSION.get(version, [])
	return names


static func _tile_blob_state_bytes(version: int) -> int:
	"""指定版本的状态段总字节数（各状态 40KB）。"""
	var names: Array = _state_names_for_version(version)
	return names.size() * CHUNK_SIZE * CHUNK_SIZE


static func _decode_u16_array(raw: PackedByteArray) -> PackedInt32Array:
	"""小端 uint16 数组 → PackedInt32Array（与后端 BLOB 布局一致）。"""
	var out := PackedInt32Array()
	var n: int = raw.size() >> 1  # BLOB 长度保证偶数（uint16 元素计数）
	out.resize(n)
	for i in n:
		out[i] = raw.decode_u16(i * 2)
	return out


static func _decode_f32_array(raw: PackedByteArray) -> PackedFloat32Array:
	"""小端 float32 数组 → PackedFloat32Array（与后端 BLOB 布局一致）。"""
	var out := PackedFloat32Array()
	var n: int = raw.size() >> 2  # BLOB 长度保证 4 字节对齐（float32 元素计数）
	out.resize(n)
	for i in n:
		out[i] = raw.decode_float(i * 4)
	return out


## 解码 BLOB 状态段（版本头已校验的完整响应/刷新响应共用）：按版本表
## 分段切出各状态数组（顺序 = 后端 STATE_TYPES 注册顺序）。
static func _decode_states(tiles_raw: PackedByteArray) -> Dictionary:
	var states: Dictionary = {}
	var state_names: Array = _state_names_for_version(tiles_raw.decode_u32(0))
	var states_raw: PackedByteArray = tiles_raw.slice(
		_TILE_BLOB_HEADER + _TILE_BLOB_TERRAIN + _TILE_BLOB_ELEV * 2,
		_TILE_BLOB_HEADER + _TILE_BLOB_TERRAIN + _TILE_BLOB_ELEV * 2
			+ _tile_blob_state_bytes(int(tiles_raw.decode_u32(0))))
	var seg: int = CHUNK_SIZE * CHUNK_SIZE
	for i in state_names.size():
		states[state_names[i]] = states_raw.slice(
			i * seg, (i + 1) * seg,
		)
	return states

## 终端节点
@onready var _terminal: TerminalWidget = $TerminalLayer/TerminalWidget
## 2D 正俯视相机
@onready var _camera: Camera2D = $World/Camera2D
## 全局昼夜色调
@onready var _canvas_modulate: CanvasModulate = $World/CanvasModulate
## 调试信息覆盖层
@onready var _debug_overlay: DebugOverlay = $DebugLayer/DebugOverlay
## 事件日志面板
@onready var _event_log: EventLog = $DebugLayer/EventLog
## ESC 暂停菜单
@onready var _pause_menu: PauseMenu = $PauseLayer/PauseMenu

## 相机几何/缩放协作者（绑定 _camera）
var _camera_rig: CameraRig = CameraRig.new()
## 昼夜色调协作者（绑定 _canvas_modulate）
var _lighting: LightingController = LightingController.new()

## 相机焦点（世界像素坐标，屏幕中心锚定点）
var _camera_focus: Vector2 = Vector2.ZERO
## 当前相机缩放（zoom=1 即 1 tile = TILE_PIXEL_SIZE 屏幕像素）
var _camera_zoom: Vector2 = Vector2(CAMERA_ZOOM_DEFAULT, CAMERA_ZOOM_DEFAULT)
## 地形 chunk 容器（TileMapLayer）
var _terrain_parent: Node2D
## 水面 chunk 容器（TileMapLayer，半透明独立层）
var _water_parent: Node2D
## 状态动态层容器（TileMapLayer，显示值追赶渲染；作为 Terrain 的兄弟节点
## 排在 ChunkPool 之后，恒在五信号层之上——覆雪/冰面盖住装饰与崖壁）
var _states_parent: Node2D

## 状态显示值追赶（真值→显示值逐帧收敛，纯视觉缓存；天气事件加速）
var _chaser: StateDisplayChaser = StateDisplayChaser.new()
## 状态动态层管理器（节点表/挂载/填充/遗忘/清空集中于此；挂 _states_parent）
var _state_layers_mgr: StateLayerManager = StateLayerManager.new()
## 真值刷新在途表: {Vector2i: true}（响应到达时区分刷新与初次加载）
var _refresh_pending: Dictionary = {}
## 真值刷新周期计时器（墙钟秒）
var _state_refresh_timer: float = 0.0

## chunk 生命周期状态（状态机逻辑在 ChunkStreamMachine 纯逻辑类，可单元测试）
## 字段请求与完整请求由状态驱动：FIELD_REQUESTED → 字段响应到达存数据 → 流循环限流发完整请求
## → TILE_REQUESTED → 完整响应解码 → RECEIVED → 材质就绪建网格 → BUILT。结构性无双请求。
const ChunkState = ChunkStreamMachine.ChunkState

## chunk 流式状态机（状态/断线降级/陈旧判定，纯逻辑）
var _stream_machine: ChunkStreamMachine = ChunkStreamMachine.new()
## chunk 数据缓存: {Vector2i(cx, cy): chunk_data_dict}
## null = 字段请求已发、响应未到（字段在途）；Dictionary = 字段或完整数据已收到
var _chunks: Dictionary = {}
## 地形/水面 TileSet 缓存（TerrainTileBuilder 懒构建，挂载时复用；按层名缓存）
var _layer_tile_sets: Dictionary = {}

## ── 异步 tile 层构建 ─────────────────────────────────────
## 后台构建在飞表: {Vector2i(cx, cy): 提交序号}。序号自增：重连重建的
## 新任务序号覆盖旧任务，陈旧结果（状态不符/序号失配）一律丢弃。
var _building: Dictionary = {}
var _build_seq: int = 0
## 后台任务完成结果队列 [key, cells, seq]（Mutex 保护，主线程 poll 挂载）
var _build_results: Array = []
var _build_mutex: Mutex = Mutex.new()
## tile 层构建器（测试可注入同步实现；_ready 默认 WorkerThreadPool 异步）
var tile_builder: Callable = Callable()
## 在飞构建上限（超出保持 RECEIVED 下帧重试，防传送后瞬间提交几十个任务）
const MAX_BUILD_INFLIGHT: int = 4

## 性能计时（微秒）
var _stream_us: int = 0

## 玩家实体 pawn（世界就绪后才创建，见 _ensure_player）
var _player: Node2D
## 实体 pawn 管理器（非玩家实体 + 玩家部件/朝向助手；挂 $World/Entities）
var _pawn_mgr: PawnManager = PawnManager.new()
## 玩家世界位置（XZ 平面移动；Y 仅保留贴地高度语义，2D 渲染不消费）
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

## 世界生成中全屏加载动画层（地形就绪前显示，_world_visible 后隐藏）：
## 不透明背景盖住 2D 世界，玩家看不到地形加载过程
var _loading_overlay: WorldLoadingOverlay
## 加载流程状态机（地形就绪计时/补满收尾幂等闸，纯逻辑）
var _loading_flow: WorldLoadingFlow = WorldLoadingFlow.new()

## 出生点附近地形是否已加载完成（就绪后才显示玩家/隐藏加载提示）
var _world_visible: bool = false
## 就绪判定半径：出生 chunk 周围 radius×radius 圈全部加载视为就绪
const TERRAIN_READY_RADIUS: int = WorldLoadingFlow.TERRAIN_READY_RADIUS
## 地形就绪等待超时（秒）
const TERRAIN_READY_TIMEOUT: float = WorldLoadingFlow.TERRAIN_READY_TIMEOUT
## 补满收尾兜底（秒）
const COMPLETION_FALLBACK_SEC: float = WorldLoadingFlow.COMPLETION_FALLBACK_SEC

## 当前游戏时间
var _game_hour: float = 6.0
var _game_minute: int = 0
## 日出日落时间（从后端天气查询获取）
var _sunrise: float = 6.0
var _sunset: float = 18.0
## 太阳方位角（0-360，从后端种子派生；2D 无方向光，保留供未来局部光/调试）
var _sun_azimuth: float = 45.0
## 日照强度（0-1，来自后端）
var _sunshine_intensity: float = 0.5
## 天气轮询计时器
var _weather_query_timer: float = 0.0
const WEATHER_QUERY_INTERVAL: float = 1.0
## 光照更新节流间隔（墙钟秒）：光照量只随游戏分钟/天气事件变化
var _lighting_timer: float = 0.0
const LIGHTING_UPDATE_INTERVAL: float = 0.5


## 节点就绪：连接终端命令/连接状态/消息信号，挂载暂停菜单保存回调，
## 创建加载提示与地形容器引用，并初始化相机与光照协作者。
func _ready() -> void:
	tile_builder = _default_tile_builder
	_terminal.remote_command_submitted.connect(_on_terminal_command)

	Connection.connection_established.connect(_on_connected)
	Connection.connection_lost.connect(_on_disconnected)
	Connection.message_received.connect(_on_message)
	Settings.locale_changed.connect(_on_settings_locale_changed)

	if _pause_menu:
		_pause_menu.save_requested.connect(_on_pause_save_requested)
		_pause_menu.set_terminal(_terminal)

	_terrain_parent = $World/Terrain/ChunkPool
	_water_parent = $World/Water

	# 状态动态层容器：运行时创建为 Terrain 的兄弟节点（排在 ChunkPool 之后，
	# 恒在全部 chunk 层之上；ChunkPool 内按挂载序 append 无法保证后置）
	if not $World/Terrain.has_node("StatesPool"):
		var states_pool := Node2D.new()
		states_pool.name = "StatesPool"
		$World/Terrain.add_child(states_pool)
	_states_parent = $World/Terrain/StatesPool
	_state_layers_mgr.bind(_states_parent)
	_pawn_mgr.bind(_entities_root(), func() -> bool: return _world_visible)

	_camera_rig.bind(_camera)
	_lighting.bind(_canvas_modulate)

	# 玩家节点不在 _ready 创建：须等后端世界就绪（出生点到达）后才创建，
	# 与后端语义一致（服务模式/读档重建期间不存在世界与玩家）
	_create_loading_overlay()

	_setup_debug_overlay()
	_configure_camera()


## 节点退出：断开 Connection 与暂停菜单信号，防止悬挂回调；清空后台构建
## 结果队列（工作线程仍在跑的任务结果直接丢弃，防止实例释放后挂载悬垂）。
func _exit_tree() -> void:
	if Connection.connection_established.is_connected(_on_connected):
		Connection.connection_established.disconnect(_on_connected)
	if Connection.connection_lost.is_connected(_on_disconnected):
		Connection.connection_lost.disconnect(_on_disconnected)
	if Connection.message_received.is_connected(_on_message):
		Connection.message_received.disconnect(_on_message)
	if Settings.locale_changed.is_connected(_on_settings_locale_changed):
		Settings.locale_changed.disconnect(_on_settings_locale_changed)
	if _pause_menu and _pause_menu.save_requested.is_connected(_on_pause_save_requested):
		_pause_menu.save_requested.disconnect(_on_pause_save_requested)
	_build_mutex.lock()
	_build_results.clear()
	_build_mutex.unlock()


## 配置相机（转发 CameraRig）：重置默认缩放与焦点后应用变换。
func _configure_camera() -> void:
	if _camera == null:
		push_error("MainWorld2D: Camera2D not found!")
		return
	_camera_zoom = Vector2(CAMERA_ZOOM_DEFAULT, CAMERA_ZOOM_DEFAULT)
	_camera_focus = _world_to_screen(_player_pos)
	_camera_rig.configure(_camera_focus, _camera_zoom)


func _ensure_player() -> void:
	"""玩家节点惰性创建：仅在世界就绪（出生点已知）后调用，幂等。"""
	if _player != null:
		return
	_create_player()


## 创建玩家 pawn（PawnRenderer 按 CREATURE 规格生成分层 Sprite2D 部件，
## 脚底中心锚点 + 头顶名称浮层；侧视 billboard 由朝向镜像换位表达）：
## 初始隐藏，位置取 _player_pos（惰性创建前可能已有权威位置，不复位）。
func _create_player() -> void:
	var player := Node2D.new()
	player.name = "Player"
	var spec: Dictionary = PawnRenderer.default_spec("CREATURE")
	_pawn_mgr.register_node(player, spec, false)
	PawnManager.apply_parts(player, spec, false)
	PawnManager.add_nameplate(player, spec, "CREATURE")
	# 局部光源：玩家火炬（PointLight2D，径向渐变占位纹理；夜间点亮，
	# 昼夜开关在 _update_lighting）
	var torch := PointLight2D.new()
	torch.name = "PlayerTorch"
	torch.texture = _torch_light_texture()
	torch.energy = 1.0
	torch.texture_scale = 0.35
	torch.position = Vector2(8, 14)  # 火把挂在身体上部
	torch.visible = false
	player.add_child(torch)
	_player = player
	_player.visible = false  # 等出生点和地形就绪后再显示
	_entities_root().add_child(_player)
	# 注：不复位 _player_pos——惰性创建可能发生在权威位置已写入之后
	# （回归：_apply_authoritative_position → _ensure_player 时位置被清零）
	_player.position = _world_to_screen(_player_pos)
	print("MainWorld2D: player created")


## 实体 pawn 挂载层（$World/Entities，Y-sort 渲染排序——实体与地形/植物
## 按 y 深度正确遮挡）。场景缺失时兜底创建（防御性）。
func _entities_root() -> Node2D:
	var root := $World/Entities
	if root == null:
		root = Node2D.new()
		root.name = "Entities"
		root.y_sort_enabled = true
		$World.add_child(root)
	return root


## 设置 pawn 朝向（-1 朝左 / 1 朝右）：转发 PawnManager（镜像重建部件）。
func _set_pawn_facing(node: Node2D, facing_left: bool) -> void:
	_pawn_mgr.set_facing(node, facing_left)


## 实体快照/事件 → 生成 pawn（转发 PawnManager；玩家由快照/player_state
## 独占消费，不入 pawn 表）。
func _ensure_pawn(entity_id: String, entity_type: String,
		x: float, y: float) -> Node2D:
	return _pawn_mgr.ensure(entity_id, entity_type, x, y)


## 放置 pawn 到全局 tile 坐标（转发 PawnManager）。
func _place_pawn(node: Node2D, x: float, y: float) -> void:
	_pawn_mgr.place(node, x, y)


## 移除实体 pawn（转发 PawnManager）。
func _despawn_pawn(entity_id: String) -> void:
	_pawn_mgr.despawn(entity_id)


## 世界 tile 坐标 → 屏幕像素坐标（全部渲染接缝的唯一换算）。
static func _world_to_screen(world: Vector3) -> Vector2:
	return Vector2(world.x, world.z) * float(TILE_PIXEL_SIZE)


## 火炬光源纹理：径向渐变占位（中心亮暖白 → 边缘透明，64px）。
var _torch_texture: ImageTexture


func _torch_light_texture() -> ImageTexture:
	if _torch_texture == null:
		var size := 64
		var img := Image.create(size, size, false, Image.FORMAT_RGBA8)
		var center := Vector2(size * 0.5, size * 0.5)
		for y in size:
			for x in size:
				var d: float = center.distance_to(Vector2(x, y)) / (size * 0.5)
				img.set_pixel(x, y, Color(1.0, 0.95, 0.8,
					clampf(1.0 - d, 0.0, 1.0)))
		_torch_texture = ImageTexture.create_from_image(img)
	return _torch_texture


func _create_loading_overlay() -> void:
	"""世界生成/地形加载中的全屏加载动画层（地形就绪后隐藏）。

	不透明背景完全盖住 2D 世界：进入存档后玩家看不到地形 chunk
	流式加载的过程，出生点就绪、玩家归位后直接看到加载完成的世界。
	"""
	var layer := CanvasLayer.new()
	layer.name = "LoadingLayer"
	layer.layer = 400
	var overlay := WorldLoadingOverlay.new()
	overlay.name = "WorldLoadingOverlay"
	overlay.mouse_filter = Control.MOUSE_FILTER_IGNORE
	overlay.set_anchors_and_offsets_preset(Control.PRESET_FULL_RECT)
	overlay.completed.connect(_finish_world_visible)
	layer.add_child(overlay)
	add_child(layer)
	_loading_overlay = overlay
	overlay.set_text(tr("ui.loading.generating_world"))


## 查询世界坐标处的地面海拔（来自已缓存 chunk 的高程数组）。
##
## Args:
##     pos: 世界坐标（Vector2 的 y 即世界 Z 轴）。
##
## Returns:
##     该 tile 海拔；chunk 未缓存、数据不全或 tile 越界时返回 NAN。
func _get_ground_elevation_at(pos: Vector2) -> float:
	var chunk_pos := _world_to_chunk(pos.x, pos.y)
	var key := chunk_pos
	var chunk = _chunks.get(key)
	if not (chunk is Dictionary):
		return NAN
	var elev: PackedFloat32Array = chunk.get("elevation", PackedFloat32Array())
	if elev.size() < CHUNK_SIZE * CHUNK_SIZE:
		return NAN
	# floori 与 _world_to_chunk 的坐标语义一致：负坐标下 int() 向零
	# 截断会算错 tile 索引（int(-0.5)=0 而 tile 应为 -1）
	var tx: int = floori(pos.x) - chunk_pos.x * CHUNK_SIZE
	var tz: int = floori(pos.y) - chunk_pos.y * CHUNK_SIZE
	if tx < 0 or tx >= CHUNK_SIZE or tz < 0 or tz >= CHUNK_SIZE:
		return NAN
	return elev[_tile_index(tx, tz)]


## 记录后端权威出生 chunk（仅首次生效）：玩家与相机焦点移到出生点并应用变换，更新加载提示。
func _set_birth_chunk(cx: int, cy: int) -> void:
	if _has_birth:
		return
	_has_birth = true
	_birth_chunk = Vector2i(cx, cy)
	_reset_authority_state()
	# 与后端权威出生点约定一致：出生 chunk 原点（PlayerService.birth_position）
	_player_pos.x = float(cx * CHUNK_SIZE)
	_player_pos.z = float(cy * CHUNK_SIZE)
	if _player:
		_player.position = _world_to_screen(_player_pos)
	_camera_focus = _world_to_screen(_player_pos)
	_apply_camera_transform()
	# 地形就绪前保持加载提示（玩家节点在 _check_terrain_ready 就绪后创建）
	if _loading_overlay:
		_loading_overlay.set_text(tr("ui.loading.loading_terrain"))
		_loading_overlay.set_stage("chunks")
		_loading_overlay.visible = true
	print("MainWorld2D: birth chunk (%d,%d), player at (%.0f, %.0f)" % [cx, cy, _player_pos.x, _player_pos.z])


func _check_terrain_ready(force: bool = false) -> void:
	"""出生点周围地形加载完成后切换为可见世界。

	判定：出生 chunk 的 TERRAIN_READY_RADIUS 邻域全部 BUILT；
	force=true（超时兜底）跳过判定直接就绪，防后端异常时玩家永久卡住。
	两条路径都先补满进度条到 100%（completed 信号）后才显示世界与玩家
	——加载画面始终以满格收尾，不会半截消失。
	"""
	if _world_visible or not _has_birth:
		return
	if not force:
		if not _stream_machine.all_built(_birth_chunk, TERRAIN_READY_RADIUS):
			_update_loading_progress()
			return
	_begin_completion()


## 进度条补满收尾（幂等）：补满 100% 并停留片刻后显示世界。
## 超时兜底与正常就绪共用：若 completed 信号异常未触发（覆盖层隐藏/销毁等），
## COMPLETION_FALLBACK_SEC 兜底计时器强制收尾，防玩家永久卡在加载层。
func _begin_completion() -> void:
	if not _loading_flow.begin_completion():
		return
	if _loading_overlay:
		_loading_overlay.complete()
	else:
		_finish_world_visible()


## 世界可见收尾（幂等）：正常路径由进度条补满信号触发，超时兜底直接调用。
func _finish_world_visible() -> void:
	if _world_visible:
		return
	_world_visible = true
	_loading_flow.finish()
	if _loading_overlay:
		_loading_overlay.visible = false
	_ensure_player()
	_player.visible = true
	_pawn_mgr.show_all()
	_update_player_ground()
	_camera_focus = _world_to_screen(_player_pos)
	_apply_camera_transform()
	print("MainWorld2D: 出生点地形就绪，世界可见")


## 按出生点邻域已构建 chunk 数推进加载进度条（90% → 100% 区间）：
## 每挂载一个 chunk 即更新一次，地形加载接近完成时进度条逐渐补满。
func _update_loading_progress() -> void:
	if _loading_overlay == null or not _has_birth:
		return
	var side: int = TERRAIN_READY_RADIUS * 2 + 1
	var total: int = side * side
	var built: int = _stream_machine.built_count(_birth_chunk, TERRAIN_READY_RADIUS)
	_loading_overlay.set_terrain_progress(
		WorldLoadingFlow.terrain_progress(built, total))


# ── 世界就绪事件（世界进程的就绪信号） ────────────────────

func _on_world_progress(data: Dictionary) -> void:
	"""世界生成阶段进度（大陆生成 5-30s 期间逐阶段更新提示）。

	阶段文案单源：WorldStageLabels（与 world_loading 共用，
	对应后端 ContinentGenerator.STAGE_*）。
	"""
	if not _has_birth and _loading_overlay:
		var stage: String = str(data.get("stage", ""))
		_loading_overlay.set_text(WorldStageLabels.label_for(stage))
		_loading_overlay.set_stage(stage)


func _on_world_initialized(data: Dictionary) -> void:
	"""新世界就绪（世界进程构建完成）：接入并拉取权威状态。

	进程模型下每次进入世界都是新连接：_on_connected 已主动请求
	玩家实体/状态；此处再请求一次保证 world_initialized 之后拿到
	（覆盖世界生成期间连接建立、事件先到的情况）。
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
	_sync_locale_to_backend()


func _reset_world_state() -> void:
	"""清空旧世界的 chunk 状态/数据与地形节点（世界重建后旧数据失效）。"""
	_has_birth = false
	_world_visible = false
	_loading_flow.reset()
	_birth_chunk = Vector2i.ZERO
	_reset_authority_state()
	# 换世界后玩家实体 ID 可能变化：清空避免旧 ID 过滤掉快照中的新玩家
	# （否则 PLAYER 独占消费被 continue 跳过，仅剩 player_state 兜底）
	_player_entity_id = ""
	_stream_machine.reset()
	_chunks.clear()
	_chaser.reset()
	_state_layers_mgr.clear()
	_refresh_pending.clear()
	_state_refresh_timer = 0.0
	if _player:
		_player.visible = false
	_clear_pawns()
	if _terrain_parent:
		for child in _terrain_parent.get_children():
			child.queue_free()
	if _water_parent:
		for child in _water_parent.get_children():
			child.queue_free()
	if _loading_overlay:
		_loading_overlay.reset()
		_loading_overlay.set_text(tr("ui.loading.loading_world"))
		_loading_overlay.visible = true


## 清空全部实体 pawn（世界重建/断线后旧实体数据失效；转发 PawnManager）。
func _clear_pawns() -> void:
	_pawn_mgr.clear()


# ── 调试数据 getter（供 DebugSection 自行拉取）────────────

## 调试 getter：相机焦点与缩放显示文本（供 DebugSection 自行拉取）。
##
## Returns:
##     含 position/camera_display 的字典；相机缺失时返回空字典。
func get_debug_camera_info() -> Dictionary:
	if _camera == null:
		return {}
	return {
		"position": _camera_focus,
		"camera_display": tr("debug.camera_zoom").format({"zoom": "%.1f" % _camera_zoom.x}),
	}


## 调试 getter：显示值追赶统计（抽样密度倍率/加速剩余/收敛中 chunk 数）。
##
## Returns:
##     含 boost_mult/boost_remaining/chunks 的字典。
func get_debug_state_chase_info() -> Dictionary:
	return {
		"boost_mult": _chaser.boost_mult(),
		"boost_remaining": _chaser.boost_remaining(),
		"chunks": _chaser.chunk_count(),
	}


## 调试 getter：玩家世界坐标、所在 chunk 与地面海拔。
##
## Returns:
##     含 world_pos/chunk/elevation 的字典。
func get_debug_player_info() -> Dictionary:
	return {
		"world_pos": Vector2(_player_pos.x, _player_pos.z),
		"chunk": _world_to_chunk(_player_pos.x, _player_pos.z),
		"elevation": _player_pos.y - 1.0,
	}


## 调试 getter：查询世界坐标所在 tile 的高程与坡度。
##
## Args:
##     world_pos: 世界坐标（Vector2 的 y 即世界 Z 轴）。
##
## Returns:
##     含 elevation/slope 的字典（字段缺失时省略）；chunk 未缓存、高程数据不全或坐标越界时返回空字典。
func get_debug_terrain_at(world_pos: Vector2) -> Dictionary:
	var key := _world_to_chunk(world_pos.x, world_pos.y)
	var chunk = _chunks.get(key)
	if chunk == null or not (chunk is Dictionary):
		return {}
	var elev: PackedFloat32Array = chunk.get("elevation", PackedFloat32Array())
	var slope: PackedFloat32Array = chunk.get("slope", PackedFloat32Array())
	if elev.size() < CHUNK_SIZE * CHUNK_SIZE:
		return {}
	# floori：负坐标下 int() 向零截断会算错 tile 索引（见 _get_ground_elevation_at）
	var tx: int = floori(world_pos.x) - key.x * CHUNK_SIZE
	var tz: int = floori(world_pos.y) - key.y * CHUNK_SIZE
	if tx < 0 or tx >= CHUNK_SIZE or tz < 0 or tz >= CHUNK_SIZE:
		return {}
	var idx: int = _tile_index(tx, tz)
	var result: Dictionary = {}
	if idx < elev.size():
		result["elevation"] = int(elev[idx])
	if idx < slope.size():
		result["slope"] = slope[idx]
	return result


## 调试 getter：查询世界坐标所在 chunk 的气候字段（温度/湿度/气候带）。
##
## Args:
##     world_pos: 世界坐标（Vector2 的 y 即世界 Z 轴）。
##
## Returns:
##     含 temperature/humidity/climate_zone 的字典（字段缺失或 chunk 未缓存时省略）。
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


## 调试 getter：已加载（BUILT）/缓存（RECEIVED）/请求中（FIELD/TILE_REQUESTED）计数。
##
## Returns:
##     含 loaded/cached/pending 计数的字典。
func get_debug_chunk_stats() -> Dictionary:
	return _stream_machine.counts()


## 调试 getter：流式加载与连接处理耗时（微秒）。
##
## Returns:
##     含 stream/conn 计时的字典。
func get_debug_timing() -> Dictionary:
	return {
		"stream": _stream_us,
		"conn": Connection.last_process_us,
	}


## 按当前 tile 高程修正玩家 Y 坐标（2D 渲染不消费，保留贴地语义）；无高程数据时不移动。
func _update_player_ground() -> void:
	var ground_y := _get_ground_elevation_at(Vector2(_player_pos.x, _player_pos.z))
	if not is_nan(ground_y):
		_player_pos.y = maxf(ground_y, 0.0) + 1.0
		_ensure_player()
		_player.position = _world_to_screen(_player_pos)


## 挂载已构建完成的 chunk tile 层（主线程）：按层创建 TileMapLayer、
## set_cell 批量画格、标记 BUILT、触发就绪检查。tile 数据由后台任务构建完成
## （TerrainTileBuilder.build_cells，含五信号层），此处只做场景树操作（几毫秒）。
## 状态非 CONSTRUCTING（卸载/断线降级后）或节点已存在时不挂载。
##
## Args:
##     key: chunk 坐标（决定节点名与挂载偏移）。
##     cells: TerrainTileBuilder.build_cells 返回的层数据（空层跳过挂载）。
func _mount_built_chunk(key: Vector2i, cells: Dictionary) -> void:
	if _stream_machine.get_state(key) != ChunkState.CONSTRUCTING:
		return
	if _terrain_parent.has_node(NodePath("Chunk_%d_%d" % [key.x, key.y])):
		# 节点已存在（重复构建/双响应竞态）：补记 BUILT 防状态卡死
		# CONSTRUCTING（不再被刷新/统计失真/加载层永不就绪）
		_stream_machine.mark_built(key)
		return

	var offset := Vector2(float(key.x * CHUNK_SIZE), float(key.y * CHUNK_SIZE)) \
			* float(TILE_PIXEL_SIZE)

	# 地形层（陆地 tile；空层也建节点占位——卸载按名字清除）
	var terrain_cells: Array = cells.get(TerrainTileBuilder.LAYER_TERRAIN, [])
	var tml := TileMapLayer.new()
	tml.name = "Chunk_%d_%d" % [key.x, key.y]
	tml.tile_set = _lazy_layer_tile_set(TerrainTileBuilder.LAYER_TERRAIN)
	tml.position = offset
	_terrain_parent.add_child(tml)
	if not terrain_cells.is_empty():
		_fill_cells(tml, terrain_cells)

	# 水面层（半透明独立层；无水域 chunk 不建，卸载按名字跳过）
	var water_cells: Array = cells.get(TerrainTileBuilder.LAYER_WATER, [])
	if not water_cells.is_empty():
		var wml := TileMapLayer.new()
		wml.name = "Water_%d_%d" % [key.x, key.y]
		wml.tile_set = _lazy_layer_tile_set(TerrainTileBuilder.LAYER_WATER)
		wml.position = offset
		_water_parent.add_child(wml)
		_fill_cells(wml, water_cells)

	# 五信号层：崖壁 / 固定方向投影 / 装饰（密度+雪顶）/ 等高线调试层（可关）
	_mount_signal_layer(key, offset, cells, TerrainTileBuilder.LAYER_CLIFF,
		_terrain_parent)
	_mount_signal_layer(key, offset, cells, TerrainTileBuilder.LAYER_SHADOW,
		_terrain_parent)
	_mount_signal_layer(key, offset, cells, TerrainTileBuilder.LAYER_DECOR,
		_terrain_parent)
	if Config.CONTOUR_LAYER_ENABLED:
		_mount_signal_layer(key, offset, cells, TerrainTileBuilder.LAYER_CONTOUR,
			_terrain_parent)

	_stream_machine.mark_built(key)
	print("MainWorld2D: chunk (%d,%d) — %d terrain, %d water, %d cliff, %d shadow, %d decor cells" % [
		key.x, key.y, terrain_cells.size(), water_cells.size(),
		cells.get(TerrainTileBuilder.LAYER_CLIFF, []).size(),
		cells.get(TerrainTileBuilder.LAYER_SHADOW, []).size(),
		cells.get(TerrainTileBuilder.LAYER_DECOR, []).size()])

	_check_terrain_ready()


## 挂载单个信号层（空层跳过）；等高线层由调用方按开关决定是否挂载。
func _mount_signal_layer(key: Vector2i, offset: Vector2, cells: Dictionary,
		layer: String, parent: Node2D) -> void:
	var layer_cells: Array = cells.get(layer, [])
	if layer_cells.is_empty():
		return
	var ml := TileMapLayer.new()
	ml.name = "%s_%d_%d" % [_node_prefix(layer), key.x, key.y]
	ml.tile_set = _lazy_layer_tile_set(layer)
	ml.position = offset
	parent.add_child(ml)
	_fill_cells(ml, layer_cells)


## 层节点名前缀（Chunk/Water/Cliff/Shadow/Decor/Contour/States）。
static func _node_prefix(layer: String) -> String:
	match layer:
		TerrainTileBuilder.LAYER_TERRAIN:
			return "Chunk"
		TerrainTileBuilder.LAYER_WATER:
			return "Water"
		TerrainTileBuilder.LAYER_CLIFF:
			return "Cliff"
		TerrainTileBuilder.LAYER_SHADOW:
			return "Shadow"
		TerrainTileBuilder.LAYER_DECOR:
			return "Decor"
		TerrainTileBuilder.LAYER_CONTOUR:
			return "Contour"
		TerrainTileBuilder.LAYER_STATES:
			return "States"
	return "Chunk"


## 批量填充 tile 层（Godot 4.7 的 TileMapLayer 无 set_cells，逐格 set_cell；
## 单 chunk 上限 40k 格，主线程一次性填充 ~ms 级可接受；后续阶段若需优化
## 可改用 TileMapLayer 内置批量接口或分层预处理）。
func _fill_cells(layer: TileMapLayer, cells: Array) -> void:
	for cell in cells:
		layer.set_cell(cell[0], cell[1], cell[2])


## 层名 → 占位色表（TerrainTileBuilder 同源；懒构建 TileSet 时查表）
const _LAYER_COLORS: Dictionary = {
	TerrainTileBuilder.LAYER_TERRAIN: TerrainTileBuilder.TERRAIN_TILE_COLORS,
	TerrainTileBuilder.LAYER_WATER: TerrainTileBuilder.WATER_TILE_COLORS,
	TerrainTileBuilder.LAYER_CLIFF: TerrainTileBuilder.CLIFF_TILE_COLORS,
	TerrainTileBuilder.LAYER_SHADOW: TerrainTileBuilder.SHADOW_TILE_COLORS,
	TerrainTileBuilder.LAYER_DECOR: TerrainTileBuilder.DECOR_TILE_COLORS,
	TerrainTileBuilder.LAYER_CONTOUR: TerrainTileBuilder.CONTOUR_TILE_COLORS,
	TerrainTileBuilder.LAYER_STATES: TerrainTileBuilder.STATES_TILE_COLORS,
}

## 懒构建指定层的 TileSet（TerrainTileBuilder 占位色块 atlas，单源多列）；
## 首次调用后缓存复用（每层全 chunk 共享一个 TileSet）。
func _lazy_layer_tile_set(layer: String) -> TileSet:
	if not _layer_tile_sets.has(layer):
		_layer_tile_sets[layer] = TerrainTileBuilder.make_tile_set(
			_LAYER_COLORS[layer])
	return _layer_tile_sets[layer]


## 每帧主循环：相机缩放 → 连接/终端/世界就绪三道闸门（未就绪仅刷调试覆盖层）→
## 事件日志更新、就绪超时兜底、流式加载、移动输入、天气轮询（1s）与光照更新。
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
		var flow_act: Dictionary = _loading_flow.tick(delta)
		if flow_act.force_ready:
			_check_terrain_ready(true)
		if flow_act.force_finish:
			_finish_world_visible()

	_stream_chunks()
	_process_state_chase(delta)
	_process_snap(delta)
	_process_input(delta)

	_weather_query_timer += delta
	if _weather_query_timer >= WEATHER_QUERY_INTERVAL:
		_weather_query_timer = 0.0
		_query_weather()
	# 光照更新节流：昼夜色调随游戏分钟/天气事件变化，墙钟 0.5s 重算一次足够平滑
	_lighting_timer += delta
	if _lighting_timer >= LIGHTING_UPDATE_INTERVAL:
		_lighting_timer = 0.0
		_update_lighting()

	if _debug_overlay and _debug_overlay.is_shown():
		_debug_overlay.process_sections(delta)


## 处理滚轮缩放输入（转发 CameraRig）：zoom 按步长倍乘并钳制，应用变换。
func _process_camera(_delta: float) -> void:
	var new_zoom := _camera_rig.process_zoom(_camera_zoom)
	if new_zoom != _camera_zoom:
		_camera_zoom = new_zoom
		_apply_camera_transform()


## 按焦点与缩放摆位相机（转发 CameraRig）。
func _apply_camera_transform() -> void:
	_camera_rig.apply(_camera_focus, _camera_zoom)


## 推进平滑吸附过渡（判定在 PlayerSync）：每帧按本帧完成比例从"当前实际
## 位置"（含 _process_input 已叠加的输入位移）向目标推进，输入不丢失。
func _process_snap(delta: float) -> void:
	var result: Array = PlayerSync.advance_snap(
		delta, _snap_time, _snap_target, _player_pos)
	_player_pos = result[0]
	_snap_time = result[1]
	if _player:
		_player.position = _world_to_screen(_player_pos)


## 处理移动与交互输入：正俯视下屏幕方向直接映射世界 XZ（右=+x、下=+z，
## 与后端坐标一致），Shift 加速，同步玩家节点与相机；移动节流到
## MOVE_REPORT_INTERVAL 后上报权威位置；交互键发送 player_interact。
func _process_input(delta: float) -> void:
	var move_input := Input.get_vector("move_left", "move_right", "move_up", "move_down")
	if move_input != Vector2.ZERO:
		var speed := PLAYER_SPEED
		if Input.is_key_pressed(KEY_SHIFT):
			speed *= PLAYER_FAST_MULT

		_player_pos.x += move_input.x * speed * delta
		_player_pos.z += move_input.y * speed * delta
		var facing: int = PlayerSync.facing_from_move(move_input)
		if facing != 0 and _player:
			_set_pawn_facing(_player, facing < 0)
		_update_player_ground()
		if _player:
			_player.position = _world_to_screen(_player_pos)
		_camera_focus = _world_to_screen(_player_pos)
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
			_pause_menu.show_status(tr("ui.pause.world_not_ready"), true)
		return
	Connection.send(SaveApi.snapshot_request(_world_id))


## 终端命令回调：原样打包为 terminal_cmd 请求转发后端执行。
func _on_terminal_command(command: String) -> void:
	Connection.send({
		"type": "request",
		"request_type": "terminal_cmd",
		"payload": {"command": command},
	})


## 语言设置同步后端（幂等）：进入世界后调一次；游戏内改语言立即调。
## 世界未就绪时跳过——下次 world_initialized 会补上。
func _sync_locale_to_backend() -> void:
	if _world_id.is_empty():
		return
	_on_terminal_command("lang %s" % Settings.get_locale())


## 设置界面语言变更回调（Settings 自动加载广播）。
func _on_settings_locale_changed(_locale: String) -> void:
	_sync_locale_to_backend()


## 上报玩家当前位置为 player_move 请求（世界未就绪时跳过；后端裁决并可能钳制越界）。
## 携带递增 seq 并记录上报位置（PlayerSync.record_report，响应按序回传 seq，
## 供回声判定精确对齐）；记录超限丢最旧。
func _send_player_move() -> void:
	if not _has_birth:
		return
	_move_report_seq += 1
	var seq: int = _move_report_seq
	PlayerSync.record_report(_report_seq_pos, seq, _player_pos)
	Connection.send({
		"type": "request",
		"request_type": "player_move",
		"payload": {"x": _player_pos.x, "y": _player_pos.z, "seq": seq},
	})


# ── 调试覆盖层 ──────────────────────────────────────────────

## 注册调试覆盖层默认分区（传 self 供各分区自行拉取数据）。
func _setup_debug_overlay() -> void:
	_debug_overlay.setup_default_sections(self)


# ── Connection 信号处理 ───────────────────────────────────

## 连接建立回调：打印连接信息，主动拉取实体快照与玩家状态。
func _on_connected(host: String, port: int) -> void:
	print("MainWorld2D: connected to %s:%d" % [host, port])
	# 采用握手协商的 tile 数据 BLOB 版本（hello_ack 携带服务端权威版本；
	# 未知时保留客户端已知版本，解码校验兜底仍会拦截版本漂移）
	var hs_blob: int = Connection._handshake.blob_version if Connection._handshake else 0
	if hs_blob > 0:
		_blob_version = hs_blob
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


## 连接断开回调：作废在途请求（重连后 _stream_chunks 自动重新入队）。
##
## 状态降级规则（由 ChunkStreamMachine 统一实现）：
##   - FIELD_REQUESTED 且无数据（字段在途）→ UNKNOWN（重连后重新字段请求）
##   - FIELD_REQUESTED 有数据 / TILE_REQUESTED → 保留数据置 FIELD_REQUESTED（重连后重发完整请求）
##   - RECEIVED / BUILT → 保留（数据仍有效，重连后恢复构建）
func _on_disconnected() -> void:
	print("MainWorld2D: disconnected")
	for key in _stream_machine.on_disconnect(
			func(k): return _chunks.get(k) is Dictionary):
		_chunks.erase(key)
	_save_file = ""
	# 真值刷新在途作废：重连后走正常流式路径，刷新响应不复用
	_refresh_pending.clear()
	# 实体 pawn 作废：重连后重新拉取 entity_snapshot 重建
	_clear_pawns()


## 消息分发：按 type（event/response/error）路由到对应处理函数，未知类型告警。
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
			push_warning("MainWorld2D: unknown message type: %s" % msg_type)


## 事件分发：world_progress/initialized 世界就绪信号直接处理不记日志；
## minute_change 更新时间（落入通用广播）、player_teleported 同步位置并记日志（提前返回）；
## 其余事件（含 minute_change）广播给调试覆盖层与事件日志。
func _handle_event(message: Dictionary) -> void:
	var event_type: String = message.get("event_type", "")
	var payload: Dictionary = message.get("payload", {})
	var data: Dictionary = payload.get("data", {})

	# 世界就绪信号：世界外元操作，不进事件日志
	if event_type == "world_progress":
		_on_world_progress(data)
		return
	if event_type == "world_initialized":
		_on_world_initialized(data)
		return

	if event_type == "minute_change":
		_game_hour = float(payload.get("game_hour", _game_hour))
		_game_minute = int(payload.get("game_minute", _game_minute))

	# 实体生灭/移动事件 → pawn 增量维护（快照是全量，事件是增量）。
	# 玩家实体由 player_state/快照独占消费——entity_born 携带
	# controller=PLAYER 时跳过（否则与 _player 双渲染分身）
	if event_type == "entity_born":
		var born_id: String = str(data.get("entity_id", ""))
		var born_controller: String = str(data.get("controller", ""))
		if not born_id.is_empty() and born_controller != "PLAYER":
			_ensure_pawn(born_id, str(data.get("entity_type", "")),
				float(data.get("x", 0.0)), float(data.get("y", 0.0)))
	if event_type == "entity_died":
		var died_id: String = str(data.get("entity_id", ""))
		if not died_id.is_empty():
			if died_id == _player_entity_id and _player:
				_player.visible = false
			else:
				_despawn_pawn(died_id)
	if event_type == "entity_moved":
		var moved_id: String = str(data.get("entity_id", ""))
		if _pawn_mgr._pawns.has(moved_id):
			_place_pawn(_pawn_mgr._pawns[moved_id],
				float(data.get("x", 0.0)), float(data.get("y", 0.0)))

	if event_type == "player_teleported":
		_reset_authority_state()
		var tx: float = float(data.get("x", _player_pos.x))
		var tz: float = float(data.get("y", _player_pos.z))
		_player_pos.x = tx
		_player_pos.z = tz
		_update_player_ground()
		_camera_focus = _world_to_screen(_player_pos)
		_apply_camera_transform()
		if _event_log:
			_event_log.push_event("[%s] %s" % [
				SaveInfoFormatter.hhmm_string(
					int(payload.get("game_hour", 0)),
					int(payload.get("game_minute", 0))),
				tr("event_log.teleported").format({
					"x": "%.0f" % tx, "y": "%.0f" % tz})])
		return

	# 天气事件 → 显示值追赶加速（初雪/暴雪"快下快铺"，见 StateDisplayChaser）
	_chaser.on_weather_event(event_type, payload)

	if _debug_overlay:
		_debug_overlay.broadcast_event(event_type, payload)

	if _event_log:
		_event_log.on_world_event(event_type, payload)


## 响应分发：按 request_type 处理 get_chunks（缓存/建网格/卸载越界）、get_weather（日出日落等）、
## player_state/player_move（权威位置吸附）、entity_snapshot（本地玩家实体）、
## terminal_cmd/save_snapshot/save_list；响应同时广播给调试分区。
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

				# 陈旧响应（卸载/世界重置后到达）：丢弃
				if _stream_machine.should_drop_response(key):
					continue

				# 数据缓存：字段或完整数据（响应后覆盖旧值，两种响应同一字典）
				var prev_chunk: Variant = _chunks.get(key)
				_chunks[key] = chunk

				# 真值刷新响应（周期拉取的已加载 chunk）：只换真值，不重建地形；
				# 刷新失败恢复旧缓存（含已解码地形数据，防缓存损坏）
				if _refresh_pending.has(key):
					_refresh_pending.erase(key)
					if has_tiles:
						if not _refresh_chunk_states(key, chunk) and prev_chunk is Dictionary:
							_chunks[key] = prev_chunk
					elif prev_chunk is Dictionary:
						# 刷新响应异常缺 tile 段（后端字段版响应）：缓存已被
						# 覆盖为无地形字典，恢复旧缓存防 BUILT chunk 数据丢失
						_chunks[key] = prev_chunk
					continue

				# 字段版响应：存数据，保持 FIELD_REQUESTED（流循环限流发完整请求）
				if not has_tiles:
					_stream_machine.on_field_response(key)
					continue

				# 完整版响应：解码 tile 数据 → RECEIVED → 立即尝试构建
				var tiles_raw: PackedByteArray = Marshalls.base64_to_raw(str(chunk.get("tiles_b64", "")))
				var expected: int = _TILE_BLOB_HEADER + _TILE_BLOB_TERRAIN + _TILE_BLOB_ELEV * 2 + _tile_blob_state_bytes(_blob_version)
				if tiles_raw.size() != expected:
					# 数据损坏：重新入队完整请求
					_stream_machine.on_full_response(key, false)
					continue
				if tiles_raw.decode_u32(0) != _blob_version:
					# 版本漂移（前后端 BLOB 契约不一致，协议版本握手未覆盖的
					# 流程失误）：重试不可能自愈——报错并标记失败，防止每帧
					# 无限重发完整请求（协议版本应随 BLOB 格式变更同步 bump）
					push_error("MainWorld2D: chunk BLOB 版本 %d 不匹配（期望 %d），契约漂移" % [
						tiles_raw.decode_u32(0), _blob_version])
					chunk["_blob_version_failed"] = true
					_stream_machine.on_full_response(key, false)
					continue
				_stream_machine.on_full_response(key, true)
				var terr: PackedInt32Array = _decode_u16_array(
					tiles_raw.slice(_TILE_BLOB_HEADER, _TILE_BLOB_HEADER + _TILE_BLOB_TERRAIN))
				var elev: PackedFloat32Array = _decode_f32_array(
					tiles_raw.slice(_TILE_BLOB_HEADER + _TILE_BLOB_TERRAIN,
						_TILE_BLOB_HEADER + _TILE_BLOB_TERRAIN + _TILE_BLOB_ELEV))
				var slope: PackedFloat32Array = _decode_f32_array(
					tiles_raw.slice(_TILE_BLOB_HEADER + _TILE_BLOB_TERRAIN + _TILE_BLOB_ELEV, expected))
				var states: Dictionary = _decode_states(tiles_raw)
				chunk["terrain"] = terr
				chunk["elevation"] = elev
				chunk["slope"] = slope
				chunk["states"] = states
				# 显示值追赶注册：新 chunk 初始格子立即应用（已有的雪/冰/湿润可见）
				_apply_state_cells(key, _chaser.set_truth(key, states))
				# 提交后台构建（同步注入的构建器此时已完成，结果由
				# _stream_chunks 末段的 _poll_build_results 挂载；
				# 测试白盒可显式调用 _poll_build_results）
				_try_build_received_chunk(key)
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
			# 全量实体快照：本地玩家独占消费（权威位置/ID），
			# 其余实体（生物/植物/建筑）生成分层 pawn 渲染。
			# 非本地玩家的 PLAYER 实体被跳过不渲染——单玩家后端
			# 的占位语义（多玩家接入后再放开）
			var entities: Array = payload.get("entities", [])
			for ent in entities:
				var ent_id: String = str(ent.get("id", ""))
				var ent_type: String = str(ent.get("entity_type", ""))
				var controller: String = str(ent.get("controller", ""))
				if controller == "PLAYER":
					if not _player_entity_id.is_empty() and ent_id != _player_entity_id:
						continue
					_player_entity_id = ent_id
					_apply_authoritative_position(ent)
				elif not ent_id.is_empty():
					_ensure_pawn(ent_id, ent_type,
						float(ent.get("x", 0.0)), float(ent.get("y", 0.0)))
		"player_move":
			# 权威裁决结果：后端可能钳制越界坐标，本地据此纠正
			_apply_authoritative_position(payload)
		"player_interact":
			# 显式"未实现"标记（后端占位 handler）：功能缺口可见
			if not payload.get("implemented", true) and _event_log:
				_event_log.push_event(tr("event_log.interact_unimplemented"))
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


## 平滑吸附过渡：from → target（XZ 平面），_snap_time < 0 表示无过渡
## （判定与推进计算在 PlayerSync，状态与本帧副作用留在本脚本）。
var _snap_from: Vector3 = Vector3.ZERO
var _snap_target: Vector3 = Vector3.ZERO
var _snap_time: float = -1.0

## 上报 seq 记录（seq → 上报位置）：响应与上报一一对应（TCP 有序），
## 响应携带的 seq 精确对齐到那次上报，据此区分"回声认可"（零纠正）
## 与"钳制偏离"（距离三档纠正）——变速/掉头期间权威位置仍是某次上报
## 位置，位移窗口错位不再误判；历史超限丢最旧，滞后恢复后回退距离判定。
var _report_seq_pos: Dictionary = {}
var _move_report_seq: int = 0


## 重置对账基准与吸附过渡：传送/出生/世界重建后，在途过渡与未回应的
## 上报记录均已失效，必须清空，否则会被插值"撤销"或被误判偏离。
func _reset_authority_state() -> void:
	_snap_time = -1.0
	_report_seq_pos.clear()


func _apply_authoritative_position(payload: Dictionary) -> void:
	"""按后端权威位置纠正玩家（判定在 PlayerSync，副作用在本脚本）。

	客户端预测 + 服务器对账：player_move 响应携带上报 seq（TCP 有序，
	响应与上报一一对应），先按 seq 对齐判定后端是否认可了那次上报：
	  - 认可（回声）：权威位置 ≈ 该次上报位置 → 零纠正——正常滞后
	    （变速/掉头期间位移窗口错位也成立），消除每 0.2s 一次的回跳；
	  - 未认可（钳制/复位，或 player_state/快照等无 seq 响应）：
	    真实裁决偏离 → 按距离三档纠正（PlayerSync.classify_correction）：
	      IGNORE       微小偏差，认可本地；
	      HARD_SNAP    硬吸（传送/读档复位/初始定位）；
	      SMOOTH_START 启动平滑过渡，由 _process 推进。
	"""
	var ax: float = float(payload.get("x", _player_pos.x))
	var az: float = float(payload.get("y", _player_pos.z))

	# 回声判定：权威位置 ≈ 该 seq 的上报位置 → 后端认可，零纠正
	var seq: int = int(payload.get("seq", -1))
	if seq >= 0 and _report_seq_pos.has(seq):
		if PlayerSync.is_echo(ax, az, seq, _report_seq_pos):
			_report_seq_pos.erase(seq)
			return
		_report_seq_pos.erase(seq)

	match PlayerSync.classify_correction(ax, az, _player_pos):
		PlayerSync.Correction.IGNORE:
			return
		PlayerSync.Correction.HARD_SNAP:
			_snap_time = -1.0
			_player_pos.x = ax
			_player_pos.z = az
			_update_player_ground()
			_camera_focus = _world_to_screen(_player_pos)
			_apply_camera_transform()
			_ensure_player()
			# 地形就绪前不显示玩家（出生点加载完成后由 _check_terrain_ready 统一显示）
			_player.visible = _world_visible
			return
		PlayerSync.Correction.SMOOTH_START:
			# 中等差距：平滑过渡（期间输入照常叠加，只做位置补偿）
			_snap_from = _player_pos
			_snap_target = Vector3(ax, _player_pos.y, az)
			_snap_time = 0.0


## ── 流式 chunk 管理 ──────────────────────────────────────

## 流式加载主循环（状态机驱动，结构性无双请求）：
##   1. 卸载远离玩家的 chunk（BUILT 释放节点，任意状态 → UNKNOWN）
##   2. 半径内 UNKNOWN → 批量字段请求（→ FIELD_REQUESTED）
##   3. RECEIVED（完整数据已到）→ 尝试构建网格（材质未就绪保持 RECEIVED，无网络请求）
##   4. FIELD_REQUESTED 且字段数据已到 → 按 MAX_PENDING 限流发完整请求（→ TILE_REQUESTED）
## 末段记录耗时。
func _stream_chunks() -> void:
	if Connection.status != Connection.Status.CONNECTED:
		return

	var t0: int = Time.get_ticks_usec()

	var center := _world_to_chunk(_player_pos.x, _player_pos.z)
	var center_cx: int = center.x
	var center_cy: int = center.y
	var stream_r := _stream_radius()

	_unload_distant_chunks(center_cx, center_cy, stream_r)

	# 1. 半径内 UNKNOWN → 批量字段请求（状态机收集并标记）
	var coords: Array = _stream_machine.collect_field_requests(center, stream_r)
	for c in coords:
		_chunks[Vector2i(c[0], c[1])] = null  # 字段在途占位
	if not coords.is_empty():
		_send_chunk_request(coords, false)

	# 2. RECEIVED → 提交后台构建（CONSTRUCTING；材质就绪即提交，失败保持 RECEIVED 下帧重试）
	for key in _stream_machine.collect_build_candidates():
		_try_build_received_chunk(key)

	# 3. 字段已到（数据 Dictionary）且未请求完整 → 限流发完整请求。
	#    排除 BLOB 版本漂移已标记失败的 chunk（永久性错误，重试不自愈）
	for key in _stream_machine.select_full_requests(
			func(k): return _chunks.get(k) is Dictionary and not _chunks[k].get("_blob_version_failed", false),
			MAX_PENDING):
		_send_chunk_request([[key.x, key.y]], true)

	# 4. 挂载后台构建完成的结果（陈旧结果在此丢弃）
	_poll_build_results()

	_stream_us = Time.get_ticks_usec() - t0


## 提交 RECEIVED chunk 的后台构建：数据完整且材质就绪才提交（→ CONSTRUCTING）；
## 任一不满足保持 RECEIVED（材质未就绪由流循环下帧重试，数据不全重新入队）。
## 在飞上限（MAX_BUILD_INFLIGHT）内才提交，超出保持 RECEIVED 下帧重试。
func _try_build_received_chunk(key: Vector2i) -> void:
	var chunk = _chunks.get(key)
	if not (chunk is Dictionary):
		return
	var terr: PackedInt32Array = chunk.get("terrain", PackedInt32Array())
	var elev: PackedFloat32Array = chunk.get("elevation", PackedFloat32Array())
	if terr.size() < CHUNK_SIZE * CHUNK_SIZE or elev.size() < CHUNK_SIZE * CHUNK_SIZE:
		# 数据不全（异常响应）：重新入队完整请求而非死等
		_stream_machine.on_full_response(key, false)
		return
	if _building.has(key):
		return
	if _building.size() >= MAX_BUILD_INFLIGHT:
		return
	# 接缝上下文：邻居已加载时提取紧邻边条（thread-safe 快照，提交时点）
	var neighbors: Dictionary = _collect_neighbor_context(key)
	_build_seq += 1
	_building[key] = _build_seq
	_stream_machine.mark_constructing(key)
	tile_builder.call(key, terr, elev, neighbors, _build_seq)


## 收集相邻 chunk 的紧邻边条数据（方向 → {terrain, elevation} 长度 CS 数组）。
## 仅当邻居已加载（含完整 terrain/elevation 数组）时提供，缺失方向省略——
## 五信号边界判定回退无邻居语义（见 TerrainTileBuilder 邻居契约）。
## 在提交时刻主线程快照：工作线程读取期间邻居缓存可能更新/淘汰，
## 本快照保证线程安全（PackedArray 写时复制）。
func _collect_neighbor_context(key: Vector2i) -> Dictionary:
	var ctx: Dictionary = {}
	_collect_edge(ctx, "west", Vector2i(key.x - 1, key.y), "east")
	_collect_edge(ctx, "east", Vector2i(key.x + 1, key.y), "west")
	_collect_edge(ctx, "north", Vector2i(key.x, key.y - 1), "south")
	_collect_edge(ctx, "south", Vector2i(key.x, key.y + 1), "north")
	return ctx


## 提取邻居紧邻本 chunk 的边条。edge 为邻居面向本 chunk 的边：
## 西邻的东侧列 / 东邻的西侧列 / 北邻的南侧行 / 南邻的北侧行。
func _collect_edge(ctx: Dictionary, dir: String, nkey: Vector2i,
		edge: String) -> void:
	var nchunk: Variant = _chunks.get(nkey)
	if not (nchunk is Dictionary):
		return
	var nterr: PackedInt32Array = nchunk.get("terrain", PackedInt32Array())
	var nelev: PackedFloat32Array = nchunk.get("elevation", PackedFloat32Array())
	if nterr.size() < CHUNK_SIZE * CHUNK_SIZE \
			or nelev.size() < CHUNK_SIZE * CHUNK_SIZE:
		return
	var edge_terr := PackedInt32Array()
	edge_terr.resize(CHUNK_SIZE)
	var edge_elev := PackedFloat32Array()
	edge_elev.resize(CHUNK_SIZE)
	for i in CHUNK_SIZE:
		match edge:
			"east":  # 邻居东侧列（x=CS-1）邻接本 chunk 西边
				edge_terr[i] = nterr[i * CHUNK_SIZE + (CHUNK_SIZE - 1)]
				edge_elev[i] = nelev[i * CHUNK_SIZE + (CHUNK_SIZE - 1)]
			"west":  # 邻居西侧列（x=0）邻接本 chunk 东边
				edge_terr[i] = nterr[i * CHUNK_SIZE]
				edge_elev[i] = nelev[i * CHUNK_SIZE]
			"south": # 邻居南侧行（z=CS-1）邻接本 chunk 北边
				edge_terr[i] = nterr[(CHUNK_SIZE - 1) * CHUNK_SIZE + i]
				edge_elev[i] = nelev[(CHUNK_SIZE - 1) * CHUNK_SIZE + i]
			"north": # 邻居北侧行（z=0）邻接本 chunk 南边
				edge_terr[i] = nterr[i]
				edge_elev[i] = nelev[i]
	ctx[dir] = {"terrain": edge_terr, "elevation": edge_elev}


## 默认 tile 层构建器：WorkerThreadPool 后台构建（纯数据计算，不碰场景树、
## RenderingServer 与材质），完成后结果入队（Mutex 保护），
## 由主线程 _poll_build_results 创建 TileMapLayer 并挂载。TileSet 就绪检查在
## 提交处完成（_try_build_received_chunk 已懒加载），builder 不接收 TileSet——
## 资源永不跨线程。
func _default_tile_builder(key: Vector2i, terr: PackedInt32Array, elev: PackedFloat32Array,
		neighbors: Dictionary, seq: int) -> void:
	WorkerThreadPool.add_task(_tile_build_task.bind(key, terr, elev, neighbors, seq))


## 后台构建任务：只生成 tile 层数据（TerrainTileBuilder.build_cells，纯数组运算），
## 不创建 TileMapLayer/TileSet——资源与场景树仅在主线程 _poll_build_results
## 中使用，规避退出时工作线程访问已销毁的 RenderingServer。
## 契约：terr/elev/neighbors 的引用主线程绝不原地修改（只整体替换/清除
## chunk 条目），PackedArray 写时复制保证工作线程读取期间数据不被破坏。
func _tile_build_task(key: Vector2i, terr: PackedInt32Array, elev: PackedFloat32Array,
		neighbors: Dictionary, seq: int) -> void:
	var cells: Dictionary = TerrainTileBuilder.build_cells(terr, elev, neighbors)
	_build_mutex.lock()
	_build_results.append([key, cells, seq])
	_build_mutex.unlock()


## 主线程轮询后台构建结果并挂载：序号失配（重连后新任务取代）或状态已非
## CONSTRUCTING（卸载/断线降级）的陈旧结果丢弃；有效结果主线程创建
## TileMapLayer 并 set_cells 后交 _mount_built_chunk。
func _poll_build_results() -> void:
	_build_mutex.lock()
	var results := _build_results.duplicate()
	_build_results.clear()
	_build_mutex.unlock()
	for r in results:
		var key: Vector2i = r[0]
		var cells: Dictionary = r[1]
		var seq: int = r[2]
		if _building.get(key) != seq:
			continue  # 陈旧任务（重建后新任务已取代）
		_building.erase(key)
		_mount_built_chunk(key, cells)


## 推进状态显示值追赶：周期刷新真值（后端 states 演化：降雪/结冰/解冻）
## 后由 chaser 逐帧抽样收敛显示值，变化格子即时更新到状态动态层。
func _process_state_chase(delta: float) -> void:
	_state_refresh_timer += delta
	if _state_refresh_timer >= STATE_REFRESH_INTERVAL:
		_state_refresh_timer = 0.0
		_refresh_state_truth()
	var changed: Dictionary = _chaser.advance(delta)
	for key in changed:
		_apply_state_cells(key, changed[key])


## 周期拉取已加载（BUILT）chunk 的完整响应换真值（限流单周期条数，
## 在途去重）。响应在 _handle_response 走刷新分支（只换真值，不重建地形）。
func _refresh_state_truth() -> void:
	var center: Vector2i = _world_to_chunk(_player_pos.x, _player_pos.z)
	var sent: int = 0
	for dx in range(-STATE_REFRESH_RADIUS, STATE_REFRESH_RADIUS + 1):
		for dy in range(-STATE_REFRESH_RADIUS, STATE_REFRESH_RADIUS + 1):
			var key := center + Vector2i(dx, dy)
			if _stream_machine.get_state(key) != ChunkState.BUILT:
				continue
			if _refresh_pending.has(key):
				continue
			if sent >= STATE_REFRESH_MAX_PER_CYCLE:
				return
			_refresh_pending[key] = true
			sent += 1
			_send_chunk_request([[key.x, key.y]], true)


## 刷新响应的状态段落地：解码完整 BLOB（terrain/elevation/slope/states——
## 与初次完整响应同形状，刷新替换的缓存字典保持完整）后替换真值
## （显示值保持，由 chaser 逐帧收敛）。校验失败返回 false——调用方
## 恢复旧缓存（瞬时失败可容忍，不损坏已解码数据）。
##
## Returns:
##     true = 解码成功且真值已更新；false = BLOB 损坏/版本漂移（缓存未动）。
func _refresh_chunk_states(key: Vector2i, chunk: Dictionary) -> bool:
	var tiles_raw: PackedByteArray = Marshalls.base64_to_raw(str(chunk.get("tiles_b64", "")))
	var expected: int = _TILE_BLOB_HEADER + _TILE_BLOB_TERRAIN + _TILE_BLOB_ELEV * 2 \
			+ _tile_blob_state_bytes(_blob_version)
	if tiles_raw.size() != expected or tiles_raw.decode_u32(0) != _blob_version:
		return false
	chunk["terrain"] = _decode_u16_array(
		tiles_raw.slice(_TILE_BLOB_HEADER, _TILE_BLOB_HEADER + _TILE_BLOB_TERRAIN))
	chunk["elevation"] = _decode_f32_array(
		tiles_raw.slice(_TILE_BLOB_HEADER + _TILE_BLOB_TERRAIN,
			_TILE_BLOB_HEADER + _TILE_BLOB_TERRAIN + _TILE_BLOB_ELEV))
	chunk["slope"] = _decode_f32_array(
		tiles_raw.slice(_TILE_BLOB_HEADER + _TILE_BLOB_TERRAIN + _TILE_BLOB_ELEV, expected))
	chunk["states"] = _decode_states(tiles_raw)
	_chaser.set_truth(key, chunk["states"])
	return true


## 把 chaser 的变化格子应用到状态动态层（转发 StateLayerManager）。
## 格子为 [cell_pos(Vector2i), atlas_col]，col = -1 擦除。
func _apply_state_cells(key: Vector2i, cells: Array) -> void:
	_state_layers_mgr.apply_cells(key, cells,
		_lazy_layer_tile_set(TerrainTileBuilder.LAYER_STATES))


## 计算流式加载半径（chunk 格数）：可视半径 ×1.5 向上取整，下限为 STREAM_MARGIN。
##
## Returns:
##     以玩家 chunk 为中心的流式半径。
func _stream_radius() -> int:
	var visible_radius: float = _compute_visible_radius() * 1.5
	var radius: int = ceili(visible_radius / float(CHUNK_SIZE))
	return maxi(STREAM_MARGIN, radius)


func _compute_visible_radius() -> float:
	"""相机在 2D 正俯视、指定缩放下可视地面的半对角线半径（世界 tile，转发 CameraRig）。"""
	var view: Vector2 = Vector2.ZERO
	if _camera and _camera.get_viewport():
		view = _camera.get_viewport().get_visible_rect().size
	return _camera_rig.visible_radius(_camera_zoom.x, view)


## 发送 get_chunks 请求（批量字段版或单块完整版，恒开 force_fields）。
##
## Args:
##     coords: 请求的 chunk 坐标数组 [[cx, cy], ...]。
##     include_tiles: true = 含地形/高程完整数据，false = 仅字段。
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


## 卸载远离玩家的 chunk（距中心超出流半径 + 卸载余量 + 在途缓冲）：
## 释放地形节点并清空状态与数据（BUILT/RECEIVED → UNKNOWN，在途请求作废）。
## 卸载判定统一在此处（每帧）：响应处理不再因越界丢弃数据——
## 缓冲圈内的在途响应到达后正常缓存/构建，越过缓冲圈才作废。
func _unload_distant_chunks(center_cx: int, center_cy: int, stream_r: int) -> void:
	var unload_r := stream_r + UNLOAD_MARGIN + UNLOAD_BUFFER
	for key in _stream_machine.keys():
		var cx: int = key.x
		var cy: int = key.y
		if abs(cx - center_cx) > unload_r or abs(cy - center_cy) > unload_r:
			_forget_chunk(key)
			print("MainWorld2D: unloaded chunk (%d,%d)" % [cx, cy])


## 彻底遗忘一个 chunk：释放全部层节点（地形/水面/五信号层/状态动态层）、
## 清状态与数据（→ UNKNOWN）；在飞构建登记与真值刷新在途一并清除
## （其结果到达时按序号失配/陈旧丢弃）。
func _forget_chunk(key: Vector2i) -> void:
	var node_names := [
		"Chunk_%d_%d" % [key.x, key.y],
		"Water_%d_%d" % [key.x, key.y],
		"Cliff_%d_%d" % [key.x, key.y],
		"Shadow_%d_%d" % [key.x, key.y],
		"Decor_%d_%d" % [key.x, key.y],
		"Contour_%d_%d" % [key.x, key.y],
	]
	for node_name in node_names:
		if _terrain_parent and _terrain_parent.has_node(NodePath(node_name)):
			_terrain_parent.get_node(NodePath(node_name)).queue_free()
		elif _water_parent and _water_parent.has_node(NodePath(node_name)):
			_water_parent.get_node(NodePath(node_name)).queue_free()
	_state_layers_mgr.erase(key)
	_chaser.forget(key)
	_refresh_pending.erase(key)
	_stream_machine.forget(key)
	_chunks.erase(key)
	_building.erase(key)


## 服务端错误处理：打印错误信息；快照请求失败时清空回查文件并在暂停菜单显示失败原因；
## get_chunks 失败时清空真值刷新在途登记（否则该 chunk 被在途表永久挡住不刷新）。
func _handle_error(message: Dictionary) -> void:
	var error_msg: String = message.get("error", "unknown error")
	push_error("MainWorld2D: server error: %s" % error_msg)
	if message.get("request_type", "") == "get_chunks":
		_refresh_pending.clear()
	if message.get("request_type", "") == SaveApi.SNAPSHOT and _pause_menu:
		_save_file = ""
		_pause_menu.show_status(tr("ui.pause.save_failed").format({"reason": error_msg}), true)


## 请求玩家所在 chunk 的天气数据（连接未建立时跳过）。
func _query_weather() -> void:
	if Connection.status != Connection.Status.CONNECTED:
		return
	var chunk: Vector2i = _world_to_chunk(_player_pos.x, _player_pos.z)
	Connection.send({
		"type": "request",
		"request_type": "get_weather",
		"payload": {"chunks": [[chunk.x, chunk.y]]},
	})


## 按游戏时间与天气调制全局色调（转发 LightingController）：太阳高度角驱动
## 昼夜插值，CanvasModulate 颜色随日出日落平滑 ramp。
func _update_lighting() -> void:
	# 局部光源（玩家火炬）：昼夜循环驱动开关——夜晚亮、白天灭。
	# 独立于 CanvasModulate（纯判据，场景缺调制器时火炬仍应工作）
	if _player:
		var torch: PointLight2D = _player.get_node_or_null("PlayerTorch")
		if torch:
			torch.visible = _lighting.is_night(
				_game_hour, _game_minute, _sunrise, _sunset)
	if _canvas_modulate == null:
		return
	_lighting.update(
		_game_hour, _game_minute, _sunrise, _sunset, _sunshine_intensity)
