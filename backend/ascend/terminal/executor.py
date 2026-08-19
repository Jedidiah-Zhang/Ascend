"""指令执行器 — 解析并执行终端指令，返回结构化结果。

从 GameConsole 提取的核心指令逻辑，封装为无 UI 依赖的纯执行器。
指令路由采用 dict 映射（O(1) 查找）。

指令结构（Issue #2）:
    status                                  运行状态（时间 + 世界树统计）
    time [speed|pause|resume|jump|tick]     时间控制组（TimeCommandsMixin）
    weather [status|set]                    天气查询与强制控制组（WeatherCommandsMixin）
    entity [list|birth|death]               实体生灭调试组（EntityCommandsMixin）
    continent [status|regen]                大陆缓存诊断组（ContinentCommandsMixin）
    tp [x y]                                玩家传送（EntityCommandsMixin）
    lang / events / help / quit             独立指令

指令组按 mixin 拆分至同目录 *_commands.py，本类负责路由、参数解析、
格式化与独立指令实现。
"""

from collections.abc import Callable
from dataclasses import dataclass, field

from ascend.world_tree import world_tree
from ascend.log import get_logger
from ascend.i18n import I18n
from ascend.time import WorldClock, GameCalendar, GAME_YEAR
from ascend.time.calendar import tick_to_hms

from .continent_commands import ContinentCommandsMixin
from .entity_commands import EntityCommandsMixin
from .result import CommandResult
from .time_commands import TimeCommandsMixin
from .weather_commands import WeatherCommandsMixin

logger = get_logger(__name__)


@dataclass
class ExecutorConfig:
    """指令执行器的可选运行时服务依赖。

    集中承载指令组依赖的运行时服务（weather/entity/continent 等），
    替代构造时散落的长参数列表——新增指令组只需在此加字段，
    构造签名不变。

    Attributes:
        weather_engine: WeatherEngine 实例，用于 weather 指令。
        default_chunk: weather/entity 指令省略坐标时的默认 chunk。
        player_service: PlayerService 实例，用于 tp 指令。
        entity_manager: EntityManager 实例，用于 entity 指令。
        continent_path: 大陆缓存文件路径（存档内 continent.bin），
            用于 continent 指令；None = 无存档模式。
        gen_fingerprint_fn: 无参回调，返回当前生成环境指纹，
            用于 continent status 漂移诊断。
        world_tree: WorldTree 实例（测试注入隔离），
            默认使用模块级单例。
    """

    weather_engine: object = None
    default_chunk: tuple[int, int] | None = None
    player_service: object = None
    entity_manager: object = None
    continent_path: str | None = None
    gen_fingerprint_fn: object = None
    world_tree: object = None


class CommandExecutor(
    TimeCommandsMixin,
    WeatherCommandsMixin,
    EntityCommandsMixin,
    ContinentCommandsMixin,
):
    """指令执行器。

    解析指令字符串，调用对应逻辑，返回结构化结果。
    指令路由采用 dict 映射（O(1) 查找），第三方可通过 `register_command`
    注入新指令，不修改核心代码。

    Usage:
        executor = CommandExecutor(clock, calendar, I18n(),
                                   config=ExecutorConfig(...))
        result = executor.execute("status")
        print(result.output)
    """

    # 退出指令集（集合查找 O(1)）
    _QUIT_CMDS: frozenset = frozenset({"q", "quit", "exit"})

    def __init__(
        self,
        clock: WorldClock,
        calendar: GameCalendar,
        i18n: I18n,
        config: ExecutorConfig | None = None,
    ) -> None:
        """初始化指令执行器。

        核心依赖（时钟/日历/国际化）为必选参数；可选运行时服务
        （天气/实体/玩家/大陆等）经 ExecutorConfig 聚合传入。

        Args:
            clock: 世界时钟实例。
            calendar: 游戏日历实例。
            i18n: 国际化实例。
            config: 可选运行时服务依赖；None = 全部缺省
                （weather/entity/continent 等指令不可用）。
        """
        config = config or ExecutorConfig()
        self._clock = clock
        self._calendar = calendar
        self._i18n = i18n
        self._weather = config.weather_engine
        self._default_chunk = config.default_chunk or (0, 0)
        self._player = config.player_service
        self._entities = config.entity_manager
        self._continent_path = config.continent_path
        self._gen_fingerprint_fn = config.gen_fingerprint_fn
        self._wt = config.world_tree if config.world_tree is not None else world_tree
        self._active_real_time: float = 0.0

        # 指令路由表：{cmd_name: handler_func(args) -> CommandResult}
        # 可被外部扩展（mod 注入）
        self._handlers: dict[str, Callable[[list[str]], CommandResult]] = {
            "status":    lambda a: CommandResult(success=True, output=self._cmd_status()),
            "time":      self._h_time,
            "weather":   self._h_weather,
            "entity":    self._h_entity,
            "continent": self._h_continent,
            "tp":        self._h_tp,
            "lang":      self._h_lang,
            "events":    self._h_events,
            "?":         lambda a: CommandResult(success=True, output=self._cmd_help()),
            "help":      lambda a: CommandResult(success=True, output=self._cmd_help()),
        }

    @property
    def paused(self) -> bool:
        """游戏时间是否暂停（透传时钟状态）。"""
        return self._clock.paused

    def add_active_time(self, dt: float) -> None:
        """累加活跃时间（由 GameEngine 每 tick 调用）。

        Args:
            dt: 真实时间增量（秒）。
        """
        self._active_real_time += dt

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
            f"CommandExecutor(time={self._clock.time}t, "
            f"speed=×{self._clock.speed:.1f}, "
            f"paused={self._clock.paused})"
        )

    # ── 公共接口 ────────────────────────────────────────

    def execute(self, command: str) -> CommandResult:
        """执行一条指令字符串。

        用 dict 映射代替 if/elif 链，O(1) 查找。
        quit 指令由 frozenset 快速匹配。

        Args:
            command: 原始指令字符串（如 "time tick 5", "status", "lang en_US"）。

        Returns:
            指令执行结果。
        """
        raw = command.strip()
        if not raw:
            return CommandResult(success=True, output="")

        parts = raw.split()
        cmd = parts[0].lower()
        args = parts[1:]

        # 指令路由：O(1) dict 查找（注册的 quit handler 优先于内置退出词）
        handler = self._handlers.get(cmd)
        if handler is not None:
            return handler(args)

        # 内置退出指令（未注册专用 handler 时的兜底）
        if cmd in CommandExecutor._QUIT_CMDS:
            return CommandResult(success=True, output="", is_quit=True)

        # 未知指令
        return CommandResult(
            success=False,
            output=self._i18n.t("console.unknown_cmd", cmd=cmd),
        )

    # ── 参数解析辅助 ────────────────────────────────────

    @staticmethod
    def _parse_int(args: list[str], idx: int, default: int) -> int | None:
        """解析 args[idx] 为 int，缺省返回 default，非法返回 None。

        Args:
            args: 参数列表。
            idx: 目标索引。
            default: 参数缺省时的默认值。

        Returns:
            解析结果，非法输入返回 None。
        """
        if idx >= len(args):
            return default
        try:
            return int(args[idx])
        except ValueError:
            return None

    def _parse_chunk(self, args: list[str]) -> tuple[int, int] | None:
        """从参数列表解析 chunk 坐标，缺省用 default_chunk。

        Args:
            args: 坐标参数（空 或 [cx, cy]）。

        Returns:
            (cx, cy)，参数个数错误或非整数时返回 None。
        """
        if not args:
            return self._default_chunk
        if len(args) != 2:
            return None
        try:
            return (int(args[0]), int(args[1]))
        except ValueError:
            return None

    # ── 其余指令处理程序 ────────────────────────────────

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

    def _h_events(self, args: list[str]) -> CommandResult:
        """处理 events 指令，验证条数参数。

        Args:
            args: 参数列表，第一个参数为条数。

        Returns:
            执行结果。
        """
        count = self._parse_int(args, 0, 10)
        if count is None or count < 1:
            return CommandResult(
                success=False,
                output=self._i18n.t("console.invalid_number",
                                    value=args[0] if args else ""),
            )
        return CommandResult(success=True, output=self._cmd_events(count))

    # ── 格式化辅助 ──────────────────────────────────────

    def _speed_label(self) -> str:
        """获取当前速度标签。

        Returns:
            格式化的速度文本。
        """
        if self._clock.paused:
            return self._i18n.t("mode.paused")
        s = self._clock.speed
        if s == 1.0:
            return self._i18n.t("mode.realtime")
        return f"×{s:.1f}"

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

    def _fmt_time_of_day(self) -> str:
        """格式化当前时刻为 HH:MM:SS。

        Returns:
            格式化后的当日时间字符串。
        """
        hour, minute, second = tick_to_hms(self._clock.time)
        return f"{hour:02d}:{minute:02d}:{second:02d}"

    @staticmethod
    def _fmt_hour(hour_float: float) -> str:
        """小数小时 → HH:MM 文本。

        Args:
            hour_float: 小时数（如 6.2）。

        Returns:
            格式化后的字符串（如 "06:12"）。
        """
        h = int(hour_float)
        m = int((hour_float - h) * 60)
        return f"{h:02d}:{m:02d}"

    # ── 顶层指令实现 ────────────────────────────────────

    def _cmd_status(self) -> str:
        """生成运行状态报告（合并原 st + report）。

        Returns:
            首行时间概览 + 世界树统计的多行文本。
        """
        state = self._i18n.t(
            "console.state_paused" if self._clock.paused else "console.state_running"
        )
        stats = self._wt.stats
        lines = [
            self._i18n.t(
                "console.status",
                active=self._fmt_active_time(),
                day=self._calendar.day,
                time=self._fmt_time_of_day(),
                mode=self._speed_label(),
                state=state,
            ),
            f"  {self._i18n.t('console.report_game_time')}:    {self._clock.time:,}t",
            f"  {self._i18n.t('console.report_elapsed')}:    {self._calendar.elapsed_days}",
            f"  {self._i18n.t('console.report_day_changes')}:    {self._calendar.day_change_count}",
            f"  {self._i18n.t('console.report_ticks')}:   {self._clock.tick_count:,}",
            f"  {self._i18n.t('console.report_events')}:    {self._wt.event_count:,}",
            f"  ---",
            f"  publish:     {stats['publish_count']:,}",
            f"  trim:        {stats['trim_count']} (cycle={stats['trim_cycle']})",
            f"  subscribers: {stats['subscriber_count']}",
            f"  graph nodes: {stats['graph_nodes']}",
            f"  archive:     {stats['archive_event_count']:,}",
        ]
        return "\n".join(lines)

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
        """显示最近 N 条事件。

        Args:
            count: 显示的事件条数。

        Returns:
            事件列表文本。
        """
        total = self._wt.event_count
        if total == 0:
            return self._i18n.t("console.no_events")

        count = min(count, total)
        now = self._clock.time
        start = max(0, now - GAME_YEAR)
        events = self._wt.get_events_in_range(start, now)
        log = events[-count:] if len(events) >= count else events
        lines = [self._i18n.t("console.events_header", count=min(count, len(log)), total=total)]
        time_hdr = self._i18n.t("console.events_col_time")
        type_hdr = self._i18n.t("console.events_col_type")
        init_hdr = self._i18n.t("console.events_col_initiator")
        sum_hdr = self._i18n.t("console.events_col_summary")
        lines.append(f"  {time_hdr:>10s}  {type_hdr:<20s}  {init_hdr:<15s}  {sum_hdr}")
        lines.append(f"  {'─'*10}  {'─'*20}  {'─'*15}  {'─'*30}")

        for ev in log:
            summary = ", ".join(f"{k}={v}" for k, v in list(ev.data.items())[:3])
            lines.append(
                f"  {ev.timestamp:>10d}  {ev.event_type:<20s}  "
                f"{ev.initiator_id:<15s}  {summary}"
            )

        return "\n".join(lines)

    def _cmd_help(self) -> str:
        """生成帮助文本。

        Returns:
            包含所有指令说明的帮助文本。
        """
        t = self._i18n.t
        lines = [
            f"  status                                   {t('console.help_status')}",
            f"  time                                     {t('console.help_time')}",
            f"  time speed <n>                           {t('console.help_time_speed')}",
            f"  time pause | resume                      {t('console.help_time_pause')}",
            f"  time jump [d]                            {t('console.help_time_jump')}",
            f"  time tick [n]                            {t('console.help_time_tick')}",
            f"  weather status [cx cy]                   {t('console.help_weather_status')}",
            f"  weather set rain <on|off> [cx cy]        {t('console.help_weather_rain')}",
            f"  weather set <feature> <on|off> [cx cy]   {t('console.help_weather_feature')}",
            f"  entity [list]                            {t('console.help_entity_list')}",
            f"  entity birth <type> [x y]                {t('console.help_entity_birth')}",
            f"  entity death <id>                        {t('console.help_entity_death')}",
            f"  continent status | regen                   {t('console.help_continent')}",
            f"  tp [x y]                                 {t('console.help_tp')}",
            f"  lang [code]                              {t('console.help_lang')}",
            f"  events [n]                               {t('console.help_events')}",
            f"  ?, help                                  {t('console.help_help')}",
            f"  q, quit, exit                            {t('console.help_quit')}",
        ]
        return "\n".join(lines)
