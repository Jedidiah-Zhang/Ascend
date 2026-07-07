"""世界时钟 — 驱动游戏时间推进，通过直接回调通知监听者。

控制接口：
    clock.tick()         — GameEngine 每帧调用，按当前 speed 推进
    clock.step()         — 强制推进 1 tick（忽略暂停/速度，调试用）
    clock.skip(ticks)    — 瞬间跳转 N tick，通知跳过回调
    clock.run_to(target) — 以当前 speed 逐 tick 模拟到目标时间
    clock.speed = 1.0    — 时间倍率（float，≥0，0.5=半速，1=正常）

    clock.pause()        — 暂停时间
    clock.resume()       — 恢复时间
    clock.paused         — 是否暂停（只读）

回调接口：
    clock.on_tick(cb)    — 每 tick 调用 cb(game_time:int)，返回 unsubscribe
    clock.on_skip(cb)    — 跳转时调用 cb(skipped:int, game_time:int)
"""

from typing import Callable

from ascend.log import get_logger
from .constants import GAME_HOUR, GAME_DAY, GAME_YEAR

logger = get_logger(__name__)


class WorldClock:
    """世界时钟。

    以 tick 为原子时间单位。tick() 每帧由 GameEngine 调用，
    按当前 speed 倍率推进；暂停时 tick() 空转不推进。

    tick 不经过 WorldTree——高频时钟信号通过直接回调分发，
    日历等订阅者检测到分钟/小时/天边界后自行发布语义事件。

    用法:
        clock = WorldClock()
        unsub = clock.on_tick(lambda t: print(f"tick {t}"))
        clock.tick()                    # 每帧
        clock.speed = 2.0               # 双倍速
        clock.pause()
        clock.step()                    # 调试：强制 1 tick
        clock.skip(3 * GAME_DAY)        # 瞬间跳 3 天
        clock.run_to(target)            # 模拟到目标时间
    """

    def __init__(self, epoch: int | None = None) -> None:
        """初始化时钟。

        Args:
            epoch: 起始游戏时间（tick）。None=默认 06:00。
        """
        if epoch is None:
            epoch = 6 * GAME_HOUR
        self._time: int = epoch
        self._speed: float = 1.0
        self._accumulator: float = 0.0
        self._paused: bool = False
        self._tick_count: int = 0
        self._on_tick_callbacks: list[Callable[[int], None]] = []
        self._on_skip_callbacks: list[Callable[[int, int], None]] = []

    # ── 回调注册 ──────────────────────────────────────────

    def on_tick(self, callback: Callable[[int], None]) -> Callable[[], None]:
        """每 tick 调用 callback(game_time)。

        线程不安全：请在主线程注册。返回 unsubscribe 函数。

        Args:
            callback: 签名为 (game_time: int) -> None。

        Returns:
            取消订阅的可调用对象，幂等。
        """
        self._on_tick_callbacks.append(callback)
        def _unsub() -> None:
            try:
                self._on_tick_callbacks.remove(callback)
            except ValueError:
                pass
        return _unsub

    def on_skip(self, callback: Callable[[int, int], None]) -> Callable[[], None]:
        """跳转时调用 callback(skipped_ticks, game_time)。

        Args:
            callback: 签名为 (skipped: int, game_time: int) -> None。

        Returns:
            取消订阅的可调用对象，幂等。
        """
        self._on_skip_callbacks.append(callback)
        def _unsub() -> None:
            try:
                self._on_skip_callbacks.remove(callback)
            except ValueError:
                pass
        return _unsub

    # ── 属性 ──────────────────────────────────────────────

    @property
    def time(self) -> int:
        """当前世界时间（tick 计数）。"""
        return self._time

    @property
    def speed(self) -> float:
        """时间倍率（≥0，1=正常，120=每分钟一跳）。"""
        return self._speed

    @speed.setter
    def speed(self, value: float) -> None:
        if value < 0:
            raise ValueError(f"speed 不能为负，实际为 {value}")
        self._speed = float(value)

    @property
    def paused(self) -> bool:
        """时间是否暂停。"""
        return self._paused

    @property
    def tick_count(self) -> int:
        """累计时间推进操作次数（每次 tick/step/skip 调用 +1）。

        注意：这是"推进次数"，不是"世界经历的 tick 数"。
        speed=2 时一次 tick() 推进 2 tick 但只 +1。
        要获取世界实际经过的 tick 数请用 ``time`` 属性。
        """
        return self._tick_count

    def pause(self) -> None:
        """暂停时间推进，tick() 变为空操作。step() 仍可用。"""
        self._paused = True

    def resume(self) -> None:
        """恢复时间推进。"""
        self._paused = False

    # ── 推进 ──────────────────────────────────────────────

    def tick(self) -> None:
        """推进一帧。

        由 GameEngine 每帧调用。暂停时或 speed≤0 时不推进。
        使用浮点累加器处理小数 speed（如 speed=0.5 时每 2 帧推进 1 tick）。
        """
        if self._paused or self._speed <= 0:
            return

        self._accumulator += self._speed
        advance = int(self._accumulator)
        if advance == 0:
            return

        self._accumulator -= advance
        self._time += advance
        self._tick_count += 1

        game_time = self._time
        for cb in self._on_tick_callbacks:
            try:
                cb(game_time)
            except Exception:
                logger.exception("tick 回调异常")

    def step(self) -> None:
        """强制推进恰好 1 tick，忽略暂停和 speed。

        调试/手动控制用。正常游戏循环不应调用此方法。
        """
        self._time += 1
        self._tick_count += 1

        game_time = self._time
        for cb in self._on_tick_callbacks:
            try:
                cb(game_time)
            except Exception:
                logger.exception("step 回调异常")

    def skip(self, ticks: int) -> None:
        """瞬间跳转 N tick，不模拟中间过程。

        通知所有 on_skip 回调（如日历需要检测跨过的日/时边界）。

        Args:
            ticks: 要跳过的 tick 数，必须 > 0。

        Raises:
            ValueError: ticks ≤ 0。
        """
        if ticks <= 0:
            raise ValueError(f"跳过 tick 数必须 > 0，实际为 {ticks}")

        skipped = ticks
        self._time += ticks
        self._tick_count += 1

        game_time = self._time
        for cb in self._on_skip_callbacks:
            try:
                cb(skipped, game_time)
            except Exception:
                logger.exception("skip 回调异常")

        logger.info("跳转: +%d tick (→ %d)", ticks, self._time)

    def run_to(self, target: int) -> None:
        """以当前 speed 逐 tick 推进到目标时间。

        每个中间 tick 都触发 on_tick 回调，日历等模块正常运作。
        用于睡眠、快速旅行等需要中间事件的场景。

        Args:
            target: 目标时间（tick 数），必须大于当前时间。

        Raises:
            ValueError: target 在过去，或 speed ≤ 0。
        """
        if target <= self._time:
            raise ValueError(
                f"目标时间 {target} 必须在当前时间 {self._time} 之后"
            )
        if self._speed <= 0:
            raise ValueError(
                f"run_to 需要 speed > 0，当前 speed={self._speed}；"
                f"暂停状态请用 resume() 或 step()，瞬跳请用 skip()"
            )

        was_paused = self._paused
        self._paused = False

        tick_count = 0
        while self._time < target:
            self.tick()
            tick_count += 1

        self._paused = was_paused
        logger.info("模拟完成: → %d (%d tick, speed=%.1f)", self._time, tick_count, self._speed)

    def game_days(self) -> float:
        return self._time / GAME_DAY

    def game_years(self) -> float:
        return self._time / GAME_YEAR

    def __repr__(self) -> str:
        state = "paused" if self._paused else f"x{self._speed:.1f}"
        return (
            f"WorldClock(time={self._time}t, "
            f"day={self.game_days():.1f}, "
            f"speed={state}, "
            f"ticks={self._tick_count})"
        )
