"""hourly ERA5 100 m wind grid from open-meteo for one bird day (port of bird_migration r/06_wind_grid.R)."""
from __future__ import annotations

import argparse
import json
import math
import time
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
API = "https://archive-api.open-meteo.com/v1/archive"


def frange(start: float, stop: float, step: float) -> list[float]:
    return [round(start + i * step, 4) for i in range(int(round((stop - start) / step)) + 1)]


def fetch(lats: list[float], lons: list[float], date: str) -> list[dict]:
    query = urllib.parse.urlencode({
        "latitude": ",".join(map(str, lats)), "longitude": ",".join(map(str, lons)),
        "start_date": date, "end_date": date, "hourly": "wind_speed_100m,wind_direction_100m",
        "wind_speed_unit": "ms", "timezone": "GMT", "models": "era5",
    })
    request = urllib.request.Request(f"{API}?{query}", headers={"User-Agent": "earth-information-jukebox"})
    for attempt in range(4):
        try:
            with urllib.request.urlopen(request, timeout=60) as response:
                body = json.load(response)
            return body if isinstance(body, list) else [body]
        except Exception:
            if attempt == 3:
                raise
            time.sleep(5 * (attempt + 1))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--date", default="2024-10-30")
    parser.add_argument("--lon", type=float, nargs=2, default=[-12, 1])
    parser.add_argument("--lat", type=float, nargs=2, default=[40, 50])
    parser.add_argument("--step", type=float, default=0.5)
    parser.add_argument("--hours", type=int, default=21)
    parser.add_argument("--out", type=Path, default=ROOT / "assets" / "data" / "nico_wind_grid.json")
    args = parser.parse_args()
    lons, lats, hours = frange(*args.lon, args.step), frange(*args.lat, args.step), range(args.hours)
    coords = [(lat, lon) for lat in lats for lon in lons]
    rows = []
    for i in range(0, len(coords), 80):
        chunk = coords[i:i + 80]
        rows += fetch([c[0] for c in chunk], [c[1] for c in chunk], args.date)
    u = [[[None] * len(lons) for _ in lats] for _ in hours]
    v = [[[None] * len(lons) for _ in lats] for _ in hours]
    for (lat, lon), row in zip(coords, rows):
        speed, direction = row["hourly"]["wind_speed_100m"], row["hourly"]["wind_direction_100m"]
        for t in hours:
            # meteorological "from" direction -> u/v the air moves along
            radians = math.radians(direction[t])
            u[t][lats.index(lat)][lons.index(lon)] = round(-speed[t] * math.sin(radians), 2)
            v[t][lats.index(lat)][lons.index(lon)] = round(-speed[t] * math.cos(radians), 2)
    args.out.write_text(json.dumps({
        "source": "Open-Meteo Historical Weather API",
        "source_url": "https://open-meteo.com/en/docs/historical-weather-api",
        "model": "ERA5", "height_m": 100, "date": args.date, "lons": lons, "lats": lats,
        "times": [f"{args.date}T{t:02d}:00:00Z" for t in hours], "u": u, "v": v,
    }, separators=(",", ":")))
    print(f"wrote {len(lats)}x{len(lons)} grid, {len(hours)} hours for {args.date} to {args.out.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
