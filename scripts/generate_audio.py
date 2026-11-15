"""pitch audio: compose each journey with story_to_midi.py and render the stems with fluidsynth.

each bird gets 26 s from departure to return, then a 4 s ending at home. the pad follows the
EIC colour under the bird, the melody its latitude, the beats its daily distance and the drone
its distance from the colony. route_to_midi.py composes the short wind example.
"""
from __future__ import annotations

import argparse
import json
import math
import re
import shutil
import subprocess
import sys
import tempfile
import wave
from datetime import datetime, timezone
from pathlib import Path

import mido
import numpy as np
import pandas as pd
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parent))
import route_to_midi as composer  # noqa: E402
import story_to_midi as story  # noqa: E402


ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "assets" / "data"
FRAMES = ROOT / "assets" / "eic-temperature"
AUDIO = ROOT / "assets" / "audio"
SOUNDFONT = ROOT / "scripts" / "soundfont" / "mbgm.SF2"
JOURNEY_SECONDS = 26.0  # departure to return; the home ending comes after
EXAMPLE_DURATION = 8.0
CONTROL_HZ = 20
SMOOTH_SECONDS = 0.18
SYNTH_GAIN = 0.7  # midi_player default from the original composer
BIRDS = ("Eric", "Nico", "Sanne")
STEMS = ("movement", "temperature", "wind")
# composer settings; 0.5 s steps (not 2 s) to fit a short timeline
COMPOSER_OPTIONS = {
    "step_seconds": 0.5, "bpm_range": [60, 140], "pitch_range": [48, 72],
    "scale": "minor_pentatonic", "key": "C", "temp_instruments": "bird,pad",
    "fixed_pitch": 60, "max_subdivision": 4, "still_fraction": 0.15,
}
# story_to_midi defaults, with 1 s steps over the journey
STORY_OPTIONS = {
    "step_seconds": 1.0, "bpm_range": [60, 140], "melody_range": [60, 84], "pad_range": [48, 64],
    "scale": "minor_pentatonic", "key": "D", "still_fraction": 0.15, "max_subdivision": 4,
    "tail_seconds": 4.0, "home_radius": 0.02,
}
DAY = 86400.0
EIC_SCALE = np.array([
    [235, 228, 250], [205, 168, 200], [160, 100, 210], [60, 50, 200],
    [40, 90, 215], [30, 130, 230], [60, 185, 230], [130, 205, 210],
    [110, 225, 140], [235, 240, 70], [250, 175, 40], [235, 80, 25],
    [175, 25, 15], [95, 15, 10], [150, 140, 140], [205, 200, 200],
], dtype=np.float32)


def read_json(path: Path):
    return json.loads(path.read_text())


def timestamp(value: str) -> float:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return parsed.replace(tzinfo=timezone.utc).timestamp() if parsed.tzinfo is None else parsed.timestamp()


def smooth(values: np.ndarray, valid: np.ndarray | None = None) -> np.ndarray:
    """smooth within valid spans; never fills gaps."""
    result = np.asarray(values, dtype=float).copy()
    valid = np.ones(len(result), dtype=bool) if valid is None else np.asarray(valid, dtype=bool)
    radius = max(1, round(CONTROL_HZ * SMOOTH_SECONDS))
    kernel = np.hanning(radius * 2 + 1)
    kernel /= kernel.sum()
    edges = np.flatnonzero(np.diff(np.r_[False, valid, False]))
    for start, end in zip(edges[::2], edges[1::2]):
        padded = np.pad(result[start:end], radius, mode="edge")
        result[start:end] = np.convolve(padded, kernel, mode="valid")
    result[~valid] = 0
    return result


def eic_profile(paths: list[Path]) -> list[dict]:
    profile = []
    for path in paths:
        with Image.open(path).convert("RGB") as image:
            pixels = np.asarray(image.resize((64, 32)), dtype=np.float32) / 255
        rgb = pixels.mean(axis=(0, 1))
        profile.append({
            "file": f"assets/eic-temperature/{path.name}",
            "brightness": round(float(rgb @ [0.2126, 0.7152, 0.0722]), 4),
            "warmth": round(float(rgb[0] - rgb[2]), 4),
        })
    return profile


def colour_warmth(frame: np.ndarray, lat: float, lon: float) -> float:
    """position on the EIC colour ramp, not degrees."""
    height, width, _ = frame.shape
    x = int(np.clip(round((lon + 180) / 360 * (width - 1)), 1, width - 2))
    y = int(np.clip(round((90 - lat) / 180 * (height - 1)), 1, height - 2))
    rgb = frame[y - 1:y + 2, x - 1:x + 2].mean(axis=(0, 1))
    palette = EIC_SCALE if abs(lat) <= 35 else EIC_SCALE[:-2]
    start, delta = palette[:-1], np.diff(palette, axis=0)
    fraction = np.clip(((rgb - start) * delta).sum(axis=1) / (delta * delta).sum(axis=1), 0, 1)
    distance = ((start + fraction[:, None] * delta - rgb) ** 2).sum(axis=1)
    index = int(np.argmin(distance))
    return float((index + fraction[index]) / (len(EIC_SCALE) - 1))


def load_weather(data_dir: Path) -> dict[str, list[dict]]:
    result = {bird: {} for bird in BIRDS}
    for filename, key in (("era5_wind.json", "rows"), ("era5_bird_layer.json", "records")):
        path = data_dir / filename
        if not path.exists():
            continue
        for row in read_json(path).get(key, []):
            bird = row.get("bird")
            if bird not in result or not row.get("date"):
                continue
            speed = row.get("wind_speed_ms", row.get("speed"))
            if speed is None or not math.isfinite(float(speed)):
                continue
            result[bird][row["date"][:10]] = {
                "date": row["date"][:10], "speed": max(0, float(speed)),
                "tail": row.get("tailwind_ms", row.get("tail")),
                "humidity": row.get("relative_humidity_pct"),
                "rain": row.get("precipitation_mm"),
                "valid_until": row.get("valid_until"),
            }
    return {bird: sorted(rows.values(), key=lambda row: row["date"]) for bird, rows in result.items()}


def wind_interval(speed: float, tailwind: float) -> float:
    if speed <= 0.1:
        return 7.0
    return float(composer.map_wind_to_interval(float(np.clip(tailwind / speed, -1, 1))))


def weather_controls(rows: list[dict], times: np.ndarray) -> dict[str, np.ndarray]:
    out = {key: np.zeros(len(times)) for key in ("wind_speed", "tailwind", "humidity", "rain")}
    out["wind_interval"] = np.full(len(times), 7.0)
    known = np.zeros(len(times), dtype=bool)
    if rows:
        dates = np.array([timestamp(row["date"]) for row in rows])
        left = np.clip(np.searchsorted(dates, times, side="right") - 1, 0, len(dates) - 1)
        right = np.minimum(left + 1, len(dates) - 1)
        # a daily row covers one day; interpolate only between adjacent days
        known = (times >= dates[left]) & (times < dates[left] + DAY)
        valid_until = np.array([timestamp(row["valid_until"]) if row.get("valid_until") else dates[index] + DAY for index, row in enumerate(rows)])
        known &= times <= valid_until[left]
        adjacent = (right != left) & (dates[right] - dates[left] <= DAY * 1.5)
        blend = np.where(adjacent, np.clip((times - dates[left]) / np.maximum(dates[right] - dates[left], 1), 0, 1), 0)
        for output, field in (("wind_speed", "speed"), ("tailwind", "tail"), ("humidity", "humidity"), ("rain", "rain")):
            values = np.array([float(row[field]) if row.get(field) is not None else 0 for row in rows])
            present = np.array([row.get(field) is not None and math.isfinite(float(row[field])) for row in rows])
            valid = known & present[left]
            amount = np.where(present[right], blend, 0)
            out[output] = smooth(np.nan_to_num(values[left] * (1 - amount) + values[right] * amount), valid)
        intervals = np.array([wind_interval(float(row.get("speed") or 0), float(row.get("tail") or 0)) for row in rows])
        interval_known = known & np.isfinite(intervals[left])
        amount = np.where(adjacent, blend, 0)
        out["wind_interval"] = smooth(intervals[left] * (1 - amount) + intervals[right] * amount, interval_known)
        out["wind_interval"][~known] = 7
    out["weather_known"] = known
    return out


def journey_controls(points: list[dict], frames: list[np.ndarray], weather: list[dict], duration: float, distance_ref: float) -> dict:
    points = sorted(points, key=lambda point: point["date"])
    if len(points) < 2:
        raise ValueError("A journey needs at least two recorded days")
    dates = np.array([timestamp(point["date"]) for point in points])
    if np.any(np.diff(dates) <= 0):
        raise ValueError("Journey dates must be unique")
    seconds = np.linspace(0, duration, round(duration * CONTROL_HZ) + 1)
    times = np.interp(seconds, [0, duration], [dates[0], dates[-1]])
    left = np.clip(np.searchsorted(dates, times, side="right") - 1, 0, len(points) - 1)
    right = np.minimum(left + 1, len(points) - 1)
    fraction = (times - dates[left]) / np.maximum(dates[right] - dates[left], 1)
    flags = np.array([bool(point.get("gap")) for point in points])
    gaps = flags[left] | flags[right] | (dates[right] - dates[left] > DAY * 1.5)
    fraction[gaps] = 0
    def interpolate(field):
        values = np.array([float(point[field]) for point in points])
        return values[left] * (1 - fraction) + values[right] * fraction
    lat, lon, km = interpolate("lat"), interpolate("lon"), interpolate("km")
    activity = smooth(np.sqrt(np.clip(km / max(distance_ref, 1), 0, 1)), ~gaps)
    frame_pos = seconds / duration * (len(frames) - 1)
    warmth = []
    for index, position in enumerate(frame_pos):
        before = int(position)
        after = min(before + 1, len(frames) - 1)
        amount = position - before
        a = colour_warmth(frames[before], lat[index], lon[index])
        b = colour_warmth(frames[after], lat[index], lon[index])
        warmth.append(a * (1 - amount) + b * amount)
    return {
        "seconds": seconds, "times": times, "km": km, "activity": activity,
        "warmth": smooth(np.array(warmth)), "gap": gaps,
        **weather_controls(weather, times),
    }


def composer_settings(duration: float, **overrides) -> composer.Settings:
    return composer.make_settings(argparse.Namespace(**{**COMPOSER_OPTIONS, **overrides, "duration": duration}))


def compose(records: list[dict], settings: composer.Settings, midi_path: Path, columns: dict[str, str] | None = None) -> pd.DataFrame:
    """same as route_to_midi.process(), without printing."""
    frame, _ = composer.canonicalize(pd.DataFrame(records), columns or {})
    frame, _ = composer.clean(frame)
    steps, _, _, span = composer.aggregate(frame, settings.steps)
    steps = composer.enrich_motion(steps, span)
    features = composer.build_features(steps, settings, True)  # ERA5 wind dir is "from"
    composer.compose(features, settings, midi_path)
    return features


def journey_records(points: list[dict], controls: dict) -> list[dict]:
    """interior gap days dropped so the composer leaves those steps silent; endpoints kept to pin the timeline."""
    days = np.array([timestamp(point["date"]) for point in points])
    warmth = np.interp(days, controls["times"], controls["warmth"])
    last = len(points) - 1
    return [
        {"timestamp": point["date"], "lat": point["lat"], "lon": point["lon"], "km": point["km"], "eic_warmth": float(value)}
        for index, (point, value) in enumerate(zip(points, warmth)) if index in (0, last) or not point.get("gap")
    ]


def story_records(points: list[dict], controls: dict, bird: str) -> list[dict]:
    """daily rows for story_to_midi, with the EIC colour under the bird on that day."""
    days = np.array([timestamp(point["date"]) for point in points])
    warmth = np.interp(days, controls["times"], controls["warmth"])
    return [{
        "bird": bird, "timestamp": point["date"], "lat": point["lat"], "lon": point["lon"], "km": point["km"],
        "colony_km": point.get("colony_km"), "phase": point.get("phase"), "gap": bool(point.get("gap")),
        "eic_warmth": float(value),
    } for point, value in zip(points, warmth)]


def story_midis(journeys: dict[str, list[dict]], profile: Path, journey: float, workdir: Path) -> dict[str, Path]:
    """one MIDI per bird over its own dates; scales are shared so the birds can be compared."""
    settings = story.make_settings(argparse.Namespace(**STORY_OPTIONS, duration=journey))
    notes, tables, dates = [], {}, {}
    for bird, rows in journeys.items():
        frame, _ = story.canonicalize(pd.DataFrame(rows), {})
        frame, _ = story.clean(frame)
        frame = story.attach_eic(frame, profile, None, None, notes)  # adds image brightness by date
        start, end = frame["timestamp"].min(), frame["timestamp"].max()
        span = (end - start).total_seconds()
        tables[bird] = story.enrich(story.step_table(frame, settings.steps, start, end), span / settings.steps / 3600)
        dates[bird] = pd.DatetimeIndex([start + pd.Timedelta(seconds=(i + .5) * span / settings.steps) for i in range(settings.steps)])
    scales = story.Scales(list(tables.values()))
    paths = {}
    for bird in journeys:
        plan = story.build_features(tables[bird], scales, settings, dates[bird], True)
        paths[bird] = workdir / f"{bird.lower()}-story.mid"
        story.compose([plan], [bird], settings, paths[bird])
    return paths


def read_wave(path: Path) -> tuple[np.ndarray, int]:
    with wave.open(str(path), "rb") as source:
        rate, channels, width = source.getframerate(), source.getnchannels(), source.getsampwidth()
        raw = source.readframes(source.getnframes())
    if width != 2:
        raise ValueError(f"Expected 16-bit audio from FluidSynth, got {width * 8}-bit")
    samples = np.frombuffer(raw, dtype="<i2").astype(np.float32).reshape(-1, channels) / 32768
    return (samples if channels == 2 else np.repeat(samples, 2, axis=1)), rate


def render_tracks(midi_path: Path, track_names: tuple[str, ...], sample_rate: int, soundfont: Path, workdir: Path) -> np.ndarray:
    midi = mido.MidiFile(str(midi_path))
    part = mido.MidiFile(type=1, ticks_per_beat=midi.ticks_per_beat)
    part.tracks = [midi.tracks[0], *[track for track in midi.tracks[1:] if track.name in track_names]]
    if len(part.tracks) != len(track_names) + 1:
        raise ValueError(f"{midi_path.name} is missing one of {track_names}")
    stem = "-".join(re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-") for name in track_names)
    part_path, wav_path = workdir / f"{midi_path.stem}-{stem}.mid", workdir / f"{midi_path.stem}-{stem}.wav"
    part.save(str(part_path))
    # mbgm.SF2 warns about unused piano presets; show output only on failure
    result = subprocess.run([
        "fluidsynth", "-ni", "-q", "-g", str(SYNTH_GAIN), "-r", str(sample_rate),
        "-T", "wav", "-O", "s16", "-F", str(wav_path), str(soundfont), str(part_path),
    ], capture_output=True, text=True)
    if result.returncode != 0 or not wav_path.exists():
        raise RuntimeError(f"FluidSynth could not render {part_path.name}:\n{result.stderr}")
    audio, rate = read_wave(wav_path)
    if rate != sample_rate:
        raise ValueError(f"FluidSynth rendered {rate} Hz instead of {sample_rate} Hz")
    return audio


def fit(audio: np.ndarray, duration: float, sample_rate: int) -> np.ndarray:
    """trim release tail to the timeline, fade edges."""
    count = round(duration * sample_rate)
    out = np.zeros((count, 2), dtype=np.float32)
    out[:min(count, len(audio))] = audio[:count]
    fade_in, fade_out = min(round(sample_rate * .01), count // 2), min(round(sample_rate * .24), count // 2)
    out[:fade_in] *= np.linspace(0, 1, fade_in, dtype=np.float32)[:, None]
    out[-fade_out:] *= (np.sin(np.linspace(np.pi / 2, 0, fade_out)) ** 2).astype(np.float32)[:, None]
    return out


def route_stems(melody: np.ndarray, pad: np.ndarray, pulse: np.ndarray, wind_observed: bool) -> dict[str, np.ndarray]:
    """pad is the wind stem only where wind was observed."""
    silence = np.zeros_like(pad)
    return {
        "movement": pulse,
        "temperature": melody if wind_observed else melody + pad,
        "wind": pad if wind_observed else silence,
    }


def render_stems(midi_path: Path, duration: float, sample_rate: int, soundfont: Path, workdir: Path, wind_observed: bool) -> dict[str, np.ndarray]:
    names = composer.TRACK_NAMES
    melody, pad, pulse = (
        fit(render_tracks(midi_path, (names[key],), sample_rate, soundfont, workdir), duration, sample_rate)
        for key in ("bird", "pad", "pulse")
    )
    return route_stems(melody, pad, pulse, wind_observed)


def route_story(melody: np.ndarray, pad: np.ndarray, beats: np.ndarray, drone: np.ndarray) -> dict[str, np.ndarray]:
    """the pad is the NASA image; melody, beats and drone come from the GPS track."""
    return {"movement": melody + beats + drone, "temperature": pad, "wind": np.zeros_like(pad)}


def midi_stems(midi_path: Path, duration: float, sample_rate: int, soundfont: Path, workdir: Path) -> dict[str, np.ndarray]:
    names = [track.name for track in mido.MidiFile(str(midi_path)).tracks[1:]]

    def part(suffix: str) -> np.ndarray:
        name = next((name for name in names if name.lower().endswith(suffix)), None)
        if name is None:
            raise ValueError(f"{midi_path.name} has no '{suffix}' track")
        return fit(render_tracks(midi_path, (name,), sample_rate, soundfont, workdir), duration, sample_rate)

    return route_story(*(part(suffix) for suffix in ("melody", "atmosphere", "beats", "drone")))


def write_wave(audio: np.ndarray, path: Path, sample_rate: int):
    with wave.open(str(path), "wb") as output:
        output.setnchannels(2)
        output.setsampwidth(2)
        output.setframerate(sample_rate)
        output.writeframes((np.clip(audio, -1, 1) * 32767).astype("<i2").tobytes())


def write_mp3(audio: np.ndarray, path: Path, sample_rate: int):
    subprocess.run([
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-f", "f32le",
        "-ar", str(sample_rate), "-ac", "2", "-i", "pipe:0", "-codec:a", "libmp3lame", "-b:a", "192k", str(path),
    ], input=np.clip(audio, -1, 1).astype("<f4").tobytes(), check=True)


def summary(features: pd.DataFrame, settings: composer.Settings) -> dict:
    return {
        "steps": settings.steps, "step_seconds": round(settings.step_seconds, 4),
        "tempo_bpm": [round(float(features["bpm"].min()), 1), round(float(features["bpm"].max()), 1)],
        "pitch": [int(features["temp_pitch"].min()), int(features["temp_pitch"].max())],
        "intervals": sorted({int(value) for value in features["interval"]}),
        "resting_steps": int((features["subdiv"] == 0).sum()),
        "silent_steps": int((~features["present"]).sum()),
    }


def wind_example(nico: dict, nico_features: pd.DataFrame, nico_settings: composer.Settings, hourly: list[dict], workdir: Path, sample_rate: int, soundfont: Path):
    """only wind varies; pitch and pulses held at nico's journey values that day."""
    start, end = timestamp(nico["start_date"]), timestamp(nico["end_date"])
    step = min(int((timestamp(hourly[0]["time"]) - start) / (end - start) * nico_settings.steps), nico_settings.steps - 1)
    pitch, pulses = int(nico_features["temp_pitch"].iloc[step]), int(nico_features["subdiv"].iloc[step])
    settings = composer_settings(
        EXAMPLE_DURATION, pitch_range=[pitch, pitch], max_subdivision=max(pulses, 1),
        still_fraction=1.01 if pulses == 0 else 0,
    )
    records = [{
        "timestamp": row["time"], "lat": row["lat"], "lon": row["lon"], "heading_deg": row["heading_deg"],
        "wind_speed_ms": row["speed"], "wind_dir_deg": row["dir_from"], "speed_kmh": 1.0,
    } for row in hourly]
    midi_path = workdir / "nico-wind-example.mid"
    features = compose(records, settings, midi_path)
    stems = render_stems(midi_path, EXAMPLE_DURATION, sample_rate, soundfont, workdir, wind_observed=True)
    return stems, features, settings


def main():
    parser = argparse.ArgumentParser(description="Render the gull journeys into MP3 mixes and stems.")
    parser.add_argument("--duration", type=float, default=JOURNEY_SECONDS, help="seconds from departure to return; the home ending is added")
    parser.add_argument("--sample-rate", type=int, default=44100)
    parser.add_argument("--data-dir", type=Path, default=DATA)
    parser.add_argument("--audio-dir", type=Path, default=AUDIO)
    parser.add_argument("--soundfont", type=Path, default=SOUNDFONT)
    args = parser.parse_args()
    if not math.isfinite(args.duration) or args.duration <= 0 or args.sample_rate < 8000:
        parser.error("Use a positive duration and a sample rate of at least 8000 Hz")
    for tool in ("ffmpeg", "fluidsynth"):
        if not shutil.which(tool):
            parser.error(f"{tool} is required (macOS: brew install {'fluid-synth' if tool == 'fluidsynth' else tool})")
    if not args.soundfont.exists():
        parser.error(f"Soundfont not found: {args.soundfont}")
    paths = sorted(FRAMES.glob("frame-*.jpg"))
    if not paths:
        parser.error(f"No NASA frames found under {FRAMES}")
    frames = []
    for path in paths:
        with Image.open(path).convert("RGB") as image:
            frames.append(np.asarray(image))
    daily = read_json(args.data_dir / "daily.json")
    totals = {row["bird"]: row for row in read_json(args.data_dir / "totals.json")}
    weather = load_weather(args.data_dir)
    distance_ref = max(float(point["km"]) for point in daily if not point.get("gap"))
    journey = args.duration
    audio_seconds = journey + STORY_OPTIONS["tail_seconds"]
    profile = args.data_dir / "eic_temperature_profile.json"
    profile.write_text(json.dumps(eic_profile(paths), indent=2) + "\n")
    score = {
        "version": 4, "duration": audio_seconds, "journey_seconds": journey, "control_hz": CONTROL_HZ,
        "smoothing_seconds": SMOOTH_SECONDS, "distance_reference_km": distance_ref,
        "provenance": {
            "composer": "story_to_midi.py (Ruzza), 26 s from departure to return plus a 4 s ending at home, rendered with FluidSynth and the mbgm.SF2 General MIDI soundfont",
            "mapping": "EIC warmth under the bird -> pad pitch; image brightness -> pad loudness; latitude -> melody register; daily km -> pulses per beat and rests; distance from colony -> drone loudness; longitude -> pan; return -> mirrored motif",
            "tracking": "Recorded daily positions, 2024-2025; departure to return per bird",
            "temperature": "NASA EIC 2026 imagery, sampled at the bird position; a relative visual score, not 2024 weather or calibrated degrees",
            "wind": "Only Nico's 21 ERA5 hours on 30 October 2024, heard in the wind example; the full journeys receive no wind, so tempo stays at the neutral value",
        },
        "composer_settings": dict(STORY_OPTIONS),
        "birds": {},
    }
    tracks, journeys = {}, {}
    features_by_bird, settings_by_bird = {}, {}
    with tempfile.TemporaryDirectory() as tmp:
        workdir = Path(tmp)
        for bird in BIRDS:
            total = totals[bird]
            points = sorted([point for point in daily if point["bird"] == bird and total["departure_date"] <= point["date"] <= total["return_date"]], key=lambda point: point["date"])
            controls = journey_controls(points, frames, weather[bird], journey, distance_ref)
            settings = composer_settings(journey)
            midi_path = workdir / f"{bird.lower()}.mid"
            # the wind example reuses nico's pitch and pulses from this composition
            features = compose(journey_records(points, controls), settings, midi_path, {"temperature": "eic_warmth", "speed": "km"})
            journeys[bird] = story_records(points, controls, bird)
            features_by_bird[bird], settings_by_bird[bird] = features, settings
            columns = {key: value.tolist() if value.dtype == bool else np.round(value, 4).tolist() for key, value in controls.items() if key not in ("seconds", "times")}
            score["birds"][bird] = {
                "start_date": points[0]["date"], "end_date": points[-1]["date"], "source_days": len(points),
                "weather_available": bool(np.any(controls["weather_known"])),
                "weather_coverage": round(float(np.mean(controls["weather_known"])), 4),
                "columns": columns,
            }
        for bird, midi_path in story_midis(journeys, profile, journey, workdir).items():
            stems = midi_stems(midi_path, audio_seconds, args.sample_rate, args.soundfont, workdir)
            tracks[bird] = (stems, midi_path.read_bytes())
        example = None
        wind_path = args.data_dir / "era5_wind.json"
        if wind_path.exists():
            wind_layer = read_json(wind_path)
            hourly = [row for row in wind_layer.get("hourly_rows", []) if row["bird"] == "Nico"]
            if len(hourly) >= 2:
                stems, features, settings = wind_example(score["birds"]["Nico"], features_by_bird["Nico"], settings_by_bird["Nico"], hourly, workdir, args.sample_rate, args.soundfont)
                example = (stems, wind_layer, hourly, summary(features, settings))
    # one gain for all birds keeps them comparable
    peak = max(float(np.abs(sum(stems.values())).max()) for stems, _ in tracks.values())
    gain = min(12.0, .86 / max(peak, 1e-6))
    score["master_gain"] = round(gain, 6)
    args.audio_dir.mkdir(parents=True, exist_ok=True)
    for bird, (stems, midi_bytes) in tracks.items():
        mix = sum(stems.values()) * gain
        for name, stem in stems.items():
            write_mp3(stem * gain, args.audio_dir / f"{bird.lower()}-{name}.mp3", args.sample_rate)
        write_wave(mix, args.audio_dir / f"{bird.lower()}.wav", args.sample_rate)
        write_mp3(mix, args.audio_dir / f"{bird.lower()}.mp3", args.sample_rate)
        (args.audio_dir / f"{bird.lower()}.mid").write_bytes(midi_bytes)
        print(f"{bird}: {audio_seconds:g}s audio, {journey:g}s journey; {score['birds'][bird]['source_days']} daily records")
    if example:
        stems, wind_layer, hourly, music = example
        files = {}
        for name, stem in stems.items():
            filename = f"nico-{name}-example.mp3"
            write_mp3(stem * gain, args.audio_dir / filename, args.sample_rate)
            files[name] = f"assets/audio/{filename}"
        score["wind_example"] = {
            "bird": "Nico", "date": hourly[0]["date"], "duration": EXAMPLE_DURATION, "files": files,
            "hours": [row["time"] for row in hourly], "source": wind_layer["source"], "music": music,
        }
        print(f"Nico wind example: {EXAMPLE_DURATION:g}s, tempo {music['tempo_bpm'][0]}-{music['tempo_bpm'][1]} BPM, intervals {music['intervals']}")
    (args.data_dir / "journey_score.json").write_text(json.dumps(score, separators=(",", ":"), allow_nan=False) + "\n")


if __name__ == "__main__":
    main()
