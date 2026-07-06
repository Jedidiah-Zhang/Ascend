"""游戏循环整合测试 — 串联事件总线、世界时钟、日历系统，模拟游戏运行。

用法:
    # 作为 pytest 运行
    PYTHONPATH=ascend-backend .venv/bin/python -m pytest tests/integration/test_game_loop.py -v -s

    # 直接运行
    PYTHONPATH=ascend-backend .venv/bin/python tests/integration/test_game_loop.py
"""

import time as _real_time
from dataclasses import dataclass, field

from ascend.world_tree import world_tree
from ascend.time import WorldClock, GameCalendar, GAME_DAY, GAME_HOUR
from ascend.log import setup_logging, get_logger

logger = get_logger(__name__)


# ── 模拟运行器 ──────────────────────────────────────────────────────

@dataclass
class GameSession:
    """一次模拟会话的状态快照。"""

    clock: WorldClock
    calendar: GameCalendar

    # 统计
    tick_count: int = 0
    day_changes: list[dict] = field(default_factory=list)
    tick_events: list[dict] = field(default_factory=list)
    start_real_time: float = 0.0

    def elapsed_real(self) -> float:
        """从会话开始经过的真实秒数。"""
        return _real_time.monotonic() - self.start_real_time


class GameLoop:
    """游戏主循环 — 串联所有已实现系统。

    管理时钟推进、日历追踪和事件监控，
    可作为测试工具或开发阶段的"游戏运行器"。

    用法:
        loop = GameLoop()
        loop.start()
        loop.run_days(3)  # 模拟 3 个游戏日
        loop.report()     # 打印运行报告
        loop.stop()
    """

    def __init__(self) -> None:
        """初始化游戏循环，创建时钟和日历。"""
        setup_logging()
        logger.info("══════ Ascend 游戏启动 ══════")

        self.clock = WorldClock()
        self.calendar = GameCalendar()
        self.session: GameSession | None = None

        # 监控事件
        self._unsubscribers: list = []
        self._setup_monitors()

        logger.info("系统就绪: WorldTree, WorldClock, GameCalendar")

    def _setup_monitors(self) -> None:
        """订阅关键事件，记录运行统计。"""
        def on_tick(event):
            if self.session:
                self.session.tick_count += 1
                self.session.tick_events.append({
                    "game_time": event.data["game_time"],
                    "mode": event.data["mode"],
                })

        def on_day_change(event):
            if self.session:
                self.session.day_changes.append(event.data)
            logger.info("📅 新的一天: 第 %d 天", event.data["day"])

        self._unsubscribers.append(world_tree.subscribe("game_tick", on_tick))
        self._unsubscribers.append(world_tree.subscribe("day_change", on_day_change))

    # ── 生命周期 ──────────────────────────────────────────────────

    def start(self) -> GameSession:
        """开始新会话。

        Returns:
            新创建的 GameSession。
        """
        self.session = GameSession(
            clock=self.clock,
            calendar=self.calendar,
            start_real_time=_real_time.monotonic(),
        )
        logger.info(
            "会话开始 | speed=×%.1f 第 %d 天",
            self.clock.speed, self.calendar.day,
        )
        return self.session

    def stop(self) -> None:
        """停止当前会话，取消所有订阅。"""
        for unsub in self._unsubscribers:
            unsub()
        self._unsubscribers.clear()
        self.calendar.shutdown()
        logger.info("会话结束")

    # ── 模拟运行 ──────────────────────────────────────────────────

    def run_realtime(self, real_seconds: float, fps: float = 60.0) -> None:
        """以实时模式运行指定真实秒数。

        Args:
            real_seconds: 运行的真实秒数。
            fps: 每秒帧数（tick 频率），默认 60。
        """
        if self.session is None:
            self.start()

        dt = 1.0 / fps
        total_frames = int(real_seconds * fps)
        logger.info("实时运行 %.1fs (%d frames, %.0f fps)", real_seconds, total_frames, fps)

        for frame in range(total_frames):
            self.clock.tick()

            # 每 10 游戏分钟打印一次状态
            if frame % 600 == 0 and frame > 0:
                logger.info(
                    "  游戏时间: 第 %d 天 %.1f 小时",
                    self.calendar.day,
                    self.calendar.time_of_day(self.clock.time) / GAME_HOUR,
                )

    def run_days(self, days: int) -> None:
        """模拟指定游戏天数。

        Args:
            days: 要模拟的游戏天数。
        """
        if self.session is None:
            self.start()

        target = self.clock.time + days * GAME_DAY
        logger.info("快进 %d 天", days)
        old_speed = self.clock.speed
        self.clock.speed = 120
        self.clock.run_to(target)
        self.clock.speed = old_speed

    def run_until(self, game_time: int, speed: float = 120.0) -> None:
        """模拟到指定游戏时间。

        Args:
            game_time: 目标游戏时间（秒）。
            speed: 时间速度，默认 120。
        """
        if self.session is None:
            self.start()

        days = (game_time - self.clock.time) / GAME_DAY
        logger.info("快进 %.1f 天 → 目标时间 %.0f", days, game_time)
        old_speed = self.clock.speed
        self.clock.speed = speed
        self.clock.run_to(game_time)
        self.clock.speed = old_speed

    # ── 报告 ──────────────────────────────────────────────────────

    def report(self) -> str:
        """生成当前会话的运行报告。

        Returns:
            格式化的报告字符串。
        """
        if self.session is None:
            return "无活跃会话"

        s = self.session
        lines = [
            "=" * 50,
            "  Ascend 游戏运行报告",
            "=" * 50,
            f"  游戏时间:      {s.clock.time:,} tick",
            f"  当前日:        第 {s.calendar.day} 天",
            f"  经过天数:      {s.calendar.elapsed_days} 天",
            f"  日期变更:      {s.calendar.day_change_count} 次",
            f"  速度:          ×{s.clock.speed:.1f}" + (" (暂停)" if s.clock.paused else ""),
            f"  累计 tick:     {s.clock.tick_count:,}",
            f"  真实耗时:      {s.elapsed_real():.2f}s",
            f"  总线事件数:    {world_tree.event_count:,}",
            f"  活跃订阅:      {world_tree.subscriber_count}",
            "=" * 50,
        ]
        report = "\n".join(lines)
        logger.info("运行报告:\n%s", report)
        return report


# ── pytest 测试用例 ──────────────────────────────────────────────────

class TestGameLoop:
    """游戏循环整合测试。"""

    def test_full_session(self):
        """完整会话：启动 → 快进多天 → 验证系统联动。"""
        loop = GameLoop()

        try:
            # 启动（游戏从第 1 天 06:00 开始）
            session = loop.start()
            initial_time = 6 * GAME_HOUR
            assert session is not None
            assert loop.calendar.day == 1
            assert loop.clock.time == initial_time

            # 快进 3 天
            loop.run_days(3)

            # 验证：3 天后应该是第 4 天
            assert loop.calendar.day == 4, f"期望 day=4，实际 day={loop.calendar.day}"
            assert loop.calendar.elapsed_days == 3
            assert loop.calendar.day_change_count == 3

            # 验证：时钟时间应该接近 initial + 3 * GAME_DAY
            expected = initial_time + 3 * GAME_DAY
            assert loop.clock.time == expected, \
                f"期望 time={expected}，实际 time={loop.clock.time}"

            # 验证：总线上应该有 game_tick 事件和 day_change 事件
            assert world_tree.event_count > 0, "总线应该有事件"
            assert len(session.day_changes) == 3, \
                f"应该有 3 次 day_change，实际 {len(session.day_changes)}"

            # 再快进 2 天
            loop.run_days(2)
            assert loop.calendar.day == 6
            assert len(session.day_changes) == 5

            # 打印报告
            loop.report()

        finally:
            loop.stop()

    def test_realgame_minute(self):
        """实时 tick：短时间内推进，不触发日期变更。"""
        loop = GameLoop()

        try:
            loop.start()
            loop.clock.speed = 1.0

            # tick 600 帧（600 tick，约 5 游戏分钟）
            for _ in range(600):
                loop.clock.tick()

            # 应该没有触发日期变更
            assert loop.calendar.day == 1
            assert loop.calendar.day_change_count == 0

            # 但时间应该前进了
            assert loop.clock.time > 0

        finally:
            loop.stop()

    def test_day_change_events(self):
        """验证 day_change 事件的数据正确性。"""
        loop = GameLoop()

        try:
            loop.start()

            # 快进 1 天
            loop.run_days(1)

            changes = loop.session.day_changes
            assert len(changes) == 1
            assert changes[0]["day"] == 2
            assert changes[0]["previous_day"] == 1
            assert changes[0]["elapsed_days"] == 1

        finally:
            loop.stop()

    def test_speed_switch_during_session(self):
        """运行时切换时间速度。"""
        loop = GameLoop()

        try:
            loop.start()

            # 高速模式快进
            loop.clock.speed = 120
            loop.run_days(1)
            assert loop.calendar.day == 2

            # 恢复实时速度
            loop.clock.speed = 1.0
            assert loop.clock.speed == 1.0

        finally:
            loop.stop()


# ── 直接运行入口 ──────────────────────────────────────────────────────

if __name__ == "__main__":
    """直接运行此文件启动一个演示会话。"""
    import logging as _logging
    loop = GameLoop()
    try:
        loop.start()

        print("\n  ▶ 实时运行 2 秒（约 120 tick）...")
        loop.run_realtime(2.0)
        print(f"     游戏时间: {loop.clock.time} tick, 第 {loop.calendar.day} 天")

        print("\n  ▶ 快进到第 2 天...")
        loop.run_days(1)
        print(f"     游戏时间: {loop.clock.time} tick, 第 {loop.calendar.day} 天")

        print("\n  ▶ 快进 4 天（到第 6 天）...")
        loop.run_days(4)

        print()
        print(loop.report())

    finally:
        loop.stop()
        _logging.shutdown()
