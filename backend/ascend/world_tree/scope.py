"""订阅作用域 — 统一收集并撤销事件订阅。

各模块向事件总线/时钟注册回调时，把返回的退订凭证交给作用域保管；
模块销毁时调用 close() 一次撤销全部订阅，不再由各模块自行维护
成对的 _unsub 变量与退订代码。

- 批量撤销：一次 close() 撤销全部已登记订阅，幂等。
- 生命周期归属：scope 随模块存活，模块销毁（含初始化中途失败）
  时经析构兜底回收订阅，避免泄漏。
- 线程安全：登记与撤销由内部锁保护。
"""

import threading
from collections.abc import Callable

from ascend.log import get_logger

logger = get_logger(__name__)


class SubscriptionScope:
    """事件订阅作用域。

    用法:
        scope = SubscriptionScope()
        scope.subscribe(wt, "minute_change", on_minute)
        scope.subscribe(clock, "tick", on_tick)      # 任意提供 subscribe 的对象
        ...
        scope.close()   # 撤销全部
    """

    def __init__(self) -> None:
        """初始化空作用域。"""
        self._unsubs: list[Callable[[], None]] = []
        self._lock: threading.RLock = threading.RLock()
        self._closed: bool = False

    def subscribe(
        self,
        bus,
        event_type: str,
        callback: Callable,
        location_filter=None,
    ) -> None:
        """订阅并登记退订凭证。

        Args:
            bus: 提供 subscribe(event_type, callback, location_filter) 的对象
                （WorldTree / WorldClock / 测试替身）。
            event_type: 事件类型或主题。
            callback: 事件回调。
            location_filter: 可选位置过滤（WorldTree 用）。
        """
        self.capture(
            bus.subscribe(event_type, callback, location_filter=location_filter)
        )

    def capture(self, unsubscribe: Callable[[], None]) -> None:
        """登记一个已获得的退订凭证。

        Args:
            unsubscribe: 订阅 API 返回的无参退订函数。
        """
        with self._lock:
            if self._closed:
                # 作用域已关闭后仍登记：立即撤销，避免凭证泄漏
                unsubscribe()
                return
            self._unsubs.append(unsubscribe)

    def close(self) -> None:
        """撤销全部已登记订阅（幂等）。

        每项退订独立 try/except：单项失败不阻断其余，日志记录。
        """
        with self._lock:
            if self._closed:
                return
            self._closed = True
            unsubs, self._unsubs = self._unsubs, []
        for unsub in unsubs:
            try:
                unsub()
            except Exception:
                logger.exception("订阅撤销失败（已隔离，继续撤销其余）")

    def __del__(self) -> None:
        """析构兜底：未显式 close 时回收订阅，防泄漏。

        解释器退出/循环引用场景下 __del__ 不保证执行，
        正常路径仍应在模块销毁时显式调用 close()。
        """
        try:
            self.close()
        except Exception:
            pass
