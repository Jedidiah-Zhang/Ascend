"""大陆生成模块 — 层1 全局低分辨率大陆生成。

在世界创建时调用一次，生成 100m/采样点 的宏观场：
   - 海拔场（两层 Perlin：低频大陆轮廓 + 高频地形细节）
   - 温雨气候（C 端物理模型：纬度梯度 + 大陆度 + 雨影，校准后以 chunk 级 dict 存储）
   - 河流宽度场
   - 内陆湖泊
   - 水文数据（D8 流向、水流累积、湖盆、流线河网）

结果保存在 ContinentData 中，所有 chunk 和 tile 生成共享此数据。

职责划分：
  - 本模块（ContinentGenerator）：层1 宏观场生成（含地形预览）。
  - continent_data：数据类（ContinentParams / ContinentData）。
  - continent_io：大陆缓存二进制序列化（落盘/读档）。

用法:
    from ascend.space.continent import ContinentGenerator, ContinentParams

    gen = ContinentGenerator(seed=42)
    data = gen.generate()

    alt = data.sample_altitude(500.0, 300.0)
    is_land = data.is_land(12.5, 34.2)
"""

from array import array
from collections.abc import Callable, Iterable

from .noise import PerlinNoise
from .climate import ClimateZone, LAPSE_RATE
from .randomness import seed_angle
from ascend.config import (
    EROSION_ITERATIONS,
    LAKE_MIN_PIXELS,
    RIVER_FLOW_THRESHOLD,
    RIVER_MIN_LENGTH,
    RIVER_WIDTH_THRESHOLD,
    ELEVATION_TARGET_P99,
    ELEVATION_SCALE_FACTOR,
    CONTINENTALITY_K,
    CONTINENTALITY_D0_KM,
    RAINSHADOW_DECAY_KM,
    RAINSHADOW_SECONDARY_WEIGHT,
    RAINSHADOW_MIN_FACTOR,
    CONTINENT_BLEND_WEIGHT,
    CONTINENT_SAMPLE_RESOLUTION_M,
    TERRAIN_BLEND_WEIGHT,
    CENTER_BIAS_WEIGHT,
    CLIMATE_CALIB_RAINFALL_REF,
    CLIMATE_CALIB_TEMP_MIN,
    CLIMATE_CALIB_TEMP_MAX,
    CLIMATE_CALIB_HOT_THRESHOLD,
    CLIMATE_CALIB_COLD_RANGE,
    CLIMATE_CALIB_HOT_RAINFALL_TARGET,
    CLIMATE_CALIB_HOT_STRETCH_PARAM,
    CLIMATE_CALIB_COLD_RAINFALL_TARGET,
    CLIMATE_CALIB_COLD_STRETCH_PARAM,
)
from ascend.log import get_logger

from .continent_data import ContinentData, ContinentParams
from .continent_io import (
    CONTINENT_CACHE_VERSION,
    serialize_continent,
    deserialize_continent,
    read_continent_header,
)

logger = get_logger(__name__)


def center_distance(dx: float, dy: float) -> float:
    """归一化坐标到矩形中心的 Chebyshev 距离，四象限对称。

    中心偏置（center bias）用「距地图中心的距离」把陆地推向中心。
    旧实现的手写分支在第三象限与负 y 轴出错（dx=0, dy=-2 时误算为 0，
    中心偏置消失）；max(abs) 恒非负且象限对称，消除该缺陷。

    Args:
        dx: 归一化 X 偏移（[-1, 1]）。
        dy: 归一化 Y 偏移（[-1, 1]）。

    Returns:
        非负距离 [0, 1]。
    """
    return max(abs(dx), abs(dy))


class ContinentGenerator:
    """层1全局大陆生成器。

    每个 seed 独立生成一个 ContinentData。
    线程安全：generate() 创建所有临时状态，无共享可变状态。
    """

    def __init__(
        self,
        seed: int = 0,
        *,
        params: ContinentParams | None = None,
    ) -> None:
        """初始化生成器。

        Args:
            seed: 世界种子。
            params: 生成参数。
        """
        self._seed = seed
        self._params = params or ContinentParams()

    def __repr__(self) -> str:
        return f"ContinentGenerator(seed={self._seed})"

    @property
    def _grid_width(self) -> int:
        return int(self._params.width_km * 1000 / self._params.sample_resolution)

    @property
    def _grid_height(self) -> int:
        return int(self._params.height_km * 1000 / self._params.sample_resolution)

    # ── 主入口 ──────────────────────────────────────────────

    # 生成阶段名（进度广播用，前端按此显示阶段文案）
    STAGE_ELEVATION = "elevation"
    STAGE_CLIMATE = "climate"
    STAGE_EROSION = "erosion"
    STAGE_WATER = "water"
    STAGE_WIDTH = "width"
    STAGE_DONE = "done"

    def generate(
        self,
        progress_cb: "Callable[[str], None] | None" = None,
    ) -> ContinentData:
        """执行完整的层1生成管线。

        管线顺序：
          海拔 + 陆地掩码 → 海拔校准 → 气候（温度+降雨）→ 气候校准
          → 侵蚀（降雨驱动水流）→ 河流树 + 湖泊盆地提取

        校准步骤保证 8 档气候覆盖：海拔/降雨/温度场分别做保结构的
        分位数拉伸，确保值域覆盖各气候档位的判定阈值。

        Args:
            progress_cb: 可选阶段回调，每个生成阶段开始时以阶段名
                调用（STAGE_* 常量）。供前端进度条展示，缓存命中时
                不进入本方法（由调用方上报 STAGE_DONE）。

        Returns:
            ContinentData 宏观场。
        """
        def _report(stage: str) -> None:
            if progress_cb is not None:
                progress_cb(stage)

        w = self._grid_width
        h = self._grid_height

        # Step 1: 海拔 + 陆地掩码（湖泊由水文系统接管）
        _report(self.STAGE_ELEVATION)
        land_mask, elevation = self._generate_elevation(w, h)

        # Step 1b: 海拔校准 — 保证高山（≥2000m）存在
        self._ensure_elevation_range(elevation, land_mask)

        # Step 2: 气候（温度、降雨、气候带）—— 降雨在侵蚀之前生成
        _report(self.STAGE_CLIMATE)
        temp_field, rain_field, climate_field = (
            self._compute_climate(elevation, land_mask, w, h))

        # Step 2b-2e: 气候校准 + 重分类（合并为单次遍历）
        self._calibrate_climate_merged(
            elevation, temp_field, rain_field, land_mask, climate_field, w, h,
        )

        # Step 3: 侵蚀（降雨驱动水流累积）—— 提取完整水文状态
        _report(self.STAGE_EROSION)
        from .hydrology import erode, extract_lake_basins, HydrologyData
        erosion_result = erode(elevation, rain_field, w, h,
                               iterations=EROSION_ITERATIONS)

        # 用侵蚀后的海拔替换原始海拔（河流已雕刻，地形已塑形）
        elevation = erosion_result.dem

        # Step 4: 湖泊盆地提取
        _report(self.STAGE_WATER)
        lake_basins = extract_lake_basins(
            elevation, erosion_result.filled_dem, land_mask, w, h,
            min_size=LAKE_MIN_PIXELS,
        )

        # Step 4b: 流线河流网络 — RK4 沿海拔梯度场追踪自然弯曲流线
        from .streamlines import build_river_network
        river_network = build_river_network(
            elevation,
            erosion_result.directions, erosion_result.flow_acc,
            land_mask, w, h,
            threshold=RIVER_FLOW_THRESHOLD, min_length=RIVER_MIN_LENGTH,
        )

        hydrology = HydrologyData(
            lake_basins=lake_basins,
            flow_acc=erosion_result.flow_acc,
            directions=erosion_result.directions,
            filled_dem=erosion_result.filled_dem,
            river_network=river_network,
        )

        # Step 5: 河流宽度场（复用侵蚀+水文数据，避免重复计算）
        _report(self.STAGE_WIDTH)
        from .hydrology import compute_river_width
        river_width = compute_river_width(
            elevation, w, h,
            land_mask=land_mask, threshold=RIVER_WIDTH_THRESHOLD,
            directions=erosion_result.directions,
            flow_acc=erosion_result.flow_acc,
            lake_basins=lake_basins,
        )

        # Step 6: 兜底 — 保证 8 档气候覆盖（最后执行，不影响水文）
        self._inject_missing_climates(
            elevation, temp_field, rain_field, land_mask, climate_field, w, h,
        )

        # Step 7: 群系细分动态值域 — 每档内细分维度的 P10/P90
        subdiv_ranges = self._compute_subdiv_ranges(
            elevation, temp_field, rain_field, land_mask, climate_field, w, h,
        )

        # Step 8: 提取 chunk 级气候缓存（校准后值，供 tile_gen 等模块使用）
        chunk_climate: dict = {}
        for cy in range(h // 2):
            for cx in range(w // 2):
                idx = (cy * 2 + 1) * w + (cx * 2 + 1)
                alt = elevation[idx]
                temp = temp_field[idx]
                rain = rain_field[idx]
                zone = climate_field[idx]
                # 海平面温度 = 地表温度 + 海拔×直减率（直减率仅作用于
                # 陆地；海域 alt<0，sea_temp = 地表温度 = 海面温度）
                sea_temp = temp + max(0.0, alt) * LAPSE_RATE / 1000.0
                chunk_climate[(cx, cy)] = (temp, rain, sea_temp, zone)

        _report(self.STAGE_DONE)
        return ContinentData(
            grid_width=w, grid_height=h,
            cell_size=self._params.sample_resolution,
            seed=self._seed,
            land_ratio=self._params.land_ratio,
            land_mask=land_mask,
            elevation_field=array('d', elevation),
            river_width=array('d', river_width),
            hydrology=hydrology,
            subdiv_ranges=subdiv_ranges,
            _chunk_climate=chunk_climate,
        )

    # ── 快速预览 ──────────────────────────────────────────

    # 预览采样分辨率 (m/格)：1000m 低分辨率缩略图（默认 100×60 网格，
    # 6000 格，秒级出图）。地形噪声场与分辨率无关（频率随采样分辨率
    # 缩放），低分辨率采样 = 同一地形的粗采样。
    PREVIEW_RESOLUTION_M: float = 1000.0

    def generate_preview(
        self, land_ratio: float,
        width_km: float | None = None, height_km: float | None = None,
        layers: Iterable[str] = (),
    ) -> dict:
        """只生成海拔 + 陆地掩码的轻量预览（跳过侵蚀/水文）。

        分位数校准保证预览陆地占比贴合 land_ratio（与真实生成同一
        校准逻辑）；海拔另做与真实生成相同的高海拔拉伸，山顶着色
        接近最终世界。未经侵蚀，预览海拔与最终世界略有偏差——
        仅作形状与占比参考的缩略图。

        layers 请求气候图层（"temp" / "rain" / "climate"）时，在
        海拔后补跑与完整管线一致的气候计算（_compute_climate +
        校准 + 缺失档位注入），预览的气候值与最终世界一致。气候
        计算仅 4 个噪声八度 + 数次 O(N) 校准遍历，相对海拔计算
        增量极小，仍保持秒级返回；侵蚀/水文（昂贵部分）依旧跳过。

        低分辨率缩略图（1000m/格）：网格随尺寸缩放（60×36 → 60×36 格，
        150×90 → 150×90 格），地形变化率一致——尺寸只影响生成范围。

        Args:
            land_ratio: 目标陆地比例 [0-1]。
            width_km: 大陆东西宽度 (km)；None 用生成器参数（默认 100）。
            height_km: 大陆南北高度 (km)；None 用生成器参数（默认 60）。
            layers: 附加请求的气候图层名集合（"temp" / "rain" / "climate"）；
                缺省仅海拔（向后兼容旧客户端）。

        Returns:
            预览数据字典:
                {width, height, land_percent, elevation: [int 米] 行优先}
                layers 含 "temp" 时附 temperature: [int °C]（海陆全域
                地表温度：陆地 = 校准后地表温度；海域 = 海面温度——
                纬度梯度场 clamp [-20, 38]，不含海拔直减率；
                与生产管线 sea_temp 同源一致）；
                layers 含 "rain" 时附 rainfall: [int mm/年]（海陆全域）；
                layers 含 "climate" 时附 climate: [int 0-7 气候档]（海域
                未校准，仅陆地有意义，前端忽略海域）。
        """
        preview_params = ContinentParams(
            width_km=width_km if width_km is not None else self._params.width_km,
            height_km=height_km if height_km is not None else self._params.height_km,
            sample_resolution=self.PREVIEW_RESOLUTION_M,
            land_ratio=float(land_ratio),
        )
        gen = ContinentGenerator(seed=self._seed, params=preview_params)
        w = int(preview_params.width_km * 1000.0 / preview_params.sample_resolution)
        h = int(preview_params.height_km * 1000.0 / preview_params.sample_resolution)
        land_mask, elevation = gen._generate_elevation(w, h)
        self._ensure_elevation_range(elevation, land_mask)
        land_count = sum(1 for v in land_mask if v)
        preview: dict = {
            "width": w,
            "height": h,
            "land_percent": round(land_count / max(1, w * h), 4),
        }
        layers = set(layers)
        if layers:
            # 与完整管线同序：气候 → 校准（含重分类）→ 缺失档位兜底注入，
            # 保证预览气候值与最终世界一致（注入亦会抬升个别高山像素海拔，
            # 使预览山顶着色接近最终世界）。
            temp_field, rain_field, climate_field = (
                gen._compute_climate(elevation, land_mask, w, h))
            gen._calibrate_climate_merged(
                elevation, temp_field, rain_field, land_mask, climate_field, w, h,
            )
            gen._inject_missing_climates(
                elevation, temp_field, rain_field, land_mask, climate_field, w, h,
            )
            if "temp" in layers:
                # 温度场统一为地表温度（海域 = 海面温度，无海底伪影）
                preview["temperature"] = [int(round(v)) for v in temp_field]
            if "rain" in layers:
                preview["rainfall"] = [int(round(v)) for v in rain_field]
            if "climate" in layers:
                preview["climate"] = [int(v) for v in climate_field]
        # 海拔在气候层之后快照：注入兜底会抬升个别高山像素，
        # 与完整管线同序，海拔视图与气候层自洽
        preview["elevation"] = [int(round(v)) for v in elevation]
        return preview

    # ── 气候覆盖校准 ──────────────────────────────────────

    @staticmethod
    def _percentile(sorted_vals: list[float], pct: float) -> float:
        """从已排序数组取分位数（线性插值）。"""
        n = len(sorted_vals)
        if n == 0:
            return 0.0
        pos = pct * (n - 1)
        lo = int(pos)
        hi = min(lo + 1, n - 1)
        frac = pos - lo
        return sorted_vals[lo] * (1 - frac) + sorted_vals[hi] * frac

    def _calibrate_climate_merged(
        self,
        elevation: list[float],
        temp: list[float],
        rain: list[float],
        land_mask: list[bool],
        climate_field: list[int],
        w: int, h: int,
    ) -> None:
        """合并气候校准 — 数据收集、排序、合并应用。

        在两次 O(N) 遍历中完成降雨/温度范围校准和气候带覆盖检查，
        对缺失气候档位通过共享排序找到最近邻区域注入种子。
        """
        from .climate import classify
        n = w * h

        # Phase 1: 一次遍历收集所有排序所需数据
        land_temps: list[float] = []
        land_rains: list[float] = []

        for i in range(n):
            if land_mask[i]:
                land_temps.append(temp[i])
                land_rains.append(rain[i])

        if not land_temps:
            return

        # Phase 2: 排序 + 计算校准参数
        land_temps.sort()
        land_rains.sort()

        # 降雨校准参数 (原 _ensure_rainfall_range)
        rain_p3 = self._percentile(land_rains, 0.03)
        rain_p10 = self._percentile(land_rains, 0.10)
        do_rain_cal = not (rain_p3 <= CLIMATE_CALIB_RAINFALL_REF or rain_p10 <= rain_p3)

        # 温度校准参数 (原 _ensure_temperature_range)
        temp_p2 = self._percentile(land_temps, 0.02)
        temp_p98 = self._percentile(land_temps, 0.98)
        do_temp_cal = (
            temp_p98 - temp_p2 >= 1.0
            and not (temp_p2 <= CLIMATE_CALIB_TEMP_MIN and temp_p98 >= CLIMATE_CALIB_TEMP_MAX)
        )

        # Phase 3: 应用降雨和温度校准，同时收集交叉校准所需数据
        # （交叉校准需要在温度校准之后收集，因为用校准后的温度分区）
        if do_rain_cal:
            rain_scale = (rain_p10 - CLIMATE_CALIB_RAINFALL_REF) / (rain_p10 - rain_p3)
        if do_temp_cal:
            temp_scale = (CLIMATE_CALIB_TEMP_MAX - CLIMATE_CALIB_TEMP_MIN) / (temp_p98 - temp_p2)
            temp_offset = CLIMATE_CALIB_TEMP_MIN - temp_p2 * temp_scale

        hot_rains: list[float] = []   # 热区(T>=20) 的降雨值
        cold_rains: list[float] = []  # 冷区(-5<=T<5) 的降雨值

        for i in range(n):
            is_land = land_mask[i]

            # 降雨校准（仅陆地）
            if is_land and do_rain_cal and rain[i] < rain_p10:
                rain[i] = max(0.0, CLIMATE_CALIB_RAINFALL_REF + (rain[i] - rain_p3) * rain_scale)

            # 温度校准（陆地+海洋统一应用，消除海陆边界跳变）
            if do_temp_cal:
                temp[i] = temp[i] * temp_scale + temp_offset

            if not is_land:
                continue

            # 收集校准后的交叉校准数据（仅陆地）
            t = temp[i]
            r = rain[i]
            if t >= CLIMATE_CALIB_HOT_THRESHOLD:
                hot_rains.append(r)
            elif CLIMATE_CALIB_COLD_RANGE[0] <= t < CLIMATE_CALIB_COLD_RANGE[1]:
                cold_rains.append(r)

        # Phase 4: 排序交叉校准数据 + 计算参数
        hot_rains.sort()
        cold_rains.sort()

        do_hot_cal = len(hot_rains) > 100
        hot_p20 = 0.0
        hot_max = 0.0
        if do_hot_cal:
            hot_p20 = hot_rains[int(len(hot_rains) * 0.20)]
            hot_max = hot_rains[-1]
            do_hot_cal = hot_max < CLIMATE_CALIB_HOT_RAINFALL_TARGET and hot_max > hot_p20

        do_cold_cal = len(cold_rains) > 100
        cold_p40 = 0.0
        cold_max = 0.0
        if do_cold_cal:
            cold_p40 = cold_rains[int(len(cold_rains) * 0.40)]
            cold_max = cold_rains[-1]
            do_cold_cal = cold_max < CLIMATE_CALIB_COLD_RAINFALL_TARGET and cold_max > cold_p40

        # Phase 5: 应用交叉校准 + 重分类（单次遍历）
        for i in range(n):
            if not land_mask[i]:
                continue

            t = temp[i]
            r = rain[i]

            # 交叉校准——使用已校准的温湿度值
            if do_hot_cal and t >= CLIMATE_CALIB_HOT_THRESHOLD and r > hot_p20:
                frac = (r - hot_p20) / (hot_max - hot_p20)
                rain[i] = CLIMATE_CALIB_HOT_STRETCH_PARAM[0] + frac * CLIMATE_CALIB_HOT_STRETCH_PARAM[1]
            elif (
                do_cold_cal
                and CLIMATE_CALIB_COLD_RANGE[0] <= t < CLIMATE_CALIB_COLD_RANGE[1]
                and r > cold_p40
            ):
                frac = (r - cold_p40) / (cold_max - cold_p40)
                rain[i] = CLIMATE_CALIB_COLD_STRETCH_PARAM[0] + frac * CLIMATE_CALIB_COLD_STRETCH_PARAM[1]

            # 重分类
            climate_field[i] = int(classify(temp[i], rain[i], elevation[i]))

    def _ensure_elevation_range(
        self, elevation: list[float], land_mask: list[bool],
    ) -> None:
        """海拔校准 — 拉伸高海拔尾部，保证陆地 P99 ≥ 2500m。

        只提升 top 10% 区域（P90 以上），低海拔不变，不影响海岸线。
        侵蚀不削平山顶，故侵蚀后仍保留 ≥2000m 的高山。
        原地修改 elevation。
        """
        land_vals = sorted(e for i, e in enumerate(elevation) if land_mask[i])
        if not land_vals:
            return
        p90 = self._percentile(land_vals, 0.90)
        p99 = self._percentile(land_vals, 0.99)
        target_p99 = ELEVATION_TARGET_P99
        if p99 >= target_p99 or p99 <= p90:
            return
        # 线性拉伸 (p90, p99] → (p90, target_p99]
        scale = (target_p99 - p90) / (p99 - p90)
        for i in range(len(elevation)):
            if land_mask[i] and elevation[i] > p90:
                elevation[i] = p90 + (elevation[i] - p90) * scale

    def _inject_missing_climates(
        self,
        elevation: list[float],
        temp: list[float],
        rain: list[float],
        land_mask: list[bool],
        climate_field: list[int],
        w: int, h: int,
    ) -> None:
        """兜底注入 — 对缺失气候档位，在最近邻区域创建最小气候种子。

        分位数拉伸解决了大部分 seed 的气候覆盖，但极端干旱/偏冷 seed
        仍可能缺失某些档位（温度-降雨空间分布天生不配合）。
        本函数在最接近目标档位阈值的陆地像素周围 3×3 区域
        直接设置参数，强制其落入目标档位。

        仅改 9 像素（0.09 km²），在 100×60km 大陆上几乎不可见，
        但保证大地图俯瞰时 8 种颜色都存在。在水文计算后执行，
        不影响河流树/湖泊/流向。
        """
        from .climate import classify
        n = w * h

        # 各档位目标参数（判定阈值中间值，确保落入该档位）
        targets = {
            0: (25.0, 2000.0, 200.0),    # 热带雨林
            1: (25.0, 1000.0, 200.0),    # 热带草原
            2: (20.0, 100.0, 200.0),     # 沙漠
            3: (15.0, 400.0, 200.0),     # 草原
            4: (12.0, 800.0, 200.0),     # 温带森林
            5: (-2.0, 500.0, 200.0),     # 亚寒带针叶林
            6: (-10.0, 300.0, 200.0),    # 极地苔原
            7: (10.0, 800.0, 2500.0),    # 高山
        }

        present = set(climate_field[i] for i in range(n) if land_mask[i])
        missing = list(set(targets.keys()) - present)

        if not missing:
            return

        # 单次扫描找到每个缺失档位的最近邻
        inv30 = 1.0 / 30.0
        inv2000 = 1.0 / 2000.0
        inv3000 = 1.0 / 3000.0
        best_i = {mz: -1 for mz in missing}
        best_d = {mz: float("inf") for mz in missing}

        for i in range(n):
            if not land_mask[i]:
                continue
            t = temp[i]
            r = rain[i]
            e = elevation[i]
            for mz in missing:
                tt, tr, ta = targets[mz]
                dt = (t - tt) * inv30
                dr = (r - tr) * inv2000
                de = (e - ta) * inv3000
                d = dt * dt + dr * dr + de * de
                if d < best_d[mz]:
                    best_d[mz] = d
                    best_i[mz] = i

        for mzone in missing:
            tt, tr, ta = targets[mzone]
            best_idx = best_i[mzone]
            if best_idx < 0:
                continue
            # 在候选位置周围 3×3 注入目标参数
            gx, gy = best_idx % w, best_idx // w
            for dy in range(-1, 2):
                for dx in range(-1, 2):
                    nx, ny = gx + dx, gy + dy
                    if 0 <= nx < w and 0 <= ny < h:
                        ni = ny * w + nx
                        if land_mask[ni]:
                            temp[ni] = tt
                            rain[ni] = tr
                            elevation[ni] = max(elevation[ni], ta)
                            climate_field[ni] = int(mzone)

    # ── 群系细分动态值域 ────────────────────────────────────

    @staticmethod
    def _compute_subdiv_ranges(
        elevation: list[float],
        temp: list[float],
        rain: list[float],
        land_mask: list[bool],
        climate_field: list[int],
        w: int, h: int,
    ) -> dict[int, tuple[float, float]]:
        """计算每气候档内细分维度的 P10/P90 值域。

        供 biome_membership 动态归一化用，使档内两子型比例均衡。
        沙漠档用 moisture 噪声细分，此处不计算（噪声值域固定 [-1,1]）。

        Returns:
            {ClimateZone_int: (P10, P90)} 每档的细分值域。
        """
        from .climate import ClimateZone
        from .biome import _SUBDIV_CONFIGS, _SUBDIV_MOISTURE

        # 按档收集细分维度值
        zone_vals: dict[int, list[float]] = {}
        for i in range(w * h):
            if not land_mask[i]:
                continue
            cz = climate_field[i]
            cfg = _SUBDIV_CONFIGS.get(ClimateZone(cz))
            if cfg is None or cfg.dimension == _SUBDIV_MOISTURE:
                continue
            if cfg.dimension == "rainfall":
                zone_vals.setdefault(cz, []).append(rain[i])
            elif cfg.dimension == "temperature":
                zone_vals.setdefault(cz, []).append(temp[i])
            elif cfg.dimension == "altitude":
                zone_vals.setdefault(cz, []).append(elevation[i])

        # P10/P90
        ranges: dict[int, tuple[float, float]] = {}
        for cz_int, vals in zone_vals.items():
            if len(vals) < 10:
                continue
            vals.sort()
            n = len(vals)
            p10 = vals[int(n * 0.10)]
            p90 = vals[int(n * 0.90)]
            if p90 - p10 < 1.0:
                # 值域过窄（档内几乎无变化），用 min/max
                p10 = vals[0]
                p90 = vals[-1]
            ranges[cz_int] = (p10, p90)

        return ranges

    # ── 海拔生成 ──────────────────────────────────────────

    def _generate_elevation(
        self, w: int, h: int,
    ) -> tuple[list[bool], list[float]]:
        """两层 Perlin 噪声 → 海拔 + 陆地。

        大陆轮廓层（低频）：决定海陆分布的大洲形状。
        地形细节层（高频）：叠加山地丘陵等局部变化。
        温和的中心倾向避免"四周陆地中间海洋"的环形分布。
        分位数校准确保陆地比例稳定在 land_ratio。
        """
        noise_terrain = PerlinNoise(self._seed + 10002)
        noise_continent = PerlinNoise(self._seed + 10003)

        terrain_freq = self._params.sample_resolution / 30000.0
        terrain_field = noise_terrain.octave_grid(
            0.5, 0.5, w, h, frequency=terrain_freq, octaves=5,
        )

        # 大陆轮廓层：绝对频率（1.5 周期 / 100km），与网格宽解耦。
        # 尺寸只改变生成范围——大尺寸下大陆轮廓自然延伸，
        # 而非把同一形状按比例缩放。
        continent_freq = self._params.sample_resolution / 100_000.0 * 1.5
        continent_field = noise_continent.octave_grid(
            0.5, 0.5, w, h, frequency=continent_freq, octaves=2,
        )

        n = w * h
        mixed = [0.0] * n
        inv_w = 1.0 / w
        inv_h = 1.0 / h

        # 用累加索引替代每像素的取模运算
        i = 0
        for y in range(h):
            dy = (y * inv_h - 0.5) * 2.0
            for x in range(w):
                dx = (x * inv_w - 0.5) * 2.0
                dist = center_distance(dx, dy)
                center = 1.0 - dist * 2.5
                if center < 0.0:
                    center = 0.0
                mixed[i] = (continent_field[i] * CONTINENT_BLEND_WEIGHT
                            + terrain_field[i] * TERRAIN_BLEND_WEIGHT
                            + center * CENTER_BIAS_WEIGHT)
                i += 1

        target = self._params.land_ratio
        sorted_vals = sorted(mixed)
        sea_idx = int(n * (1.0 - target))
        sea_idx = max(0, min(n - 1, sea_idx))
        sea_level = sorted_vals[sea_idx]

        # 列表推导 — 比 .append() 循环快
        elevation = [(m - sea_level) * ELEVATION_SCALE_FACTOR for m in mixed]
        land_mask = [e > 0 for e in elevation]
        return land_mask, elevation

    # ── 气候计算 ──────────────────────────────────────────

    def _compute_climate(
        self, elevation: list[float], land_mask: list[bool], w: int, h: int,
    ) -> tuple[list[float], list[float], list[int]]:
        """计算温度、降雨、气候带。

        温度 = 海平面纬度温度 - 海拔 × 9.0°C/km - 大陆度修正
        （直减率仅作用于陆地；海域 = 海面温度，clamp [-20, 38]）
        降雨 = 噪声 × 雨影因子（水分预算追踪，海域仅继承陆地抬升衰减）

        温度基线由 seed 决定的方向梯度给出，往某方向走持续变暖、反方向变冷。
        大陆度修正：距海越远年均温越低（海洋调节缺失，冬季降温主导年均值）。
        叠加微量噪声使气候带边界自然蜿蜒。

        Returns:
            (temp_field, rain_field, climate_field) 三个行优先数组；
            气候档已转 int。
        """
        import math

        # seed → 随机温度梯度方向
        angle = seed_angle(self._seed)
        gx = math.cos(angle)
        gy = math.sin(angle)

        lat_wiggle = PerlinNoise(self._seed + 99999)
        lat_wiggle_field = lat_wiggle.octave_grid(
            0.5, 0.5, w, h,
            frequency=self._params.sample_resolution / 15000.0, octaves=1,
        )

        rain_noise = PerlinNoise(self._seed + 88888)
        rain_field_raw = rain_noise.octave_grid(
            0.5, 0.5, w, h,
            frequency=self._params.sample_resolution / 25000.0, octaves=3,
        )

        rain_shadow = self._compute_rain_shadow(elevation, w, h)

        # 距海距离
        from .hydrology import _distance_to_ocean_c
        elev_arr = array('d', elevation)
        dist_to_ocean = _distance_to_ocean_c(elev_arr, w, h)

        # 气候计算（温度、降雨、气候分类）
        from .hydrology import _compute_climate_c
        lat_arr = array('d', lat_wiggle_field)
        rain_raw_arr = array('d', rain_field_raw)
        shadow_arr = array('d', rain_shadow)
        temp_field, rain_field, climate_field = _compute_climate_c(
            elev_arr, lat_arr, rain_raw_arr, shadow_arr, dist_to_ocean,
            w, h, gx, gy,
            continentality_k=CONTINENTALITY_K,
            continentality_d0=CONTINENTALITY_D0_KM,
            cell_size_km=self._params.sample_resolution / 1000.0,
        )

        # convert climate to int (from array)
        climate_field = [int(c) for c in climate_field]

        return temp_field, rain_field, climate_field

    def _compute_rain_shadow(
        self, elevation: list[float], w: int, h: int,
    ) -> list[float]:
        """雨影因子：万向盛行风 + 水分预算追踪。

        seed 决定连续风向角 [0, 2π)，主风向（80%）+ 次风向偏移 45°（20%）混合。
        使用水分预算模型：风携带水汽从海岸向内陆移动，
        地形抬升消耗水汽 → 背风面干燥。
        海洋格无地形抬升（负海拔不产生伪抬升），仅继承上风陆地的
        衰减抬升——近岸海域保留干燥气团出海的残余雨影。
        因子范围 [MIN_FACTOR, 1.0]，保证基础降水。
        """
        import math
        from .hydrology import _rain_shadow_omnidirectional_c

        # seed → 连续风向角（与温度梯度相同的 Knuth 乘法哈希）
        wind_angle = seed_angle(self._seed)

        # 次风向：偏移 45°，模拟环境风切变
        secondary_angle = wind_angle + math.pi / 4.0

        elev_arr = array('d', elevation)
        factors = _rain_shadow_omnidirectional_c(
            elev_arr, w, h,
            primary_angle=wind_angle,
            secondary_angle=secondary_angle,
            secondary_weight=RAINSHADOW_SECONDARY_WEIGHT,
            decay_length_km=RAINSHADOW_DECAY_KM,   # 抬升衰减距离 (km)
            cell_size_km=self._params.sample_resolution / 1000.0,
            min_factor=RAINSHADOW_MIN_FACTOR,
        )
        return factors.tolist()


__all__ = ["ContinentParams", "ContinentData", "ContinentGenerator"]
