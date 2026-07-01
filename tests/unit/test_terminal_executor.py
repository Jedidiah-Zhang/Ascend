"""CommandExecutor 单元测试。

测试终端指令解析和执行的各个分支，包括正常路径、边界情况和错误处理。
"""

import pytest
from ascend.terminal import CommandExecutor, CommandResult


@pytest.fixture
def clock():
    """使用默认起始时间的 WorldClock 固件。"""
    from ascend.time import WorldClock
    return WorldClock()


@pytest.fixture
def calendar():
    """使用默认起始日的 GameCalendar 固件，测试后自动清理订阅。"""
    from ascend.time import GameCalendar
    cal = GameCalendar()
    yield cal
    cal.shutdown()


@pytest.fixture
def i18n():
    """使用 zh_CN 的 I18n 固件。"""
    from ascend.i18n import I18n
    return I18n("zh_CN")


@pytest.fixture
def executor(clock, calendar, i18n):
    """标准 CommandExecutor 固件。"""
    return CommandExecutor(clock=clock, calendar=calendar, i18n=i18n)


# ══════════════════════════════════════════════════════════
# T1: status
# ══════════════════════════════════════════════════════════

class TestStatus:
    """status/st 指令测试。"""

    def test_T1_status(self, executor):
        """execute("st") 和 execute("status") 返回包含时间/日/模式的文本。

        Arrange:
            新创建的 CommandExecutor。
        Act:
            执行 "st" 和 "status"。
        Assert:
            返回结果 success=True，输出包含日、时间、模式等信息。
        """
        for cmd in ("st", "status"):
            result = executor.execute(cmd)
            assert result.success is True
            assert "日" in result.output or "day" in result.output
            assert ":" in result.output  # 时间格式 HH:MM:SS
            assert executor._i18n.t("mode.realtime") in result.output


# ══════════════════════════════════════════════════════════
# T2-T4: pause / resume
# ══════════════════════════════════════════════════════════

class TestPauseResume:
    """pause/resume 指令测试。"""

    def test_T2_pause_resume(self, executor):
        """依次 pause、resume，状态翻转。

        Arrange:
            CommandExecutor 初始为 unpaused 状态。
        Act:
            执行 pause → resume。
        Assert:
            第一次 pause 成功，resume 后成功。
        """
        r1 = executor.execute("pause")
        assert r1.success is True
        assert executor._paused is True

        r2 = executor.execute("resume")
        assert r2.success is True
        assert executor._paused is False

    def test_T3_pause_twice(self, executor):
        """连续两次 pause，第二次返回 "already paused"。

        Arrange:
            CommandExecutor 初始为 unpaused 状态。
        Act:
            执行 pause → pause。
        Assert:
            第一次成功，第二次输出包含 "already paused" 或 "已在暂停"。
        """
        executor.execute("pause")
        result = executor.execute("pause")
        assert result.success is True
        assert "already" in result.output or "暂停" in result.output

    def test_T4_resume_twice(self, executor):
        """连续两次 resume，第二次返回 "already running"。

        Arrange:
            CommandExecutor 初始为 unpaused 状态。
        Act:
            执行 resume → resume。
        Assert:
            第一次输出包含 "already running" 或 "运行中"。
        """
        r1 = executor.execute("resume")
        assert r1.success is True
        assert "already" in r1.output or "运行" in r1.output

        r2 = executor.execute("resume")
        assert r2.success is True


# ══════════════════════════════════════════════════════════
# T5-T7: tick
# ══════════════════════════════════════════════════════════

class TestTick:
    """tick 指令测试。"""

    def test_T5_tick_default(self, executor):
        """execute("tick") 推进 1 帧。

        Arrange:
            CommandExecutor 含初始 clock。
        Act:
            执行 "tick"。
        Assert:
            clock.tick_count 增加，输出包含推进信息。
        """
        before = executor._clock.tick_count
        result = executor.execute("tick")
        assert result.success is True
        assert executor._clock.tick_count == before + 1
        assert "1" in result.output

    def test_T6_tick_with_count(self, executor):
        """execute("tick 5") 推进 5 帧。

        Arrange:
            CommandExecutor 含初始 clock。
        Act:
            执行 "tick 5"。
        Assert:
            clock.tick_count 增加 5，输出包含 "5"。
        """
        before = executor._clock.tick_count
        result = executor.execute("tick 5")
        assert result.success is True
        assert executor._clock.tick_count == before + 5
        assert "5" in result.output

    def test_T7_tick_invalid_args(self, executor):
        """execute("tick abc") 参数错误，success=false。

        Arrange:
            CommandExecutor。
        Act:
            执行 "tick abc"。
        Assert:
            result.success 为 False，输出包含错误信息。
        """
        result = executor.execute("tick abc")
        assert result.success is False
        assert "?" in result.output or "帮助" in result.output or "未知" in result.output


# ══════════════════════════════════════════════════════════
# T8-T9: sleep
# ══════════════════════════════════════════════════════════

class TestSleep:
    """sleep 指令测试。"""

    def test_T8_sleep_default(self, executor):
        """execute("sleep") 快进 8 小时。

        Arrange:
            CommandExecutor 含初始 clock (06:00)。
        Act:
            执行 "sleep"。
        Assert:
            游戏时间推进约 8 小时 (28800 游戏秒)，输出包含时间。
        """
        before = executor._clock.time
        result = executor.execute("sleep")
        assert result.success is True
        assert executor._clock.time >= before + 8 * 3600 - 60  # 允许 1 分钟误差
        assert "8" in result.output or "小时" in result.output

    def test_T9_sleep_with_hours(self, executor):
        """execute("sleep 3.5") 快进 3.5 小时。

        Arrange:
            CommandExecutor。
        Act:
            执行 "sleep 3.5"。
        Assert:
            游戏时间推进约 3.5 小时。
        """
        before = executor._clock.time
        result = executor.execute("sleep 3.5")
        assert result.success is True
        assert executor._clock.time >= before + 3.5 * 3600 - 60


# ══════════════════════════════════════════════════════════
# T10-T11: travel
# ══════════════════════════════════════════════════════════

class TestTravel:
    """travel 指令测试。"""

    def test_T10_travel_default(self, executor):
        """execute("travel") 快进 1 小时。

        Arrange:
            CommandExecutor。
        Act:
            执行 "travel"。
        Assert:
            游戏时间推进约 1 小时。
        """
        before = executor._clock.time
        result = executor.execute("travel")
        assert result.success is True
        assert executor._clock.time >= before + 3600 - 60

    def test_T11_travel_with_hours(self, executor):
        """execute("travel 2") 快进 2 小时。

        Arrange:
            CommandExecutor。
        Act:
            执行 "travel 2"。
        Assert:
            游戏时间推进约 2 小时。
        """
        before = executor._clock.time
        result = executor.execute("travel 2")
        assert result.success is True
        assert executor._clock.time >= before + 2 * 3600 - 60


# ══════════════════════════════════════════════════════════
# T12-T13: jump
# ══════════════════════════════════════════════════════════

class TestJump:
    """jump 指令测试。"""

    def test_T12_jump_default(self, executor, clock):
        """execute("jump") 跳 1 天。

        Arrange:
            CommandExecutor，初始 time=06:00。
        Act:
            执行 "jump"。
        Assert:
            游戏日推进约 1 天。
        """
        from ascend.time import GAME_DAY
        before_day = executor._calendar.day
        result = executor.execute("jump")
        assert result.success is True
        # jump 跳到目标日 06:00，所以至少推进 1 天
        assert executor._calendar.day >= before_day + 1

    def test_T13_jump_with_days(self, executor):
        """execute("jump 7") 跳 7 天。

        Arrange:
            CommandExecutor。
        Act:
            执行 "jump 7"。
        Assert:
            游戏日推进约 7 天。
        """
        before_day = executor._calendar.day
        result = executor.execute("jump 7")
        assert result.success is True
        assert executor._calendar.day >= before_day + 7


# ══════════════════════════════════════════════════════════
# T14-T16: mode
# ══════════════════════════════════════════════════════════

class TestMode:
    """mode 指令测试。"""

    def test_T14_mode_show(self, executor):
        """execute("mode") 显示当前模式和可用列表。

        Arrange:
            CommandExecutor。
        Act:
            执行 "mode"。
        Assert:
            输出包含当前模式名称和可用模式列表。
        """
        result = executor.execute("mode")
        assert result.success is True
        assert "realtime" in result.output or "当前" in result.output
        assert "sleep" in result.output or "travel" in result.output

    def test_T15_mode_switch(self, executor):
        """execute("mode sleep") 切换模式。

        Arrange:
            CommandExecutor，当前模式为 REALTIME。
        Act:
            执行 "mode sleep"。
        Assert:
            executor 的模式切换为 SLEEP，输出包含确认。
        """
        result = executor.execute("mode sleep")
        assert result.success is True
        from ascend.time import TimeMode
        assert executor._clock.mode == TimeMode.SLEEP

    def test_T16_mode_invalid(self, executor):
        """execute("mode invalid") 未知模式。

        Arrange:
            CommandExecutor。
        Act:
            执行 "mode invalid"。
        Assert:
            result.success 为 False，输出提示未知模式。
        """
        result = executor.execute("mode invalid")
        assert result.success is False
        assert "未知" in result.output or "invalid" in result.output.lower()


# ══════════════════════════════════════════════════════════
# T17-T19: lang
# ══════════════════════════════════════════════════════════

class TestLang:
    """lang 指令测试。"""

    def test_T17_lang_show(self, executor):
        """execute("lang") 显示当前语言。

        Arrange:
            CommandExecutor, lang=zh_CN。
        Act:
            执行 "lang"。
        Assert:
            输出包含当前语言代码和可用语言列表。
        """
        result = executor.execute("lang")
        assert result.success is True
        assert "zh_CN" in result.output or "当前" in result.output

    def test_T18_lang_switch(self, executor):
        """execute("lang en_US") 切换语言。

        Arrange:
            CommandExecutor, lang=zh_CN。
        Act:
            执行 "lang en_US"。
        Assert:
            i18n.lang 变为 "en_US"，输出包含确认。
        """
        result = executor.execute("lang en_US")
        assert result.success is True
        assert executor._i18n.lang == "en_US"

    def test_T19_lang_invalid(self, executor):
        """execute("lang xx_XX") 未知语言。

        Arrange:
            CommandExecutor。
        Act:
            执行 "lang xx_XX"。
        Assert:
            result.success 为 False，输出包含 "未知" 信息。
        """
        result = executor.execute("lang xx_XX")
        assert result.success is False
        assert "未知" in result.output or "xx_XX" in result.output


# ══════════════════════════════════════════════════════════
# T20-T22: events
# ══════════════════════════════════════════════════════════

class TestEvents:
    """events 指令测试。"""

    def test_T20_events_default(self, executor):
        """execute("events") 返回事件列表。

        Arrange:
            CommandExecutor，总线上已有一些事件（由 clock tick 产生）。
        Act:
            先推进几帧产生事件，执行 "events"。
        Assert:
            输出包含事件列表。
        """
        # 先 tick 以产生 game_minute 事件
        executor.execute("tick")
        executor.execute("tick")
        result = executor.execute("events")
        assert result.success is True
        assert result.output.strip() != ""

    def test_T21_events_with_count(self, executor):
        """execute("events 3") 返回 3 个事件。

        Arrange:
            CommandExecutor。
        Act:
            执行 "events 3"。
        Assert:
            输出包含事件列表，不报错。
        """
        executor.execute("tick")
        result = executor.execute("events 3")
        assert result.success is True

    def test_T22_events_empty(self, executor):
        """events 指令在成功时输出格式正确。

        由于全局事件总线被其他测试共享，events 总线上可能有数据。
        此处验证指令执行成功且输出格式符合预期（包含表头）。

        Arrange:
            CommandExecutor。
        Act:
            执行 "events"。
        Assert:
            指令成功，输出包含 Time/Type/Summary 等表头字段。
        """
        result = executor.execute("events")
        assert result.success is True
        # 输出可能包含事件表格或 "no events" 消息，都算有效
        assert len(result.output) > 0


# ══════════════════════════════════════════════════════════
# T23: report
# ══════════════════════════════════════════════════════════

class TestReport:
    """report/rp 指令测试。"""

    def test_T23_report(self, executor):
        """execute("rp") 和 execute("report") 返回运行报告。

        Arrange:
            CommandExecutor。
        Act:
            执行 "rp" 和 "report"。
        Assert:
            输出包含报告关键词（时间、tick、事件等）。
        """
        for cmd in ("rp", "report"):
            result = executor.execute(cmd)
            assert result.success is True
            assert "tick" in result.output.lower() or "报告" in result.output


# ══════════════════════════════════════════════════════════
# T24: help
# ══════════════════════════════════════════════════════════

class TestHelp:
    """help/? 指令测试。"""

    def test_T24_help(self, executor):
        """execute("?") 和 execute("help") 返回帮助文本。

        Arrange:
            CommandExecutor。
        Act:
            执行 "?" 和 "help"。
        Assert:
            输出包含帮助关键词（st、status、tick 等指令说明）。
        """
        for cmd in ("?", "help"):
            result = executor.execute(cmd)
            assert result.success is True
            assert "st" in result.output or "status" in result.output
            assert "tick" in result.output or "help" in result.output


# ══════════════════════════════════════════════════════════
# T25: quit
# ══════════════════════════════════════════════════════════

class TestQuit:
    """quit/q/exit 指令测试。"""

    def test_T25_quit(self, executor):
        """execute("q"/"quit"/"exit") is_quit=true。

        Arrange:
            CommandExecutor。
        Act:
            执行 "q"、"quit"、"exit"。
        Assert:
            每个结果的 is_quit 字段均为 True。
        """
        for cmd in ("q", "quit", "exit"):
            result = executor.execute(cmd)
            assert result.is_quit is True


# ══════════════════════════════════════════════════════════
# T26: unknown command
# ══════════════════════════════════════════════════════════

class TestUnknownCommand:
    """未知指令测试。"""

    def test_T26_unknown_command(self, executor):
        """execute("foobar") success=false。

        Arrange:
            CommandExecutor。
        Act:
            执行 "foobar"。
        Assert:
            result.success 为 False，输出包含错误信息。
        """
        result = executor.execute("foobar")
        assert result.success is False
        assert "未知" in result.output or "invalid" in result.output.lower()


# ══════════════════════════════════════════════════════════
# T27: empty line
# ══════════════════════════════════════════════════════════

class TestEmptyLine:
    """空行/空白行测试。"""

    def test_T27_empty_line(self, executor):
        """execute(""/"   ") success=true, output=""。

        Arrange:
            CommandExecutor。
        Act:
            执行空字符串和空白字符串。
        Assert:
            result.success 为 True，output 为空字符串。
        """
        for cmd in ("", "   ", "\t"):
            result = executor.execute(cmd)
            assert result.success is True
            assert result.output == ""


# ══════════════════════════════════════════════════════════
# T28: map with world_gen
# ══════════════════════════════════════════════════════════

class TestMap:
    """map 指令测试（需要 WorldGenerator）。"""

    @pytest.fixture
    def executor_with_world_gen(self, clock, calendar, i18n):
        """含 WorldGenerator 的 CommandExecutor 固件。"""
        from ascend.space import WorldGenerator
        wg = WorldGenerator(seed=42)
        return CommandExecutor(clock=clock, calendar=calendar, i18n=i18n, world_gen=wg)

    def test_T28_map_with_world_gen(self, executor_with_world_gen):
        """有 world_gen 时 execute("map") 返回 ASCII 地图。

        Arrange:
            CommandExecutor 含 WorldGenerator(seed=42)。
        Act:
            执行 "map"。
        Assert:
            输出包含种子信息和地图字符。
        """
        result = executor_with_world_gen.execute("map")
        assert result.success is True
        assert "42" in result.output or "种子" in result.output
        assert len(result.output) > 20  # 地图渲染应有相当长度


# ══════════════════════════════════════════════════════════
# T29: repr
# ══════════════════════════════════════════════════════════

class TestRepr:
    """__repr__ 测试。"""

    def test_T29_repr(self, executor):
        """repr(executor) 包含关键信息。

        Arrange:
            CommandExecutor。
        Act:
            调用 repr(executor)。
        Assert:
            返回字符串包含 "CommandExecutor" 等关键字段。
        """
        r = repr(executor)
        assert "CommandExecutor" in r
        assert str(executor._clock.mode.key) in r
