#!/usr/bin/env python3
"""Ascend 后端服务器启动脚本。

启动 GameEngine，监听 Godot 前端连接。

用法:
    cd ascend-backend && PYTHONPATH=. python run_server.py
    或从项目根:
    cd ascend-backend && PYTHONPATH=. ../.venv/bin/python run_server.py

按 Ctrl+C 停止。
"""

import sys
import time as _real_time
from pathlib import Path

# 确保 ascend-backend 在 sys.path 中
sys.path.insert(0, str(Path(__file__).parent))

from ascend.log import setup_logging
from ascend.game import GameEngine, SERVER_HOST, SERVER_PORT


def main() -> None:
    """启动游戏引擎并等待 Ctrl+C。"""
    setup_logging()

    engine = GameEngine(seed=42)
    engine.start()

    print(f"Ascend 服务器运行在 {SERVER_HOST}:{SERVER_PORT}")
    print("按 Ctrl+C 停止")

    try:
        while True:
            _real_time.sleep(1)
    except KeyboardInterrupt:
        print("\n正在停止...")
        engine.stop()
        print("已停止。")


if __name__ == "__main__":
    main()
