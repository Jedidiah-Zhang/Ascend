"""chunk 生命周期服务注册器 — 接入 weather/状态/生态/基因等引擎。

chunk 生命周期三事件：register（chunk 接入世界）→ on_tiles_ready
（tile 生成/恢复完成）→ unregister（LRU 淘汰/卸载）。各服务实现同一
鸭子类型接口（register/on_tiles_ready/unregister），装配方把服务列表
注入注册器，map_handler/game 只遍历注册器。

约定：
- register(chunk)：chunk 元数据已就绪（含气候基线），tile 网格可能
  尚未生成（生成中）。解析算服务（weather）此时即可注册；依赖网格的
  服务（状态引擎）注册后等 on_tiles_ready。
- on_tiles_ready(cx, cy)：tile 生成/恢复完成，网格可用。解析算服务
  无动作；状态引擎补齐积分缺口。
- unregister(cx, cy)：chunk 卸载（LRU 淘汰），服务释放该 chunk 状态。
"""


class ChunkServiceRegistry:
    """chunk 生命周期事件广播器（服务列表有序遍历）。"""

    def __init__(self, services: list | None = None) -> None:
        self._services: list = list(services) if services else []

    def add(self, service) -> None:
        """追加服务（按追加顺序遍历）。"""
        self._services.append(service)

    def register(self, chunk) -> None:
        """把 register(chunk) 依次转发给各服务。"""
        for s in self._services:
            s.register(chunk)

    def on_tiles_ready(self, cx: int, cy: int) -> None:
        """把 on_tiles_ready(cx, cy) 依次转发给各服务。"""
        for s in self._services:
            s.on_tiles_ready(cx, cy)

    def unregister(self, cx: int, cy: int) -> None:
        """把 unregister(cx, cy) 依次转发给各服务。"""
        for s in self._services:
            s.unregister(cx, cy)


class WeatherChunkService:
    """weather_engine 适配器：5 参 register_chunk → register(chunk)。

    on_tiles_ready 无动作：weather 为解析算，不依赖 tile 网格。
    """

    def __init__(self, weather_engine) -> None:
        self._w = weather_engine

    def register(self, chunk) -> None:
        """用 chunk 的坐标与气候基线调用 ``register_chunk``。"""
        self._w.register_chunk(
            chunk.cx, chunk.cy, chunk.annual_baseline,
            chunk.climate_zone, chunk.sea_level_temp,
        )

    def on_tiles_ready(self, cx: int, cy: int) -> None:
        """无动作。"""
        pass

    def unregister(self, cx: int, cy: int) -> None:
        """调用 ``unregister_chunk`` 释放该 chunk 天气状态。"""
        self._w.unregister_chunk(cx, cy)


class TileStateChunkService:
    """tile_state_engine 适配器：把 chunk 生命周期事件转发给状态引擎。"""

    def __init__(self, engine) -> None:
        self._e = engine

    def register(self, chunk) -> None:
        """调用 ``register_chunk`` 登记 chunk。"""
        self._e.register_chunk(chunk)

    def on_tiles_ready(self, cx: int, cy: int) -> None:
        """调用 ``on_tiles_ready`` 补齐积分缺口。"""
        self._e.on_tiles_ready(cx, cy)

    def unregister(self, cx: int, cy: int) -> None:
        """调用 ``unregister_chunk`` 注销 chunk。"""
        self._e.unregister_chunk(cx, cy)
