"""Generate placeholder pixel-art terrain textures for GridMap MeshLibrary.

Outputs 64×64 PNG tiles to frontend/assets/terrain/textures/.
Each terrain type gets top_<name>.png and side_<name>.png.
Replace these PNGs with AI-generated assets or user texture packs later.

Usage:
    cd /home/Jedidiah/Documents/Ascend
    .venv/bin/python scripts/gen_terrain_textures.py
"""

import os
import sys
import numpy as np
from PIL import Image

# ── config ──────────────────────────────────────────────────
SIZE = 64
OUT_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "frontend", "assets", "terrain", "textures",
)
OCTAVES = 4
PERSISTENCE = 0.5
LACUNARITY = 2.0

# ── terrain palette – each entry: (top_color, side_color) in (R,G,B) ──
# Use pixel-art style palettes: slightly desaturated, clear identity
TERRAIN_PALETTE = {
    "shallow_water":    ((64, 140, 180),  (50, 100, 120)),
    "sand":             ((210, 190, 130), (180, 160, 110)),
    "plains":           ((100, 170, 70),  (90, 70, 40)),
    "hills":            ((75, 140, 55),   (95, 75, 45)),
    "rock":             ((140, 140, 145), (100, 100, 105)),
    "mountain":         ((110, 110, 115), (80, 80, 85)),
    "snow":             ((240, 245, 250), (200, 210, 220)),
    "fertile":          ((80, 55, 30),    (55, 35, 18)),
    "underwater_floor": ((90, 110, 95),   (60, 75, 65)),
}


def _simple_noise(seed: int, width: int, height: int, octaves: int) -> np.ndarray:
    """Generate a 2D noise field using random upscale + interpolation.

    Not real Perlin, but produces plausible tileable pixel textures
    without external dependencies.
    """
    rng = np.random.default_rng(seed)
    result = np.zeros((height, width), dtype=np.float32)
    max_amp = 0.0
    amp = 1.0
    freq = 1.0

    for o in range(octaves):
        period = max(2, int(width / freq))
        if period < 2:
            break
        # coarse random grid
        gw = width // period + 2
        gh = height // period + 2
        grid = rng.random((gh, gw)).astype(np.float32)

        # bilinear upscale to target size
        y = (np.arange(height, dtype=np.float32) / period) % 1.0  # wrap for tiling
        x = (np.arange(width, dtype=np.float32) / period) % 1.0
        # build per-row/per-col indices
        yi = np.floor(np.arange(height, dtype=np.float32) / period).astype(int) % gw
        xi = np.floor(np.arange(width, dtype=np.float32) / period).astype(int) % gh
        # tile wrap
        yi_next = (yi + 1) % gw
        xi_next = (xi + 1) % gh

        # build full 2D interpolated field via outer products
        y0 = y.reshape(-1, 1).repeat(width, axis=1)
        x0 = x.reshape(1, -1).repeat(height, axis=0)

        v00 = grid[yi.reshape(-1, 1), xi.reshape(1, -1)]  # simplified slice
        # For simplicity, interpolate with numpy meshgrid
        yy, xx = np.meshgrid(y, x, indexing="ij")
        yi_m = np.clip(np.floor(yy * (gh - 1)).astype(int), 0, gh - 1)
        yr = yy * (gh - 1) - yi_m.astype(np.float32)
        xi_m = np.clip(np.floor(xx * (gw - 1)).astype(int), 0, gw - 1)
        xr = xx * (gw - 1) - xi_m.astype(np.float32)

        yi1 = np.clip(yi_m + 1, 0, gh - 1)
        xi1 = np.clip(xi_m + 1, 0, gw - 1)

        v00 = grid[yi_m, xi_m]
        v10 = grid[yi1, xi_m]
        v01 = grid[yi_m, xi1]
        v11 = grid[yi1, xi1]

        octave_val = (
            v00 * (1 - yr) * (1 - xr)
            + v10 * yr * (1 - xr)
            + v01 * (1 - yr) * xr
            + v11 * yr * xr
        )
        result += octave_val * amp
        max_amp += amp
        amp *= PERSISTENCE
        freq *= LACUNARITY

    result /= max(max_amp, 0.001)
    return np.clip(result, 0.0, 1.0)


def _make_pixel_texture(base_color, noise_seed: int, side: bool) -> Image.Image:
    """Create a 64×64 pixel-art texture tile.

    Args:
        base_color: (R, G, B) tuple
        noise_seed: random seed for noise variation
        side: True for side texture (darker, more variation), False for top (cleaner)
    """
    rng = np.random.default_rng(noise_seed)
    noise = _simple_noise(noise_seed, SIZE, SIZE, OCTAVES if not side else OCTAVES + 1)

    # Pixel-art effect: quantize noise to reduce color banding
    steps = 6 if not side else 5
    noise_q = np.round(noise * steps) / steps

    # Build RGB array
    rgb = np.zeros((SIZE, SIZE, 3), dtype=np.uint8)
    for c in range(3):
        bc = base_color[c]
        if side:
            # side face: more contrast, darker variation
            var = (noise_q * 80 - 40).astype(np.float32)
        else:
            # top face: subtler variation
            var = (noise_q * 50 - 25).astype(np.float32)

        # Add sparse "grain" noise for pixel-art feel
        grain = (rng.random((SIZE, SIZE)).astype(np.float32) - 0.5) * 12
        channel = np.clip(bc + var + grain, 0, 255).astype(np.uint8)
        rgb[:, :, c] = channel

    return Image.fromarray(rgb, "RGB")


def generate_all() -> list[str]:
    os.makedirs(OUT_DIR, exist_ok=True)
    generated: list[str] = []

    for i, (terrain_name, (top_color, side_color)) in enumerate(TERRAIN_PALETTE.items()):
        # top texture
        top_img = _make_pixel_texture(top_color, noise_seed=i * 2, side=False)
        top_path = os.path.join(OUT_DIR, f"top_{terrain_name}.png")
        top_img.save(top_path)
        generated.append(top_path)

        # side texture
        side_img = _make_pixel_texture(side_color, noise_seed=i * 2 + 1, side=True)
        side_path = os.path.join(OUT_DIR, f"side_{terrain_name}.png")
        side_img.save(side_path)
        generated.append(side_path)

    return generated


def _make_placeholder_png(path: str, color: tuple, label: str) -> None:
    """Fallback: solid-color PNG if generation fails."""
    img = Image.new("RGB", (SIZE, SIZE), color)
    img.save(path)


if __name__ == "__main__":
    try:
        paths = generate_all()
        print(f"Generated {len(paths)} textures:")
        for p in paths:
            print(f"  {p}")
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        # fallback: solid colour placeholders
        os.makedirs(OUT_DIR, exist_ok=True)
        for name, (top_c, side_c) in TERRAIN_PALETTE.items():
            _make_placeholder_png(
                os.path.join(OUT_DIR, f"top_{name}.png"), top_c, name
            )
            _make_placeholder_png(
                os.path.join(OUT_DIR, f"side_{name}.png"), side_c, name
            )
        print("Fallback solid-colour PNGs generated.")
        sys.exit(1)
