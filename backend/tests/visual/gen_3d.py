"""生成 3D 海拔地图 HTML — Plotly Surface + 水体叠加。

水体叠加方案：
  - 自定义 colorscale：海平面以下蓝色渐变 + 陆地绿-棕-白
  - 河流：3D scatter 线（河树节点连线）
  - 湖泊：3D surface 平面（湖面高程）

用法:
    cd backend && PYTHONPATH=. python tests/visual/gen_3d.py
    浏览器打开 ascending-backend/tests/visual/output/terrain_3d.html
"""

import os
from pathlib import Path

_OUTPUT_DIR = Path(__file__).resolve().parent / "output"
_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
_OUTPUT = _OUTPUT_DIR / "terrain_3d.html"


def generate(seed: int = 42, downsample: int = 4) -> None:
    """生成带水体叠加的 3D 海拔地图。

    Args:
        seed: 世界种子。
        downsample: 降采样因子（4 → 250×150 网格，平衡性能和细节）。
    """
    from ascend.space.continent import ContinentGenerator

    print(f"生成大陆数据 (seed={seed})...")
    cont = ContinentGenerator(seed=seed).generate()
    w, h = cont.grid_width, cont.grid_height
    hyd = cont.hydrology

    # ── 降采样 ──
    ds = downsample
    dw, dh = w // ds, h // ds

    elev_grid = [[0.0] * dw for _ in range(dh)]
    water_mask = [[False] * dw for _ in range(dh)]  # 湖泊像素

    for gy in range(dh):
        for gx in range(dw):
            # 逆向读 x（数据 x=0 在最右端 → 翻到左边）
            idx = gy * ds * w + (w - 1 - gx * ds)
            elev_grid[gy][gx] = cont.elevation_field[idx]

    # 湖泊 → water_mask（x 翻转匹配 elevation）
    if hyd and hyd.lake_basins:
        for basin in hyd.lake_basins:
            for ci in basin.cells:
                bx = w - 1 - (ci % w)  # x 翻转
                by = ci // w
                dgx = bx // ds
                dgy = by // ds
                if 0 <= dgx < dw and 0 <= dgy < dh:
                    water_mask[dgy][dgx] = True

    # ── 构建 lake grid → 快速查询节点所在湖面高程 ──
    lake_surface_at: dict[int, float] = {}  # grid_idx → surface_elev
    if hyd and hyd.lake_basins:
        for basin in hyd.lake_basins:
            for ci in basin.cells:
                lake_surface_at[ci] = basin.surface_elev

    # ── 河流流线 → 3D 散点 ──
    river_x, river_y, river_z = [], [], []
    if hyd and hyd.river_network:
        for river in hyd.river_network.rivers:
            for p in river.points:
                fx = (w - 1 - p.x) / ds
                fy = p.y / ds
                if 0 <= fx < dw and 0 <= fy < dh:
                    gi = int(p.y) * w + int(p.x)
                    elev = cont.elevation_field[gi] if 0 <= gi < len(cont.elevation_field) else 0
                    if gi in lake_surface_at and elev < lake_surface_at[gi]:
                        continue
                    river_x.append(fx)
                    river_y.append(fy)
                    river_z.append(max(0, elev) + 5)

    # ── 自定义 colorscale（对齐实际海拔范围 [-2486, 2413]）──
    # 位置 = (elev + 2486) / 4899
    custom_colorscale = [
        [0.000, "rgb(10,20,60)"],      # -2486m 最深海
        [0.099, "rgb(26,58,92)"],      # -2000m 深蓝
        [0.344, "rgb(60,120,180)"],    # -800m 中海
        [0.477, "rgb(90,160,210)"],    # -150m 浅海
        [0.501, "rgb(126,200,227)"],   # -30m 极浅海
        [0.507, "rgb(180,210,200)"],   # 0m 潮间带
        [0.508, "rgb(212,184,150)"],   # +5m 沙色
        [0.520, "rgb(180,200,100)"],   # +60m 浅绿
        [0.548, "rgb(122,173,79)"],    # +200m 绿
        [0.630, "rgb(140,160,80)"],    # +600m 黄绿
        [0.752, "rgb(184,154,107)"],   # +1200m 棕
        [0.916, "rgb(160,140,110)"],   # +2000m 深棕
        [1.000, "rgb(240,240,240)"],   # +2413m 白
    ]

    # ── Plotly 3D ──
    import plotly.graph_objects as go

    fig = go.Figure()

    # 海拔 Surface
    fig.add_trace(go.Surface(
        z=elev_grid,
        colorscale=custom_colorscale,
        cmin=-2486,
        cmax=2413,
        showscale=True,
        colorbar=dict(title="Elevation (m)"),
        name="Elevation",
        lighting=dict(ambient=0.5, diffuse=0.9, roughness=0.4, specular=0.2),
        contours=dict(
            z=dict(show=True, usecolormap=False, highlightcolor="black",
                   project=dict(z=True), start=0, size=200),
        ),
    ))

    # 河流 3D 散点
    if river_x:
        fig.add_trace(go.Scatter3d(
            x=river_x, y=river_y, z=river_z,
            mode="markers",
            marker=dict(size=5, color="rgba(30,80,220,0.8)", line=dict(width=0)),
            name=f"Rivers ({len(river_x)} nodes)",
        ))

    # 湖泊 Surface（蓝色半透明平面，x 翻转匹配 elevation）
    if hyd and hyd.lake_basins:
        for i, basin in enumerate(hyd.lake_basins[:5]):
            surface_elev = basin.surface_elev
            # x 翻转后的 bounding box
            lx_min = min(w - 1 - (ci % w) for ci in basin.cells) // ds
            lx_max = max(w - 1 - (ci % w) for ci in basin.cells) // ds
            ly_min = min(ci // w for ci in basin.cells) // ds
            ly_max = max(ci // w for ci in basin.cells) // ds

            if lx_max - lx_min < 2 or ly_max - ly_min < 2:
                continue

            lw = lx_max - lx_min + 1
            lh = ly_max - ly_min + 1
            # 湖面略高于地形避免 z-fighting
            lake_plane = [[surface_elev + 1.0] * lw for _ in range(lh)]

            fig.add_trace(go.Surface(
                z=lake_plane,
                x=list(range(lx_min, lx_max + 1)),
                y=list(range(ly_min, ly_max + 1)),
                colorscale=[[0, "rgb(50,100,220)"], [1, "rgb(50,100,220)"]],
                opacity=0.5,
                showscale=False,
                name=f"Lake {i+1} ({surface_elev:.0f}m, {basin.area_km2:.1f}km²)",
                lighting=dict(ambient=0.9, diffuse=0.1, specular=0),
            ))

    # 布局
    fig.update_layout(
        title=f"Ascend — 3D Terrain (seed={seed}, {dw}×{dh})",
        scene=dict(
            xaxis_title="X (100m × {})".format(ds),
            yaxis_title="Y (100m × {})".format(ds),
            zaxis_title="Elevation (m)",
            aspectratio=dict(x=1, y=0.6, z=0.08),
            camera=dict(eye=dict(x=1.5, y=1.5, z=0.8)),
        ),
        width=1600,
        height=1000,
    )

    out = str(_OUTPUT)
    fig.write_html(out)
    print(f"3D 地形已保存: {out}")
    print(f"  尺寸: {dw}×{dh} (降采样 {ds}x)")
    if river_x:
        print(f"  河流: {len(river_x)} 节点")
    if hyd and hyd.lake_basins:
        print(f"  湖泊: {len(hyd.lake_basins)} 个（显示前 5 大湖的湖面）")


if __name__ == "__main__":
    generate()
