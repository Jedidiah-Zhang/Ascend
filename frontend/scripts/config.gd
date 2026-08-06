"""集中配置 — Godot 端可调参数单一定义源。

与 Python 端 ascend/config.py 保持相同的值，两边需同步更新。
"""
extends RefCounted

# ═══════════════════════════════════════════════════════════
# Server — 网络与连接
# ═══════════════════════════════════════════════════════════

const DEFAULT_HOST: String = "127.0.0.1"
const DEFAULT_PORT: int = 9081
const RECONNECT_INTERVAL: float = 2.0
const MAX_MESSAGE_SIZE: int = 16 * 1024 * 1024  # 16 MiB
const PROTOCOL_VERSION: int = 0x01  # 与后端 ascend/net/protocol.py 同步

const VENV_PYTHON_REL: String = ".venv/bin/python"
const BACKEND_SCRIPT_REL: String = "backend/run_server.py"
## 认证令牌文件（项目根相对路径，后端 run_server.py 启动时写入）
const TOKEN_FILE_REL: String = ".ascend_token"
## TCP 连接建立超时（秒）
const CONNECTING_TIMEOUT: float = 10.0
## 连接建立后最后收包超时（秒）：超过该时长未收到任何数据视为后端挂死，断开重连
const RECEIVE_TIMEOUT: float = 60.0
## 握手（hello/hello_ack）超时（秒）
const HELLO_TIMEOUT: float = 10.0
## 后端启动超时：大陆生成（侵蚀+水文）耗时 5-30s+，须覆盖整个启动窗口
const BACKEND_STARTUP_TIMEOUT: float = 60.0

# ═══════════════════════════════════════════════════════════
# World — 世界
# ═══════════════════════════════════════════════════════════

const TILE_MAP_SIZE: int = 200  # 每个 chunk 的 tile 数

# 游戏时间常量（与后端 ascend/config.py 同步）
const TICK_RATE: int = 24           # 1 真实秒 = 24 tick
const GAME_HOUR: int = 7200         # 1 游戏小时 = 7200 tick
const GAME_MINUTE: int = 120        # 1 游戏分钟 = 120 tick
const GAME_DAY: int = 172800        # 1 游戏天 = 172800 tick
const GAME_YEAR: int = 62208000     # 1 游戏年 = 360 游戏天

# ═══════════════════════════════════════════════════════════
# 3D — 正交等轴视角 3D 渲染（相机方向 (1,1,1)，见视觉风格设计文档）
# ═══════════════════════════════════════════════════════════

## 相机 FOV（极小值近似正交）
const CAMERA_3D_FOV: float = 5.0
## 相机默认距离
const CAMERA_3D_DISTANCE_DEFAULT: float = 400.0
## 缩放步长（距离变化）
const CAMERA_3D_DISTANCE_STEP: float = 40.0
const CAMERA_3D_DISTANCE_MIN: float = 60.0
const CAMERA_3D_DISTANCE_MAX: float = 1200.0

## 3D 玩家移动速度（每秒世界单位）
const PLAYER_3D_SPEED: float = 30.0
const PLAYER_3D_FAST_MULT: float = 3.0

# ═══════════════════════════════════════════════════════════
# UI — 界面
# ═══════════════════════════════════════════════════════════

const TERMINAL_OUTPUT_LINE_LIMIT: int = 500
const TERMINAL_HISTORY_LIMIT: int = 100
const TERMINAL_FONT_SIZE: int = 15
const TERMINAL_PROMPT: String = "$ "
