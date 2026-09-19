#!/usr/bin/env python3
"""Generate the agents-ensemble icon raster set from the design.

Design source-of-truth lives in `frontend/public/favicon.svg` (hand-authored
SVG, served as the modern-browser favicon). This script renders the same
design via PIL+numpy primitives, then exports:

  * apple-touch-icon.png (180x180)
  * favicon.ico (multi-size 16/32/48)
  * icons/favicon-{16,32,48}.png (sanity renders for legibility inspection)

Re-running is idempotent and deterministic. No external deps beyond PIL +
numpy (both stdlib-adjacent and already on the dev box). No SVG rasterizer
needed — the SVG and the Python here are two parallel hand-maintained
representations of the same design (a triangular constellation of three
outer "agent" nodes connected to one central "ensemble" hub).

Run from repo root:

    python3 frontend/scripts/build-icons.py

Outputs are written under `frontend/public/` and `frontend/public/icons/`.
The build script itself is intentionally small (~100 lines) and safe to
re-run; the design is documented in the constants block below.
"""

from __future__ import annotations

import math
from pathlib import Path

import numpy as np
from PIL import Image

# ---------- Palette (must match frontend/public/favicon.svg) ----------
BG_CENTER = (0x1E, 0x29, 0x3B)   # slate-800, radial-gradient center
BG_EDGE   = (0x0B, 0x12, 0x22)   # near-black slate, radial-gradient edge
LINE      = (0x2D, 0xD4, 0xBF)   # teal-400, connection lines @ 0.6 alpha
NODE_LT   = (0x5E, 0xEA, 0xD4)   # teal-300, outer-node gradient top
NODE_DK   = (0x0D, 0x94, 0x88)   # teal-600, outer-node gradient bot
HUB_LT    = (0x81, 0x8C, 0xF8)   # indigo-400, hub gradient top
HUB_DK    = (0x43, 0x38, 0xCA)   # indigo-700, hub gradient bot

# ---------- Geometry (logical 32x32 units, mirrors the SVG) ----------
VIEW = 32
BG_RADIUS = 7  # rounded-square corner radius (logical units)
OUTER_R   = 4
HUB_R     = 5

# (cx, cy) in logical units
OUTER_NODES = [(16.0, 7.0), (7.5, 22.0), (24.5, 22.0)]
HUB         = (16.0, 17.0)


# ---------- Rendering primitives ----------

def _to_px(units: float, scale: float) -> float:
    return units * scale


def _aa_disk_mask(size: int, cx: float, cy: float, r: float) -> np.ndarray:
    """Return a float32 alpha mask (0..1) of an anti-aliased filled disk."""
    yy, xx = np.mgrid[0:size, 0:size]
    d = np.sqrt((xx + 0.5 - cx) ** 2 + (yy + 0.5 - cy) ** 2)
    # 1px feather for AA; clamp outside to 0, inside to 1
    return np.clip(1.0 - (d - r + 0.5), 0.0, 1.0).astype(np.float32)


def _aa_line_mask(size: int, x0: float, y0: float, x1: float, y1: float,
                  width: float) -> np.ndarray:
    """Return a float32 alpha mask of an anti-aliased thick line segment."""
    yy, xx = np.mgrid[0:size, 0:size].astype(np.float32)
    # Project each pixel onto the segment, clamp to [0,1]
    dx = x1 - x0
    dy = y1 - y0
    seg_len_sq = dx * dx + dy * dy
    if seg_len_sq == 0:
        return np.zeros((size, size), dtype=np.float32)
    t = ((xx - x0) * dx + (yy - y0) * dy) / seg_len_sq
    t = np.clip(t, 0.0, 1.0)
    proj_x = x0 + t * dx
    proj_y = y0 + t * dy
    d = np.sqrt((xx - proj_x) ** 2 + (yy - proj_y) ** 2)
    half = width / 2.0
    return np.clip(half + 0.5 - d, 0.0, 1.0).astype(np.float32)


def _aa_rounded_rect_mask(size: int, radius: float) -> np.ndarray:
    """Anti-aliased rounded-rect mask filling the whole size x size canvas."""
    yy, xx = np.mgrid[0:size, 0:size].astype(np.float32)
    # SDF to nearest edge, positive inside the rect
    # Use the standard rounded-rect SDF (Inigo Quilez style).
    qx = np.abs(xx + 0.5 - size / 2.0) - (size / 2.0 - radius)
    qy = np.abs(yy + 0.5 - size / 2.0) - (size / 2.0 - radius)
    outside = np.sqrt(np.maximum(qx, 0.0) ** 2 + np.maximum(qy, 0.0) ** 2)
    inside = np.minimum(np.maximum(qx, qy), 0.0)
    sdf = outside + inside - radius
    return np.clip(0.5 - sdf, 0.0, 1.0).astype(np.float32)


def _radial_bg(size: int) -> np.ndarray:
    """Build the (size, size, 3) radial-gradient background RGB array."""
    yy, xx = np.mgrid[0:size, 0:size].astype(np.float32)
    cx = (size - 1) / 2.0
    cy = size * 0.40
    max_d = math.sqrt(cx * cx + cy * cy)
    d = np.sqrt((xx - cx) ** 2 + (yy - cy) ** 2) / max_d
    d = np.clip(d, 0.0, 1.0)
    bg = np.empty((size, size, 3), dtype=np.float32)
    for ch, (a, b) in enumerate(zip(BG_CENTER, BG_EDGE)):
        bg[..., ch] = a * (1 - d) + b * d
    return bg


def _gradient_disk(size: int, cx: float, cy: float, r: float,
                   top_color: tuple[int, int, int],
                   bot_color: tuple[int, int, int]) -> tuple[np.ndarray, np.ndarray]:
    """Return (rgb array, alpha mask) for a disk filled with a diagonal
    top-left -> bottom-right linear gradient. The gradient direction matches
    the SVG's linearGradient default (x1=0,y1=0,x2=1,y2=1)."""
    yy, xx = np.mgrid[0:size, 0:size].astype(np.float32)
    # Project onto the (1,1) diagonal; normalise to [0,1] across the disk bbox
    proj = (xx + yy) / (2.0 * (size - 1))
    proj = np.clip(proj, 0.0, 1.0)
    rgb = np.empty((size, size, 3), dtype=np.float32)
    for ch, (a, b) in enumerate(zip(top_color, bot_color)):
        rgb[..., ch] = a * (1 - proj) + b * proj
    mask = _aa_disk_mask(size, cx, cy, r)
    return rgb, mask


def render_master(size: int = 512) -> Image.Image:
    """Render the icon at the given square size, return RGBA PIL Image."""
    scale = size / VIEW

    # Background (radial) clipped to rounded square
    bg = _radial_bg(size)
    rect_mask = _aa_rounded_rect_mask(size, _to_px(BG_RADIUS, scale))[..., None]
    canvas = bg * rect_mask  # (size, size, 3) — already on rounded bg

    # Helper: paint a disk onto the float canvas (over blend).
    def paint_disk(cx_u: float, cy_u: float, r_u: float,
                   top: tuple[int, int, int], bot: tuple[int, int, int]) -> None:
        rgb, mask = _gradient_disk(
            size,
            _to_px(cx_u, scale),
            _to_px(cy_u, scale),
            _to_px(r_u, scale),
            top, bot,
        )
        m = mask[..., None]
        canvas[...] = rgb * m + canvas * (1 - m)

    # Connection lines (drawn before nodes so node fills cover line endpoints).
    line_mask = np.zeros((size, size), dtype=np.float32)
    for (ox, oy) in OUTER_NODES:
        line_mask = np.maximum(
            line_mask,
            _aa_line_mask(
                size,
                _to_px(ox, scale), _to_px(oy, scale),
                _to_px(HUB[0], scale), _to_px(HUB[1], scale),
                _to_px(2.0, scale),  # stroke-width 2 in logical units
            ),
        )
    line_mask *= 0.6  # opacity
    m = line_mask[..., None]
    canvas[:] = np.broadcast_to(LINE, canvas.shape) * m + canvas * (1 - m)

    # Outer agent nodes
    for (ox, oy) in OUTER_NODES:
        paint_disk(ox, oy, OUTER_R, NODE_LT, NODE_DK)

    # Central ensemble hub (drawn last so it sits on top)
    paint_disk(HUB[0], HUB[1], HUB_R, HUB_LT, HUB_DK)

    return Image.fromarray(np.clip(canvas, 0, 255).astype(np.uint8), mode="RGB")


# ---------- Output writers ----------

def write_ico(sources: dict[int, Image.Image], out: Path) -> None:
    """Write a multi-size .ico. Pillow reads each frame's own size and packs
    them all into the ICO container. The base image must be the largest."""
    # Sort descending; the first frame is the base image.
    sizes = sorted(sources.keys(), reverse=True)
    base = sources[sizes[0]].convert("RGBA")
    append = [sources[s].convert("RGBA") for s in sizes[1:]]
    base.save(
        out,
        format="ICO",
        append_images=append,
    )


def main() -> None:
    repo_root = Path(__file__).resolve().parents[2]
    public_dir = repo_root / "frontend" / "public"
    icons_dir = public_dir / "icons"
    icons_dir.mkdir(parents=True, exist_ok=True)

    # Master render (high-res for downsampling).
    master = render_master(512)

    # Apple touch icon (180x180)
    apple = master.resize((180, 180), Image.LANCZOS)
    apple.save(public_dir / "apple-touch-icon.png", format="PNG", optimize=True)

    # Sanity renders for legibility inspection.
    for sz in (16, 32, 48):
        master.resize((sz, sz), Image.LANCZOS).save(
            icons_dir / f"favicon-{sz}.png", format="PNG", optimize=True
        )

    # Multi-size .ico (16/32/48) — must use RGBA for full Windows support.
    sources = {sz: master.resize((sz, sz), Image.LANCZOS) for sz in (16, 32, 48)}
    write_ico(sources, public_dir / "favicon.ico")

    # Done — print a tiny audit so re-runs are easy to spot-check.
    out_files = [
        public_dir / "favicon.ico",
        public_dir / "favicon.svg",
        public_dir / "apple-touch-icon.png",
        icons_dir / "favicon-16.png",
        icons_dir / "favicon-32.png",
        icons_dir / "favicon-48.png",
    ]
    for p in out_files:
        size = p.stat().st_size if p.exists() else -1
        print(f"  {p.relative_to(repo_root)}  {size} bytes")


if __name__ == "__main__":
    main()