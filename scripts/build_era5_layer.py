"""prepare the hourly ERA5 grid for the map and audio."""

from __future__ import annotations

import json
import math
from datetime import datetime
from pathlib import Path

import numpy as np


ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "assets" / "data"


def sample_grid(grid: np.ndarray, lons: list[float], lats: list[float], lon: float, lat: float):
    if not (lons[0] <= lon <= lons[-1] and lats[0] <= lat <= lats[-1]):
        return None
    x = (lon - lons[0]) / (lons[1] - lons[0])
    y = (lat - lats[0]) / (lats[1] - lats[0])
    j, i = min(math.floor(x), len(lons) - 2), min(math.floor(y), len(lats) - 2)
    fx, fy = x - j, y - i
    return float((grid[i, j] * (1 - fx) + grid[i, j + 1] * fx) * (1 - fy) + (grid[i + 1, j] * (1 - fx) + grid[i + 1, j + 1] * fx) * fy)


def heading(first: dict, second: dict) -> float:
    a, b = math.radians(first["lat"]), math.radians(second["lat"])
    delta = math.radians(second["lon"] - first["lon"])
    east = math.sin(delta) * math.cos(b)
    north = math.cos(a) * math.sin(b) - math.sin(a) * math.cos(b) * math.cos(delta)
    return math.atan2(east, north)


def wind_record(point: dict, u: float, v: float, direction: float) -> dict:
    return {
        "bird": point["bird"], "date": point["date"], "lat": point["lat"], "lon": point["lon"],
        "u": round(u, 4), "v": round(v, 4), "speed": round(math.hypot(u, v), 4),
        "dir_from": round(math.degrees(math.atan2(-u, -v)) % 360, 2),
        "heading_deg": round(math.degrees(direction) % 360, 2),
        "tail": round(u * math.sin(direction) + v * math.cos(direction), 4),
        "cross": round(u * math.cos(direction) - v * math.sin(direction), 4),
    }


def main():
    source = json.loads((DATA / "nico_wind_grid.json").read_text())
    daily = json.loads((DATA / "daily.json").read_text())
    u, v = np.array(source["u"], dtype=float), np.array(source["v"], dtype=float)
    expected = (len(source["times"]), len(source["lats"]), len(source["lons"]))
    if u.shape != expected or v.shape != expected or not np.isfinite([u, v]).all():
        raise ValueError("The hourly wind grid has missing or mismatched values")
    rows, hourly = [], []
    for bird in ("Eric", "Nico", "Sanne"):
        points = sorted([point for point in daily if point["bird"] == bird], key=lambda point: point["date"])
        for index, point in enumerate(points):
            if point["date"] != source["date"] or point.get("gap"):
                continue
            next_point = points[min(index + 1, len(points) - 1)]
            direction = heading(point, next_point)
            start = datetime.fromisoformat(point["date"] + "T00:00:00+00:00")
            matched = []
            for time, a, b in zip(source["times"], u, v):
                fraction = (datetime.fromisoformat(time.replace("Z", "+00:00")) - start).total_seconds() / 86400
                position = {**point, "lat": point["lat"] + (next_point["lat"] - point["lat"]) * fraction, "lon": point["lon"] + (next_point["lon"] - point["lon"]) * fraction}
                east = sample_grid(a, source["lons"], source["lats"], position["lon"], position["lat"])
                north = sample_grid(b, source["lons"], source["lats"], position["lon"], position["lat"])
                if east is not None and north is not None:
                    matched.append({**wind_record(position, east, north, direction), "time": time})
            if not matched:
                continue
            hourly.extend(matched)
            rows.append({**wind_record(point, float(np.mean([row["u"] for row in matched])), float(np.mean([row["v"] for row in matched])), direction), "valid_from": matched[0]["time"], "valid_until": matched[-1]["time"]})
    layer = {
        "source": source["source"], "source_url": source["source_url"], "model": source["model"], "height_m": source["height_m"],
        "source_file": "nico_wind_grid.json", "date": source["date"], "hours": source["times"],
        "sampling": "Spatial bilinear interpolation at positions interpolated between adjacent recorded days; daily row averages matching hourly vectors; outside the grid remains unknown",
        "speed_max": round(float(np.hypot(u, v).max()), 4),
        "tail_abs_max": max([abs(row["tail"]) for row in hourly], default=1),
        "rows": rows, "hourly_rows": hourly,
    }
    (DATA / "era5_wind.json").write_text(json.dumps(layer, indent=2) + "\n")
    scale = min(5.0, 126 / max(float(np.abs(u).max()), float(np.abs(v).max()), 1))
    packed = np.rint(np.stack((u, v), axis=1) * scale).astype(np.int8)
    (DATA / "era5_wind_grid.bin").write_bytes(packed.tobytes())
    (DATA / "era5_wind_grid.json").write_text(json.dumps({
        "source": source["source"], "source_url": source["source_url"], "model": source["model"], "height_m": source["height_m"],
        "dates": [source["date"]], "times": source["times"], "lons": source["lons"], "lats": source["lats"], "scale": scale,
        "layout": "time, component (u then v), latitude, longitude; signed int8 divided by scale",
    }, indent=2) + "\n")
    print(f"Prepared {len(source['times'])} hourly ERA5 grids and {len(rows)} matching bird day")


if __name__ == "__main__":
    main()
