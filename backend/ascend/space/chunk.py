"""分块数据结构 — 分块在缓存中的值对象。

ChunkData 持有大地图层信息（群系、气候、标记）与按需生成的
详细 tile 网格（tile_grid）。详细层由 TileGenerator 生成，
经 ChunkStore 持久化玩家改动。

创建后归属调用线程，无跨线程共享，天然线程安全。
"""

from dataclasses import dataclass, field

from .biome import BiomeType
from .climate import ClimateZone, WeatherParams
from .tile_grid import TileGrid, TILE_MAP_SIZE


@dataclass
class ChunkData:
    """一个分块的大地图层数据与按需生成的详细 tile 网格。

    详细层（tile_grid）由 TileGenerator 生成，None 表示未生成；
    玩家改动经 ChunkStore.mark_dirty 置脏、由 ChunkStore 持久化。

    连续气候属性（mean_temp/annual_rainfall/sea_level_temp/altitude）
    是层1场在该 chunk 中心的值，气候档位与群系由其派生。
    群系隶属度在 tile 级通过海拔和 moisture 噪声混合，避免 chunk 边界跳变。

    Attributes:
        cx: 分块 X 坐标。
        cy: 分块 Y 坐标。
        biome: 群系类型。
        climate_zone: 气候档位（派生标签，UI 显示用）。
        annual_baseline: 年均气象参数基线。
        mean_temp: 年均温度 (°C)（连续属性）。
        annual_rainfall: 年降雨量 (mm)（连续属性）。
        sea_level_temp: 海平面温度 (°C)（连续属性）。
        altitude: 海拔 (m)（连续属性）。
        markers: 大地图标记 {marker_id: description}。
        passable: 能否穿越（默认 True）。
        travel_speed: 穿越速度倍率（默认 1.0）。
        tile_grid: 详细 tile 网格，None 表示未生成。
        dirty: 玩家修改未落盘标记。
    """

    cx: int
    cy: int
    biome: BiomeType
    climate_zone: ClimateZone
    annual_baseline: WeatherParams

    mean_temp: float = 0.0
    annual_rainfall: float = 0.0
    sea_level_temp: float = 0.0
    altitude: float = 0.0

    markers: dict[str, str] = field(default_factory=dict)
    passable: bool = True
    travel_speed: float = 1.0

    # 详细层 — 按需生成
    # tile_grid 代表地表层（layer_id=0）的详细 tile。
    # 未来洞穴层（layer_id<0）的 chunk 数据结构待定，
    # 可能是 ChunkData 内嵌 caves: dict[int, TileGrid]，
    # 或独立的 CaveChunkData。当前不预留字段，避免过早抽象。
    tile_grid: TileGrid | None = None

    # 玩家修改未落盘标记。不变量：dirty ⇒ 持有 tile_grid——
    # 置脏入口（ChunkStore.mark_dirty）要求网格在场，覆盖/卸载
    # 入口（generate_tiles / unload_tiles）拒绝脏 chunk；持久化
    # 成功后由 ChunkStore 清除。
    dirty: bool = False

    @property
    def chunk_key(self) -> tuple[int, int]:
        """分块坐标元组。

        Returns:
            (cx, cy)。
        """
        return (self.cx, self.cy)

    @property
    def has_tiles(self) -> bool:
        """详细 tile 层是否已生成。

        Returns:
            True 表示 tile_grid 非空。
        """
        return self.tile_grid is not None

    def generate_tiles(self, grid: TileGrid) -> None:
        """写入详细 tile 数据（seed 确定性生成，clean）。

        通常在分块首次进入玩家视野时调用。
        只应被归属线程调用一次。

        脏 chunk 的网格含未落盘的玩家改动，拒绝覆盖——先经
        ChunkStore 持久化清除脏标记后再生成。

        Args:
            grid: 200×200 的 TileGrid。

        Raises:
            ValueError: chunk 为脏，或网格尺寸不符。
        """
        if self.dirty:
            raise ValueError("脏 chunk 的网格含未落盘改动，拒绝覆盖")
        if grid.size != TILE_MAP_SIZE:
            raise ValueError(
                f"TileGrid 尺寸必须为 {TILE_MAP_SIZE}，实际为 {grid.size}"
            )
        self.tile_grid = grid

    def restore_tiles(self, grid: TileGrid) -> None:
        """从持久化恢复详细 tile 数据，并标记为脏。

        库中行 = 玩家改动（确定性 tile 从不落盘）：恢复的 chunk
        必须保持脏标记，使淘汰/退出时重新落盘。

        Args:
            grid: 200×200 的 TileGrid。

        Raises:
            ValueError: chunk 为脏，或网格尺寸不符。
        """
        self.generate_tiles(grid)
        self.dirty = True

    def unload_tiles(self) -> bool:
        """卸载详细 tile 层以释放内存。

        保留大地图层数据。TileGrid 由 GC 回收。
        脏 chunk 的网格含未落盘的玩家改动，拒绝卸载——先经
        ChunkStore 持久化清除脏标记后再卸载。

        Returns:
            True 表示已卸载；False 表示 chunk 为脏，网格保留。
        """
        if self.dirty:
            return False
        self.tile_grid = None
        return True

    def add_marker(self, marker_id: str, description: str) -> None:
        """在大地图上添加标记。

        Args:
            marker_id: 标记唯一 ID（如 "settlement_main"）。
            description: 标记描述。
        """
        self.markers[marker_id] = description

    def remove_marker(self, marker_id: str) -> None:
        """移除标记。

        Args:
            marker_id: 要移除的标记 ID。
        """
        self.markers.pop(marker_id, None)

    def __repr__(self) -> str:
        has_tiles = "tiles" if self.tile_grid else "no_tiles"
        return (
            f"ChunkData({self.cx}, {self.cy}, "
            f"biome={self.biome.label}, "
            f"climate={self.climate_zone.label}, "
            f"markers={len(self.markers)}, "
            f"{has_tiles})"
        )
