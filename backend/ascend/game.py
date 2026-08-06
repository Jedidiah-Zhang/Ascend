"""游戏引擎 — 串联 WorldGenerator、GameServer、EventBridge 和 MessageDispatcher。

在后台线程中运行 tick 循环，以固定频率处理传入的客户端消息。

架构（服务器与世界观解耦）:
  - 网络层（GameServer / MessageDispatcher / EventBridge）常驻：
    引擎启动即就绪（服务模式），跨读档重建存活，客户端全程不断线；
  - 世界观（生成器 / ChunkStore / 实体 / 天气 / 日历 / 事件归档）随
    读档重建整体替换，由 start() 构建、_cleanup_world() 释放；
  - 世界就绪信号 = world_initialized 事件（含出生点），前端据此接入；
    world_reloading 事件在重建开始时广播（前端显示加载提示）。

启动流程:
  1. 网络层就绪（服务模式：仅存档管理，不生成世界）
  2. save_load 请求 → tick 线程内读档重建：随机 seed（读档取 manifest.seed）
  3. 主动生成大陆宏观场（侵蚀+水文，约 30s；缓存命中秒级）
  4. 随机选取出生点（海岸低地，避开河流/湖泊，海陆地形多样）
  5. 预生成出生点周边 radius 个 chunk 的详细 tile 层
  6. 创建实体管理器接入事件管线
  7. 配置世界树归档 + 启动 tick 循环（时钟+日历随之运转）
"""

import os
import random
import threading
import time as _real_time

from ascend.config import (
    TICK_RATE,
    TICK_DT,
    SERVER_HOST,
    SERVER_PORT,
    INITIAL_CHUNK_RADIUS,
    BIRTH_ELEV_MIN,
    BIRTH_ELEV_MAX,
    CHUNK_STORE_MAX_SIZE,
    CHUNK_STORE_DB_PATH,
    WT_MAX_MEMORY_EVENTS,
    WT_ARCHIVE_PATH,
    WT_GRAPH_WARMUP_EVENTS,
    SAVE_ROOT,
    SAVE_STATE_INTERVAL,
    SAVE_CHUNK_FLUSH_INTERVAL,
)
from ascend.log import get_logger
from ascend.net import GameServer, MessageDispatcher, EventBridge
from ascend.net.protocol import make_response
from ascend.net.handlers.map_handler import make_map_handlers
from ascend.net.handlers.terminal_handler import make_terminal_handler
from ascend.net.handlers.weather_handler import make_weather_handler
from ascend.net.handlers.player_handler import make_player_handler
from ascend.net.handlers.entity_handler import make_entity_handlers
from ascend.net.handlers.save_handler import make_save_handlers
from ascend.space import WorldGenerator, TileGenerator
from ascend.space.chunk_store import ChunkStore
from ascend.entity import EntityManager, PlayerService
from ascend.weather import WeatherEngine
from ascend.terminal import CommandExecutor
from ascend.time import WorldClock, GameCalendar
from ascend.i18n import I18n
from ascend.world_tree import world_tree, Event, AffectedParty
from ascend.save import SaveManager, collect_state, aligned_time, apply_clock, apply_player

logger = get_logger(__name__)

# 8 邻域偏移（用于海岸像素检测）
_NDX = (1, -1, 0, 0, 1, -1, 1, -1)
_NDY = (0, 0, 1, -1, 1, 1, -1, -1)

world_tree.register_event_schema(
    "world_initialized",
    required={"seed": int, "birth_chunk": list, "loaded_chunks": int},
    optional={"world_id": str},
    description="地图生成完毕、出生点确定、周边区块就绪后发布",
)


class GameEngine:
    """游戏引擎。在后台线程中运行，管理网络通信 + 世界生成。

    Usage:
        engine = GameEngine()        # seed=0 自动随机
        engine.start()
        # ... 运行中 ...
        engine.stop()
    """

    # tick 循环连续异常熔断阈值
    _MAX_CONSECUTIVE_ERRORS: int = 5

    def __init__(self, seed: int = 0, host: str = SERVER_HOST, port: int = SERVER_PORT) -> None:
        """初始化引擎。

        Args:
            seed: 世界种子。0 表示启动时自动随机。
            host: 服务器监听地址。
            port: 服务器监听端口。
        """
        self.seed: int = seed
        self._host: str = host
        self._port: int = port
        self.world_gen: WorldGenerator | None = None
        self.server: GameServer | None = None
        self.dispatcher: MessageDispatcher | None = None
        self.event_bridge: EventBridge | None = None
        self.clock: WorldClock = WorldClock()
        self.calendar: GameCalendar | None = None  # start() 时创建（世界存在才需要日历）
        self.i18n: I18n = I18n()
        self._executor: CommandExecutor | None = None
        self.entity_manager: EntityManager | None = None
        self.player_service: PlayerService | None = None
        self.weather_engine: WeatherEngine | None = None
        self.tile_generator: TileGenerator | None = None
        self.birth_chunk: tuple[int, int] | None = None
        self.chunk_store: ChunkStore | None = None
        # 存档
        self.save_manager: SaveManager | None = None
        self.world_id: str | None = None      # 当前存档位（None=无存档模式）
        self._manifest = None                 # 内存中的 Manifest（touch 用）
        self._load_state: dict | None = None  # 读档恢复的状态
        self._pending_load: tuple | None = None  # 待执行读档请求 (world_id, snapshot)
        self._last_state_save: float = 0.0    # 上次 state 落盘时刻（monotonic）
        self._last_chunk_flush: float = 0.0   # 上次 dirty chunk flush 时刻
        self._world_start_monotonic: float = 0.0
        self._reloading: bool = False         # 读档重建中（run_server 据此抑制自动停止）
        self._service_mode: bool = False      # 服务模式：仅网络+存档，无世界
        self._world_request_types: set[str] = set()  # 世界观处理程序清单（读档时重注册）
        self._running: threading.Event = threading.Event()
        self._stop_requested: threading.Event = threading.Event()  # stop() 读档期取消标志
        self._thread: threading.Thread | None = None

    @property
    def is_reloading(self) -> bool:
        """读档重建中（网络层常驻，世界生成期间抑制外部自动停止）。"""
        return self._reloading

    def __repr__(self) -> str:
        """返回引擎状态摘要。

        Returns:
            含种子、运行状态、客户端数的 repr 字符串。
        """
        client_count = self.server.client_count if self.server else 0
        return (
            f"GameEngine(seed={self.seed}, "
            f"running={self._running.is_set()}, "
            f"paused={self.paused}, "
            f"clients={client_count})"
        )

    @property
    def paused(self) -> bool:
        """游戏是否暂停。

        Returns:
            True 表示暂停。
        """
        return self.clock.paused

    @paused.setter
    def paused(self, value: bool) -> None:
        """设置暂停状态。

        Args:
            value: True 暂停，False 恢复。
        """
        if value:
            self.clock.pause()
        else:
            self.clock.resume()

    def start_service(self) -> None:
        """服务模式启动：只开 TCP 服务 + 存档管理，不生成世界。

        主菜单只需 save_list / save_create / save_rename / save_delete /
        save_export；世界在 save_load 请求时才生成（读档重建流程），
        避免后端启动即等待大陆生成（5-30s+）。

        时钟不推进（无日历事件、不进归档）；tick 循环仅处理网络消息。

        网络层（服务器/分发器/事件桥）在此常驻启动：之后 start()
        读档只替换世界观，不重启服务器，客户端全程不断线。

        幂等：已在运行时调用无效果。
        """
        if self._running.is_set():
            return
        self._ensure_network()
        self._service_mode = True
        self.world_id = None
        self._manifest = None
        self._running.set()
        self._ensure_tick_thread()
        logger.info(
            "服务模式已启动: %s:%d（世界将在读档时生成）",
            SERVER_HOST, SERVER_PORT,
        )

    def _ensure_network(self) -> None:
        """确保网络层就绪（服务器/分发器/事件桥/存档处理程序），幂等。

        服务器与世界观解耦：跨读档重建常驻，客户端不断线。
        存档处理程序与世界观无关，只注册一次；世界观处理程序由
        _register_world_handlers 在读档时以 replace 覆盖。
        """
        if self.server is not None:
            return
        if self.save_manager is None:
            self.save_manager = SaveManager(SAVE_ROOT)
        self.server = GameServer(host=self._host, port=self._port)
        self.server.start()
        self.dispatcher = MessageDispatcher(self.server)
        save_handlers = make_save_handlers(self.save_manager, self)
        for req_type, handler in save_handlers.items():
            self.dispatcher.register(req_type, handler)
        self.event_bridge = EventBridge(world_tree, self.server)
        self.event_bridge.install()
        logger.info("网络层已就绪: %s:%d", SERVER_HOST, SERVER_PORT)

    def _ensure_tick_thread(self) -> None:
        """确保 tick 循环线程存活（常驻线程，跨读档重建不重建）。"""
        if self._thread is not None and self._thread.is_alive():
            return
        self._thread = threading.Thread(
            target=self._run_loop, name="game-engine", daemon=True
        )
        self._thread.start()

    def start(
        self,
        *,
        world_id: str | None = None,
    ) -> None:
        """初始化所有子系统并在后台启动 tick 循环。

        启动模式:
          - 无参: 随机种子新世界（无存档模式，测试/调试用）
          - world_id: 从存档位读档（seed/时钟/玩家/DB 路径全部恢复）

        读档流程:
          1. 恢复时钟（对齐归档时间，防时间倒流）
          2. 按 manifest.seed 重建大陆宏观场
          3. 以存档目录路径打开 ChunkStore / 事件归档
          4. 静默恢复玩家实体（不发布 entity_born，Issue #20/#25 语义）

        幂等：已在运行时调用无效果。
        """
        if self._running.is_set():
            return
        self._service_mode = False

        # 0. 存档准备
        self._load_state = None
        self._manifest = None
        if world_id is not None:
            manifest = self.save_manager.get_manifest(world_id)
            self._manifest = manifest
            self.world_id = world_id
            self.seed = manifest.seed
            # state 文件存在才读档恢复；新世界首次进入尚无 state
            if os.path.isfile(self.save_manager.state_path(world_id)):
                self._load_state = self.save_manager.read_state(world_id)
        else:
            self.world_id = None

        # 0b. 读档恢复时钟（须先于日历创建，避免虚假 day_change 事件）
        if self._load_state is not None:
            self._load_state.setdefault("clock", {})["time"] = aligned_time(self._load_state)
            apply_clock(self._load_state, self.clock)

        # 0c. 重建日历（基于恢复后的时钟；stop 后为 None 或需重启）
        if self.calendar is not None:
            self.calendar.shutdown()
        self.calendar = GameCalendar(clock=self.clock)

        # 1. 种子（seed=0 仅在无存档模式启动时随机；存档世界在
        # create_world 时已定案——见 SaveManager.create_world）。
        if self.seed == 0 and self._manifest is None:
            self.seed = random.randint(1, 2**31 - 1)
        logger.info("游戏引擎启动: seed=%d world=%s", self.seed, self.world_id)

        # 2. 世界生成器 + 主动生成大陆宏观场（侵蚀+水文，首次约 5-30s，
        #    之后从存档内 continent.bin 缓存恢复，秒级）
        continent_cache_path = (
            self.save_manager.continent_path(self.world_id)
            if self.world_id else None
        )
        self.world_gen = WorldGenerator(
            seed=self.seed, continent_cache_path=continent_cache_path,
        )
        continent = self.world_gen.ensure_continent(
            progress_cb=self._broadcast_world_progress,
        )
        self.tile_generator = TileGenerator(
            seed=self.seed, continent=continent,
        )
        logger.info("大陆生成完成: %s", continent)

        # 3. 出生点（读档优先用存档中的出生点）
        if self._manifest is not None and self._manifest.birth_chunk:
            self.birth_chunk = tuple(self._manifest.birth_chunk)
        else:
            self.birth_chunk = self._select_birth_point(continent, self.seed)
        logger.info("出生点: chunk %s", self.birth_chunk)

        # 3b. 初始化 ChunkStore（读档时直接以存档内路径打开）
        db_path = (
            self.save_manager.chunks_db_path(self.world_id)
            if self.world_id else CHUNK_STORE_DB_PATH
        )
        self.chunk_store = ChunkStore(
            db_path, max_size=CHUNK_STORE_MAX_SIZE,
            on_evict=self._on_chunk_evicted,
        )
        # 读档防篡改（设计文档承诺）：明文 SQLite 靠完整性校验兜底
        if self.world_id:
            try:
                self.chunk_store.verify()
            except ValueError as exc:
                raise RuntimeError(
                    f"存档 chunk 数据校验失败: {exc}"
                ) from exc

        # 4. 预生成出生点周边区块（前端就绪后请求时立即命中缓存）
        self._broadcast_world_progress("chunks")
        self._generate_initial_chunks(continent)
        logger.info(
            "已生成周边 %d 个区块 (radius=%d)",
            len(self.chunk_store), INITIAL_CHUNK_RADIUS,
        )

        # 5. 实体管理器（接入事件管线）
        self.entity_manager = EntityManager()

        # 5a. 权威玩家实体（读档静默恢复，不发布 entity_born）
        # 地图为有界矩形：chunk 坐标 ∈ [0, grid//2)，玩家坐标越界钳制
        self.player_service = PlayerService(
            self.entity_manager, self.clock, self.birth_chunk,
            max_chunk=(
                continent.grid_width // 2,
                continent.grid_height // 2,
            ),
        )
        player_state = (
            self._load_state.get("player", {}) if self._load_state else {}
        )
        if player_state.get("entity_id"):
            apply_player(self._load_state, self.player_service)
        else:
            self.player_service.birth()
        logger.info("玩家实体就绪: %r", self.player_service)

        # 5b. 天气引擎（接入已加载 chunk 的天气基线）
        self.weather_engine = WeatherEngine(self.clock, seed=self.seed)
        for (cx, cy), chunk in self.chunk_store.items():
            self.weather_engine.register_chunk(
                cx, cy, chunk.annual_baseline, chunk.climate_zone,
                chunk.sea_level_temp,
            )
        logger.info("天气引擎已接入 %d 个 chunk", len(self.chunk_store))

        # 5c. 网络层（幂等：服务模式已就绪则保持，客户端不断线）
        self._ensure_network()

        # 8. 终端指令执行器
        self._executor = CommandExecutor(
            clock=self.clock,
            calendar=self.calendar,
            i18n=self.i18n,
            weather_engine=self.weather_engine,
            default_chunk=self.birth_chunk,
            player_service=self.player_service,
            entity_manager=self.entity_manager,
        )

        # 8b. 世界观处理程序（replace 语义：读档重建时覆盖旧闭包）
        self._register_world_handlers()

        # 9. 世界树：归档 + 内存限制 + 图预热
        # （读档时切换到存档内归档路径，旧归档自动关闭）
        archive_path = (
            self.save_manager.events_db_path(self.world_id)
            if self.world_id else WT_ARCHIVE_PATH
        )
        world_tree.configure(
            archive_path=archive_path,
            max_memory_events=WT_MAX_MEMORY_EVENTS,
        )
        # 读档防篡改：事件归档完整性校验（设计文档承诺）
        if self.world_id:
            try:
                world_tree.verify_archive()
            except ValueError as exc:
                raise RuntimeError(
                    f"存档事件归档校验失败: {exc}"
                ) from exc
        world_tree.warmup_graph(max_events=WT_GRAPH_WARMUP_EVENTS)
        logger.info(
            "已配置世界树: archive=%s max_memory=%d",
            archive_path, WT_MAX_MEMORY_EVENTS,
        )

        # 10. 发布世界初始化事件（时钟此时停在 epoch/存档时间，尚未推进）
        self._publish_world_initialized()
        self._persist_manifest()

        # 11. 启动 tick 循环——clock.tick() 推进时间，calendar 自动收事件。
        # 常驻线程：服务模式已启动时复用，跨读档重建不重建。
        self._last_state_save = _real_time.monotonic()
        self._last_chunk_flush = self._last_state_save
        self._world_start_monotonic = self._last_state_save
        self._running.set()
        self._ensure_tick_thread()
        logger.info("游戏引擎在后台运行 (tick=%.1f Hz)", TICK_RATE)

    def request_load(
        self, world_id: str | None = None, snapshot: str | None = None,
    ) -> None:
        """请求读档重建（异步，tick 线程内执行）。

        网络层入口：校验并置位读档请求；已有请求在处理时抛 ValueError。
        重建失败时引擎广播 world_reloading_failed 事件（前端可感知并
        结束加载状态），不在此处同步抛异常——调用方已先行返回"已受理"。

        Args:
            world_id: 目标存档位。
            snapshot: 快照文件（回滚）；None 时加载活目录。

        Raises:
            ValueError: 已有读档请求在处理中。
        """
        if self._pending_load is not None:
            raise ValueError("已有读档请求在处理中")
        self._pending_load = (world_id, snapshot)

    def stop(self) -> None:
        """停止引擎并清理所有子系统。

        退出前执行最终保存（flush + 最终 state 落盘），
        等价 MC 关服保存——实时存档保证此步幂等、开销小。

        幂等：已停止时调用无效果。

        读档取消：stop() 在读档重建（_pending_load 已置位、tick 线程
        正在 _reload）期间被调用时，置 _stop_requested 取消标志——
        _run_loop 在重建收尾后据此退出，_reload 的恢复路径也不得将
        运行标志复活（否则引擎在 stop() 后继续运行）。
        """
        self._stop_requested.set()
        if not self._running.is_set():
            # 读档重建中（_reload 已清运行标志）：join 等待重建收尾，
            # 循环检测到取消标志后自行退出
            if self._thread and self._thread.is_alive():
                self._thread.join(timeout=13.0)
                self._thread = None
                self._cleanup()
            return
        self._running.clear()
        if self._thread and threading.current_thread() is not self._thread:
            self._thread.join(timeout=3.0)
            if self._thread.is_alive():
                # tick 线程可能卡在耗时 handler（如同步生成 chunk）。
                # 再等一轮，仍未退出则记录并继续清理——_tick 使用局部
                # 快照 + dispatcher 异常兜底，清理期竞态只会产生可忽略
                # 的错误响应，不会破坏持久化数据。
                logger.warning("tick 线程 3s 内未退出，延长等待 10s")
                self._thread.join(timeout=10.0)
                if self._thread.is_alive():
                    logger.error("tick 线程仍未退出，强制继续资源清理")
        self._thread = None
        self._cleanup()

    def _cleanup_world(self) -> None:
        """释放世界观子系统（读档重建与退出共用）；网络层保留。

        停服是世界外操作：不发 entity_died（那会向因果历史写入虚假
        死亡），直接释放内存；实体状态持久化是存档系统的职责。

        网络层（服务器/分发器/事件桥）不在此释放：读档重建时客户端
        保持连接，世界就绪后经 world_initialized 事件恢复。
        """
        world_tree.await_async()
        world_tree.reset()  # 读档重建：清旧世界事件/索引/因果图，保留订阅
        self._save_state_now()
        self._unregister_world_handlers()
        if self.calendar:
            self.calendar.shutdown()
            self.calendar = None
        if self.weather_engine:
            self.weather_engine.shutdown()
            self.weather_engine = None
        self.player_service = None
        self.entity_manager = None
        self.tile_generator = None
        # 注：显式 is not None —— ChunkStore 定义 __len__，空缓存时
        # bool(store) 为 False，真值判断会静默跳过 close（回归测试暴露）
        if self.chunk_store is not None:
            self.chunk_store.close()
            self.chunk_store = None
        if self.world_gen:
            self.world_gen = None
        if self._executor:
            self._executor = None
        self._load_state = None
        logger.info("世界观已清理（网络层保留）")

    def _cleanup(self) -> None:
        """完全停止：世界观 + 网络层。"""
        self._cleanup_world()
        if self.event_bridge:
            self.event_bridge.uninstall()
            self.event_bridge = None
        if self.server:
            self.server.stop()
            self.server = None
        self.dispatcher = None
        logger.info("游戏引擎已停止")

    def _on_chunk_evicted(self, cx: int, cy: int) -> None:
        """ChunkStore LRU 淘汰时注销天气数据。"""
        if self.weather_engine:
            self.weather_engine.unregister_chunk(cx, cy)

    # ── 出生点与初始区块 ──────────────────────────────────

    @staticmethod
    def _select_birth_point(continent, seed: int = 0) -> tuple[int, int]:
        """从海岸 chunk 中随机选取出生点。

        以 chunk 为单位遍历，判断 chunk 中心格（land_mask 格
        (cx*2+1, cy*2+1)，因 cell=100m、chunk=200m）是否为海岸陆地：
          - 是陆地（land_mask）
          - 不在河流/湖泊上（river_width==0）
          - 至少一个 8 邻居是海洋（elevation<0）
        优先海拔 0-50m 的海岸低地（沙滩/草地带，海陆地形多样）。

        以 chunk 中心而非任意像素判定，保证出生 chunk 主体是陆地
        而非像素碰巧落在海岸但 chunk 整体在深海。

        Args:
            continent: ContinentData。
            seed: 世界种子（仅用于无陆地时的错误诊断信息）。

        Returns:
            (chunk_x, chunk_y) 出生 chunk 坐标。

        Raises:
            RuntimeError: 大陆无任何陆地 chunk 时。
        """
        w, h = continent.grid_width, continent.grid_height
        elev = continent.elevation_field
        river_w = continent.river_width
        has_river = bool(river_w)

        ideal: list[tuple[int, int]] = []
        any_coast: list[tuple[int, int]] = []
        for cy in range(h // 2):
            for cx in range(w // 2):
                gx = cx * 2 + 1
                gy = cy * 2 + 1
                gi = gy * w + gx
                if not continent.land_mask[gi]:
                    continue
                if has_river and river_w[gi] > 0:
                    continue
                # 检测 8 邻居是否有海洋
                is_coast = False
                for d in range(8):
                    nx, ny = gx + _NDX[d], gy + _NDY[d]
                    if 0 <= nx < w and 0 <= ny < h:
                        if elev[ny * w + nx] < 0:
                            is_coast = True
                            break
                if not is_coast:
                    continue
                any_coast.append((cx, cy))
                if BIRTH_ELEV_MIN < elev[gi] < BIRTH_ELEV_MAX:
                    ideal.append((cx, cy))
        pool = ideal or any_coast
        if not pool:
            # 兜底：取任意陆地 chunk 中心（不要求海岸）
            for cy in range(h // 2):
                for cx in range(w // 2):
                    gi = (cy * 2 + 1) * w + (cx * 2 + 1)
                    if continent.land_mask[gi]:
                        pool.append((cx, cy))
                    if pool:
                        break
                if pool:
                    break
        if not pool:
            raise RuntimeError(f"seed={seed}: 大陆无陆地 chunk，无法选取出生点")
        return pool[random.randrange(len(pool))]

    def _generate_initial_chunks(self, continent) -> None:
        """预生成出生点周边 INITIAL_CHUNK_RADIUS 范围的详细 tile 层。

        层1 ChunkData 由 WorldGenerator 并行生成（群系/气候，确定性可再生）。
        层2 TileGrid 优先从存档恢复（tile 不可再生：含玩家修改），
        未持久化的才由 TileGenerator 生成——与 map_handler 懒加载语义一致。

        Args:
            continent: ContinentData（已由 ensure_continent 生成）。
        """
        from concurrent.futures import ThreadPoolExecutor, as_completed

        bcx, bcy = self.birth_chunk
        r = INITIAL_CHUNK_RADIUS
        coords = [
            (bcx + dx, bcy + dy)
            for dy in range(-r, r + 1)
            for dx in range(-r, r + 1)
        ]

        # 并行生成层1 ChunkData（WorldGenerator 线程安全）
        chunks = self.world_gen.generate_parallel(coords, max_workers=4)

        # 层2 TileGrid：存档优先，未持久化才生成（每个 chunk 独立，无需加锁）
        def _build_tiles(chunk):
            saved_grid = self.chunk_store.load_tiles(chunk.cx, chunk.cy)
            if saved_grid is not None:
                chunk.generate_tiles(saved_grid)
            else:
                grid = self.tile_generator.generate_chunk_for(chunk)
                chunk.generate_tiles(grid)
            return chunk

        with ThreadPoolExecutor(max_workers=4) as pool:
            futures = {
                pool.submit(_build_tiles, chunk): (chunk.cx, chunk.cy)
                for chunk in chunks
            }
            for future in as_completed(futures):
                chunk = future.result()
                self.chunk_store.put(chunk)

    def _publish_world_initialized(self) -> None:
        """发布 world_initialized 事件，通知各模块世界已就绪。

        时钟此时停在 epoch（尚未推进），事件携带 seed、出生点、
        已加载区块数。订阅者可据此初始化群体/生态等。
        """
        bc = self.birth_chunk or (0, 0)
        world_tree.publish(Event(
            timestamp=self.clock.time,
            location=(bc[0], bc[1], None, None),
            initiator_type="system",
            initiator_id="game_engine",
            affected=[AffectedParty("world", "subject")],
            event_type="world_initialized",
            weight=5,
            data={
                "world_id": self.world_id or "",
                "seed": self.seed,
                "birth_chunk": list(bc),
                "loaded_chunks": len(self.chunk_store),
            },
        ))

    # ── 存档（实时保存 / 读档重建） ──────────────────────

    def _register_world_handlers(self) -> None:
        """注册绑定世界观子系统的处理程序（replace：读档重建时覆盖旧闭包）。

        世界观处理程序闭包引用本次 start() 构建的子系统实例；
        世界重建后必须整体替换，否则旧闭包会访问已销毁的对象。
        """
        handlers: dict = {}
        handlers.update(make_map_handlers(
            self.world_gen, tile_gen=self.tile_generator,
            chunk_store=self.chunk_store,
            weather_engine=self.weather_engine,
        ))
        handlers.update(make_weather_handler(self.weather_engine, self.i18n))
        handlers.update(make_player_handler(self.player_service))
        handlers.update(make_entity_handlers(self.entity_manager))
        handlers.update(make_terminal_handler(self._executor))
        # 占位 handler：尚未实现的功能返回空成功响应
        # （需携带 request_type，与真实 handler 的响应约定一致）
        def _placeholder_ok(msg: dict) -> dict:
            return make_response(msg.get("request_type", ""), {})
        handlers["open_menu"] = _placeholder_ok
        handlers["player_interact"] = _placeholder_ok
        self._world_request_types = set(handlers)
        for req_type, handler in handlers.items():
            self.dispatcher.replace(req_type, handler)
        logger.info("世界观处理程序已注册: %s", sorted(handlers))

    def _unregister_world_handlers(self) -> None:
        """注销世界观处理程序（世界卸载后旧闭包指向已销毁子系统）。"""
        if not self.dispatcher:
            return
        for req_type in self._world_request_types:
            self.dispatcher.unregister(req_type)
        self._world_request_types.clear()

    def _broadcast_world_reloading(self, world_id, snapshot) -> None:
        """广播世界重建提示（前端显示加载提示）。

        直接经服务器广播而非 world_tree 事件：世界重建是世界外元操作，
        不产生历史、不进因果图、不入归档。
        """
        if self.server:
            self.server.broadcast({
                "type": "event",
                "event_type": "world_reloading",
                "payload": {"data": {
                    "world_id": world_id,
                    "snapshot": snapshot,
                }},
            })

    def _broadcast_world_reloading_failed(self, world_id, snapshot) -> None:
        """广播读档重建失败（前端结束加载提示并展示错误）。

        与 _broadcast_world_reloading 同为世界外元操作，直接经服务器广播。
        """
        if self.server:
            self.server.broadcast({
                "type": "event",
                "event_type": "world_reloading_failed",
                "payload": {"data": {
                    "world_id": world_id,
                    "snapshot": snapshot,
                }},
            })

    def _broadcast_world_progress(self, stage: str) -> None:
        """广播世界生成阶段进度（前端进度条文案）。

        世界外元操作，直接经服务器广播；大陆生成每个阶段开始时调用。
        """
        if self.server:
            self.server.broadcast({
                "type": "event",
                "event_type": "world_progress",
                "payload": {"data": {"stage": stage}},
            })

    def _persist_manifest(self) -> None:
        """回写 manifest（出生点/游玩信息），存档选择页数据源。"""
        if not self.world_id or not self.save_manager or not self._manifest:
            return
        manifest = self._manifest
        if self.birth_chunk:
            manifest.birth_chunk = self.birth_chunk
        manifest.touch(
            self.save_manager.manifest_path(self.world_id),
            game_time=self.clock.time,
            play_duration_sec=self._play_duration(),
        )

    def _play_duration(self) -> float:
        """累计游玩时长（真实秒）。"""
        base = self._manifest.play_duration_sec if self._manifest else 0.0
        if self._world_start_monotonic:
            base += _real_time.monotonic() - self._world_start_monotonic
        return base

    def _save_state_now(self) -> None:
        """立即将当前状态落盘（周期保存/退出保存共用）。

        世界外元操作：不产生历史、不进因果图。
        """
        if not self.world_id or not self.save_manager:
            return
        if self.player_service is None or self.clock is None:
            return
        state = collect_state(
            self.clock, self.player_service, self.weather_engine,
            world_tree.archived_max_timestamp(),
        )
        self.save_manager.write_state(self.world_id, state)
        self._persist_manifest()

    def _maybe_save_state(self) -> None:
        """周期实时保存：state 每 5s、dirty chunk 每 30s（MC 同款节奏）。

        单次失败不中断游戏循环，记录后下个周期重试。
        """
        now = _real_time.monotonic()
        if now - self._last_state_save >= SAVE_STATE_INTERVAL:
            self._last_state_save = now
            try:
                self._save_state_now()
            except Exception:
                logger.exception("周期状态保存失败")
        if self.chunk_store is not None and now - self._last_chunk_flush >= SAVE_CHUNK_FLUSH_INTERVAL:
            self._last_chunk_flush = now
            try:
                self.chunk_store.flush_dirty()
            except Exception:
                logger.exception("dirty chunk 定时保存失败")

    def snapshot_current(self, world_id: str | None = None, suffix: str = "manual") -> str:
        """创建一致性快照：flush 全部缓存 → 两库 WAL checkpoint → 打包。

        必须经此入口而非直接调 save_manager.create_snapshot——
        WAL 模式下直接拷贝 .db 文件会丢失未 checkpoint 的数据
        （实测快照回滚后 chunk/事件表完全缺失）。

        Args:
            world_id: 目标存档位；None = 当前已加载世界。
                目标非当前加载世界（含服务模式）时其 DB 未打开，
                直接打包活目录即为一致快照；当前世界才需
                flush + checkpoint（其 WAL 可能含未写回数据）。
            suffix: 快照来源标识（manual/auto）。

        Returns:
            快照文件名（不含目录）。

        Raises:
            ValueError: 当前无存档位。
            SaveFormatError: 目标存档不存在。
        """
        if world_id is None:
            world_id = self.world_id
        if not world_id or not self.save_manager:
            raise ValueError("当前无存档位，无法创建快照")
        if world_id == self.world_id:
            # 当前加载的世界：DB 打开中，须先提交缓存并 checkpoint，
            # 否则打包的 .db 缺 WAL 内数据（注：is not None——
            # ChunkStore 定义 __len__，空缓存时 bool 为 False）
            if self.chunk_store is not None:
                self.chunk_store.flush()
                self.chunk_store.checkpoint()
            world_tree.checkpoint_archive()
            # 血缘 game_time：当前世界用引擎时钟（比周期 state 落盘更新）；
            # 非当前世界由 create_snapshot 读其活目录状态兜底
            game_time = self.clock.time if self.clock else None
        else:
            game_time = None
        return self.save_manager.create_snapshot(
            world_id, suffix=suffix, game_time=game_time,
        )

    def _reload(self, world_id: str | None = None, snapshot: str | None = None) -> None:
        """读档重建：清理旧世界并按目标重建（网络层常驻，客户端不断线）。

        运行在 tick 线程内部（由 save_load 请求触发）：
          1. 广播 world_reloading（前端复位世界状态并显示加载提示）
          2. 回滚时先最终保存 + 一致性快照保护当前分支（DB 仍打开，
             snapshot_current 负责 flush + checkpoint）
          3. 清理世界观（服务器/分发器/事件桥保留）
          4. 快照展开为活目录（目标 world_id 覆盖由调用方传入）
          5. 按目标重建世界（world_initialized 事件 = 就绪信号）

        Args:
            world_id: 目标存档位（快照回滚时可指定覆盖目标）；
                      None 时从快照决定。
            snapshot: 快照文件路径（回滚）；None 时加载活目录。
        """
        logger.info("读档重建: world=%s snapshot=%s", world_id, snapshot)
        self._reloading = True
        self._running.clear()
        previous_world = self.world_id
        cleaned = False
        try:
            self._broadcast_world_reloading(world_id, snapshot)
            if snapshot is not None and self.world_id:
                # 回滚保护：先把当前（已最终保存的）活目录快照起来，
                # 回滚后仍可从该自动快照找回回滚前的分支
                self._save_state_now()
                self.snapshot_current(suffix="auto")
            self._cleanup_world()
            cleaned = True
            if snapshot is not None:
                world_id = self.save_manager.extract_snapshot(
                    snapshot, world_id=world_id,
                )
            self.start(world_id=world_id)
        except Exception:
            logger.exception("读档重建失败")
            if not cleaned:
                self._cleanup_world()
            recovered = False
            try:
                if previous_world is not None:
                    self.start(world_id=previous_world)
                    recovered = True
            except Exception:
                logger.exception("恢复旧世界失败，转入服务模式")
            if not recovered:
                # 兜底：回到服务模式（无世界）——网络层仍在线，
                # 存档管理可用，前端可重新发起读档
                self._cleanup_world()
                self.world_id = None
                self._manifest = None
                self._service_mode = True
                if not self._stop_requested.is_set():
                    # stop() 期间不复活运行标志：循环随后退出
                    self._running.set()
            raise
        finally:
            self._reloading = False

    # ── 内部 ──────────────────────────────────────────

    def _run_loop(self) -> None:
        """Tick 循环（常驻后台线程，跨世界重建存活）。

        异常防护：
          - 单次 _tick 异常不中断循环，但异常路径也会 sleep，
            避免紧循环占满 CPU 刷日志；
          - 连续异常达到 _MAX_CONSECUTIVE_ERRORS 次触发熔断，
            自动清除运行标志退出循环（资源清理仍由 stop() 负责）。

        读档：_pending_load 置位时在本线程内执行 _reload（世界重建
        期间网络层常驻、客户端不断线），完成后循环继续。
        """
        consecutive_errors = 0
        while self._running.is_set() and not self._stop_requested.is_set():
            tick_start = _real_time.monotonic()
            try:
                self._tick()
                consecutive_errors = 0
            except Exception:
                consecutive_errors += 1
                logger.exception(
                    "tick 循环异常（连续第 %d 次），引擎可能处于不一致状态",
                    consecutive_errors,
                )
                if consecutive_errors >= self._MAX_CONSECUTIVE_ERRORS:
                    logger.critical(
                        "tick 连续异常达 %d 次，熔断退出 tick 循环",
                        consecutive_errors,
                    )
                    self._running.clear()
                    return
            elapsed = _real_time.monotonic() - tick_start
            sleep_time = TICK_DT - elapsed
            if sleep_time > 0:
                _real_time.sleep(sleep_time)
            if self._pending_load:
                pending = self._pending_load
                self._pending_load = None
                try:
                    self._reload(*pending)
                except Exception:
                    # 日志已在 _reload 内单点记录（含恢复路径上下文）；
                    # 此处只广播失败事件，避免双份重复日志
                    self._broadcast_world_reloading_failed(*pending)

    def _tick(self) -> None:
        """单个 tick：推进时钟 + 处理所有排队消息。

        属性先抓局部快照再使用，避免 stop() 在其他线程将属性
        置 None 时出现 check-then-use 竞态。
        """
        clock = self.clock
        executor = self._executor
        dispatcher = self.dispatcher
        if not self._service_mode and clock:
            clock.tick()
            if executor is not None:
                executor.add_active_time(TICK_DT)
        if dispatcher:
            dispatcher.process()
        self._maybe_save_state()
