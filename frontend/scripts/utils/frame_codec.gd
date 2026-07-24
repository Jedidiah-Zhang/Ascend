"""帧编解码器 — 协议帧的构造与解析，纯逻辑 RefCounted 类。

每条消息 = 4 字节大端长度前缀 + MsgPack 体
与 Connection Node 解耦，可独立测试。
"""

class_name FrameCodec
extends RefCounted


var seq: int = 0


func next_seq() -> int:
	seq += 1
	return seq


func frame_encode(message: Dictionary) -> PackedByteArray:
	var encoded: PackedByteArray = MsgPack.encode(message)
	if encoded.is_empty():
		push_error("FrameCodec: failed to encode message")
		return PackedByteArray()
	var length: int = encoded.size()
	var framed: PackedByteArray = PackedByteArray()
	framed.append((length >> 24) & 0xff)
	framed.append((length >> 16) & 0xff)
	framed.append((length >> 8) & 0xff)
	framed.append(length & 0xff)
	framed.append_array(encoded)
	return framed


func frame_decode(buffer: PackedByteArray, max_message_size: int = 16 * 1024 * 1024) -> Dictionary:
	var bodies: Array[PackedByteArray] = []
	var remaining: PackedByteArray = buffer

	while remaining.size() >= 4:
		var msg_len: int = (remaining[0] << 24) | (remaining[1] << 16) | (remaining[2] << 8) | remaining[3]
		if msg_len <= 0 or msg_len > max_message_size:
			push_error("FrameCodec: invalid message length: %d" % msg_len)
			return {"bodies": bodies, "remaining": PackedByteArray()}
		if remaining.size() < 4 + msg_len:
			break
		var body: PackedByteArray = remaining.slice(4, 4 + msg_len)
		remaining = remaining.slice(4 + msg_len)
		bodies.append(body)

	return {"bodies": bodies, "remaining": remaining}
