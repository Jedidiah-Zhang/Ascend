"""游戏运行配置 — 引擎与基础设施常量。

服务端口、线程与查询限流、出生点、存档根与 SQLite/事件归档参数。
世界时间刻度与内容参数见 ``olam.constants``。
"""

from __future__ import annotations

import os as _os
import tempfile as _tempfile

SERVER_HOST: str = "127.0.0.1"

SERVER_PORT: int = 9081

MAX_MESSAGE_SIZE: int = 16 * 1024 * 1024  # 16 MiB

# 地图瓦片生成线程池大小
TILE_WORKERS: int = 8

# 出生点周边预生成 chunk 半径（2 → 5×5 共 25 个）
INITIAL_CHUNK_RADIUS: int = 2

# 天气查询 API
MAX_WEATHER_QUERY_CHUNKS: int = 64      # get_weather 单请求最大 chunk 数（超限截断）

# 地图请求 API
MAX_CHUNK_QUERY: int = 512              # get_chunks 单请求最大 chunk 数（超限截断）

AUTOSAVE_INTERVAL: float = 5.0        # 统一周期保存间隔（真实秒）


# 无存档模式（测试/调试，world_id=None）的数据根：系统临时目录，
# 随系统临时目录清理
_DEV_DATA_ROOT: str = _os.path.join(_tempfile.gettempdir(), "ascend-dev")

# ChunkStore
CHUNK_STORE_DB_PATH: str = _os.path.join(_DEV_DATA_ROOT, "chunks.db")

CHUNK_STORE_MAX_SIZE: int = 49          # LRU 缓存最大 chunk 数

# WorldTree 归档
WT_MAX_MEMORY_EVENTS: int = 100_000     # 内存最大事件数

WT_GRAPH_WARMUP_EVENTS: int = 10_000    # 图预热事件数

WT_ARCHIVE_PATH: str = _os.path.join(_DEV_DATA_ROOT, "events.db")

# SQLite 性能参数
SQLITE_JOURNAL_MODE: str = "WAL"

SQLITE_SYNCHRONOUS: str = "NORMAL"

SQLITE_MMAP_SIZE: int = 268435456       # 256MB 内存映射

SQLITE_CACHE_SIZE: int = -8000          # 8MB 页缓存（负数 = KB）

# 存档根目录（用户主目录 .ascend/saves；ASCEND_SAVE_ROOT 环境变量
# 覆盖——测试隔离用，进程级测试写入临时目录）
SAVE_ROOT: str = _os.environ.get(
    "ASCEND_SAVE_ROOT",
    _os.path.join(_os.path.expanduser("~"), ".ascend", "saves"),
)

# 终端输出行限制
TERMINAL_OUTPUT_LINE_LIMIT: int = 500

TERMINAL_HISTORY_LIMIT: int = 100
