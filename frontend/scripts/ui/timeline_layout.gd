"""时间线分叉布局 — 快照血缘的纯逻辑树构建（存档选择页时间线视图）。

输入快照数组（save_list 响应条目，含 file/parent/seq/game_time/saved_at/suffix）
与活目录来源（live_origin / live_game_time），输出分叉树节点与边：

  - 快照永远从活目录派生：parent = 创建时活目录来源（回滚目标快照，
    "" = 世界初始），因此回滚后保存的新快照形成分叉
  - 同一来源链上的兄弟快照（parent 相同且非分叉）按时间顺序串成链
  - 「当前时间点」= 活目录节点（is_live）：从 live_origin 派生；
    无来源时挂在链尾（从未回滚 = 与初始同线）。**当 live_origin
    是树内的 auto 记录时省略该伪节点，并把该记录标记 is_live**——
    auto 记录即当前线的滚动记录（当前位置），既保留当前位置提示
    又不重复挂点（如手动保存后「手动点 + auto 点 + 当前点」三点）
  - auto 节点（当前线的滚动记录：恒为叶子，手动保存/离开线时原地
    晋升/冻结）参与时间线，隐藏会让旧分支在跳转后"消失"；
    quit 等其它来源仍可过滤

排序键 = 血缘 seq（后端权威的单调创建顺序，回滚后游戏时间倒退
不影响它）：save_order_ids 编号、链内串链、兄弟排序全部统一，
saved_at/game_time 仅作展示字段（旧档缺 seq 时回退 saved_at 排序）。
LIVE 节点取伪 seq = max+1，恒排最后——悬空来源时也只会串在链尾，
不可能成为链头（防护：LIVE 的 time 若取 0 会排最前导致树反转）。

布局：深度 = 距链头代数（x 轴），槽位 = 子树纵向区间（y 轴），
经典 tidy 树分配：叶子占一个槽位，内部节点取子节点中点。
纯逻辑 RefCounted，无节点依赖，可直接单元测试。
"""

class_name TimelineLayout
extends RefCounted

## 活目录（当前时间点）节点 ID
const LIVE_ID: String = "@live"


## 排序键：血缘 seq（创建顺序）主键，saved_at/game_time 兜底（旧档无 seq）。
static func _sort_key(snap: Dictionary) -> Dictionary:
	return {
		"seq": int(snap.get("seq", 0)),
		"saved_at": float(snap.get("saved_at", 0.0)),
		"time": int(snap.get("game_time", 0)),
	}


## 排序比较器：按 seq → saved_at → time（游戏时间，_sort_key 产出）逐级比较（sort_custom 用）。
##
## Returns:
##     a 是否应排在 b 之前。
static func _sort_cmp(a: Dictionary, b: Dictionary) -> bool:
	if a["seq"] != b["seq"]:
		return a["seq"] < b["seq"]
	if a["saved_at"] != b["saved_at"]:
		return a["saved_at"] < b["saved_at"]
	return a["time"] < b["time"]


## 保存顺序的节点 id 列表（编号数据源，全端共用保证编号一致）。
##
## 以血缘 seq（真实创建顺序）为主键——回滚后游戏时间倒退不会影响它；
## 旧档（无 seq）回退到 saved_at，同秒以游戏时间兜底。
##
## Args:
##     snapshots: 快照条目数组（含 file/seq/saved_at/game_time）。
##
## Returns:
##     按保存顺序排列的 file 数组（脏条目跳过）。
static func save_order_ids(snapshots: Array) -> Array:
	var entries: Array = []
	for s in snapshots:
		if not s is Dictionary:
			continue
		var file: String = str(s.get("file", ""))
		if file.is_empty():
			continue
		var key: Dictionary = _sort_key(s)
		entries.append({
			"id": file,
			"seq": key["seq"],
			"saved_at": key["saved_at"],
			"time": key["time"],
		})
	entries.sort_custom(_sort_cmp)
	var ids: Array = []
	for e in entries:
		ids.append(e["id"])
	return ids


## 构建时间线分叉树。
##
## Args:
##     snapshots: 快照条目数组（含 file/parent/seq/game_time/saved_at/suffix）。
##     live_game_time: 活目录当前游戏时间（世界摘要 game_time，仅展示）。
##     live_origin: 活目录来源快照 file（"" = 世界初始）。
##     keep_suffixes: 参与时间线的快照来源（默认 manual + auto——
##         auto 是分支的滚动记录/延续节点，隐藏会使旧分支"消失"）。
##
## Returns:
##     {nodes: [{id, label, time, saved_at, seq, depth, slot, is_live, suffix, children}],
##      edges: [[parent_id, child_id], ...], roots: [id]}
static func build(
	snapshots: Array,
	live_game_time: int,
	live_origin: String = "",
	keep_suffixes: Array = ["manual", "auto"],
) -> Dictionary:
	var keep: Dictionary = {}
	for suffix in keep_suffixes:
		keep[str(suffix)] = true

	# 1. 收集参与节点（过滤来源 + 无 file 脏数据）
	var nodes: Dictionary = {}  # id -> {id, parent, seq, time, saved_at, suffix, is_live}
	var max_seq: int = -1
	for snap in snapshots:
		if not snap is Dictionary:
			continue
		if not keep.has(str(snap.get("suffix", ""))):
			continue
		var file: String = str(snap.get("file", ""))
		if file.is_empty():
			continue
		var key: Dictionary = _sort_key(snap)
		max_seq = maxi(max_seq, key["seq"])
		nodes[file] = {
			"id": file,
			"parent": str(snap.get("parent", "")),
			"seq": key["seq"],
			"time": key["time"],
			"saved_at": key["saved_at"],
			"suffix": str(snap.get("suffix", "")),
			"is_live": false,
		}
	# LIVE 伪节点：live_origin 是树内的 auto 记录时省略——该记录
	# 即当前线的滚动记录（当前位置），标记 is_live 供 UI 高亮，
	# 不再挂「当前时间点」伪节点（会重复）；
	# ""（世界初始）/ 悬空来源 / 非 auto 来源（异常态）仍展示。
	# LIVE 伪 seq = max+1：恒排最后，悬空来源时只会串到链尾（不做链头）
	if (
		nodes.has(live_origin)
		and str(nodes[live_origin]["suffix"]) == "auto"
	):
		nodes[live_origin]["is_live"] = true
	else:
		nodes[LIVE_ID] = {
			"id": LIVE_ID,
			"parent": live_origin if nodes.has(live_origin) else "",
			"seq": max_seq + 1,
			"time": live_game_time,
			"saved_at": 0.0,
			"suffix": "live",
			"is_live": true,
		}

	# 2. 布局父节点：真实 parent 存在则用之（分叉）；否则按排序串链
	#    （同一来源的兄弟快照 = 同一条线上的先后点，非分叉）。
	#    防御：绝不从 LIVE 派生（LIVE 必为叶子）
	var layout_parent: Dictionary = {}
	var roots: Array = []
	var by_time: Array = []
	for id in nodes:
		by_time.append({"id": id, "key": _sort_key({
			"seq": nodes[id]["seq"],
			"saved_at": nodes[id]["saved_at"],
			"game_time": nodes[id]["time"],
		})})
	by_time.sort_custom(func(a, b): return _sort_cmp(a["key"], b["key"]))
	for i in by_time.size():
		var id: String = by_time[i]["id"]
		var real_parent: String = nodes[id]["parent"]
		if real_parent.is_empty() or not nodes.has(real_parent):
			if roots.is_empty() or roots[-1] == LIVE_ID:
				layout_parent[id] = ""  # 链头
			else:
				layout_parent[id] = roots[-1]  # 串到上一节点
			roots.append(id)
		else:
			layout_parent[id] = real_parent

	# 3. 孩子表 + 深度（BFS 从链头起）
	var children: Dictionary = {}
	for id in nodes:
		children[id] = []
	for id in nodes:
		var p: String = layout_parent[id]
		if not p.is_empty():
			children[p].append(id)
	for id in children:
		children[id].sort_custom(
			func(a, b): return _sort_cmp(
				_sort_key({
					"seq": nodes[a]["seq"],
					"saved_at": nodes[a]["saved_at"],
					"game_time": nodes[a]["time"],
				}),
				_sort_key({
					"seq": nodes[b]["seq"],
					"saved_at": nodes[b]["saved_at"],
					"game_time": nodes[b]["time"],
				}),
			))

	var depth: Dictionary = {}
	if not roots.is_empty():
		var queue: Array = [roots[0]]
		depth[roots[0]] = 0
		while not queue.is_empty():
			var cur: String = queue.pop_front()
			for child in children[cur]:
				depth[child] = depth[cur] + 1
				queue.append(child)

	# 4. 槽位分配：叶子占一个槽位，内部节点取子树槽位区间中点
	var slot: Dictionary = {}
	if not roots.is_empty():
		var counter: Array = [0]
		_assign_slots(children, roots[0], slot, counter)

	# 5. 输出
	var result_nodes: Array = []
	for id in nodes:
		result_nodes.append({
			"id": id,
			"label": id,
			"time": nodes[id]["time"],
			"saved_at": nodes[id]["saved_at"],
			"seq": nodes[id]["seq"],
			"depth": depth.get(id, 0),
			"slot": slot.get(id, 0),
			"is_live": nodes[id]["is_live"],
			"suffix": nodes[id]["suffix"],
			"children": children[id],
		})
	var result_edges: Array = []
	for id in nodes:
		var p: String = layout_parent[id]
		if not p.is_empty():
			result_edges.append([p, id])
	return {
		"nodes": result_nodes,
		"edges": result_edges,
		"roots": roots,
	}


static func _assign_slots(
	children: Dictionary, root_id: String,
	slot: Dictionary, counter: Array,
) -> Array:
	"""槽位分配（后序递归）：叶子占一个槽位，内部节点取子树区间中点。

	Args:
	    children: id → 有序子节点数组。
	    root_id: 当前子树根。
	    slot: 输出槽位表（引用传递）。
	    counter: [下一个叶子槽位]（引用传递，跨递归共享）。

	Returns:
	    [子树最小槽位, 子树最大槽位]。
	"""
	var kids: Array = children[root_id]
	if kids.is_empty():
		var s: int = counter[0]
		counter[0] += 1
		slot[root_id] = s
		return [s, s]
	var min_s: int = 0
	var max_s: int = 0
	for i in kids.size():
		var r: Array = _assign_slots(children, kids[i], slot, counter)
		if i == 0:
			min_s = r[0]
		max_s = r[1]
	slot[root_id] = (min_s + max_s) * 0.5
	return [min_s, max_s]
