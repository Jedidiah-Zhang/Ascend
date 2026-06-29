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
_HISTORY_FILE = Path(__file__).parent.parent.parent / ".ascend_history"
_HISTORY_MAX = 1000

from ascend.world_tree import bus
from ascend.time import WorldClock, GameCalendar, TimeMode, GAME_DAY, GAME_HOUR
from ascend.log import setup_logging, quiet_console, get_logger
from ascend.i18n import I18n
from ascend.space import WorldGenerator, render_map, render_region_detail

logger = get_logger(__name__)
i18n = I18n()


# ── 控制台 ──────────────────────────────────────────────────────────

class GameConsole:
    """交互式游戏控制台。

    后台线程运行游戏循环，主线程处理用户输入指令。

    用法:
        console = GameConsole()
        console.run()
    """

    TICK_RATE = 60.0  # 每秒帧数

    def __init__(self) -> None:
        """初始化控制台：创建时钟、日历，准备后台线程。"""
        setup_logging()
        quiet_console()  # INFO 只写日志文件，不干扰控制台显示

        self.clock = WorldClock()
        self.calendar = GameCalendar()
        self._world_gen: WorldGenerator | None = None
        self._world_seed: int = 0
        self._running = False
        self._paused = False
        self._start_real_time: float = 0.0
        self._active_real_time: float = 0.0  # 仅游戏活跃时累加
        self._thread: threading.Thread | None = None

        # 监控关键事件，输出到控制台
        self._setup_watchers()

    @staticmethod
    def _mode_name(mode: TimeMode) -> str:
        """获取模式的翻译名称。

        Args:
            mode: 时间模式。

        Returns:
            翻译后的模式名称。
        """
        key_map = {
            TimeMode.REALTIME: "mode.realtime",
            TimeMode.SLEEP: "mode.sleep",
            TimeMode.FAST_TRAVEL: "mode.fast_travel",
            TimeMode.LONG_JUMP: "mode.long_jump",
        }
        return i18n.t(key_map.get(mode, ""))

    def _setup_watchers(self) -> None:
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

        bus.subscribe("day_end", on_day_end)
        bus.subscribe("day_change", on_day_change)
        bus.subscribe("hour_change", on_hour_change)

    # ── 游戏循环（后台线程） ──────────────────────────────────────

    def _game_loop(self) -> None:
        """后台游戏循环：按帧率持续 tick，直到 _running 为 False。

        精确追踪活跃时间——暂停期间不计入 active_real_time。
        帧间耗时设上限，避免与主线程操作重复计时。
        """
        dt = 1.0 / self.TICK_RATE
        last = _real_time.monotonic()
        while self._running:
            now = _real_time.monotonic()
            elapsed = now - last
            last = now

            if not self._paused:
                self.clock.tick(dt)
                # 上限 2×dt，防止计入主线程耗时
                self._active_real_time += min(elapsed, dt * 2)

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

    @staticmethod
    def _fmt_seconds(total_seconds: float) -> str:
        """将秒数格式化为人类可读字符串。

        Args:
            total_seconds: 总秒数。

        Returns:
            形如 "1h 23m 45s"、小于 1 秒时显示毫秒。
        """
        if total_seconds < 1.0:
            return f"{total_seconds * 1000:.0f}ms"
        s = int(total_seconds)
        h, m, sec = s // 3600, (s % 3600) // 60, s % 60
        if h > 0:
            return f"{h}h {m:02d}m {sec:02d}s"
        elif m > 0:
            return f"{m}m {sec:02d}s"
        return f"{sec}s"

    def _elapsed_real_str(self) -> str:
        """格式化的总运行时间（含暂停）。

        Returns:
            形如 "1h 23m 45s" 的字符串。
        """
        return self._fmt_seconds(_real_time.monotonic() - self._start_real_time)

    def _elapsed_active_str(self) -> str:
        """格式化的活跃时间（暂停不计）。

        Returns:
            形如 "1h 23m 45s" 的字符串。
        """
        return self._fmt_seconds(self._active_real_time)

    def _cmd_status(self) -> None:
        """显示当前游戏状态。"""
        t = self.clock.time
        day = self.calendar.day
        tod = self.calendar.time_of_day(t)
        hour = int(tod / GAME_HOUR)
        minute = int((tod % GAME_HOUR) / 60)
        second = int(tod % 60)
        state = i18n.t("console.state_paused" if self._paused else "console.state_running")
        print("  " + i18n.t("console.status",
                           active=f"{self._elapsed_active_str():>8s}",
                           day=day,
                           time=f"{hour:02d}:{minute:02d}:{second:02d}",
                           mode=self._mode_name(self.clock.mode),
                           state=state))

    def _cmd_pause(self) -> None:
        """暂停游戏时间。"""
        if self._paused:
            print("  " + i18n.t("console.already_paused"))
        else:
            self._paused = True
            print("  " + i18n.t("console.paused"))

    def _cmd_resume(self) -> None:
        """恢复游戏时间。"""
        if not self._paused:
            print("  " + i18n.t("console.already_running"))
        else:
            self._paused = False
            print("  " + i18n.t("console.resumed"))

    def _cmd_tick(self, count: int = 1) -> None:
        """手动推进 N 帧。暂停状态下也可使用。

        Args:
            count: 要推进的帧数，默认 1。
        """
        t0 = _real_time.monotonic()
        dt = 1.0 / self.TICK_RATE
        for _ in range(count):
            self.clock.tick(dt)
        self._active_real_time += (_real_time.monotonic() - t0)
        print("  " + i18n.t("console.ticked", count=count, time=f"{self.clock.time:,.0f}"))

    def _cmd_sleep(self, hours: float = 8.0) -> None:
        """睡眠指定小时数，支持小数。

        Args:
            hours: 睡眠小时数，默认 8。
        """
        t0 = _real_time.monotonic()
        target = self.clock.time + hours * GAME_HOUR
        self.clock.fast_forward(target, mode=TimeMode.SLEEP)
        self._active_real_time += (_real_time.monotonic() - t0)
        day = self.calendar.day
        tod = self.calendar.time_of_day(self.clock.time)
        h = int(tod / GAME_HOUR)
        m = int((tod % GAME_HOUR) / 60)
        print("  " + i18n.t("console.slept", hours=hours, day=day, time=f"{h:02d}:{m:02d}"))

    def _cmd_travel(self, hours: float = 1.0) -> None:
        """快速旅行指定小时数，支持小数。

        Args:
            hours: 旅行小时数，默认 1。
        """
        t0 = _real_time.monotonic()
        target = self.clock.time + hours * GAME_HOUR
        self.clock.fast_forward(target, mode=TimeMode.FAST_TRAVEL)
        self._active_real_time += (_real_time.monotonic() - t0)
        day = self.calendar.day
        tod = self.calendar.time_of_day(self.clock.time)
        h = int(tod / GAME_HOUR)
        m = int((tod % GAME_HOUR) / 60)
        print("  " + i18n.t("console.traveled", hours=hours, day=day, time=f"{h:02d}:{m:02d}"))

    def _cmd_skip(self, days: int) -> None:
        """跳过 N 天，落地到目标日 06:00。

        Args:
            days: 要跳过的天数。
        """
        t0 = _real_time.monotonic()
        target_day = self.calendar.day + days
        target = (target_day - 1) * GAME_DAY + 6 * GAME_HOUR
        self.clock.skip_to(target)
        self._active_real_time += (_real_time.monotonic() - t0)
        print("  " + i18n.t("console.jumped", days=days, day=self.calendar.day))

    def _cmd_mode(self, mode_name: str | None = None) -> None:
        """查看或切换时间模式。

        Args:
            mode_name: 模式名称（realtime/sleep/travel/jump），None 则查看当前。
        """
        mode_map = {
            "realtime": TimeMode.REALTIME,
            "sleep": TimeMode.SLEEP,
            "travel": TimeMode.FAST_TRAVEL,
            "jump": TimeMode.LONG_JUMP,
        }
        if mode_name is None:
            print("  " + i18n.t("console.mode_current",
                               desc=self._mode_name(self.clock.mode),
                               key=self.clock.mode.key))
            print("  " + i18n.t("console.mode_available", modes=", ".join(mode_map.keys())))
            return

        mode = mode_map.get(mode_name.lower())
        if mode is None:
            print("  " + i18n.t("console.mode_unknown",
                               name=mode_name, modes=", ".join(mode_map.keys())))
            return

        self.clock.set_mode(mode)
        print("  " + i18n.t("console.mode_switched", desc=self._mode_name(mode)))

    def _cmd_lang(self, lang_code: str | None = None) -> None:
        """查看或切换语言。

        Args:
            lang_code: 语言代码（zh_CN/en_US），None 则查看当前。
        """
        if lang_code is None:
            print("  " + i18n.t("console.lang_current", lang=i18n.lang))
            print("  " + i18n.t("console.lang_available",
                               langs=", ".join(i18n.available_langs())))
            return

        available = i18n.available_langs()
        if lang_code not in available:
            print("  " + i18n.t("console.lang_unknown",
                               name=lang_code, langs=", ".join(available)))
            return

        i18n.set_lang(lang_code)
        print("  " + i18n.t("console.lang_switched", lang=lang_code))

    def _cmd_events(self, count: int = 10) -> None:
        """显示最近 N 个事件。

        Args:
            count: 显示的事件数，默认 10。
        """
        total = bus.event_count
        if total == 0:
            print("  " + i18n.t("console.no_events"))
            return

        count = min(count, total)
        log = bus._event_log
        print("  " + i18n.t("console.events_header", count=count, total=total))
        print(f"  {'Time':>10s}  {'Type':<20s}  {'Initiator':<15s}  Summary")
        print(f"  {'─'*10}  {'─'*20}  {'─'*15}  {'─'*30}")

        for ev in log[-count:]:
            summary = ", ".join(f"{k}={v}" for k, v in list(ev.data.items())[:3])
            print(f"  {ev.timestamp:>10.0f}  {ev.event_type:<20s}  "
                  f"{ev.initiator_id:<15s}  {summary}")

    def _cmd_report(self) -> None:
        """显示运行报告。"""
        title = i18n.t("console.report_title")
        print(f"""  ══════════════════════════════════════
   {i18n.t('console.report_active')}:    {self._elapsed_active_str()}（{i18n.t('console.report_active_note')}）
   {i18n.t('console.report_total')}:  {self._elapsed_real_str()}
   {i18n.t('console.report_game_time')}:    {self.clock.time:,.0f}s
   {i18n.t('console.report_day')}:       {self.calendar.day}
   {i18n.t('console.report_elapsed')}:    {self.calendar.elapsed_days}
   {i18n.t('console.report_day_changes')}:    {self.calendar.day_change_count}
   {i18n.t('console.report_mode')}:    {self._mode_name(self.clock.mode)}
   {i18n.t('console.report_ticks')}:   {self.clock.tick_count:,}
   {i18n.t('console.report_events')}:    {bus.event_count:,}
  ══════════════════════════════════════""")

    def _cmd_map(self, args: list[str]) -> None:
        """显示世界地图（ASCII 渲染）。

        map [radius] [seed]         群系视图（默认 step=1）
        map climate [radius] [seed]   气候视图
        map altitude [radius] [seed]  海拔等高线
        map detail [radius] [seed]    区域详情
        map zoom [step] [radius]      指定采样步长

        Args:
            args: 命令参数列表。
        """
        # 解析参数
        mode = "biome"
        radius = 15
        step = 1
        seed = self._world_seed

        arg_idx = 0
        if args and args[arg_idx] in ("biome", "climate", "altitude", "detail", "zoom"):
            if args[arg_idx] == "zoom":
                mode = "biome"
                arg_idx += 1
                if arg_idx < len(args):
                    try:
                        step = int(args[arg_idx])
                        arg_idx += 1
                    except ValueError:
                        step = 20  # 默认 zoom=20
            else:
                mode = args[arg_idx]
                arg_idx += 1
        if arg_idx < len(args):
            try:
                radius = int(args[arg_idx])
                arg_idx += 1
            except ValueError:
                pass
        if arg_idx < len(args):
            try:
                seed = int(args[arg_idx])
            except ValueError:
                print(f"  无效种子: {args[arg_idx]}")
                return

        # 创建/更新 WorldGenerator
        if self._world_gen is None or seed != self._world_seed:
            self._world_seed = seed
            self._world_gen = WorldGenerator(seed=seed)

        print(f"  种子: {seed}  |  半径: {radius}  |  步长: {step}")
        if mode == "detail":
            print(render_region_detail(self._world_gen, radius=min(radius, 5)))
        else:
            print(render_map(self._world_gen, radius=radius, mode=mode, step=step))

    def _cmd_help(self) -> None:
        """显示帮助。"""
        print(f"""  ┌─ {i18n.t('console.welcome')} ──────────────────────────────┐
  │ st, status        {i18n.t('console.help_status'):<32s} │
  │ pa, pause         {i18n.t('console.help_pause'):<32s} │
  │ re, resume        {i18n.t('console.help_resume'):<32s} │
  │ wait [n]          {i18n.t('console.help_wait'):<32s} │
  │ tick [n]          {i18n.t('console.help_tick'):<32s} │
  │ sleep [n]         {i18n.t('console.help_sleep'):<32s} │
  │ travel [n]        {i18n.t('console.help_travel'):<32s} │
  │ jump [n]          {i18n.t('console.help_jump'):<32s} │
  │ mode [name]       {i18n.t('console.help_mode'):<32s} │
  │ lang [code]       {i18n.t('console.help_lang'):<32s} │
  │ map [r] [seed]    {i18n.t('console.help_map'):<32s} │
  │ events [n]        {i18n.t('console.help_events'):<32s} │
  │ rp, report        {i18n.t('console.help_report'):<32s} │
  │ ?, help           {i18n.t('console.help_help'):<32s} │
  │ q, quit, exit     {i18n.t('console.help_quit'):<32s} │
  └{'─' * 54}┘""")

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
        """主指令输入循环。"""
        while self._running:
            try:
                raw = input("> ").strip()
            except (EOFError, KeyboardInterrupt):
                break

            if not raw:
                continue

            parts = raw.split()
            cmd = parts[0].lower()
            args = parts[1:]

            try:
                if cmd in ("q", "quit", "exit"):
                    break
                elif cmd in ("st", "status"):
                    self._cmd_status()
                elif cmd in ("pa", "pause"):
                    self._cmd_pause()
                elif cmd in ("re", "resume"):
                    self._cmd_resume()
                elif cmd == "tick":
                    n = int(args[0]) if args else 1
                    self._cmd_tick(n)
                elif cmd == "wait":
                    secs = float(args[0]) if args else 1.0
                    _real_time.sleep(secs)
                elif cmd == "sleep":
                    n = float(args[0]) if args else 8.0
                    self._cmd_sleep(n)
                elif cmd == "travel":
                    n = float(args[0]) if args else 1.0
                    self._cmd_travel(n)
                elif cmd == "jump":
                    n = int(args[0]) if args else 1
                    self._cmd_skip(n)
                elif cmd == "mode":
                    self._cmd_mode(args[0] if args else None)
                elif cmd == "lang":
                    self._cmd_lang(args[0] if args else None)
                elif cmd == "map":
                    self._cmd_map(args)
                elif cmd == "events":
                    n = int(args[0]) if args else 10
                    self._cmd_events(n)
                elif cmd in ("rp", "report"):
                    self._cmd_report()
                elif cmd in ("?", "help"):
                    self._cmd_help()
                else:
                    print("  " + i18n.t("console.unknown_cmd", cmd=cmd))
            except ValueError as e:
                print(f"  参数错误: {e}")
            except Exception as e:
                logger.error("指令执行失败: %s", e)
                print(f"  错误: {e}")


# ── 入口 ───────────────────────────────────────────────────────────

if __name__ == "__main__":
    console = GameConsole()
    console.run()
