"""Tile 粒度完整地图渲染 — 将指定区域的 chunk 拼接为一张 PNG，海拔渐变色。

用法:
    cd backend && PYTHONPATH=. ../.venv/bin/python tests/visual/render_tiles.py
"""

import os
from ascend.space.continent import ContinentGenerator
from ascend.space.tile_gen import TileGenerator
from ascend.space.tile_grid import TILE_MAP_SIZE

# 海拔渐变色标 (threshold_m, (R, G, B)) — 与 render.py 一致
_COLOR_STOPS: list[tuple[float, tuple[int, int, int]]] = [
    (-4000.0, (10, 20, 60)),     # 深海 — 深蓝黑
    (-2000.0, (26, 58, 92)),     # 深海 — 深蓝
    (-800.0, (60, 120, 180)),    # 中海 — 蓝
    (-150.0, (90, 160, 210)),    # 浅海 — 亮蓝
    (-30.0, (126, 200, 227)),    # 极浅海 — 青蓝
    (-3.0, (180, 210, 200)),     # 潮间带 — 浅青
    (5.0, (212, 184, 150)),      # 海滩 — 沙色
    (60.0, (180, 200, 100)),     # 海岸低地 — 浅绿
    (200.0, (122, 173, 79)),     # 低地 — 绿色
    (600.0, (140, 160, 80)),     # 丘陵 — 黄绿
    (1200.0, (184, 154, 107)),   # 高地 — 棕色
    (2000.0, (160, 140, 110)),   # 山地 — 深棕
    (2800.0, (158, 158, 158)),   # 高山 — 灰色
    (4000.0, (240, 240, 240)),   # 高峰 — 白色
]

OUTPUT_DIR = os.path.join(os.path.dirname(__file__), "output")


def _elevation_to_rgb(elev: float) -> tuple[int, int, int]:
    """海拔 → RGB 颜色（相邻色标之间线性插值）。"""
    stops = _COLOR_STOPS
    if elev <= stops[0][0]:
        return stops[0][1]
    if elev >= stops[-1][0]:
        return stops[-1][1]
    for i in range(len(stops) - 1):
        e_lo, c_lo = stops[i]
        e_hi, c_hi = stops[i + 1]
        if e_lo <= elev <= e_hi:
            t = (elev - e_lo) / (e_hi - e_lo)
            r = int(c_lo[0] + (c_hi[0] - c_lo[0]) * t)
            g = int(c_lo[1] + (c_hi[1] - c_lo[1]) * t)
            b = int(c_lo[2] + (c_hi[2] - c_lo[2]) * t)
            return (r, g, b)
    return stops[-1][1]


def render_tile_region(
    seed: int = 42,
    chunk_x0: int = 180,
    chunk_y0: int = 100,
    chunk_cols: int = 25,
    chunk_rows: int = 15,
    output_name: str = "tile_map.png",
) -> None:
    """渲染指定 chunk 区域的 tile 粒度地图，用海拔渐变着色。

    Args:
        seed: 世界种子。
        chunk_x0, chunk_y0: 起始 chunk 坐标。
        chunk_cols, chunk_rows: chunk 列数和行数。
        output_name: 输出文件名。
    """
    from PIL import Image

    print(f"生成大陆数据 (seed={seed})...")
    cont = ContinentGenerator(seed=seed).generate()
    print(f"  大陆: {cont}")

    tile_gen = TileGenerator(seed=seed, continent=cont)
    detail_freq = 0.005  # 与 tile_gen 一致
    noise = tile_gen._detail_noise

    tile_size = TILE_MAP_SIZE
    total_w = chunk_cols * tile_size
    total_h = chunk_rows * tile_size
    print(f"渲染区域: {chunk_cols}×{chunk_rows} chunks = {total_w}×{total_h} tiles")
    print(f"世界坐标: ({chunk_x0 * tile_size}, {chunk_y0 * tile_size}) → "
          f"({(chunk_x0 + chunk_cols) * tile_size}, {(chunk_y0 + chunk_rows) * tile_size})")

    img = Image.new("RGB", (total_w, total_h))
    pixels = img.load()

    chunk_count = 0
    for cy in range(chunk_rows):
        for cx in range(chunk_cols):
            chunk_cx = chunk_x0 + cx
            chunk_cy = chunk_y0 + cy
            world_x0 = chunk_cx * tile_size
            world_y0 = chunk_cy * tile_size

            # 一次 C 调用获取整个 chunk 的细节噪声
            noise_field = noise.octave_grid(
                world_x0 + 0.5, world_y0 + 0.5, tile_size, tile_size,
                frequency=detail_freq, octaves=4,
            )

            x_offset = cx * tile_size
            y_offset = cy * tile_size
            for ty in range(tile_size):
                row_base = ty * tile_size
                wy = world_y0 + ty
                for tx in range(tile_size):
                    wx = world_x0 + tx
                    macro = cont.sample_altitude_bilinear(wx, wy)
                    detail = noise_field[row_base + tx] * 100.0
                    elev = macro + detail

                    # 海拔渐变色
                    color = _elevation_to_rgb(elev)

                    # 叠加河道/湖泊——河宽越大越蓝
                    rw = cont.sample_river_width(wx, wy)
                    if rw > 0.5 and elev > -20:
                        # 即使 2m 宽小河也以 40% 混合，大河道全蓝
                        blend = min(1.0, 0.4 + rw / 20.0)
                        river_color = (30, 80, 220)
                        r = int(color[0] + (river_color[0] - color[0]) * blend)
                        g = int(color[1] + (river_color[1] - color[1]) * blend)
                        b = int(color[2] + (river_color[2] - color[2]) * blend)
                        color = (r, g, b)

                    pixels[x_offset + tx, y_offset + ty] = color

            chunk_count += 1
            if chunk_count % 50 == 0:
                print(f"  进度: {chunk_count}/{chunk_cols * chunk_rows} chunks")

    print(f"  完成: {chunk_count} chunks, {total_w * total_h:,} tiles")
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    out_path = os.path.join(OUTPUT_DIR, output_name)
    img.save(out_path)
    print(f"[tile] 保存: {out_path}")
    print(f"  尺寸: {total_w}×{total_h} px")


def render_full_overview(seed: int = 42) -> None:
    """渲染多张不同区域的全景图。"""
    render_tile_region(
        seed=seed,
        chunk_x0=22, chunk_y0=12,
        chunk_cols=30, chunk_rows=20,
        output_name="tile_coast.png",
    )
    render_tile_region(
        seed=seed,
        chunk_x0=50, chunk_y0=30,
        chunk_cols=30, chunk_rows=20,
        output_name="tile_inland.png",
    )


def render_river_map(seed: int = 42) -> None:
    """渲染 continent 分辨率的河道+湖泊宽度图。"""
    from PIL import Image

    print(f"生成大陆数据 (seed={seed})...")
    cont = ContinentGenerator(seed=seed).generate()
    w, h = cont.grid_width, cont.grid_height
    rw = cont.river_width

    # 找到最大河宽用于归一化
    max_rw = max(rw) if rw else 1.0

    img = Image.new("RGB", (w, h))
    pixels = img.load()
    for y in range(h):
        for x in range(w):
            v = rw[y * w + x]
            if v <= 0:
                # 陆地：暗绿色背景
                pixels[x, y] = (30, 50, 20)
            else:
                # 河道/湖泊：蓝白渐变，越宽越亮
                t = min(1.0, v / max_rw)
                r = int(20 + 60 * t)
                g = int(60 + 140 * t)
                b = int(160 + 95 * t)
                pixels[x, y] = (r, g, b)

    os.makedirs(OUTPUT_DIR, exist_ok=True)
    out_path = os.path.join(OUTPUT_DIR, "river_network.png")
    img.save(out_path)
    print(f"[tile] 河道网络保存: {out_path} ({w}×{h} px)")


if __name__ == "__main__":
    render_river_map()
    render_full_overview()
