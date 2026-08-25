"""集中配置 — Godot 端可调参数单一定义源。

与 Python 端 ascend/config.py 保持相同的值，两边需同步更新。
"""
class_name Config

extends RefCounted

# ═══════════════════════════════════════════════════════════
# Server — 网络与连接
# ═══════════════════════════════════════════════════════════

const DEFAULT_HOST: String = "127.0.0.1"
const DEFAULT_PORT: int = 9081
## 重连基础间隔（秒）；连续失败指数退避，上限 RECONNECT_MAX_INTERVAL，成功复位
const RECONNECT_INTERVAL: float = 2.0
## 重连退避封顶间隔（秒）
const RECONNECT_MAX_INTERVAL: float = 32.0
## 握手失败重试上限：超过即进入 FAILED 终态（版本不兼容不计数、立即终态）
const HANDSHAKE_MAX_RETRIES: int = 5
const MAX_MESSAGE_SIZE: int = 16 * 1024 * 1024  # 16 MiB
const PROTOCOL_VERSION: int = 0x01  # 与后端 ascend/net/protocol.py 同步
## tile 数据 BLOB 版本（客户端已知/支持的版本；握手时上报，服务端以
## 其 TILE_GRID_VERSION 裁决兼容性——见 handshake.gd / client_handler.py）
## issue #42 材质 9→8 重排后重新标 v1（无历史版本）。
const TILE_BLOB_VERSION: int = 1

const VENV_PYTHON_REL: String = ".venv/bin/python"
const BACKEND_SCRIPT_REL: String = "backend/run_server.py"
## 认证令牌文件（项目根相对路径，后端 run_server.py 启动时写入）
const TOKEN_FILE_REL: String = ".ascend_token"
## TCP 连接建立超时（秒）
const CONNECTING_TIMEOUT: float = 10.0
## 连接建立后最后收包超时（秒）：超过该时长未收到任何数据视为后端挂死，断开重连
const RECEIVE_TIMEOUT: float = 60.0
## 请求超时（秒）：请求发出后未收到响应即投本地错误（UI 复位忙状态，防假死）
const REQUEST_TIMEOUT: float = 10.0
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
# 2D — 正俯视扁平化渲染（见视觉风格设计文档）
# ═══════════════════════════════════════════════════════════

## 每地形格像素尺寸（tile 基准；角色 sprite 独立于 tile，可 16×20-24）
const TILE_PIXEL_SIZE: int = 16
## 相机默认缩放（zoom=1 即 1 tile = TILE_PIXEL_SIZE 屏幕像素）
const CAMERA_ZOOM_DEFAULT: float = 1.0
## 滚轮缩放步长（倍率，zoom_in 乘、zoom_out 除）
const CAMERA_ZOOM_STEP: float = 1.2
const CAMERA_ZOOM_MIN: float = 0.5
const CAMERA_ZOOM_MAX: float = 4.0

## 2D 玩家移动速度（每秒 tile 数，与后端世界坐标一致）
const PLAYER_2D_SPEED: float = 30.0
const PLAYER_2D_FAST_MULT: float = 3.0

# ── 海拔五信号（视觉风格设计文档；阈值单位 = 米，与后端 elevation 同刻度） ──

## 崖壁贴片：相邻 tile 海拔差 > 此值时，高侧边缘画悬崖 sprite
const CLIFF_ELEVATION_DIFF_M: float = 8.0
## 固定方向投影：光照方向固定（西北），东南侧高差 > 此值时铺半透明阴影贴片
const SHADOW_ELEVATION_DIFF_M: float = 6.0
## 等高线调试层：500m 间隔（500/1000/1500/2000 恰与 ALPINE 阈值对齐）
const CONTOUR_INTERVAL_M: float = 500.0
## 装饰密度海拔档位（米）：低于档位 0 无装饰，之后逐档加密（见 TerrainTileBuilder）
const DECOR_ELEVATION_TIERS: Array[float] = [300.0, 1000.0, 2000.0]
## 等高线调试层默认开关（开发期调试用；挂调试面板后改为运行时开关）
const CONTOUR_LAYER_ENABLED: bool = false

# ═══════════════════════════════════════════════════════════
# UI — 界面
# ═══════════════════════════════════════════════════════════

const TERMINAL_OUTPUT_LINE_LIMIT: int = 500
const TERMINAL_HISTORY_LIMIT: int = 100
const TERMINAL_FONT_SIZE: int = 15
const TERMINAL_PROMPT: String = "$ "
