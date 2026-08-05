#!/usr/bin/env python3
"""Ascend 后端服务器启动脚本。

以服务模式启动（GameEngine.start_service）：TCP 端口立即就绪，
仅提供存档管理请求；世界在大陆生成（5-30s+）只在 save_load
读档/新建进入时执行——主菜单不再等待地图生成。

网络层常驻：读档重建只替换世界观，客户端全程不断线。

用法:
    cd backend && PYTHONPATH=. python run_server.py
    或从项目根:
    cd backend && PYTHONPATH=. ../.venv/bin/python run_server.py

按 Ctrl+C 停止；前端退出时会发送 SIGTERM 优雅停止（先落盘再退出）。

环境变量:
    ASCEND_SERVER_PORT: 覆盖监听端口（测试隔离用，默认 ascend/config.py）。
"""

import os
import sys
import signal
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
    """服务模式启动，等待 Ctrl+C 或客户端全部断开后自动退出。"""
    _cleanup_old_logs()
    setup_logging()

    # 测试隔离：ASCEND_SERVER_PORT 覆盖监听端口（直接传给引擎构造参数）
    listen_port = SERVER_PORT
    port_override = os.environ.get("ASCEND_SERVER_PORT", "").strip()
    if port_override:
        listen_port = int(port_override)

    # SIGTERM（前端优雅关闭时发送）：结束主循环，走 engine.stop()
    # 最终落盘（state + chunk flush + WAL），避免强杀丢状态
    stop_requested = False

    def _handle_sigterm(_signum: int, _frame: object) -> None:
        nonlocal stop_requested
        stop_requested = True
        print("\n收到 SIGTERM，正在保存并停止...")

    signal.signal(signal.SIGTERM, _handle_sigterm)

    engine = GameEngine(seed=42, port=listen_port)
    engine.start_service()

    print(f"Ascend 服务器运行在 {SERVER_HOST}:{listen_port}")
    print("按 Ctrl+C 停止，或关闭所有前端后自动退出")

    had_client: bool = False
    empty_since: float | None = None

    try:
        while True:
            if stop_requested:
                print("正在停止...")
                break
            _real_time.sleep(0.5)
            client_count = engine.server.client_count if engine.server else 0

            if client_count > 0:
                had_client = True
                empty_since = None
            elif engine.is_reloading:
                # 读档重建中：tick 线程忙于世界生成，客户端数可能短暂
                # 变化，抑制自动停止（网络层常驻，重建后恢复）
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
