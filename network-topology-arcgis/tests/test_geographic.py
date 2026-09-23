# SPDX-License-Identifier: GPL-2.0-or-later

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from network_topology.dangle_resolver import resolve_dangles
from network_topology.geographic import meters_per_degree
from network_topology.units import is_geographic, tolerance_ground_meters


def feature(*coords, **attrs):
    return {"attrs": attrs, "parts": [[(x, y) for x, y in coords]]}


class GeographicDangleTests(unittest.TestCase):
    def test_equator_undershoot_is_one_metre(self):
        lon_scale, _ = meters_per_degree(0.0)
        gap_end = 4.6 / lon_scale
        wall = 5.0 / lon_scale
        features = [
            feature((0.0, 0.0), (gap_end, 0.0)),
            feature((wall, -0.001), (wall, 0.001)),
        ]
        result = resolve_dangles(
            features, 1.0, geographic=True, latitude=0.0, fix_overshoots=False
        )
        self.assertEqual(result.extended, 1)
        end = result.features[0]["parts"][0][-1]
        self.assertAlmostEqual(end.x, wall, places=9)
        self.assertAlmostEqual(end.y, 0.0, places=9)

    def test_equator_gap_beyond_one_metre_stays_open(self):
        lon_scale, _ = meters_per_degree(0.0)
        features = [
            feature((0.0, 0.0), (3.0 / lon_scale, 0.0)),
            feature((5.0 / lon_scale, -0.001), (5.0 / lon_scale, 0.001)),
        ]
        result = resolve_dangles(
            features, 1.0, geographic=True, latitude=0.0, fix_overshoots=False
        )
        self.assertEqual(result.extended, 0)

    def test_overshoot_trimmed_in_metres_at_latitude_45(self):
        lat = 45.0
        lon_scale, _ = meters_per_degree(lat)
        features = [
            feature((0.0, lat), (6.0 / lon_scale, lat)),
            feature((5.0 / lon_scale, lat - 0.01), (5.0 / lon_scale, lat + 0.01)),
        ]
        result = resolve_dangles(
            features, 1.5, geographic=True, latitude=lat, fix_undershoots=False
        )
        self.assertEqual(result.trimmed, 1)
        end = result.features[0]["parts"][0][-1]
        self.assertAlmostEqual(end.x, 5.0 / lon_scale, places=9)
        self.assertAlmostEqual(end.y, lat, places=9)

    def test_high_latitude_diagonal_follows_ground_bearing(self):
        lat = 80.0
        lon_scale, lat_scale = meters_per_degree(lat)
        x0, y0 = 10.0, lat
        x1 = x0 + 10.0 / lon_scale
        y1 = y0 + 10.0 / lat_scale
        wall_x = x1 + 1.0 / lon_scale
        features = [
            feature((x0, y0), (x1, y1)),
            feature((wall_x, lat - 0.01), (wall_x, lat + 0.01)),
        ]
        fixed = resolve_dangles(
            features, 1.5, geographic=True, latitude=lat, fix_overshoots=False
        )
        self.assertEqual(fixed.extended, 1)
        end = fixed.features[0]["parts"][0][-1]
        self.assertAlmostEqual(end.x, wall_x, places=8)
        # Same vertices, treating degrees as if they were metres, misses the wall.
        naive = resolve_dangles(
            features, 1.5 / lat_scale, geographic=False, fix_overshoots=False
        )
        self.assertEqual(naive.extended, 0)

    def test_degree_tolerance_becomes_ground_metres(self):
        self.assertAlmostEqual(tolerance_ground_meters("1 Meters", 60.0), 1.0)
        self.assertAlmostEqual(tolerance_ground_meters("10 Feet", 12.0), 3.048, places=6)
        _, lat_scale = meters_per_degree(0.0)
        self.assertAlmostEqual(
            tolerance_ground_meters("0.00001 Decimal Degrees", 0.0),
            0.00001 * lat_scale,
            places=4,
        )

    def test_high_latitude_gap_is_not_measured_at_the_equator(self):
        lat = 70.0
        lon_scale, _ = meters_per_degree(lat)
        x0 = 12.0
        end = x0 + 4.0 / lon_scale
        wall = end + 1.2 / lon_scale
        clutter = feature(*[(x0 + i * 0.001, 0.0) for i in range(40)])
        features = [
            clutter,
            feature((x0, lat), (end, lat)),
            feature((wall, lat - 0.001), (wall, lat + 0.001)),
        ]
        fixed = resolve_dangles(features, 1.5, geographic=True, fix_overshoots=False)
        self.assertEqual(fixed.extended, 1)
        hit = fixed.features[1]["parts"][0][-1]
        self.assertAlmostEqual(hit.x, wall, places=8)
        # One frame at the equator stretches this 1.2 m gap past the tolerance.
        biased = resolve_dangles(
            features, 1.5, geographic=True, latitude=0.0, fix_overshoots=False
        )
        self.assertEqual(biased.extended, 0)

    def test_overshoot_uses_local_metres_far_from_the_equator(self):
        lat = 70.0
        lon_scale, _ = meters_per_degree(lat)
        x0 = 8.0
        wall = x0 + 5.0 / lon_scale
        end = x0 + 6.0 / lon_scale
        clutter = feature(*[(x0 + i * 0.001, 0.0) for i in range(30)])
        features = [
            clutter,
            feature((x0, lat), (end, lat)),
            feature((wall, lat - 0.002), (wall, lat + 0.002)),
        ]
        result = resolve_dangles(features, 1.5, geographic=True, fix_undershoots=False)
        self.assertEqual(result.trimmed, 1)
        trimmed = result.features[1]["parts"][0][-1]
        self.assertAlmostEqual(trimmed.x, wall, places=7)
        biased = resolve_dangles(
            features, 1.5, geographic=True, latitude=0.0, fix_undershoots=False
        )
        self.assertEqual(biased.trimmed, 0)

    def test_gap_across_the_antimeridian(self):
        lon_scale, _ = meters_per_degree(0.0)
        one = 1.0 / lon_scale
        end = 180.0 - 0.4 * one
        wall = -180.0 + 0.6 * one
        features = [
            feature((179.0, 0.0), (end, 0.0)),
            feature((wall, -0.001), (wall, 0.001)),
        ]
        result = resolve_dangles(features, 1.5, geographic=True, fix_overshoots=False)
        self.assertEqual(result.extended, 1)
        hit = result.features[0]["parts"][0][-1].x
        wall_x = result.features[1]["parts"][0][0].x
        self.assertAlmostEqual(hit, wall_x, places=8)
        xs = [point.x for point in result.features[0]["parts"][0]]
        self.assertLess(max(xs) - min(xs), 2.0)

    def test_epsg_4326_is_geographic(self):
        class SR:
            factoryCode = 4326
            linearUnitName = "Degree"
            type = "Geographic"

        self.assertTrue(is_geographic(SR()))

        class Projected:
            factoryCode = 3857
            linearUnitName = "Meter"
            type = "Projected"

        self.assertFalse(is_geographic(Projected()))


if __name__ == "__main__":
    unittest.main()
