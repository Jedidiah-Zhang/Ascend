"""应用入口统一收集世界、游戏与直接运行脚本的日志。"""

import subprocess
import sys
from pathlib import Path


def test_application_logging_captures_package_namespaces(tmp_path):
    # 子进程隔离标准库根 logger 和模块级一次性初始化状态。
    result = subprocess.run(
        [sys.executable, "-c", """
import logging
import sys
from pathlib import Path
from miskhak.log import get_logger, setup_logging

path = setup_logging(log_dir=Path(sys.argv[1]))
assert setup_logging(log_dir=Path(sys.argv[1])) == path
logging.getLogger('olam.generation').info('WORLD_LOG')
get_logger('miskhak.app').info('GAME_LOG')
get_logger('__main__').debug('SCRIPT_DEBUG')
logging.shutdown()
""", str(tmp_path)],
        cwd=Path(__file__).resolve().parents[2],
        capture_output=True, text=True, check=True,
    )
    logs = list(tmp_path.glob("ascend_*.log"))
    assert len(logs) == 1
    content = logs[0].read_text(encoding="utf-8")
    for marker in ("WORLD_LOG", "GAME_LOG", "SCRIPT_DEBUG"):
        assert content.count(marker) == 1
    assert "WORLD_LOG" in result.stdout
    assert "GAME_LOG" in result.stdout
    assert "SCRIPT_DEBUG" not in result.stdout
