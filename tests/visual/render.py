"""海拔场 → PNG 渲染工具。

将层1低分辨率海拔场渲染为彩色 PNG 图片，用于肉眼验证生成效果。

色彩方案（高度带）：
  深海 (< -3000m)   → 深蓝 #1a3a5c
  中海 (-3000~-100m) → 浅蓝 #4a90c4
  浅海 (-100~0m)     → 青蓝 #7ec8e3
  海岸 (0~100m)      → 沙色 #d4b896
  低地 (100~500m)    → 绿色 #7aad4f
  高地 (500~2000m)   → 棕色 #b89a6b
  山地 (2000~4000m)  → 灰色 #9e9e9e
  高峰 (> 4000m)     → 白色 #f0f0f0

用法:
    from tests.visual.render import render_elevation, render_mask
    render_elevation(dem, 1000, 600, "output/04_elevation.png", title="原始海拔")
"""

from array import array
from pathlib import Path


# 高度带色彩映射 (threshold_m, (R, G, B))
# 相邻色标之间做线性插值
def render_temperature(
    temperature: list[float],
    width: int,
    height: int,
    output_path: str,
    *,
    title: str = "",
) -> None:
    """渲染温度场——蓝（冷）→白→红（热）渐变。

    Args:
        temperature: 行优先温度数组 (°C)。
        width, height: 网格尺寸。
        output_path: 输出路径。
        title: 标题。
    """
    _ensure_pillow()
    from PIL import Image

    def temp_to_rgb(t: float) -> tuple[int, int, int]:
        t = max(-20.0, min(45.0, t))
        if t < 10.0:
            # 冷色调 [-20, 10]: 深蓝 → 浅蓝
            ratio = (t + 20.0) / 30.0
            r = int(30 + 170 * ratio)
            g = int(50 + 180 * ratio)
            b = int(220)
            return (r, g, b)
        elif t < 20.0:
            # 温和 [10, 20]: 浅蓝 → 白
            ratio = (t - 10.0) / 10.0
            r = int(200 + 55 * ratio)
            g = int(230 + 25 * ratio)
            b = int(220 - 20 * ratio)
            return (r, g, b)
        else:
            # 暖色 [20, 45]: 白 → 橙 → 红
            ratio = (t - 20.0) / 25.0
            r = min(255, int(255))
            g = max(0, int(255 - 200 * ratio))
            b = max(0, int(200 - 200 * ratio))
            return (r, g, b)

    pixels = [temp_to_rgb(t) for t in temperature]
    img = Image.new("RGB", (width, height))
    img.putdata(pixels)
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    img.save(output_path)
    print(f"[visual] 温度渲染已保存: {output_path}")


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

# 备选色彩 — 地块 id → 颜色
_BLOCK_COLORS: list[tuple[int, int, int]] = [
    (230, 120, 100),   # 红
    (100, 180, 230),   # 蓝
    (120, 210, 140),   # 绿
    (230, 200, 100),   # 黄
    (180, 130, 210),   # 紫
    (240, 160, 80),    # 橙
    (100, 210, 210),   # 青
    (210, 130, 170),   # 粉
    (140, 180, 100),   # 黄绿
    (160, 150, 220),   # 蓝紫
    (220, 140, 100),   # 棕橙
    (100, 160, 200),   # 灰蓝
    (200, 200, 140),   # 浅黄
    (160, 120, 180),   # 浅紫
    (190, 160, 130),   # 浅棕
]


def render_elevation_with_rivers(
    elevation: list[float],
    width: int,
    height: int,
    river_pixels: set[int],
    output_path: str,
    *,
    title: str = "",
) -> None:
    """渲染海拔场，河道像素强制标为蓝色（水面）。

    Args:
        elevation: 行优先海拔数组。
        width, height: 网格尺寸。
        river_pixels: 河道像素索引集合。
        output_path: 输出路径。
        title: 标题。
    """
    _ensure_pillow()
    from PIL import Image

    pixels: list[tuple[int, int, int]] = []
    for i, e in enumerate(elevation):
        if i in river_pixels:
            # 河道像素：陆地变蓝，海底保持原色
            if e > 0:
                pixels.append((60, 140, 220))  # 陆地河道 → 蓝色水体
            else:
                pixels.append(_elevation_to_rgb(e))  # 海底无变化
        else:
            pixels.append(_elevation_to_rgb(e))

    img = Image.new("RGB", (width, height))
    img.putdata(pixels)
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    img.save(output_path)
    print(f"[visual] 海拔+河流渲染已保存: {output_path}")


def _elevation_to_rgb(elevation: float) -> tuple[int, int, int]:
    """将海拔值映射为 RGB 颜色（连续渐变）。

    在相邻色标之间做线性插值。

    Args:
        elevation: 海拔 (m)。

    Returns:
        (R, G, B) 元组。
    """
    stops = _COLOR_STOPS
    if elevation <= stops[0][0]:
        return stops[0][1]
    if elevation >= stops[-1][0]:
        return stops[-1][1]

    # 找到 elevation 落在哪两个色标之间
    for i in range(len(stops) - 1):
        e_lo, c_lo = stops[i]
        e_hi, c_hi = stops[i + 1]
        if e_lo <= elevation <= e_hi:
            t = (elevation - e_lo) / (e_hi - e_lo)
            r = int(c_lo[0] + (c_hi[0] - c_lo[0]) * t)
            g = int(c_lo[1] + (c_hi[1] - c_lo[1]) * t)
            b = int(c_lo[2] + (c_hi[2] - c_lo[2]) * t)
            return (r, g, b)

    # 兜底
    return stops[-1][1]


def _ensure_pillow():
    """确保 Pillow 可用，不可用时给出提示。"""
    try:
        from PIL import Image  # noqa: F401
    except ImportError:
        raise ImportError(
            "可视化渲染需要 Pillow 库。请执行: pip install Pillow"
        )


def render_elevation(
    elevation: list[float] | array,
    width: int,
    height: int,
    output_path: str,
    *,
    title: str = "",
) -> None:
    """将一维行优先海拔数组渲染为彩色 PNG。

    Args:
        elevation: 一维行优先海拔数组（长度 = width × height）。
        width: 网格宽度（像素）。
        height: 网格高度（像素）。
        output_path: 输出 PNG 文件路径。
        title: 图片标题（暂无渲染，保留接口）。

    Raises:
        ImportError: 未安装 Pillow。
        ValueError: 数组长度与 width×height 不匹配。
    """
    _ensure_pillow()
    from PIL import Image

    expected = width * height
    if len(elevation) != expected:
        raise ValueError(
            f"海拔数组长度 {len(elevation)} != width×height ({expected})"
        )

    # 构建 RGB 像素数据
    pixels: list[tuple[int, int, int]] = []
    for e in elevation:
        pixels.append(_elevation_to_rgb(e))

    # 创建图片（行优先 → PIL 从左到右、从上到下）
    img = Image.new("RGB", (width, height))
    img.putdata(pixels)

    # 确保输出目录存在
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    img.save(output_path)
    print(f"[visual] 海拔渲染已保存: {output_path}")


def render_mask(
    mask: list[bool] | list[int],
    width: int,
    height: int,
    output_path: str,
    *,
    title: str = "",
    true_color: tuple[int, int, int] = (122, 173, 79),
    false_color: tuple[int, int, int] = (74, 144, 196),
) -> None:
    """将布尔掩码渲染为双色 PNG。

    Args:
        mask: 一维行优先布尔掩码（或 0/1 整数列表）。
        width: 网格宽度。
        height: 网格高度。
        output_path: 输出路径。
        title: 图片标题。
        true_color: True 像素的颜色。
        false_color: False 像素的颜色。
    """
    _ensure_pillow()
    from PIL import Image

    pixels: list[tuple[int, int, int]] = []
    for v in mask:
        pixels.append(true_color if v else false_color)

    img = Image.new("RGB", (width, height))
    img.putdata(pixels)
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    img.save(output_path)
    print(f"[visual] 掩码渲染已保存: {output_path}")


def render_blocks(
    block_ids: list[int],
    width: int,
    height: int,
    output_path: str,
    *,
    title: str = "",
) -> None:
    """将地块 ID 数组渲染为分色 PNG。

    不同地块 ID 使用不同颜色，便于区分地块边界。

    Args:
        block_ids: 一维行优先地块 ID 数组（-1 = 海洋/无地块）。
        width: 网格宽度。
        height: 网格高度。
        output_path: 输出路径。
        title: 图片标题。
    """
    _ensure_pillow()
    from PIL import Image

    ocean_color = (74, 144, 196)  # 海洋用浅蓝
    pixels: list[tuple[int, int, int]] = []
    for bid in block_ids:
        if bid < 0:
            pixels.append(ocean_color)
        else:
            pixels.append(_BLOCK_COLORS[bid % len(_BLOCK_COLORS)])

    img = Image.new("RGB", (width, height))
    img.putdata(pixels)
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    img.save(output_path)
    print(f"[visual] 地块渲染已保存: {output_path}")


def render_overlay_lines(
    base_path: str,
    lines: list[list[tuple[float, float]]],
    output_path: str,
    *,
    colors: list[tuple[int, int, int]] | None = None,
    line_width: int = 2,
    title: str = "",
) -> None:
    """在已有 PNG 底图上叠加折线。

    Args:
        base_path: 底图 PNG 路径。
        lines: 折线列表，每条折线是 (x, y) 坐标元组列表。
        output_path: 输出路径。
        colors: 每条折线的颜色列表，默认红色。
        line_width: 线宽（像素）。
        title: 图片标题。
    """
    _ensure_pillow()
    from PIL import Image, ImageDraw

    img = Image.open(base_path)
    draw = ImageDraw.Draw(img)

    if colors is None:
        colors = [(255, 60, 60)] * len(lines)

    for i, line in enumerate(lines):
        if len(line) < 2:
            continue
        color = colors[i % len(colors)]
        # 坐标可能是 0-1 归一化或像素坐标
        # 假设是像素坐标
        points: list[tuple[float, float]] = [(p[0], p[1]) for p in line]
        draw.line(points, fill=color, width=line_width)

    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    img.save(output_path)
    print(f"[visual] 叠加渲染已保存: {output_path}")


def render_rivers(
    base_path: str,
    rivers: list[list[tuple[float, float]]],
    output_path: str,
    *,
    title: str = "",
) -> None:
    """在底图上叠加河流网络。

    使用蓝色渐变表示河流等级。

    Args:
        base_path: 底图 PNG 路径。
        rivers: 河流列表，每条河流是 (x, y) 坐标元组列表。
        output_path: 输出路径。
        title: 图片标题。
    """
    _ensure_pillow()
    from PIL import Image, ImageDraw

    img = Image.open(base_path)
    draw = ImageDraw.Draw(img)

    # 蓝色渐变 — 主流深色，支流浅色
    river_colors = [
        (20, 40, 180),    # 主流 — 深蓝
        (40, 80, 200),    # 二级
        (70, 130, 220),   # 三级
        (120, 170, 235),  # 四级+
    ]

    for i, river in enumerate(rivers):
        if len(river) < 2:
            continue
        color = river_colors[min(i, len(river_colors) - 1)]
        points = [(p[0], p[1]) for p in river]
        draw.line(points, fill=color, width=1)

    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    img.save(output_path)
    print(f"[visual] 河流渲染已保存: {output_path}")


__all__ = [
    "render_elevation",
    "render_mask",
    "render_blocks",
    "render_overlay_lines",
    "render_rivers",
]
