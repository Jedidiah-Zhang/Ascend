"""chunk 生命周期服务注册器 — 统一接入 weather/状态/生态/基因等引擎。

chunk 生命周期三事件：register（chunk 接入世界）→ on_tiles_ready
（tile 生成/恢复完成）→ unregister（LRU 淘汰/卸载）。各服务实现同一
鸭子类型接口（register/on_tiles_ready/unregister），装配方把服务列表
注入注册器，map_handler/game 只遍历注册器——**新增引擎不改装配代码**
（防三份复制膨胀；时序细节由各服务自管）。

约定：
- register(chunk)：chunk 元数据已就绪（含气候基线），tile 网格可能
  尚未生成（生成中）。解析算服务（weather）此时即可注册；依赖网格的
  服务（状态引擎）注册后等 on_tiles_ready。
- on_tiles_ready(cx, cy)：tile 生成/恢复完成，网格可用。解析算服务
  无动作；状态引擎结算缺口。
- unregister(cx, cy)：chunk 卸载（LRU 淘汰），服务释放该 chunk 状态。
"""


class ChunkServiceRegistry:
    """chunk 生命周期事件广播器（服务列表有序遍历）。"""

    def __init__(self, services: list | None = None) -> None:
        self._services: list = list(services) if services else []

    def add(self, service) -> None:
        self._services.append(service)

    def register(self, chunk) -> None:
        for s in self._services:
            s.register(chunk)

    def on_tiles_ready(self, cx: int, cy: int) -> None:
        for s in self._services:
            s.on_tiles_ready(cx, cy)

    def unregister(self, cx: int, cy: int) -> None:
        for s in self._services:
            s.unregister(cx, cy)


class WeatherChunkService:
    """weather_engine 适配器：5 参 register_chunk → 统一 register(chunk)。

    on_tiles_ready 无动作——weather 是解析算（seed+时间重算），
    tile 生成完成无需追赶。
    """

    def __init__(self, weather_engine) -> None:
        self._w = weather_engine

    def register(self, chunk) -> None:
        self._w.register_chunk(
            chunk.cx, chunk.cy, chunk.annual_baseline,
            chunk.climate_zone, chunk.sea_level_temp,
        )

    def on_tiles_ready(self, cx: int, cy: int) -> None:
        pass

    def unregister(self, cx: int, cy: int) -> None:
        self._w.unregister_chunk(cx, cy)


class TileStateChunkService:
    """tile_state_engine 适配器：方法名对齐统一接口。"""

    def __init__(self, engine) -> None:
        self._e = engine

    def register(self, chunk) -> None:
        self._e.register_chunk(chunk)

    def on_tiles_ready(self, cx: int, cy: int) -> None:
        self._e.on_tiles_ready(cx, cy)

    def unregister(self, cx: int, cy: int) -> None:
        self._e.unregister_chunk(cx, cy)
