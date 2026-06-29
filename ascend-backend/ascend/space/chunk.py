"""分块数据结构 — 大地图层 + 详细层的值对象。

ChunkData 是不可变的部分（群系、气候参数）和可变的部分（详细 tile 网格、
标记）的组合。创建后归属调用线程，无跨线程共享，天然线程安全。
"""

from dataclasses import dataclass, field

from .biome import BiomeType
from .climate import ClimateZone, WeatherParams


# 详细地图固定尺寸
TILE_MAP_SIZE: int = 200


@dataclass
class ChunkData:
    """一个分块的完整数据。

    大地图层（轻量，始终存在）：
    - 群系类型、气候档位
    - 年均气象基线
    - 通行属性和标记

    详细层（按需生成）：
    - 200×200 tile 网格（生成后持久化）

    Attributes:
        cx: 分块 X 坐标。
        cy: 分块 Y 坐标。
        biome: 群系类型。
        climate_zone: 气候档位。
        annual_baseline: 年均气象参数基线。
        markers: 大地图标记 {marker_id: description}。
        passable: 能否穿越（默认 True）。
        travel_speed: 穿越速度倍率（默认 1.0）。
        tiles: 详细 tile 网格，None 表示未生成。
    """

    cx: int
    cy: int
    biome: BiomeType
    climate_zone: ClimateZone
    annual_baseline: WeatherParams

    markers: dict[str, str] = field(default_factory=dict)
    passable: bool = True
    travel_speed: float = 1.0

    # 详细层 — 按需生成
    tiles: list[list[int]] | None = None

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
            True 表示 tiles 非空。
        """
        return self.tiles is not None

    def generate_tiles(self, tile_data: list[list[int]]) -> None:
        """写入详细 tile 数据。

        通常在分块首次进入玩家视野时调用。
        只应被归属线程调用一次。

        Args:
            tile_data: 200×200 的 tile 类型 ID 矩阵。

        Raises:
            ValueError: 维度不是 200×200。
        """
        if len(tile_data) != TILE_MAP_SIZE:
            raise ValueError(
                f"tile 行数必须为 {TILE_MAP_SIZE}，实际为 {len(tile_data)}"
            )
        for row in tile_data:
            if len(row) != TILE_MAP_SIZE:
                raise ValueError(
                    f"tile 列数必须为 {TILE_MAP_SIZE}，实际为 {len(row)}"
                )
        self.tiles = tile_data

    def unload_tiles(self) -> None:
        """卸载详细 tile 层以释放内存。

        保留大地图层数据。
        """
        self.tiles = None

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
        has_tiles = "tiles" if self.tiles else "no_tiles"
        return (
            f"ChunkData({self.cx}, {self.cy}, "
            f"biome={self.biome.label}, "
            f"climate={self.climate_zone.label}, "
            f"markers={len(self.markers)}, "
            f"{has_tiles})"
        )
