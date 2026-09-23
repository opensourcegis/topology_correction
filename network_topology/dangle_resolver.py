# SPDX-License-Identifier: GPL-2.0-or-later
# Derived from Oksion/network-topology-qgis (GPL-2.0-or-later):
# https://github.com/Oksion/network-topology-qgis

"""Resolve dangling ends of a line network without splitting features.

* Undershoot — a free end that stops short of another line is extended along
  its own direction until it meets that line, when the gap is within tolerance.
* Overshoot — a free end that runs past a crossing is trimmed back to the
  crossing nearest that end, when the tail is shorter than tolerance.

One output feature is produced per input feature. The pass is single-pass:
every end is matched against the input network, not against edits made to
other ends in the same run. Geometry is planar 2D. Z and M are preserved on
existing vertices; an extended vertex copies Z/M from the free end, and a
trim interpolates Z/M.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Sequence

from network_topology.geographic import (
    WGS84_A,
    WGS84_B,
    LocalMeterFrame,
    continuous_longitudes,
    latitude_band,
    mean_xy,
    spans_many_latitudes,
)
from network_topology.geometry import (
    LineIndex,
    Point,
    data_eps,
    dist,
    extend_end,
    is_dangle,
    junction_distances,
    locate_along,
    polyline_length,
    sub_polyline,
)


@dataclass
class ResolveStats:
    extended: int = 0
    trimmed: int = 0


@dataclass
class ResolveResult:
    features: list[dict[str, Any]] = field(default_factory=list)
    extended: int = 0
    trimmed: int = 0


@dataclass
class Correction:
    """One dangling end the resolver can fix.

    ``kind`` is ``undershoot`` (extend) or ``overshoot`` (trim). ``before`` and
    ``after`` are that part in the layer's coordinates. ``gap`` is the ground
    distance that would be closed or cut, in the same units as ``tolerance``.
    """

    part_index: int
    at_start: bool
    kind: str
    gap: float
    before: list[Point]
    after: list[Point]
    context: list[list[Point]] = field(default_factory=list)
    feature_index: int = 0
    part_in_feature: int = 0

    @property
    def key(self) -> tuple[int, bool, str]:
        return (self.part_index, self.at_start, self.kind)


def _as_point(value: Any) -> Point:
    if isinstance(value, Point):
        return value
    if len(value) >= 4:
        return Point(value[0], value[1], value[2], value[3])
    if len(value) == 3:
        return Point(value[0], value[1], value[2])
    return Point(value[0], value[1])


def _normalize_features(features: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    normalized = []
    for feat in features:
        parts = []
        for part in feat.get("parts") or []:
            pts = [_as_point(p) for p in part]
            if len(pts) >= 2:
                parts.append(pts)
        normalized.append({"attrs": feat.get("attrs"), "parts": parts})
    return normalized


def resolve_dangles(
    features: Sequence[dict[str, Any]],
    tolerance: float,
    fix_undershoots: bool = True,
    fix_overshoots: bool = True,
    geographic: bool = False,
    latitude: float | None = None,
    semi_major: float = WGS84_A,
    semi_minor: float = WGS84_B,
    decide=None,
) -> ResolveResult:
    """Clean dangling ends. ``features`` items are ``{"parts", "attrs"}``.

    ``parts`` is a list of polylines. Each vertex is a :class:`Point` or an
    ``(x, y)`` pair. Attributes are copied onto the output unchanged. Input
    vertex lists are not modified.

    When ``geographic`` is true the coordinates are longitude/latitude in
    degrees (EPSG:4326 and other geographic CRSs) and ``tolerance`` is metres
    on the ground. Each dangling end is solved in a local metre frame at that
    end's latitude, so a degree of longitude counts for less ground distance
    toward the poles, then written back in degrees. ``latitude`` forces one
    frame for the whole dataset. Longitudes that cross the antimeridian are
    shifted by 360° so the gap stays the short     way around.

    ``decide`` is called with each :class:`Correction` the automatic pass would
    make. Return true to apply it and false to leave that end alone. Omit it
    to apply every correction.
    """
    if geographic:
        stored = _normalize_features(features)
        if (
            not any(part for feature in stored for part in feature["parts"])
            or tolerance <= 0
            or not (fix_undershoots or fix_overshoots)
        ):
            return ResolveResult(features=stored, extended=0, trimmed=0)
        continuous_longitudes(stored)
        if latitude is not None:
            origin_x, origin_y = mean_xy(stored)
            return _resolve_in_frame(
                stored,
                latitude,
                origin_x,
                origin_y,
                tolerance,
                fix_undershoots,
                fix_overshoots,
                semi_major,
                semi_minor,
                decide,
            )
        latitudes = [
            point.y
            for feature in stored
            for part in feature["parts"]
            for point in part
        ]
        if not spans_many_latitudes(latitudes):
            origin_x, origin_y = mean_xy(stored)
            return _resolve_in_frame(
                stored,
                sum(latitudes) / len(latitudes),
                origin_x,
                origin_y,
                tolerance,
                fix_undershoots,
                fix_overshoots,
                semi_major,
                semi_minor,
                decide,
            )
        return _resolve_banded(
            stored,
            tolerance,
            fix_undershoots,
            fix_overshoots,
            semi_major,
            semi_minor,
            decide,
        )
    return _resolve_planar(
        features, tolerance, fix_undershoots, fix_overshoots, decide
    )


def _resolve_in_frame(
    stored: list[dict[str, Any]],
    latitude: float,
    origin_x: float,
    origin_y: float,
    tolerance: float,
    fix_undershoots: bool,
    fix_overshoots: bool,
    semi_major: float,
    semi_minor: float,
    decide=None,
) -> ResolveResult:
    """Run the planar resolver in one equirectangular metre frame."""
    frame = LocalMeterFrame(latitude, origin_x, origin_y, semi_major, semi_minor)
    transformed = [
        {
            "attrs": feature["attrs"],
            "parts": [[frame.forward(point) for point in part] for part in feature["parts"]],
        }
        for feature in stored
    ]
    result = _resolve_planar(
        transformed,
        tolerance,
        fix_undershoots,
        fix_overshoots,
        _map_decide(decide, frame),
    )
    for feature in result.features:
        feature["parts"] = [
            [frame.inverse(point) for point in part] for part in feature["parts"]
        ]
    return result


def _copy_pts(pts: Sequence[Point]) -> list[Point]:
    return [Point(point.x, point.y, point.z, point.m) for point in pts]


def _context_lines(
    flat_pts: Sequence[Sequence[Point]],
    part_index: int,
    focus: Sequence[Point],
    pad: float,
) -> list[list[Point]]:
    xs = [point.x for point in focus]
    ys = [point.y for point in focus]
    if not xs:
        return []
    minx, maxx = min(xs) - pad, max(xs) + pad
    miny, maxy = min(ys) - pad, max(ys) + pad
    lines = []
    for index, part in enumerate(flat_pts):
        if index == part_index or len(part) < 2:
            continue
        part_xs = [point.x for point in part]
        part_ys = [point.y for point in part]
        if max(part_xs) < minx or min(part_xs) > maxx or max(part_ys) < miny or min(part_ys) > maxy:
            continue
        lines.append(_copy_pts(part))
    return lines


def _confirm(
    decide,
    part_index: int,
    at_start: bool,
    kind: str,
    gap: float,
    before: Sequence[Point],
    after: Sequence[Point],
    flat_pts: Sequence[Sequence[Point]],
    pad: float,
) -> bool:
    if decide is None:
        return True
    focus = list(before) + list(after)
    correction = Correction(
        part_index=part_index,
        at_start=at_start,
        kind=kind,
        gap=float(gap),
        before=_copy_pts(before),
        after=_copy_pts(after),
        context=_context_lines(flat_pts, part_index, focus, pad),
    )
    return bool(decide(correction))


def _tag_decide(decide, feature_index: int, part_in_feature: int):
    if decide is None:
        return None

    def wrapped(correction: Correction) -> bool:
        correction.feature_index = feature_index
        correction.part_in_feature = part_in_feature
        return bool(decide(correction))

    return wrapped


def _map_decide(decide, frame: LocalMeterFrame):
    """Present corrections in layer degrees while the edit ran in metres."""
    if decide is None:
        return None

    def wrapped(correction: Correction) -> bool:
        mapped = Correction(
            part_index=correction.part_index,
            at_start=correction.at_start,
            kind=correction.kind,
            gap=correction.gap,
            before=[frame.inverse(point) for point in correction.before],
            after=[frame.inverse(point) for point in correction.after],
            context=[
                [frame.inverse(point) for point in line] for line in correction.context
            ],
            feature_index=correction.feature_index,
            part_in_feature=correction.part_in_feature,
        )
        return bool(decide(mapped))

    return wrapped


def _resolve_part(
    part_index: int,
    pts: Sequence[Point],
    flat_pts: Sequence[Sequence[Point]],
    index: LineIndex,
    tolerance: float,
    eps: float,
    fix_undershoots: bool,
    fix_overshoots: bool,
    decide=None,
) -> tuple[list[Point], int, int]:
    """Extend and trim one part against the original network."""
    length = polyline_length(pts)
    trimmed_start = False
    trimmed_end = False
    d0 = 0.0
    d1 = length
    n_ext = 0
    n_trim = 0
    pad = max(tolerance * 3.0, eps * 10.0)

    if fix_overshoots:
        crossings = junction_distances(part_index, pts, flat_pts, index, eps)
        if is_dangle(pts[0], part_index, flat_pts, index, eps):
            candidates = [d for d in crossings if eps < d <= tolerance + eps]
            if candidates:
                d0 = min(candidates)
                trimmed_start = True
        if is_dangle(pts[-1], part_index, flat_pts, index, eps):
            lo = length - tolerance - eps
            candidates = [d for d in crossings if lo <= d < length - eps]
            if candidates:
                d1 = max(candidates)
                trimmed_end = True

    work: Sequence[Point] = pts
    if trimmed_start or trimmed_end:
        trimmed = sub_polyline(pts, d0, d1, eps)
        if len(trimmed) >= 2 and polyline_length(trimmed) > eps:
            if trimmed_start:
                start_only = sub_polyline(pts, d0, length, eps)
                if not _confirm(
                    decide, part_index, True, "overshoot", d0, pts, start_only, flat_pts, pad
                ):
                    trimmed_start = False
                    d0 = 0.0
            if trimmed_end:
                end_only = sub_polyline(pts, 0.0, d1, eps)
                tail = length - d1
                if not _confirm(
                    decide, part_index, False, "overshoot", tail, pts, end_only, flat_pts, pad
                ):
                    trimmed_end = False
                    d1 = length
            if trimmed_start or trimmed_end:
                work = sub_polyline(pts, d0, d1, eps)
                n_trim += int(trimmed_start) + int(trimmed_end)
            else:
                work = pts
        else:
            trimmed_start = False
            trimmed_end = False

    if fix_undershoots:
        if not trimmed_start:
            hit = extend_end(part_index, work, True, flat_pts, index, tolerance, eps)
            if hit is not None:
                proposed = [hit, *work]
                gap = dist(work[0], hit)
                if _confirm(
                    decide, part_index, True, "undershoot", gap, work, proposed, flat_pts, pad
                ):
                    work = proposed
                    n_ext += 1
        if not trimmed_end:
            hit = extend_end(part_index, work, False, flat_pts, index, tolerance, eps)
            if hit is not None:
                proposed = [*work, hit]
                gap = dist(work[-1], hit)
                if _confirm(
                    decide, part_index, False, "undershoot", gap, work, proposed, flat_pts, pad
                ):
                    work = proposed
                    n_ext += 1
    return list(work), n_ext, n_trim


def _resolve_banded(
    stored: list[dict[str, Any]],
    tolerance: float,
    fix_undershoots: bool,
    fix_overshoots: bool,
    semi_major: float,
    semi_minor: float,
    decide=None,
) -> ResolveResult:
    """Solve each end in a metre frame at that end's latitude.

    A single scale would stretch east-west gaps at high latitude when the
    layer also contains lines near the equator. Straight lines in degrees stay
    straight because each frame is a constant scale.
    """
    flat_pts: list[list[Point]] = []
    flat_ref: list[tuple[int, int]] = []
    for feature_index, feature in enumerate(stored):
        for part_index, part in enumerate(feature["parts"]):
            flat_pts.append(part)
            flat_ref.append((feature_index, part_index))

    degree_eps = data_eps(point for part in flat_pts for point in part)
    cache: dict[float, tuple[LocalMeterFrame, list[list[Point]], LineIndex, float]] = {}

    def view(lat: float):
        key = latitude_band(lat)
        cached = cache.get(key)
        if cached is not None:
            return cached
        frame = LocalMeterFrame(key, 0.0, 0.0, semi_major, semi_minor)
        lines = [[frame.forward(point) for point in part] for part in flat_pts]
        eps = data_eps(point for part in lines for point in part)
        index = LineIndex(lines, cell=max(tolerance, eps * 1e6, 1e-6))
        cached = (frame, lines, index, eps)
        cache[key] = cached
        return cached

    n_ext = 0
    n_trim = 0
    for part_index, pts in enumerate(flat_pts):
        start_band = latitude_band(pts[0].y)
        end_band = latitude_band(pts[-1].y)
        if start_band == end_band:
            frame, lines, index, eps = view(pts[0].y)
            feature_index, line_index = flat_ref[part_index]
            work, part_ext, part_trim = _resolve_part(
                part_index,
                lines[part_index],
                lines,
                index,
                tolerance,
                eps,
                fix_undershoots,
                fix_overshoots,
                _map_decide(_tag_decide(decide, feature_index, line_index), frame),
            )
            if len(work) >= 2 and polyline_length(work) > eps:
                edited = [frame.inverse(point) for point in work]
            else:
                edited = []
        else:
            feature_index, line_index = flat_ref[part_index]
            edited, part_ext, part_trim = _resolve_part_across_bands(
                part_index,
                pts,
                view,
                tolerance,
                degree_eps,
                fix_undershoots,
                fix_overshoots,
                _tag_decide(decide, feature_index, line_index),
                flat_pts,
            )
        n_ext += part_ext
        n_trim += part_trim
        feature_index, line_index = flat_ref[part_index]
        stored[feature_index]["parts"][line_index] = edited

    for feature in stored:
        feature["parts"] = [
            part
            for part in feature["parts"]
            if len(part) >= 2 and polyline_length(part) > degree_eps
        ]
    return ResolveResult(features=stored, extended=n_ext, trimmed=n_trim)


def _resolve_part_across_bands(
    part_index: int,
    pts: Sequence[Point],
    view,
    tolerance: float,
    degree_eps: float,
    fix_undershoots: bool,
    fix_overshoots: bool,
    decide=None,
    flat_pts: Sequence[Sequence[Point]] | None = None,
) -> tuple[list[Point], int, int]:
    """Trim and extend a part whose ends sit in different latitude bands."""
    trimmed_start = False
    trimmed_end = False
    d0 = 0.0
    d1 = polyline_length(pts)
    start_gap_m = 0.0
    end_gap_m = 0.0
    n_ext = 0
    n_trim = 0

    if fix_overshoots:
        for at_start in (True, False):
            frame, lines, index, eps = view(pts[0].y if at_start else pts[-1].y)
            metre_pts = lines[part_index]
            if not is_dangle(
                metre_pts[0] if at_start else metre_pts[-1],
                part_index,
                lines,
                index,
                eps,
            ):
                continue
            crossings = junction_distances(part_index, metre_pts, lines, index, eps)
            length_m = polyline_length(metre_pts)
            if at_start:
                candidates = [d for d in crossings if eps < d <= tolerance + eps]
                if not candidates:
                    continue
                piece = sub_polyline(metre_pts, min(candidates), length_m, eps)
                if len(piece) < 2:
                    continue
                along, offset = locate_along(pts, frame.inverse(piece[0]))
                if offset > max(degree_eps * 10.0, 1e-8):
                    continue
                d0 = along
                start_gap_m = min(candidates)
                trimmed_start = True
            else:
                lo = length_m - tolerance - eps
                candidates = [d for d in crossings if lo <= d < length_m - eps]
                if not candidates:
                    continue
                piece = sub_polyline(metre_pts, 0.0, max(candidates), eps)
                if len(piece) < 2:
                    continue
                along, offset = locate_along(pts, frame.inverse(piece[-1]))
                if offset > max(degree_eps * 10.0, 1e-8):
                    continue
                d1 = along
                end_gap_m = length_m - max(candidates)
                trimmed_end = True

    neighbors = flat_pts if flat_pts is not None else [list(pts)]
    pad = max(tolerance / 110000.0 * 5.0, degree_eps * 10.0)
    work: Sequence[Point] = pts
    if trimmed_start or trimmed_end:
        trimmed = sub_polyline(pts, d0, d1, degree_eps)
        if len(trimmed) >= 2 and polyline_length(trimmed) > degree_eps:
            if trimmed_start:
                start_only = sub_polyline(pts, d0, polyline_length(pts), degree_eps)
                if not _confirm(
                    decide,
                    part_index,
                    True,
                    "overshoot",
                    start_gap_m,
                    pts,
                    start_only,
                    neighbors,
                    pad,
                ):
                    trimmed_start = False
                    d0 = 0.0
            if trimmed_end:
                end_only = sub_polyline(pts, 0.0, d1, degree_eps)
                if not _confirm(
                    decide,
                    part_index,
                    False,
                    "overshoot",
                    end_gap_m,
                    pts,
                    end_only,
                    neighbors,
                    pad,
                ):
                    trimmed_end = False
                    d1 = polyline_length(pts)
            if trimmed_start or trimmed_end:
                work = sub_polyline(pts, d0, d1, degree_eps)
                n_trim += int(trimmed_start) + int(trimmed_end)
        else:
            trimmed_start = False
            trimmed_end = False

    if fix_undershoots:
        if not trimmed_start and len(work) >= 2:
            frame, lines, index, eps = view(work[0].y)
            metre_work = [frame.forward(point) for point in work]
            hit = extend_end(part_index, metre_work, True, lines, index, tolerance, eps)
            if hit is not None:
                proposed = [frame.inverse(hit), *work]
                if _confirm(
                    decide,
                    part_index,
                    True,
                    "undershoot",
                    dist(metre_work[0], hit),
                    work,
                    proposed,
                    neighbors,
                    pad,
                ):
                    work = proposed
                    n_ext += 1
        if not trimmed_end and len(work) >= 2:
            frame, lines, index, eps = view(work[-1].y)
            metre_work = [frame.forward(point) for point in work]
            hit = extend_end(part_index, metre_work, False, lines, index, tolerance, eps)
            if hit is not None:
                proposed = [*work, frame.inverse(hit)]
                if _confirm(
                    decide,
                    part_index,
                    False,
                    "undershoot",
                    dist(metre_work[-1], hit),
                    work,
                    proposed,
                    neighbors,
                    pad,
                ):
                    work = proposed
                    n_ext += 1

    if len(work) >= 2 and polyline_length(work) > degree_eps:
        return list(work), n_ext, n_trim
    return [], 0, 0


def _resolve_planar(
    features: Sequence[dict[str, Any]],
    tolerance: float,
    fix_undershoots: bool = True,
    fix_overshoots: bool = True,
    decide=None,
) -> ResolveResult:
    stored = _normalize_features(features)
    flat_pts: list[list[Point]] = []
    flat_ref: list[tuple[int, int]] = []
    for fi, feat in enumerate(stored):
        for li, pts in enumerate(feat["parts"]):
            flat_pts.append(pts)
            flat_ref.append((fi, li))

    if not flat_pts or tolerance <= 0 or not (fix_undershoots or fix_overshoots):
        return ResolveResult(features=stored, extended=0, trimmed=0)

    eps = data_eps(p for part in flat_pts for p in part)
    index = LineIndex(flat_pts, cell=max(tolerance, eps * 1e6, 1e-6))
    n_ext = 0
    n_trim = 0

    for i, pts in enumerate(flat_pts):
        feature_index, line_index = flat_ref[i]
        work, part_ext, part_trim = _resolve_part(
            i,
            pts,
            flat_pts,
            index,
            tolerance,
            eps,
            fix_undershoots,
            fix_overshoots,
            _tag_decide(decide, feature_index, line_index),
        )
        n_ext += part_ext
        n_trim += part_trim
        stored[feature_index]["parts"][line_index] = work

    for feat in stored:
        feat["parts"] = [
            part for part in feat["parts"] if len(part) >= 2 and polyline_length(part) > eps
        ]

    return ResolveResult(features=stored, extended=n_ext, trimmed=n_trim)
