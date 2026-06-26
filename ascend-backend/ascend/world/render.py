"""ASCII 地图渲染 — 将 WorldGenerator 的分块数据渲染为终端字符地图。

支持群系视图和气候视图两种模式，ANSI 颜色标注。
"""

import shutil

from ascend.world import WorldGenerator, BiomeType, ClimateZone

# ── ANSI 颜色 ────────────────────────────────────────────

_RESET = "\033[0m"

# 群系颜色
_BIOME_COLORS: dict[BiomeType, str] = {
    BiomeType.TEMPERATE_DECIDUOUS_FOREST: "\033[0;32m",  # 绿
    BiomeType.ARID_SHRUBLAND:            "\033[0;33m",  # 黄
}

# 气候颜色
_CLIMATE_COLORS: dict[ClimateZone, str] = {
    ClimateZone.TROPICAL:  "\033[0;31m",  # 红
    ClimateZone.TEMPERATE: "\033[0;32m",  # 绿
    ClimateZone.COLD:      "\033[0;36m",  # 青
    ClimateZone.ARID:      "\033[0;33m",  # 黄
}

# 群系字符
_BIOME_CHARS: dict[BiomeType, str] = {
    BiomeType.TEMPERATE_DECIDUOUS_FOREST: "T",
    BiomeType.ARID_SHRUBLAND:            "~",
}

# 气候字符
_CLIMATE_CHARS: dict[ClimateZone, str] = {
    ClimateZone.TROPICAL:  "H",
    ClimateZone.TEMPERATE: "M",
    ClimateZone.COLD:      "C",
    ClimateZone.ARID:      "A",
}


def render_map(
    gen: WorldGenerator,
    center: tuple[int, int] = (0, 0),
    radius: int = 10,
    *,
    mode: str = "biome",
    step: int = 1,
) -> str:
    """生成 ASCII 地图字符串。

    step 参数控制采样间距——世界群系在数百个分块的尺度上过渡，
    小 step 适合看局部一致性，大 step 适合看跨群系变化。

    Args:
        gen: WorldGenerator 实例。
        center: 中心分块坐标 (cx, cy)。
        radius: 渲染半径（显示格数），视野为 (radius*2+1)² 格。
        mode: "biome" 显示群系，"climate" 显示气候档位。
        step: 采样步长。实际采样坐标为 (cx*step, cy*step)。
              step=1 显示逐分块，step=20 时 40 格窗口覆盖 800 分块跨度。

    Returns:
        带 ANSI 颜色的多行字符串。
    """
    cx0, cy0 = center
    display_size = radius * 2 + 1

    # 先算 max_coord 才能确定 cell_w
    max_coord = max(abs(cx0 + radius * step), abs(cy0 + radius * step))
    cell_w = max(len(str(max_coord)) + 1, 3)  # 至少 3 字符
    gap = " " * (cell_w - 1)

    # 获取终端宽度以自适应半径
    term_w = shutil.get_terminal_size().columns
    max_visible = (term_w - 4) // cell_w  # 4 字符边距
    if display_size > max_visible:
        display_size = max_visible
        radius = (display_size - 1) // 2
        # 重新计算 max_coord（半径可能缩小了）
        max_coord = max(abs(cx0 + radius * step), abs(cy0 + radius * step))
        cell_w = max(len(str(max_coord)) + 1, 3)
        gap = " " * (cell_w - 1)

    if mode == "climate":
        colors = _CLIMATE_COLORS
        chars = _CLIMATE_CHARS
        get_value = gen.get_climate
    else:
        colors = _BIOME_COLORS
        chars = _BIOME_CHARS
        get_value = gen.get_biome

    lines: list[str] = []

    # 列坐标标尺
    header = " " * 4  # 左侧行号区宽度
    for dx in range(-radius, radius + 1):
        cx = cx0 + dx * step
        if dx % 5 == 0:
            label = f"{cx:>{cell_w-1}d}"
        else:
            label = " " * (cell_w - 1)
        header += label + " "
    lines.append(header)

    # 顶边框
    bar_w = display_size * cell_w
    lines.append("   ┌" + "─" * bar_w + "┐")

    # 分块行
    for dy in range(-radius, radius + 1):
        cy = cy0 + dy * step
        if dy % 5 == 0:
            row_label = f"{cy:>3d}│"
        else:
            row_label = " " * 3 + "│"
        row = ""
        for dx in range(-radius, radius + 1):
            cx = cx0 + dx * step
            value = get_value(cx, cy)
            color = colors.get(value, "")
            ch = chars.get(value, "?")
            # 中心点用 @ 标记
            if dx == 0 and dy == 0:
                color = "\033[1;37m"  # 亮白
                ch = "@"
            row += f"{color}{ch}{gap}{_RESET}"
        lines.append(row_label + row + "│")

    # 底边框
    lines.append("   └" + "─" * bar_w + "┘")

    # 图例
    lines.append("")
    if mode == "climate":
        legend_items = [
            ("H", _CLIMATE_COLORS[ClimateZone.TROPICAL], ClimateZone.TROPICAL.label),
            ("M", _CLIMATE_COLORS[ClimateZone.TEMPERATE], ClimateZone.TEMPERATE.label),
            ("C", _CLIMATE_COLORS[ClimateZone.COLD], ClimateZone.COLD.label),
            ("A", _CLIMATE_COLORS[ClimateZone.ARID], ClimateZone.ARID.label),
        ]
    else:
        legend_items = [
            ("T", _BIOME_COLORS[BiomeType.TEMPERATE_DECIDUOUS_FOREST],
             BiomeType.TEMPERATE_DECIDUOUS_FOREST.label),
            ("~", _BIOME_COLORS[BiomeType.ARID_SHRUBLAND],
             BiomeType.ARID_SHRUBLAND.label),
        ]
    legend = "   图例:  "
    for ch, color, label in legend_items:
        legend += f"{color}{ch}{_RESET}={label}  "
    legend += "\033[1;37m@\033[0m=当前"
    lines.append(legend)

    return "\n".join(lines)


def render_region_detail(
    gen: WorldGenerator,
    center: tuple[int, int] = (0, 0),
    radius: int = 2,
) -> str:
    """渲染小范围区域详情，显示每个分块的关键参数。

    Args:
        gen: WorldGenerator 实例。
        center: 中心分块坐标。
        radius: 渲染半径。

    Returns:
        格式化的多行详情字符串。
    """
    lines: list[str] = []
    lines.append(f"  {'Chunk':>8s}  {'Biome':<20s}  {'Climate':<10s}  "
                 f"{'Temp':>6s}  {'Rain':>6s}  {'Alt':>6s}")

    for cy in range(center[1] - radius, center[1] + radius + 1):
        for cx in range(center[0] - radius, center[0] + radius + 1):
            chunk = gen.generate_chunk(cx, cy)
            marker = " @<" if (cx, cy) == center else "   "
            lines.append(
                f"  ({cx:>3d},{cy:>3d})  "
                f"{chunk.biome.label:<20s}  "
                f"{chunk.climate_zone.label:<10s}  "
                f"{chunk.annual_baseline.temperature:>5.1f}C  "
                f"{chunk.annual_baseline.rainfall:>5.0f}mm  "
                f"{chunk.annual_baseline.altitude:>5.0f}m"
                f"{marker}"
            )
    return "\n".join(lines)
