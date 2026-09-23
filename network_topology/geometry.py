# SPDX-License-Identifier: GPL-2.0-or-later
# Planar line geometry for the ArcGIS Pro port of Network Topology.
# Algorithm behavior follows Oksion/network-topology-qgis (GPL-2.0-or-later):
# https://github.com/Oksion/network-topology-qgis

"""Pure-Python planar geometry used by the dangle resolver.

The resolver only needs polylines: length, distance-along, segment intersection,
and a bbox index. Keeping this free of ArcPy means the same rules can be tested
without ArcGIS Pro.
"""

from __future__ import annotations

import math
from typing import Iterable, Sequence


class Point:
    """A 2D vertex. Z and M are carried through and are not used in the topology tests."""

    __slots__ = ("x", "y", "z", "m")

    def __init__(self, x: float, y: float, z: float | None = None, m: float | None = None):
        self.x = float(x)
        self.y = float(y)
        self.z = None if z is None else float(z)
        self.m = None if m is None else float(m)

    def __repr__(self) -> str:
        return f"Point({self.x}, {self.y})"


def dist(a: Point, b: Point) -> float:
    return math.hypot(a.x - b.x, a.y - b.y)


def polyline_length(pts: Sequence[Point]) -> float:
    return sum(dist(pts[i - 1], pts[i]) for i in range(1, len(pts)))


def data_eps(points: Iterable[Point]) -> float:
    """Planar epsilon scaled to the data extent, matching the QGIS plugin."""
    pts = list(points)
    if not pts:
        return 1e-9
    xs = [p.x for p in pts]
    ys = [p.y for p in pts]
    diag = math.hypot(max(xs) - min(xs), max(ys) - min(ys))
    return max(diag * 1e-9, 1e-9)


def _lerp(a: float | None, b: float | None, t: float) -> float | None:
    if a is None or b is None:
        return None
    return a + t * (b - a)


def _bbox(pts: Sequence[Point]) -> tuple[float, float, float, float]:
    xs = [p.x for p in pts]
    ys = [p.y for p in pts]
    return min(xs), min(ys), max(xs), max(ys)


def _boxes_overlap(
    a: tuple[float, float, float, float],
    b: tuple[float, float, float, float],
    pad: float,
) -> bool:
    return not (a[2] < b[0] - pad or b[2] < a[0] - pad or a[3] < b[1] - pad or b[3] < a[1] - pad)


class LineIndex:
    """Uniform grid over polyline bounding boxes.

    Lines that would occupy too many cells are kept in an overflow list and
    tested on every query, so a long feature cannot make the index wrong.
    """

    def __init__(self, lines: Sequence[Sequence[Point]], cell: float):
        self.lines = lines
        self.cell = cell if cell > 0 else 1.0
        self.boxes: list[tuple[float, float, float, float]] = []
        self.grid: dict[tuple[int, int], list[int]] = {}
        self.overflow: list[int] = []
        for i, pts in enumerate(lines):
            box = _bbox(pts)
            self.boxes.append(box)
            ix0, iy0, ix1, iy1 = self._cell_range(box)
            span = (ix1 - ix0 + 1) * (iy1 - iy0 + 1)
            if span > 64:
                self.overflow.append(i)
                continue
            for ix in range(ix0, ix1 + 1):
                for iy in range(iy0, iy1 + 1):
                    self.grid.setdefault((ix, iy), []).append(i)

    def _cell_range(
        self, box: tuple[float, float, float, float]
    ) -> tuple[int, int, int, int]:
        minx, miny, maxx, maxy = box
        return (
            math.floor(minx / self.cell),
            math.floor(miny / self.cell),
            math.floor(maxx / self.cell),
            math.floor(maxy / self.cell),
        )

    def query(self, box: tuple[float, float, float, float], pad: float = 0.0) -> list[int]:
        minx, miny, maxx, maxy = box
        if pad:
            minx -= pad
            miny -= pad
            maxx += pad
            maxy += pad
        ix0, iy0, ix1, iy1 = self._cell_range((minx, miny, maxx, maxy))
        if (ix1 - ix0 + 1) * (iy1 - iy0 + 1) > 4096:
            return list(range(len(self.lines)))
        found = set(self.overflow)
        for ix in range(ix0, ix1 + 1):
            for iy in range(iy0, iy1 + 1):
                bucket = self.grid.get((ix, iy))
                if bucket:
                    found.update(bucket)
        return list(found)


def point_segment_distance(p: Point, a: Point, b: Point) -> float:
    dx = b.x - a.x
    dy = b.y - a.y
    len2 = dx * dx + dy * dy
    if len2 == 0.0:
        return dist(p, a)
    t = ((p.x - a.x) * dx + (p.y - a.y) * dy) / len2
    if t < 0.0:
        t = 0.0
    elif t > 1.0:
        t = 1.0
    return math.hypot(p.x - (a.x + t * dx), p.y - (a.y + t * dy))


def point_polyline_distance(p: Point, pts: Sequence[Point]) -> float:
    if len(pts) == 1:
        return dist(p, pts[0])
    best = math.inf
    for i in range(1, len(pts)):
        best = min(best, point_segment_distance(p, pts[i - 1], pts[i]))
    return best


def locate_along(pts: Sequence[Point], p: Point) -> tuple[float, float]:
    """Return (distance along the polyline, perpendicular distance) of the closest point."""
    best_dd = None
    best_along = 0.0
    cum = 0.0
    for i in range(1, len(pts)):
        a = pts[i - 1]
        b = pts[i]
        seglen = dist(a, b)
        if seglen == 0.0:
            dd = dist(p, a)
            along = cum
        else:
            dx = b.x - a.x
            dy = b.y - a.y
            t = ((p.x - a.x) * dx + (p.y - a.y) * dy) / (seglen * seglen)
            if t < 0.0:
                t = 0.0
            elif t > 1.0:
                t = 1.0
            qx = a.x + t * dx
            qy = a.y + t * dy
            dd = math.hypot(p.x - qx, p.y - qy)
            along = cum + t * seglen
        if best_dd is None or dd < best_dd - 1e-15 or (
            abs(dd - best_dd) <= 1e-15 and along < best_along
        ):
            best_dd = dd
            best_along = along
        cum += seglen
    if best_dd is None:
        return 0.0, math.inf
    return best_along, best_dd


def _point_on_segment(p: Point, a: Point, b: Point, eps: float) -> bool:
    return point_segment_distance(p, a, b) <= eps


def _unique_points(pts: Sequence[Point], eps: float) -> list[Point]:
    out: list[Point] = []
    for p in pts:
        if all(dist(p, q) > eps for q in out):
            out.append(p)
    return out


def segment_intersections(p1: Point, p2: Point, p3: Point, p4: Point, eps: float) -> list[Point]:
    """Intersection points of segment p1-p2 with p3-p4.

    A proper crossing contributes one point. A collinear overlap contributes the
    two endpoints of the overlapping portion (the same choice the QGIS tool makes
    when it flattens a line intersection).
    """
    rx, ry = p2.x - p1.x, p2.y - p1.y
    sx, sy = p4.x - p3.x, p4.y - p3.y
    qpx, qpy = p3.x - p1.x, p3.y - p1.y
    denom = rx * sy - ry * sx
    scale = max(math.hypot(rx, ry), math.hypot(sx, sy), 1.0)
    if abs(denom) <= eps * scale:
        if abs(qpx * ry - qpy * rx) > eps * scale:
            return []
        hits = []
        for p in (p1, p2):
            if _point_on_segment(p, p3, p4, eps):
                hits.append(Point(p.x, p.y))
        for p in (p3, p4):
            if _point_on_segment(p, p1, p2, eps):
                hits.append(Point(p.x, p.y))
        return _unique_points(hits, eps)

    t = (qpx * sy - qpy * sx) / denom
    u = (qpx * ry - qpy * rx) / denom
    slack = eps / scale
    if -slack <= t <= 1.0 + slack and -slack <= u <= 1.0 + slack:
        t_clamped = min(1.0, max(0.0, t))
        return [Point(p1.x + t_clamped * rx, p1.y + t_clamped * ry)]
    return []


def polyline_intersections(a: Sequence[Point], b: Sequence[Point], eps: float) -> list[Point]:
    if len(a) < 2 or len(b) < 2:
        return []
    if not _boxes_overlap(_bbox(a), _bbox(b), eps):
        return []
    hits: list[Point] = []
    for i in range(1, len(a)):
        a0, a1 = a[i - 1], a[i]
        abox = (min(a0.x, a1.x), min(a0.y, a1.y), max(a0.x, a1.x), max(a0.y, a1.y))
        for j in range(1, len(b)):
            b0, b1 = b[j - 1], b[j]
            bbox = (min(b0.x, b1.x), min(b0.y, b1.y), max(b0.x, b1.x), max(b0.y, b1.y))
            if not _boxes_overlap(abox, bbox, eps):
                continue
            hits.extend(segment_intersections(a0, a1, b0, b1, eps))
    return _unique_points(hits, eps)


def is_dangle(
    end_pt: Point,
    part_index: int,
    lines: Sequence[Sequence[Point]],
    index: LineIndex,
    eps: float,
) -> bool:
    """True when an endpoint touches no other part."""
    probe = (end_pt.x, end_pt.y, end_pt.x, end_pt.y)
    for j in index.query(probe, pad=eps):
        if j == part_index:
            continue
        if point_polyline_distance(end_pt, lines[j]) <= eps:
            return False
    return True


def extend_end(
    part_index: int,
    pts: Sequence[Point],
    at_start: bool,
    lines: Sequence[Sequence[Point]],
    index: LineIndex,
    tolerance: float,
    eps: float,
) -> Point | None:
    """Extend a dangling end straight along its last segment.

    Returns the nearest point where that ray meets another part, when the gap
    is within ``tolerance``. Returns None when the end is already connected,
    the segment is degenerate, or nothing is hit.
    """
    if len(pts) < 2:
        return None
    if at_start:
        end, neighbor = pts[0], pts[1]
    else:
        end, neighbor = pts[-1], pts[-2]

    if not is_dangle(end, part_index, lines, index, eps):
        return None

    dx, dy = end.x - neighbor.x, end.y - neighbor.y
    norm = math.hypot(dx, dy)
    if norm <= eps:
        return None
    dx /= norm
    dy /= norm
    far = Point(end.x + dx * tolerance, end.y + dy * tolerance)
    ray = [end, far]
    ray_box = (min(end.x, far.x), min(end.y, far.y), max(end.x, far.x), max(end.y, far.y))

    best: tuple[float, Point] | None = None
    for j in index.query(ray_box, pad=eps):
        if j == part_index:
            continue
        for hit in polyline_intersections(ray, lines[j], eps):
            gap = dist(end, hit)
            if eps < gap <= tolerance + eps and (best is None or gap < best[0]):
                best = (gap, hit)
    if best is None:
        return None
    hit = best[1]
    return Point(hit.x, hit.y, end.z, end.m)


def _lines_near_segment(index: LineIndex, a: Point, b: Point, eps: float, part_index: int) -> set[int]:
    """Line ids whose cells meet this segment.

    A long segment is split so the lookup stays on the cells it crosses.
    Asking for the whole line at once pulls in every feature and stalls.
    """
    found: set[int] = set()
    span = math.hypot(b.x - a.x, b.y - a.y)
    cell = index.cell if index.cell > 0 else 1.0
    pieces = max(1, int(span / (cell * 32.0)) + 1)
    for step in range(pieces):
        t0 = step / pieces
        t1 = (step + 1) / pieces
        x0 = a.x + (b.x - a.x) * t0
        y0 = a.y + (b.y - a.y) * t0
        x1 = a.x + (b.x - a.x) * t1
        y1 = a.y + (b.y - a.y) * t1
        box = (min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1))
        for line_index in index.query(box, pad=eps):
            if line_index != part_index:
                found.add(line_index)
    return found


def junction_distances(
    part_index: int,
    pts: Sequence[Point],
    lines: Sequence[Sequence[Point]],
    index: LineIndex,
    eps: float,
) -> list[float]:
    """Distances along ``pts`` where this part crosses or touches another part."""
    length = polyline_length(pts)
    neighbors: set[int] = set()
    for index_vertex in range(1, len(pts)):
        neighbors.update(
            _lines_near_segment(index, pts[index_vertex - 1], pts[index_vertex], eps, part_index)
        )
    out: list[float] = []
    for j in neighbors:
        for hit in polyline_intersections(pts, lines[j], eps):
            along, offset = locate_along(pts, hit)
            if offset > eps:
                continue
            if 0.0 <= along <= length:
                out.append(along)
    return out


def sub_polyline(vertices: Sequence[Point], d0: float, d1: float, eps: float) -> list[Point]:
    """Polyline between distances d0 and d1, keeping interior vertices."""
    if len(vertices) < 2:
        return []
    cum = [0.0]
    for i in range(1, len(vertices)):
        cum.append(cum[-1] + dist(vertices[i - 1], vertices[i]))
    total = cum[-1]
    d0 = max(0.0, min(d0, total))
    d1 = max(0.0, min(d1, total))
    if d1 - d0 <= eps:
        return []

    def point_at(d: float) -> Point:
        if d <= 0.0:
            src = vertices[0]
            return Point(src.x, src.y, src.z, src.m)
        if d >= total:
            src = vertices[-1]
            return Point(src.x, src.y, src.z, src.m)
        for k in range(1, len(vertices)):
            if cum[k] >= d:
                seg = cum[k] - cum[k - 1]
                t = (d - cum[k - 1]) / seg if seg > 0 else 0.0
                a, b = vertices[k - 1], vertices[k]
                return Point(
                    a.x + t * (b.x - a.x),
                    a.y + t * (b.y - a.y),
                    _lerp(a.z, b.z, t),
                    _lerp(a.m, b.m, t),
                )
        src = vertices[-1]
        return Point(src.x, src.y, src.z, src.m)

    result = [point_at(d0)]
    for k in range(len(vertices)):
        if d0 + eps < cum[k] < d1 - eps:
            src = vertices[k]
            result.append(Point(src.x, src.y, src.z, src.m))
    result.append(point_at(d1))

    cleaned = [result[0]]
    for p in result[1:]:
        if dist(p, cleaned[-1]) > eps:
            cleaned.append(p)
    return cleaned if len(cleaned) >= 2 else []
