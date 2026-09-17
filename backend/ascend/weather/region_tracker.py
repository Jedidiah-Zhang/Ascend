"""区域观测 — 降水连通域（纯函数派生，观察者域由调用方声明）。

判定：对声明域内每个 chunk，按解析场降水信号与降水阈值（经注入求值器，
含干预覆盖）判定"越阈"；连通域 = 4-邻接 chunk 集合。

事件：``events(t) = diff(regions(域ₜ, t), regions(域ₜ₋₁, t−GAME_MINUTE))``
——前后两帧都是解析量，**没有任何隐藏状态**（WC-3.2）：读档/首帧天然
正确，同一 (时刻, 域, 前域) 重复观测结果相同，不产生伪事件。前域由
调用方（观察者）提供：域移动时前后帧各用**当时**的域比较。

域由观察者声明（当前为玩家窗口），与加载集/缓存无关（WC-2.3）；阈值与
强度校准输入（年降雨量、基准降雨强度）来自纯气候查询（chunk 气候为
seed 确定的静态量）。

事件语义（相对观察者）：
  - 新区域出现（本帧区域与上一帧区域无交集）→ start；
  - 区域消失（上一帧区域与本帧区域无交集）→ stop；
  - 持续/分裂/合并 → 无事件（"还在下雨"）。
域随观察者移动时，进入窗口的雨区记为 start（"雨来了"）——不设边缘
抑制规则：比窗口更大的雨带也能正常通报。

线程安全：由 WeatherEngine 单线程驱动（查询侧不接触本类）。

用法:
    tracker = RegionTracker(field, evaluate=engine.evaluate_node,
                            climate_baseline=lookup.baseline)
    events = tracker.observe(now, domain)   # → list[RegionEvent]
"""

from dataclasses import dataclass
from typing import Callable, Iterable

from ascend.config import GAME_MINUTE, PRECIP_SIGNAL_MAX
from ascend.space import TILE_MAP_SIZE

from . import mechanisms as m
from .field import UnifiedWeatherField, CH_PRECIPITATION

# 降水信号最大可信值（超过视同饱和，防止校准溢出）
_PRECIP_SIGNAL_CAP: float = PRECIP_SIGNAL_MAX

# 默认观察窗口半径（chunk）：**观察层参数**（不是世界声明）——只决定
# 降水通报的观察范围，不改变世界演化，也不进世界身份；⑤b 将由观测
# 协议元数据接管声明（不在 config.py：config 是机制方程源码依赖，
# 改动会改变世界身份）。
DEFAULT_REGION_RADIUS: int = 16


@dataclass(slots=True)
class RegionEvent:
    """区域级降水事件（出现/消失）。

    Attributes:
        kind: "start" | "stop"。
        cells: 区域包含的 chunk 中心（世界坐标 m 列表）。
        center_chunk: 区域质心所在 chunk (cx, cy)。
        intensity: 质心处降雨强度 (mm/h)，stop 时为 0。
        chunks: 区域包含的 chunk 坐标集合（排序元组）——前端区域渲染
            与叙事载荷的契约字段。
    """

    kind: str
    cells: list[tuple[float, float]]
    center_chunk: tuple[int, int]
    intensity: float = 0.0
    chunks: tuple[tuple[int, int], ...] = ()


class RegionTracker:
    """降水连通域观测器（无状态：域与气候基线均由调用方提供）。

    Args:
        field: 统一天气场（降水信号采样源）。
        evaluate: 节点求值入口 ``(节点 ID, 父值, *, frame, instance) -> 值``
            （WeatherEngine.evaluate_node，含干预覆盖）。
        climate_baseline: 气候基线查询 ``(cx, cy) -> (年降雨量, 基准强度)``
            （纯派生；None = 未接入，``observe`` 即拒绝）。
    """

    def __init__(
        self,
        field: UnifiedWeatherField,
        *,
        evaluate: Callable[..., object],
        climate_baseline: Callable[[int, int], tuple[float, float]] | None = None,
    ) -> None:
        self._field = field
        self._evaluate = evaluate
        self._climate = climate_baseline

    def __repr__(self) -> str:
        return f"RegionTracker(stateless, climate={'on' if self._climate else 'off'})"

    # ── 观测入口 ───────────────────────────────────────────

    def observe(
        self, now: int, domain: Iterable[tuple[int, int]],
        *, previous_domain: Iterable[tuple[int, int]] | None = None,
    ) -> list[RegionEvent]:
        """对声明域输出区域出现/消失事件（功能纯：同参数同结果）。

        前后两帧各用当时的观察域比较：``previous_domain`` 为上一帧的
        域（观察者移动时进入窗口的雨区记为 start）；None = 与 ``domain``
        相同（静态域/首帧）。

        Args:
            now: 当前时刻（tick）。
            domain: 观察者声明的 chunk 域（与加载集无关）。
            previous_domain: 上一帧的观察域；None = 与 ``domain`` 相同。

        Returns:
            区域事件列表。

        Raises:
            RuntimeError: 未接入气候基线查询（fail-closed，不静默空转）。
        """
        if self._climate is None:
            raise RuntimeError(
                "区域观测缺少气候基线查询（climate_baseline）"
            )
        chunks = tuple(domain)
        prev_chunks = (
            chunks if previous_domain is None else tuple(previous_domain)
        )
        current = self._regions(chunks, now)
        previous = (
            self._regions(prev_chunks, now - GAME_MINUTE)
            if now >= GAME_MINUTE else []
        )
        return self._diff(current, previous, now)

    # ── 纯派生 ────────────────────────────────────────────

    def _signal_at_chunk(self, cx: int, cy: int, now: int) -> float:
        """chunk 中心的降水信号（与 get_weather 查询路径一致）。"""
        x = (cx + 0.5) * TILE_MAP_SIZE
        y = (cy + 0.5) * TILE_MAP_SIZE
        return min(
            self._field.sample(CH_PRECIPITATION, x, y, now),
            _PRECIP_SIGNAL_CAP,
        )

    def _regions(
        self, domain: tuple[tuple[int, int], ...], now: int,
    ) -> list[set[tuple[int, int]]]:
        """域内越阈 chunk 的连通域（4-邻接）。"""
        raining: set[tuple[int, int]] = set()
        for cx, cy in domain:
            annual, _mean = self._climate(cx, cy)
            threshold = self._evaluate(
                m.PRECIPITATION_THRESHOLD,
                {m.ANNUAL_RAINFALL: annual},
                frame=now, instance=(cx, cy),
            )
            if self._signal_at_chunk(cx, cy, now) > threshold:
                raining.add((cx, cy))
        return self._connected(raining)

    @staticmethod
    def _connected(
        raining: set[tuple[int, int]],
    ) -> list[set[tuple[int, int]]]:
        """越阈 chunk → 连通域（4-邻接，chunk 网格）。"""
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
        self,
        current: list[set[tuple[int, int]]],
        previous: list[set[tuple[int, int]]],
        now: int,
    ) -> list[RegionEvent]:
        """前后两帧区域重叠匹配，输出出现/消失事件。"""
        events: list[RegionEvent] = []
        for prev in previous:
            if not any(prev & cur for cur in current):
                events.append(self._make_event("stop", prev, now))
        for cur in current:
            if not any(cur & prev for prev in previous):
                events.append(self._make_event("start", cur, now))
        return events

    def _make_event(
        self, kind: str, region: set[tuple[int, int]], now: int,
    ) -> RegionEvent:
        """构造区域事件（质心 + 质心处强度）。"""
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
            annual, mean_intensity = self._climate(center_cx, center_cy)
            threshold = self._evaluate(
                m.PRECIPITATION_THRESHOLD,
                {m.ANNUAL_RAINFALL: annual},
                frame=now, instance=(center_cx, center_cy),
            )
            intensity = self._evaluate(
                m.INSTANT_PRECIPITATION_INTENSITY,
                {
                    m.FIELD_PRECIPITATION_SIGNAL: signal,
                    m.PRECIPITATION_THRESHOLD: threshold,
                    m.MEAN_PRECIP_INTENSITY: mean_intensity,
                },
                frame=now, instance=(center_cx, center_cy),
            )
        return RegionEvent(
            kind=kind,
            cells=cells,
            center_chunk=(center_cx, center_cy),
            intensity=intensity,
            chunks=tuple(sorted(region)),
        )
