"""指令执行器 — 解析并执行终端指令，返回结构化结果。

从 GameConsole 提取的核心指令逻辑，封装为无 UI 依赖的纯执行器。
指令路由采用 dict 映射（O(1) 查找），替代 if/elif 链。
"""

from collections.abc import Callable
from dataclasses import dataclass
from typing import Optional

from ascend.world_tree import bus
from ascend.log import get_logger
from ascend.i18n import I18n
from ascend.time import WorldClock, GameCalendar, TimeMode, GAME_DAY, GAME_HOUR

logger = get_logger(__name__)


@dataclass
class CommandResult:
    """指令执行结果。

    Attributes:
        success: 是否成功执行。
        output: 执行输出的文本（空字符串表示无输出）。
        is_quit: 是否为退出指令。
    """
    success: bool = True
    output: str = ""
    is_quit: bool = False


class CommandExecutor:
    """指令执行器。

    解析指令字符串，调用对应逻辑，返回结构化结果。
    指令路由采用 dict 映射（O(1) 查找），第三方可通过 `register_command`
    注入新指令，不修改核心代码。

    Usage:
        executor = CommandExecutor(clock, calendar, I18n())
        result = executor.execute("st")
        print(result.output)
    """

    # 退出指令集（集合查找 O(1)）
    _QUIT_CMDS: frozenset = frozenset({"q", "quit", "exit"})

    # 用于 mode 指令的快速验证集
    _VALID_MODES: frozenset = frozenset({"realtime", "sleep", "travel", "jump"})

    def __init__(
        self,
        clock: WorldClock,
        calendar: GameCalendar,
        i18n: I18n,
        world_gen=None,
    ) -> None:
        """初始化指令执行器。

        Args:
            clock: 世界时钟实例。
            calendar: 游戏日历实例。
            i18n: 国际化实例。
            world_gen: 可选的 WorldGenerator 实例，用于 map 指令。
        """
        self._clock = clock
        self._calendar = calendar
        self._i18n = i18n
        self._world_gen = world_gen
        self._paused: bool = False
        self._active_real_time: float = 0.0
        self._mode_name_cache = {
            TimeMode.REALTIME: "mode.realtime",
            TimeMode.SLEEP: "mode.sleep",
            TimeMode.FAST_TRAVEL: "mode.fast_travel",
            TimeMode.LONG_JUMP: "mode.long_jump",
        }

        # 指令路由表：{cmd_name: handler_func(args) -> CommandResult}
        # 可被外部扩展（mod 注入）
        self._handlers: dict[str, Callable[[list[str]], CommandResult]] = {
            "st":      lambda a: CommandResult(success=True, output=self._cmd_status()),
            "status":  lambda a: CommandResult(success=True, output=self._cmd_status()),
            "pa":      lambda a: CommandResult(success=True, output=self._cmd_pause()),
            "pause":   lambda a: CommandResult(success=True, output=self._cmd_pause()),
            "re":      lambda a: CommandResult(success=True, output=self._cmd_resume()),
            "resume":  lambda a: CommandResult(success=True, output=self._cmd_resume()),
            "tick":    self._h_tick,
            "sleep":   lambda a: CommandResult(success=True, output=self._cmd_sleep(float(a[0]) if a else 8.0)),
            "travel":  lambda a: CommandResult(success=True, output=self._cmd_travel(float(a[0]) if a else 1.0)),
            "jump":    lambda a: CommandResult(success=True, output=self._cmd_jump(int(a[0]) if a else 1)),
            "mode":    self._h_mode,
            "lang":    self._h_lang,
            "events":  lambda a: CommandResult(success=True, output=self._cmd_events(int(a[0]) if a else 10)),
            "rp":      lambda a: CommandResult(success=True, output=self._cmd_report()),
            "report":  lambda a: CommandResult(success=True, output=self._cmd_report()),
            "map":     lambda a: CommandResult(success=True, output=self._cmd_map(list(a))),
            "?":       lambda a: CommandResult(success=True, output=self._cmd_help()),
            "help":    lambda a: CommandResult(success=True, output=self._cmd_help()),
        }

    def register_command(self, name: str, handler: Callable[[list[str]], CommandResult]) -> None:
        """注册新指令（供 mod 和扩展使用）。

        Args:
            name: 指令名称（小写，不含空格）。
            handler: 接收 args 列表、返回 CommandResult 的函数。
        """
        self._handlers[name] = handler

    def __repr__(self) -> str:
        """返回执行器状态摘要。

        Returns:
            含类名、模式、暂停状态的字符串。
        """
        return (
            f"CommandExecutor(mode={self._clock.mode.key}, "
            f"paused={self._paused})"
        )

    # ── 公共接口 ────────────────────────────────────────

    def execute(self, command: str) -> CommandResult:
        """执行一条指令字符串。

        用 dict 映射代替 if/elif 链，O(1) 查找。
        quit 指令由 frozenset 快速匹配。

        Args:
            command: 原始指令字符串（如 "tick 5", "st", "lang en_US"）。

        Returns:
            指令执行结果。
        """
        raw = command.strip()
        if not raw:
            return CommandResult(success=True, output="")

        parts = raw.split()
        cmd = parts[0].lower()
        args = parts[1:]

        # quit — 独立处理 is_quit 标志
        if cmd in CommandExecutor._QUIT_CMDS:
            return CommandResult(success=True, output="", is_quit=True)

        # 指令路由：O(1) dict 查找
        handler = self._handlers.get(cmd)
        if handler is not None:
            return handler(args)

        # 未知指令
        return CommandResult(
            success=False,
            output=self._i18n.t("console.unknown_cmd", cmd=cmd),
        )

    # ── 指令处理程序（参数验证 + 结果包装）───────────────

    def _h_tick(self, args: list[str]) -> CommandResult:
        """处理 tick 指令，验证参数为整数。

        Args:
            args: 参数列表，第一个参数为 tick 次数。

        Returns:
            执行结果。
        """
        if args:
            try:
                count = int(args[0])
            except ValueError:
                return CommandResult(
                    success=False,
                    output=self._i18n.t("console.unknown_cmd", cmd="tick " + args[0]),
                )
        else:
            count = 1
        return CommandResult(success=True, output=self._cmd_tick(count))

    def _h_mode(self, args: list[str]) -> CommandResult:
        """处理 mode 指令，验证模式名称。

        Args:
            args: 参数列表，第一个参数为模式名称。

        Returns:
            执行结果。
        """
        mode_name = args[0] if args else None
        output = self._cmd_mode(mode_name)
        # 验证失败时返回 success=False
        if mode_name is not None and mode_name.lower() not in self._VALID_MODES:
            return CommandResult(success=False, output=output)
        return CommandResult(success=True, output=output)

    def _h_lang(self, args: list[str]) -> CommandResult:
        """处理 lang 指令，验证语言代码。

        Args:
            args: 参数列表，第一个参数为语言代码。

        Returns:
            执行结果。
        """
        lang_code = args[0] if args else None
        output = self._cmd_lang(lang_code)
        # 验证失败时返回 success=False
        if lang_code is not None and lang_code not in self._i18n.available_langs():
            return CommandResult(success=False, output=output)
        return CommandResult(success=True, output=output)

    # ── 内部实现 ────────────────────────────────────────

    def _mode_name(self, mode: TimeMode) -> str:
        """获取模式的中文名称。

        Args:
            mode: 时间模式。

        Returns:
            翻译后的模式名称。
        """
        key = self._mode_name_cache.get(mode, "")
        return self._i18n.t(key) if key else mode.key

    def _fmt_active_time(self) -> str:
        """格式化活跃时间为 'Xh Ym Zs'。

        Returns:
            格式化后的活跃时间字符串。
        """
        total_sec = int(self._active_real_time)
        h = total_sec // 3600
        m = (total_sec % 3600) // 60
        s = total_sec % 60
        return f"{h}h {m:02d}m {s:02d}s"

    def _cmd_status(self) -> str:
        """生成状态文本。

        Returns:
            包含时间、日、模式、状态的字符串。
        """
        t = self._clock.time
        day = self._calendar.day
        tod = self._calendar.time_of_day(t)
        hour = int(tod / GAME_HOUR)
        minute = int((tod % GAME_HOUR) / 60)
        second = int(tod % 60)
        state = self._i18n.t("console.state_paused" if self._paused else "console.state_running")
        return self._i18n.t(
            "console.status",
            active=self._fmt_active_time(),
            day=day,
            time=f"{hour:02d}:{minute:02d}:{second:02d}",
            mode=self._mode_name(self._clock.mode),
            state=state,
        )

    def _cmd_pause(self) -> str:
        """暂停游戏时间。

        Returns:
            暂停确认文本。
        """
        if self._paused:
            return self._i18n.t("console.already_paused")
        self._paused = True
        return self._i18n.t("console.paused")

    def _cmd_resume(self) -> str:
        """恢复游戏时间。

        Returns:
            恢复确认文本。
        """
        if not self._paused:
            return self._i18n.t("console.already_running")
        self._paused = False
        return self._i18n.t("console.resumed")

    def _cmd_tick(self, count: int = 1) -> str:
        """手动推进 N 帧。

        Args:
            count: 要推进的帧数。

        Returns:
            推进确认文本。
        """
        dt = 1.0 / 60.0
        for _ in range(count):
            self._clock.tick(dt)
        return self._i18n.t("console.ticked", count=count, time=f"{self._clock.time:,.0f}")

    def _cmd_sleep(self, hours: float = 8.0) -> str:
        """睡眠指定小时数。

        Args:
            hours: 睡眠小时数。

        Returns:
            睡眠后状态文本。
        """
        target = self._clock.time + hours * GAME_HOUR
        self._clock.fast_forward(target, mode=TimeMode.SLEEP)
        day = self._calendar.day
        tod = self._calendar.time_of_day(self._clock.time)
        h = int(tod / GAME_HOUR)
        m = int((tod % GAME_HOUR) / 60)
        return self._i18n.t("console.slept", hours=hours, day=day, time=f"{h:02d}:{m:02d}")

    def _cmd_travel(self, hours: float = 1.0) -> str:
        """快速旅行指定小时数。

        Args:
            hours: 旅行小时数。

        Returns:
            旅行后状态文本。
        """
        target = self._clock.time + hours * GAME_HOUR
        self._clock.fast_forward(target, mode=TimeMode.FAST_TRAVEL)
        day = self._calendar.day
        tod = self._calendar.time_of_day(self._clock.time)
        h = int(tod / GAME_HOUR)
        m = int((tod % GAME_HOUR) / 60)
        return self._i18n.t("console.traveled", hours=hours, day=day, time=f"{h:02d}:{m:02d}")

    def _cmd_jump(self, days: int = 1) -> str:
        """跳过 N 天，落地到目标日 06:00。

        Args:
            days: 要跳过的天数。

        Returns:
            跳转后状态文本。
        """
        target_day = self._calendar.day + days
        target = (target_day - 1) * GAME_DAY + 6 * GAME_HOUR
        self._clock.skip_to(target)
        return self._i18n.t("console.jumped", days=days, day=self._calendar.day)

    def _cmd_mode(self, mode_name: str | None = None) -> str:
        """查看或切换时间模式。

        Args:
            mode_name: 模式名称，None 则查看当前。

        Returns:
            模式信息或切换确认文本。
        """
        mode_map = {
            "realtime": TimeMode.REALTIME,
            "sleep": TimeMode.SLEEP,
            "travel": TimeMode.FAST_TRAVEL,
            "jump": TimeMode.LONG_JUMP,
        }
        if mode_name is None:
            current = self._i18n.t(
                "console.mode_current",
                desc=self._mode_name(self._clock.mode),
                mode_key=self._clock.mode.key,
            )
            available = self._i18n.t(
                "console.mode_available",
                modes=", ".join(mode_map.keys()),
            )
            return current + "\n" + available

        mode = mode_map.get(mode_name.lower())
        if mode is None:
            return self._i18n.t(
                "console.mode_unknown",
                name=mode_name,
                modes=", ".join(mode_map.keys()),
            )

        self._clock.set_mode(mode)
        return self._i18n.t("console.mode_switched", desc=self._mode_name(mode))

    def _cmd_lang(self, lang_code: str | None = None) -> str:
        """查看或切换语言。

        Args:
            lang_code: 语言代码，None 则查看当前。

        Returns:
            语言信息或切换确认文本。
        """
        if lang_code is None:
            current = self._i18n.t("console.lang_current", lang=self._i18n.lang)
            available = self._i18n.t(
                "console.lang_available",
                langs=", ".join(self._i18n.available_langs()),
            )
            return current + "\n" + available

        available = self._i18n.available_langs()
        if lang_code not in available:
            return self._i18n.t(
                "console.lang_unknown",
                name=lang_code,
                langs=", ".join(available),
            )

        self._i18n.set_lang(lang_code)
        return self._i18n.t("console.lang_switched", lang=lang_code)

    def _cmd_events(self, count: int = 10) -> str:
        """显示最近 N 个事件。

        Args:
            count: 显示的事件数。

        Returns:
            事件列表文本。
        """
        total = bus.event_count
        if total == 0:
            return self._i18n.t("console.no_events")

        count = min(count, total)
        log = bus._event_log
        lines = [self._i18n.t("console.events_header", count=count, total=total)]
        lines.append(f"  {'Time':>10s}  {'Type':<20s}  {'Initiator':<15s}  Summary")
        lines.append(f"  {'─'*10}  {'─'*20}  {'─'*15}  {'─'*30}")

        for ev in log[-count:]:
            summary = ", ".join(f"{k}={v}" for k, v in list(ev.data.items())[:3])
            lines.append(
                f"  {ev.timestamp:>10.0f}  {ev.event_type:<20s}  "
                f"{ev.initiator_id:<15s}  {summary}"
            )

        return "\n".join(lines)

    def _cmd_report(self) -> str:
        """生成运行报告。

        Returns:
            包含各项统计的运行报告文本。
        """
        lines = [
            f"  {self._i18n.t('console.report_active')}:    {self._fmt_active_time()}",
            f"  {self._i18n.t('console.report_game_time')}:    {self._clock.time:,.0f}s",
            f"  {self._i18n.t('console.report_day')}:       {self._calendar.day}",
            f"  {self._i18n.t('console.report_elapsed')}:    {self._calendar.elapsed_days}",
            f"  {self._i18n.t('console.report_day_changes')}:    {self._calendar.day_change_count}",
            f"  {self._i18n.t('console.report_mode')}:    {self._mode_name(self._clock.mode)}",
            f"  {self._i18n.t('console.report_ticks')}:   {self._clock.tick_count:,}",
            f"  {self._i18n.t('console.report_events')}:    {bus.event_count:,}",
        ]
        return "\n".join(lines)

    def _cmd_map(self, args: list[str]) -> str:
        """显示世界地图（ASCII 渲染）。

        Args:
            args: 命令参数列表。

        Returns:
            ASCII 地图文本。
        """
        if self._world_gen is None:
            return "WorldGenerator 未提供"

        # 解析参数
        radius = 15
        step = 1
        seed = 0

        arg_idx = 0
        mode = "biome"
        if args and args[arg_idx] in ("biome", "climate", "altitude", "detail", "zoom"):
            if args[arg_idx] == "zoom":
                mode = "biome"
                arg_idx += 1
                step = int(args[arg_idx]) if arg_idx < len(args) else 20
                arg_idx += 1
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
                return f"  无效种子: {args[arg_idx]}"

        from ascend.space import render_map, render_region_detail

        output = f"  种子: {seed}  |  半径: {radius}  |  步长: {step}\n"
        if mode == "detail":
            output += render_region_detail(self._world_gen, radius=min(radius, 5))
        else:
            output += render_map(self._world_gen, radius=radius, mode=mode, step=step)
        return output

    def _cmd_help(self) -> str:
        """生成帮助文本。

        Returns:
            包含所有指令说明的帮助文本。
        """
        lines = [
            f"  st, status        {self._i18n.t('console.help_status')}",
            f"  pa, pause         {self._i18n.t('console.help_pause')}",
            f"  re, resume        {self._i18n.t('console.help_resume')}",
            f"  tick [n]          {self._i18n.t('console.help_tick')}",
            f"  sleep [n]         {self._i18n.t('console.help_sleep')}",
            f"  travel [n]        {self._i18n.t('console.help_travel')}",
            f"  jump [n]          {self._i18n.t('console.help_jump')}",
            f"  mode [name]       {self._i18n.t('console.help_mode')}",
            f"  lang [code]       {self._i18n.t('console.help_lang')}",
            f"  events [n]        {self._i18n.t('console.help_events')}",
            f"  rp, report        {self._i18n.t('console.help_report')}",
            f"  ?, help           {self._i18n.t('console.help_help')}",
            f"  q, quit, exit     {self._i18n.t('console.help_quit')}",
        ]
        return "\n".join(lines)
