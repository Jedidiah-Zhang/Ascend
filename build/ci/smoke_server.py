#!/usr/bin/env python3
"""Ascend 打包产物冒烟测试 — 协议级握手验证（纯 stdlib，无第三方依赖）。

用法:
    python3 build/ci/smoke_server.py --port <端口> --token-file <路径> [--timeout <秒>]

流程:
    1. 轮询 TCP 连接直到端口就绪（打包产物启动中，默认最长 45s）
    2. 发送 hello{token, protocol_version}，等待 hello_ack
    3. 发送 save_list 请求，等待 response（验证 handler 注册与存档层就绪）

帧格式与 backend/ascend/net/protocol.py 保持一致（1B 版本 + 4B 大端长度 + JSON 体）；
故意独立实现（不 import 后端代码），使冒烟能暴露打包产物自身的协议破损。

退出码: 0 = 通过；1 = 失败（附时间线，便于与产物日志对照）。
"""

import argparse
import json
import socket
import struct
import sys
import time

VERSION_BYTE: int = 0x01
MAX_MESSAGE: int = 64 * 1024 * 1024
DEFAULT_TIMEOUT: float = 45.0
RECV_CHUNK: int = 4096


def encode_message(msg: dict) -> bytes:
    """编码一帧（JSON 体 + 版本与长度前缀）。"""
    body = json.dumps(msg, ensure_ascii=False).encode("utf-8")
    return struct.pack(">BI", VERSION_BYTE, len(body)) + body


def recv_frame(sock: socket.socket, buf: bytearray, deadline: float) -> dict:
    """接收并解码一帧。数据不足时继续读，超时或断开抛异常。"""
    while True:
        if len(buf) >= 5:
            length = struct.unpack(">I", buf[1:5])[0]
            if length <= 0 or length > MAX_MESSAGE:
                raise RuntimeError(f"冒烟: 非法帧长度 {length}")
            if len(buf) >= 5 + length:
                body = bytes(buf[5 : 5 + length])
                del buf[: 5 + length]
                return json.loads(body.decode("utf-8"))
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError("等待响应超时")
        sock.settimeout(min(remaining, 5.0))
        try:
            chunk = sock.recv(RECV_CHUNK)
        except socket.timeout:
            continue
        if not chunk:
            raise ConnectionError("连接被对端关闭")
        buf.extend(chunk)


def wait_ready(host: str, port: int, timeout: float) -> socket.socket:
    """轮询连接直到端口就绪。"""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            return socket.create_connection((host, port), timeout=2.0)
        except OSError:
            time.sleep(1.0)
    raise TimeoutError(f"端口 {port} 在 {timeout:.0f}s 内未就绪")


def main() -> int:
    parser = argparse.ArgumentParser(description="Ascend 打包产物协议级冒烟")
    parser.add_argument("--port", type=int, required=True, help="产物监听端口")
    parser.add_argument("--token-file", required=True, help=".ascend_token 文件路径")
    parser.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT, help="等待就绪秒数")
    parser.add_argument("--host", default="127.0.0.1")
    args = parser.parse_args()

    timeline: list[str] = []

    token: str = ""
    token_deadline = time.monotonic() + args.timeout
    while time.monotonic() < token_deadline:
        try:
            with open(args.token_file, encoding="utf-8") as fh:
                token = fh.read().strip()
            if token:
                break
        except OSError:
            pass
        time.sleep(0.5)
    if not token:
        print(f"冒烟失败: {args.timeout:.0f}s 内未读到 token 文件 {args.token_file}")
        return 1
    timeline.append(f"token 已读取（{len(token)} 字符）")

    try:
        sock = wait_ready(args.host, args.port, args.timeout)
    except TimeoutError as exc:
        print(f"冒烟失败: {exc}")
        return 1
    timeline.append(f"端口 {args.port} 就绪")

    try:
        buf: bytearray = bytearray()
        deadline = time.monotonic() + 10.0

        sock.sendall(encode_message({
            "type": "hello",
            "payload": {"token": token, "protocol_version": VERSION_BYTE},
        }))
        ack = recv_frame(sock, buf, deadline)
        if ack.get("type") != "hello_ack":
            print(f"冒烟失败: 期望 hello_ack，收到 {json.dumps(ack, ensure_ascii=False)}")
            return 1
        timeline.append("握手成功（hello → hello_ack）")

        sock.sendall(encode_message({"type": "request", "request_type": "save_list"}))
        resp = recv_frame(sock, buf, deadline)
        if resp.get("type") != "response" or resp.get("request_type") != "save_list":
            print(f"冒烟失败: 期望 save_list 响应，收到 {json.dumps(resp, ensure_ascii=False)}")
            return 1
        if "worlds" not in resp.get("payload", {}):
            print(f"冒烟失败: save_list 响应缺少 worlds: {json.dumps(resp, ensure_ascii=False)}")
            return 1
        timeline.append("save_list 响应正常（handler 层可用）")
    except (OSError, ConnectionError, TimeoutError, json.JSONDecodeError, struct.error) as exc:
        print(f"冒烟失败: {exc}")
        return 1
    finally:
        sock.close()

    print("冒烟通过: " + " → ".join(timeline))
    return 0


if __name__ == "__main__":
    sys.exit(main())