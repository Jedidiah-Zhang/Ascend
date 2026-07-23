"""日志系统 — 每次运行生成带时间戳的日志文件。

用法:
    from ascend.log import get_logger
    logger = get_logger(__name__)
    logger.info("事件总线启动")
    logger.debug("event_id=%s", event.id)
"""

import logging
import sys
import threading
from datetime import datetime
from pathlib import Path

# 日志目录
LOG_DIR = Path(__file__).parent.parent.parent / "logs"

# 防止重复初始化
_setup_lock: threading.Lock = threading.Lock()
_setup_done: bool = False
_log_path: str | None = None


def setup_logging(level: int = logging.DEBUG) -> str:
    """初始化日志系统，创建带时间戳的日志文件。

    日志同时输出到文件和控制台。
    多次调用不会重复添加 handler。

    Args:
        level: 日志级别，默认 DEBUG。

    Returns:
        日志文件的绝对路径。
    """
    global _setup_done, _log_path
    with _setup_lock:
        if _setup_done:
            return _log_path or ""

        LOG_DIR.mkdir(parents=True, exist_ok=True)

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        log_path = LOG_DIR / f"ascend_{timestamp}.log"

        root_logger = logging.getLogger("ascend")
        root_logger.setLevel(level)

        # 文件 handler：每次运行新文件
        file_handler = logging.FileHandler(log_path, encoding="utf-8")
        file_handler.setLevel(level)
        file_formatter = logging.Formatter(
            "[%(asctime)s] %(levelname)-7s %(name)s | %(message)s",
            datefmt="%H:%M:%S",
        )
        file_handler.setFormatter(file_formatter)

        # 控制台 handler
        console_handler = logging.StreamHandler(sys.stdout)
        console_handler.setLevel(logging.INFO)
        console_formatter = logging.Formatter(
            "%(levelname)-7s %(name)s | %(message)s",
        )
        console_handler.setFormatter(console_formatter)

        root_logger.addHandler(file_handler)
        root_logger.addHandler(console_handler)

        _setup_done = True
        _log_path = str(log_path)
        return _log_path


def quiet_console() -> None:
    """将控制台 handler 级别提升到 WARNING，抑制 INFO/DEBUG 输出。

    日志文件不受影响。用于交互式控制台等不希望日志干扰屏幕输出的场景。
    """
    for h in logging.getLogger("ascend").handlers:
        if isinstance(h, logging.StreamHandler) and h.stream is sys.stdout:
            h.setLevel(logging.WARNING)


def get_logger(name: str) -> logging.Logger:
    """获取模块级 logger。

    Args:
        name: 通常传入 __name__。

    Returns:
        Logger 实例。
    """
    return logging.getLogger(name)
