#!/usr/bin/env python3
"""turn a GPS track (CSV or JSON), with optional weather, into a MIDI file.

    python route_to_midi.py gulls.csv
    python route_to_midi.py track.json --duration 90 --bird Eric
    python route_to_midi.py merged.csv --col temperature=T2M --col wind_u=U10M --col wind_v=V10M

temperature sets the pitch, wind speed the tempo, wind against the heading the
interval, and speed the pulses per beat (a resting bird holds one note). the
whole trip is fitted into --duration seconds.

each value is scaled to the trip's own min and max, so units don't matter.
columns are found by name (see COLUMN_ALIASES); use --col NAME=COLUMN otherwise.
wind direction is read as where the wind comes from, as in ERA5 and Open-Meteo;
pass --wind-dir-convention to if yours is where it goes.
"""

import argparse
import json
import math
import re
from dataclasses import dataclass
from pathlib import Path

import mido
import numpy as np
import pandas as pd

TICKS_PER_BEAT = 480

# instruments and scales
PROGRAMS = {"bird": 73, "pad": 89, "pulse": 12}        # GM: flute, warm pad, marimba
CHANNELS = {"bird": 0, "pad": 1, "pulse": 2}           # channel 9 is drums, so avoided
TRACK_NAMES = {"bird": "Bird (melody)", "pad": "Atmosphere (pad)", "pulse": "Beats (pulse)"}
OCTAVE_SHIFT = {"bird": 12, "pad": 0, "pulse": 12}     # added to the temperature pitch
VELOCITY = {"bird": 85, "pad": 50, "pulse": 60}
MOTIF = [0, 2, 4, 3, 1, 4, 5, 2]                       # melody contour, in scale steps
# semitones, from most open to most tense
INTERVAL_LADDER = [12, 7, 5, 4, 3, 2, 6, 1]

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
    "speed": ["speed_kmh", "speed_2d", "speed", "ground_speed", "velocity"],
    "heading": ["heading_deg", "direction", "heading", "bearing"],
    "temperature": ["temperature_c", "temperature", "temperature_2m", "temp", "t2m"],
    "wind_speed": ["wind_speed_ms", "wind_speed", "wind_speed_10m", "windspeed", "ws10m"],
    "wind_dir": ["wind_dir_deg", "wind_direction", "wind_direction_10m", "wind_dir", "wd10m"],
    "wind_u": ["wind_u", "u10m", "u10", "u_wind", "eastward_wind"],
    "wind_v": ["wind_v", "v10m", "v10", "v_wind", "northward_wind"],
    "bird": ["bird_name", "bird", "individual", "individual_id", "tag_id", "device_info_serial"],
}
PREFERRED_JSON_KEYS = ("records", "data", "rows", "points", "track", "tracks", "observations", "items")


# loading
def json_to_frame(raw):
    if isinstance(raw, list):
        return pd.json_normalize(raw)
    if not isinstance(raw, dict):
        raise ValueError("JSON must be a list of records or an object that contains one.")

    # column lists: {"timestamp": [...], "lat": [...]}
    if raw and all(isinstance(v, list) for v in raw.values()):
        if not any(v and isinstance(v[0], (dict, list)) for v in raw.values()):
            return pd.DataFrame(raw)

    record_lists = {k: v for k, v in raw.items()
                    if isinstance(v, list) and v and isinstance(v[0], dict)}
    if not record_lists:
        raise ValueError("Could not find a list of records in the JSON file.")
    for key in PREFERRED_JSON_KEYS:
        if key in record_lists:
            return pd.json_normalize(record_lists[key])
    if len(record_lists) == 1:
        return pd.json_normalize(next(iter(record_lists.values())))

    # {"Eric": [...], "Nico": [...]}: the key becomes the bird name
    frames = []
    for name, records in record_lists.items():
        frame = pd.json_normalize(records)
        known = {str(c).lower() for c in frame.columns}
        if not known & set(COLUMN_ALIASES["bird"]):
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
    """rename found columns to standard names; returns (frame, {standard: original})."""
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
        raise ValueError(
            f"Could not find columns for: {', '.join(missing)}.\n"
            f"Columns in file: {', '.join(map(str, df.columns))}\n"
            f"Tell the script which one to use, e.g. --col {missing[0]}=YOUR_COLUMN"
        )
    return pd.DataFrame({canon: df[src] for canon, src in found.items()}), found


def clean(d):
    """parse types, sort by time, and get wind speed and direction from u/v if needed.
    returns (frame, whether wind direction was derived)."""
    d = d.copy()
    ts = d["timestamp"]
    if pd.api.types.is_numeric_dtype(ts):
        unit = "ms" if ts.median() > 1e11 else "s"
        d["timestamp"] = pd.to_datetime(ts, unit=unit, utc=True, errors="coerce")
    else:
        d["timestamp"] = pd.to_datetime(ts, utc=True, errors="coerce")

    for c in d.columns:
        if c not in ("timestamp", "bird"):
            d[c] = pd.to_numeric(d[c], errors="coerce")
    d = d.dropna(subset=["timestamp", "lat", "lon"]).sort_values("timestamp").reset_index(drop=True)

    derived = False
    if "wind_speed" not in d.columns and {"wind_u", "wind_v"} <= set(d.columns):
        u, v = d["wind_u"], d["wind_v"]
        d["wind_speed"] = np.hypot(u, v)
        d["wind_dir"] = np.degrees(np.arctan2(-u, -v)) % 360.0   # where the wind comes from
        derived = True
    return d, derived


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


# the trip is split into equal time steps; each step becomes the same length of music
def aggregate(d, steps):
    t0, t1 = d["timestamp"].min(), d["timestamp"].max()
    span = (t1 - t0).total_seconds()
    if span <= 0:
        raise ValueError("The track has no time span (all timestamps are equal).")

    frac = (d["timestamp"] - t0).dt.total_seconds() / span
    d = d.assign(step=np.minimum((frac * steps).astype(int), steps - 1))

    numeric = [c for c in ("lat", "lon", "speed", "temperature", "wind_speed") if c in d.columns]
    grouped = d.groupby("step")
    out = grouped[numeric].mean()

    for ang in ("heading", "wind_dir"):          # circular mean for angles
        if ang in d.columns:
            rad = np.radians(d[ang])
            tmp = pd.DataFrame({"step": d["step"], "s": np.sin(rad), "c": np.cos(rad)}).dropna()
            tmp = tmp.groupby("step").mean()
            out[ang] = np.degrees(np.arctan2(tmp["s"], tmp["c"])) % 360.0

    out["n"] = grouped.size()
    out = out.reindex(range(steps))
    out["n"] = out["n"].fillna(0).astype(int)
    return out, t0, t1, span


def enrich_motion(sd, span_seconds):
    """fill heading, and speed if missing, from consecutive step positions."""
    n = len(sd)
    step_hours = span_seconds / n / 3600.0
    lat, lon = sd["lat"].to_numpy(), sd["lon"].to_numpy()
    valid = np.flatnonzero(np.isfinite(lat) & np.isfinite(lon))
    head = np.full(n, np.nan)
    speed = np.full(n, np.nan)
    for a, b in zip(valid[:-1], valid[1:]):
        head[a] = bearing_degrees(lat[a], lon[a], lat[b], lon[b])
        speed[a] = haversine_km(lat[a], lon[a], lat[b], lon[b]) / ((b - a) * step_hours)
    if len(valid) >= 2:
        head[valid[-1]], speed[valid[-1]] = head[valid[-2]], speed[valid[-2]]

    if "heading" in sd.columns:
        sd["heading"] = sd["heading"].fillna(pd.Series(head, index=sd.index))
    else:
        sd["heading"] = head
    if "speed" not in sd.columns:
        sd["speed"] = speed          # straight-line distance between steps, so an underestimate
    return sd


# data to music
def norm(series):
    """scale to 0..1 using the trip's own min and max; NaN stays NaN."""
    s = series.astype(float)
    if not s.notna().any():
        return s
    lo, hi = s.min(), s.max()
    if hi - lo < 1e-12:
        return s.where(s.isna(), 0.5)
    return (s - lo) / (hi - lo)


def fill(series, default):
    return series.ffill().bfill().fillna(default)


def map_temperature_to_pitch(temp_n, st):
    """temperature 0..1 to a MIDI pitch on the scale."""
    raw = st.pitch_lo + temp_n * (st.pitch_hi - st.pitch_lo)
    return int(st.scale[np.abs(st.scale - raw).argmin()])


def map_wind_to_bpm(wind_n, st):
    """wind speed 0..1 to tempo in BPM."""
    return st.bpm_lo + wind_n * (st.bpm_hi - st.bpm_lo)


def map_wind_to_interval(alignment):
    """wind along the heading (+1 tailwind to -1 headwind) to an interval in semitones."""
    idx = int(round((1.0 - alignment) / 2.0 * (len(INTERVAL_LADDER) - 1)))
    return INTERVAL_LADDER[min(max(idx, 0), len(INTERVAL_LADDER) - 1)]


def map_speed_to_subdivision(speed_n, st):
    """speed 0..1 to pulses per beat; 0 means resting."""
    if speed_n < st.still_fraction:
        return 0
    return 1 + int(round(speed_n * (st.max_subdivision - 1)))


def build_features(sd, st, wind_dir_is_from):
    """one row per step with all musical values."""
    def col(name):
        return sd[name] if name in sd.columns else pd.Series(np.nan, index=sd.index)

    f = pd.DataFrame(index=sd.index)
    f["present"] = sd["n"] > 0
    temp_n = fill(norm(col("temperature")), 0.5)
    wind_n = fill(norm(col("wind_speed")), 0.5)

    heading, wdir = col("heading"), col("wind_dir")
    if wdir.notna().any() and heading.notna().any():
        wind_to = (wdir + 180.0) % 360.0 if wind_dir_is_from else wdir % 360.0
        alignment = fill(np.cos(np.radians(wind_to - heading)), 0.0)
    else:
        alignment = 1.0 - 2.0 * wind_n       # no direction: calm wind gives an open interval

    speed = col("speed")
    valid = speed[f["present"]].dropna()
    ref = np.percentile(valid, 95) if len(valid) else 0.0
    speed_n = fill((speed / ref).clip(0, 1) if ref > 0 else speed * 0.0, 0.5)

    f["bpm"] = [map_wind_to_bpm(w, st) for w in wind_n]
    f["temp_pitch"] = [map_temperature_to_pitch(t, st) for t in temp_n]
    f["interval"] = [map_wind_to_interval(a) for a in alignment]
    f["subdiv"] = [map_speed_to_subdivision(s, st) for s in speed_n]
    return f


# MIDI output; mido stores real tempo changes
def to_tick(beat):
    return int(round(beat * TICKS_PER_BEAT))


class TrackBuilder:
    def __init__(self, key):
        self.key = key
        self.channel = CHANNELS[key]
        self.events = []     # (tick, order, message); note_off sorts before note_on

    def note(self, start_beat, end_beat, pitch, velocity):
        pitch = int(min(max(pitch, 0), 127))
        velocity = int(min(max(velocity, 1), 127))
        s = to_tick(start_beat)
        e = max(s + 1, to_tick(end_beat))
        self.events.append((s, 1, mido.Message("note_on", channel=self.channel, note=pitch, velocity=velocity)))
        self.events.append((e, 0, mido.Message("note_off", channel=self.channel, note=pitch, velocity=0)))

    def build(self, end_tick):
        track = mido.MidiTrack()
        track.append(mido.MetaMessage("track_name", name=TRACK_NAMES[self.key], time=0))
        track.append(mido.Message("program_change", channel=self.channel, program=PROGRAMS[self.key], time=0))
        write_events(track, self.events, end_tick)
        return track


def write_events(track, events, end_tick):
    prev = 0
    for tick, _, msg in sorted(events, key=lambda e: (e[0], e[1])):
        track.append(msg.copy(time=tick - prev))
        prev = tick
    track.append(mido.MetaMessage("end_of_track", time=max(0, end_tick - prev)))


def compose(feat, st, out_path):
    scale = st.scale

    def snap_idx(pitch):
        return int(np.abs(scale - pitch).argmin())

    def root_for(instr, temp_pitch):
        base = temp_pitch if instr in st.temp_instruments else st.fixed_pitch
        return int(base) + OCTAVE_SHIFT[instr]

    tracks = {k: TrackBuilder(k) for k in PROGRAMS}
    conductor = [(0, 0, mido.MetaMessage("track_name", name="Bird journey", time=0)),
                 (0, 0, mido.MetaMessage("time_signature", numerator=4, denominator=4, time=0))]
    beat = 0.0
    motif_k = 0

    for row in feat.itertuples():
        bpm = float(row.bpm)
        nb = st.step_seconds * bpm / 60.0            # beats in this step
        b0, b1 = beat, beat + nb
        conductor.append((to_tick(b0), 1, mido.MetaMessage("set_tempo", tempo=mido.bpm2tempo(bpm), time=0)))

        if row.present:                              # steps with no GPS fix stay silent
            interval = int(row.interval)

            # pad: temperature sets the root, wind the interval above it
            root = root_for("pad", row.temp_pitch)
            tracks["pad"].note(b0, b1, root, VELOCITY["pad"])
            tracks["pad"].note(b0, b1, root + interval, VELOCITY["pad"] - 14)

            # melody: one note per beat, or one held note when resting
            base_idx = snap_idx(root_for("bird", row.temp_pitch))
            if row.subdiv == 0:
                idx = min(max(base_idx + MOTIF[motif_k % len(MOTIF)], 0), len(scale) - 1)
                tracks["bird"].note(b0, b1, scale[idx], VELOCITY["bird"])
                motif_k += 1
            else:
                k = 0
                while k < nb - 0.05:
                    idx = min(max(base_idx + MOTIF[motif_k % len(MOTIF)], 0), len(scale) - 1)
                    pos = b0 + k
                    tracks["bird"].note(pos, min(pos + 0.9, b1), scale[idx], VELOCITY["bird"])
                    motif_k += 1
                    k += 1

                # pulses: speed sets how many per beat, alternating root and root + interval
                sub = int(row.subdiv)
                proot = root_for("pulse", row.temp_pitch)
                for j in range(int(math.ceil(nb * sub - 0.05))):
                    pos = b0 + j / sub
                    if pos >= b1 - 1e-9:
                        break
                    pitch = proot + (interval if j % 2 else 0)
                    tracks["pulse"].note(pos, min(pos + min(0.25, 0.5 / sub), b1), pitch, VELOCITY["pulse"])
        beat = b1

    end_tick = to_tick(beat)
    mid = mido.MidiFile(type=1, ticks_per_beat=TICKS_PER_BEAT)
    cond = mido.MidiTrack()
    write_events(cond, conductor, end_tick)
    mid.tracks.append(cond)
    for t in tracks.values():
        mid.tracks.append(t.build(end_tick))
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    mid.save(str(out_path))
    return mido.MidiFile(str(out_path)).length


# command line
@dataclass
class Settings:
    step_seconds: float
    steps: int
    bpm_lo: float
    bpm_hi: float
    pitch_lo: int
    pitch_hi: int
    scale: np.ndarray
    temp_instruments: list
    fixed_pitch: int
    max_subdivision: int
    still_fraction: float


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


def make_settings(args):
    steps = max(2, int(round(args.duration / args.step_seconds)))
    degrees = SCALES[args.scale]
    key = KEYS[args.key.upper()]
    scale = np.array([p for p in range(128) if (p - key) % 12 in degrees])
    instruments = [x.strip().lower() for x in args.temp_instruments.split(",")
                   if x.strip() and x.strip().lower() != "none"]
    bad = set(instruments) - set(PROGRAMS)
    if bad:
        raise ValueError(f"--temp-instruments: unknown {', '.join(sorted(bad))}. Choose from bird, pad, pulse.")
    fixed = int(scale[np.abs(scale - args.fixed_pitch).argmin()])
    return Settings(
        step_seconds=args.duration / steps, steps=steps,
        bpm_lo=min(args.bpm_range), bpm_hi=max(args.bpm_range),
        pitch_lo=min(args.pitch_range), pitch_hi=max(args.pitch_range),
        scale=scale, temp_instruments=instruments, fixed_pitch=fixed,
        max_subdivision=max(1, args.max_subdivision), still_fraction=args.still_fraction,
    )


def process(d, label, out_path, st, wind_from, mapping):
    sd, t0, t1, span = aggregate(d, st.steps)
    sd = enrich_motion(sd, span)
    feat = build_features(sd, st, wind_from)
    length = compose(feat, st, out_path)

    print(f"\n{label}: {out_path}")
    print(f"  trip      {t0:%Y-%m-%d} to {t1:%Y-%m-%d} ({span / 86400:.0f} days, {len(d)} fixes)")
    print(f"  audio     {length:.1f} s in {st.steps} steps of {st.step_seconds:.2f} s "
          f"({int((~feat['present']).sum())} silent steps without data)")
    print(f"  tempo     {feat['bpm'].min():.0f}-{feat['bpm'].max():.0f} BPM")
    print(f"  pitch     {feat['temp_pitch'].min()}-{feat['temp_pitch'].max()} (before octave shifts)")
    print(f"  intervals used (semitones): {sorted(set(feat['interval']))}")
    print(f"  resting steps (held note, no pulses): {int((feat['subdiv'] == 0).sum())}")


def main():
    p = argparse.ArgumentParser(description="Turn a bird track (CSV/JSON) with weather into MIDI.")
    p.add_argument("input", help="CSV, TSV, JSON or JSON Lines file")
    p.add_argument("-o", "--output", help="Output .mid name (default: input name)")
    p.add_argument("--duration", type=float, default=60.0,
                   help="Length of the audio in seconds; the whole trip is squeezed into it (default 60)")
    p.add_argument("--step-seconds", type=float, default=2.0,
                   help="Approximate length of one musical step in seconds (default 2)")
    p.add_argument("--bird", help="Only use this bird (default: one MIDI file per bird)")
    p.add_argument("--start", help="Only use data from this date (UTC), e.g. 2013-11-25")
    p.add_argument("--end", help="Only use data up to this date (UTC)")
    p.add_argument("--bpm-range", type=float, nargs=2, default=[60, 140], metavar=("MIN", "MAX"),
                   help="Tempo range for calm..strong wind (default 60 140)")
    p.add_argument("--pitch-range", type=int, nargs=2, default=[48, 72], metavar=("LOW", "HIGH"),
                   help="MIDI pitch range for coldest..warmest (default 48 72)")
    p.add_argument("--scale", choices=list(SCALES), default="minor_pentatonic")
    p.add_argument("--key", default="C", help="Scale key, e.g. C, F#, Bb (default C)")
    p.add_argument("--temp-instruments", default="bird,pad",
                   help="Instruments whose pitch follows temperature: any of bird,pad,pulse or none")
    p.add_argument("--fixed-pitch", type=int, default=60,
                   help="Root pitch for instruments not following temperature (default 60)")
    p.add_argument("--max-subdivision", type=int, default=4,
                   help="Most pulses per beat, reached at the bird's top speed (default 4)")
    p.add_argument("--still-fraction", type=float, default=0.05,
                   help="Bird speed below this share of its top speed counts as resting (default 0.05)")
    p.add_argument("--wind-dir-convention", choices=["from", "to"], default="from",
                   help="Whether wind direction is where the wind comes FROM (default) or goes TO")
    p.add_argument("--col", action="append", default=[], metavar="NAME=COLUMN",
                   help="Map a canonical name to a column, e.g. --col temperature=T2M. "
                        f"Names: {', '.join(COLUMN_ALIASES)}")
    args = p.parse_args()

    if args.duration <= 0 or args.step_seconds <= 0:
        p.error("--duration and --step-seconds must be greater than 0")

    try:
        st = make_settings(args)
        raw = read_table(args.input)
        d, found = canonicalize(raw, parse_overrides(args.col))
        d, derived = clean(d)
        wind_from = derived or args.wind_dir_convention == "from"

        if args.start:
            d = d[d["timestamp"] >= utc_timestamp(args.start)]
        if args.end:
            d = d[d["timestamp"] <= utc_timestamp(args.end)]
        if d.empty:
            raise ValueError("No valid rows left after parsing and date filtering.")

        print("Columns used: " + ", ".join(f"{k}<-{v}" for k, v in found.items()))
        missing = [k for k in ("temperature", "wind_speed", "speed") if k not in d.columns]
        if missing:
            print(f"Not in file (neutral defaults used): {', '.join(missing)}")

        stem = Path(args.output).with_suffix("") if args.output else Path(args.input).with_suffix("")

        if "bird" in d.columns:
            d["bird"] = d["bird"].astype(str)
            names = sorted(d["bird"].unique())
            if args.bird:
                names = [n for n in names if n.lower() == args.bird.lower()]
                if not names:
                    raise ValueError(f"Bird '{args.bird}' not found. Available: "
                                     f"{', '.join(sorted(d['bird'].unique()))}")
            for name in names:
                out = Path(f"{stem}.mid") if len(names) == 1 else Path(f"{stem}_{safe_name(name)}.mid")
                process(d[d["bird"] == name], name, out, st, wind_from, found)
        else:
            process(d, "track", Path(f"{stem}.mid"), st, wind_from, found)
    except ValueError as err:
        p.exit(1, f"Error: {err}\n")


if __name__ == "__main__":
    main()