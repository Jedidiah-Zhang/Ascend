"""帧编解码器 — 协议帧的构造与解析，纯逻辑 RefCounted 类。

每条消息 = 1 字节协议版本 + 4 字节大端长度前缀 + JSON 体
与 Connection Node 解耦，可独立测试。

帧格式与后端 ascend/net/protocol.py 保持一致（struct.pack(">BI")）：
跨语言一致性由 tests/unit/test_frame_codec.gd 与后端 protocol 测试锁定。
"""

class_name FrameCodec
extends RefCounted


const PROTOCOL_VERSION: int = 0x01  # 与后端 ascend/net/protocol.py 同步


var seq: int = 0


## 自增消息序号：每次调用 +1 并返回，保证同连接内消息序号单调递增。
func next_seq() -> int:
	seq += 1
	return seq


## 编码消息帧：JSON 序列化后前置 1 字节协议版本 + 4 字节大端长度前缀
## （与后端 struct.pack(">BI") 一致）。
##
## Args:
##     message: 待发送的消息字典。
##
## Returns:
##     完整协议帧（版本 + 长度前缀 + JSON 体）；编码失败时返回空 PackedByteArray。
func frame_encode(message: Dictionary) -> PackedByteArray:
	var encoded: PackedByteArray = JsonCodec.encode(message)
	if encoded.is_empty():
		push_error("FrameCodec: failed to encode message")
		return PackedByteArray()
	var length: int = encoded.size()
	var framed: PackedByteArray = PackedByteArray()
	framed.append(PROTOCOL_VERSION)
	framed.append((length >> 24) & 0xff)
	framed.append((length >> 16) & 0xff)
	framed.append((length >> 8) & 0xff)
	framed.append(length & 0xff)
	framed.append_array(encoded)
	return framed


## 解码缓冲区帧序列：循环切出完整帧体，帧头非法（版本不支持、长度
## ≤0 或超上限）时丢弃该缓冲，尾部不足一帧的字节保留在 remaining 中，
## 待与下次收到的数据拼接后继续解析。
##
## Args:
##     buffer: 收到的原始字节（可能含多帧 + 半帧尾部）。
##     max_message_size: 单帧体长度上限（默认 16 MiB），防超大非法帧。
##
## Returns:
##     {bodies: 完整帧体数组, remaining: 未凑齐一帧的剩余字节}。
func frame_decode(buffer: PackedByteArray, max_message_size: int = 16 * 1024 * 1024) -> Dictionary:
	var bodies: Array[PackedByteArray] = []
	var remaining: PackedByteArray = buffer

	while remaining.size() >= 5:
		var version: int = remaining[0]
		if version != PROTOCOL_VERSION:
			push_error("FrameCodec: unsupported protocol version: 0x%02x" % version)
			return {"bodies": bodies, "remaining": PackedByteArray()}
		var msg_len: int = (remaining[1] << 24) | (remaining[2] << 16) | (remaining[3] << 8) | remaining[4]
		if msg_len <= 0 or msg_len > max_message_size:
			push_error("FrameCodec: invalid message length: %d" % msg_len)
			return {"bodies": bodies, "remaining": PackedByteArray()}
		if remaining.size() < 5 + msg_len:
			break
		var body: PackedByteArray = remaining.slice(5, 5 + msg_len)
		remaining = remaining.slice(5 + msg_len)
		bodies.append(body)

	return {"bodies": bodies, "remaining": remaining}
