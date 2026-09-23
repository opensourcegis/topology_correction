# SPDX-License-Identifier: GPL-2.0-or-later

"""Behavior tests for undershoot extension and overshoot trimming.

The cases marked as QGIS counterparts match tests/test_dangle_resolver.py in
https://github.com/Oksion/network-topology-qgis
"""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from network_topology.dangle_resolver import (
    PARALLEL_MIN_PARTS,
    PARALLEL_MIN_VERTICES,
    resolve_dangles,
    use_parallel,
)
from network_topology.geometry import (
    Point,
    polyline_length,
    segment_intersections,
)


def P(*xy):
    return Point(xy[0], xy[1])


def line(*coords):
    return [P(x, y) for x, y in coords]


def feature(*coords, **attrs):
    return {"attrs": attrs, "parts": [line(*coords)]}


def multipart(*parts, **attrs):
    return {"attrs": attrs, "parts": [line(*part) for part in parts]}


def length_of(feat):
    return sum(polyline_length(part) for part in feat["parts"])


class ResolveDanglesTests(unittest.TestCase):
    def test_undershoot_is_extended(self):
        # QGIS: LineString (0 5, 4.6 5) grows to length 5 against x=5.
        features = [
            feature((0, 5), (4.6, 5), id=0),
            feature((5, 0), (5, 10), id=1),
        ]
        original = polyline_length(features[0]["parts"][0])
        result = resolve_dangles(features, 1.0, fix_undershoots=True, fix_overshoots=False)
        self.assertEqual(len(result.features), 2)
        self.assertAlmostEqual(length_of(result.features[0]), 5.0, places=6)
        self.assertAlmostEqual(original, 4.6, places=6)
        self.assertEqual(result.features[0]["attrs"]["id"], 0)
        self.assertEqual(result.extended, 1)
        self.assertEqual(result.trimmed, 0)
        end = result.features[0]["parts"][0][-1]
        self.assertAlmostEqual(end.x, 5.0, places=6)
        self.assertAlmostEqual(end.y, 5.0, places=6)

    def test_overshoot_is_trimmed(self):
        # QGIS: LineString (0 5, 6 5) overshoots x=5 by 1. Tolerance 1.5 trims to 5.
        features = [
            feature((0, 5), (6, 5), id=0),
            feature((5, 0), (5, 10), id=1),
        ]
        result = resolve_dangles(features, 1.5, fix_undershoots=False, fix_overshoots=True)
        self.assertAlmostEqual(length_of(result.features[0]), 5.0, places=6)
        self.assertEqual(result.trimmed, 1)
        self.assertEqual(result.extended, 0)
        end = result.features[0]["parts"][0][-1]
        self.assertAlmostEqual(end.x, 5.0, places=6)

    def test_tail_longer_than_tolerance_kept(self):
        features = [
            feature((0, 5), (6, 5), id=0),
            feature((5, 0), (5, 10), id=1),
        ]
        result = resolve_dangles(features, 0.5, fix_undershoots=False, fix_overshoots=True)
        self.assertAlmostEqual(length_of(result.features[0]), 6.0, places=6)
        self.assertEqual(result.trimmed, 0)

    def test_connected_end_untouched(self):
        features = [
            feature((0, 0), (5, 0), id=0),
            feature((5, 0), (10, 0), id=1),
        ]
        result = resolve_dangles(features, 2.0, fix_undershoots=True, fix_overshoots=True)
        total = sum(length_of(feat) for feat in result.features)
        self.assertAlmostEqual(total, 10.0, places=6)
        self.assertEqual(result.extended, 0)
        self.assertEqual(result.trimmed, 0)

    def test_extension_follows_direction_not_sideways_snap(self):
        # The target sits 1 unit off the ray. A sideways snap would move the end;
        # this tool must leave it alone.
        features = [
            feature((0, 0), (4, 0)),
            feature((4.5, 1), (10, 1)),
        ]
        result = resolve_dangles(features, 2.0, fix_undershoots=True, fix_overshoots=False)
        self.assertAlmostEqual(length_of(result.features[0]), 4.0, places=6)
        self.assertEqual(result.extended, 0)

    def test_diagonal_undershoot_hits_along_bearing(self):
        features = [
            feature((0, 0), (4, 4)),
            feature((5, -10), (5, 10)),
        ]
        missed = resolve_dangles(features, 1.0, fix_undershoots=True, fix_overshoots=False)
        self.assertEqual(missed.extended, 0)
        hit = resolve_dangles(features, 1.5, fix_undershoots=True, fix_overshoots=False)
        self.assertEqual(hit.extended, 1)
        end = hit.features[0]["parts"][0][-1]
        self.assertAlmostEqual(end.x, 5.0, places=6)
        self.assertAlmostEqual(end.y, 5.0, places=6)

    def test_gap_beyond_tolerance_is_kept(self):
        features = [
            feature((0, 5), (3, 5)),
            feature((5, 0), (5, 10)),
        ]
        result = resolve_dangles(features, 1.0)
        self.assertAlmostEqual(length_of(result.features[0]), 3.0, places=6)
        self.assertEqual(result.extended, 0)

    def test_both_ends_undershoot(self):
        features = [
            feature((0.4, 5), (4.6, 5)),
            feature((0, 0), (0, 10)),
            feature((5, 0), (5, 10)),
        ]
        result = resolve_dangles(features, 1.0, fix_undershoots=True, fix_overshoots=False)
        self.assertEqual(result.extended, 2)
        self.assertAlmostEqual(length_of(result.features[0]), 5.0, places=6)
        self.assertAlmostEqual(result.features[0]["parts"][0][0].x, 0.0, places=6)
        self.assertAlmostEqual(result.features[0]["parts"][0][-1].x, 5.0, places=6)

    def test_trim_uses_crossing_nearest_the_dangle(self):
        features = [
            feature((0, 0), (10, 0)),
            feature((8, -1), (8, 1)),
            feature((9, -1), (9, 1)),
        ]
        result = resolve_dangles(features, 1.5, fix_undershoots=False, fix_overshoots=True)
        self.assertAlmostEqual(length_of(result.features[0]), 9.0, places=6)
        self.assertEqual(result.trimmed, 1)

    def test_trim_keeps_interior_vertices(self):
        features = [
            feature((0, 5), (2, 6), (4, 5), (6, 5)),
            feature((5, 0), (5, 10)),
        ]
        result = resolve_dangles(features, 2.0, fix_undershoots=False, fix_overshoots=True)
        pts = result.features[0]["parts"][0]
        self.assertAlmostEqual(pts[-1].x, 5.0, places=6)
        self.assertTrue(any(abs(p.x - 2) < 1e-6 and abs(p.y - 6) < 1e-6 for p in pts))
        self.assertTrue(any(abs(p.x - 4) < 1e-6 and abs(p.y - 5) < 1e-6 for p in pts))

    def test_degenerate_trim_does_not_delete_the_line(self):
        # Both ends are free and the only crossing is the midpoint. Cutting both
        # ends would leave a point, so the part is kept.
        features = [
            feature((-0.2, 5), (0.2, 5)),
            feature((0, 0), (0, 10)),
        ]
        result = resolve_dangles(features, 1.0)
        self.assertAlmostEqual(length_of(result.features[0]), 0.4, places=6)
        self.assertEqual(result.trimmed, 0)

    def test_multipart_part_extends_to_its_sibling(self):
        features = [
            multipart(((0, 5), (4.6, 5)), ((5, 0), (5, 10)), id=3),
        ]
        result = resolve_dangles(features, 1.0, fix_undershoots=True, fix_overshoots=False)
        self.assertEqual(len(result.features), 1)
        self.assertEqual(len(result.features[0]["parts"]), 2)
        self.assertAlmostEqual(polyline_length(result.features[0]["parts"][0]), 5.0, places=6)
        self.assertEqual(result.features[0]["attrs"]["id"], 3)

    def test_single_pass_matches_the_input_not_other_edits(self):
        # Each free end sees the original neighbour, so both extend across the gap.
        features = [
            feature((0, 5), (2, 5)),
            feature((3, 5), (5, 5)),
        ]
        result = resolve_dangles(features, 1.0, fix_undershoots=True, fix_overshoots=False)
        self.assertEqual(result.extended, 2)
        self.assertAlmostEqual(length_of(result.features[0]), 3.0, places=6)
        self.assertAlmostEqual(length_of(result.features[1]), 3.0, places=6)

    def test_zero_tolerance_changes_nothing(self):
        features = [
            feature((0, 5), (4.6, 5)),
            feature((5, 0), (5, 10)),
        ]
        result = resolve_dangles(features, 0.0)
        self.assertAlmostEqual(length_of(result.features[0]), 4.6, places=6)
        self.assertEqual(result.extended, 0)

    def test_flags_are_independent(self):
        features = [
            feature((0, 5), (6, 5)),
            feature((5, 0), (5, 10)),
        ]
        extend_only = resolve_dangles(features, 1.5, fix_undershoots=True, fix_overshoots=False)
        # The end past the crossing is a dangle, but extending it further is not an
        # undershoot toward another line within tolerance. Length stays 6.
        self.assertAlmostEqual(length_of(extend_only.features[0]), 6.0, places=6)
        self.assertEqual(extend_only.trimmed, 0)

    def test_large_coordinates(self):
        base = 1_000_000.0
        features = [
            feature((base, base + 5), (base + 4.6, base + 5)),
            feature((base + 5, base), (base + 5, base + 10)),
        ]
        result = resolve_dangles(features, 1.0, fix_undershoots=True, fix_overshoots=False)
        self.assertAlmostEqual(length_of(result.features[0]), 5.0, places=5)
        self.assertEqual(result.extended, 1)

    def test_negative_coordinates(self):
        features = [
            feature((-5, 5), (-0.4, 5)),
            feature((0, 0), (0, 10)),
        ]
        result = resolve_dangles(features, 1.0, fix_undershoots=True, fix_overshoots=False)
        self.assertAlmostEqual(length_of(result.features[0]), 5.0, places=6)
        self.assertAlmostEqual(result.features[0]["parts"][0][-1].x, 0.0, places=6)

    def test_z_and_m_carried_on_extend_and_trim(self):
        undershoot = [
            {
                "attrs": None,
                "parts": [[Point(0, 5, 10, 1), Point(4.6, 5, 20, 2)]],
            },
            {"attrs": None, "parts": [[Point(5, 0, 0, 0), Point(5, 10, 0, 0)]]},
        ]
        extended = resolve_dangles(undershoot, 1.0, fix_undershoots=True, fix_overshoots=False)
        tip = extended.features[0]["parts"][0][-1]
        self.assertAlmostEqual(tip.z, 20.0, places=6)
        self.assertAlmostEqual(tip.m, 2.0, places=6)

        overshoot = [
            {
                "attrs": None,
                "parts": [[Point(0, 5, 0, 0), Point(6, 5, 60, 6)]],
            },
            {"attrs": None, "parts": [[Point(5, 0), Point(5, 10)]]},
        ]
        trimmed = resolve_dangles(overshoot, 1.5, fix_undershoots=False, fix_overshoots=True)
        tip = trimmed.features[0]["parts"][0][-1]
        # Cut is 5/6 of the way from z=0 to z=60.
        self.assertAlmostEqual(tip.z, 50.0, places=6)
        self.assertAlmostEqual(tip.m, 5.0, places=6)


class ParallelResolveTests(unittest.TestCase):
    def test_small_network_stays_on_one_thread(self):
        import concurrent.futures as futures

        original = futures.ThreadPoolExecutor

        def fail(*_args, **_kwargs):
            raise AssertionError("small input started a thread pool")

        futures.ThreadPoolExecutor = fail
        try:
            result = resolve_dangles(
                [feature((0, 5), (4.6, 5)), feature((5, 0), (5, 10))],
                1.0,
                fix_overshoots=False,
            )
        finally:
            futures.ThreadPoolExecutor = original
        self.assertEqual(result.extended, 1)

    def test_threshold_switches_only_for_a_large_input(self):
        self.assertFalse(use_parallel([[(0, 0), (1, 0)]] * 10))
        self.assertTrue(use_parallel([[(0, 0), (1, 0)]] * PARALLEL_MIN_PARTS))
        self.assertTrue(use_parallel([[(0, 0)] * PARALLEL_MIN_VERTICES, [(0, 0), (1, 0)]]))

    def test_threads_match_the_serial_result(self):
        features = [feature((0, y), (4.6, y)) for y in range(24)]
        features.append(feature((5, -1), (5, 30)))
        serial = resolve_dangles(features, 1.0, workers=1, fix_overshoots=False)
        parallel = resolve_dangles(features, 1.0, workers=4, fix_overshoots=False)
        self.assertEqual(serial.extended, parallel.extended)
        self.assertEqual(parallel.extended, 24)
        for left, right in zip(serial.features, parallel.features):
            for part_a, part_b in zip(left["parts"], right["parts"]):
                for point_a, point_b in zip(part_a, part_b):
                    self.assertAlmostEqual(point_a.x, point_b.x, places=6)
                    self.assertAlmostEqual(point_a.y, point_b.y, places=6)

    def test_parallel_geographic_matches_serial(self):
        from network_topology.geographic import meters_per_degree

        lon_scale, _ = meters_per_degree(0.0)
        features = [
            feature((0.0, lat), (4.6 / lon_scale, lat)) for lat in (i * 0.15 for i in range(8))
        ]
        features.append(feature((5.0 / lon_scale, -0.2), (5.0 / lon_scale, 2.0)))
        serial = resolve_dangles(features, 1.0, geographic=True, workers=1, fix_overshoots=False)
        parallel = resolve_dangles(features, 1.0, geographic=True, workers=3, fix_overshoots=False)
        self.assertEqual(serial.extended, parallel.extended)
        self.assertGreater(serial.extended, 0)
        for left, right in zip(serial.features, parallel.features):
            for part_a, part_b in zip(left["parts"], right["parts"]):
                self.assertEqual(len(part_a), len(part_b))
                for point_a, point_b in zip(part_a, part_b):
                    self.assertAlmostEqual(point_a.x, point_b.x, places=6)
                    self.assertAlmostEqual(point_a.y, point_b.y, places=6)


class SegmentIntersectionTests(unittest.TestCase):
    def test_proper_crossing(self):
        hits = segment_intersections(P(0, 0), P(2, 2), P(0, 2), P(2, 0), 1e-9)
        self.assertEqual(len(hits), 1)
        self.assertAlmostEqual(hits[0].x, 1.0, places=6)
        self.assertAlmostEqual(hits[0].y, 1.0, places=6)

    def test_parallel_miss(self):
        hits = segment_intersections(P(0, 0), P(5, 0), P(0, 1), P(5, 1), 1e-9)
        self.assertEqual(hits, [])

    def test_endpoint_touch(self):
        hits = segment_intersections(P(0, 0), P(5, 0), P(5, 0), P(5, 5), 1e-9)
        self.assertEqual(len(hits), 1)
        self.assertAlmostEqual(hits[0].x, 5.0, places=6)
        self.assertAlmostEqual(hits[0].y, 0.0, places=6)

    def test_collinear_overlap_returns_endpoints(self):
        hits = segment_intersections(P(0, 0), P(10, 0), P(4, 0), P(6, 0), 1e-9)
        xs = sorted(round(p.x, 6) for p in hits)
        self.assertEqual(xs, [4.0, 6.0])


if __name__ == "__main__":
    unittest.main()
