"""time 指令组 — 时间控制（speed/pause/resume/jump/tick）。

Mixin，依赖宿主 CommandExecutor 提供的:
  self._clock / self._calendar / self._i18n / self._parse_int
  self._fmt_time_of_day / self._speed_label
"""

import math
from collections.abc import Callable

from ascend.config import SUNRISE_HOUR
from ascend.time import GAME_DAY, GAME_HOUR

from .result import CommandResult


class TimeCommandsMixin:
    """time 指令组实现。"""

    # time tick 单次执行上限（step 同步触发日历边界回调，过大冻结游戏线程）
    MAX_TICK_STEPS: int = 10_000

    # time 指令组子命令注册表（sub → 处理器(executor, rest)）
    # 处理器签名与顶层 handler 不同（多收 executor 以访问 i18n），
    # 组内复用由 lambda 闭包捕获 self。
    _TIME_SUBS: dict[str, Callable[["TimeCommandsMixin", list[str]], CommandResult]] = {
        "speed": lambda e, rest: e._h_time_speed(rest),
        "pause": lambda e, _r: CommandResult(success=True, output=e._cmd_pause()),
        "resume": lambda e, _r: CommandResult(success=True, output=e._cmd_resume()),
        "jump": lambda e, rest: e._h_time_jump(rest),
        "tick": lambda e, rest: e._h_time_tick(rest),
    }

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
        handler = self._TIME_SUBS.get(sub)
        if handler is None:
            return CommandResult(
                success=False, output=self._i18n.t("console.time_usage"),
            )
        return handler(self, args[1:])

    def _h_time_jump(self, rest: list[str]) -> CommandResult:
        """time jump <days>：跳 N 天（校验与单次上限）。"""
        days = self._parse_int(rest, 0, 1)
        if days is None or days < 1:
            return CommandResult(
                success=False,
                output=self._i18n.t("console.invalid_number",
                                    value=rest[0] if rest else ""),
            )
        return CommandResult(success=True, output=self._cmd_jump(days))

    def _h_time_tick(self, rest: list[str]) -> CommandResult:
        """time tick <count>：手动推进 N tick（校验与单次上限）。

        step() 同步触发日历边界回调，超大 count 会冻结游戏线程数秒。
        """
        count = self._parse_int(rest, 0, 1)
        if count is None or count < 1:
            return CommandResult(
                success=False,
                output=self._i18n.t("console.invalid_number",
                                    value=rest[0] if rest else ""),
            )
        if count > self.MAX_TICK_STEPS:
            return CommandResult(
                success=False,
                output=self._i18n.t("console.tick_limit",
                                    limit=self.MAX_TICK_STEPS),
            )
        return CommandResult(success=True, output=self._cmd_tick(count))

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
        if not math.isfinite(speed) or speed < 0:
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

        每次 step 都会触发日历边界回调，超大 count 会冻结游戏线程，
        因此限制单次执行上限（见 _h_time_tick）。

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
        target = (target_day - 1) * GAME_DAY + SUNRISE_HOUR * GAME_HOUR
        skipped = target - self._clock.time
        self._clock.skip(skipped)
        return self._i18n.t("console.jumped", days=days, day=self._calendar.day)
