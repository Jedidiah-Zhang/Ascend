"""CommandExecutor 单元测试。

测试终端指令解析和执行的各个分支，包括正常路径、边界情况和错误处理。
指令结构对应 Issue #2：status / time 组 / weather 组 / 独立指令。
"""

import pytest
from ascend.terminal import CommandExecutor, CommandResult
from ascend.time import GAME_HOUR


@pytest.fixture
def clock():
    """使用默认起始时间的 WorldClock 固件。"""
    from ascend.time import WorldClock
    return WorldClock()


@pytest.fixture
def calendar(clock):
    """使用默认起始日的 GameCalendar 固件，测试后自动清理订阅。"""
    from ascend.time import GameCalendar
    cal = GameCalendar(clock=clock)
    yield cal
    cal.shutdown()


@pytest.fixture
def i18n():
    """使用 zh_CN 的 I18n 固件。"""
    from ascend.i18n import I18n
    return I18n("zh_CN")


@pytest.fixture
def executor(clock, calendar, i18n):
    """标准 CommandExecutor 固件（无天气引擎）。"""
    return CommandExecutor(clock=clock, calendar=calendar, i18n=i18n)


@pytest.fixture
def weather_engine(clock):
    """含 chunk (0,0) 温带森林 + (1,1) 极地苔原的 WeatherEngine 固件。

    使用独立 WorldTree 隔离事件总线。
    """
    from ascend.weather import WeatherEngine
    from ascend.world_tree import WorldTree
    from ascend.space import WeatherParams, ClimateZone
    wt = WorldTree()
    engine = WeatherEngine(clock, seed=42, world_tree_arg=wt)
    baseline = WeatherParams(20.0, 800.0, 12.0, 100.0, 60.0, 5.0)
    engine.register_chunk(0, 0, baseline, ClimateZone.TEMPERATE_FOREST, 15.0)
    cold_baseline = WeatherParams(-10.0, 200.0, 8.0, 100.0, 50.0, 6.0)
    engine.register_chunk(1, 1, cold_baseline, ClimateZone.POLAR_TUNDRA, -5.0)
    yield engine
    engine.shutdown()


@pytest.fixture
def executor_weather(clock, calendar, i18n, weather_engine):
    """含 WeatherEngine + 默认 chunk (0,0) 的 CommandExecutor 固件。"""
    return CommandExecutor(
        clock=clock, calendar=calendar, i18n=i18n,
        weather_engine=weather_engine, default_chunk=(0, 0),
    )


# ══════════════════════════════════════════════════════════
# T1: status（合并原 st + report）
# ══════════════════════════════════════════════════════════

class TestStatus:
    """status 指令测试。"""

    def test_T1_status_merged(self, executor):
        """execute("status") 返回时间概览 + 世界树统计的合并报告。

        Arrange:
            新创建的 CommandExecutor。
        Act:
            执行 "status"。
        Assert:
            success=True，输出含日/时间/模式概览行和 publish/tick 统计行。
        """
        result = executor.execute("status")
        assert result.success is True
        assert "天" in result.output or "Day" in result.output
        assert ":" in result.output  # 时间格式 HH:MM:SS
        assert "publish" in result.output
        assert "tick" in result.output.lower()

    def test_T2_legacy_aliases_removed(self, executor):
        """原 st/rp/report 顶层别名已删除，返回未知指令。

        Arrange:
            CommandExecutor。
        Act:
            执行 "st"、"rp"、"report"。
        Assert:
            均 success=False。
        """
        for cmd in ("st", "rp", "report"):
            result = executor.execute(cmd)
            assert result.success is False, cmd


# ══════════════════════════════════════════════════════════
# T3-T10: time 指令组
# ══════════════════════════════════════════════════════════

class TestTimeStatus:
    """time（无参）指令测试。"""

    def test_T3_time_shows_status(self, executor):
        """execute("time") 显示当前时间状态。

        Arrange:
            CommandExecutor。
        Act:
            执行 "time"。
        Assert:
            输出包含日、时间和速度信息。
        """
        result = executor.execute("time")
        assert result.success is True
        assert "天" in result.output or "Day" in result.output
        assert ":" in result.output
        assert executor._i18n.t("mode.realtime") in result.output

    def test_T4_time_unknown_sub(self, executor):
        """execute("time bogus") 返回用法提示。

        Arrange:
            CommandExecutor。
        Act:
            执行 "time bogus"。
        Assert:
            success=False，输出包含用法。
        """
        result = executor.execute("time bogus")
        assert result.success is False
        assert "time" in result.output


class TestTimePauseResume:
    """time pause / time resume 指令测试。"""

    def test_T5_pause_resume(self, executor):
        """依次 time pause、time resume，状态翻转。

        Arrange:
            CommandExecutor 初始为 unpaused 状态。
        Act:
            执行 time pause → time resume。
        Assert:
            时钟状态相应切换。
        """
        r1 = executor.execute("time pause")
        assert r1.success is True
        assert executor._clock.paused is True

        r2 = executor.execute("time resume")
        assert r2.success is True
        assert executor._clock.paused is False

    def test_T6_pause_twice(self, executor):
        """连续两次 time pause，第二次返回已暂停提示。

        Arrange:
            CommandExecutor 初始为 unpaused 状态。
        Act:
            执行 time pause → time pause。
        Assert:
            第二次输出包含"已在暂停"。
        """
        executor.execute("time pause")
        result = executor.execute("time pause")
        assert result.success is True
        assert "already" in result.output or "暂停" in result.output

    def test_T7_resume_twice(self, executor):
        """未暂停时 time resume 返回运行中提示。

        Arrange:
            CommandExecutor 初始为 unpaused 状态。
        Act:
            执行 time resume。
        Assert:
            输出包含"运行中"。
        """
        r = executor.execute("time resume")
        assert r.success is True
        assert "already" in r.output or "运行" in r.output


class TestTimeSpeed:
    """time speed 指令测试。"""

    def test_T8_speed_set(self, executor):
        """execute("time speed 2") 设置时间流速。

        Arrange:
            CommandExecutor，初始 speed=1.0。
        Act:
            执行 "time speed 2"。
        Assert:
            clock.speed 变为 2.0，输出包含确认。
        """
        result = executor.execute("time speed 2")
        assert result.success is True
        assert executor._clock.speed == 2.0
        assert "2" in result.output

    def test_T9_speed_zero_pauses(self, executor):
        """execute("time speed 0") 等价于暂停。

        Arrange:
            CommandExecutor。
        Act:
            执行 "time speed 0"。
        Assert:
            时钟进入暂停状态。
        """
        result = executor.execute("time speed 0")
        assert result.success is True
        assert executor._clock.paused is True

    def test_T10_speed_resumes_paused_clock(self, executor):
        """暂停后 time speed 2 自动恢复运行。

        Arrange:
            CommandExecutor 已 pause。
        Act:
            执行 "time speed 2"。
        Assert:
            时钟恢复且速度为 2.0。
        """
        executor.execute("time pause")
        result = executor.execute("time speed 2")
        assert result.success is True
        assert executor._clock.paused is False
        assert executor._clock.speed == 2.0

    def test_T11_speed_invalid(self, executor):
        """非法流速参数返回错误。

        Arrange:
            CommandExecutor。
        Act:
            执行 "time speed abc"、"time speed -1"、"time speed"。
        Assert:
            均 success=False。
        """
        for cmd in ("time speed abc", "time speed -1", "time speed"):
            result = executor.execute(cmd)
            assert result.success is False, cmd


class TestTimeTick:
    """time tick 指令测试。"""

    def test_T12_tick_default(self, executor):
        """execute("time tick") 推进 1 tick。

        Arrange:
            CommandExecutor 含初始 clock。
        Act:
            执行 "time tick"。
        Assert:
            clock.tick_count 增加 1。
        """
        before = executor._clock.tick_count
        result = executor.execute("time tick")
        assert result.success is True
        assert executor._clock.tick_count == before + 1

    def test_T13_tick_with_count(self, executor):
        """execute("time tick 5") 推进 5 tick。

        Arrange:
            CommandExecutor 含初始 clock。
        Act:
            执行 "time tick 5"。
        Assert:
            clock.tick_count 增加 5，输出包含 "5"。
        """
        before = executor._clock.tick_count
        result = executor.execute("time tick 5")
        assert result.success is True
        assert executor._clock.tick_count == before + 5
        assert "5" in result.output

    def test_T14_tick_invalid(self, executor):
        """execute("time tick abc") 参数错误。

        Arrange:
            CommandExecutor。
        Act:
            执行 "time tick abc"。
        Assert:
            success=False，输出含错误提示。
        """
        result = executor.execute("time tick abc")
        assert result.success is False
        assert "abc" in result.output or "无效" in result.output


class TestTimeJump:
    """time jump 指令测试。"""

    def test_T15_jump_default(self, executor):
        """execute("time jump") 跳 1 天。

        Arrange:
            CommandExecutor，初始 time=06:00。
        Act:
            执行 "time jump"。
        Assert:
            游戏日至少推进 1 天。
        """
        before_day = executor._calendar.day
        result = executor.execute("time jump")
        assert result.success is True
        assert executor._calendar.day >= before_day + 1

    def test_T16_jump_with_days(self, executor):
        """execute("time jump 7") 跳 7 天。

        Arrange:
            CommandExecutor。
        Act:
            执行 "time jump 7"。
        Assert:
            游戏日推进至少 7 天。
        """
        before_day = executor._calendar.day
        result = executor.execute("time jump 7")
        assert result.success is True
        assert executor._calendar.day >= before_day + 7

    def test_T17_jump_invalid(self, executor):
        """execute("time jump abc") 与 "time jump 0" 参数错误。

        Arrange:
            CommandExecutor。
        Act:
            执行非法 jump 参数。
        Assert:
            均 success=False。
        """
        for cmd in ("time jump abc", "time jump 0"):
            result = executor.execute(cmd)
            assert result.success is False, cmd


class TestLegacyTimeCommands:
    """过时顶层时间指令已删除的测试。"""

    def test_T18_removed_commands_unknown(self, executor):
        """sleep/travel/mode/pause/resume/tick/jump 顶层指令返回未知。

        Arrange:
            CommandExecutor。
        Act:
            执行各过时指令。
        Assert:
            均 success=False（未知指令）。
        """
        for cmd in ("sleep", "travel 2", "mode fast", "pause",
                    "resume", "tick 5", "jump 3", "pa", "re"):
            result = executor.execute(cmd)
            assert result.success is False, cmd


# ══════════════════════════════════════════════════════════
# T19-T30: weather 指令组
# ══════════════════════════════════════════════════════════

class TestWeatherUnavailable:
    """无天气引擎时的 weather 指令测试。"""

    def test_T19_weather_without_engine(self, executor):
        """无 weather_engine 时 weather 指令返回不可用。

        Arrange:
            无天气引擎的 CommandExecutor。
        Act:
            执行 "weather status"。
        Assert:
            success=False，输出包含不可用提示。
        """
        result = executor.execute("weather status")
        assert result.success is False
        assert "不可用" in result.output or "not available" in result.output


class TestWeatherStatus:
    """weather status 指令测试。"""

    def test_T20_status_default_chunk(self, executor_weather):
        """execute("weather status") 使用默认 chunk 查询天气。

        Arrange:
            executor_weather（default_chunk=(0,0)）。
        Act:
            执行 "weather status"。
        Assert:
            输出包含温度/湿度/风速/日照/降水各字段。
        """
        result = executor_weather.execute("weather status")
        assert result.success is True
        for kw in ("温度", "湿度", "风速", "日照", "降水"):
            assert kw in result.output
        assert "(0,0)" in result.output

    def test_T21_bare_weather_equals_status(self, executor_weather):
        """execute("weather") 等价于 weather status。

        Arrange:
            executor_weather。
        Act:
            执行 "weather"。
        Assert:
            输出与 weather status 结构一致。
        """
        result = executor_weather.execute("weather")
        assert result.success is True
        assert "温度" in result.output

    def test_T22_status_explicit_chunk(self, executor_weather):
        """execute("weather status 1 1") 查询指定 chunk。

        Arrange:
            executor_weather，chunk (1,1) 已注册。
        Act:
            执行 "weather status 1 1"。
        Assert:
            输出头部包含 (1,1)。
        """
        result = executor_weather.execute("weather status 1 1")
        assert result.success is True
        assert "(1,1)" in result.output

    def test_T23_status_unregistered_chunk(self, executor_weather):
        """查询未注册 chunk 返回错误。

        Arrange:
            executor_weather，chunk (9,9) 未注册。
        Act:
            执行 "weather status 9 9"。
        Assert:
            success=False，输出包含坐标。
        """
        result = executor_weather.execute("weather status 9 9")
        assert result.success is False
        assert "(9,9)" in result.output

    def test_T24_status_bad_coords(self, executor_weather):
        """坐标参数个数错误或非整数返回用法。

        Arrange:
            executor_weather。
        Act:
            执行 "weather status 1"、"weather status a b"。
        Assert:
            均 success=False。
        """
        for cmd in ("weather status 1", "weather status a b"):
            result = executor_weather.execute(cmd)
            assert result.success is False, cmd


class TestWeatherSetRain:
    """weather set rain 指令测试。"""

    def test_T25_rain_on_off(self, executor_weather, weather_engine, clock):
        """weather set rain on/off 切换降雨状态。

        Arrange:
            executor_weather，chunk (0,0)。
        Act:
            执行 set rain on → set rain off。
        Assert:
            RainSchedule 的 is_raining 状态随之切换。
        """
        rain = weather_engine._rain_schedules[(0, 0)]

        r1 = executor_weather.execute("weather set rain on")
        assert r1.success is True
        assert rain.is_raining(clock.time) is True
        assert "开启" in r1.output

        r2 = executor_weather.execute("weather set rain off")
        assert r2.success is True
        assert rain.is_raining(clock.time) is False
        assert "关闭" in r2.output

    def test_T26_rain_on_twice_noop(self, executor_weather):
        """已在下雨时 set rain on 返回 no-op 提示。

        Arrange:
            executor_weather 已执行 set rain on。
        Act:
            再次执行 "weather set rain on"。
        Assert:
            success=True，输出包含已处于目标状态。
        """
        executor_weather.execute("weather set rain on")
        result = executor_weather.execute("weather set rain on")
        assert result.success is True
        assert "已处于" in result.output or "already" in result.output

    def test_T27_rain_off_when_dry_noop(self, executor_weather):
        """未下雨时 set rain off 返回 no-op 提示。

        Arrange:
            executor_weather（初始未下雨或先强制关闭）。
        Act:
            执行 "weather set rain off" 两次。
        Assert:
            第二次输出包含已处于目标状态。
        """
        executor_weather.execute("weather set rain off")
        result = executor_weather.execute("weather set rain off")
        assert result.success is True
        assert "已处于" in result.output or "already" in result.output

    def test_T28_rain_query_reflects_force(self, executor_weather, clock):
        """强制降雨后 weather status 显示降雨强度。

        Arrange:
            executor_weather 执行 set rain on。
        Act:
            执行 "weather status"。
        Assert:
            输出包含 mm/h（降雨强度）。
        """
        executor_weather.execute("weather set rain on")
        result = executor_weather.execute("weather status")
        assert result.success is True
        assert "mm/h" in result.output


class TestWeatherSetModifier:
    """weather set <modifier> 指令测试。"""

    def test_T29_modifier_on_off(self, executor_weather, weather_engine, clock):
        """weather set cold_snap on/off 切换修改器状态。

        Arrange:
            executor_weather，chunk (0,0) 温带森林（cold_snap 率 > 0）。
        Act:
            执行 set cold_snap on → off。
        Assert:
            ModifierSchedule 的激活状态随之切换。
        """
        r1 = executor_weather.execute("weather set cold_snap on")
        assert r1.success is True
        sched = weather_engine._modifier_schedules[(0, 0, "cold_snap")]
        assert sched.is_active(clock.time) is True
        assert sched.temp_offset(clock.time) == pytest.approx(-15.0)

        r2 = executor_weather.execute("weather set cold_snap off")
        assert r2.success is True
        assert sched.is_active(clock.time) is False

    def test_T30_modifier_dynamic_schedule(self, executor_weather,
                                           weather_engine, clock):
        """气候带天然无该修改器的 chunk 强制开启时动态创建调度。

        Arrange:
            chunk (1,1) 极地苔原（heat_wave 率为 0，无调度）。
        Act:
            执行 "weather set heat_wave on 1 1"。
        Assert:
            调度被动态创建且激活。
        """
        assert (1, 1, "heat_wave") not in weather_engine._modifier_schedules
        result = executor_weather.execute("weather set heat_wave on 1 1")
        assert result.success is True
        sched = weather_engine._modifier_schedules[(1, 1, "heat_wave")]
        assert sched.is_active(clock.time) is True

    def test_T31_set_invalid_args(self, executor_weather):
        """set 的目标/状态/坐标非法时返回错误。

        Arrange:
            executor_weather。
        Act:
            执行各类非法 set 指令。
        Assert:
            均 success=False。
        """
        for cmd in ("weather set foo on", "weather set rain maybe",
                    "weather set rain", "weather set",
                    "weather set rain on 1", "weather bogus"):
            result = executor_weather.execute(cmd)
            assert result.success is False, cmd

    def test_T32_set_unregistered_chunk(self, executor_weather):
        """对未注册 chunk 执行 set 返回错误。

        Arrange:
            executor_weather，chunk (9,9) 未注册。
        Act:
            执行 "weather set rain on 9 9"。
        Assert:
            success=False，输出包含坐标。
        """
        result = executor_weather.execute("weather set rain on 9 9")
        assert result.success is False
        assert "(9,9)" in result.output


# ══════════════════════════════════════════════════════════
# tp 指令
# ══════════════════════════════════════════════════════════

class TestTeleportCommand:
    """tp 指令测试（需要 PlayerService）。"""

    @pytest.fixture
    def player_service(self, clock):
        """出生 chunk (2, 3)、已 spawn 的 PlayerService 固件（隔离 WorldTree）。"""
        from ascend.entity import EntityManager, PlayerService
        from ascend.world_tree import WorldTree
        wt = WorldTree()
        manager = EntityManager(world_tree_arg=wt)
        svc = PlayerService(manager, clock, birth_chunk=(2, 3), world_tree_arg=wt)
        svc.spawn()
        return svc

    @pytest.fixture
    def executor_player(self, clock, calendar, i18n, player_service):
        """含 PlayerService 的 CommandExecutor 固件。"""
        return CommandExecutor(
            clock=clock, calendar=calendar, i18n=i18n,
            player_service=player_service,
        )

    def test_tp_without_service(self, executor):
        """无 player_service 时 tp 返回不可用。

        Arrange:
            标准 CommandExecutor（无玩家服务）。
        Act:
            执行 "tp 1 2"。
        Assert:
            success=False，输出含不可用提示。
        """
        result = executor.execute("tp 1 2")
        assert result.success is False
        assert "不可用" in result.output or "not available" in result.output

    def test_tp_with_coords(self, executor_player, player_service):
        """execute("tp 100 200") 传送玩家到指定坐标。

        Arrange:
            executor_player。
        Act:
            执行 "tp 100 200"。
        Assert:
            权威位置更新，输出含坐标。
        """
        result = executor_player.execute("tp 100 200")
        assert result.success is True
        assert player_service.position == (100.0, 200.0)
        assert "100" in result.output and "200" in result.output

    def test_tp_no_args_goes_home(self, executor_player, player_service):
        """execute("tp") 传送回出生点。

        Arrange:
            executor_player，玩家已移动到别处。
        Act:
            执行 "tp"。
        Assert:
            权威位置回到出生点，输出含出生点提示。
        """
        player_service.move_to(9999.0, 9999.0)
        result = executor_player.execute("tp")
        assert result.success is True
        assert player_service.position == player_service.birth_position
        assert "出生点" in result.output or "spawn" in result.output

    def test_tp_invalid_args(self, executor_player):
        """参数个数/类型错误返回用法。

        Arrange:
            executor_player。
        Act:
            执行 "tp 1"、"tp a b"。
        Assert:
            均 success=False。
        """
        for cmd in ("tp 1", "tp a b"):
            result = executor_player.execute(cmd)
            assert result.success is False, cmd


# ══════════════════════════════════════════════════════════
# T33-T35: lang
# ══════════════════════════════════════════════════════════class TestLang:
    """lang 指令测试。"""

    def test_T33_lang_show(self, executor):
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

    def test_T34_lang_switch(self, executor):
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

    def test_T35_lang_invalid(self, executor):
        """execute("lang xx_XX") 未知语言。

        Arrange:
            CommandExecutor。
        Act:
            执行 "lang xx_XX"。
        Assert:
            success=False，输出包含 "未知" 信息。
        """
        result = executor.execute("lang xx_XX")
        assert result.success is False
        assert "未知" in result.output or "xx_XX" in result.output


# ══════════════════════════════════════════════════════════
# T36-T38: events
# ══════════════════════════════════════════════════════════

class TestEvents:
    """events 指令测试。"""

    def test_T36_events_default(self, executor):
        """execute("events") 返回事件列表。

        Arrange:
            CommandExecutor，总线上已有一些事件（由 clock tick 产生）。
        Act:
            先推进几帧产生事件，执行 "events"。
        Assert:
            输出非空。
        """
        executor.execute("time tick")
        executor.execute("time tick")
        result = executor.execute("events")
        assert result.success is True
        assert result.output.strip() != ""

    def test_T37_events_with_count(self, executor):
        """execute("events 3") 返回 3 个事件。

        Arrange:
            CommandExecutor。
        Act:
            执行 "events 3"。
        Assert:
            输出包含事件列表，不报错。
        """
        executor.execute("time tick")
        result = executor.execute("events 3")
        assert result.success is True

    def test_T38_events_invalid_count(self, executor):
        """execute("events abc") 参数错误。

        Arrange:
            CommandExecutor。
        Act:
            执行 "events abc"。
        Assert:
            success=False。
        """
        result = executor.execute("events abc")
        assert result.success is False


# ══════════════════════════════════════════════════════════
# T39: help
# ══════════════════════════════════════════════════════════

class TestHelp:
    """help/? 指令测试。"""

    def test_T39_help(self, executor):
        """execute("?") 和 execute("help") 返回帮助文本。

        Arrange:
            CommandExecutor。
        Act:
            执行 "?" 和 "help"。
        Assert:
            输出包含新指令结构的关键词。
        """
        for cmd in ("?", "help"):
            result = executor.execute(cmd)
            assert result.success is True
            for kw in ("status", "time", "weather", "lang", "events"):
                assert kw in result.output
            # 过时指令不再出现
            assert "sleep" not in result.output
            assert "travel" not in result.output


# ══════════════════════════════════════════════════════════
# T40: quit
# ══════════════════════════════════════════════════════════

class TestQuit:
    """quit/q/exit 指令测试。"""

    def test_T40_quit(self, executor):
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
# T41-T42: 未知指令 / 空行
# ══════════════════════════════════════════════════════════

class TestUnknownCommand:
    """未知指令测试。"""

    def test_T41_unknown_command(self, executor):
        """execute("foobar") success=false。

        Arrange:
            CommandExecutor。
        Act:
            执行 "foobar"。
        Assert:
            success=False，输出包含错误信息。
        """
        result = executor.execute("foobar")
        assert result.success is False
        assert "未知" in result.output or "invalid" in result.output.lower()


class TestEmptyLine:
    """空行/空白行测试。"""

    def test_T42_empty_line(self, executor):
        """execute(""/"   ") success=true, output=""。

        Arrange:
            CommandExecutor。
        Act:
            执行空字符串和空白字符串。
        Assert:
            success=True，output 为空字符串。
        """
        for cmd in ("", "   ", "\t"):
            result = executor.execute(cmd)
            assert result.success is True
            assert result.output == ""


# ══════════════════════════════════════════════════════════
# T43: map with world_gen
# ══════════════════════════════════════════════════════════

class TestMap:
    """map 指令测试（需要 WorldGenerator）。"""

    @pytest.fixture
    def executor_with_world_gen(self, clock, calendar, i18n):
        """含 WorldGenerator 的 CommandExecutor 固件。"""
        from ascend.space import WorldGenerator
        wg = WorldGenerator(seed=42)
        return CommandExecutor(clock=clock, calendar=calendar, i18n=i18n, world_gen=wg)

    def test_T43_map_with_world_gen(self, executor_with_world_gen):
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

    def test_T44_map_without_world_gen(self, executor):
        """无 world_gen 时 execute("map") 返回提示。

        Arrange:
            标准 CommandExecutor（无 world_gen）。
        Act:
            执行 "map"。
        Assert:
            输出为未提供提示。
        """
        result = executor.execute("map")
        assert result.success is True
        assert "WorldGenerator" in result.output


# ══════════════════════════════════════════════════════════
# T45-T47: 扩展接口与属性
# ══════════════════════════════════════════════════════════

class TestRegisterCommand:
    """register_command 扩展接口测试。"""

    def test_T45_register_custom_command(self, executor):
        """注册自定义指令后可被 execute 路由。

        Arrange:
            CommandExecutor + 自定义 handler。
        Act:
            register_command("hello", handler) 后执行 "hello world"。
        Assert:
            返回 handler 的结果，args 正确传入。
        """
        received = []

        def handler(args):
            received.append(args)
            return CommandResult(success=True, output="hi")

        executor.register_command("hello", handler)
        result = executor.execute("hello world")
        assert result.success is True
        assert result.output == "hi"
        assert received == [["world"]]


class TestPausedProperty:
    """paused 属性测试。"""

    def test_T46_paused_reflects_clock(self, executor):
        """executor.paused 透传时钟暂停状态。

        Arrange:
            CommandExecutor。
        Act:
            读取 paused → pause → 再读取。
        Assert:
            与 clock.paused 一致。
        """
        assert executor.paused is False
        executor.execute("time pause")
        assert executor.paused is True


class TestRepr:
    """__repr__ 测试。"""

    def test_T47_repr(self, executor):
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
        assert "×1.0" in r
