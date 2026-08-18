"""生命周期栈 — 装配/拆卸的逆操作登记与统一回滚。

装配时把每一步的逆操作压栈，拆卸时按逆序执行（LIFO），保证
"后创建的先销毁"——对应"变换携带逆、运行时按序回滚"的可组合思想，
消除"装配清单与拆卸清单两份手写、需人工对齐"的漂移风险。

- 逆序回滚：teardown() 按压栈逆序执行全部逆操作，幂等。
- 单项隔离：单个逆操作异常被记录，不阻断其余回滚。
- 线程安全：登记与回滚由内部锁保护。
"""

import threading
from collections.abc import Callable

from ascend.log import get_logger

logger = get_logger(__name__)


class LifecycleStack:
    """装配/拆卸生命周期栈。

    用法:
        stack = LifecycleStack()
        stack.push(server.stop)          # 装配时登记逆操作
        stack.push(bridge.uninstall)
        ...
        stack.teardown()                 # 逆序：bridge.uninstall → server.stop
    """

    def __init__(self) -> None:
        """初始化空栈。"""
        self._teardowns: list[Callable[[], None]] = []
        self._lock: threading.RLock = threading.RLock()

    def push(self, teardown: Callable[[], None]) -> None:
        """登记一个逆操作。

        Args:
            teardown: 无参可调用，撤销对应装配步骤的副作用。
        """
        with self._lock:
            self._teardowns.append(teardown)

    def teardown(self) -> None:
        """按逆序执行全部已登记逆操作（幂等）。

        每项独立 try/except：单项失败不阻断其余，日志记录。
        """
        with self._lock:
            teardowns, self._teardowns = self._teardowns, []
        for td in reversed(teardowns):
            try:
                td()
            except Exception:
                logger.exception("逆操作执行失败（已隔离，继续回滚其余）")

    def __del__(self) -> None:
        """析构兜底：未显式 teardown 时尝试回滚。

        解释器退出/循环引用场景下 __del__ 不保证执行，
        正常路径仍应在销毁时显式调用 teardown()。
        """
        try:
            self.teardown()
        except Exception:
            pass
