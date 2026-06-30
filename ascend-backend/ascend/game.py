"""游戏引擎 — 串联 WorldGenerator、GameServer、EventBridge 和 MessageDispatcher。

在后台线程中运行 tick 循环，以固定频率处理传入的客户端消息。
"""

import threading
import time as _real_time

from ascend.log import get_logger
from ascend.net import GameServer, MessageDispatcher
from ascend.net.handlers.map_handler import make_map_handlers
from ascend.net.handlers.terminal_handler import make_terminal_handler
from ascend.space import WorldGenerator
from ascend.terminal import CommandExecutor
from ascend.time import WorldClock, GameCalendar
from ascend.i18n import I18n

logger = get_logger(__name__)

TICK_RATE: float = 30.0        # 后端 tick 频率（Hz）
TICK_DT: float = 1.0 / TICK_RATE
SERVER_HOST: str = "127.0.0.1"
SERVER_PORT: int = 9081


class GameEngine:
    """游戏引擎。在后台线程中运行，管理网络通信 + 世界生成。

    Usage:
        engine = GameEngine(seed=42)
        engine.start()
        # ... 运行中 ...
        engine.stop()
    """

    def __init__(self, seed: int = 0) -> None:
        """初始化引擎。

        Args:
            seed: 世界种子。
        """
        self.seed: int = seed
        self.world_gen: WorldGenerator | None = None
        self.server: GameServer | None = None
        self.dispatcher: MessageDispatcher | None = None
        self.clock: WorldClock = WorldClock()
        self.calendar: GameCalendar = GameCalendar()
        self.i18n: I18n = I18n()
        self._paused: bool = False
        self._executor: CommandExecutor | None = None
        self._running: bool = False
        self._thread: threading.Thread | None = None

    def __repr__(self) -> str:
        """返回引擎状态摘要。

        Returns:
            含种子、运行状态、客户端数的 repr 字符串。
        """
        client_count = self.server.client_count if self.server else 0
        return (
            f"GameEngine(seed={self.seed}, "
            f"running={self._running}, "
            f"paused={self._paused}, "
            f"clients={client_count})"
        )

    @property
    def paused(self) -> bool:
        """游戏是否暂停。

        Returns:
            True 表示暂停。
        """
        return self._paused

    @paused.setter
    def paused(self, value: bool) -> None:
        """设置暂停状态。

        Args:
            value: True 暂停，False 恢复。
        """
        self._paused = bool(value)

    def start(self) -> None:
        """初始化所有子系统并在后台启动 tick 循环。

        幂等：已在运行时调用无效果。
        """
        if self._running:
            return
        logger.info("游戏引擎启动: seed=%d", self.seed)

        # 1. 世界生成器
        self.world_gen = WorldGenerator(seed=self.seed)
        logger.debug("WorldGenerator 已创建")

        # 2. TCP 服务器
        self.server = GameServer(host=SERVER_HOST, port=SERVER_PORT)
        self.server.start()

        # 3. 消息分发器
        self.dispatcher = MessageDispatcher(self.server)
        handlers = make_map_handlers(self.world_gen)
        for req_type, handler in handlers.items():
            self.dispatcher.register(req_type, handler)
        logger.info("已注册地图处理程序: %s", list(handlers.keys()))

        # 4. 终端指令执行器
        self._executor = CommandExecutor(
            clock=self.clock,
            calendar=self.calendar,
            i18n=self.i18n,
            world_gen=self.world_gen,
        )
        term_handlers = make_terminal_handler(self._executor)
        for req_type, handler in term_handlers.items():
            self.dispatcher.register(req_type, handler)
        logger.info("已注册终端处理程序: %s", list(term_handlers.keys()))

        # 5. 启动 tick 循环
        self._running = True
        self._thread = threading.Thread(
            target=self._run_loop, name="game-engine", daemon=True
        )
        self._thread.start()
        logger.info("游戏引擎在后台运行 (tick=%.1f Hz)", TICK_RATE)

    def stop(self) -> None:
        """停止引擎并清理所有子系统。

        幂等：已停止时调用无效果。
        """
        if not self._running:
            return
        self._running = False
        if self._thread:
            self._thread.join(timeout=3.0)
            self._thread = None
        if self.server:
            self.server.stop()
            self.server = None
        if self.calendar:
            self.calendar.shutdown()
            self.calendar = None  # type: ignore[assignment]
        if self.world_gen:
            self.world_gen = None
        if self._executor:
            self._executor = None
        logger.info("游戏引擎已停止")

    # ── 内部 ──────────────────────────────────────────

    def _run_loop(self) -> None:
        """Tick 循环（运行在后台线程）。"""
        while self._running:
            tick_start = _real_time.monotonic()
            self._tick()
            elapsed = _real_time.monotonic() - tick_start
            sleep_time = TICK_DT - elapsed
            if sleep_time > 0:
                _real_time.sleep(sleep_time)

    def _tick(self) -> None:
        """单个 tick：推进时钟（非暂停时）+ 处理所有排队消息。"""
        if self.clock and not self._paused:
            self.clock.tick(TICK_DT)
            # 累加活跃时间供 st/rp 指令显示
            if hasattr(self, "_executor") and self._executor is not None:
                self._executor._active_real_time += TICK_DT
        if self.dispatcher:
            self.dispatcher.process()
