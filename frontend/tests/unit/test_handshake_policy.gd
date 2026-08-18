extends GutTest

const HandshakePolicy = preload("res://scripts/net/handshake_policy.gd")
const HandshakeClass = preload("res://scripts/net/handshake.gd")
const Config = preload("res://scripts/config.gd")


var _policy: HandshakePolicy = null


func before_each() -> void:
	_policy = HandshakePolicy.new()


func after_each() -> void:
	_policy = null


# ── 拒绝分类 ───────────────────────────────────────────────

func test_version_mismatch_immediate_fail() -> void:
	"""版本不兼容是永久性失败：不消耗预算，立即终态。"""
	assert_eq(_policy.on_rejected(HandshakeClass.RejectKind.VERSION_MISMATCH),
		HandshakePolicy.Verdict.FAIL)


func test_anomaly_retryable_within_budget() -> void:
	"""协议异常可重试：预算内 RETRY，耗尽 FAIL。"""
	for i in Config.HANDSHAKE_MAX_RETRIES:
		assert_eq(_policy.on_rejected(HandshakeClass.RejectKind.ANOMALY),
			HandshakePolicy.Verdict.RETRY, "第 %d 次失败应重试" % (i + 1))
	assert_eq(_policy.on_rejected(HandshakeClass.RejectKind.ANOMALY),
		HandshakePolicy.Verdict.FAIL, "预算耗尽应终态")


# ── 超时 / 握手期断开 ─────────────────────────────────────

func test_timeout_retryable_within_budget() -> void:
	for i in Config.HANDSHAKE_MAX_RETRIES:
		assert_eq(_policy.on_timeout(), HandshakePolicy.Verdict.RETRY)
	assert_eq(_policy.on_timeout(), HandshakePolicy.Verdict.FAIL)


func test_disconnect_retryable_within_budget() -> void:
	"""握手期断开（token 失效/后端重启）可重试：token 每次握手重读。"""
	for i in Config.HANDSHAKE_MAX_RETRIES:
		assert_eq(_policy.on_disconnect(), HandshakePolicy.Verdict.RETRY)
	assert_eq(_policy.on_disconnect(), HandshakePolicy.Verdict.FAIL)


func test_budget_shared_across_failure_types() -> void:
	"""不同失败类型共用同一预算（连续失败累计）。"""
	for i in 3:
		_policy.on_disconnect()
	_policy.on_timeout()
	assert_eq(_policy.on_rejected(HandshakeClass.RejectKind.ANOMALY),
		HandshakePolicy.Verdict.RETRY, "累计 5 次内仍可重试")
	assert_eq(_policy.on_rejected(HandshakeClass.RejectKind.ANOMALY),
		HandshakePolicy.Verdict.FAIL, "累计第 6 次终态")


# ── 复位 ───────────────────────────────────────────────────

func test_ack_resets_budget() -> void:
	"""任何一次握手成功即重置重试历史。"""
	for i in Config.HANDSHAKE_MAX_RETRIES:
		_policy.on_timeout()
	_policy.on_ack()
	assert_eq(_policy.on_timeout(), HandshakePolicy.Verdict.RETRY,
		"ack 后预算应清零")


func test_reset_clears_budget() -> void:
	"""主动复位（进程切换/手动重连）清空预算。"""
	for i in Config.HANDSHAKE_MAX_RETRIES:
		_policy.on_timeout()
	_policy.reset()
	assert_eq(_policy.on_timeout(), HandshakePolicy.Verdict.RETRY)


func test_version_mismatch_after_retries_still_fail() -> void:
	"""致命判定独立于预算：任何时候版本不兼容都立即终态。"""
	for i in 2:
		_policy.on_timeout()
	assert_eq(_policy.on_rejected(HandshakeClass.RejectKind.VERSION_MISMATCH),
		HandshakePolicy.Verdict.FAIL)