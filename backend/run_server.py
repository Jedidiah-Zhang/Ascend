#!/usr/bin/env python3
"""Ascend 后端服务器启动脚本。

启动 GameEngine，监听 Godot 前端连接。

用法:
    cd backend && PYTHONPATH=. python run_server.py
    或从项目根:
    cd backend && PYTHONPATH=. ../.venv/bin/python run_server.py

按 Ctrl+C 停止。
"""

import sys
import time as _real_time
import glob
from pathlib import Path

# 确保 backend 在 sys.path 中
sys.path.insert(0, str(Path(__file__).parent))

from ascend.log import setup_logging
from ascend.game import GameEngine, SERVER_HOST, SERVER_PORT

AUTO_STOP_DELAY: float = 3.0
LOG_RETENTION_DAYS: int = 7


def _cleanup_old_logs() -> None:
    """删除超过 LOG_RETENTION_DAYS 天的旧日志文件。"""
    project_root = Path(__file__).parent.parent
    log_dir = project_root / "logs"
    if not log_dir.is_dir():
        return
    cutoff = _real_time.time() - LOG_RETENTION_DAYS * 86400
    for log_file in glob.glob(str(log_dir / "*.log")):
        if Path(log_file).stat().st_mtime < cutoff:
            Path(log_file).unlink()


def main() -> None:
    """启动游戏引擎并等待 Ctrl+C 或客户端全部断开后自动退出。"""
    _cleanup_old_logs()
    setup_logging()

    engine = GameEngine(seed=42)
    engine.start()

    print(f"Ascend 服务器运行在 {SERVER_HOST}:{SERVER_PORT}")
    print("按 Ctrl+C 停止，或关闭所有前端后自动退出")

    had_client: bool = False
    empty_since: float | None = None

    try:
        while True:
            _real_time.sleep(0.5)
            client_count = engine.server.client_count if engine.server else 0

            if client_count > 0:
                had_client = True
                empty_since = None
            elif had_client and empty_since is None:
                empty_since = _real_time.monotonic()
            elif had_client and empty_since is not None:
                if _real_time.monotonic() - empty_since >= AUTO_STOP_DELAY:
                    print("\n所有客户端已断开，正在停止...")
                    break
    except KeyboardInterrupt:
        print("\n正在停止...")
    engine.stop()
    print("已停止。")


if __name__ == "__main__":
    main()
