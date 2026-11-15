import importlib.util
import json
import unittest
from pathlib import Path

import numpy as np


ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("build_era5_layer", ROOT / "scripts" / "build_era5_layer.py")
era5 = importlib.util.module_from_spec(spec)
spec.loader.exec_module(era5)


class ERA5LayerTests(unittest.TestCase):
    def test_bilinear_sampling_and_outside_grid(self):
        grid = np.array([[0, 10], [20, 30]])
        self.assertEqual(era5.sample_grid(grid, [0, 1], [0, 1], .5, .5), 15)
        self.assertIsNone(era5.sample_grid(grid, [0, 1], [0, 1], 2, .5))

    def test_only_matching_bird_and_hours_are_used(self):
        layer = json.loads((ROOT / "assets/data/era5_wind.json").read_text())
        grid = json.loads((ROOT / "assets/data/nico_wind_grid.json").read_text())
        self.assertEqual([(row["bird"], row["date"]) for row in layer["rows"]], [("Nico", grid["date"])])
        self.assertIn(layer["rows"][0]["valid_until"], grid["times"])
        self.assertTrue(0 < len(layer["hourly_rows"]) <= len(grid["times"]))
        for row in layer["hourly_rows"]:
            self.assertTrue(grid["lats"][0] <= row["lat"] <= grid["lats"][-1])
            self.assertTrue(grid["lons"][0] <= row["lon"] <= grid["lons"][-1])

    def test_packed_grid_keeps_hourly_order_and_values(self):
        original = json.loads((ROOT / "assets/data/nico_wind_grid.json").read_text())
        metadata = json.loads((ROOT / "assets/data/era5_wind_grid.json").read_text())
        values = np.frombuffer((ROOT / "assets/data/era5_wind_grid.bin").read_bytes(), dtype=np.int8)
        values = values.reshape(len(original["times"]), 2, len(original["lats"]), len(original["lons"])) / metadata["scale"]
        np.testing.assert_allclose(values[:, 0], original["u"], atol=.101)
        np.testing.assert_allclose(values[:, 1], original["v"], atol=.101)


if __name__ == "__main__":
    unittest.main()
