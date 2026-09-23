# SPDX-License-Identifier: GPL-2.0-or-later

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from network_topology.units import parse_linear_unit, tolerance_in_xy_units


class FakeSpatialRef:
    def __init__(self, name, meters_per_unit):
        self.linearUnitName = name
        self.metersPerUnit = meters_per_unit


class LinearUnitTests(unittest.TestCase):
    def test_parse_bare_number(self):
        self.assertEqual(parse_linear_unit("1.5"), (1.5, None))

    def test_parse_arcgis_linear_unit(self):
        self.assertEqual(parse_linear_unit("10 Meters"), (10.0, "Meters"))

    def test_same_unit_is_unchanged(self):
        sr = FakeSpatialRef("Meter", 1.0)
        self.assertAlmostEqual(tolerance_in_xy_units("2.5 Meter", sr), 2.5)

    def test_feet_to_meters(self):
        sr = FakeSpatialRef("Meter", 1.0)
        self.assertAlmostEqual(tolerance_in_xy_units("10 Feet", sr), 3.048, places=6)

    def test_negative_rejected(self):
        with self.assertRaises(ValueError):
            tolerance_in_xy_units("-1 Meters", FakeSpatialRef("Meter", 1.0))

    def test_empty_is_zero(self):
        self.assertEqual(tolerance_in_xy_units("  "), 0.0)
        self.assertEqual(tolerance_in_xy_units(None), 0.0)


if __name__ == "__main__":
    unittest.main()
