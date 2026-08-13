"""PlayerSync 单元测试 — 位置对账纯逻辑判定。

覆盖：回声认可（零纠正）、距离三档纠正（微小偏差/硬吸/平滑过渡）、
平滑推进（结束落点/指数逼近）、上报记录（seq 对齐/超限丢最旧）。
"""

extends GutTest


# ── 回声判定 ──────────────────────────────────────────────

func test_echo_accepts_reported_position() -> void:
	"""权威位置 ≈ 上报位置 → 回声认可（零纠正）。"""
	var reported := {3: Vector2(10.0, 20.0)}
	assert_true(PlayerSync.is_echo(10.0, 20.0, 3, reported), "完全一致应视为回声")
	assert_true(PlayerSync.is_echo(10.004, 20.0, 3, reported), "误差 ≤ 容差应视为回声")


func test_echo_rejects_unknown_seq() -> void:
	"""seq 不在上报记录 → 非回声（按距离判定）。"""
	var reported := {3: Vector2(10.0, 20.0)}
	assert_false(PlayerSync.is_echo(10.0, 20.0, 99, reported), "未知 seq 不应认可")
	assert_false(PlayerSync.is_echo(10.0, 20.0, -1, reported), "无 seq 响应不应认可")


func test_echo_rejects_deviation_beyond_tolerance() -> void:
	"""权威位置偏离上报超过容差 → 非回声（裁决偏离）。"""
	var reported := {3: Vector2(10.0, 20.0)}
	assert_false(PlayerSync.is_echo(12.0, 20.0, 3, reported), "偏离超容差不应认可")


# ── 距离三档纠正 ──────────────────────────────────────────

func test_classify_tiny_deviation_ignored() -> void:
	"""差距 < SNAP_TOLERANCE → IGNORE（认可本地）。"""
	var current := Vector3(0, 0, 0)
	assert_eq(
		PlayerSync.classify_correction(1.9, 0.0, current),
		PlayerSync.Correction.IGNORE)
	assert_eq(
		PlayerSync.classify_correction(0.0, -1.9, current),
		PlayerSync.Correction.IGNORE)


func test_classify_large_deviation_hard_snaps() -> void:
	"""差距 ≥ SNAP_HARD_THRESHOLD → HARD_SNAP（传送/复位/初始定位）。"""
	var current := Vector3(0, 0, 0)
	assert_eq(
		PlayerSync.classify_correction(8.0, 0.0, current),
		PlayerSync.Correction.HARD_SNAP, "恰好等于阈值应硬吸")
	assert_eq(
		PlayerSync.classify_correction(0.0, 50.0, current),
		PlayerSync.Correction.HARD_SNAP)


func test_classify_mid_deviation_smooth_starts() -> void:
	"""差距在 [容差, 硬吸) 间 → SMOOTH_START。"""
	var current := Vector3(0, 0, 0)
	assert_eq(
		PlayerSync.classify_correction(5.0, 0.0, current),
		PlayerSync.Correction.SMOOTH_START)


# ── 平滑推进 ──────────────────────────────────────────────

func test_advance_snap_no_transition_returns_current() -> void:
	"""无过渡（snap_time < 0）→ 位置不变、保持无过渡。"""
	var result: Array = PlayerSync.advance_snap(0.1, -1.0, Vector3(10, 0, 10), Vector3.ZERO)
	assert_eq(result[0], Vector3.ZERO)
	assert_eq(result[1], -1.0)


func test_advance_snap_finishes_exactly_on_target() -> void:
	"""结束帧精确落在目标 XZ（权威位置），y 保留当前值，snap_time 复位 -1。"""
	var result: Array = PlayerSync.advance_snap(
		PlayerSync.SNAP_DURATION, 0.0, Vector3(10, 5, 10), Vector3(0, 12, 0))
	assert_eq(result[0], Vector3(10, 12, 10), "结束帧应落目标 XZ、保留当前 y（贴地不丢）")
	assert_eq(result[1], -1.0)


func test_advance_snap_monotonic_toward_target() -> void:
	"""中间帧向目标单调推进、不越界，进度持续推进。"""
	var target := Vector3(10, 0, 10)
	var pos := Vector3.ZERO
	var t: float = 0.0
	var prev_dist := 1e9
	for i in range(6):
		var r: Array = PlayerSync.advance_snap(0.01, t, target, pos)
		pos = r[0]
		t = r[1]
		var dist: float = pos.distance_to(target)
		assert_lt(dist, prev_dist, "第 %d 帧应向目标收敛" % i)
		prev_dist = dist
	assert_gt(prev_dist, 0.0, "未到结束帧前不应提前落点")


# ── 上报记录 ──────────────────────────────────────────────

func test_record_report_stores_position_and_seq() -> void:
	"""上报记录 seq → 位置（Vector2），供回声判定对齐。"""
	var records := {}
	PlayerSync.record_report(records, 1, Vector3(5, 0, 7))
	assert_eq(records[1], Vector2(5, 7), "Y 轴映射：世界 Z → Vector2.y")


func test_record_report_drops_oldest_over_limit() -> void:
	"""超出 REPORT_SEQ_MAX 丢最旧（字典插入序 = 上报序）。"""
	var records := {}
	for i in range(PlayerSync.REPORT_SEQ_MAX + 5):
		PlayerSync.record_report(records, i, Vector3(float(i), 0, 0))
	assert_eq(records.size(), PlayerSync.REPORT_SEQ_MAX)
	assert_false(records.has(0), "最早上报应被丢弃")
	assert_true(records.has(PlayerSync.REPORT_SEQ_MAX + 4), "最新上报应保留")
