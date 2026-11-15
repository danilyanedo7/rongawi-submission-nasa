import importlib.util
import shutil
import tempfile
import unittest
from pathlib import Path

import mido
import numpy as np


spec = importlib.util.spec_from_file_location("generate_audio", Path(__file__).resolve().parents[1] / "scripts" / "generate_audio.py")
audio = importlib.util.module_from_spec(spec)
spec.loader.exec_module(audio)


def journey(days=12):
    return [
        {"timestamp": f"2014-01-{day + 1:02d}", "lat": 30 + day, "lon": -9 + day * .5,
         "km": 0 if day in (4, 5) else 50 + day * 20, "eic_warmth": day / days}
        for day in range(days)
    ]


class JourneyAudioTests(unittest.TestCase):
    def test_smoothing_does_not_fill_or_bridge_missing_data(self):
        values = np.r_[np.ones(10), np.full(6, 99), np.full(10, 5)]
        valid = np.r_[np.ones(10, dtype=bool), np.zeros(6, dtype=bool), np.ones(10, dtype=bool)]
        result = audio.smooth(values, valid)
        np.testing.assert_allclose(result[:10], 1)
        np.testing.assert_allclose(result[10:16], 0)
        np.testing.assert_allclose(result[16:], 5)

    def test_sparse_weather_is_not_extended_across_the_journey(self):
        rows = [{"date": "2014-01-01", "speed": 8}, {"date": "2014-01-04", "speed": 12}]
        start = audio.timestamp("2014-01-01")
        times = start + np.arange(0, 4, .25) * audio.DAY
        controls = audio.weather_controls(rows, times)
        missing = (times >= start + audio.DAY) & (times < start + audio.DAY * 3)
        self.assertFalse(controls["weather_known"][missing].any())
        np.testing.assert_array_equal(controls["wind_speed"][missing], 0)

    def test_tracking_gap_mutes_activity_and_keeps_calendar_time(self):
        points = [
            {"date": "2014-01-01", "lat": 30, "lon": -9, "km": 100},
            {"date": "2014-01-02", "lat": 31, "lon": -8, "km": 200},
            {"date": "2014-01-06", "lat": 40, "lon": 0, "km": 400, "gap": True},
            {"date": "2014-01-07", "lat": 41, "lon": 1, "km": 100},
        ]
        frame = np.full((8, 16, 3), [235, 80, 25], dtype=np.uint8)
        controls = audio.journey_controls(points, [frame], [], 6, 400)
        self.assertEqual(len(controls["seconds"]), 121)
        self.assertTrue(controls["gap"][60])
        self.assertEqual(controls["activity"][60], 0)
        self.assertFalse(controls["gap"][0])
        records = audio.journey_records(points, controls)
        self.assertEqual([row["timestamp"] for row in records], ["2014-01-01", "2014-01-02", "2014-01-07"])
        points[0]["gap"] = True  # a flagged endpoint still pins the timeline
        self.assertEqual(audio.journey_records(points, controls)[0]["timestamp"], "2014-01-01")

    def test_weather_stops_at_last_matched_hour(self):
        start = audio.timestamp("2014-04-20T00:00:00Z")
        times = start + np.arange(25) * 3600
        controls = audio.weather_controls([{"date": "2014-04-20", "speed": 8, "valid_until": "2014-04-20T17:00:00Z"}], times)
        self.assertTrue(controls["weather_known"][:18].all())
        self.assertFalse(controls["weather_known"][18:].any())
        np.testing.assert_array_equal(controls["wind_speed"][18:], 0)

    def test_wind_interval_follows_the_interval_ladder(self):
        self.assertEqual(audio.wind_interval(8, 8), 12)    # full tailwind: octave
        self.assertEqual(audio.wind_interval(8, -8), 1)    # full headwind: semitone
        self.assertEqual(audio.wind_interval(0, 0), 7)     # calm: neutral fifth

    def test_composition_fills_the_timeline_exactly(self):
        settings = audio.composer_settings(15)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "journey.mid"
            features = audio.compose(journey(), settings, path, {"temperature": "eic_warmth", "speed": "km"})
            midi = mido.MidiFile(str(path))
        self.assertAlmostEqual(midi.length, 15, places=3)
        self.assertEqual(settings.steps, 30)
        self.assertEqual([track.name for track in midi.tracks[1:]], ["Bird (melody)", "Atmosphere (pad)", "Beats (pulse)"])
        self.assertGreater(features["temp_pitch"].iloc[-1], features["temp_pitch"].iloc[0])  # warmer image, higher note
        self.assertTrue((features["subdiv"] == 0).any())  # still days hold a note

    def test_pad_is_the_wind_voice_only_where_wind_was_observed(self):
        melody, pad, pulse = (np.full((4, 2), value, dtype=np.float32) for value in (.1, .2, .3))
        unobserved = audio.route_stems(melody, pad, pulse, wind_observed=False)
        np.testing.assert_allclose(unobserved["temperature"], .3)
        np.testing.assert_array_equal(unobserved["wind"], 0)
        observed = audio.route_stems(melody, pad, pulse, wind_observed=True)
        np.testing.assert_allclose(observed["wind"], .2)
        np.testing.assert_allclose(observed["temperature"], .1)
        for routed in (unobserved, observed):
            np.testing.assert_allclose(sum(routed.values()), melody + pad + pulse)  # nothing lost or doubled
            np.testing.assert_allclose(routed["movement"], pulse)

    def test_fit_trims_the_release_tail_and_ends_silent(self):
        tail = np.ones((9000, 2), dtype=np.float32)
        fitted = audio.fit(tail, 1, 8000)
        self.assertEqual(fitted.shape, (8000, 2))
        np.testing.assert_allclose(fitted[[0, -1]], 0, atol=1e-6)
        self.assertEqual(audio.fit(tail[:100], 1, 8000).shape, (8000, 2))

    @unittest.skipUnless(shutil.which("fluidsynth") and audio.SOUNDFONT.exists(), "needs fluidsynth and the soundfont")
    def test_soundfont_renders_each_track(self):
        with tempfile.TemporaryDirectory() as tmp:
            workdir = Path(tmp)
            path = workdir / "journey.mid"
            audio.compose(journey(), audio.composer_settings(3), path, {"temperature": "eic_warmth", "speed": "km"})
            stems = audio.render_stems(path, 3, 22050, audio.SOUNDFONT, workdir, wind_observed=True)
        for name in audio.STEMS:
            self.assertEqual(stems[name].shape, (3 * 22050, 2))
            self.assertGreater(float(np.abs(stems[name]).max()), 0, name)

    def test_story_pad_follows_each_birds_own_image_colour(self):
        def rows(bird, warmth):
            return [{"bird": bird, "timestamp": f"2014-01-{day + 1:02d}", "lat": 50 - day, "lon": 3 - day * .3,
                     "km": 50 + day * 20, "colony_km": day * 300, "phase": "outward", "gap": False,
                     "eic_warmth": warmth} for day in range(12)]
        with tempfile.TemporaryDirectory() as tmp:
            workdir = Path(tmp)
            profile = workdir / "profile.json"
            profile.write_text('[{"file": "a.jpg", "brightness": 0.4, "warmth": 0.5}, {"file": "b.jpg", "brightness": 0.6, "warmth": 0.5}]')
            journeys = {"Cool": rows("Cool", .1), "Warm": rows("Warm", .9)}
            paths = audio.story_midis(journeys, profile, 6, workdir)
            pads = {}
            for bird, path in paths.items():
                midi = mido.MidiFile(str(path))
                self.assertAlmostEqual(midi.length, 6 + audio.STORY_OPTIONS["tail_seconds"], places=2)
                pad = next(track for track in midi.tracks if track.name.endswith("atmosphere"))
                pads[bird] = min(msg.note for msg in pad if msg.type == "note_on" and msg.velocity > 0)
        self.assertGreater(pads["Warm"], pads["Cool"])  # warmer colour under the bird, higher harmony

    def test_story_stems_keep_the_image_apart_from_the_gps_track(self):
        melody, pad, beats, drone = (np.full((4, 2), value, dtype=np.float32) for value in (.1, .2, .3, .4))
        stems = audio.route_story(melody, pad, beats, drone)
        np.testing.assert_allclose(stems["temperature"], .2)
        np.testing.assert_allclose(stems["movement"], .8)
        np.testing.assert_array_equal(stems["wind"], 0)


if __name__ == "__main__":
    unittest.main()
