extends GutTest

const TimelineLayout = preload("res://scripts/ui/timeline_layout.gd")


func _snap(file: String, parent: String = "", game_time: int = 0,
		saved_at: float = 0.0, suffix: String = "manual",
		seq: int = 0) -> Dictionary:
	return {
		"file": file, "parent": parent, "game_time": game_time,
		"saved_at": saved_at, "suffix": suffix, "seq": seq,
	}


# ── 线性链（从未回滚） ─────────────────────────────────────

func test_linear_chain_orders_snapshots() -> void:
	"""同一来源的多个快照按时间串成链（深度递增）。"""
	var snaps: Array = [
		_snap("s1", "", 100),
		_snap("s2", "", 200),
		_snap("s3", "", 300),
	]
	var tree: Dictionary = TimelineLayout.build(snaps, 400)
	var depths: Dictionary = {}
	for n in tree["nodes"]:
		depths[n["id"]] = n["depth"]
	assert_eq(depths["s1"], 0, "最早快照为链头")
	assert_eq(depths["s2"], 1)
	assert_eq(depths["s3"], 2)
	assert_eq(depths[TimelineLayout.LIVE_ID], 3, "当前时间点挂在链尾")
	assert_eq(tree["edges"].size(), 3)


func test_real_parent_chain_from_backend() -> void:
	"""后端真实数据（连续保存自动串链）：S2.parent=S1、S3.parent=S2。"""
	var snaps: Array = [
		_snap("s1", "", 100),
		_snap("s2", "s1", 200),
		_snap("s3", "s2", 300),
	]
	var tree: Dictionary = TimelineLayout.build(snaps, 400, "s3")
	var depths: Dictionary = {}
	for n in tree["nodes"]:
		depths[n["id"]] = n["depth"]
	assert_eq(depths["s1"], 0)
	assert_eq(depths["s2"], 1)
	assert_eq(depths["s3"], 2)
	assert_eq(depths[TimelineLayout.LIVE_ID], 3, "当前点从最新快照派生")
	assert_eq(tree["edges"].size(), 3)


func test_live_only_when_no_snapshots() -> void:
	"""无快照的世界只有当前时间点一个节点。"""
	var tree: Dictionary = TimelineLayout.build([], 100)
	assert_eq(tree["nodes"].size(), 1)
	assert_true(tree["nodes"][0]["is_live"])


# ── 分叉（回滚后保存） ─────────────────────────────────────

func test_fork_after_rollback() -> void:
	"""回滚到 s1 后继续玩再保存 → s4 从 s1 分叉（s1 两个子节点）。"""
	var snaps: Array = [
		_snap("s1", "", 100),
		_snap("s2", "", 200),
	]
	var tree: Dictionary = TimelineLayout.build(snaps, 300, "s1")
	var s1: Dictionary = {}
	for n in tree["nodes"]:
		if n["id"] == "s1":
			s1 = n
	assert_eq(s1["children"].size(), 2, "s1 应同时派生 s2 与当前点")
	var live: Dictionary = {}
	for n in tree["nodes"]:
		if n["is_live"]:
			live = n
	assert_eq(live["depth"], 1, "当前点直接从 s1 派生")
	assert_eq(s1["children"], ["s2", TimelineLayout.LIVE_ID], "子节点按时间排序")


func test_unknown_live_origin_chains_at_end() -> void:
	"""live_origin 指向不存在的快照（已过滤/已删）→ 当前点串在链尾。"""
	var snaps: Array = [_snap("s1", "", 100)]
	var tree: Dictionary = TimelineLayout.build(snaps, 200, "ghost")
	var live: Dictionary = {}
	for n in tree["nodes"]:
		if n["is_live"]:
			live = n
	assert_eq(live["depth"], 1, "来源未知时视为同线延续")


func test_live_dangling_origin_never_chains_head() -> void:
	"""历史 bug 回归：LIVE 悬空来源 + 游戏时间 0 时不能成为链头。

	旧实现 LIVE 的 time 取 0（排序最前），悬空来源时成为链头，
	初始快照反成其子节点，树整体反转。
	"""
	var snaps: Array = [_snap("s1", "", 100, 0.0, "manual", 0)]
	var tree: Dictionary = TimelineLayout.build(snaps, 0, "ghost")
	var depths: Dictionary = {}
	for n in tree["nodes"]:
		depths[n["id"]] = n["depth"]
	assert_eq(tree["roots"], ["s1", TimelineLayout.LIVE_ID])
	assert_eq(depths["s1"], 0, "初始快照仍是链头")
	assert_eq(depths[TimelineLayout.LIVE_ID], 1, "当前点串在链尾")


func test_live_dangling_origin_after_newer_snapshots() -> void:
	"""悬空来源 + 更新快照存在时，LIVE 仍串在链尾而非夹在中间。"""
	var snaps: Array = [
		_snap("s1", "", 100, 0.0, "manual", 0),
		_snap("s2", "", 200, 0.0, "manual", 1),
	]
	var tree: Dictionary = TimelineLayout.build(snaps, 0, "ghost")
	var depths: Dictionary = {}
	for n in tree["nodes"]:
		depths[n["id"]] = n["depth"]
	assert_eq(depths["s1"], 0)
	assert_eq(depths["s2"], 1)
	assert_eq(depths[TimelineLayout.LIVE_ID], 2, "LIVE 恒为链尾")


# ── 来源过滤 ───────────────────────────────────────────────

func test_auto_snapshots_visible_quit_filtered() -> void:
	"""自动保护快照参与时间线（分支延续节点，隐藏会让旧分支消失）；
	quit 等其它来源仍过滤。"""
	var snaps: Array = [
		_snap("m1", "", 100, 0.0, "manual"),
		_snap("a1", "m1", 150, 0.0, "auto"),
		_snap("q1", "", 200, 0.0, "quit"),
	]
	var tree: Dictionary = TimelineLayout.build(snaps, 300)
	var ids: Array = []
	var suffix_map: Dictionary = {}
	for n in tree["nodes"]:
		ids.append(n["id"])
		suffix_map[n["id"]] = n["suffix"]
	assert_eq(ids, ["m1", "a1", TimelineLayout.LIVE_ID], "manual + auto 参与，quit 过滤")
	assert_eq(suffix_map["a1"], "auto", "节点应携带来源标识供 UI 区分样式")
	assert_eq(tree["edges"].size(), 2)


func test_keep_suffixes_override() -> void:
	"""调用方可显式收窄来源（如仅 manual）。"""
	var snaps: Array = [
		_snap("m1", "", 100, 0.0, "manual"),
		_snap("a1", "", 150, 0.0, "auto"),
	]
	var tree: Dictionary = TimelineLayout.build(snaps, 300, "", ["manual"])
	var ids: Array = []
	for n in tree["nodes"]:
		ids.append(n["id"])
	assert_eq(ids, ["m1", TimelineLayout.LIVE_ID], "显式仅 manual 时应过滤 auto")


# ── 布局几何 ───────────────────────────────────────────────

func test_fork_branches_get_distinct_slots() -> void:
	"""分叉分支在纵向上错开（不重叠），父节点取子节点中点。"""
	var snaps: Array = [
		_snap("s1", "", 100),
		_snap("s2", "", 200),
		_snap("s3", "", 300),
	]
	var tree: Dictionary = TimelineLayout.build(snaps, 400, "s1")
	var slots: Dictionary = {}
	for n in tree["nodes"]:
		slots[n["id"]] = n["slot"]
	assert_ne(slots["s2"], slots[TimelineLayout.LIVE_ID], "分支槽位应错开")
	assert_eq(slots["s1"], (slots["s2"] + slots[TimelineLayout.LIVE_ID]) * 0.5,
		"父节点居中于子树")


func test_dirty_snapshot_entries_skipped() -> void:
	"""无 file / 非字典条目应跳过（弱后端容错）。"""
	var snaps: Array = [
		"bad",
		42,
		_snap("s1", "", 100),
	]
	var tree: Dictionary = TimelineLayout.build(snaps, 200)
	assert_eq(tree["nodes"].size(), 2, "仅有效快照 + 当前点")


# ── 保存顺序编号（编号数据源） ─────────────────────────────

func test_save_order_ids_by_saved_at() -> void:
	"""编号按保存时刻（saved_at）递增。"""
	var snaps: Array = [
		_snap("s3", "", 300, 3000.0),
		_snap("s1", "", 100, 1000.0),
		_snap("s2", "", 200, 2000.0),
	]
	assert_eq(TimelineLayout.save_order_ids(snaps), ["s1", "s2", "s3"])


func test_save_order_ignores_rollback_time_reversal() -> void:
	"""回滚后游戏时间倒退不应影响保存顺序（saved_at 为主键）。"""
	var snaps: Array = [
		_snap("s1", "", 100, 1000.0),
		_snap("s2", "", 200, 3000.0),
		_snap("a1", "", 150, 2000.0),  # 游戏时间更小但保存更早
	]
	assert_eq(TimelineLayout.save_order_ids(snaps), ["s1", "a1", "s2"])


func test_save_order_same_second_falls_back_to_game_time() -> void:
	"""同秒保存以游戏时间兜底排序。"""
	var snaps: Array = [
		_snap("s2", "", 200, 1000.0),
		_snap("s1", "", 100, 1000.0),
	]
	assert_eq(TimelineLayout.save_order_ids(snaps), ["s1", "s2"])


func test_save_order_skips_dirty_entries() -> void:
	"""脏条目跳过，编号仍连续。"""
	var snaps: Array = [
		"bad",
		_snap("s1", "", 100, 1000.0),
		_snap("s2", "", 200, 2000.0),
	]
	assert_eq(TimelineLayout.save_order_ids(snaps), ["s1", "s2"])


func test_save_order_uses_seq() -> void:
	"""seq（后端权威创建顺序）优先于 saved_at 与游戏时间。"""
	var snaps: Array = [
		_snap("s2", "", 200, 1000.0, "manual", 1),
		_snap("s1", "", 100, 3000.0, "manual", 0),
	]
	assert_eq(TimelineLayout.save_order_ids(snaps), ["s1", "s2"])


func test_save_order_seq_ignores_rollback_time_reversal() -> void:
	"""回滚后游戏时间倒退 + saved_at 乱序时，seq 仍给出创建顺序。"""
	var snaps: Array = [
		_snap("s1", "", 300, 3000.0, "manual", 0),
		_snap("a1", "", 150, 1000.0, "auto", 1),
		_snap("s2", "", 100, 2000.0, "manual", 2),
	]
	assert_eq(TimelineLayout.save_order_ids(snaps), ["s1", "a1", "s2"])


func test_legacy_mixed_seq_falls_back_to_saved_at() -> void:
	"""旧档（全 seq=0）回退 saved_at 排序（同秒游戏时间兜底）。"""
	var snaps: Array = [
		_snap("s3", "", 300, 3000.0),
		_snap("s1", "", 100, 1000.0),
		_snap("s2", "", 200, 2000.0),
	]
	assert_eq(TimelineLayout.save_order_ids(snaps), ["s1", "s2", "s3"])


func test_sibling_order_follows_seq() -> void:
	"""分叉兄弟节点按 seq（创建顺序）而非游戏时间排序。"""
	var snaps: Array = [
		_snap("s1", "", 100, 0.0, "manual", 0),
		_snap("a1", "s1", 500, 0.0, "auto", 2),
		_snap("a2", "s1", 150, 0.0, "auto", 1),
	]
	var tree: Dictionary = TimelineLayout.build(snaps, 600, "s1")
	var s1: Dictionary = {}
	for n in tree["nodes"]:
		if n["id"] == "s1":
			s1 = n
	assert_eq(s1["children"], ["a2", "a1", TimelineLayout.LIVE_ID],
		"兄弟按 seq 排（a2 创建更晚但 game_time 更小）")
