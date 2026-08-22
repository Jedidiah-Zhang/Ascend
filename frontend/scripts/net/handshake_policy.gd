"""握手重试策略 — 致命失败判定 / 重试预算（纯逻辑 RefCounted）。

职责（从 connection.gd 抽离）:
  - on_rejected(kind)：按拒绝分类判定——VERSION_MISMATCH 永久性失败，
    立即 FAIL（重试无意义）；ANOMALY 计入预算
  - on_timeout() / on_disconnect()：计入预算（后端挂起 / 握手期被断开，
    token 失效或后端重启中，重试可能成功——token 每次握手重读）
  - on_ack()：握手成功，预算清零（任何一次成功即重置）
  - reset()：主动复位（进程切换/手动重连）

重试时序（间隔）归 TcpTransport 所有（连续失败指数退避、成功复位），
本策略只回答"继续重试还是终态"。

依赖方向：connection(门面) → 本层；本层不感知其他子层。
"""

class_name HandshakePolicy
extends RefCounted

const HandshakeClass = preload("res://scripts/net/handshake.gd")


# ── 判定结果 ───────────────────────────────────────────────

enum Verdict { RETRY, FAIL }


# ── 属性 ───────────────────────────────────────────────────

var _failures: int = 0


# ── 预算接口 ───────────────────────────────────────────────

## 握手成功：预算清零（任何一次成功即重置重试历史）。
func on_ack() -> void:
	_failures = 0


## 拒绝分类判定：永久性失败（版本不兼容）立即终态，其余计入预算。
func on_rejected(kind: HandshakeClass.RejectKind) -> Verdict:
	if kind == HandshakeClass.RejectKind.VERSION_MISMATCH:
		return Verdict.FAIL
	return _record_retryable()


## 等待 ack 超时：后端挂起，计入预算。
func on_timeout() -> Verdict:
	return _record_retryable()


## 握手期连接断开（token 失效/后端重启）：计入预算。
func on_disconnect() -> Verdict:
	return _record_retryable()


## 主动复位（进程切换/手动重连前调用）。
func reset() -> void:
	_failures = 0


# ── 内部 ───────────────────────────────────────────────────

func _record_retryable() -> Verdict:
	_failures += 1
	if _failures > Config.HANDSHAKE_MAX_RETRIES:
		return Verdict.FAIL
	return Verdict.RETRY