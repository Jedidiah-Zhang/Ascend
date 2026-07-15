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

const VENV_PYTHON_REL: String = ".venv/bin/python"
const BACKEND_SCRIPT_REL: String = "ascend-backend/run_server.py"
const BACKEND_STARTUP_TIMEOUT: float = 10.0

# ═══════════════════════════════════════════════════════════
# World — 世界
# ═══════════════════════════════════════════════════════════

const TILE_MAP_SIZE: int = 200  # 每个 chunk 的 tile 数

# ═══════════════════════════════════════════════════════════
# Camera — 相机
# ═══════════════════════════════════════════════════════════

const CAMERA_PAN_SPEED: float = 600.0
const CAMERA_ZOOM_STEP: float = 0.15
const CAMERA_ZOOM_MIN: float = 0.15
const CAMERA_ZOOM_MAX: float = 4.0

# ═══════════════════════════════════════════════════════════
# Map — 地图
# ═══════════════════════════════════════════════════════════

const INITIAL_VIEW_RADIUS: int = 2
const STREAM_MARGIN: int = 1
const UNLOAD_RADIUS: int = 3
const MAX_PENDING_TILES: int = 3
const PLAYER_SPEED: float = 80.0
const PLACE_TIME_BUDGET_US: int = 5000

# ═══════════════════════════════════════════════════════════
# UI — 界面
# ═══════════════════════════════════════════════════════════

const TERMINAL_OUTPUT_LINE_LIMIT: int = 500
const TERMINAL_HISTORY_LIMIT: int = 100
const TERMINAL_FONT_SIZE: int = 15
const TERMINAL_PROMPT: String = "$ "
