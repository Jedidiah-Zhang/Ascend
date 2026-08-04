"""游戏引擎 — 串联 WorldGenerator、GameServer、EventBridge 和 MessageDispatcher。

在后台线程中运行 tick 循环，以固定频率处理传入的客户端消息。

启动流程:
  1. 随机 seed（seed=0 时自动随机）
  2. 主动生成大陆宏观场（侵蚀+水文，约 30s）
  3. 随机选取出生点（海岸低地，避开河流/湖泊，海陆地形多样）
  4. 预生成出生点周边 radius 个 chunk 的详细 tile 层
  5. 创建实体管理器接入事件管线
  6. 配置世界树归档 + 启动 tick 循环（时钟+日历随之运转）
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
    TILE_MAP_SIZE,
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

    def __init__(self, seed: int = 0) -> None:
        """初始化引擎。

        Args:
            seed: 世界种子。0 表示启动时自动随机。
        """
        self.seed: int = seed
        self.world_gen: WorldGenerator | None = None
        self.server: GameServer | None = None
        self.dispatcher: MessageDispatcher | None = None
        self.clock: WorldClock = WorldClock()
        self.calendar: GameCalendar | None = GameCalendar(clock=self.clock)  # shutdown 后为 None
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
        self._running: threading.Event = threading.Event()
        self._thread: threading.Thread | None = None

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

        幂等：已在运行时调用无效果。
        """
        if self._running.is_set():
            return
        self.save_manager = SaveManager(SAVE_ROOT)
        self.server = GameServer(host=SERVER_HOST, port=SERVER_PORT)
        self.server.start()
        self.dispatcher = MessageDispatcher(self.server)
        save_handlers = make_save_handlers(self.save_manager, self)
        for req_type, handler in save_handlers.items():
            self.dispatcher.register(req_type, handler)
        logger.info(
            "服务模式已启动: %s:%d（世界将在读档时生成）",
            SERVER_HOST, SERVER_PORT,
        )
        self._service_mode = True
        self._running.set()
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
        self.save_manager = SaveManager(SAVE_ROOT)
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

        # 1. 随机 seed（seed=0 时自动随机；读档时回写 manifest 保持一致性）
        if self.seed == 0:
            self.seed = random.randint(1, 2**31 - 1)
            if self._manifest is not None:
                self._manifest.seed = self.seed
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
        continent = self.world_gen.ensure_continent()
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

        # 4. 预生成出生点周边区块
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

        # 6. TCP 服务器
        self.server = GameServer(host=SERVER_HOST, port=SERVER_PORT)
        self.server.start()

        # 6b. 事件桥接器 — 将 WorldTree 事件转发给 Godot 前端
        self.event_bridge = EventBridge(world_tree, self.server)
        self.event_bridge.install()
        logger.info("事件桥接器已安装")

        # 7. 消息分发器
        self.dispatcher = MessageDispatcher(self.server)
        handlers = make_map_handlers(
            self.world_gen, tile_gen=self.tile_generator,
            birth_chunk=self.birth_chunk, chunk_store=self.chunk_store,
            weather_engine=self.weather_engine,
        )
        for req_type, handler in handlers.items():
            self.dispatcher.register(req_type, handler)
        logger.info("已注册地图处理程序: %s", list(handlers.keys()))

        # 7b. 天气查询处理程序
        weather_handlers = make_weather_handler(self.weather_engine, self.i18n)
        for req_type, handler in weather_handlers.items():
            self.dispatcher.register(req_type, handler)
        logger.info("已注册天气查询处理程序: %s", list(weather_handlers.keys()))

        # 7c. 玩家状态处理程序
        player_handlers = make_player_handler(self.player_service)
        for req_type, handler in player_handlers.items():
            self.dispatcher.register(req_type, handler)
        logger.info("已注册玩家处理程序: %s", list(player_handlers.keys()))

        # 7d. 实体快照处理程序（状态通道：前端接入时初始化实体视图）
        entity_handlers = make_entity_handlers(self.entity_manager)
        for req_type, handler in entity_handlers.items():
            self.dispatcher.register(req_type, handler)
        logger.info("已注册实体处理程序: %s", list(entity_handlers.keys()))

        # 7e. 存档处理程序（状态通道：列表/快照/读档）
        save_handlers = make_save_handlers(self.save_manager, self)
        for req_type, handler in save_handlers.items():
            self.dispatcher.register(req_type, handler)
        logger.info("已注册存档处理程序: %s", list(save_handlers.keys()))

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
        term_handlers = make_terminal_handler(self._executor)
        for req_type, handler in term_handlers.items():
            self.dispatcher.register(req_type, handler)
        logger.info("已注册终端处理程序: %s", list(term_handlers.keys()))

        # 8b. 占位 handler：尚未实现的功能返回空成功响应
        # （需携带 request_type，与真实 handler 的响应约定一致）
        def _placeholder_ok(msg: dict) -> dict:
            return {
                "type": "response",
                "request_type": msg.get("request_type", ""),
                "payload": {},
            }

        self.dispatcher.register("open_menu", _placeholder_ok)
        self.dispatcher.register("player_interact", _placeholder_ok)

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

        # 11. 启动 tick 循环——clock.tick() 推进时间，calendar 自动收事件
        self._last_state_save = _real_time.monotonic()
        self._last_chunk_flush = self._last_state_save
        self._world_start_monotonic = self._last_state_save
        self._running.set()
        self._thread = threading.Thread(
            target=self._run_loop, name="game-engine", daemon=True
        )
        self._thread.start()
        logger.info("游戏引擎在后台运行 (tick=%.1f Hz)", TICK_RATE)

    def stop(self) -> None:
        """停止引擎并清理所有子系统。

        退出前执行最终保存（flush + 最终 state 落盘），
        等价 MC 关服保存——实时存档保证此步幂等、开销小。

        幂等：已停止时调用无效果。
        """
        if not self._running.is_set():
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

    def _cleanup(self) -> None:
        """释放所有子系统资源（stop 与读档重建共用）。

        停服是世界外操作：不发 entity_died（那会向因果历史写入虚假
        死亡），直接释放内存；实体状态持久化是存档系统的职责。
        """
        if hasattr(self, 'event_bridge') and self.event_bridge:
            self.event_bridge.uninstall()
            self.event_bridge = None
        world_tree.await_async()
        if self.server:
            self.server.stop()
            self.server = None
        self._save_state_now()
        if self.calendar:
            self.calendar.shutdown()
            self.calendar = None
        if self.weather_engine:
            self.weather_engine.shutdown()
            self.weather_engine = None
        self.player_service = None
        self.entity_manager = None
        self.tile_generator = None
        if self.chunk_store:
            self.chunk_store.close()
            self.chunk_store = None
        if self.world_gen:
            self.world_gen = None
        if self._executor:
            self._executor = None
        self._load_state = None
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
                "seed": self.seed,
                "birth_chunk": list(bc),
                "loaded_chunks": len(self.chunk_store),
            },
        ))

    # ── 存档（实时保存 / 读档重建） ──────────────────────

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
        if self.chunk_store and now - self._last_chunk_flush >= SAVE_CHUNK_FLUSH_INTERVAL:
            self._last_chunk_flush = now
            try:
                self.chunk_store.flush_dirty()
            except Exception:
                logger.exception("dirty chunk 定时保存失败")

    def snapshot_current(self, suffix: str = "manual") -> str:
        """创建一致性快照：flush 全部缓存 → 两库 WAL checkpoint → 打包。

        必须经此入口而非直接调 save_manager.create_snapshot——
        WAL 模式下直接拷贝 .db 文件会丢失未 checkpoint 的数据
        （实测快照回滚后 chunk/事件表完全缺失）。

        Args:
            suffix: 快照来源标识（manual/auto）。

        Returns:
            快照文件名（不含目录）。
        """
        if not self.world_id or not self.save_manager:
            raise ValueError("当前无存档位，无法创建快照")
        if self.chunk_store:
            self.chunk_store.flush()
            self.chunk_store.checkpoint()
        world_tree.checkpoint_archive()
        return self.save_manager.create_snapshot(self.world_id, suffix=suffix)

    def _reload(self, world_id: str | None = None, snapshot: str | None = None) -> None:
        """读档重建：清理当前世界并按目标重启。

        运行在 tick 线程内部（由 save_load 请求触发）：
          1. 回滚时先最终保存 + 一致性快照保护当前分支（DB 仍打开，
             snapshot_current 负责 flush + checkpoint）
          2. 清理旧世界（含最终保存）
          3. 快照展开为活目录（目标 world_id 覆盖由调用方传入）
          4. 按目标重建世界

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
            if snapshot is not None and self.world_id:
                # 回滚保护：先把当前（已最终保存的）活目录快照起来，
                # 回滚后仍可从该自动快照找回回滚前的分支
                self._save_state_now()
                self.snapshot_current(suffix="auto")
            self._cleanup()
            cleaned = True
            if snapshot is not None:
                world_id = self.save_manager.extract_snapshot(
                    snapshot, world_id=world_id,
                )
            self.start(world_id=world_id)
        except Exception:
            logger.exception("读档重建失败")
            if not cleaned:
                self._cleanup()
            try:
                # 兜底：尝试回到重建前的世界，避免后端整体死亡
                self.start(world_id=previous_world)
            except Exception:
                logger.critical("读档失败后无法恢复旧世界，引擎已停止")
            raise
        finally:
            self._reloading = False

    # ── 内部 ──────────────────────────────────────────

    def _run_loop(self) -> None:
        """Tick 循环（运行在后台线程）。

        异常防护：
          - 单次 _tick 异常不中断循环，但异常路径也会 sleep，
            避免紧循环占满 CPU 刷日志；
          - 连续异常达到 _MAX_CONSECUTIVE_ERRORS 次触发熔断，
            自动清除运行标志退出循环（资源清理仍由 stop() 负责）。

        读档：_pending_load 置位时退出循环并执行 _reload
        （仍在 tick 线程内，可安全调用 _cleanup/start）。
        """
        consecutive_errors = 0
        while self._running.is_set():
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
                break

        pending = self._pending_load
        if pending:
            self._pending_load = None
            try:
                self._reload(*pending)
            except Exception:
                logger.exception("读档重建失败")

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
