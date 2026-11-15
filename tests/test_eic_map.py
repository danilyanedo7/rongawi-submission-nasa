import importlib.util
import unittest
from pathlib import Path

import numpy as np


spec = importlib.util.spec_from_file_location("build_eic_layer", Path(__file__).resolve().parents[1] / "scripts" / "build_eic_layer.py")
eic = importlib.util.module_from_spec(spec)
spec.loader.exec_module(eic)


class EICMapTests(unittest.TestCase):
    def test_hiroshige_runs_blue_to_orange(self):
        colours = eic.hiroshige(np.array([0.0, 1.0]))
        np.testing.assert_array_equal(colours, [[30, 70, 110], [231, 98, 84]])  # #1e466e, #e76254

    def test_europe_reads_cool_and_middle_east_warm(self):
        europe, middle_east = eic.display(np.array([.56, .81]))  # median warmth over 2013-14 frames
        self.assertLess(europe, .45)
        self.assertGreater(middle_east, .75)

    def test_relative_colours_preserve_cool_to_warm_order(self):
        src = np.zeros((8, 4, 3), dtype=np.float32)
        for index, stop in enumerate((3, 6, 9, 12)):
            src[:, index] = eic.EIC_SCALE[stop]
        values = eic.relative_warmth(src)
        self.assertTrue(np.all(np.diff(values, axis=1) > 0))
        np.testing.assert_allclose(values[4], np.array([3, 6, 9, 12]) / 15, atol=1e-6)

    def test_reprojection_does_not_quantize_the_relative_field(self):
        values = np.full((16, 32), .375, dtype=np.float32)
        projected = eic.reproject(values)
        self.assertGreater(projected.size, values.size)
        np.testing.assert_allclose(projected, .375, atol=1e-6)


if __name__ == "__main__":
    unittest.main()
