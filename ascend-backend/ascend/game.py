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

import random
import threading
import time as _real_time

from ascend.log import get_logger
from ascend.net import GameServer, MessageDispatcher
from ascend.net.handlers.map_handler import make_map_handlers
from ascend.net.handlers.terminal_handler import make_terminal_handler
from ascend.space import WorldGenerator, TileGenerator
from ascend.space.tile_grid import TILE_MAP_SIZE
from ascend.entity import EntityManager
from ascend.terminal import CommandExecutor
from ascend.time import WorldClock, GameCalendar, TICK_RATE
from ascend.i18n import I18n
from ascend.world_tree import world_tree, Event, AffectedParty

logger = get_logger(__name__)

TICK_DT: float = 1.0 / TICK_RATE
SERVER_HOST: str = "127.0.0.1"
SERVER_PORT: int = 9081

# 出生点周边预生成半径（chunk 数），2 → 5×5 共 25 个 chunk
INITIAL_CHUNK_RADIUS: int = 2
# 出生点理想海拔范围（m）——海岸低地，沙滩/草地带，海陆地形多样
BIRTH_ELEV_MIN: float = 0.0
BIRTH_ELEV_MAX: float = 50.0

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
        self.calendar: GameCalendar = GameCalendar()
        self.i18n: I18n = I18n()
        self._executor: CommandExecutor | None = None
        self.entity_manager: EntityManager | None = None
        self.tile_generator: TileGenerator | None = None
        self.birth_chunk: tuple[int, int] | None = None
        self.loaded_chunks: dict[tuple[int, int], object] = {}
        self._running: bool = False
        self._thread: threading.Thread | None = None

    def __repr__(self) -> str:
        """返回引擎状态摘要。

        Returns:
            含种子、运行状态、客户端数的 repr 字符串。
        """
        client_count = self.server.client_count if self.server else 0
        return (
            f"GameEngine(seed={self.seed}, "
            f"running={self._running}, "
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

    def start(self) -> None:
        """初始化所有子系统并在后台启动 tick 循环。

        流程:
          1. 随机 seed（seed=0 时）
          2. 主动生成大陆宏观场
          3. 随机选取出生点
          4. 预生成周边区块的详细 tile 层
          5. 创建实体管理器
          6. TCP 服务器 + 消息分发器
          7. 世界树归档配置
          8. 发布 world_initialized 事件
          9. 启动 tick 循环（时钟+日历随之运转）

        幂等：已在运行时调用无效果。
        """
        if self._running:
            return

        # 1. 随机 seed
        if self.seed == 0:
            self.seed = random.randint(1, 2**31 - 1)
        logger.info("游戏引擎启动: seed=%d", self.seed)

        # 2. 世界生成器 + 主动生成大陆宏观场（侵蚀+水文，耗时约 30s）
        self.world_gen = WorldGenerator(seed=self.seed)
        continent = self.world_gen.ensure_continent()
        self.tile_generator = TileGenerator(
            seed=self.seed, continent=continent,
        )
        logger.info("大陆生成完成: %s", continent)

        # 3. 随机出生点（陆地、海拔适中的温和低地）
        self.birth_chunk = self._select_birth_point(continent)
        logger.info("出生点: chunk %s", self.birth_chunk)

        # 4. 预生成出生点周边区块
        self._generate_initial_chunks(continent)
        logger.info(
            "已生成周边 %d 个区块 (radius=%d)",
            len(self.loaded_chunks), INITIAL_CHUNK_RADIUS,
        )

        # 5. 实体管理器（接入事件管线）
        self.entity_manager = EntityManager()

        # 6. TCP 服务器
        self.server = GameServer(host=SERVER_HOST, port=SERVER_PORT)
        self.server.start()

        # 7. 消息分发器
        self.dispatcher = MessageDispatcher(self.server)
        handlers = make_map_handlers(self.world_gen)
        for req_type, handler in handlers.items():
            self.dispatcher.register(req_type, handler)
        logger.info("已注册地图处理程序: %s", list(handlers.keys()))

        # 8. 终端指令执行器
        self._executor = CommandExecutor(
            clock=self.clock,
            calendar=self.calendar,
            i18n=self.i18n,
            world_gen=self.world_gen,
        )
        term_handlers = make_terminal_handler(self._executor)
        for req_type, handler in term_handlers.items():
            self.dispatcher.register(req_type, handler)
        logger.info("已注册终端处理程序: %s", list(term_handlers.keys()))

        # 9. 世界树：归档 + 内存限制 + 图预热
        world_tree.configure(
            archive_path="save/events.db",
            max_memory_events=100_000,
        )
        world_tree.warmup_graph(max_events=10_000)
        logger.info(
            "已配置世界树: archive=save/events.db max_memory=%d",
            100_000,
        )

        # 10. 发布世界初始化事件（时钟此时停在 epoch，尚未推进）
        self._publish_world_initialized()

        # 11. 启动 tick 循环——clock.tick() 推进时间，calendar 自动收事件
        self._running = True
        self._thread = threading.Thread(
            target=self._run_loop, name="game-engine", daemon=True
        )
        self._thread.start()
        logger.info("游戏引擎在后台运行 (tick=%.1f Hz)", TICK_RATE)

    def stop(self) -> None:
        """停止引擎并清理所有子系统。

        幂等：已停止时调用无效果。
        """
        if not self._running:
            return
        self._running = False
        if self._thread:
            self._thread.join(timeout=3.0)
            self._thread = None
        world_tree.await_async()
        if self.server:
            self.server.stop()
            self.server = None
        if self.calendar:
            self.calendar.shutdown()
            self.calendar = None  # type: ignore[assignment]
        self.entity_manager = None
        self.tile_generator = None
        self.loaded_chunks.clear()
        if self.world_gen:
            self.world_gen = None
        if self._executor:
            self._executor = None
        logger.info("游戏引擎已停止")

    # ── 出生点与初始区块 ──────────────────────────────────

    @staticmethod
    def _select_birth_point(continent) -> tuple[int, int]:
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

        Returns:
            (chunk_x, chunk_y) 出生 chunk 坐标。
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
            return (0, 0)
        return pool[random.randrange(len(pool))]

    def _generate_initial_chunks(self, continent) -> None:
        """预生成出生点周边 INITIAL_CHUNK_RADIUS 范围的详细 tile 层。

        层1 ChunkData 由 WorldGenerator 并行生成（群系/气候），
        层2 TileGrid 由 TileGenerator 生成（地形/河流/湖泊），
        两者合并写入 ChunkData 并缓存到 loaded_chunks。

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

        # 层2 TileGrid 生成（每个 chunk 独立，无需加锁）
        def _build_tiles(chunk):
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
                self.loaded_chunks[(chunk.cx, chunk.cy)] = chunk

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
                "loaded_chunks": len(self.loaded_chunks),
            },
        ))

    # ── 内部 ──────────────────────────────────────────

    def _run_loop(self) -> None:
        """Tick 循环（运行在后台线程）。"""
        while self._running:
            tick_start = _real_time.monotonic()
            self._tick()
            elapsed = _real_time.monotonic() - tick_start
            sleep_time = TICK_DT - elapsed
            if sleep_time > 0:
                _real_time.sleep(sleep_time)

    def _tick(self) -> None:
        """单个 tick：推进时钟 + 处理所有排队消息。"""
        if self.clock:
            self.clock.tick()
            if hasattr(self, "_executor") and self._executor is not None:
                self._executor._active_real_time += TICK_DT
        if self.dispatcher:
            self.dispatcher.process()
