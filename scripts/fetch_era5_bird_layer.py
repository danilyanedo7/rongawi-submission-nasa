"""optional: fetch daily ERA5 summaries for twelve route anchors per journey."""

from __future__ import annotations

import argparse
import csv
import io
import json
import math
import zipfile
from datetime import date
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "assets" / "data"
DEFAULT_OUTPUT = DATA / "era5_bird_layer.json"
DEFAULT_CACHE = ROOT / "extra" / "era5-cache"
DATASET = "reanalysis-era5-single-levels-timeseries"
BIRDS = ("Eric", "Nico", "Sanne")
VARIABLES = (
    "10m_u_component_of_wind",
    "10m_v_component_of_wind",
    "2m_temperature",
    "2m_dewpoint_temperature",
    "total_precipitation",
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--samples",
        type=int,
        default=12,
        help="number of route anchors per bird, matching the twelve audio sections",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=DEFAULT_OUTPUT,
        help="JSON layer written for the page and audio generator",
    )
    parser.add_argument(
        "--cache-dir",
        type=Path,
        default=DEFAULT_CACHE,
        help="directory for raw CDS downloads; this repo keeps it under ignored extra/",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="list the route anchors without contacting Copernicus",
    )
    return parser.parse_args()


def load_json(path: Path):
    return json.loads(path.read_text())


def parse_date(value: str) -> date:
    return date.fromisoformat(value[:10])


def haversine_km(first: dict, second: dict) -> float:
    radius_km = 6371.0088
    lat_one = math.radians(float(first["lat"]))
    lat_two = math.radians(float(second["lat"]))
    delta_lat = lat_two - lat_one
    delta_lon = math.radians(float(second["lon"]) - float(first["lon"]))
    hav = math.sin(delta_lat / 2) ** 2 + math.cos(lat_one) * math.cos(lat_two) * math.sin(delta_lon / 2) ** 2
    return radius_km * 2 * math.asin(math.sqrt(hav))


def bearing_deg(first: dict, second: dict) -> float:
    lat_one = math.radians(float(first["lat"]))
    lat_two = math.radians(float(second["lat"]))
    delta_lon = math.radians(float(second["lon"]) - float(first["lon"]))
    east = math.sin(delta_lon) * math.cos(lat_two)
    north = math.cos(lat_one) * math.sin(lat_two) - math.sin(lat_one) * math.cos(lat_two) * math.cos(delta_lon)
    return (math.degrees(math.atan2(east, north)) + 360) % 360


def movement_records(points: list[dict]) -> list[dict]:
    """great-circle speed and bearing per day."""

    ordered = sorted(points, key=lambda point: point["date"])
    records = []
    for index, point in enumerate(ordered):
        if len(ordered) == 1:
            previous, current = point, point
        elif index == 0:
            previous, current = point, ordered[index + 1]
        else:
            previous, current = ordered[index - 1], point

        elapsed_hours = max((parse_date(current["date"]) - parse_date(previous["date"])).days * 24, 1)
        distance = haversine_km(previous, current)
        records.append({
            **point,
            "bird_speed_kmh": distance / elapsed_hours,
            "bird_heading_deg": bearing_deg(previous, current),
            "bird_dx_km": distance,
            "bird_dt_hours": elapsed_hours,
        })
    return records


def choose_samples(points: list[dict], count: int) -> list[dict]:
    if count < 2:
        raise ValueError("--samples must be at least 2")
    if len(points) <= count:
        return points
    indexes = [round(index * (len(points) - 1) / (count - 1)) for index in range(count)]
    return [points[index] for index in indexes]


def grid_value(value: float) -> float:
    """snap to the 0.25° ERA5 grid."""

    return round(value * 4) / 4


def request_for(point: dict) -> dict:
    return {
        "variable": list(VARIABLES),
        "location": {
            "longitude": grid_value(float(point["lon"])),
            "latitude": grid_value(float(point["lat"])),
        },
        "date": [point["date"]],
        "data_format": "csv",
    }


def cache_path(cache_dir: Path, point: dict) -> Path:
    latitude = grid_value(float(point["lat"]))
    longitude = grid_value(float(point["lon"]))
    return cache_dir / f"{point['date']}_{latitude:+.2f}_{longitude:+.2f}.download"


def download_text(client, point: dict, cache_dir: Path) -> str:
    cache_dir.mkdir(parents=True, exist_ok=True)
    target = cache_path(cache_dir, point)
    if not target.exists():
        client.retrieve(DATASET, request_for(point), str(target))

    if zipfile.is_zipfile(target):
        with zipfile.ZipFile(target) as archive:
            csv_names = [name for name in archive.namelist() if name.lower().endswith(".csv")]
            if not csv_names:
                raise ValueError(f"CDS download contains no CSV file: {target}")
            return archive.read(csv_names[0]).decode("utf-8-sig")
    return target.read_text(encoding="utf-8-sig")


def csv_rows(text: str) -> list[dict[str, str]]:
    lines = [line for line in text.splitlines() if line.strip()]
    if not lines:
        raise ValueError("CDS returned an empty CSV")

    header_index = next(
        (
            index
            for index, line in enumerate(lines)
            if "time" in line.lower() and any(variable in line for variable in VARIABLES)
        ),
        0,
    )
    reader = csv.DictReader(io.StringIO("\n".join(lines[header_index:])))
    rows = [{(key or "").strip(): (value or "").strip() for key, value in row.items()} for row in reader]
    if not rows:
        raise ValueError("CDS returned no hourly rows")
    return rows


def column_value(row: dict[str, str], variable: str) -> float | None:
    for key, value in row.items():
        normalized = key.lower().replace(" ", "_")
        if normalized == variable or variable in normalized:
            try:
                return float(value)
            except (TypeError, ValueError):
                return None
    return None


def values(rows: list[dict[str, str]], variable: str) -> list[float]:
    return [value for row in rows if (value := column_value(row, variable)) is not None and math.isfinite(value)]


def mean(xs: list[float]) -> float | None:
    return sum(xs) / len(xs) if xs else None


def relative_humidity_pct(temperature_k: float | None, dewpoint_k: float | None) -> float | None:
    if temperature_k is None or dewpoint_k is None:
        return None
    temperature_c = temperature_k - 273.15
    dewpoint_c = dewpoint_k - 273.15
    saturation = 6.112 * math.exp((17.62 * temperature_c) / (243.12 + temperature_c))
    vapour = 6.112 * math.exp((17.62 * dewpoint_c) / (243.12 + dewpoint_c))
    return max(0.0, min(100.0, 100 * vapour / saturation))


def summarize_weather(text: str, motion: dict) -> dict:
    rows = csv_rows(text)
    u10 = mean(values(rows, "10m_u_component_of_wind"))
    v10 = mean(values(rows, "10m_v_component_of_wind"))
    temperature = mean(values(rows, "2m_temperature"))
    dewpoint = mean(values(rows, "2m_dewpoint_temperature"))
    precipitation_m = sum(values(rows, "total_precipitation"))

    wind_speed = math.hypot(u10, v10) if u10 is not None and v10 is not None else None
    wind_from = (180 + math.degrees(math.atan2(u10, v10))) % 360 if u10 is not None and v10 is not None else None
    humidity = relative_humidity_pct(temperature, dewpoint)

    tailwind = None
    crosswind = None
    heading = motion.get("bird_heading_deg")
    if u10 is not None and v10 is not None and heading is not None:
        heading_rad = math.radians(heading)
        bird_east = math.sin(heading_rad)
        bird_north = math.cos(heading_rad)
        tailwind = u10 * bird_east + v10 * bird_north
        crosswind = u10 * math.cos(heading_rad) - v10 * math.sin(heading_rad)

    return {
        "u10_ms": u10,
        "v10_ms": v10,
        "wind_speed_ms": wind_speed,
        "wind_from_deg": wind_from,
        "relative_humidity_pct": humidity,
        "precipitation_mm": max(0.0, precipitation_m * 1000) if precipitation_m is not None else None,
        "tailwind_ms": tailwind,
        "crosswind_ms": crosswind,
        "temperature_k": temperature,
        "dewpoint_k": dewpoint,
    }


def rounded(value):
    if isinstance(value, float):
        return round(value, 4)
    if isinstance(value, dict):
        return {key: rounded(item) for key, item in value.items()}
    if isinstance(value, list):
        return [rounded(item) for item in value]
    return value


def load_samples(sample_count: int) -> dict[str, list[dict]]:
    daily = load_json(DATA / "daily.json")
    totals = {row["bird"]: row for row in load_json(DATA / "totals.json")}
    samples = {}
    for bird in BIRDS:
        total = totals[bird]
        route = [
            point for point in daily
            if point["bird"] == bird and total["departure_date"] <= point["date"] <= total["return_date"]
        ]
        samples[bird] = choose_samples(movement_records(route), sample_count)
    return samples


def run(args: argparse.Namespace) -> None:
    samples = load_samples(args.samples)
    request_points = [(bird, point) for bird in BIRDS for point in samples[bird]]
    if args.dry_run:
        for bird, point in request_points:
            request = request_for(point)
            location = request["location"]
            print(f"{bird:5} {point['date']}  {location['latitude']:+.2f}, {location['longitude']:+.2f}")
        print(f"{len(request_points)} route anchors across {len(BIRDS)} gulls")
        return

    try:
        import cdsapi
    except ImportError as error:
        raise SystemExit(
            "cdsapi is not installed. Run: python3 -m pip install 'cdsapi>=0.7.7'"
        ) from error

    client = cdsapi.Client()
    records = []
    for bird in BIRDS:
        for index, point in enumerate(samples[bird]):
            print(f"Fetching {bird} {point['date']} at {point['lat']:.2f}, {point['lon']:.2f}")
            weather = summarize_weather(download_text(client, point, args.cache_dir), point)
            records.append(rounded({
                "bird": bird,
                "journey_index": index,
                "journey_count": len(samples[bird]),
                "date": point["date"],
                "lat": point["lat"],
                "lon": point["lon"],
                "phase": point["phase"],
                "gap": point["gap"],
                "bird_speed_kmh": point["bird_speed_kmh"],
                "bird_heading_deg": point["bird_heading_deg"],
                "bird_dx_km": point["bird_dx_km"],
                "bird_dt_hours": point["bird_dt_hours"],
                **weather,
            }))

    output = {
        "source": "Copernicus Climate Change Service ERA5",
        "dataset": DATASET,
        "dataset_url": "https://cds.climate.copernicus.eu/datasets/reanalysis-era5-single-levels-timeseries",
        "resolution": "0.25 degree atmospheric grid; CDS nearest grid point",
        "sampling": f"One daily ERA5 summary at {args.samples} route anchors per gull",
        "variables": list(VARIABLES),
        "derived": [
            "bird_speed_kmh and bird_heading_deg from great-circle dx/dt between daily GPS points",
            "wind_speed_ms and wind_from_deg from 10m u/v components",
            "relative_humidity_pct from 2m temperature and 2m dewpoint",
            "precipitation_mm from hourly total precipitation accumulations",
            "tailwind_ms and crosswind_ms relative to the bird heading",
        ],
        "units": {
            "bird_speed_kmh": "km/h",
            "bird_heading_deg": "degrees clockwise from north",
            "wind_speed_ms": "m/s",
            "wind_from_deg": "meteorological degrees clockwise from north",
            "relative_humidity_pct": "%",
            "precipitation_mm": "mm per requested day",
            "tailwind_ms": "m/s; positive along flight direction",
            "crosswind_ms": "m/s; positive to the bird's right",
        },
        "records": records,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(output, indent=2) + "\n")
    print(f"Wrote {len(records)} records to {args.output}")


if __name__ == "__main__":
    run(parse_args())
