#!/usr/bin/env python3
"""turn a bird's migration (daily positions) plus weather into MIDI, told from the bird's side:
the bird is the melody, the world around it the harmony, and the colony is home, the key the
music keeps returning to.

    python story_to_midi.py daily.json --wind era5_wind.json --eic eic_temperature_profile.json
    python story_to_midi.py daily.json --eic eic_temperature_profile.json --trio
    python story_to_midi.py daily.json --wind era5_wind.json --eic eic_temperature_profile.json --closeup

bird data
    distance per day         pulses per beat (1-4) and how busy the melody is
    low-distance run         rest: one held note, no pulses, more reverb (a stopover)
    distance from colony     loudness of a low drone on the home key; near the colony the pad
                             plays the home chord, and the piece ends on it (coda)
    outward / return         the same motif, mirrored on the return
    latitude                 melody register (north high, south low)
    longitude                stereo pan (west left)
    tag outage               silence
    day length               melody velocity and brightness (CC74)
weather
    temperature or EIC warmth    pad pitch
    EIC brightness               pad loudness (CC11)
    wind speed                   tempo
    tailwind / headwind          pad interval: open with a tailwind, tense with a headwind
    crosswind                    pitch wobble on the melody
    humidity                     reverb (CC91)
    rain                         short high plucks

input is CSV, TSV, JSON or JSON Lines with a date, lat and lon; any other column is optional
and skipped when missing. columns are found by name (see COLUMN_ALIASES); use --col NAME=COLUMN
otherwise. --wind takes the ERA5 file (rows / hourly_rows) or any table with bird, date and
wind columns, matched by bird and date, so weather is only used on the days it exists.
--eic takes eic_temperature_profile.json; it has no coordinates, so its frames are spread
evenly over the journey and matched by date.
"""

import argparse
import json
import math
import re
from dataclasses import dataclass, replace
from pathlib import Path

import mido
import numpy as np
import pandas as pd

TICKS_PER_BEAT = 480

# instruments, one per bird (cycled), as General MIDI numbers
PROGRAMS = {
    "melody": [73, 68, 71],      # flute, oboe, clarinet
    "pad":    [89, 88, 91],      # warm pad, new age pad, choir pad
    "pulse":  [12, 11, 13],      # marimba, vibraphone, xylophone
    "rain":   [9, 9, 9],         # glockenspiel
    "drone":  [42],              # cello
}
LABEL = {"melody": "melody", "pad": "atmosphere", "pulse": "beats", "rain": "rain", "drone": "drone"}
VELOCITY = {"melody": 80, "pad": 48, "pulse": 58, "rain": 45, "drone": 50}
MOTIF = [0, 2, 4, 3, 1, 4, 5, 2]               # melody contour in scale steps (mirrored on the return)
INTERVAL_LADDER = [12, 7, 5, 4, 3, 2, 6, 1]    # semitones, most open to most tense
WOBBLE_MAX = 3000                              # pitch-bend units for the strongest crosswind (8192 = 2 semitones)

SCALES = {
    "chromatic": list(range(12)),
    "major": [0, 2, 4, 5, 7, 9, 11],
    "minor": [0, 2, 3, 5, 7, 8, 10],
    "major_pentatonic": [0, 2, 4, 7, 9],
    "minor_pentatonic": [0, 3, 5, 7, 10],
}
KEYS = {"C": 0, "C#": 1, "DB": 1, "D": 2, "D#": 3, "EB": 3, "E": 4, "F": 5, "F#": 6,
        "GB": 6, "G": 7, "G#": 8, "AB": 8, "A": 9, "A#": 10, "BB": 10, "B": 11}

# first matching name wins, case-insensitive
COLUMN_ALIASES = {
    "timestamp": ["timestamp", "date_time", "datetime", "datetime_utc", "time_utc", "time", "date"],
    "lat": ["lat", "latitude"],
    "lon": ["lon", "lng", "long", "longitude"],
    "speed": ["speed_kmh", "speed_2d", "speed", "km", "km_day", "daily_km", "ground_speed", "velocity"],
    "heading": ["heading_deg", "heading", "bearing", "direction"],
    "colony": ["colony_km", "home_km", "dist_home_km", "distance_from_home"],
    "phase": ["phase"],
    "gap": ["gap", "gap_flag", "outage"],
    "temperature": ["temperature_c", "temperature_2m_mean", "temperature_2m", "temperature", "temp", "t2m",
                    "eic_warmth"],
    "brightness": ["eic_brightness", "brightness"],
    "wind_speed": ["wind_speed_ms", "wind_speed_10m_max", "wind_speed_10m", "wind_speed", "windspeed", "ws10m"],
    "wind_dir": ["wind_dir_deg", "wind_direction_10m_dominant", "wind_direction_10m", "wind_direction",
                 "wind_dir", "dir_from", "wd10m"],
    "wind_u": ["wind_u", "u10m", "u10", "u_wind", "eastward_wind"],
    "wind_v": ["wind_v", "v10m", "v10", "v_wind", "northward_wind"],
    "tailwind": ["tailwind", "tail", "tailwind_ms"],
    "crosswind": ["crosswind", "cross", "crosswind_ms"],
    "humidity": ["relative_humidity_2m_mean", "relative_humidity", "humidity_pct", "humidity"],
    "precip": ["precipitation_sum", "precipitation", "rain_sum", "rain", "precip_mm"],
    "bird": ["bird_name", "bird", "individual", "individual_id", "tag_id", "device_info_serial"],
}
STEP_MEAN_COLS = ["lat", "lon", "speed", "colony", "temperature", "wind_speed", "tailwind",
                  "crosswind", "brightness", "humidity", "precip"]
SCALE_COLS = STEP_MEAN_COLS
PREFERRED_JSON_KEYS = ("records", "data", "rows", "points", "track", "tracks", "observations", "items")
# ERA5 wind file names, renamed to the standard ones above
WIND_RENAME = {"speed": "wind_speed", "dir_from": "wind_dir", "tail": "tailwind", "cross": "crosswind",
               "u": "wind_u", "v": "wind_v"}


# loading
def json_to_frame(raw):
    if isinstance(raw, list):
        return pd.json_normalize(raw)
    if not isinstance(raw, dict):
        raise ValueError("JSON must be a list of records or an object that contains one.")
    if raw and all(isinstance(v, list) for v in raw.values()):
        if not any(v and isinstance(v[0], (dict, list)) for v in raw.values()):
            return pd.DataFrame(raw)
    record_lists = {k: v for k, v in raw.items() if isinstance(v, list) and v and isinstance(v[0], dict)}
    if not record_lists:
        raise ValueError("Could not find a list of records in the JSON file.")
    for key in PREFERRED_JSON_KEYS:
        if key in record_lists:
            return pd.json_normalize(record_lists[key])
    if len(record_lists) == 1:
        return pd.json_normalize(next(iter(record_lists.values())))
    frames = []
    for name, records in record_lists.items():
        frame = pd.json_normalize(records)
        if not {str(c).lower() for c in frame.columns} & set(COLUMN_ALIASES["bird"]):
            frame["bird_name"] = name
        frames.append(frame)
    return pd.concat(frames, ignore_index=True)


def read_table(path):
    path = Path(path)
    ext = path.suffix.lower()
    if ext in (".csv", ".txt"):
        return pd.read_csv(path)
    if ext == ".tsv":
        return pd.read_csv(path, sep="\t")
    if ext in (".jsonl", ".ndjson"):
        return pd.read_json(path, lines=True)
    if ext == ".json":
        with open(path, encoding="utf-8") as fh:
            return json_to_frame(json.load(fh))
    raise ValueError(f"Unsupported file type '{ext}'. Use .csv, .tsv, .json or .jsonl.")


def canonicalize(df, overrides):
    lower = {str(c).strip().lower(): c for c in df.columns}
    found = {}
    for canon, aliases in COLUMN_ALIASES.items():
        if canon in overrides:
            if overrides[canon] not in df.columns:
                raise ValueError(f"--col {canon}={overrides[canon]}: column not found. "
                                 f"Available: {', '.join(map(str, df.columns))}")
            found[canon] = overrides[canon]
            continue
        for alias in aliases:
            if alias in lower:
                found[canon] = lower[alias]
                break
    missing = [c for c in ("timestamp", "lat", "lon") if c not in found]
    if missing:
        raise ValueError(f"Could not find columns for: {', '.join(missing)}.\n"
                         f"Columns in file: {', '.join(map(str, df.columns))}\n"
                         f"Tell the script which one to use, e.g. --col {missing[0]}=YOUR_COLUMN")
    return pd.DataFrame({canon: df[src] for canon, src in found.items()}), found


def to_utc(ts):
    if pd.api.types.is_numeric_dtype(ts):
        unit = "ms" if ts.median() > 1e11 else "s"
        return pd.to_datetime(ts, unit=unit, utc=True, errors="coerce")
    return pd.to_datetime(ts, utc=True, errors="coerce")


def clean(d):
    d = d.copy()
    d["timestamp"] = to_utc(d["timestamp"])
    for c in d.columns:
        if c not in ("timestamp", "bird", "phase", "gap"):
            d[c] = pd.to_numeric(d[c], errors="coerce")
    if "gap" in d.columns:
        d["gap"] = d["gap"].astype(str).str.lower().isin(["true", "1", "yes", "t"])
    else:
        d["gap"] = False
    if "bird" in d.columns:
        d["bird"] = d["bird"].astype(str)
    d = d.dropna(subset=["timestamp", "lat", "lon"]).sort_values("timestamp").reset_index(drop=True)
    derived = False
    if "wind_speed" not in d.columns and {"wind_u", "wind_v"} <= set(d.columns):
        u, v = d["wind_u"], d["wind_v"]
        d["wind_speed"] = np.hypot(u, v)
        d["wind_dir"] = np.degrees(np.arctan2(-u, -v)) % 360.0
        derived = True
    d["_day"] = d["timestamp"].dt.strftime("%Y-%m-%d")
    return d, derived


# merging wind and EIC
def load_wind(path):
    """returns (daily wind rows, hourly rows or None, metadata), with standard column names."""
    path = Path(path)
    meta, hourly = {}, None
    if path.suffix.lower() == ".json":
        with open(path, encoding="utf-8") as fh:
            raw = json.load(fh)
        if isinstance(raw, dict) and ("rows" in raw or "hourly_rows" in raw):
            meta = {k: raw[k] for k in ("speed_max", "tail_abs_max", "source", "model", "height_m") if k in raw}
            daily = pd.DataFrame(raw.get("rows", []))
            hourly = pd.DataFrame(raw["hourly_rows"]) if raw.get("hourly_rows") else None
            if daily.empty and hourly is not None:
                daily = hourly.groupby(["bird", "date"], as_index=False).mean(numeric_only=True)
        else:
            daily = json_to_frame(raw)
    else:
        daily = read_table(path)
    rename = lambda df: df.rename(columns={k: v for k, v in WIND_RENAME.items() if k in df.columns})
    daily = rename(daily)
    hourly = rename(hourly) if hourly is not None else None
    if "date" not in daily.columns and "timestamp" in daily.columns:
        daily["date"] = daily["timestamp"]
    daily["date"] = pd.to_datetime(daily["date"], utc=True, errors="coerce").dt.strftime("%Y-%m-%d")
    if hourly is not None and "time" in hourly.columns:
        hourly["time"] = pd.to_datetime(hourly["time"], utc=True, errors="coerce")
    return daily, hourly, meta


def merge_wind(d, wind_daily, notes):
    keys = ["_day"] + (["bird"] if "bird" in d.columns and "bird" in wind_daily.columns else [])
    w = wind_daily.rename(columns={"date": "_day"}).copy()
    w["_day"] = w["_day"].astype(str)
    take = [c for c in ("wind_speed", "wind_dir", "tailwind", "crosswind", "wind_u", "wind_v", "heading_deg")
            if c in w.columns]
    take = [c for c in take if c not in d.columns or c == "heading_deg"]
    if "heading_deg" in take and "heading" in d.columns:
        take.remove("heading_deg")
    w = w[keys + take + [c for c in ("lat", "lon") if c in w.columns]].drop_duplicates(keys)
    w = w.rename(columns={"lat": "_wlat", "lon": "_wlon", "heading_deg": "heading"})
    m = d.merge(w, on=keys, how="left")
    hit = m[[c for c in ("wind_speed", "tailwind", "crosswind", "wind_dir") if c in m.columns]].notna().any(axis=1)
    notes.append(f"wind: {int(hit.sum())} of {len(m)} daily rows got wind data "
                 f"(wind file has {len(wind_daily)} daily row(s))")
    if "_wlat" in m.columns:
        both = m[hit & m["_wlat"].notna()]
        if len(both):
            err = max((both["lat"] - both["_wlat"]).abs().max(), (both["lon"] - both["_wlon"]).abs().max())
            notes.append(f"wind positions agree with the journey positions (max difference {err:.3f} degrees)")
            if err > 0.5:
                notes.append("WARNING: wind positions differ from the journey positions; check the dates")
        m = m.drop(columns=["_wlat", "_wlon"])
    unmatched = len(wind_daily) - int(hit.sum())
    if unmatched > 0:
        notes.append(f"WARNING: {unmatched} wind row(s) found no matching bird + date in the journey")
    return m


def attach_eic(d, profile_path, start, end, notes):
    prof = read_table(profile_path)
    for c in ("file", "brightness", "warmth"):
        if c not in prof.columns:
            raise ValueError(f"{profile_path}: missing '{c}' column.")
    prof = prof.sort_values("file").reset_index(drop=True)
    n = len(prof)
    t0 = utc_timestamp(start) if start else d["timestamp"].min()
    t1 = utc_timestamp(end) if end else d["timestamp"].max()
    span = (t1 - t0).total_seconds()
    if span <= 0:
        raise ValueError("--eic-end must be after --eic-start.")
    frac = ((d["timestamp"] - t0).dt.total_seconds() / span).clip(0, 1)
    idx = np.minimum((frac * n).astype(int), n - 1).to_numpy()
    d = d.copy()
    d["eic_frame"] = idx + 1
    if "brightness" not in d.columns:
        d["brightness"] = prof["brightness"].to_numpy()[idx]
    if "temperature" not in d.columns:
        d["temperature"] = prof["warmth"].to_numpy()[idx]
        notes.append("temperature = EIC warmth (a relative index from the frames, not degrees)")
    notes.append(f"EIC: {n} frames spread over {t0:%Y-%m-%d} to {t1:%Y-%m-%d}, matched by DATE "
                 f"(the profile has no coordinates)")
    return d


# geometry
def bearing_degrees(lat1, lon1, lat2, lon2):
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dl = math.radians(lon2 - lon1)
    y = math.sin(dl) * math.cos(p2)
    x = math.cos(p1) * math.sin(p2) - math.sin(p1) * math.cos(p2) * math.cos(dl)
    return (math.degrees(math.atan2(y, x)) + 360.0) % 360.0


def haversine_km(lat1, lon1, lat2, lon2):
    p1, p2 = math.radians(lat1), math.radians(lat2)
    a = (math.sin((p2 - p1) / 2) ** 2
         + math.cos(p1) * math.cos(p2) * math.sin(math.radians(lon2 - lon1) / 2) ** 2)
    return 2 * 6371.0 * math.asin(math.sqrt(a))


# the journey is cut into equal time slices; each slice is one musical step
def step_table(d, steps, t0, t1):
    span = (t1 - t0).total_seconds()
    if span <= 0:
        raise ValueError("The track has no time span (all timestamps are equal).")
    frac = (d["timestamp"] - t0).dt.total_seconds() / span
    d = d.assign(step=np.minimum((frac * steps).astype(int), steps - 1))
    ok = d[~d["gap"]]
    g = ok.groupby("step")
    cols = [c for c in STEP_MEAN_COLS if c in ok.columns]
    out = g[cols].mean().reindex(range(steps))
    for ang in ("heading", "wind_dir"):
        if ang in ok.columns:
            rad = np.radians(ok[ang])
            tmp = pd.DataFrame({"step": ok["step"], "s": np.sin(rad), "c": np.cos(rad)}).dropna()
            tmp = tmp.groupby("step").mean()
            out[ang] = np.degrees(np.arctan2(tmp["s"], tmp["c"])) % 360.0
    n_ok = g.size().reindex(range(steps)).fillna(0).to_numpy()
    gaps = d["timestamp"].drop_duplicates().sort_values().diff().median()
    dt = gaps.total_seconds() if pd.notna(gaps) and gaps.total_seconds() > 0 else 86400.0
    expected = max(1.0, span / steps / dt)
    out["coverage"] = n_ok / expected
    out["present"] = out["coverage"] >= 0.5
    if "phase" in ok.columns:
        out["ret"] = g["phase"].apply(lambda s: (s.astype(str).str.lower() == "return").mean()).reindex(range(steps))
    else:
        out["ret"] = np.nan
    return out


def enrich(sd, step_hours):
    n = len(sd)
    lat, lon = sd["lat"].to_numpy(float), sd["lon"].to_numpy(float)
    valid = np.flatnonzero(np.isfinite(lat) & np.isfinite(lon))
    if len(valid) < 2:
        raise ValueError("Not enough positions to build a journey (need data in at least 2 steps).")
    head, spd = np.full(n, np.nan), np.full(n, np.nan)
    for a, b in zip(valid[:-1], valid[1:]):
        head[a] = bearing_degrees(lat[a], lon[a], lat[b], lon[b])
        spd[a] = haversine_km(lat[a], lon[a], lat[b], lon[b]) / ((b - a) * step_hours)
    head[valid[-1]], spd[valid[-1]] = head[valid[-2]], spd[valid[-2]]
    derived = pd.Series(head, index=sd.index)
    sd["heading"] = sd["heading"].where(sd["heading"].notna(), derived) if "heading" in sd.columns else derived
    if "speed" not in sd.columns:
        sd["speed"] = spd
    if "colony" not in sd.columns:
        first = valid[0]
        sd["colony"] = [haversine_km(lat[first], lon[first], la, lo) if np.isfinite(la) else np.nan
                        for la, lo in zip(lat, lon)]
    if sd["ret"].isna().all():
        peak = int(np.nanargmax(sd["colony"].to_numpy(float)))
        sd["ret"] = (np.arange(n) > peak).astype(float)
    return sd


class Scales:
    """min and max of every measurement over all birds, so the birds can be compared."""

    def __init__(self, tables):
        allt = pd.concat(tables, ignore_index=True)
        allt = allt[allt["present"].astype(bool)]
        self.lo, self.hi = {}, {}
        for c in SCALE_COLS:
            if c in allt.columns and allt[c].notna().any():
                self.lo[c], self.hi[c] = float(allt[c].min()), float(allt[c].max())
        if "speed" in self.lo:
            self.lo["speed"], self.hi["speed"] = 0.0, float(np.nanpercentile(allt["speed"], 95))
        for c in ("colony", "precip"):
            if c in self.lo:
                self.lo[c] = 0.0

    def override(self, col, lo, hi):
        self.lo[col], self.hi[col] = float(lo), float(hi)

    def norm(self, col, series):
        if col not in self.lo:
            return pd.Series(np.nan, index=series.index)
        lo, hi = self.lo[col], self.hi[col]
        if hi - lo < 1e-12:
            return series.where(series.isna(), 0.5)
        return ((series - lo) / (hi - lo)).clip(0, 1)


def fill(series, default):
    return series.ffill().bfill().fillna(default)


# data to music
def snap(st, x):
    return int(st.scale[np.abs(st.scale - x).argmin()])


def tonic_near(st, x):
    cands = st.scale[(st.scale - st.key) % 12 == 0]
    return int(cands[np.abs(cands - x).argmin()])


def map_wind_to_bpm(wind_n, st):
    return st.bpm_lo + wind_n * (st.bpm_hi - st.bpm_lo)


def map_alignment_to_interval(alignment):
    """+1 tailwind to -1 headwind, as semitones above the pad root."""
    idx = int(round((1.0 - alignment) / 2.0 * (len(INTERVAL_LADDER) - 1)))
    return INTERVAL_LADDER[min(max(idx, 0), len(INTERVAL_LADDER) - 1)]


def map_effort_to_subdivision(effort, st):
    return 1 + int(round(effort * (st.max_subdivision - 1)))


def daylight_hours(lat, dates):
    doy = np.asarray(dates.dayofyear, dtype=float)
    decl = np.radians(23.44) * np.sin(2 * np.pi * (doy - 81) / 365.0)
    x = np.clip(-np.tan(np.radians(lat)) * np.tan(decl), -1, 1)
    return 24.0 / np.pi * np.arccos(x)


def build_features(sd, S, st, dates, wind_from):
    """one row per step with all musical values."""
    idx = sd.index
    nan = pd.Series(np.nan, index=idx)

    def col(c):
        return sd[c] if c in sd.columns else nan

    f = pd.DataFrame(index=idx)
    f["present"] = sd["present"].astype(bool).to_numpy()
    f["lat_n"] = fill(S.norm("lat", col("lat")), 0.5)
    f["lon_n"] = fill(S.norm("lon", col("lon")), 0.5)
    dist = fill(S.norm("colony", col("colony")), 0.0)
    f["closeness"] = 1.0 - dist
    f["ret"] = fill(sd["ret"], 0.0).to_numpy() > 0.5

    effort = fill(S.norm("speed", col("speed")), 0.5)
    f["effort"] = effort
    f["rest"] = effort < st.still_fraction
    f["subdiv"] = [0 if r else map_effort_to_subdivision(e, st) for r, e in zip(f["rest"], effort)]

    # wind only where it was recorded, never stretched over days without data
    wspeed = col("wind_speed")
    w_hi = S.hi.get("wind_speed", 1.0) or 1.0
    wind_n = S.norm("wind_speed", wspeed)
    f["wind_n"] = wind_n.fillna(0.5)
    f["bpm"] = [map_wind_to_bpm(w, st) for w in f["wind_n"]]

    heading, wdir, tail, cross = col("heading"), col("wind_dir"), col("tailwind"), col("crosswind")
    align, cross_ms = nan.copy(), nan.copy()
    if wdir.notna().any() and heading.notna().any():
        to = (wdir + 180.0) % 360.0 if wind_from else wdir % 360.0
        ang = np.radians(to - heading)
        align, cross_ms = np.cos(ang), np.abs(np.sin(ang)) * wspeed
    if tail.notna().any():
        denom = wspeed.where(wspeed > 0.1)
        t_scale = max(abs(S.lo.get("tailwind", 0)), abs(S.hi.get("tailwind", 0)), 1e-9)
        a_tail = (tail / denom).where(denom.notna(), tail / t_scale).clip(-1, 1)
        align = a_tail.where(tail.notna(), align)
    if cross.notna().any():
        cross_ms = cross.abs().where(cross.notna(), cross_ms)
    align = align.where(align.notna(), (1.0 - 2.0 * wind_n))      # speed only: calm wind gives an open interval
    f["interval"] = [map_alignment_to_interval(a) for a in align.fillna(0.0)]
    f["cross_n"] = (cross_ms / (0.5 * w_hi)).clip(0, 1).fillna(0.0)

    # other weather and daylight
    f["bright_n"] = fill(S.norm("brightness", col("brightness")), 0.7)
    f["humid_n"] = fill(S.norm("humidity", col("humidity")), 0.4)
    f["rain_n"] = S.norm("precip", col("precip")).fillna(0.0)
    lat_deg = fill(col("lat"), 45.0)
    f["daylight_n"] = np.clip((daylight_hours(lat_deg.to_numpy(), dates) - 8.0) / 8.0, 0, 1)

    # pitches
    f["mel_raw"] = st.mel_lo + f["lat_n"] * (st.mel_hi - st.mel_lo)
    if "temperature" in S.lo:
        temp_n = fill(S.norm("temperature", col("temperature")), 0.5)
        raw = st.pad_lo + temp_n * (st.pad_hi - st.pad_lo)
    else:
        raw = pd.Series((st.pad_lo + st.pad_hi) / 2.0, index=idx)
    f["pad_root"] = [snap(st, x) for x in raw]
    home = (dist < st.home_radius).to_numpy()
    f["home"] = home
    f.loc[home, "pad_root"] = tonic_near(st, (st.pad_lo + st.pad_hi) / 2.0)
    f.loc[home, "interval"] = 7
    return f


# MIDI output
def to_tick(beat):
    return int(round(beat * TICKS_PER_BEAT))


def clamp(x, lo=0, hi=127):
    return int(min(max(round(x), lo), hi))


class Voice:
    def __init__(self, name, channel, program):
        self.name, self.channel, self.program = name, channel, program
        self.events = []     # (tick, order, message); note_off sorts first

    def note(self, b0, b1, pitch, velocity):
        s = to_tick(b0)
        e = max(s + 1, to_tick(b1))
        p, v = clamp(pitch), clamp(velocity, 1, 127)
        self.events.append((s, 1, mido.Message("note_on", channel=self.channel, note=p, velocity=v)))
        self.events.append((e, 0, mido.Message("note_off", channel=self.channel, note=p, velocity=0)))

    def cc(self, beat, number, value):
        self.events.append((to_tick(beat), 1, mido.Message("control_change", channel=self.channel,
                                                           control=number, value=clamp(value))))

    def bend(self, beat, value):
        self.events.append((to_tick(beat), 1, mido.Message("pitchwheel", channel=self.channel,
                                                           pitch=clamp(value, -8192, 8191))))

    def build(self, end_tick):
        track = mido.MidiTrack()
        track.append(mido.MetaMessage("track_name", name=self.name, time=0))
        track.append(mido.Message("program_change", channel=self.channel, program=self.program, time=0))
        write_events(track, self.events, end_tick)
        return track


def write_events(track, events, end_tick):
    prev = 0
    for tick, _, msg in sorted(events, key=lambda e: (e[0], e[1])):
        track.append(msg.copy(time=tick - prev))
        prev = tick
    track.append(mido.MetaMessage("end_of_track", time=max(0, end_tick - prev)))


def compose(plans, names, st, out_path):
    has_rain = any(float(p["rain_n"].max()) > 0.05 for p in plans)
    kinds = ["melody", "pad", "pulse"] + (["rain"] if has_rain else [])
    if len(plans) * len(kinds) + 1 > 15:
        raise ValueError("Too many birds for one MIDI file (15 channels). Use --bird to pick fewer.")
    chans = iter(c for c in range(16) if c != 9)          # channel 10 (index 9) is drums, so skipped
    voices = {}
    for j, nm in enumerate(names):
        for k in kinds:
            voices[(j, k)] = Voice(f"{nm} - {LABEL[k]}", next(chans), PROGRAMS[k][j % len(PROGRAMS[k])])
    drone = Voice("Home - drone", next(chans), PROGRAMS["drone"][0])

    scale = st.scale
    snap_idx = lambda p: int(np.abs(scale - p).argmin())
    bpm = pd.concat([p["bpm"].where(p["present"]) for p in plans], axis=1).mean(axis=1)
    bpm = bpm.ffill().bfill().fillna((st.bpm_lo + st.bpm_hi) / 2.0)

    conductor = [(0, 0, mido.MetaMessage("track_name", name="Bird journey", time=0)),
                 (0, 0, mido.MetaMessage("time_signature", numerator=4, denominator=4, time=0))]
    beat, motif_k = 0.0, [0] * len(plans)
    last_mel = [(st.mel_lo + st.mel_hi) / 2.0] * len(plans)

    for i in range(st.steps):
        bpm_i = float(bpm.iloc[i])
        nbeats = st.step_seconds * bpm_i / 60.0
        b0, b1 = beat, beat + nbeats
        conductor.append((to_tick(b0), 1, mido.MetaMessage("set_tempo", tempo=mido.bpm2tempo(bpm_i), time=0)))
        rows = [p.iloc[i] for p in plans]
        here = [j for j, r in enumerate(rows) if r["present"]]
        if here:
            close = float(np.mean([rows[j]["closeness"] for j in here]))
            drone.cc(b0, 11, 20 + 105 * close ** 1.5)       # louder near home
        else:
            drone.cc(b0, 11, 0)                              # no data: silent

        for j in here:
            r = rows[j]
            mel, pad, pul = voices[(j, "melody")], voices[(j, "pad")], voices[(j, "pulse")]
            interval, root, rest = int(r["interval"]), int(r["pad_root"]), bool(r["rest"])
            last_mel[j] = r["mel_raw"]

            mel.cc(b0, 10, 20 + 87 * r["lon_n"])                                  # west on the left
            for v in (mel, pad):
                v.cc(b0, 74, 40 + 75 * r["daylight_n"])                           # brightness
                v.cc(b0, 91, 30 + 70 * r["humid_n"] + (30 if rest else 0))        # reverb
            pad.cc(b0, 11, 70 + 57 * r["bright_n"])

            pad.note(b0, b1, root, VELOCITY["pad"])
            pad.note(b0, b1, root + interval, VELOCITY["pad"] - 14)

            base_idx = snap_idx(r["mel_raw"])
            sign = -1 if r["ret"] else 1                                           # mirror on the return
            vel = VELOCITY["melody"] - 8 + 18 * r["daylight_n"]

            def melody_idx():
                k = MOTIF[motif_k[j] % len(MOTIF)]
                motif_k[j] += 1
                return int(min(max(base_idx + sign * k, 0), len(scale) - 1))

            if rest:
                mel.note(b0, b1, scale[melody_idx()], vel)
            else:
                k = 0
                while k < nbeats - 0.05:
                    pos, idx = b0 + k, melody_idx()
                    if k == 0 and r["effort"] > 0.85:                              # grace note on hard days
                        up = min(idx + 1, len(scale) - 1)
                        mel.note(pos, pos + 0.25, scale[up], vel - 15)
                        mel.note(pos + 0.25, min(pos + 0.9, b1), scale[idx], vel)
                    else:
                        mel.note(pos, min(pos + 0.9, b1), scale[idx], vel)
                    k += 1

                sub = int(r["subdiv"])
                for q in range(int(math.ceil(nbeats * sub - 0.05))):
                    pos = b0 + q / sub
                    if pos >= b1 - 1e-9:
                        break
                    pul.note(pos, min(pos + min(0.25, 0.5 / sub), b1), root + 12 + (interval if q % 2 else 0),
                             VELOCITY["pulse"])

                cn = float(r["cross_n"])                                           # crosswind wobble
                if cn > 0.05:
                    t = 0.0
                    while t < nbeats - 1e-9:
                        mel.bend(b0 + t, WOBBLE_MAX * cn * math.sin(2 * math.pi * t))
                        t += 0.25
                    mel.bend(b1, 0)

            if has_rain and r["rain_n"] > 0.05:
                count = 1 + int(r["rain_n"] * 5)
                rain = voices[(j, "rain")]
                top = snap_idx(root + 36)
                for m in range(count):
                    pos = b0 + (m + 0.5) / count * nbeats
                    rain.note(pos, pos + 0.15, scale[min(top + (m * 2) % 5, len(scale) - 1)], VELOCITY["rain"])
        beat = b1

    if st.tail_seconds > 0:                      # coda: home, on the tonic
        nbeats = st.tail_seconds * float(bpm.iloc[-1]) / 60.0
        b0, b1 = beat, beat + nbeats
        mid_pad = (st.pad_lo + st.pad_hi) / 2.0
        drone.cc(b0, 11, 115)
        for j in range(len(plans)):
            voices[(j, "melody")].bend(b0, 0)
            voices[(j, "melody")].note(b0, b1, tonic_near(st, last_mel[j]), VELOCITY["melody"])
            voices[(j, "pad")].cc(b0, 11, 110)
            voices[(j, "pad")].note(b0, b1, tonic_near(st, mid_pad), VELOCITY["pad"] + 6)
            voices[(j, "pad")].note(b0, b1, tonic_near(st, mid_pad) + 7, VELOCITY["pad"] - 8)
        beat = b1

    end_tick = to_tick(beat)
    tonic = st.key + 36
    drone.note(0, beat, tonic, VELOCITY["drone"])
    drone.note(0, beat, tonic + 7, VELOCITY["drone"] - 10)

    mid = mido.MidiFile(type=1, ticks_per_beat=TICKS_PER_BEAT)
    cond = mido.MidiTrack()
    write_events(cond, conductor, end_tick)
    mid.tracks.append(cond)
    for v in list(voices.values()) + [drone]:
        mid.tracks.append(v.build(end_tick))
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    mid.save(str(out_path))
    return mido.MidiFile(str(out_path)).length


# command line
@dataclass
class Settings:
    steps: int
    step_seconds: float
    bpm_lo: float
    bpm_hi: float
    mel_lo: int
    mel_hi: int
    pad_lo: int
    pad_hi: int
    scale: np.ndarray
    key: int
    max_subdivision: int
    still_fraction: float
    tail_seconds: float
    home_radius: float


def parse_overrides(items):
    out = {}
    for item in items:
        if "=" not in item:
            raise ValueError(f"--col expects NAME=COLUMN, got '{item}'")
        name, column = item.split("=", 1)
        name = name.strip().lower()
        if name not in COLUMN_ALIASES:
            raise ValueError(f"--col: unknown name '{name}'. Choose from: {', '.join(COLUMN_ALIASES)}")
        out[name] = column.strip()
    return out


def utc_timestamp(text):
    ts = pd.Timestamp(text)
    return ts.tz_localize("UTC") if ts.tzinfo is None else ts.tz_convert("UTC")


def safe_name(text):
    return re.sub(r"[^A-Za-z0-9_-]+", "_", str(text)).strip("_") or "bird"


def make_settings(a):
    steps = max(2, int(round(a.duration / a.step_seconds)))
    key = KEYS[a.key.upper()]
    degrees = SCALES[a.scale]
    scale = np.array([p for p in range(128) if (p - key) % 12 in degrees])
    return Settings(steps=steps, step_seconds=a.duration / steps,
                    bpm_lo=min(a.bpm_range), bpm_hi=max(a.bpm_range),
                    mel_lo=min(a.melody_range), mel_hi=max(a.melody_range),
                    pad_lo=min(a.pad_range), pad_hi=max(a.pad_range),
                    scale=scale, key=key, max_subdivision=max(1, a.max_subdivision),
                    still_fraction=a.still_fraction, tail_seconds=max(0.0, a.tail_seconds),
                    home_radius=a.home_radius)


def describe(label, path, length, st, plans, names):
    print(f"\n{label}: {path}")
    print(f"  audio {length:.1f} s ({st.steps} steps of {st.step_seconds:.2f} s"
          + (f" + {st.tail_seconds:g} s coda)" if st.tail_seconds else ")"))
    for nm, p in zip(names, plans):
        pr = p[p["present"]]
        print(f"  {nm:8s} silent steps {int((~p['present']).sum()):3d} | rest steps {int(pr['rest'].sum()):3d} | "
              f"home steps {int(pr['home'].sum()):3d} | return steps {int(pr['ret'].sum()):3d} | "
              f"tempo {pr['bpm'].min():.0f}-{pr['bpm'].max():.0f} BPM | "
              f"intervals {sorted(set(int(x) for x in pr['interval']))}")


def closeup_tables(hourly, d, S, notes):
    """hour-by-hour step tables for each bird and day in the wind file's hourly rows."""
    out = []
    for (bird, day), h in hourly.groupby(["bird", "date"] if "bird" in hourly.columns else [lambda x: "track", "date"]):
        h = h.sort_values("time").reset_index(drop=True)
        day = str(day)
        sel = d[(d["_day"] == day) & ((d["bird"] == bird) if "bird" in d.columns else True)]
        row = sel.iloc[0] if len(sel) else None
        t = pd.DataFrame(index=range(len(h)))
        t["lat"], t["lon"] = h["lat"].to_numpy(float), h["lon"].to_numpy(float)
        for c in ("wind_speed", "wind_dir", "tailwind", "crosswind"):
            if c in h.columns:
                t[c] = h[c].to_numpy(float)
        if "heading_deg" in h.columns:
            t["heading"] = h["heading_deg"].to_numpy(float)
        else:
            t["heading"] = np.nan
        for c in ("speed", "colony", "temperature", "brightness", "humidity", "precip"):
            t[c] = float(row[c]) if row is not None and c in row.index and pd.notna(row[c]) else np.nan
        phase = row["phase"] if row is not None and "phase" in row.index else None
        t["ret"] = 1.0 if str(phase).lower() == "return" else 0.0
        t["coverage"], t["present"] = 1.0, True
        t.attrs["dates"] = pd.DatetimeIndex(h["time"])
        if row is None:
            notes.append(f"close-up {bird} {day}: that day is not in the journey file; "
                         f"effort/home/temperature use neutral defaults")
        out.append((str(bird), day, t))
    return out


def main():
    p = argparse.ArgumentParser(description="Turn a bird journey + weather into story-like MIDI.")
    p.add_argument("daily", help="Journey file: daily positions (CSV/TSV/JSON/JSONL)")
    p.add_argument("--wind", help="ERA5 wind file (rows/hourly_rows) or any table with bird, date and wind columns")
    p.add_argument("--eic", help="eic_temperature_profile.json (brightness/warmth per frame)")
    p.add_argument("--eic-start", help="Date of the first EIC frame (default: first journey date)")
    p.add_argument("--eic-end", help="Date of the last EIC frame (default: last journey date)")
    p.add_argument("--closeup", action="store_true", help="Also render an hour-by-hour piece for the wind file's day(s)")
    p.add_argument("--closeup-seconds", type=float, default=40.0, help="Length of each close-up piece (default 40)")
    p.add_argument("--save-merged", metavar="FILE", help="Write the merged table (.json or .csv)")
    p.add_argument("-o", "--output", help="Output name stem (default: next to the input)")
    p.add_argument("--duration", type=float, default=120.0, help="Audio length for the whole trip in seconds (default 120)")
    p.add_argument("--step-seconds", type=float, default=2.0, help="Approximate length of one step (default 2)")
    p.add_argument("--trio", action="store_true", help="All birds in one file instead of one file per bird")
    p.add_argument("--bird", help="Only this bird")
    p.add_argument("--start", help="Only data from this date (UTC)")
    p.add_argument("--end", help="Only data up to this date (UTC)")
    p.add_argument("--bpm-range", type=float, nargs=2, default=[60, 140], metavar=("MIN", "MAX"),
                   help="Tempo for calm..strong wind (default 60 140)")
    p.add_argument("--melody-range", type=int, nargs=2, default=[60, 84], metavar=("LOW", "HIGH"),
                   help="Melody register for southernmost..northernmost (default 60 84)")
    p.add_argument("--pad-range", type=int, nargs=2, default=[48, 64], metavar=("LOW", "HIGH"),
                   help="Pad pitch for coldest..warmest (default 48 64)")
    p.add_argument("--scale", choices=list(SCALES), default="minor_pentatonic")
    p.add_argument("--key", default="D", help="Home key, e.g. D, F#, Bb (default D)")
    p.add_argument("--still-fraction", type=float, default=0.15,
                   help="Distance below this share of the typical (95th percentile) day counts as rest (default 0.15)")
    p.add_argument("--max-subdivision", type=int, default=4, help="Most pulses per beat (default 4)")
    p.add_argument("--tail-seconds", type=float, default=4.0, help="Coda on the home chord at the end (default 4, 0 = none)")
    p.add_argument("--home-radius", type=float, default=0.02,
                   help="Within this share of the maximum colony distance the pad plays the home chord "
                        "(default 0.02, about 70 km for a 3,500 km journey)")
    p.add_argument("--wind-max", type=float, help="Wind speed that counts as 'strong' (default: from the wind file, else data max)")
    p.add_argument("--wind-dir-convention", choices=["from", "to"], default="from",
                   help="Wind direction is where the wind comes FROM (default) or goes TO")
    p.add_argument("--col", action="append", default=[], metavar="NAME=COLUMN",
                   help=f"Map a name to a column, e.g. --col temperature=T2M. Names: {', '.join(COLUMN_ALIASES)}")
    a = p.parse_args()
    if a.duration <= 0 or a.step_seconds <= 0:
        p.error("--duration and --step-seconds must be greater than 0")

    notes = []
    try:
        st = make_settings(a)
        d, found = canonicalize(read_table(a.daily), parse_overrides(a.col))
        d, derived = clean(d)
        wind_from = derived or a.wind_dir_convention == "from"

        wind_meta, hourly = {}, None
        if a.closeup and not a.wind:
            raise ValueError("--closeup needs a wind file with hourly_rows (--wind).")
        if a.wind:
            wd, hourly, wind_meta = load_wind(a.wind)
            if a.closeup and (hourly is None or hourly.empty):
                raise ValueError("--closeup needs a wind file with hourly_rows; this one has only daily rows.")
            d = merge_wind(d, wd, notes)
        if a.eic:
            d = attach_eic(d, a.eic, a.eic_start, a.eic_end, notes)

        if a.start:
            d = d[d["timestamp"] >= utc_timestamp(a.start)]
        if a.end:
            d = d[d["timestamp"] <= utc_timestamp(a.end)]
        if d.empty:
            raise ValueError("No valid rows left after parsing and date filtering.")
        if "bird" not in d.columns:
            d["bird"] = "track"
        if a.bird:
            d = d[d["bird"].str.lower() == a.bird.lower()]
            if d.empty:
                raise ValueError(f"Bird '{a.bird}' not found.")

        stem = Path(a.output).with_suffix("") if a.output else Path(a.daily).with_suffix("")
        print("Columns used: " + ", ".join(f"{k}<-{v}" for k, v in found.items()))
        have = [c for c in ("temperature", "brightness", "wind_speed", "tailwind", "humidity", "precip")
                if c in d.columns and d[c].notna().any()]
        print("Weather present: " + (", ".join(
            f"{c} ({int(d[c].notna().sum())} rows)" for c in have) or "none"))
        for n in notes:
            print("  - " + n)

        if a.save_merged:
            m = d.drop(columns=["_day"]).copy()
            m["timestamp"] = m["timestamp"].dt.strftime("%Y-%m-%d")
            sm = Path(a.save_merged)
            sm.parent.mkdir(parents=True, exist_ok=True)
            if sm.suffix.lower() == ".csv":
                m.to_csv(sm, index=False)
            else:
                with open(sm, "w", encoding="utf-8") as fh:
                    json.dump({"meta": {"notes": notes}, "data": m.where(m.notna(), None).to_dict("records")},
                              fh, indent=1)
            print(f"  saved merged table: {sm}")

        t0, t1 = d["timestamp"].min(), d["timestamp"].max()
        span = (t1 - t0).total_seconds()
        step_hours = span / st.steps / 3600.0
        dates = pd.DatetimeIndex([t0 + pd.Timedelta(seconds=(i + 0.5) * span / st.steps) for i in range(st.steps)])
        names = sorted(d["bird"].unique())
        tables = {nm: enrich(step_table(d[d["bird"] == nm], st.steps, t0, t1), step_hours) for nm in names}

        S = Scales(list(tables.values()))
        if "speed_max" in wind_meta or a.wind_max:
            S.override("wind_speed", 0.0, a.wind_max or wind_meta["speed_max"])
        if "tail_abs_max" in wind_meta:
            S.override("tailwind", -wind_meta["tail_abs_max"], wind_meta["tail_abs_max"])
        plans = {nm: build_features(tables[nm], S, st, dates, wind_from) for nm in names}

        if a.trio:
            out = Path(f"{stem}_trio.mid")
            length = compose([plans[n] for n in names], names, st, out)
            describe("trio", out, length, st, [plans[n] for n in names], names)
        else:
            for nm in names:
                out = Path(f"{stem}.mid") if len(names) == 1 else Path(f"{stem}_{safe_name(nm)}.mid")
                length = compose([plans[nm]], [nm], st, out)
                describe(nm, out, length, st, [plans[nm]], [nm])

        if a.closeup:
            if hourly is None or hourly.empty:
                raise ValueError("--closeup needs a wind file with hourly_rows (--wind).")
            for bird, day, t in closeup_tables(hourly, d, S, notes):
                n = len(t)
                cst = replace(st, steps=n, step_seconds=a.closeup_seconds / n, tail_seconds=0.0)
                plan = build_features(t, S, cst, t.attrs["dates"], wind_from)
                out = Path(f"{stem}_closeup_{safe_name(bird)}_{day}.mid")
                length = compose([plan], [bird], cst, out)
                describe(f"close-up {bird} {day} (1 step = 1 hour)", out, length, cst, [plan], [bird])
    except ValueError as err:
        p.exit(1, f"Error: {err}\n")


if __name__ == "__main__":
    main()
