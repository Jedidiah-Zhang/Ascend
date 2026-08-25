"""游戏引擎 — 串联 WorldGenerator、GameServer、EventBridge 和 MessageDispatcher。

在后台线程中运行 tick 循环，以固定频率处理传入的客户端消息。

进程模型（一个进程 = 一种模式，进程内不换世界）:
  - 菜单进程（run_server 无参 → start_service）: 仅网络层 + 存档管理，
    主菜单无需等待大陆生成（5-30s+）；
  - 世界进程（run_server --world-id <id> → start）: 直接构建世界观并
    运行游戏；回滚（--snapshot）由进程启动时先保护活目录分支再展开。
  进入世界 = 前端停菜单进程、以世界参数拉起新进程（网络层跨进程重建，
  前端经握手 + world_initialized 事件感知就绪）。

启动流程（世界进程）:
  1. 网络层先就绪（端口立即开放，大陆生成期间前端可连接并收进度）
  2. 主动生成大陆宏观场（侵蚀+水文，约 30s；缓存命中秒级）
  3. 随机选取出生点（海岸低地，避开河流/湖泊，海陆地形多样）
  4. 预生成出生点周边 radius 个 chunk 的详细 tile 层
  5. 创建实体管理器接入事件管线
  6. 配置世界树归档 + 启动 tick 循环（时钟+日历随之运转）
"""

import os
import queue
import random
import threading
import time as _real_time

from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import ClassVar

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
    SAVE_PULSE_INTERVAL,
    TILE_WORKERS,
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
from ascend.net.handlers.preview_handler import make_preview_handlers
from ascend.space import WorldGenerator, TileGenerator
from ascend.space.generator import compute_gen_fingerprint
from ascend.space.chunk_store import ChunkStore
from ascend.space.chunk_services import (
    ChunkServiceRegistry,
    WeatherChunkService,
    TileStateChunkService,
)
from ascend.entity import EntityManager, PlayerService
from ascend.weather import WeatherEngine
from ascend.terminal import CommandExecutor
from ascend.time import WorldClock, GameCalendar
from ascend.i18n import I18n, get_default
from ascend.lifecycle import LifecycleStack
from ascend.world_tree import world_tree, Event, AffectedParty, WorldEvent
from ascend.fate import derive
from ascend.save import (
    SaveManager, collect_state, aligned_time, apply_clock, apply_player,
)
from ascend.save.manifest import SEED_MAX, seed_to_hex

logger = get_logger(__name__)

# 8 邻域偏移（用于海岸像素检测）
_NDX = (1, -1, 0, 0, 1, -1, 1, -1)
_NDY = (0, 0, 1, -1, 1, 1, -1, -1)


@dataclass
class WorldInitialized(WorldEvent):
    """地图生成完毕、出生点确定、周边区块就绪后发布。

    seed 为协议层 hex 字符串（Godot JSON 仅 int64，256-bit 直传丢失）。
    """

    event_type: ClassVar[str] = "world_initialized"
    seed: str
    birth_chunk: list
    loaded_chunks: int
    world_id: str = ""


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
        self.i18n: I18n = get_default()  # 进程共享实例：set_lang 全局生效（枚举 label 亦跟随）
        self._executor: CommandExecutor | None = None
        self.entity_manager: EntityManager | None = None
        self.player_service: PlayerService | None = None
        self.weather_engine: WeatherEngine | None = None
        self.tile_generator: TileGenerator | None = None
        self.birth_chunk: tuple[int, int] | None = None
        self.chunk_store: ChunkStore | None = None
        self.chunk_services: ChunkServiceRegistry | None = None
        # 存档
        self.save_manager: SaveManager | None = None
        self.world_id: str | None = None      # 当前存档位（None=无存档模式）
        self._manifest = None                 # 内存中的 Manifest（touch 用）
        self._load_state: dict | None = None  # 读档恢复的状态
        self._regen_continent: bool = False   # 强制重建大陆（--regen-continent）
        self._last_pulse: float = 0.0         # 上次保存脉搏时刻（monotonic）
        self._save_queue: queue.Queue = queue.Queue(maxsize=1)  # 单槽位防堆积
        self._save_thread: threading.Thread | None = None
        self._world_start_monotonic: float = 0.0
        self._service_mode: bool = False      # 服务模式：仅网络+存档，无世界
        self._running: threading.Event = threading.Event()
        self._thread: threading.Thread | None = None
        # 装配/拆卸生命周期栈：装配时登记逆操作，拆卸时按逆序回滚
        # （世界层与网络层分开，见 start/_ensure_network/_cleanup_*）
        self._world_stack: LifecycleStack = LifecycleStack()
        self._net_stack: LifecycleStack = LifecycleStack()

    def _unset(
        self, attr: str, teardown: Callable[[], None] | None = None,
    ) -> Callable[[], None]:
        """构造逆操作：先执行 teardown（若有）再清除属性引用。

        供生命周期栈 push 使用——装配时登记，拆卸逆序执行时
        先释放资源再清引用（避免 teardown 后属性仍指向已关闭实例）。

        Args:
            attr: 引擎属性名（装配时已赋值，拆卸时置 None）。
            teardown: 可选资源释放回调（如 calendar.shutdown）。

        Returns:
            无参逆操作闭包。
        """
        def _invoke() -> None:
            if teardown is not None:
                teardown()
            setattr(self, attr, None)
        return _invoke

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
        save_export；世界由前端以 --world-id 拉起世界进程时生成，
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
            "服务模式已启动: %s:%d（世界进程由前端以 --world-id 拉起）",
            SERVER_HOST, SERVER_PORT,
        )

    def ensure_network(self) -> None:
        """确保网络层就绪（幂等；run_server 入口用）。

        世界进程在 load_world（大陆生成 5-30s）前调用：端口与 token
        文件立即就绪，前端可马上连接完成握手。
        """
        self._ensure_network()

    def _ensure_network(self) -> None:
        """确保网络层就绪（服务器/分发器/事件桥/存档处理程序），幂等。

        菜单进程与世界进程共用：存档处理程序只注册一次；世界进程的
        世界观处理程序由 start() 经 _register_world_handlers 追加注册
        （request_type 无交集，进程内不换世界）。
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
        for req_type, handler in make_preview_handlers().items():
            self.dispatcher.register(req_type, handler)
        self.event_bridge = EventBridge(world_tree, self.server)
        self.event_bridge.install()
        # 登记网络层逆操作（逆序回滚：bridge → server → dispatcher）
        self._net_stack.push(self._unset("event_bridge", self.event_bridge.uninstall))
        self._net_stack.push(self._unset("server", self.server.stop))
        self._net_stack.push(self._unset("dispatcher"))
        logger.info("网络层已就绪: %s:%d", SERVER_HOST, SERVER_PORT)

    def _ensure_tick_thread(self) -> None:
        """确保 tick 循环线程存活（常驻线程）。"""
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
          1. 网络层先就绪（端口立即开放，大陆生成期间前端可连接收进度）
          2. 恢复时钟（对齐归档时间，防时间倒流）
          3. 按 manifest.seed 重建大陆宏观场
          4. 以存档目录路径打开 ChunkStore / 事件归档
          5. 静默恢复玩家实体（不发布 entity_born，Issue #20/#25 语义）

        幂等：已在运行时调用无效果。
        """
        if self._running.is_set():
            return
        self._service_mode = False

        # 0. 网络层先就绪（幂等）：世界生成 5-30s 期间端口已开放，
        #    前端可连接并收 world_progress 进度（进程模型下每次进入
        #    世界都是新进程，必须先开端口再生成）。
        self._ensure_network()

        # 0a. 存档准备
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
        self._world_stack.push(self._unset("calendar", self.calendar.shutdown))

        # 1. 种子（seed=0 仅在无存档模式启动时随机；存档世界在
        # create_world 时已定案——见 SaveManager.create_world）。
        if self.seed == 0 and self._manifest is None:
            self.seed = random.randint(1, SEED_MAX)
        logger.info("游戏引擎启动: seed=%d world=%s", self.seed, self.world_id)

        # 2. 世界生成器 + 主动生成大陆宏观场（侵蚀+水文，首次约 5-30s，
        #    之后从存档内 continent.bin 缓存恢复，秒级）
        continent_cache_path = (
            self.save_manager.continent_path(self.world_id)
            if self.world_id else None
        )
        land_ratio = None
        width_km = None
        height_km = None
        if self._manifest is not None and self._manifest.gen_params:
            land_ratio = self._manifest.gen_params.get("land_ratio")
            width_km = self._manifest.gen_params.get("width_km")
            height_km = self._manifest.gen_params.get("height_km")
        self.world_gen = WorldGenerator(
            seed=self.seed, continent_cache_path=continent_cache_path,
            land_ratio=land_ratio, width_km=width_km, height_km=height_km,
            ignore_cache=self._regen_continent,
        )
        self._world_stack.push(self._unset("world_gen"))
        continent = self.world_gen.ensure_continent(
            progress_cb=self._broadcast_world_progress,
        )
        self.tile_generator = TileGenerator(
            seed=self.seed, continent=continent,
        )
        self._world_stack.push(self._unset("tile_generator"))
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
        self._world_stack.push(self._unset("chunk_store", self.chunk_store.close))
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
        self._world_stack.push(self._unset("entity_manager"))

        # 5a. 权威玩家实体（读档静默恢复，不发布 entity_born）
        # 地图为有界矩形：chunk 坐标 ∈ [0, grid//2)，玩家坐标越界钳制
        self.player_service = PlayerService(
            self.entity_manager, self.clock, self.birth_chunk,
            max_chunk=(
                continent.grid_width // 2,
                continent.grid_height // 2,
            ),
        )
        self._world_stack.push(self._unset("player_service"))
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
        self._world_stack.push(
            self._unset("weather_engine", self.weather_engine.shutdown)
        )
        # 5c. 地形状态引擎（三通道驱动；chunk 接入统一走
        # chunk_services 注册器——新增引擎 = registry.add(service)，
        # 生命周期广播（register/on_tiles_ready/unregister）零改动）
        from ascend.space.tile_state import TileStateEngine
        self.tile_state_engine = TileStateEngine(
            self.clock, self.weather_engine, wt=world_tree,
        )
        self._world_stack.push(
            self._unset("tile_state_engine", self.tile_state_engine.shutdown)
        )
        self.chunk_services = ChunkServiceRegistry([
            WeatherChunkService(self.weather_engine),
            TileStateChunkService(self.tile_state_engine),
        ])
        for (cx, cy), chunk in self.chunk_store.items():
            self.chunk_services.register(chunk)
            # tile 已在第 4 步生成/恢复完毕——就绪即结算（时序契约）
            self.chunk_services.on_tiles_ready(cx, cy)
        logger.info("天气引擎已接入 %d 个 chunk", len(self.chunk_store))

        # 8. 终端指令执行器
        from ascend.terminal.executor import ExecutorConfig
        self._executor = CommandExecutor(
            self.clock, self.calendar, self.i18n,
            config=ExecutorConfig(
                weather_engine=self.weather_engine,
                default_chunk=self.birth_chunk,
                player_service=self.player_service,
                entity_manager=self.entity_manager,
                continent_path=continent_cache_path,
                gen_fingerprint_fn=compute_gen_fingerprint,
            ),
        )
        self._world_stack.push(self._unset("_executor"))

        # 8a. tile 生成线程池（服务世界观；随引擎停止回收）
        self._tile_pool = ThreadPoolExecutor(
            max_workers=TILE_WORKERS, thread_name_prefix="tile-gen"
        )
        self._world_stack.push(
            self._unset("_tile_pool", self._tile_pool.shutdown)
        )

        # 8b. 世界观处理程序（进程内只注册一次；save 处理程序已在
        # _ensure_network 注册，request_type 无交集）
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
        self._last_pulse = _real_time.monotonic()
        self._world_start_monotonic = self._last_pulse
        self._running.set()
        self._ensure_tick_thread()
        self._ensure_save_thread()
        logger.info("游戏引擎在后台运行 (tick=%.1f Hz)", TICK_RATE)

    def load_world(
        self, world_id: str | None = None, snapshot: str | None = None,
        regen_continent: bool = False,
    ) -> None:
        """世界进程启动入口：进入语义 → 展开快照 → 构建世界。

        由 run_server --world-id/--snapshot/--regen-continent 调用。回滚时活目录即目标世界
        的当前状态（上一进程退出时已最终保存）；进入语义（冻结离开
        记录 → 展开 → 手动档开启新当前记录）由 SaveManager.enter_snapshot
        统一保证——auto 节点是当前线的滚动记录，永无下游、永不重复新建。

        regen_continent=True 时无视大陆缓存强制重建（开发者/研究侧
        调参用；对存档世界有破坏性——玩家改动的 chunk 与新场可能
        出现接缝不一致）。

        若引擎已在运行（测试中模拟进程切换），先 stop() 清旧状态：
        语义 = "以该世界重启引擎"，与进程模型一致。

        Args:
            world_id: 目标存档位。
            snapshot: 快照文件（回滚）；None 时加载活目录。
            regen_continent: True 时无视大陆缓存强制重建。

        Raises:
            ValueError: 回滚未指定 world_id 或存档不存在。
        """
        if self._running.is_set():
            self.stop()
        self._regen_continent = regen_continent
        self._ensure_network()
        try:
            if snapshot is not None:
                if not world_id:
                    raise ValueError("回滚必须指定 world_id")
                self.save_manager.get_manifest(world_id)  # 校验目标存在性
                # 进入语义（冻结离开记录/展开/手动档开新当前记录）
                # 由 SaveManager.enter_snapshot 统一处理
                world_id = self.save_manager.enter_snapshot(
                    snapshot, world_id=world_id,
                )
            self.start(world_id=world_id)
        except Exception:
            # 构建失败：清理已创建的网络层（否则 _running 未置位，
            # stop() 幂等短路，server socket 泄漏阻塞端口重 bind）
            self._cleanup()
            raise

    def stop(self) -> None:
        """停止引擎并清理所有子系统。

        退出前执行最终保存（flush + 最终 state 落盘），
        等价于最后一次完整落盘——实时存档保证此步幂等、开销小。

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
        if self._save_thread is not None and self._save_thread.is_alive():
            # 保存线程在 _running 清除后最多 0.5s（心跳）退出；
            # 最终脉搏由 _cleanup_world 同步排空，此处只回收线程。
            # join 超时（脉搏 >3s，如慢盘）后 _final_pulse 与幸存
            # worker 并发执行：archive/chunk 有内部锁串行、state 原子
            # 写 last-wins，无数据破坏
            self._save_thread.join(timeout=3.0)
            if self._save_thread.is_alive():
                logger.warning("保存线程 3s 内未退出（最终脉搏由 _cleanup_world 排空）")
        self._save_thread = None
        self._cleanup()

    def _cleanup_world(self) -> None:
        """释放世界观子系统（退出共用）。

        停服是世界外操作：不发 entity_died（那会向因果历史写入虚假
        死亡），直接释放内存；实体状态持久化是存档系统的职责。

        先提交再回滚：await_async（等待异步回调）+ 最终保存（依赖
        各子系统存活）在前，随后按装配逆序执行世界层生命周期栈——
        "后创建的先销毁"由栈派生，无需手写清单。

        网络层（服务器/分发器/事件桥）在此保留，由 _cleanup 统一
        释放（读档重建时 stop() 的清理顺序复用本方法）。
        """
        world_tree.await_async()
        self._final_pulse()
        self._world_stack.teardown()
        self._load_state = None
        logger.info("世界观已清理（网络层保留）")

    def _cleanup(self) -> None:
        """完全停止：世界观 + 网络层。"""
        self._cleanup_world()
        self._net_stack.teardown()
        logger.info("游戏引擎已停止")

    def _on_chunk_evicted(self, cx: int, cy: int) -> None:
        """ChunkStore LRU 淘汰时注销全部 chunk 服务（注册器统一广播）。"""
        if self.chunk_services:
            self.chunk_services.unregister(cx, cy)

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
        # 确定性选取（命运织机）：同 seed 同大陆 → 同出生点。
        # 原全局 random.randrange 无种子，破坏同 seed 双跑复现
        # （CRN 前提，见 docs/世界框架/随机系统/设计.md）。
        return pool[derive(seed, "world", "birth_point") % len(pool)]

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
            saved = self.chunk_store.load_tiles_with_day(chunk.cx, chunk.cy)
            if saved is not None:
                saved_grid, settled_day = saved
                chunk.restore_tiles(saved_grid)
                chunk.settled_day = settled_day
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
            event_type=WorldInitialized.event_type,
            weight=5,
            data=WorldInitialized(
                world_id=self.world_id or "",
                seed=seed_to_hex(self.seed),
                birth_chunk=list(bc),
                loaded_chunks=len(self.chunk_store),
            ).as_dict(),
        ))

    # ── 存档（实时保存 / 读档重建） ──────────────────────

    def _register_world_handlers(self) -> None:
        """注册绑定世界观子系统的处理程序（进程内仅一次）。

        闭包引用本次 start() 构建的子系统实例；进程模型下每个世界
        进程只 start 一次，无需覆盖语义。
        """
        handlers: dict = {}
        handlers.update(make_map_handlers(
            self.world_gen, tile_gen=self.tile_generator,
            chunk_store=self.chunk_store,
            chunk_services=self.chunk_services,
            tile_pool=self._tile_pool,
        ))
        handlers.update(make_weather_handler(self.weather_engine, self.i18n))
        handlers.update(make_player_handler(self.player_service))
        handlers.update(make_entity_handlers(self.entity_manager))
        handlers.update(make_terminal_handler(self._executor))
        # 占位 handler：尚未实现的功能返回显式"未实现"标记而非空成功
        # 响应——前端可感知功能缺口并提示，不让缺口被系统性掩盖。
        def _not_implemented(msg: dict) -> dict:
            return make_response(
                msg.get("request_type", ""), {"implemented": False},
            )
        handlers["player_interact"] = _not_implemented
        for req_type, handler in handlers.items():
            self.dispatcher.register(req_type, handler)
        logger.info("世界观处理程序已注册: %s", sorted(handlers))

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

    def _maybe_save_pulse(self) -> None:
        """保存脉搏调度（tick 线程调用）：到点入队，零 I/O 阻塞。

        单槽位防堆积：上一脉搏在途时跳过本次（脉搏天然可合并，
        数据由在途或下一次脉搏落盘，滞后 ≤ 脉搏时长 + SAVE_PULSE_INTERVAL；
        退出时最终脉搏兜底）。
        """
        if self._save_thread is None:
            return
        now = _real_time.monotonic()
        if now - self._last_pulse < SAVE_PULSE_INTERVAL:
            return
        self._last_pulse = now
        try:
            self._save_queue.put_nowait(None)
        except queue.Full:
            pass  # 上一脉搏在途，本次合并

    def _ensure_save_thread(self) -> None:
        """确保保存脉搏线程存活（世界进程常驻线程）。"""
        if self._save_thread is not None and self._save_thread.is_alive():
            return
        self._save_thread = threading.Thread(
            target=self._save_worker, name="save-pulse", daemon=True
        )
        self._save_thread.start()

    def _save_worker(self) -> None:
        """保存线程主体：串行执行脉搏（退出时最多等 0.5s 心跳退出）。"""
        while self._running.is_set():
            try:
                self._save_queue.get(timeout=0.5)
            except queue.Empty:
                continue
            try:
                self._run_pulse()
            except Exception:
                logger.exception("保存脉搏执行失败")

    def _run_pulse(self) -> None:
        """单个保存脉搏：事件 flush → state 写入 → chunk flush。

        顺序保证 state 的 archive_max_timestamp 新鲜（事件先落盘）；
        任一步失败不阻断其余步骤，下一次脉搏重试（事件丢失 ≤1 窗口
        由 archive_pending 语义兜底）。
        """
        try:
            world_tree.archive_pending()
        except Exception:
            logger.exception("保存脉搏: 事件 flush 失败")
        try:
            self._save_state_now()
        except Exception:
            logger.exception("保存脉搏: state 写入失败")
        if self.chunk_store is not None:
            try:
                self.chunk_store.flush()
            except Exception:
                logger.exception("保存脉搏: chunk flush 失败")

    def _final_pulse(self) -> None:
        """排空：同步执行完整脉搏（退出/快照强一致点）。"""
        self._run_pulse()

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
            SaveFormatError: 目标存档不存在（save 模块异常）。
        """
        if world_id is None:
            world_id = self.world_id
        if not world_id or not self.save_manager:
            raise ValueError("当前无存档位，无法创建快照")
        if world_id == self.world_id:
            # 当前加载的世界：DB 打开中，先同步完整脉搏（事件 flush →
            # state 写入 → chunk flush）再 checkpoint，否则打包的 .db 缺
            # WAL 内数据、快照缺近期事件（注：is not None——ChunkStore
            # 定义 __len__，空缓存时 bool 为 False）
            self._final_pulse()
            if self.chunk_store is not None:
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

    # ── 内部 ──────────────────────────────────────────

    def _run_loop(self) -> None:
        """Tick 循环（后台线程，进程生命周期内常驻）。

        异常防护：
          - 单次 _tick 异常不中断循环，但异常路径也会 sleep，
            避免紧循环占满 CPU 刷日志；
          - 连续异常达到 _MAX_CONSECUTIVE_ERRORS 次触发熔断，
            自动清除运行标志退出循环（资源清理仍由 stop() 负责）。
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
        self._maybe_save_pulse()
