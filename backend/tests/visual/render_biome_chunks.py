"""群系细分可视化 — 展示不同群系 chunk 的地形分布差异。

生成两张图：
1. terrain_comparison.png — 多个不同群系 chunk 并排，地形类型着色
2. biome_membership.png — 跨群系边界的隶属度渐变带

用法:
    cd backend && PYTHONPATH=. ../.venv/bin/python tests/visual/render_biome_chunks.py
"""

import os
import random
from collections import Counter

from PIL import Image, ImageDraw, ImageFont

from ascend.space.continent import ContinentGenerator
from ascend.space.tile_gen import TileGenerator
from ascend.space.tile_grid import TILE_MAP_SIZE
from ascend.space.terrain import TerrainType
from ascend.space.biome import BiomeType
from ascend.space.generator import WorldGenerator

# ── 地形类型颜色 ──────────────────────────────────────────

TERRAIN_COLORS: dict[int, tuple[int, int, int]] = {
    int(TerrainType.DEEP_WATER): (26, 58, 92),
    int(TerrainType.SHALLOW_WATER): (90, 160, 210),
    int(TerrainType.SAND): (232, 213, 163),
    int(TerrainType.FERTILE_SOIL): (92, 61, 46),
    int(TerrainType.GRASSLAND): (126, 200, 80),
    int(TerrainType.ROCK): (139, 139, 139),
    int(TerrainType.STEEP_SLOPE): (107, 107, 107),
    int(TerrainType.MOUNTAIN_PEAK): (224, 224, 224),
    int(TerrainType.MARSH): (74, 107, 58),
}

TERRAIN_LABELS: dict[int, str] = {
    int(TerrainType.DEEP_WATER): "深水",
    int(TerrainType.SHALLOW_WATER): "浅水",
    int(TerrainType.SAND): "沙地",
    int(TerrainType.FERTILE_SOIL): "沃土",
    int(TerrainType.GRASSLAND): "草地",
    int(TerrainType.ROCK): "岩石",
    int(TerrainType.STEEP_SLOPE): "陡坡",
    int(TerrainType.MOUNTAIN_PEAK): "山巅",
    int(TerrainType.MARSH): "沼泽",
}

# ── 群系颜色（与 web server 一致）──────────────────────────

BIOME_COLORS: dict[BiomeType, tuple[int, int, int]] = {
    BiomeType.TROPICAL_MONSOON_FOREST: (42, 139, 58),
    BiomeType.TROPICAL_RAINFOREST: (26, 107, 58),
    BiomeType.TROPICAL_SAVANNA: (196, 164, 62),
    BiomeType.TROPICAL_WOODLAND: (90, 140, 58),
    BiomeType.SANDY_DESERT: (230, 200, 120),
    BiomeType.ROCKY_DESERT: (184, 160, 96),
    BiomeType.SHORT_GRASS_STEPPE: (184, 160, 96),
    BiomeType.TALL_GRASS_STEPPE: (140, 170, 90),
    BiomeType.TEMPERATE_MIXED_FOREST: (58, 124, 79),
    BiomeType.TEMPERATE_DECIDUOUS_FOREST: (74, 124, 63),
    BiomeType.BOREAL_WETLAND: (74, 122, 138),
    BiomeType.BOREAL_FOREST: (58, 106, 138),
    BiomeType.POLAR_BARREN: (224, 224, 232),
    BiomeType.TUNDRA: (216, 216, 232),
    BiomeType.ALPINE_MEADOW: (176, 176, 192),
    BiomeType.ALPINE_BARREN: (144, 144, 152),
    BiomeType.WARM_OCEAN: (30, 107, 138),
    BiomeType.TEMPERATE_OCEAN: (46, 107, 138),
    BiomeType.COLD_OCEAN: (90, 138, 170),
}

OUTPUT_DIR = os.path.join(os.path.dirname(__file__), "output")

# 中文字体路径（含中文 CJK 字形）
_FONT_CANDIDATES = [
    "/usr/share/fonts/noto-cjk/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/adobe-source-han-sans/SourceHanSansCN-Light.otf",
    "/usr/share/fonts/truetype/wqy/wqy-microhei.ttc",
]


def _load_font(size: int) -> ImageFont.FreeTypeFont:
    """加载含中文字形的字体，回退到默认字体。"""
    for path in _FONT_CANDIDATES:
        if os.path.exists(path):
            try:
                return ImageFont.truetype(path, size)
            except (IOError, OSError):
                continue
    return ImageFont.load_default()

# 每个 chunk 渲染单元的尺寸（含标签边距）
_CHUNK_TILE = TILE_MAP_SIZE  # 200
_LABEL_H = 40
_CELL_W = _CHUNK_TILE
_CELL_H = _CHUNK_TILE + _LABEL_H
_PAD = 10


def _find_diverse_chunks(
    wg: WorldGenerator, cont, n: int = 9,
) -> list[tuple[int, int, BiomeType]]:
    """随机采样找到 n 个不同群系的 chunk。

    用大陆格粒度采样（每 chunk 2×2 大陆格），覆盖更广。
    """
    from ascend.space import biome_from_attrs

    random.seed(0)
    seen: dict[BiomeType, tuple[int, int]] = {}
    attempts = 0
    w, h = cont.grid_width, cont.grid_height
    ranges = cont.subdiv_ranges
    while len(seen) < n and attempts < 5000:
        gx = random.randint(0, w - 1)
        gy = random.randint(0, h - 1)
        idx = gy * w + gx
        if not cont.land_mask[idx]:
            attempts += 1
            continue
        alt = cont.elevation_field[idx]
        cx, cy = gx // 2, gy // 2
        temp, rain, sea_temp, _ = cont.get_chunk_climate(cx, cy)
        moisture = wg._sample_derived_noise(wg._noise_moisture, cx, cy, 6)
        b = biome_from_attrs(temp, rain, alt, sea_temp, moisture, subdiv_ranges=ranges)
        if not b.is_ocean and b not in seen:
            seen[b] = (cx, cy)
        attempts += 1
    return [(cx, cy, b) for b, (cx, cy) in seen.items()]


def render_terrain_comparison(seed: int = 42) -> None:
    """渲染多个不同群系 chunk 的地形分布对比图。

    每个 chunk 一格，地形类型着色 + 标签（群系名 + 海拔 + 地形统计）。
    """
    print(f"\n[terrain_comparison] seed={seed}")
    print("  生成大陆数据...")
    cont = ContinentGenerator(seed=seed).generate()
    wg = WorldGenerator(seed=seed)
    wg._continent = cont
    tg = TileGenerator(seed=seed, continent=cont)

    print("  搜索不同群系的 chunk...")
    chunks = _find_diverse_chunks(wg, cont, n=9)
    chunks.sort(key=lambda x: x[2].value)
    print(f"  找到 {len(chunks)} 个不同群系 chunk")

    cols = 3
    rows = (len(chunks) + cols - 1) // cols
    canvas_w = cols * _CELL_W + (cols + 1) * _PAD
    canvas_h = rows * _CELL_H + (rows + 1) * _PAD + 80  # +80 给图例

    img = Image.new("RGB", (canvas_w, canvas_h), (30, 30, 30))
    draw = ImageDraw.Draw(img)

    font = _load_font(16)
    font_small = _load_font(12)

    # 标题
    draw.text((_PAD, 10), f"群系细分地形对比 (seed={seed})",
              fill=(255, 255, 255), font=font)

    for idx, (cx, cy, biome) in enumerate(chunks):
        col = idx % cols
        row = idx // cols
        x0 = _PAD + col * (_CELL_W + _PAD)
        y0 = 40 + _PAD + row * (_CELL_H + _PAD)

        print(f"  生成 chunk ({cx},{cy}) biome={biome.label}...")
        chunk = wg.generate_chunk(cx, cy)
        grid = tg.generate_chunk_for(chunk)

        # 统计地形分布
        terrain_count = Counter()
        for ty in range(TILE_MAP_SIZE):
            for tx in range(TILE_MAP_SIZE):
                terrain_count[int(grid.get(tx, ty))] += 1

        # 渲染 chunk tile
        pixels = img.load()
        for ty in range(TILE_MAP_SIZE):
            for tx in range(TILE_MAP_SIZE):
                t = int(grid.get(tx, ty))
                pixels[x0 + tx, y0 + ty] = TERRAIN_COLORS.get(t, (128, 0, 128))

        # 标签
        label_y = y0 + _CHUNK_TILE + 4
        label_text = f"{biome.label}  alt={chunk.altitude:.0f}m  T={chunk.mean_temp:.1f}°C"
        draw.text((x0, label_y), label_text, fill=(255, 255, 255), font=font_small)

        # 地形 top-3
        top3 = terrain_count.most_common(3)
        top_str = "  ".join(
            f"{TERRAIN_LABELS.get(t, '?')}:{n}" for t, n in top3
        )
        draw.text((x0, label_y + 16), top_str, fill=(180, 180, 180), font=font_small)

    # 图例
    legend_y = canvas_h - 30
    lx = _PAD
    draw.text((lx, legend_y - 16), "地形图例:", fill=(200, 200, 200), font=font_small)
    lx += 70
    for t, label in TERRAIN_LABELS.items():
        color = TERRAIN_COLORS[t]
        draw.rectangle([lx, legend_y, lx + 12, legend_y + 12], fill=color)
        draw.text((lx + 16, legend_y - 2), label, fill=(200, 200, 200), font=font_small)
        lx += 16 + len(label) * 11 + 12

    os.makedirs(OUTPUT_DIR, exist_ok=True)
    out_path = os.path.join(OUTPUT_DIR, "biome_terrain_comparison.png")
    img.save(out_path)
    print(f"  保存: {out_path} ({canvas_w}×{canvas_h})")


def render_biome_membership_band(seed: int = 42) -> None:
    """渲染跨群系边界的隶属度渐变带。

    找一对相邻不同群系的 chunk，渲染 2×1 区域，群系颜色着色。
    展示 chunk 边界处的平滑过渡。
    """
    print(f"\n[biome_membership_band] seed={seed}")
    print("  生成大陆数据...")
    cont = ContinentGenerator(seed=seed).generate()
    wg = WorldGenerator(seed=seed)
    wg._continent = cont
    tg = TileGenerator(seed=seed, continent=cont)

    print("  搜索跨群系边界...")
    boundary = None
    random.seed(1)
    for _ in range(500):
        cx = random.randint(5, cont.grid_width // 2 - 2)
        cy = random.randint(5, cont.grid_height // 2 - 2)
        b1 = wg.get_biome(cx, cy)
        b2 = wg.get_biome(cx + 1, cy)
        if b1 != b2 and not b1.is_ocean and not b2.is_ocean:
            boundary = (cx, cy, b1, b2)
            break

    if boundary is None:
        print("  未找到跨群系边界，跳过")
        return

    cx, cy, b1, b2 = boundary
    print(f"  边界: {b1.label} (chunk {cx},{cy}) | {b2.label} (chunk {cx+1},{cy})")

    from ascend.space.biome import biome_membership

    total_w = 2 * _CHUNK_TILE
    total_h = _CHUNK_TILE + 60
    img = Image.new("RGB", (total_w, total_h), (30, 30, 30))
    draw = ImageDraw.Draw(img)

    font = _load_font(16)
    font_small = _load_font(12)

    pixels = img.load()

    for chunk_idx, (ccx, ccy) in enumerate([(cx, cy), (cx + 1, cy)]):
        x0 = chunk_idx * _CHUNK_TILE
        # chunk 中心气候（与 tile_gen 一致，整 chunk 复用）
        if cont.get_chunk_climate(ccx, ccy) == (-20.0, 0.0, -20.0, 0):
            continue  # 越界 chunk，跳过
        cc_temp, cc_rain, _, _ = cont.get_chunk_climate(ccx, ccy)
        for ty in range(_CHUNK_TILE):
            for tx in range(_CHUNK_TILE):
                wx = ccx * _CHUNK_TILE + tx
                wy = ccy * _CHUNK_TILE + ty
                macro = cont.sample_altitude_bilinear(wx, wy)
                sea_temp = cc_temp + macro * 9.0 / 1000.0
                moisture = tg._moisture_noise.octave(
                    wx + 0.5, wy + 0.5, octaves=2, frequency=0.005,
                )
                # 用宏观海拔算隶属度（与 tile_gen 一致，传动态值域）
                m = biome_membership(
                    cc_temp, cc_rain, macro, sea_temp, moisture,
                    subdiv_ranges=cont.subdiv_ranges,
                )
                # 混合群系颜色
                r = g = bb = 0.0
                for biome, w in m:
                    cr, cg, cb = BIOME_COLORS[biome]
                    r += cr * w
                    g += cg * w
                    bb += cb * w
                pixels[x0 + tx, ty] = (int(r), int(g), int(bb))

    # chunk 边界线
    boundary_x = _CHUNK_TILE
    for y in range(_CHUNK_TILE):
        pixels[boundary_x - 1, y] = (255, 255, 0)
        pixels[boundary_x, y] = (255, 255, 0)

    # 标签
    draw.text((10, _CHUNK_TILE + 8),
              f"{b1.label}", fill=(255, 255, 255), font=font)
    draw.text((_CHUNK_TILE + 10, _CHUNK_TILE + 8),
              f"{b2.label}", fill=(255, 255, 255), font=font)
    draw.text((10, _CHUNK_TILE + 28),
              f"黄色竖线 = chunk 边界 (seed={seed})  颜色按群系隶属度混合",
              fill=(200, 200, 200), font=font_small)

    os.makedirs(OUTPUT_DIR, exist_ok=True)
    out_path = os.path.join(OUTPUT_DIR, "biome_membership_band.png")
    img.save(out_path)
    print(f"  保存: {out_path} ({total_w}×{total_h})")


def render_biome_overview(seed: int = 42) -> None:
    """渲染大范围 chunk 区域的群系分布俯瞰图。

    用大陆格粒度采样（每格 100m），群系颜色着色。
    """
    print(f"\n[biome_overview] seed={seed}")
    print("  生成大陆数据...")
    wg = WorldGenerator(seed=seed)
    cont = wg.ensure_continent()  # 含沙漠 moisture 动态值域
    from ascend.space import biome_from_attrs
    ranges = cont.subdiv_ranges

    w, h = cont.grid_width, cont.grid_height

    img = Image.new("RGB", (w, h), (20, 20, 20))
    pixels = img.load()

    print(f"  渲染 {w}×{h} 大陆格...")
    for gy in range(h):
        for gx in range(w):
            idx = gy * w + gx
            cx, cy = gx // 2, gy // 2
            temp, rain, sea_temp, _ = cont.get_chunk_climate(cx, cy)
            if not cont.land_mask[idx]:
                # 海洋按海平面温度分色
                if sea_temp >= 20:
                    pixels[gx, gy] = BIOME_COLORS[BiomeType.WARM_OCEAN]
                elif sea_temp >= 5:
                    pixels[gx, gy] = BIOME_COLORS[BiomeType.TEMPERATE_OCEAN]
                else:
                    pixels[gx, gy] = BIOME_COLORS[BiomeType.COLD_OCEAN]
                continue
            alt = cont.elevation_field[idx]
            moisture = wg._sample_derived_noise(wg._noise_moisture, cx, cy, 6)
            b = biome_from_attrs(temp, rain, alt, sea_temp, moisture, subdiv_ranges=ranges)
            pixels[gx, gy] = BIOME_COLORS.get(b, (128, 0, 128))

    os.makedirs(OUTPUT_DIR, exist_ok=True)
    out_path = os.path.join(OUTPUT_DIR, "biome_overview.png")
    img.save(out_path)
    print(f"  保存: {out_path} ({w}×{h})")

    # 统计
    from collections import Counter
    counts = Counter()
    for gy in range(h):
        for gx in range(w):
            idx = gy * w + gx
            if not cont.land_mask[idx]:
                continue
            alt = cont.elevation_field[idx]
            cx, cy = gx // 2, gy // 2
            temp, rain, sea_temp, _ = cont.get_chunk_climate(cx, cy)
            moisture = wg._sample_derived_noise(wg._noise_moisture, cx, cy, 6)
            b = biome_from_attrs(temp, rain, alt, sea_temp, moisture, subdiv_ranges=ranges)
            counts[b] += 1
    total = sum(counts.values())
    print("  陆地群系分布:")
    for b in sorted(counts, key=lambda x: x.value):
        n = counts[b]
        pct = n / total * 100
        print(f"    {b.label:8s}: {pct:5.1f}%")


if __name__ == "__main__":
    SEED = 100  # 16 种陆地群系全覆盖
    render_biome_overview(SEED)
    render_terrain_comparison(SEED)
    render_biome_membership_band(SEED)
