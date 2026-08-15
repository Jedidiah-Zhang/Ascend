#!/usr/bin/env python3
"""Ascend 后端服务器启动脚本。

进程模型（一个进程 = 一种模式，进程内不换世界）:
  - 菜单进程（无参）: 服务模式启动（GameEngine.start_service），
    TCP 端口立即就绪，仅提供存档管理请求——主菜单不再等待地图生成；
  - 世界进程（--world-id <id>）: 直接构建世界观（GameEngine.start），
    世界就绪信号 = world_initialized 事件；大陆生成 5-30s 期间端口
    已开放（网络层先行），前端可连接并收 world_progress 进度；
  - 回滚（--world-id + --snapshot <file>）: 进程启动时先保护活目录
    分支（auto 快照）再展开快照（GameEngine.load_world）。
  进入世界 / 回滚 = 前端停旧进程、以目标参数拉起新进程。

用法:
    cd backend && PYTHONPATH=. python run_server.py
    cd backend && PYTHONPATH=. python run_server.py --world-id <id>
    cd backend && PYTHONPATH=. python run_server.py --world-id <id> --snapshot <file>
    cd backend && PYTHONPATH=. python run_server.py --world-id <id> --regen-continent

--regen-continent: 无视大陆缓存强制重建（开发者/研究侧调参用；
对存档世界有破坏性——玩家改动的 chunk 与新场可能出现接缝不一致）。

按 Ctrl+C 停止；前端退出时会发送 SIGTERM 优雅停止（先落盘再退出）。

环境变量:
    ASCEND_SERVER_PORT: 覆盖监听端口（测试隔离用，默认 ascend/config.py）。
    ASCEND_SAVE_ROOT:   覆盖存档根目录（测试隔离用，默认 ~/.ascend/saves）。
"""

import os
import sys
import signal
import time as _real_time
import glob
from pathlib import Path

# 确保 backend 在 sys.path 中
sys.path.insert(0, str(Path(__file__).parent))

from ascend.log import setup_logging, get_logger
from ascend.game import GameEngine, SERVER_HOST, SERVER_PORT

AUTO_STOP_DELAY: float = 3.0
LOG_RETENTION_DAYS: int = 7
TOKEN_FILE: str = ".ascend_token"  # 认证令牌文件（项目根，前端启动时读取）

# 项目根：默认取脚本上级目录（开发模式）；打包后由前端以
# --project-root 显式传入（Nuitka 下 __file__ 为构建路径）。
_PROJECT_ROOT: Path = Path(__file__).parent.parent
# 数据根（token/日志落点）：打包环境（AppImage 只读挂载、Program Files
# 受限）由前端以 --data-root 显式传入；默认与项目根一致。
_DATA_ROOT: Path | None = None


def _data_root() -> Path:
    return _DATA_ROOT if _DATA_ROOT is not None else _PROJECT_ROOT


def _write_token_file(token: str) -> None:
    """写入认证令牌文件（本地握手用，非机密传输通道）。

    前端拉起的后端经此文件获取 token 完成握手；文件为数据根相对路径
    （--data-root 覆盖，默认项目根），已加入 .gitignore。
    """
    path = _data_root() / TOKEN_FILE
    try:
        path.write_text(token, encoding="utf-8")
    except OSError:
        logger = get_logger("run_server")
        logger.warning("令牌文件写入失败: %s", path)


def _cleanup_old_logs() -> None:
    """删除超过 LOG_RETENTION_DAYS 天的旧日志文件。"""
    log_dir = _data_root() / "logs"
    if not log_dir.is_dir():
        return
    cutoff = _real_time.time() - LOG_RETENTION_DAYS * 86400
    for log_file in glob.glob(str(log_dir / "*.log")):
        if Path(log_file).stat().st_mtime < cutoff:
            Path(log_file).unlink()


def _parse_args(argv: list[str]) -> tuple[str | None, str | None, str | None, str | None, bool]:
    """解析 --world-id / --snapshot / --project-root / --data-root /
    --regen-continent（无第三方依赖的手写解析）。"""
    world_id: str | None = None
    snapshot: str | None = None
    project_root: str | None = None
    data_root: str | None = None
    regen_continent: bool = False
    i = 0
    while i < len(argv):
        if argv[i] == "--world-id" and i + 1 < len(argv):
            world_id = argv[i + 1]
            i += 2
        elif argv[i] == "--snapshot" and i + 1 < len(argv):
            snapshot = argv[i + 1]
            i += 2
        elif argv[i] == "--project-root" and i + 1 < len(argv):
            project_root = argv[i + 1]
            i += 2
        elif argv[i] == "--data-root" and i + 1 < len(argv):
            data_root = argv[i + 1]
            i += 2
        elif argv[i] == "--regen-continent":
            regen_continent = True
            i += 1
        else:
            print(f"未知参数: {argv[i]}", file=sys.stderr)
            sys.exit(2)
    return world_id, snapshot, project_root, data_root, regen_continent


def _force_utf8_stdio() -> None:
    """Windows 控制台/管道默认 ANSI 代码页（如 cp1252），中文日志
    UnicodeEncodeError 直接崩溃（英文 Windows 真实运行同样触发）。
    统一重配置为 UTF-8 + errors=replace：任何环境不崩，文件/管道
    输出保持可读，控制台至多显示替换符。
    """
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            try:
                reconfigure(encoding="utf-8", errors="replace")
            except (ValueError, OSError):
                pass


def main() -> None:
    """按参数启动菜单进程或世界进程。"""
    global _PROJECT_ROOT, _DATA_ROOT

    _force_utf8_stdio()

    world_id, snapshot, project_root, data_root, regen_continent = _parse_args(sys.argv[1:])
    if project_root is not None:
        _PROJECT_ROOT = Path(project_root)
    if data_root is not None:
        _DATA_ROOT = Path(data_root)

    _cleanup_old_logs()
    setup_logging(log_dir=_data_root() / "logs")

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
    # 网络层先行（幂等）：端口 + token 文件立即就绪。世界进程的
    # load_world 会阻塞 5-30s（大陆生成），token 若延迟写入，
    # 前端立即连接时读到旧 token 握手失败（且前端缓存 token 不重读）。
    engine.ensure_network()
    if engine.server is not None:
        _write_token_file(engine.server.token)
    if world_id is not None or snapshot is not None:
        # 世界进程：世界加载失败（存档不存在/损坏/回滚目标无效）时
        # 打印错误并以非零码退出——前端端口探测超时后按启动失败处理
        try:
            engine.load_world(
                world_id=world_id, snapshot=snapshot,
                regen_continent=regen_continent,
            )
        except Exception as exc:
            logger = get_logger("run_server")
            logger.error("世界启动失败: world=%s snapshot=%s: %s", world_id, snapshot, exc)
            print(f"世界启动失败: {exc}", file=sys.stderr)
            sys.exit(1)
    else:
        engine.start_service()

    if world_id is not None:
        print(f"Ascend 世界进程运行在 {SERVER_HOST}:{listen_port} (world={world_id})")
        print("按 Ctrl+C 停止，或关闭所有前端后自动退出")
    else:
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
