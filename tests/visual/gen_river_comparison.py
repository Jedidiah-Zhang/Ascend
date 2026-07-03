"""生成河流流线渲染对比图。

输出:
  11_streamlines_overview.png — 层1 流线河流网络全局图(海拔底图+流线叠加)
  12_river_meander.png        — 单条弯曲河流的层1走向特写
  13_tile_river_meander.png   — 该河流穿过的 chunk 的 tile 级渲染
"""
import math
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_BACKEND = _HERE.parent.parent / "ascend-backend"
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

from ascend.space.continent import ContinentGenerator
from ascend.space.tile_gen import TileGenerator
from ascend.space.terrain import TerrainType
from PIL import Image, ImageDraw

OUT_DIR = _HERE / "output"
OUT_DIR.mkdir(parents=True, exist_ok=True)


def _elev_to_rgb(e: float) -> tuple[int, int, int]:
    """海拔→RGB(与 render.py 一致)。"""
    if e < -300:
        return (5, 12, 60)
    if e < -100:
        return (18, 55, 130)
    if e < -30:
        return (40, 105, 185)
    if e < 0:
        return (65, 150, 230)
    if e < 200:
        return (155, 182, 85)
    if e < 500:
        return (105, 158, 52)
    if e < 1000:
        return (60, 122, 35)
    if e < 2000:
        return (88, 106, 48)
    if e < 3500:
        return (140, 120, 68)
    return (215, 208, 200)


def main() -> None:
    seed = 42
    data = ContinentGenerator(seed=seed).generate()
    w, h = data.grid_width, data.grid_height
    net = data.hydrology.river_network
    print(f"流线网络: {net}")

    # ── 图 11: 流线河流网络全局图 ──
    pixels = [_elev_to_rgb(data.elevation_field[i]) for i in range(w * h)]
    img = Image.new("RGB", (w, h))
    img.putdata(pixels)
    draw = ImageDraw.Draw(img)

    # 流线按 Strahler 着色:1级=浅蓝,高级=深蓝
    max_order = max(
        (p.strahler for r in net.rivers for p in r.points),
        default=1,
    )
    for river in net.rivers:
        pts = [(p.x, p.y) for p in river.points]
        if len(pts) < 2:
            continue
        order = river.points[0].strahler
        t = order / max(max_order, 1)
        r_col = int(40 + 80 * (1 - t))
        g_col = int(120 + 80 * (1 - t))
        b_col = int(200 + 55 * t)
        width = max(1, order)
        draw.line(pts, fill=(r_col, g_col, b_col), width=width)

    out = OUT_DIR / "11_streamlines_overview.png"
    img.save(out)
    print(f"[saved] {out}")

    # ── 图 12: 直线距离最远的河流走向特写 ──
    best_dist, best_ri = 0, 0
    for ri, r in enumerate(net.rivers):
        pts = r.points
        if len(pts) < 50:
            continue
        dx = pts[-1].x - pts[0].x
        dy = pts[-1].y - pts[0].y
        dist = math.sqrt(dx * dx + dy * dy)
        if dist > best_dist:
            best_dist = dist
            best_ri = ri

    river = net.rivers[best_ri]
    pts = river.points
    elen = sum(
        math.sqrt((pts[i].x - pts[i-1].x)**2 + (pts[i].y - pts[i-1].y)**2)
        for i in range(1, len(pts))
    )
    print(f"最远河流: {len(pts)}点 直线={best_dist*0.1:.1f}km 实际={elen*0.1:.1f}km 弯曲度={elen/best_dist:.2f}")

    xs = [p.x for p in pts]
    ys = [p.y for p in pts]
    minx, maxx = min(xs), max(xs)
    miny, maxy = min(ys), max(ys)
    bw, bh = maxx - minx + 20, maxy - miny + 20
    pad = 10
    img2 = Image.new("RGB", (int(bw), int(bh)), (30, 40, 50))
    draw2 = ImageDraw.Draw(img2)
    # 海拔底图
    for gy in range(int(miny) - pad, int(maxy) + pad):
        for gx in range(int(minx) - pad, int(maxx) + pad):
            if 0 <= gx < w and 0 <= gy < h:
                e = data.elevation_field[gy * w + gx]
                px = gx - int(minx) + pad
                py = gy - int(miny) + pad
                if 0 <= px < bw and 0 <= py < bh:
                    draw2.point((px, py), fill=_elev_to_rgb(e))
    # 流线
    draw_pts = [(p.x - minx + pad, p.y - miny + pad) for p in pts]
    draw2.line(draw_pts, fill=(60, 140, 220), width=2)
    # 源头和终点标记
    draw2.ellipse([draw_pts[0][0]-4, draw_pts[0][1]-4, draw_pts[0][0]+4, draw_pts[0][1]+4],
                  fill=(255, 200, 0))
    draw2.ellipse([draw_pts[-1][0]-4, draw_pts[-1][1]-4, draw_pts[-1][0]+4, draw_pts[-1][1]+4],
                  fill=(255, 60, 60))
    out2 = OUT_DIR / "12_river_meander.png"
    img2.save(out2)
    print(f"[saved] {out2} (黄=源头, 红=入海)")

    # ── 图 13: tile 级渲染特写 ──
    gen = TileGenerator(seed=seed, continent=data)
    mid = len(pts) // 2
    mp = pts[mid]
    cx = int(mp.x * data.cell_size / 200)
    cy = int(mp.y * data.cell_size / 200)

    grid = gen.generate_chunk(cx, cy)
    terrain_colors = {
        TerrainType.GRASSLAND: (126, 200, 80),
        TerrainType.SAND: (232, 213, 163),
        TerrainType.FERTILE_SOIL: (92, 61, 46),
        TerrainType.ROCK: (139, 139, 139),
        TerrainType.STEEP_SLOPE: (107, 107, 107),
        TerrainType.MOUNTAIN_PEAK: (224, 224, 224),
        TerrainType.SHALLOW_WATER: (91, 158, 207),
        TerrainType.DEEP_WATER: (26, 58, 92),
        TerrainType.MARSH: (74, 107, 58),
    }
    size = 200
    img3 = Image.new("RGB", (size, size))
    pixels3 = []
    for y in range(size):
        for x in range(size):
            pixels3.append(terrain_colors.get(grid.get(x, y), (0, 0, 0)))
    img3.putdata(pixels3)
    out3 = OUT_DIR / "13_tile_river_meander.png"
    img3.save(out3)
    water = sum(1 for p in pixels3 if p in [(91, 158, 207), (26, 58, 92)])
    print(f"[saved] {out3} chunk=({cx},{cy}) 水体={water/400:.1f}%")

    # ── 图 14: 多个河流 chunk 拼接特写(3x3)──
    # 找河流最密集的 3x3 chunk 区域
    from collections import Counter
    chunk_counts = Counter()
    for rv in net.rivers:
        for p in rv.points:
            ccx = int(p.x * data.cell_size / 200)
            ccy = int(p.y * data.cell_size / 200)
            chunk_counts[(ccx, ccy)] += 1
    # 找密度最高的中心 chunk
    center = chunk_counts.most_common(1)[0][0]
    cx0, cy0 = center
    tile_size = 200
    mosaic = Image.new("RGB", (tile_size * 3, tile_size * 3))
    for dy in range(-1, 2):
        for dx in range(-1, 2):
            g = gen.generate_chunk(cx0 + dx, cy0 + dy)
            img_c = Image.new("RGB", (tile_size, tile_size))
            px = [terrain_colors.get(g.get(x, y), (0, 0, 0))
                  for y in range(tile_size) for x in range(tile_size)]
            img_c.putdata(px)
            mosaic.paste(img_c, ((dx + 1) * tile_size, (dy + 1) * tile_size))
    out4 = OUT_DIR / "14_tile_river_mosaic.png"
    mosaic.save(out4)
    print(f"[saved] {out4} 中心chunk=({cx0},{cy0}) 3x3拼接")


if __name__ == "__main__":
    main()
