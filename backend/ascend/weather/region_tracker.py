"""区域跟踪器 — 场越阈 chunk 的连通域追踪 + 区域级降水事件。

降水从"场降水信号 + 气候带校准阈值"判定（非 per-chunk 调度）。
本模块按注册 chunk 中心采样信号（与 get_weather 查询路径完全一致），
越阈 chunk 聚合成连通域（区域），区域出现/消失 → 区域级
precipitation_start/stop 事件。

与上一帧区域做重叠匹配：区域持续存在（即使移动/变形）不发事件；
区域分裂/合并不发事件（"还在下雨"）；完全消失发 stop、
全新出现发 start。事件数与 tile 无关（chunk 粒度，区域 ~5km 尺度）。

线程安全：由 WeatherEngine 单线程驱动（查询侧不接触本类）。

用法:
    tracker = RegionTracker(field)
    tracker.set_chunk_baseline(cx, cy, annual_rainfall, mean_intensity)
    events = tracker.update(now)   # → list[RegionEvent]
    tracker.remove_chunk(cx, cy)
"""

from dataclasses import dataclass

from ascend.config import PRECIP_SIGNAL_MAX
from ascend.space import TILE_MAP_SIZE

from .field import (
    UnifiedWeatherField, CH_PRECIPITATION, calibrate_precip, precip_threshold,
)

# 降水信号最大可信值（超过视同饱和，防止校准溢出）
_PRECIP_SIGNAL_CAP: float = PRECIP_SIGNAL_MAX


@dataclass(slots=True)
class RegionEvent:
    """区域级降水事件（出现/消失）。

    Attributes:
        kind: "start" | "stop"。
        cells: 区域包含的 chunk 中心（世界坐标 m 列表）。
        center_chunk: 区域质心所在 chunk (cx, cy)。
        intensity: 质心处降雨强度 (mm/h)，stop 时为 0。
        chunks: 区域包含的 chunk 坐标集合（排序元组）——状态引擎
            批量涂抹与前端区域渲染的契约字段。
    """

    kind: str
    cells: list[tuple[float, float]]
    center_chunk: tuple[int, int]
    intensity: float = 0.0
    chunks: tuple[tuple[int, int], ...] = ()


class RegionTracker:
    """场越阈区域追踪器。

    Args:
        field: 统一天气场（降水信号采样源）。
    """

    def __init__(self, field: UnifiedWeatherField) -> None:
        """初始化区域跟踪器。

        Args:
            field: 统一天气场。
        """
        self._field = field
        # chunk → (年降雨量, 基准降雨强度)（阈值校准输入，注册时注入）
        self._baselines: dict[tuple[int, int], tuple[float, float]] = {}
        # 上一帧区域（每区域 = chunk 坐标集合）
        self._prev_regions: list[set[tuple[int, int]]] = []

    def __repr__(self) -> str:
        return (
            f"RegionTracker(chunks={len(self._baselines)}, "
            f"regions={len(self._prev_regions)})"
        )

    # ── chunk 校准数据注入 ─────────────────────────────────

    def set_chunk_baseline(
        self, cx: int, cy: int, annual_rainfall: float,
        mean_intensity: float = 5.0,
    ) -> None:
        """注册 chunk 的年降雨量 + 基准降雨强度（降水校准输入）。

        Args:
            cx, cy: chunk 坐标。
            annual_rainfall: 年降雨量 (mm/年)（越阈水平连续标定输入）。
            mean_intensity: 气候带基准降雨强度 (mm/h)（强度放大基准）。
        """
        self._baselines[(cx, cy)] = (annual_rainfall, mean_intensity)

    def remove_chunk(self, cx: int, cy: int) -> None:
        """注销 chunk（LRU 淘汰时由 WeatherEngine 调用）。

        Args:
            cx, cy: chunk 坐标。
        """
        self._baselines.pop((cx, cy), None)

    def _signal_at_chunk(self, cx: int, cy: int, now: int) -> float:
        """chunk 中心的降水信号（与 get_weather 查询路径一致）。

        Args:
            cx, cy: chunk 坐标。
            now: 时刻（tick）。

        Returns:
            降水信号（钳制在 PRECIP_SIGNAL_MAX）。
        """
        x = (cx + 0.5) * TILE_MAP_SIZE
        y = (cy + 0.5) * TILE_MAP_SIZE
        return min(
            self._field.sample(CH_PRECIPITATION, x, y, now),
            _PRECIP_SIGNAL_CAP,
        )

    # ── 逐 tick 更新 ───────────────────────────────────────

    def update(self, now: int) -> list[RegionEvent]:
        """扫描注册 chunk，输出区域出现/消失事件。

        每游戏分钟调用一次（WeatherEngine._on_minute_change）。

        Args:
            now: 当前时刻（tick）。

        Returns:
            区域事件列表（start 优先于 stop，顺序无关紧要）。
        """
        if not self._baselines:
            # 无注册 chunk：上一帧区域全部消失 → 补发 stop（从未 update 过则无事件）
            stops = [
                self._make_event("stop", prev, now)
                for prev in self._prev_regions
            ]
            self._prev_regions = []
            return stops
        raining: set[tuple[int, int]] = set()
        for (cx, cy), (annual, _mi) in self._baselines.items():
            threshold = precip_threshold(annual)
            if self._signal_at_chunk(cx, cy, now) > threshold:
                raining.add((cx, cy))
        regions = self._connected(raining)
        events = self._diff(regions, now)
        self._prev_regions = regions
        return events

    def _connected(
        self, raining: set[tuple[int, int]],
    ) -> list[set[tuple[int, int]]]:
        """越阈 chunk → 连通域（4-邻接，chunk 网格）。

        Args:
            raining: 越阈 chunk 坐标集合。

        Returns:
            区域列表（每区域 = chunk 坐标集合）。
        """
        visited: set[tuple[int, int]] = set()
        regions: list[set[tuple[int, int]]] = []
        for start in raining:
            if start in visited:
                continue
            stack = [start]
            region: set[tuple[int, int]] = set()
            visited.add(start)
            while stack:
                cx, cy = stack.pop()
                region.add((cx, cy))
                for nxy in ((cx - 1, cy), (cx + 1, cy),
                            (cx, cy - 1), (cx, cy + 1)):
                    if nxy in raining and nxy not in visited:
                        visited.add(nxy)
                        stack.append(nxy)
            regions.append(region)
        return regions

    def _diff(
        self, regions: list[set[tuple[int, int]]], now: int,
    ) -> list[RegionEvent]:
        """与上一帧区域重叠匹配，输出出现/消失事件。

        Args:
            regions: 本帧区域列表。
            now: 当前时刻（tick）。

        Returns:
            区域事件列表。
        """
        events: list[RegionEvent] = []
        # 消失：上一帧区域无本帧交集
        for prev in self._prev_regions:
            if not any(prev & cur for cur in regions):
                events.append(self._make_event("stop", prev, now))
        # 出现：本帧区域无上一帧交集
        for cur in regions:
            if not any(cur & prev for prev in self._prev_regions):
                events.append(self._make_event("start", cur, now))
        return events

    def _make_event(
        self, kind: str, region: set[tuple[int, int]], now: int,
    ) -> RegionEvent:
        """构造区域事件（质心 + 质心处强度）。

        Args:
            kind: "start" | "stop"。
            region: 区域（chunk 坐标集合）。
            now: 当前时刻（tick）。

        Returns:
            RegionEvent。
        """
        cxs = [c[0] for c in region]
        cys = [c[1] for c in region]
        center_cx = int(round(sum(cxs) / len(cxs)))
        center_cy = int(round(sum(cys) / len(cys)))
        cells = [
            ((cx + 0.5) * TILE_MAP_SIZE, (cy + 0.5) * TILE_MAP_SIZE)
            for cx, cy in sorted(region)
        ]
        intensity = 0.0
        if kind == "start":
            signal = self._signal_at_chunk(center_cx, center_cy, now)
            annual, mean_intensity = self._baselines.get(
                (center_cx, center_cy), (0.0, 5.0))
            intensity = calibrate_precip(
                signal, annual, mean_intensity,
            )
        return RegionEvent(
            kind=kind,
            cells=cells,
            center_chunk=(center_cx, center_cy),
            intensity=intensity,
            chunks=tuple(sorted(region)),
        )
