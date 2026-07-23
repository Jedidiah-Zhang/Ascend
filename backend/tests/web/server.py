"""世界地图可视化 Web 服务器 — 开发调试用。

用法:
    cd backend && PYTHONPATH=. python tests/web/server.py
    浏览器打开 http://localhost:8080
"""

import json
import sys
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler
from socketserver import ThreadingMixIn


class ThreadingHTTPServer(ThreadingMixIn, HTTPServer):
    """多线程 HTTP 服务器 — 并发处理请求。"""
    daemon_threads = True
from pathlib import Path
from urllib.parse import urlparse, parse_qs
from concurrent.futures import ThreadPoolExecutor

# 确保 backend 在 sys.path 中
_HERE = Path(__file__).resolve().parent
_BACKEND = _HERE.parent.parent
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

from ascend.space import WorldGenerator, BiomeType, ClimateZone, TileGenerator

# ── 颜色方案 ──────────────────────────────────────────────────

BIOME_COLORS = {
    BiomeType.TROPICAL_MONSOON_FOREST:    "#2a8b3a",
    BiomeType.TROPICAL_RAINFOREST:        "#1a6b3a",
    BiomeType.TROPICAL_SAVANNA:           "#c4a43e",
    BiomeType.TROPICAL_WOODLAND:          "#5a8c3a",
    BiomeType.SANDY_DESERT:               "#e6c878",
    BiomeType.ROCKY_DESERT:               "#b8a060",
    BiomeType.SHORT_GRASS_STEPPE:         "#b8a060",
    BiomeType.TALL_GRASS_STEPPE:          "#8caa5a",
    BiomeType.TEMPERATE_MIXED_FOREST:     "#3a7c4f",
    BiomeType.TEMPERATE_DECIDUOUS_FOREST: "#4a7c3f",
    BiomeType.BOREAL_WETLAND:             "#4a7a8a",
    BiomeType.BOREAL_FOREST:              "#3a6a8a",
    BiomeType.POLAR_BARREN:               "#e0e0e8",
    BiomeType.TUNDRA:                     "#d8d8e8",
    BiomeType.ALPINE_MEADOW:              "#b0b0c0",
    BiomeType.ALPINE_BARREN:              "#909098",
    BiomeType.WARM_OCEAN:                 "#1e6b8a",
    BiomeType.TEMPERATE_OCEAN:            "#2e6b8a",
    BiomeType.COLD_OCEAN:                 "#5a8aaa",
}

CLIMATE_COLORS = {
    ClimateZone.EQUATORIAL_RAINFOREST: "#1a6b3a",
    ClimateZone.TROPICAL_SAVANNA: "#c4a43e",
    ClimateZone.DESERT: "#e6c878",
    ClimateZone.STEPPE: "#b8a060",
    ClimateZone.TEMPERATE_FOREST: "#4a7c3f",
    ClimateZone.SUBARCTIC_TAIGA: "#3a6a8a",
    ClimateZone.POLAR_TUNDRA: "#d8d8e8",
    ClimateZone.ALPINE: "#b0b0c0",
}

# 地形颜色映射 — 9 种 TerrainType → RGB
TERRAIN_COLORS: dict[int, tuple[int, int, int]] = {
    0: (126, 200, 80),    # 草地
    1: (232, 213, 163),   # 沙地
    2: (92, 61, 46),      # 沃土
    3: (139, 139, 139),   # 岩石地
    4: (107, 107, 107),   # 陡坡
    5: (224, 224, 224),   # 山巅
    6: (91, 158, 207),    # 浅水
    7: (26, 58, 92),      # 深水
    8: (74, 107, 58),     # 沼泽
}

# ── WorldGenerator 缓存 ─────────────────────────────────────

_lock = threading.Lock()
_generators: dict[int, WorldGenerator] = {}
_executor = ThreadPoolExecutor(max_workers=8)

# 多进程池 — 绕过 GIL 并行生成瓦片像素
from concurrent.futures import ProcessPoolExecutor as _PPE
import os as _os
_proc_pool = _PPE(max_workers=min(8, _os.cpu_count() or 4))

# 服务端瓦片缓存 — 一次生成 5 层，按需返回单层
_tile_cache: dict[tuple, bytes] = {}   # key: (seed, cx, cy, w, h) → 5层 raw bytes
_tile_cache_lock = threading.Lock()
_MAX_SERVER_TILES = 500
_LAYER_SIZE = None  # 运行时计算

# 地形瓦片缓存 — key: (seed, cx, cy) → RGBA bytes (200×200×4)
_terrain_cache: dict[tuple, bytes] = {}
_terrain_cache_lock = threading.Lock()
_MAX_TERRAIN_TILES = 200


def _get_generator(seed: int) -> WorldGenerator:
    """按 seed 获取或创建 WorldGenerator 实例（线程安全）。"""
    with _lock:
        if seed not in _generators:
            _generators[seed] = WorldGenerator(seed, executor=_executor)
        return _generators[seed]


# ── 地图数据生成 ─────────────────────────────────────────────


def _build_biome_grid(gen: WorldGenerator, cx: int, cy: int, w: int, h: int) -> dict:
    """生成群系网格。并行调用 get_biome（纯读，无共享状态）。"""
    from concurrent.futures import as_completed

    coords = [(cx + col, cy + row) for row in range(h) for col in range(w)]
    results: dict[tuple[int, int], int] = {}

    futures = {}
    for coord in coords:
        future = _executor.submit(gen.get_biome, coord[0], coord[1])
        futures[future] = coord

    for future in as_completed(futures):
        coord = futures[future]
        results[coord] = int(future.result())

    grid = [[results[(cx + col, cy + row)] for col in range(w)] for row in range(h)]

    legend = {}
    for bt, color in BIOME_COLORS.items():
        legend[int(bt)] = {"label": bt.label, "color": color}
    return {"grid": grid, "legend": legend}


def _build_climate_grid(gen: WorldGenerator, cx: int, cy: int, w: int, h: int) -> dict:
    """生成气候网格。并行调用 get_climate（纯读，无共享状态）。"""
    from concurrent.futures import as_completed

    coords = [(cx + col, cy + row) for row in range(h) for col in range(w)]
    results: dict[tuple[int, int], int] = {}

    futures = {}
    for coord in coords:
        future = _executor.submit(gen.get_climate, coord[0], coord[1])
        futures[future] = coord

    for future in as_completed(futures):
        coord = futures[future]
        results[coord] = int(future.result())

    grid = [[results[(cx + col, cy + row)] for col in range(w)] for row in range(h)]

    legend = {}
    for cz, color in CLIMATE_COLORS.items():
        legend[int(cz)] = {"label": cz.label, "color": color}
    return {"grid": grid, "legend": legend}


def _build_continuous_grid(
    gen: WorldGenerator, cx: int, cy: int, w: int, h: int, field: str
) -> dict:
    """生成连续值网格（海拔/温度/降雨等）。需要完整区块生成。

    Args:
        gen: WorldGenerator 实例。
        cx, cy: 起始区块坐标。
        w, h: 网格宽高（区块数）。
        field: 要提取的字段名（altitude / temperature / rainfall / sunshine / humidity / wind_speed）。

    Returns:
        包含 grid（二维浮点数数组）和 color_scale 信息的字典。
    """
    # 收集所有需要的区块坐标
    coords = [(cx + col, cy + row) for row in range(h) for col in range(w)]

    # 并行生成
    chunks = gen.generate_parallel(coords, max_workers=8)

    # 提取值并组装网格
    values = [
        getattr(c.annual_baseline, field) if c else 0.0
        for c in chunks
    ]

    grid = []
    idx = 0
    for row in range(h):
        line = []
        for col in range(w):
            line.append(round(values[idx], 2))
            idx += 1
        grid.append(line)

    # 计算值域
    valid = [v for v in values if v is not None]
    vmin = min(valid) if valid else 0
    vmax = max(valid) if valid else 1

    return {
        "grid": grid,
        "range": {"min": round(vmin, 2), "max": round(vmax, 2)},
    }


def _build_chunk_detail(gen: WorldGenerator, cx: int, cy: int) -> dict:
    """获取单个区块的详细信息。"""
    chunk = gen.generate_chunk(cx, cy)
    w = chunk.annual_baseline
    return {
        "cx": chunk.cx,
        "cy": chunk.cy,
        "biome": {"id": int(chunk.biome), "label": chunk.biome.label},
        "climate_zone": {
            "id": int(chunk.climate_zone),
            "label": chunk.climate_zone.label,
        },
        "passable": chunk.passable,
        "travel_speed": chunk.travel_speed,
        "markers": chunk.markers,
        "weather": {
            "temperature": round(w.temperature, 2),
            "rainfall": round(w.rainfall, 2),
            "sunshine": round(w.sunshine, 2),
            "altitude": round(w.altitude, 2),
            "humidity": round(w.humidity, 2),
            "wind_speed": round(w.wind_speed, 2),
        },
    }


# ── 瓦片图片生成（服务端渲染为 RGBA 像素，跳过 JSON/客户端颜色计算） ─

# 固定值域（与前端 FIXED_RANGES 一致）
_TILE_RANGES: dict[str, tuple[float, float]] = {
    "altitude":    (-500.0, 5000.0),
    "temperature": (-30.0, 50.0),
    "rainfall":    (0.0, 5000.0),
}


def _color_for_biome(v: int) -> tuple[int, int, int]:
    """群系值 → RGB。"""
    colors = {
        0:  (74, 124, 63),    # 温带落叶林
        1:  (26, 107, 58),    # 热带雨林
        2:  (196, 164, 62),   # 热带草原
        3:  (230, 200, 120),  # 沙漠
        4:  (184, 160, 96),   # 灌木草原
        5:  (58, 106, 138),   # 针叶林
        6:  (216, 216, 232),  # 苔原
        7:  (176, 176, 192),  # 高山草甸
        10: (30, 107, 138),   # 暖水海洋
        11: (46, 107, 138),   # 温带海洋
        12: (90, 138, 170),   # 冷水海洋
    }
    return colors.get(v, (34, 34, 34))


def _color_for_climate(v: int) -> tuple[int, int, int]:
    """气候值 → RGB。"""
    colors = {
        0: (26, 107, 58),    # 热带雨林
        1: (196, 164, 62),   # 热带草原
        2: (230, 200, 120),  # 沙漠
        3: (184, 160, 96),   # 草原
        4: (74, 124, 63),    # 温带森林
        5: (58, 106, 138),   # 亚寒带针叶林
        6: (216, 216, 232),  # 极地苔原
        7: (176, 176, 192),  # 高山
    }
    return colors.get(v, (34, 34, 34))


def _color_for_continuous(v: float, mode: str) -> tuple[int, int, int]:
    """连续值 → RGB（简化版渐变，省去 HSL→RGB 的开销）。"""
    lo, hi = _TILE_RANGES.get(mode, (-500.0, 5000.0))
    t = max(0.0, min(1.0, (v - lo) / (hi - lo + 0.001)))
    if mode == "altitude":
        # 海拔渐变 — 海平面(alt=0, t≈0.091)处冷暖分明
        stops = [
            (0.000, (5, 12, 60)),     # -500m  深海
            (0.050, (18, 55, 130)),   # -225m  大洋
            (0.082, (40, 105, 185)),  # -50m   浅海
            (0.090, (65, 150, 230)),  # -5m    近岸(亮蓝)
            (0.091, (215, 195, 105)), # 0m     海岸线 ← 暖沙色
            (0.100, (155, 182, 85)),  # +50m   沿海草地
            (0.160, (105, 158, 52)),  # +380m  低地绿
            (0.320, (60, 122, 35)),   # +1260m 森林绿
            (0.520, (88, 106, 48)),   # +2360m 高地橄榄
            (0.680, (140, 120, 68)),  # +3240m 山腰棕
            (0.820, (175, 160, 140)), # +4010m 岩石灰
            (0.930, (215, 208, 200)), # +4615m 高山
            (1.000, (252, 250, 245)), # +5000m 雪峰
        ]
    else:
        # 蓝→青→绿→黄→红 HSL 模拟
        stops = [
            (0.00, (30, 60, 180)), (0.25, (40, 140, 180)),
            (0.50, (60, 170, 60)), (0.75, (200, 180, 50)),
            (1.00, (200, 40, 30)),
        ]
    lo_s, hi_s = stops[0], stops[-1]
    for i in range(1, len(stops)):
        if t <= stops[i][0]:
            lo_s, hi_s = stops[i - 1], stops[i]
            break
    dt = (t - lo_s[0]) / (hi_s[0] - lo_s[0] + 0.0001)
    return (
        int(lo_s[1][0] + (hi_s[1][0] - lo_s[1][0]) * dt),
        int(lo_s[1][1] + (hi_s[1][1] - lo_s[1][1]) * dt),
        int(lo_s[1][2] + (hi_s[1][2] - lo_s[1][2]) * dt),
    )


def _value_to_rgb(v: float | int, mode: str) -> tuple[int, int, int]:
    """模式无关的值 → RGB。"""
    if mode == "biome":
        return _color_for_biome(int(v))
    if mode == "climate":
        return _color_for_climate(int(v))
    return _color_for_continuous(float(v), mode)


def _tile_worker(seed: int, cx: int, cy: int, w: int, h: int, mode: str) -> bytes:
    """在子进程中生成瓦片原始数据（float32 数组）。

    返回 5 层: [海拔][温度][降雨][气候][群系] 各 float32。
    海拔使用 Voronoi 构造模拟，其余为零（待气候管线接入）。
    """
    import math
    import struct
    from ascend.space.tectonic import tectonic_altitude
    from ascend.space.climate import (
        sea_level_temperature, rainfall_from_noise, apply_lapse_rate,
        climate_zone_from_values,
    )
    from ascend.space.biome import biome_from_climate
    from ascend.space.noise import PerlinNoise

    phi = (math.sqrt(5.0) - 1.0) / 2.0
    seed_float = float(abs(seed) % 100000)
    phases = [
        ((seed_float + i * 137.5) * phi * 1000.0) % 9973.0
        for i in range(8)
    ]

    noise_lat = PerlinNoise(seed + 200)
    noise_rain = PerlinNoise(seed + 300)
    noise_moist = PerlinNoise(seed + 700)

    p_t = phases[1]
    lat_noise = noise_lat.octave_grid(int(cx + p_t), int(cy + p_t), w, h,
                                       frequency=0.0003, octaves=2)
    p_r = phases[2]
    rain_noise = noise_rain.octave_grid(int(cx + p_r), int(cy + p_r), w, h,
                                         frequency=0.004, octaves=4)
    p_m = phases[4]
    moist_noise = noise_moist.octave_grid(int(cx + p_m), int(cy + p_m), w, h,
                                           frequency=0.005, octaves=2)

    size = w * h
    packer = struct.Struct(f'{size}f')

    altitudes = [0.0] * size
    temps = [0.0] * size
    rains = [0.0] * size
    climates = [0.0] * size
    biomes = [0.0] * size

    for idx in range(size):
        world_tx = (cx + (idx % w)) * 200 + 100
        world_ty = (cy + (idx // w)) * 200 + 100
        altitude = tectonic_altitude(float(world_tx), float(world_ty), seed)

        sea_temp = sea_level_temperature(lat_noise[idx])
        rainfall = rainfall_from_noise(rain_noise[idx])
        temp = apply_lapse_rate(sea_temp, altitude)
        climate = climate_zone_from_values(temp, rainfall)
        m = moist_noise[idx]
        biome = biome_from_climate(climate, m, altitude, sea_temp)

        altitudes[idx] = altitude
        temps[idx] = temp
        rains[idx] = rainfall
        climates[idx] = float(int(climate))
        biomes[idx] = float(int(biome))

    return (
        packer.pack(*altitudes) +
        packer.pack(*temps) +
        packer.pack(*rains) +
        packer.pack(*climates) +
        packer.pack(*biomes)
    )


def _terrain_worker(seed: int, cx: int, cy: int) -> bytes:
    """生成单 chunk 的 200×200 地形 RGBA 像素数据。

    使用 Voronoi 构造海拔 + 简单的海拔分带着色。
    """
    from ascend.space.tectonic import tectonic_altitude_batch

    size = 200
    n = size * size
    world_x = cx * size
    world_y = cy * size

    # 构造海拔 — tile 粒度
    alts = tectonic_altitude_batch(world_x, world_y, size, size, seed)

    # 简单海拔分带着色
    terrain_colors = {
        0: (126, 200, 80),    # 草地
        1: (232, 213, 163),   # 沙地
        2: (92, 61, 46),      # 沃土
        3: (139, 139, 139),   # 岩石地
        4: (107, 107, 107),   # 陡坡
        5: (224, 224, 224),   # 山巅
        6: (91, 158, 207),    # 浅水
        7: (26, 58, 92),      # 深水
        8: (74, 107, 58),     # 沼泽
        9: (70, 140, 210),    # 河流蓝
    }

    rgba = bytearray(n * 4)
    for i, alt in enumerate(alts):
        if alt < -500:
            t = 7  # 深水
        elif alt < 0:
            t = 6  # 浅水
        elif alt < 2:
            t = 1  # 沙地（海滩）
        elif alt < 80:
            t = 0  # 草地
        elif alt < 300:
            t = 2  # 沃土（低坡）
        elif alt < 800:
            t = 3  # 岩石地
        elif alt < 2000:
            t = 4  # 陡坡
        else:
            t = 5  # 山巅
        r, g, b = terrain_colors.get(t, (0, 0, 0))
        p = i * 4
        rgba[p] = r
        rgba[p + 1] = g
        rgba[p + 2] = b
        rgba[p + 3] = 255

    return bytes(rgba)


# ── HTTP Handler ─────────────────────────────────────────────

_STATIC_DIR = _HERE / "static"


class MapHandler(BaseHTTPRequestHandler):
    """世界地图 API 请求处理器。"""

    def log_message(self, format, *args):
        """重写日志格式，更简洁。"""
        sys.stderr.write("[%s] %s\n" % (self.address_string(), format % args))

    def do_GET(self):
        """处理 GET 请求。"""
        parsed = urlparse(self.path)
        params = parse_qs(parsed.query)

        # 将单值参数展平
        flat = {k: v[0] if len(v) == 1 else v for k, v in params.items()}

        if parsed.path == "/" or parsed.path == "/index.html":
            self._serve_file("index.html", "text/html; charset=utf-8")
        elif parsed.path == "/api/map":
            self._handle_map(flat)
        elif parsed.path == "/api/tile":
            self._handle_tile(flat)
        elif parsed.path == "/api/chunk":
            self._handle_chunk(flat)
        elif parsed.path == "/api/terrain-tile":
            self._handle_terrain_tile(flat)
        else:
            self.send_error(404, "Not Found")

    def _serve_file(self, filename: str, content_type: str):
        """提供静态文件。"""
        filepath = _STATIC_DIR / filename
        if not filepath.is_file():
            self.send_error(404, "File not found")
            return
        data = filepath.read_bytes()
        mtime = filepath.stat().st_mtime
        etag = f'"{int(mtime)}"'

        # 检查条件请求
        if self.headers.get("If-None-Match") == etag:
            self.send_response(304)
            self.end_headers()
            return

        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-cache, must-revalidate")
        self.send_header("ETag", etag)
        self.end_headers()
        self.wfile.write(data)

    def _handle_map(self, params: dict):
        """处理 /api/map 请求。

        Query 参数:
            seed: 世界种子（默认 0）。
            cx: 中心区块 X（默认 0）。
            cy: 中心区块 Y（默认 0）。
            w: 网格宽度 — 区块数（默认 60）。
            h: 网格高度 — 区块数（默认 40）。
            mode: 视图模式 — biome / climate / altitude / temperature / rainfall
                  （默认 biome）。
        """
        try:
            seed = int(params.get("seed", 0))
            w = min(int(params.get("w", 60)), 600)
            h = min(int(params.get("h", 40)), 600)
            mode = params.get("mode", "biome")
        except (ValueError, TypeError):
            self.send_error(400, "Invalid numeric parameter")
            return

        # 计算左上角坐标（cx, cy 指定的是左上角而非中心）
        cx = int(params.get("cx", -w // 2))
        cy = int(params.get("cy", -h // 2))

        gen = _get_generator(seed)

        try:
            if mode == "biome":
                result = _build_biome_grid(gen, cx, cy, w, h)
            elif mode == "climate":
                result = _build_climate_grid(gen, cx, cy, w, h)
            elif mode in ("altitude", "temperature", "rainfall",
                          "sunshine", "humidity", "wind_speed"):
                result = _build_continuous_grid(gen, cx, cy, w, h, mode)
            else:
                self.send_error(400, f"Unknown mode: {mode}")
                return
        except Exception as exc:
            self.send_error(500, f"Generation error: {exc}")
            return

        result.update({"mode": mode, "cx": cx, "cy": cy, "w": w, "h": h, "seed": seed})

        body = json.dumps(result, ensure_ascii=False).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        self.wfile.write(body)

    def _handle_tile(self, params: dict):
        """处理 /api/tile 请求 — 多进程生成，返回原始 RGBA 像素数据。

        Query 参数: seed, cx, cy, w, h, mode
        Content-Type: application/octet-stream
        Body: w*h*4 bytes RGBA
        """
        try:
            seed = int(params.get("seed", 0))
            w = min(int(params.get("w", 100)), 256)
            h = min(int(params.get("h", 100)), 256)
            mode = params.get("mode", "biome")
            cx = int(params.get("cx", 0))
            cy = int(params.get("cy", 0))
        except (ValueError, TypeError):
            self.send_error(400, "Invalid numeric parameter")
            return

        cache_key = (seed, cx, cy, w, h)
        with _tile_cache_lock:
            full_data = _tile_cache.get(cache_key)

        if full_data is None:
            try:
                future = _proc_pool.submit(_tile_worker, seed, cx, cy, w, h, mode)
                full_data = future.result(timeout=30)
            except Exception as exc:
                self.send_error(500, f"Generation error: {exc}")
                return
            with _tile_cache_lock:
                if len(_tile_cache) >= _MAX_SERVER_TILES:
                    _tile_cache.pop(next(iter(_tile_cache)))
                _tile_cache[cache_key] = full_data

        # 返回全部 5 层（客户端缓存后模式切换无需请求）
        rgba = full_data

        self.send_response(200)
        self.send_header("Content-Type", "application/octet-stream")
        self.send_header("Content-Length", str(len(rgba)))
        self.send_header("Cache-Control", "no-cache")
        self.send_header("X-Tile-W", str(w))
        self.send_header("X-Tile-H", str(h))
        self.end_headers()
        self.wfile.write(rgba)

    def _handle_chunk(self, params: dict):
        """处理 /api/chunk 请求。

        Query 参数:
            seed: 世界种子（默认 0）。
            cx: 区块 X。
            cy: 区块 Y。
        """
        try:
            seed = int(params.get("seed", 0))
            cx = int(params.get("cx", 0))
            cy = int(params.get("cy", 0))
        except (ValueError, TypeError):
            self.send_error(400, "Invalid numeric parameter")
            return

        gen = _get_generator(seed)
        try:
            detail = _build_chunk_detail(gen, cx, cy)
        except Exception as exc:
            self.send_error(500, f"Generation error: {exc}")
            return

        detail["seed"] = seed

        body = json.dumps(detail, ensure_ascii=False).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        self.wfile.write(body)

    def _handle_terrain_tile(self, params: dict):
        """处理 /api/terrain-tile 请求 — 返回单 chunk 的 200×200 地形 RGBA。

        Query 参数:
            seed: 世界种子（默认 0）。
            cx: 区块 X。
            cy: 区块 Y。
        """
        try:
            seed = int(params.get("seed", 0))
            cx = int(params.get("cx", 0))
            cy = int(params.get("cy", 0))
        except (ValueError, TypeError):
            self.send_error(400, "Invalid numeric parameter")
            return

        cache_key = (seed, cx, cy)
        with _terrain_cache_lock:
            rgba = _terrain_cache.get(cache_key)

        if rgba is None:
            try:
                future = _proc_pool.submit(_terrain_worker, seed, cx, cy)
                rgba = future.result(timeout=30)
            except Exception as exc:
                self.send_error(500, f"Terrain generation error: {exc}")
                return
            with _terrain_cache_lock:
                if len(_terrain_cache) >= _MAX_TERRAIN_TILES:
                    _terrain_cache.pop(next(iter(_terrain_cache)))
                _terrain_cache[cache_key] = rgba

        self.send_response(200)
        self.send_header("Content-Type", "application/octet-stream")
        self.send_header("Content-Length", str(len(rgba)))
        self.send_header("Cache-Control", "no-cache")
        self.send_header("X-Tile-Size", "200")
        self.end_headers()
        self.wfile.write(rgba)


# ── 入口 ──────────────────────────────────────────────────────

def run_server(host: str = "127.0.0.1", port: int = 8080):
    """启动 Web 服务器。

    Args:
        host: 监听地址。
        port: 监听端口。
    """
    server = ThreadingHTTPServer((host, port), MapHandler)
    print(f"世界地图可视化服务器已启动: http://{host}:{port}")
    print("按 Ctrl+C 停止")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n服务器已停止")
        server.shutdown()
        _executor.shutdown(wait=False)
        _proc_pool.shutdown(wait=False)


if __name__ == "__main__":
    run_server()
