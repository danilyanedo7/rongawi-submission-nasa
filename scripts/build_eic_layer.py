"""recolour NASA EIC frames with MetBrewer hiroshige and reproject to web mercator."""

from __future__ import annotations

import json
import math
from pathlib import Path

import numpy as np
from PIL import Image, ImageFilter

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "assets" / "eic-temperature"
OUT = ROOT / "assets" / "eic-map"
META = ROOT / "assets" / "data" / "eic_layer.json"

# west, south, east, north
BOUNDS = (-115.0, -32.0, 90.0, 78.0)
PX_PER_DEG = 4.0
BLUR_PX = 3.0
EIC_SCALE = np.array([
    [235, 228, 250], [205, 168, 200], [160, 100, 210], [60, 50, 200],
    [40, 90, 215], [30, 130, 230], [60, 185, 230], [130, 205, 210],
    [110, 225, 140], [235, 240, 70], [250, 175, 40], [235, 80, 25],
    [175, 25, 15], [95, 15, 10], [150, 140, 140], [205, 200, 200],
], dtype=np.float32)
# MetBrewer hiroshige, reversed: blue = cooler, orange = warmer
HIROSHIGE = np.array([
    [30, 70, 110], [55, 103, 149], [82, 143, 173], [114, 188, 213], [170, 220, 224],
    [255, 230, 183], [255, 208, 111], [247, 170, 88], [239, 138, 71], [231, 98, 84],
], dtype=np.float32)

# turn blue -> orange at NASA's orange stop; NASA's dark red reaches full orange
DISPLAY_STOPS = ([0, 10 / 15, 13 / 15], [0, .5, 1])


def relative_warmth(src: np.ndarray) -> np.ndarray:
    """palette position, same ramp as the audio."""
    height = src.shape[0]
    latitudes = 90 - (np.arange(height) + .5) / height * 180
    best_distance = np.full(src.shape[:2], np.inf)
    warmth = np.zeros(src.shape[:2], dtype=np.float32)
    for index, (start, end) in enumerate(zip(EIC_SCALE[:-1], EIC_SCALE[1:])):
        delta = end - start
        fraction = np.clip(((src - start) * delta).sum(axis=2) / (delta @ delta), 0, 1)
        distance = ((src - start - fraction[..., None] * delta) ** 2).sum(axis=2)
        choose = distance < best_distance
        # polar white is not the hottest colour
        if index >= len(EIC_SCALE) - 3:
            choose &= np.abs(latitudes[:, None]) <= 35
        warmth[choose] = (index + fraction[choose]) / (len(EIC_SCALE) - 1)
        best_distance[choose] = distance[choose]
    return warmth


def display(values: np.ndarray) -> np.ndarray:
    return np.interp(values, *DISPLAY_STOPS)


def hiroshige(values: np.ndarray) -> np.ndarray:
    position = np.clip(values, 0, 1) * (len(HIROSHIGE) - 1)
    stops = np.linspace(0, len(HIROSHIGE) - 1, len(HIROSHIGE))
    rgb = np.stack([np.interp(position, stops, HIROSHIGE[:, channel]) for channel in range(3)], axis=-1)
    return np.round(rgb).astype(np.uint8)


def merc(lat_deg: np.ndarray | float):
    return np.log(np.tan(np.pi / 4 + np.radians(lat_deg) / 2))


def inv_merc(y: np.ndarray):
    return np.degrees(2 * np.arctan(np.exp(y)) - np.pi / 2)


def reproject(src: np.ndarray) -> np.ndarray:
    h, w = src.shape[:2]
    west, south, east, north = BOUNDS
    out_w = int(round((east - west) * PX_PER_DEG))
    y_top, y_bottom = merc(north), merc(south)
    # keep pixels square in mercator space
    out_h = int(round(out_w * (y_top - y_bottom) / math.radians(east - west)))

    lons = west + (np.arange(out_w) + 0.5) / out_w * (east - west)
    lats = inv_merc(y_top - (np.arange(out_h) + 0.5) / out_h * (y_top - y_bottom))
    sx = (lons + 180) / 360 * w - 0.5
    sy = (90 - lats) / 180 * h - 0.5

    x0 = np.clip(np.floor(sx).astype(int), 0, w - 2)
    y0 = np.clip(np.floor(sy).astype(int), 0, h - 2)
    fx = np.clip(sx - x0, 0, 1)[None, :]
    fy = np.clip(sy - y0, 0, 1)[:, None]
    if src.ndim == 3:
        fx, fy = fx[..., None], fy[..., None]
    yy, xx = y0[:, None], x0[None, :]
    top = src[yy, xx] * (1 - fx) + src[yy, xx + 1] * fx
    bottom = src[yy + 1, xx] * (1 - fx) + src[yy + 1, xx + 1] * fx
    return top * (1 - fy) + bottom * fy


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    frames = []
    for path in sorted(SRC.glob("frame-*.jpg")):
        with Image.open(path).convert("RGB") as image:
            src = np.asarray(image, dtype=np.float32)
        values = reproject(relative_warmth(src))
        # blur hides enlarged JPEG blocks
        image = Image.fromarray(hiroshige(display(values))).filter(ImageFilter.GaussianBlur(BLUR_PX))
        image.save(OUT / path.name, quality=92, optimize=True)
        frames.append(f"assets/eic-map/{path.name}")
    west, south, east, north = BOUNDS
    META.write_text(json.dumps({
        "source": "NASA Earth Information Center - GEOS-FP near-surface temperature (SVS 5147)",
        "source_url": "https://svs.gsfc.nasa.gov/5147/",
        "projection": "mercator",
        "colormap": "MetBrewer Hiroshige (reversed)",
        "display_blur_px": BLUR_PX,
        "colour_meaning": "Relative EIC image warmth, not calibrated degrees; original imagery retained for audio analysis",
        "legend_colors": ["#%02x%02x%02x" % tuple(rgb) for rgb in hiroshige(np.linspace(0, 1, len(HIROSHIGE)))],
        "bounds": {"west": west, "south": south, "east": east, "north": north},
        "frames": frames,
    }, indent=2) + "\n")
    print(f"wrote {len(frames)} hiroshige frames to {OUT.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
