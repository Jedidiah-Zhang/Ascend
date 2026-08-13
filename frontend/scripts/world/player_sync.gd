"""玩家位置对账的纯逻辑判定 — 客户端预测 + 服务器权威纠正。

从 main_world.gd 拆出（原 _process_snap / _apply_authoritative_position /
_send_player_move 的判定部分）：纯函数无状态，可独立单元测试。
状态（snap 过渡 / 上报 seq 记录）由调用方持有，本类只负责判定与推进计算。
"""

class_name PlayerSync
extends RefCounted

## 权威纠正容差（tiles）：裁决偏离时，权威与本地差距小于该值视为微小偏差，
## 认可本地位置不纠正。
const SNAP_TOLERANCE: float = 2.0
## 硬吸阈值：差距 ≥ SNAP_HARD_THRESHOLD → 立即硬吸（传送/读档复位/初始定位）；
## 差距在 [SNAP_TOLERANCE, SNAP_HARD_THRESHOLD) 间 → 平滑过渡（无瞬跳）。
const SNAP_HARD_THRESHOLD: float = 8.0
## 平滑过渡时长（秒）
const SNAP_DURATION: float = 0.15
## 回声容差（tiles）：权威位置与该次上报位置的逐分量差 ≤ 该值视为后端原样
## 回显（认可），而非裁决偏离——浮点往返无损，理论误差为 0，仅防御性放宽。
const SNAP_ECHO_TOLERANCE: float = 0.01
## 上报 seq 记录上限：超出丢弃最旧（响应滞后超过该数量时回声判定失效，
## 回退距离判定，仍能正确钳制纠正，只是丢失一次零纠正机会）
const REPORT_SEQ_MAX: int = 32

## 权威纠正判定结果（main_world 据此执行副作用）
enum Correction { ECHO, IGNORE, HARD_SNAP, SMOOTH_START }


## 回声判定：权威位置 ≈ 该 seq 的上报位置 → 后端认可（零纠正）。
##
## Args:
##     ax/az: 权威位置（后端回传）。
##     seq: 响应携带的上报序号（-1 = 无序号，不做回声判定）。
##     reported_seq_pos: 上报位置记录（{seq: Vector2}，调用方维护）。
##
## Returns:
##     true = 后端原样回显了该次上报，应零纠正。
static func is_echo(ax: float, az: float, seq: int,
		reported_seq_pos: Dictionary) -> bool:
	if not reported_seq_pos.has(seq):
		return false
	var reported: Vector2 = reported_seq_pos[seq]
	return absf(ax - reported.x) <= SNAP_ECHO_TOLERANCE \
			and absf(az - reported.y) <= SNAP_ECHO_TOLERANCE


## 按权威/本地差距做三档纠正判定（非回声路径）：
##   < SNAP_TOLERANCE      微小偏差 → IGNORE（认可本地）；
##   ≥ SNAP_HARD_THRESHOLD 硬吸     → HARD_SNAP（传送/复位/初始定位）；
##   中间                   平滑过渡 → SMOOTH_START（由调用方启动过渡）。
static func classify_correction(ax: float, az: float, current: Vector3) -> int:
	var dx := ax - current.x
	var dz := az - current.z
	var dist_sq := dx * dx + dz * dz
	if dist_sq < SNAP_TOLERANCE * SNAP_TOLERANCE:
		return Correction.IGNORE
	if dist_sq >= SNAP_HARD_THRESHOLD * SNAP_HARD_THRESHOLD:
		return Correction.HARD_SNAP
	return Correction.SMOOTH_START


## 推进平滑吸附过渡一帧。
##
## 每帧按本帧完成比例从「当前实际位置」（可能已叠加输入位移）向目标推进，
## 输入不丢失；结束帧精确落在目标 XZ（权威位置），y 保留当前值——
## 过渡期间贴地逻辑可能已更新 y（悬浮/下陷不得被过渡结束拉回）。
##
## Args:
##     delta: 帧时长（秒）。
##     snap_time: 当前过渡进度（< 0 = 无过渡）。
##     snap_target: 过渡目标位置。
##     current: 当前玩家位置（含本帧输入位移）。
##
## Returns:
##     [推进后的位置, 新的 snap_time]（结束帧 snap_time = -1）。
static func advance_snap(delta: float, snap_time: float,
		snap_target: Vector3, current: Vector3) -> Array:
	if snap_time < 0.0:
		return [current, snap_time]
	var t_new := snap_time + delta
	if t_new >= SNAP_DURATION:
		return [Vector3(snap_target.x, current.y, snap_target.z), -1.0]
	# 中间帧：剩余差距 × 本帧完成比例（t 线性推进 → 每帧推进比例递增，
	# 指数逼近曲线，平滑单调）
	var prev_t := clampf(snap_time / SNAP_DURATION, 0.0, 1.0)
	var t := clampf(t_new / SNAP_DURATION, 0.0, 1.0)
	var weight := (t - prev_t) / maxf(1.0 - prev_t, 0.0001)
	return [current + (snap_target - current) * weight, t_new]


## 记录一次上报位置（seq → Vector2），超限丢最旧。
##
## Args:
##     report_seq_pos: 上报记录字典（调用方持有）。
##     seq: 上报序号（调用方自增）。
##     pos: 上报的世界位置。
static func record_report(report_seq_pos: Dictionary, seq: int, pos: Vector3) -> void:
	report_seq_pos[seq] = Vector2(pos.x, pos.z)
	while report_seq_pos.size() > REPORT_SEQ_MAX:
		report_seq_pos.erase(report_seq_pos.keys()[0])
