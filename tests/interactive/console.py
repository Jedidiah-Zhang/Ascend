"""Ascend 游戏控制台 — 交互式命令行。

游戏在后台线程实时运行，用户可以随时输入指令干预。

用法:
    PYTHONPATH=ascend-backend .venv/bin/python tests/interactive/console.py
"""

import sys
import time as _real_time
import threading
import readline
from pathlib import Path

# 确保 ascend-backend 在 Python 路径中
sys.path.insert(0, str(Path(__file__).parent.parent.parent / "ascend-backend"))

# 指令历史文件
_HISTORY_FILE = Path(__file__).parent / ".ascend_history"
_HISTORY_MAX = 1000

from ascend.world_tree import world_tree
from ascend.time import WorldClock, GameCalendar
from ascend.log import setup_logging, quiet_console, get_logger
from ascend.i18n import I18n
from ascend.terminal import CommandExecutor

logger = get_logger(__name__)
i18n = I18n()

# 帧率常量（与 executor 保持一致）
TICK_RATE: float = 60.0


# ── 控制台 ──────────────────────────────────────────────────────────

class GameConsole:
    """交互式游戏控制台。

    后台线程运行游戏循环，主线程处理用户输入指令。
    使用 CommandExecutor 处理所有指令路由和格式化。

    用法:
        console = GameConsole()
        console.run()
    """

    TICK_RATE = TICK_RATE

    def __init__(self) -> None:
        """初始化控制台：创建时钟、日历、指令执行器，准备后台线程。"""
        setup_logging()
        quiet_console()

        self.clock = WorldClock()
        self.calendar = GameCalendar()
        self._running = False
        self._start_real_time: float = 0.0
        self._thread: threading.Thread | None = None

        self._executor = CommandExecutor(
            clock=self.clock,
            calendar=self.calendar,
            i18n=i18n,
        )

        # 监控关键事件，输出到控制台
        self._setup_watchers()

    def __repr__(self) -> str:
        """返回调试用字符串表示。

        Returns:
            包含运行状态的字符串。
        """
        return (f"GameConsole(running={self._running}, "
                f"paused={self._executor.paused}, "
                f"clock={self.clock!r})")

    @staticmethod
    def _setup_watchers() -> None:
        """订阅关键事件，在控制台输出摘要。"""
        def on_day_end(event):
            print(f"\n  -- {i18n.t('console.day_end', day=event.data['day'])} --",
                  end="", flush=True)

        def on_day_change(event):
            print(f"\n  -- {i18n.t('console.day_start', day=event.data['day'])} --\n> ",
                  end="", flush=True)

        def on_hour_change(event):
            h = event.data["hour"]
            if 6 <= h <= 22:
                print(f"\n  {i18n.t('console.hour_bell', hour=f'{h:02d}')}\n> ", end="", flush=True)

        world_tree.subscribe("day_end", on_day_end)
        world_tree.subscribe("day_change", on_day_change)
        world_tree.subscribe("hour_change", on_hour_change)

    # ── 游戏循环（后台线程） ──────────────────────────────────────

    def _game_loop(self) -> None:
        """后台游戏循环：按帧率持续 tick，直到 _running 为 False。

        精确追踪活跃时间——暂停期间不计入 active_real_time。
        帧间耗时设上限，避免与主线程操作重复计时。
        暂停状态由 executor.paused 统一管理。
        """
        dt = 1.0 / self.TICK_RATE
        last = _real_time.monotonic()
        while self._running:
            now = _real_time.monotonic()
            elapsed = now - last
            last = now

            if not self._executor.paused:
                self.clock.tick()
                # 上限 2×dt，防止计入主线程耗时
                self._executor._active_real_time += min(elapsed, dt * 2)

            sleep_time = dt - (_real_time.monotonic() - now)
            if sleep_time > 0:
                _real_time.sleep(sleep_time)

    # ── 指令处理 ──────────────────────────────────────────────────

    @staticmethod
    def _load_history() -> None:
        """从文件加载指令历史。"""
        try:
            readline.read_history_file(str(_HISTORY_FILE))
        except (FileNotFoundError, PermissionError):
            pass

    @staticmethod
    def _save_history() -> None:
        """保存指令历史到文件。"""
        readline.set_history_length(_HISTORY_MAX)
        try:
            readline.write_history_file(str(_HISTORY_FILE))
        except PermissionError:
            pass

    # ── 生命周期 ──────────────────────────────────────────────────

    def run(self) -> None:
        """启动控制台：开始后台游戏循环，进入指令输入循环。"""
        self._load_history()

        welcome = i18n.t("console.welcome")
        hint = i18n.t("console.hint")
        width = max(len(welcome), len(hint)) + 4
        print("╔" + "═" * width + "╗")
        print(f"║  {welcome:<{width - 2}}║")
        print(f"║  {hint:<{width - 2}}║")
        print("╚" + "═" * width + "╝")
        print()

        self._running = True
        self._start_real_time = _real_time.monotonic()
        self._thread = threading.Thread(target=self._game_loop, daemon=True)
        self._thread.start()
        logger.info("游戏循环已启动 (%.0f fps)", self.TICK_RATE)

        try:
            self._input_loop()
        except KeyboardInterrupt:
            print("\n")
        finally:
            self._running = False
            if self._thread:
                self._thread.join(timeout=1.0)
            self.calendar.shutdown()
            self._save_history()
            logger.info("游戏控制台已关闭")
            print("  " + i18n.t("console.goodbye"))

    def _input_loop(self) -> None:
        """主指令输入循环。

        除了 wait 指令由本地处理外，其余全部委托给 CommandExecutor。
        """
        while self._running:
            try:
                raw = input("> ").strip()
            except (EOFError, KeyboardInterrupt):
                break

            if not raw:
                continue

            # wait 指令：纯本地 sleep，不经过 executor
            parts = raw.split()
            cmd = parts[0].lower()
            if cmd == "wait":
                secs = float(parts[1]) if len(parts) > 1 else 1.0
                _real_time.sleep(secs)
                continue

            # 其余指令全部委托给执行器
            result = self._executor.execute(raw)
            if result.output:
                print(result.output)
            if result.is_quit:
                break
