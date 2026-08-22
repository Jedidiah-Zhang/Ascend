"""生成 chunk 的 2D tile 层数据 — 后台线程安全的纯数据计算。

从 terrain_mesh_builder.gd 迁移（3D ArrayMesh → 2D TileMapLayer）：不再生成
顶点/法线/UV/顶点色，改为输出每个 TileMapLayer 的 cell 数组（set_cell 形状）。
海拔不做几何抬升——"五信号"（地形色/崖壁贴片/固定方向投影/装饰密度/
等高线调试层，见视觉风格设计文档）全部在此类内以纯数据计算表达。

层划分：
  - terrain：陆地 terrain_id → terrain atlas 列索引（第一层级信号）
  - water：浅水/深水 → water atlas 列索引（半透明水面，独立层）
  - cliff：高侧边缘崖壁贴片（相邻 tile 海拔差 > 阈值）
  - shadow：固定方向投影（西北光照 → 东南侧低 tile 铺半透明阴影）
  - decor：海拔越高装饰越密（确定性哈希，非随机）+ 雪线雪顶
  - contour：等高线调试层（500m 间隔，默认关闭，挂调试面板）

地形类型映射与后端 ascend/space/terrain.py 的 TerrainType 枚举对齐：
0 GRASSLAND / 1 SAND / 2 FERTILE_SOIL / 3 ROCK / 4 STEEP_SLOPE /
5 MOUNTAIN_PEAK / 6 SHALLOW_WATER / 7 DEEP_WATER / 8 MARSH。

已知限制：信号只读本 chunk 数据，chunk 边界相邻 tile 的高差/等高线在接缝处
可能不连续（跨 chunk 感知留待后续阶段）。自阶段 6 起支持邻居上下文：
build_cells 可接收 neighbors 参数（各方向的紧邻边条数据），边界判定改读邻居
边条——邻居已加载时接缝连续（否则回退无邻居语义）。
"""

class_name TerrainTileBuilder
extends RefCounted


const CHUNK_SIZE: int = Config.TILE_MAP_SIZE
const TILE_PIXEL_SIZE: int = Config.TILE_PIXEL_SIZE

## terrain_id → terrain atlas 列索引（正俯视下 9 地形各一色块，见 TERRAIN_TILE_COLORS）
const TERRAIN_TO_TILE: PackedInt32Array = [0, 1, 2, 3, 4, 5, 6, 7, 8]

## 与后端 TerrainType 对齐的水域 id
const SHALLOW_WATER_ID: int = 6
const DEEP_WATER_ID: int = 7

## 层名（挂载方按此从 build_cells 返回取数组）
const LAYER_TERRAIN: String = "terrain"
const LAYER_WATER: String = "water"
const LAYER_CLIFF: String = "cliff"
const LAYER_SHADOW: String = "shadow"
const LAYER_DECOR: String = "decor"
const LAYER_CONTOUR: String = "contour"
## 状态动态层（StateDisplayChaser 动态更新，不参与 build_cells——主世界按需挂载）
const LAYER_STATES: String = "states"

## 五信号阈值（Config 同源）
const CLIFF_ELEVATION_DIFF_M: float = Config.CLIFF_ELEVATION_DIFF_M
const SHADOW_ELEVATION_DIFF_M: float = Config.SHADOW_ELEVATION_DIFF_M
const SNOWLINE_ELEVATION_M: float = Config.SNOWLINE_ELEVATION_M
const CONTOUR_INTERVAL_M: float = Config.CONTOUR_INTERVAL_M
const DECOR_ELEVATION_TIERS: Array[float] = Config.DECOR_ELEVATION_TIERS

## 装饰密度（百分比）：海拔档位 [<300, <1000, <2000, ≥2000]
const DECOR_DENSITY_PERCENT: PackedInt32Array = [2, 5, 10, 16]
## 装饰 atlas 列：0 砾石 / 1 岩石 / 2 大岩块 / 3 雪顶（MOUNTAIN_PEAK ≥ 雪线）
const DECOR_TILE_ROCKS: Array[int] = [0, 1, 2]
const DECOR_TILE_SNOWCAP: int = 3

## 地形 atlas 占位纯色（terrain_id 顺序；像素资产未开始，渲染管线先用色块）
## 与旧 terrain_mesh_builder 时代的 _TERRAIN_FALLBACK_COLORS 主色调对齐
const TERRAIN_TILE_COLORS: Array[Color] = [
	Color(0.45, 0.62, 0.35),  # 0 GRASSLAND
	Color(0.85, 0.78, 0.5),   # 1 SAND
	Color(0.35, 0.45, 0.25),  # 2 FERTILE_SOIL
	Color(0.45, 0.42, 0.38),  # 3 ROCK
	Color(0.55, 0.5, 0.35),   # 4 STEEP_SLOPE
	Color(0.5, 0.48, 0.45),   # 5 MOUNTAIN_PEAK
	Color(0.2, 0.6, 0.9),     # 6 SHALLOW_WATER（半透明在水面层另设）
	Color(0.05, 0.2, 0.6),    # 7 DEEP_WATER
	Color(0.4, 0.55, 0.4),    # 8 MARSH
]

## 水面 atlas 占位纯色（半透明，浅水/深水）
const WATER_TILE_COLORS: Array[Color] = [
	Color(0.2, 0.6, 0.9, 0.6),   # 浅水
	Color(0.05, 0.2, 0.6, 0.85), # 深水
]

## 崖壁 atlas 占位纯色（暗岩边缘，单一样式；方向变体留待资产阶段）
const CLIFF_TILE_COLORS: Array[Color] = [
	Color(0.25, 0.2, 0.15, 0.95),
]

## 固定方向投影 atlas 占位纯色（半透明深色贴片）
const SHADOW_TILE_COLORS: Array[Color] = [
	Color(0.05, 0.08, 0.12, 0.35),
]

## 装饰 atlas 占位纯色：砾石 / 岩石 / 大岩块 / 雪顶
const DECOR_TILE_COLORS: Array[Color] = [
	Color(0.6, 0.58, 0.52, 1.0),
	Color(0.5, 0.47, 0.42, 1.0),
	Color(0.42, 0.38, 0.33, 1.0),
	Color(0.95, 0.96, 0.98, 1.0),
]

## 等高线调试层 atlas 占位纯色（高对比亮色，调试期醒目）
const CONTOUR_TILE_COLORS: Array[Color] = [
	Color(0.95, 0.9, 0.2, 0.8),
]

## 状态动态层 atlas 占位纯色（StateDisplayChaser 量化 4 档 × 3 状态）：
## 湿润（土壤加深色调）→ 覆雪（厚度白）→ 结冰（冰面淡蓝），
## 档位 0 不渲染（擦除），档位越高越浓（alpha 递增）。
const STATES_TILE_COLORS: Array[Color] = [
	Color(0.25, 0.18, 0.1, 0.1),   # moisture 档 0（未使用）
	Color(0.25, 0.18, 0.1, 0.22),  # moisture 档 1
	Color(0.25, 0.18, 0.1, 0.32),  # moisture 档 2
	Color(0.25, 0.18, 0.1, 0.42),  # moisture 档 3
	Color(0.98, 0.98, 0.99, 0.3),  # snow 档 0（未使用）
	Color(0.98, 0.98, 0.99, 0.5),  # snow 档 1
	Color(0.98, 0.98, 0.99, 0.7),  # snow 档 2
	Color(0.98, 0.98, 0.99, 0.9),  # snow 档 3
	Color(0.75, 0.85, 0.95, 0.3),  # ice 档 0（未使用）
	Color(0.75, 0.85, 0.95, 0.5),  # ice 档 1
	Color(0.75, 0.85, 0.95, 0.7),  # ice 档 2
	Color(0.75, 0.85, 0.95, 0.9),  # ice 档 3
]


## 构建 chunk 的 tile 层 cell 数据（后台线程安全）：五信号全部在单次调用内
## 完成（逐 tile 单遍 + 邻居探测，均为纯数组运算，无随机源——装饰用
## 确定性哈希，结果跨线程/跨调用稳定）。纯数据不创建 TileMapLayer/材质。
##
## Args:
##     terrain: 长度 CHUNK_SIZE² 的 terrain_id 数组（行优先，越界补 0）。
##     elevation: 长度 CHUNK_SIZE² 的海拔数组（米，行优先）。
##     neighbors: 可选邻居上下文（见 collect 契约）：{方向: {terrain, elevation}}，
##         方向为 west/east/north/south（本 chunk 的方向），值为紧邻本 chunk
##         边的长度 CHUNK_SIZE 边条数组（顺序与本地轴对齐）。缺失方向 = 该侧
##         无已加载邻居，边界判定回退无邻居语义（接缝不连续但安全）。
##
## Returns:
##     六层 cell 数组字典：LAYER_TERRAIN / LAYER_WATER / LAYER_CLIFF /
##     LAYER_SHADOW / LAYER_DECOR / LAYER_CONTOUR，元素为
##     TileMapLayer.set_cell 参数 [cell_coords(Vector2i), source_id,
##     atlas_coords(Vector2i)]。
static func build_cells(terrain: PackedInt32Array,
		elevation: PackedFloat32Array, neighbors: Dictionary = {}) -> Dictionary:
	var cells: Dictionary = {
		LAYER_TERRAIN: [],
		LAYER_WATER: [],
		LAYER_CLIFF: [],
		LAYER_SHADOW: [],
		LAYER_DECOR: [],
		LAYER_CONTOUR: [],
	}
	var CS: int = CHUNK_SIZE
	var elev_ok: bool = elevation.size() >= CS * CS

	for z in CS:
		for x in CS:
			var idx: int = z * CS + x
			var terrain_id: int = terrain[idx] if idx < terrain.size() else 0
			if terrain_id < 0 or terrain_id >= TERRAIN_TO_TILE.size():
				continue
			var cell_pos := Vector2i(x, z)
			if terrain_id == SHALLOW_WATER_ID or terrain_id == DEEP_WATER_ID:
				cells[LAYER_WATER].append([
					cell_pos, 0, Vector2i(terrain_id - SHALLOW_WATER_ID, 0),
				])
				continue
			cells[LAYER_TERRAIN].append([
				cell_pos, 0, Vector2i(TERRAIN_TO_TILE[terrain_id], 0),
			])
			if not elev_ok:
				continue
			var elev: float = elevation[idx]
			if _has_cliff(terrain, elevation, x, z, idx, neighbors):
				cells[LAYER_CLIFF].append([cell_pos, 0, Vector2i(0, 0)])
			if _receives_shadow(terrain, elevation, x, z, idx, neighbors):
				cells[LAYER_SHADOW].append([cell_pos, 0, Vector2i(0, 0)])
			var decor := _decor_at(terrain_id, elev, x, z, idx)
			if decor >= 0:
				cells[LAYER_DECOR].append([cell_pos, 0, Vector2i(decor, 0)])
			if _on_contour(elevation, x, z, idx, neighbors):
				cells[LAYER_CONTOUR].append([cell_pos, 0, Vector2i(0, 0)])
	return cells


## 取方向邻居在本地索引 i 处的海拔（缺失方向返回 -1e9，阈值比较必不触发）。
static func _neigh_elev(neighbors: Dictionary, dir: String, i: int) -> float:
	if not neighbors.has(dir):
		return -1e9
	return float(neighbors[dir]["elevation"][i])


## 取方向邻居在本地索引 i 处的 terrain_id（缺失方向返回 -1，非水域判定不触发）。
static func _neigh_terr(neighbors: Dictionary, dir: String, i: int) -> int:
	if not neighbors.has(dir):
		return -1
	return int(neighbors[dir]["terrain"][i])


## 崖壁判定：本 tile 为高侧，且任一方向（东/西/南/北）邻居海拔差 >
## CLIFF_ELEVATION_DIFF_M —— 高侧边缘无论朝哪个方向都画崖壁。
## 边界处优先读邻居上下文（已加载邻居接缝连续）；无邻居回退无邻居语义。
static func _has_cliff(terrain: PackedInt32Array, elevation: PackedFloat32Array,
		x: int, z: int, idx: int, neighbors: Dictionary) -> bool:
	var CS: int = CHUNK_SIZE
	var elev: float = elevation[idx]
	# 西邻居
	if x - 1 >= 0:
		var w_idx: int = z * CS + x - 1
		if w_idx < terrain.size() and w_idx < elevation.size() \
				and not _is_water(terrain[w_idx]) \
				and elev - elevation[w_idx] > CLIFF_ELEVATION_DIFF_M:
			return true
	elif neighbors.has("west") and not _is_water(_neigh_terr(neighbors, "west", z)) \
			and elev - _neigh_elev(neighbors, "west", z) > CLIFF_ELEVATION_DIFF_M:
		return true
	# 东邻居
	if x + 1 < CS:
		var e_idx: int = z * CS + x + 1
		if e_idx < terrain.size() and e_idx < elevation.size() \
				and not _is_water(terrain[e_idx]) \
				and elev - elevation[e_idx] > CLIFF_ELEVATION_DIFF_M:
			return true
	elif neighbors.has("east") and not _is_water(_neigh_terr(neighbors, "east", z)) \
			and elev - _neigh_elev(neighbors, "east", z) > CLIFF_ELEVATION_DIFF_M:
		return true
	# 北邻居
	if z - 1 >= 0:
		var n_idx: int = (z - 1) * CS + x
		if n_idx < terrain.size() and n_idx < elevation.size() \
				and not _is_water(terrain[n_idx]) \
				and elev - elevation[n_idx] > CLIFF_ELEVATION_DIFF_M:
			return true
	elif neighbors.has("north") and not _is_water(_neigh_terr(neighbors, "north", x)) \
			and elev - _neigh_elev(neighbors, "north", x) > CLIFF_ELEVATION_DIFF_M:
		return true
	# 南邻居
	if z + 1 < CS:
		var s_idx: int = (z + 1) * CS + x
		if s_idx < terrain.size() and s_idx < elevation.size() \
				and not _is_water(terrain[s_idx]) \
				and elev - elevation[s_idx] > CLIFF_ELEVATION_DIFF_M:
			return true
	elif neighbors.has("south") and not _is_water(_neigh_terr(neighbors, "south", x)) \
			and elev - _neigh_elev(neighbors, "south", x) > CLIFF_ELEVATION_DIFF_M:
		return true
	return false


## 固定方向投影：光照从西北来，高差 > SHADOW_ELEVATION_DIFF_M 时阴影落在
## 东南侧低 tile 上——本 tile 的西北邻居更高即被投影。边界处读邻居上下文。
static func _receives_shadow(terrain: PackedInt32Array, elevation: PackedFloat32Array,
		x: int, z: int, idx: int, neighbors: Dictionary) -> bool:
	var CS: int = CHUNK_SIZE
	var elev: float = elevation[idx]
	# 西邻居（本 tile 西侧更高 → 投影落本 tile）
	if x - 1 >= 0:
		var w_idx: int = z * CS + x - 1
		if w_idx < terrain.size() and w_idx < elevation.size() \
				and not _is_water(terrain[w_idx]) \
				and elevation[w_idx] - elev > SHADOW_ELEVATION_DIFF_M:
			return true
	elif neighbors.has("west") and not _is_water(_neigh_terr(neighbors, "west", z)) \
			and _neigh_elev(neighbors, "west", z) - elev > SHADOW_ELEVATION_DIFF_M:
		return true
	# 北邻居
	if z - 1 >= 0:
		var n_idx: int = (z - 1) * CS + x
		if n_idx < terrain.size() and n_idx < elevation.size() \
				and not _is_water(terrain[n_idx]) \
				and elevation[n_idx] - elev > SHADOW_ELEVATION_DIFF_M:
			return true
	elif neighbors.has("north") and not _is_water(_neigh_terr(neighbors, "north", x)) \
			and _neigh_elev(neighbors, "north", x) - elev > SHADOW_ELEVATION_DIFF_M:
		return true
	return false


## 装饰判定：海拔越高越密（确定性哈希决定落点，非随机源——同 chunk 数据
## 任何线程/任何次构建结果一致）；MOUNTAIN_PEAK 且海拔 ≥ 雪线 → 雪顶。
## Returns:
##     decor atlas 列索引；无装饰返回 -1。
static func _decor_at(terrain_id: int, elev: float, x: int, z: int, idx: int) -> int:
	var tier: int = 3
	for i in DECOR_ELEVATION_TIERS.size():
		if elev < DECOR_ELEVATION_TIERS[i]:
			tier = i
			break
	if terrain_id == 5 and elev >= SNOWLINE_ELEVATION_M:
		return DECOR_TILE_SNOWCAP
	var density: int = DECOR_DENSITY_PERCENT[tier]
	if _decor_hash(x, z, idx) % 100 >= density:
		return -1
	return DECOR_TILE_ROCKS[_decor_hash(z, x, idx) % DECOR_TILE_ROCKS.size()]


## 确定性整数哈希（位置混合）：跨线程/跨构建稳定（Godot hash() 不保证
## 跨版本稳定，视觉缓存也不可依赖全局随机源——chunk 装载顺序无影响）。
static func _decor_hash(a: int, b: int, c: int) -> int:
	var h: int = a * 73856093 ^ b * 19349663 ^ c * 83492791
	h = (h ^ (h >> 13)) * 1274126177
	return h & 0x7FFFFFFF


## 等高线判定：本 tile 与东/南邻居的海拔跨过 500m 档位边界即落在等高线上
## （500/1000/1500/2000 恰对齐后端 ALPINE 2000m 阈值）。边界处读邻居上下文。
static func _on_contour(elevation: PackedFloat32Array, x: int, z: int, idx: int,
		neighbors: Dictionary) -> bool:
	var CS: int = CHUNK_SIZE
	var band: int = floori(elevation[idx] / CONTOUR_INTERVAL_M)
	if x + 1 < CS:
		if floori(elevation[z * CS + x + 1] / CONTOUR_INTERVAL_M) != band:
			return true
	elif neighbors.has("east") \
			and floori(_neigh_elev(neighbors, "east", z) / CONTOUR_INTERVAL_M) != band:
		return true
	if z + 1 < CS:
		if floori(elevation[(z + 1) * CS + x] / CONTOUR_INTERVAL_M) != band:
			return true
	elif neighbors.has("south") \
			and floori(_neigh_elev(neighbors, "south", x) / CONTOUR_INTERVAL_M) != band:
		return true
	return false


static func _is_water(terrain_id: int) -> bool:
	return terrain_id == SHALLOW_WATER_ID or terrain_id == DEEP_WATER_ID


## 由色表生成程序化占位 atlas 纹理（TILE_PIXEL_SIZE 方 tile 横向排布，
## 纯色占位块——像素风渲染管线先通，资产后补替换此纹理即可；
## 过滤模式沿用场景默认（未显式设置 texture_filter））。
##
## Args:
##     colors: 每 tile 的纯色（长度 = atlas 列数）。
##
## Returns:
##     供 TileSetAtlasSource 使用的 ImageTexture。
static func make_placeholder_atlas(colors: Array[Color]) -> ImageTexture:
	var n: int = colors.size()
	var img := Image.create(n * TILE_PIXEL_SIZE, TILE_PIXEL_SIZE, false,
		Image.FORMAT_RGBA8)
	for i in n:
		var col: Color = colors[i]
		for py in TILE_PIXEL_SIZE:
			for px in TILE_PIXEL_SIZE:
				img.set_pixel(i * TILE_PIXEL_SIZE + px, py, col)
	return ImageTexture.create_from_image(img)


## 由色表构建单源 TileSet（占位 atlas + 每色一个 tile，source id=0）。
## 主线程调用（创建资源）；挂载方每 chunk 复用同一 TileSet。
##
## Args:
##     colors: 每 tile 的纯色。
##
## Returns:
##     配置好的 TileSet（tile_size = TILE_PIXEL_SIZE）。
static func make_tile_set(colors: Array[Color]) -> TileSet:
	var ts := TileSet.new()
	ts.tile_size = Vector2i(TILE_PIXEL_SIZE, TILE_PIXEL_SIZE)
	var src := TileSetAtlasSource.new()
	src.texture = make_placeholder_atlas(colors)
	src.texture_region_size = Vector2i(TILE_PIXEL_SIZE, TILE_PIXEL_SIZE)
	ts.add_source(src, 0)
	for i in colors.size():
		src.create_tile(Vector2i(i, 0))
	return ts