"""可视化地图生成管线各阶段输出 — 多种子对比。

用法:
    cd ascend-backend && PYTHONPATH=. ../.venv/bin/python ../tests/visual/gen_pipeline_stages.py
"""

import time
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.font_manager as fm
import numpy as np
from matplotlib.colors import ListedColormap, BoundaryNorm

from ascend.space.continent import ContinentGenerator, ContinentParams

# ── 中文字体设置 ─────────────────────────
_cjk_fonts = [f for f in fm.findSystemFonts()
              if "SourceHan" in f or "NotoSansCJK" in f or "WenQuanYi" in f]
if _cjk_fonts:
    _prop = fm.FontProperties(fname=_cjk_fonts[0])
    plt.rcParams["font.family"] = _prop.get_name()
else:
    plt.rcParams["font.family"] = "sans-serif"

# ── 气候带色板 ────────────────────────────
CLIMATE_COLORS = [
    "#1a6b3a",  # 0 热带雨林
    "#c4a43e",  # 1 热带草原
    "#e6c878",  # 2 沙漠
    "#b8a060",  # 3 草原
    "#4a7c3f",  # 4 温带森林
    "#3a6a8a",  # 5 亚寒带针叶林
    "#d8d8e8",  # 6 极地苔原
    "#b0b0c0",  # 7 高山
]
CLIMATE_NAMES = [
    "热带雨林", "热带草原", "沙漠", "草原",
    "温带森林", "亚寒带针叶林", "极地苔原", "高山",
]


def plot_pipeline(seed: int, axs: np.ndarray) -> None:
    """对一个种子执行完整管线并在 axs 上绘制各阶段。"""
    params = ContinentParams(width_km=100, height_km=60, sample_resolution=100.0)
    gen = ContinentGenerator(seed=seed, params=params)

    t0 = time.perf_counter()
    data = gen.generate()
    elapsed = time.perf_counter() - t0

    w, h = data.grid_width, data.grid_height

    def _to_2d(arr, w=w, h=h):
        return np.array(arr, dtype=np.float64).reshape(h, w)

    titles = [
        f"海拔 (seed={seed})",
        "温度 (°C)",
        "降雨 (mm/yr)",
        "气候带",
        "河流 (流线 RK4)",
        "湖泊盆地",
    ]

    fields = [
        _to_2d(data.elevation_field),
        _to_2d(data.temperature_field),
        _to_2d(data.rainfall_field),
        np.array(data.climate_zone, dtype=np.int32).reshape(h, w),
        None,  # streamline rivers 叠加
        None,  # lake basins 叠加
    ]

    cmaps = ["terrain", "coolwarm", "YlGnBu", None, None, None]

    for col, (title, field, cmap) in enumerate(zip(titles, fields, cmaps)):
        ax = axs[col]
        if field is None:
            land = np.array(data.land_mask, dtype=bool).reshape(h, w)
            bg = np.full((h, w, 3), 0.6)
            bg[land] = [0.92, 0.90, 0.80]
            bg[~land] = [0.75, 0.85, 0.95]
            ax.imshow(bg, origin="upper")

            if col == 4:
                # 流线河流
                if data.hydrology and data.hydrology.river_network:
                    for river in data.hydrology.river_network.rivers:
                        xs = [p.x for p in river.points]
                        ys = [p.y for p in river.points]
                        ww = max(0.3, min(2.0, np.log10(river.points[0].flow + 1) * 0.5))
                        ax.plot(xs, ys, color="#3366cc", linewidth=ww, alpha=0.8)
            elif col == 5:
                # 湖泊盆地
                if data.hydrology and data.hydrology.lake_basins:
                    for basin in data.hydrology.lake_basins:
                        xs = [ci % w for ci in basin.cells]
                        ys = [ci // w for ci in basin.cells]
                        ax.scatter(xs, ys, s=0.3, c="#3388cc", alpha=0.6, edgecolors="none")

            ax.set_xlim(0, w)
            ax.set_ylim(h, 0)
        elif col == 3:
            # 气候带
            cmap_climate = ListedColormap(CLIMATE_COLORS)
            norm = BoundaryNorm(np.arange(-0.5, 8.5, 1), 8)
            im = ax.imshow(field, cmap=cmap_climate, norm=norm, origin="upper",
                          interpolation="nearest")
            # 图例
            patches = [plt.Rectangle((0, 0), 1, 1, color=c) for c in CLIMATE_COLORS]
        else:
            im = ax.imshow(field, cmap=cmap, origin="upper", interpolation="bilinear")

        ax.set_title(title, fontsize=8, pad=2)
        ax.set_xticks([])
        ax.set_yticks([])

    # 气候带图例（在最后一个气候带子图上）
    ax = axs[3]
    legend_labels = [f"{i}:{CLIMATE_NAMES[i]}" for i in range(8)]
    legend_patches = [plt.Rectangle((0, 0), 1, 1, color=c) for c in CLIMATE_COLORS]
    ax.legend(legend_patches, legend_labels, loc="lower left",
              fontsize=5, ncol=2, framealpha=0.7, handlelength=1, handleheight=1)

    # 在图上方标注总耗时
    axs[0].text(0.02, 0.98, f"{elapsed:.1f}s", transform=axs[0].transAxes,
                fontsize=7, va="top", fontfamily="monospace",
                bbox=dict(boxstyle="round,pad=0.3", facecolor="white", alpha=0.8))


def main():
    seeds = [42, 137, 888]
    n_seeds = len(seeds)
    n_cols = 6  # 海拔、温度、降雨、气候、流线河、湖泊

    fig, all_axs = plt.subplots(
        n_seeds, n_cols,
        figsize=(n_cols * 2.2, n_seeds * 2.0),
        gridspec_kw={"wspace": 0.08, "hspace": 0.25},
    )

    if n_seeds == 1:
        all_axs = all_axs[np.newaxis, :]

    print(f"生成 {n_seeds} 个种子 {n_cols} 个阶段的可视化...")
    for row, seed in enumerate(seeds):
        t0 = time.perf_counter()
        plot_pipeline(seed, all_axs[row])
        print(f"  seed={seed}: {time.perf_counter() - t0:.1f}s")

    fig.suptitle(
        "地图生成管线 — 各阶段输出对比 (100×60km, 100m 分辨率)",
        fontsize=10, fontweight="bold", y=0.995,
    )

    import os
    out_dir = os.path.join(os.path.dirname(__file__), "output")
    out = os.path.join(out_dir, "pipeline_stages.png")
    fig.savefig(out, dpi=180, bbox_inches="tight", facecolor="white")
    print(f"\n保存到: {out}")
    plt.close(fig)


if __name__ == "__main__":
    main()
