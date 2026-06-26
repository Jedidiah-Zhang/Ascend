"""时间模式定义 — 四种时间推进方式和对应的模拟步长。"""

from enum import Enum


class TimeMode(Enum):
    """时间推进模式。

    Attributes:
        step_seconds: 该模式下一次 tick 对应的游戏秒数。
        description: 中文描述。
    """

    REALTIME = ("realtime", 12, "日常实时")
    SLEEP = ("sleep", 60, "睡眠")
    FAST_TRAVEL = ("fast_travel", 60, "快速旅行")
    LONG_JUMP = ("long_jump", 31536000, "长跳/闭关")

    def __init__(self, key: str, step_seconds: float, description: str) -> None:
        """初始化时间模式。

        Args:
            key: 模式标识字符串。
            step_seconds: 单步对应的游戏秒数。
            description: 中文描述。
        """
        self.key = key
        self.step_seconds = step_seconds
        self.description = description

    def __repr__(self) -> str:
        return f"TimeMode.{self.name}(step={self.step_seconds}s)"


# 真实时间与游戏时间的比率：2 真实小时 = 1 游戏天
# 1 游戏天 = 86400 游戏秒，2 真实小时 = 7200 真实秒
# 比率 = 86400 / 7200 = 12 游戏秒/真实秒
GAME_SECONDS_PER_REAL_SECOND = 12.0

# 游戏时间单位（秒）
GAME_MINUTE = 60
GAME_HOUR = 3600
GAME_DAY = 86400
GAME_YEAR = 31536000  # 360 游戏天
