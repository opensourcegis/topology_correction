# SPDX-License-Identifier: GPL-2.0-or-later

import struct
import unittest
import zlib
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from network_topology.dangle_resolver import resolve_dangles
from network_topology.review_ui import (
    apply_decisions,
    claim_choice,
    collect_corrections,
    decision_for_key,
    dedupe_corrections,
    highlight_band,
    highlight_radius,
    is_review_mode,
    release_choice,
    render_review_png,
    review_highlight,
    review_zoom_extent,
)


def feature(*coords):
    return {"attrs": None, "parts": [[(x, y) for x, y in coords]]}


def _png_pixels(data: bytes) -> bytes:
    assert data.startswith(b"\x89PNG\r\n\x1a\n")
    pos = 8
    width = height = None
    idat = b""
    while pos < len(data):
        length = struct.unpack(">I", data[pos : pos + 4])[0]
        tag = data[pos + 4 : pos + 8]
        chunk = data[pos + 8 : pos + 8 + length]
        if tag == b"IHDR":
            width, height = struct.unpack(">II", chunk[:8])
        elif tag == b"IDAT":
            idat += chunk
        pos += 12 + length
        if tag == b"IEND":
            break
    raw = zlib.decompress(idat)
    rows = []
    stride = width * 3
    cursor = 0
    for _ in range(height):
        assert raw[cursor] == 0
        cursor += 1
        rows.append(raw[cursor : cursor + stride])
        cursor += stride
    return b"".join(rows)


def _has_color(pixels: bytes, color, slack: int = 12) -> bool:
    target = bytes(color)
    for index in range(0, len(pixels), 3):
        pixel = pixels[index : index + 3]
        if all(abs(pixel[channel] - target[channel]) <= slack for channel in range(3)):
            return True
    return False


class ReviewDecisionTests(unittest.TestCase):
    def _undershoot(self):
        return [
            feature((0, 5), (4.6, 5)),
            feature((5, 0), (5, 10)),
        ]

    def _overshoot(self):
        return [
            feature((0, 5), (6, 5)),
            feature((5, 0), (5, 10)),
        ]

    def test_tick_extends_and_cross_leaves_the_gap(self):
        features = self._undershoot()
        corrections = collect_corrections(features, 1.0, fix_overshoots=False)
        self.assertEqual(len(corrections), 1)
        self.assertEqual(corrections[0].kind, "undershoot")
        self.assertAlmostEqual(corrections[0].gap, 0.4, places=6)

        accepted = apply_decisions(features, 1.0, corrections, [True], fix_overshoots=False)
        self.assertEqual(accepted.extended, 1)
        self.assertAlmostEqual(accepted.features[0]["parts"][0][-1].x, 5.0, places=6)

        rejected = apply_decisions(features, 1.0, corrections, [False], fix_overshoots=False)
        self.assertEqual(rejected.extended, 0)
        self.assertAlmostEqual(rejected.features[0]["parts"][0][-1].x, 4.6, places=6)

    def test_rejecting_a_trim_does_not_invent_an_extend(self):
        features = self._overshoot()
        corrections = collect_corrections(features, 1.5, fix_undershoots=False)
        self.assertEqual([item.kind for item in corrections], ["overshoot"])
        rejected = apply_decisions(features, 1.5, corrections, [False], fix_undershoots=False)
        self.assertEqual(rejected.trimmed, 0)
        self.assertEqual(rejected.extended, 0)
        self.assertAlmostEqual(rejected.features[0]["parts"][0][-1].x, 6.0, places=6)

    def test_accepting_every_correction_matches_the_automatic_pass(self):
        features = self._undershoot() + [feature((0, 0), (6, 0)), feature((5, -1), (5, 1))]
        # The third feature is an overshoot on y=0 against x=5, plus the undershoot above.
        automatic = resolve_dangles(features, 1.5)
        corrections = collect_corrections(features, 1.5)
        self.assertGreaterEqual(len(corrections), 2)
        reviewed = apply_decisions(features, 1.5, corrections, [True] * len(corrections))
        self.assertEqual(automatic.extended, reviewed.extended)
        self.assertEqual(automatic.trimmed, reviewed.trimmed)
        for left, right in zip(automatic.features, reviewed.features):
            for part_a, part_b in zip(left["parts"], right["parts"]):
                self.assertEqual(len(part_a), len(part_b))
                for point_a, point_b in zip(part_a, part_b):
                    self.assertAlmostEqual(point_a.x, point_b.x, places=6)
                    self.assertAlmostEqual(point_a.y, point_b.y, places=6)

    def test_one_end_can_be_ticked_and_the_other_crossed(self):
        features = [
            feature((4.6, 5), (0, 5), (4.7, 5)),
            feature((5, 0), (5, 10)),
        ]
        corrections = collect_corrections(features, 1.0, fix_overshoots=False)
        self.assertEqual(len(corrections), 2)
        flags = [item.at_start for item in corrections]
        result = apply_decisions(features, 1.0, corrections, flags, fix_overshoots=False)
        self.assertEqual(result.extended, 1)
        part = result.features[0]["parts"][0]
        self.assertAlmostEqual(part[0].x, 5.0, places=6)
        self.assertAlmostEqual(part[-1].x, 4.7, places=6)


class ReviewHighlightTests(unittest.TestCase):
    def test_undershoot_highlights_the_end_segment_and_the_extension(self):
        features = [
            feature((0, 5), (2, 5), (4.6, 5)),
            feature((5, 0), (5, 10)),
        ]
        correction = collect_corrections(features, 1.0, fix_overshoots=False)[0]
        pieces = review_highlight(correction)
        self.assertEqual(pieces["kind"], "undershoot")
        self.assertAlmostEqual(pieces["dangle"][0].x, 2.0, places=6)
        self.assertAlmostEqual(pieces["dangle"][1].x, 4.6, places=6)
        self.assertAlmostEqual(pieces["change"][0].x, 4.6, places=6)
        self.assertAlmostEqual(pieces["change"][1].x, 5.0, places=6)
        self.assertAlmostEqual(pieces["anchor"].x, 4.6, places=6)
        self.assertNotAlmostEqual(pieces["dangle"][0].x, 0.0, places=6)
        radius = highlight_radius(correction)
        self.assertAlmostEqual(radius, 0.4 * 0.45, places=6)
        band = highlight_band(pieces["dangle"], radius)
        self.assertEqual(len(band), 4)
        self.assertAlmostEqual(max(point.y for point in band) - min(point.y for point in band), radius * 2, places=6)

    def test_overshoot_highlights_the_tail(self):
        features = [
            feature((0, 5), (3, 5), (6, 5)),
            feature((5, 0), (5, 10)),
        ]
        correction = collect_corrections(features, 1.5, fix_undershoots=False)[0]
        pieces = review_highlight(correction)
        self.assertEqual(pieces["kind"], "overshoot")
        self.assertAlmostEqual(pieces["anchor"].x, 6.0, places=6)
        self.assertAlmostEqual(pieces["change"][0].x, 6.0, places=6)
        self.assertAlmostEqual(pieces["change"][1].x, 5.0, places=6)


class ReviewZoomTests(unittest.TestCase):
    def test_extent_frames_the_gap_and_not_the_whole_line(self):
        features = [
            feature((0, 5), (4.6, 5)),
            feature((5, -5000), (5, 5000)),
        ]
        correction = collect_corrections(features, 1.0, fix_overshoots=False)[0]
        minx, miny, maxx, maxy = review_zoom_extent(correction)
        self.assertAlmostEqual(minx, 4.0, places=5)
        self.assertAlmostEqual(maxx, 5.6, places=5)
        self.assertAlmostEqual(miny, 4.2, places=5)
        self.assertAlmostEqual(maxy, 5.8, places=5)
        self.assertLess(maxy, 100.0)
        self.assertGreater(minx, -1.0)


class ReviewKeyTests(unittest.TestCase):
    def test_enter_accepts_and_space_rejects(self):
        self.assertTrue(decision_for_key("Return"))
        self.assertTrue(decision_for_key("KP_Enter"))
        self.assertFalse(decision_for_key("space"))
        self.assertIsNone(decision_for_key("Escape"))

    def test_a_second_answer_for_the_same_end_is_ignored(self):
        state = {"i": 0}
        self.assertTrue(claim_choice(state))
        self.assertFalse(claim_choice(state))
        release_choice(state)
        self.assertTrue(claim_choice(state))

    def test_the_same_end_is_kept_once(self):
        features = [
            feature((0, 5), (4.6, 5)),
            feature((5, 0), (5, 10)),
        ]
        found = collect_corrections(features, 1.0, fix_overshoots=False)
        self.assertEqual(len(found), 1)
        self.assertEqual(len(dedupe_corrections(found + found)), 1)
        self.assertTrue(is_review_mode("Review"))
        self.assertTrue(is_review_mode(" review "))
        self.assertFalse(is_review_mode("Automatic"))
        self.assertFalse(is_review_mode(None))


class ReviewScreenTests(unittest.TestCase):
    def test_screen_has_a_green_tick_and_a_red_cross(self):
        features = [
            feature((0, 5), (4.6, 5)),
            feature((5, 0), (5, 10)),
        ]
        correction = collect_corrections(features, 1.0, fix_overshoots=False)[0]
        png = render_review_png(correction, index=1, total=1, unit="m")
        pixels = _png_pixels(png)
        self.assertTrue(_has_color(pixels, (27, 138, 62)))
        self.assertTrue(_has_color(pixels, (209, 36, 47)))
        self.assertTrue(_has_color(pixels, (255, 255, 255)))


if __name__ == "__main__":
    unittest.main()
