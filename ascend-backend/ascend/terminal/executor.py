"""指令执行器 — 解析并执行终端指令，返回结构化结果。

从 GameConsole 提取的核心指令逻辑，封装为无 UI 依赖的纯执行器。
指令路由采用 dict 映射（O(1) 查找）。

指令结构（Issue #2）:
    status                                  运行状态（时间 + 世界树统计）
    time [speed|pause|resume|jump|tick]     时间控制组
    weather [status|set]                    天气查询与强制控制组
    lang / events / map / help / quit       独立指令
"""

from collections.abc import Callable
from dataclasses import dataclass

from ascend.world_tree import world_tree
from ascend.log import get_logger
from ascend.i18n import I18n
from ascend.time import WorldClock, GameCalendar, GAME_DAY, GAME_HOUR, GAME_MINUTE, GAME_YEAR

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
        result = executor.execute("status")
        print(result.output)
    """

    # 退出指令集（集合查找 O(1)）
    _QUIT_CMDS: frozenset = frozenset({"q", "quit", "exit"})

    # weather set 的合法状态词
    _ON_OFF: frozenset = frozenset({"on", "off"})

    def __init__(
        self,
        clock: WorldClock,
        calendar: GameCalendar,
        i18n: I18n,
        world_gen=None,
        weather_engine=None,
        default_chunk: tuple[int, int] | None = None,
        player_service=None,
    ) -> None:
        """初始化指令执行器。

        Args:
            clock: 世界时钟实例。
            calendar: 游戏日历实例。
            i18n: 国际化实例。
            world_gen: 可选的 WorldGenerator 实例，用于 map 指令。
            weather_engine: 可选的 WeatherEngine 实例，用于 weather 指令。
            default_chunk: weather 指令省略坐标时的默认 chunk（通常为出生点）。
            player_service: 可选的 PlayerService 实例，用于 tp 指令。
        """
        self._clock = clock
        self._calendar = calendar
        self._i18n = i18n
        self._world_gen = world_gen
        self._weather = weather_engine
        self._default_chunk = default_chunk or (0, 0)
        self._player = player_service
        self._active_real_time: float = 0.0

        # 指令路由表：{cmd_name: handler_func(args) -> CommandResult}
        # 可被外部扩展（mod 注入）
        self._handlers: dict[str, Callable[[list[str]], CommandResult]] = {
            "status":  lambda a: CommandResult(success=True, output=self._cmd_status()),
            "time":    self._h_time,
            "weather": self._h_weather,
            "tp":      self._h_tp,
            "lang":    self._h_lang,
            "events":  self._h_events,
            "map":     lambda a: CommandResult(success=True, output=self._cmd_map(list(a))),
            "?":       lambda a: CommandResult(success=True, output=self._cmd_help()),
            "help":    lambda a: CommandResult(success=True, output=self._cmd_help()),
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

    # ── time 指令组 ─────────────────────────────────────

    def _h_time(self, args: list[str]) -> CommandResult:
        """处理 time 指令组：无参查看状态，子指令控制时间。

        Args:
            args: 参数列表，args[0] 为子指令（speed/pause/resume/jump/tick）。

        Returns:
            执行结果。
        """
        if not args:
            return CommandResult(success=True, output=self._cmd_time_status())

        sub = args[0].lower()
        rest = args[1:]
        if sub == "speed":
            return self._h_time_speed(rest)
        if sub == "pause":
            return CommandResult(success=True, output=self._cmd_pause())
        if sub == "resume":
            return CommandResult(success=True, output=self._cmd_resume())
        if sub == "jump":
            days = self._parse_int(rest, 0, 1)
            if days is None or days < 1:
                return CommandResult(
                    success=False,
                    output=self._i18n.t("console.invalid_number",
                                        value=rest[0] if rest else ""),
                )
            return CommandResult(success=True, output=self._cmd_jump(days))
        if sub == "tick":
            count = self._parse_int(rest, 0, 1)
            if count is None or count < 1:
                return CommandResult(
                    success=False,
                    output=self._i18n.t("console.invalid_number",
                                        value=rest[0] if rest else ""),
                )
            return CommandResult(success=True, output=self._cmd_tick(count))
        return CommandResult(
            success=False, output=self._i18n.t("console.time_usage"),
        )

    def _h_time_speed(self, args: list[str]) -> CommandResult:
        """处理 time speed <n>：设置时间流速（0=暂停）。

        Args:
            args: 参数列表，args[0] 为流速数值。

        Returns:
            执行结果。
        """
        if not args:
            return CommandResult(
                success=False, output=self._i18n.t("console.time_usage"),
            )
        try:
            speed = float(args[0])
        except ValueError:
            speed = -1.0
        if speed < 0:
            return CommandResult(
                success=False,
                output=self._i18n.t("console.speed_invalid", value=args[0]),
            )
        if speed == 0:
            return CommandResult(success=True, output=self._cmd_pause())
        if self._clock.paused:
            self._clock.resume()
        self._clock.speed = speed
        return CommandResult(
            success=True,
            output=self._i18n.t("console.speed_set", speed=f"{speed:g}"),
        )

    def _cmd_time_status(self) -> str:
        """生成时间状态文本。

        Returns:
            包含日、时间、速度、状态的字符串。
        """
        day = self._calendar.day
        state = self._i18n.t(
            "console.state_paused" if self._clock.paused else "console.state_running"
        )
        return self._i18n.t(
            "console.time_status",
            day=day,
            time=self._fmt_time_of_day(),
            mode=self._speed_label(),
            state=state,
        )

    def _cmd_pause(self) -> str:
        """暂停游戏时间。

        Returns:
            暂停确认文本。
        """
        if self._clock.paused:
            return self._i18n.t("console.already_paused")
        self._clock.pause()
        return self._i18n.t("console.paused")

    def _cmd_resume(self) -> str:
        """恢复游戏时间。

        Returns:
            恢复确认文本。
        """
        if not self._clock.paused:
            return self._i18n.t("console.already_running")
        self._clock.resume()
        return self._i18n.t("console.resumed")

    def _cmd_tick(self, count: int = 1) -> str:
        """手动推进 N tick（忽略暂停和速度，调试用）。

        Args:
            count: 要推进的 tick 数。

        Returns:
            推进确认文本。
        """
        for _ in range(count):
            self._clock.step()
        return self._i18n.t("console.ticked", count=count, time=f"{self._clock.time:,}")

    def _cmd_jump(self, days: int = 1) -> str:
        """跳过 N 天，落地到目标日 06:00。

        Args:
            days: 要跳过的天数。

        Returns:
            跳转后状态文本。
        """
        target_day = self._calendar.day + days
        target = (target_day - 1) * GAME_DAY + 6 * GAME_HOUR
        skipped = target - self._clock.time
        self._clock.skip(skipped)
        return self._i18n.t("console.jumped", days=days, day=self._calendar.day)

    # ── weather 指令组 ──────────────────────────────────

    def _h_weather(self, args: list[str]) -> CommandResult:
        """处理 weather 指令组：status 查询 / set 强制控制。

        Args:
            args: 参数列表。

        Returns:
            执行结果。
        """
        if self._weather is None:
            return CommandResult(
                success=False,
                output=self._i18n.t("console.weather_unavailable"),
            )
        if not args or args[0].lower() == "status":
            return self._h_weather_status(args[1:] if args else [])
        if args[0].lower() == "set":
            return self._h_weather_set(args[1:])
        return CommandResult(
            success=False, output=self._i18n.t("console.weather_usage"),
        )

    def _h_weather_status(self, args: list[str]) -> CommandResult:
        """处理 weather status [cx cy]：查询指定位置当前天气。

        Args:
            args: 坐标参数（空 或 [cx, cy]）。

        Returns:
            执行结果。
        """
        coord = self._parse_chunk(args)
        if coord is None:
            return CommandResult(
                success=False, output=self._i18n.t("console.weather_usage"),
            )
        cx, cy = coord
        report = self._weather.get_weather_report(cx, cy)
        if report is None:
            return CommandResult(
                success=False,
                output=self._i18n.t(
                    "console.weather_chunk_unregistered", cx=cx, cy=cy,
                ),
            )
        wp, sunrise_h, sunset_h, _, intensity = report

        from ascend.weather.weather_engine import (
            classify_temperature, classify_humidity, classify_wind,
            classify_sunshine, classify_sunlight_intensity,
        )
        t = self._i18n.t
        temp = round(wp.temperature, 1)
        hum = round(wp.humidity, 1)
        wind = round(wp.wind_speed, 1)
        sun = round(wp.sunshine, 1)
        light = round(intensity, 2)
        if wp.rainfall > 0:
            precip_key = "weather.snow" if temp <= 0 else "weather.rain"
            precip = t("weather.intensity", type=t(precip_key),
                       intensity=f"{wp.rainfall:.1f}")
        else:
            precip = t("weather.clear")
        lines = [
            t("console.weather_header", cx=cx, cy=cy),
            f"  {t('console.weather_temp')}: {temp}°C"
            f" ({t('perception.temp.' + classify_temperature(temp))})",
            f"  {t('console.weather_hum')}: {hum}%"
            f" ({t('perception.hum.' + classify_humidity(hum))})",
            f"  {t('console.weather_wind')}: {wind} m/s"
            f" ({t('perception.wind.' + classify_wind(wind))})",
            f"  {t('console.weather_sun')}: {sun}h"
            f" ({t('perception.sun.' + classify_sunshine(sun))})"
            f"  |  {t('console.weather_light')}: {light}"
            f" ({t('perception.light.' + classify_sunlight_intensity(light))})",
            f"  {t('console.weather_precip')}: {precip}",
            f"  {t('console.weather_sun_times', sunrise=self._fmt_hour(sunrise_h), sunset=self._fmt_hour(sunset_h))}",
        ]
        return CommandResult(success=True, output="\n".join(lines))

    def _h_weather_set(self, args: list[str]) -> CommandResult:
        """处理 weather set <rain|modifier> <on|off> [cx cy]。

        Args:
            args: [target, state, cx?, cy?]。

        Returns:
            执行结果。
        """
        from ascend.weather.weather_modifier import WEATHER_MODIFIERS

        if len(args) < 2:
            return CommandResult(
                success=False, output=self._i18n.t("console.weather_usage"),
            )
        target = args[0].lower()
        state = args[1].lower()
        valid_targets = ["rain"] + list(WEATHER_MODIFIERS.keys())
        if target not in valid_targets:
            return CommandResult(
                success=False,
                output=self._i18n.t(
                    "console.weather_target_unknown",
                    name=target, targets=", ".join(valid_targets),
                ),
            )
        if state not in self._ON_OFF:
            return CommandResult(
                success=False,
                output=self._i18n.t("console.weather_state_invalid"),
            )
        coord = self._parse_chunk(args[2:])
        if coord is None:
            return CommandResult(
                success=False, output=self._i18n.t("console.weather_usage"),
            )
        cx, cy = coord
        active = state == "on"

        if target == "rain":
            changed = self._weather.set_rain(cx, cy, active)
        else:
            changed = self._weather.set_modifier(cx, cy, target, active)

        if changed is None:
            return CommandResult(
                success=False,
                output=self._i18n.t(
                    "console.weather_chunk_unregistered", cx=cx, cy=cy,
                ),
            )
        if not changed:
            return CommandResult(
                success=True,
                output=self._i18n.t(
                    "console.weather_set_noop", target=target, cx=cx, cy=cy,
                ),
            )
        key = "console.weather_set_on" if active else "console.weather_set_off"
        return CommandResult(
            success=True,
            output=self._i18n.t(key, target=target, cx=cx, cy=cy),
        )

    # ── tp 指令 ─────────────────────────────────────────

    def _h_tp(self, args: list[str]) -> CommandResult:
        """处理 tp [x y]：传送玩家（权威实体在后端）。

        无参数回出生点。传送通过 player_teleported 事件推送前端吸附。

        Args:
            args: 参数列表（空 或 [x, y]）。

        Returns:
            执行结果。
        """
        if self._player is None:
            return CommandResult(
                success=False,
                output=self._i18n.t("console.player_unavailable"),
            )
        if not args:
            x, y = self._player.teleport_home()
            return CommandResult(
                success=True,
                output=self._i18n.t("console.tp_home", x=f"{x:.0f}", y=f"{y:.0f}"),
            )
        if len(args) != 2:
            return CommandResult(
                success=False, output=self._i18n.t("console.tp_usage"),
            )
        try:
            x = float(args[0])
            y = float(args[1])
        except ValueError:
            return CommandResult(
                success=False, output=self._i18n.t("console.tp_usage"),
            )
        x, y = self._player.teleport(x, y)
        return CommandResult(
            success=True,
            output=self._i18n.t("console.tp_done", x=f"{x:.0f}", y=f"{y:.0f}"),
        )

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

    # ── 内部实现 ────────────────────────────────────────

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
        tod = self._calendar.time_of_day(self._clock.time)
        hour = int(tod / GAME_HOUR)
        minute = int((tod % GAME_HOUR) / GAME_MINUTE)
        second = int((tod % GAME_MINUTE) * 60 / GAME_MINUTE)
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

    def _cmd_status(self) -> str:
        """生成运行状态报告（合并原 st + report）。

        Returns:
            首行时间概览 + 世界树统计的多行文本。
        """
        state = self._i18n.t(
            "console.state_paused" if self._clock.paused else "console.state_running"
        )
        stats = world_tree.stats
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
            f"  {self._i18n.t('console.report_events')}:    {world_tree.event_count:,}",
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
        total = world_tree.event_count
        if total == 0:
            return self._i18n.t("console.no_events")

        count = min(count, total)
        now = self._clock.time
        start = max(0, now - GAME_YEAR)
        events = world_tree.get_events_in_range(start, now)
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

    def _cmd_map(self, args: list[str]) -> str:
        """显示世界地图（ASCII 渲染）。

        Args:
            args: 命令参数列表。

        Returns:
            ASCII 地图文本。
        """
        if self._world_gen is None:
            return self._i18n.t("console.no_world_gen")

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
                return self._i18n.t("console.invalid_seed", seed=args[arg_idx])

        from .render import render_map, render_region_detail

        output = self._i18n.t("console.map_header",
            seed=seed, radius=radius, step=step) + "\n"
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
            f"  weather set <mod> <on|off> [cx cy]       {t('console.help_weather_modifier')}",
            f"  tp [x y]                                 {t('console.help_tp')}",
            f"  lang [code]                              {t('console.help_lang')}",
            f"  events [n]                               {t('console.help_events')}",
            f"  map [mode] [radius] [seed]               {t('console.help_map')}",
            f"  ?, help                                  {t('console.help_help')}",
            f"  q, quit, exit                            {t('console.help_quit')}",
        ]
        return "\n".join(lines)
