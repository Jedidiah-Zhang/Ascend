"""时间常量 — 以 tick 为原子时间单位。

TICK_RATE = 24Hz，即 1 真实秒 = 24 tick。
1 游戏分钟 = 120 tick（5 真实秒）。
"""

# 以 tick 为单位的游戏时间常量
GAME_MINUTE = 120
GAME_HOUR = 7200
GAME_DAY = 172800
GAME_YEAR = 62208000  # 360 游戏天
