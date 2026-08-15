"""run_server.py 集成测试 — SIGTERM 优雅关闭（退出前落盘路径）。

真实子进程验证：spawn run_server.py（独立端口，隔离测试环境），
保持一个客户端连接（抑制自动停止），发 SIGTERM，断言进程正常
退出（exit 0）。

防护：前端退出必须走优雅关闭——锁定「SIGTERM → handler →
engine.stop() → 正常退出」契约（若 handler 缺失，SIGTERM 默认
终止进程，returncode 为负信号值）。
engine.stop() 内部的最终落盘由 test_game_engine 覆盖。

进程模型（--world-id）：世界进程有效加载路径由
test_game_engine.TestWorldProcessEntry 覆盖（快路径 patch）；
此处仅验证 CLI 错误路径（无效存档快速失败退出）。
"""

import os
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path

import pytest

from ascend.save.manager import SaveManager

BACKEND_DIR = Path(__file__).resolve().parents[2]
RUN_SERVER = BACKEND_DIR / "run_server.py"

START_TIMEOUT = 15.0
STOP_TIMEOUT = 15.0


def _free_port() -> int:
    """获取一个空闲端口（bind 0 后释放）。"""
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _connect(port: int, timeout: float = START_TIMEOUT) -> socket.socket:
    """轮询连接后端，成功后返回保持打开的套接字。

    连接保持到测试结束：避免触发 run_server 的「客户端全断开
    3s 自动停止」，使 SIGTERM 成为唯一退出路径。
    """
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            return socket.create_connection(("127.0.0.1", port), timeout=0.2)
        except OSError:
            time.sleep(0.1)
    raise AssertionError(f"后端未在 {timeout}s 内监听端口 {port}")


@pytest.mark.skipif(not hasattr(signal, "SIGTERM"), reason="需 POSIX 信号")
def test_sigterm_stops_gracefully_with_exit_0() -> None:
    """SIGTERM 应触发优雅关闭：进程正常退出（exit 0）而非被信号杀死。"""
    port = _free_port()
    env = dict(os.environ)
    env["ASCEND_SERVER_PORT"] = str(port)

    proc = subprocess.Popen(
        [sys.executable, str(RUN_SERVER)],
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    hold: socket.socket | None = None
    try:
        hold = _connect(port)
        proc.send_signal(signal.SIGTERM)
        try:
            returncode = proc.wait(timeout=STOP_TIMEOUT)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()
            pytest.fail("SIGTERM 后未在超时内退出（handler 未生效？）")
        assert returncode == 0, (
            f"应经 SIGTERM handler 正常退出，实际 returncode={returncode}"
        )
    finally:
        if hold is not None:
            hold.close()
        if proc.poll() is None:
            proc.kill()
            proc.wait()


@pytest.mark.skipif(not hasattr(signal, "SIGTERM"), reason="需 POSIX 信号")
def test_sigterm_before_any_client_exits_cleanly() -> None:
    """无客户端时 SIGTERM 同样优雅退出（不依赖自动停止路径）。"""
    port = _free_port()
    env = dict(os.environ)
    env["ASCEND_SERVER_PORT"] = str(port)

    proc = subprocess.Popen(
        [sys.executable, str(RUN_SERVER)],
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    try:
        with _connect(port):
            pass  # 探测就绪后断开即可
        proc.send_signal(signal.SIGTERM)
        returncode = proc.wait(timeout=STOP_TIMEOUT)
        assert returncode == 0, (
            f"无客户端时 SIGTERM 也应正常退出，实际 returncode={returncode}"
        )
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait()


def test_world_mode_invalid_world_id_exits_nonzero(tmp_path) -> None:
    """--world-id 指向不存在的存档：进程快速失败（exit 1）。

    前端以端口超时感知启动失败；run_server 的世界启动异常路径
    必须显式非零退出（而非挂死等待客户端）。
    """
    port = _free_port()
    env = dict(os.environ)
    env["ASCEND_SERVER_PORT"] = str(port)
    env["ASCEND_SAVE_ROOT"] = str(tmp_path / "saves")

    proc = subprocess.Popen(
        [sys.executable, str(RUN_SERVER), "--world-id", "0" * 32],
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    try:
        returncode = proc.wait(timeout=START_TIMEOUT)
        assert returncode == 1, (
            f"无效存档应非零退出，实际 returncode={returncode}"
        )
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait()


def test_world_mode_rollback_missing_world_id_exits_nonzero(tmp_path) -> None:
    """--snapshot 未带 --world-id：参数校验失败非零退出。"""
    port = _free_port()
    env = dict(os.environ)
    env["ASCEND_SERVER_PORT"] = str(port)
    env["ASCEND_SAVE_ROOT"] = str(tmp_path / "saves")

    proc = subprocess.Popen(
        [sys.executable, str(RUN_SERVER), "--snapshot", "x.ascendsave"],
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    try:
        returncode = proc.wait(timeout=START_TIMEOUT)
        assert returncode == 1, "回滚缺 world_id 应非零退出"
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait()


def test_world_mode_valid_world_opens_port_then_graceful_stop(tmp_path) -> None:
    """世界进程：端口先行开放（生成期间可连）→ SIGTERM 优雅退出 0。

    有效世界加载的完整断言（birth_chunk/处理程序）由引擎级测试
    TestWorldProcessEntry 覆盖；此处验证进程级契约：网络层先行
    + 优雅退出。
    """
    port = _free_port()
    save_root = tmp_path / "saves"
    world_id = SaveManager(root=str(save_root)).create_world(
        "世界进程", seed=7,
    ).world_id
    env = dict(os.environ)
    env["ASCEND_SERVER_PORT"] = str(port)
    env["ASCEND_SAVE_ROOT"] = str(save_root)

    proc = subprocess.Popen(
        [sys.executable, str(RUN_SERVER), "--world-id", world_id],
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    hold: socket.socket | None = None
    try:
        # 大陆生成 5-30s：端口须在网络层先行阶段开放（前端据此连接收进度）
        hold = _connect(port, timeout=90.0)
        proc.send_signal(signal.SIGTERM)
        returncode = proc.wait(timeout=120.0)
        assert returncode == 0, (
            f"世界进程 SIGTERM 应优雅退出 0，实际 returncode={returncode}"
        )
    finally:
        if hold is not None:
            hold.close()
        if proc.poll() is None:
            proc.kill()
            proc.wait()


def test_parse_args_regen_continent_flag() -> None:
    """--regen-continent 布尔旗标解析（组合与缺省）。"""
    import sys as _sys
    _sys.path.insert(0, str(BACKEND_DIR))
    from run_server import _parse_args

    assert _parse_args(["--world-id", "w1", "--regen-continent"]) == (
        "w1", None, None, None, True,
    )
    assert _parse_args(["--world-id", "w1"]) == (
        "w1", None, None, None, False,
    )
    assert _parse_args([]) == (None, None, None, None, False)
