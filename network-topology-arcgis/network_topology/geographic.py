# SPDX-License-Identifier: GPL-2.0-or-later

"""Local metre frame for geographic coordinates (EPSG:4326 and other degree CRSs).

Longitude and latitude are not equal ground distances, and that ratio changes
with latitude. Dangle gaps are a few metres, so the resolver works in a
tangent-plane frame at the data's latitude and writes the edited vertices
back out in degrees.
"""

from __future__ import annotations

import math
from typing import Any, Sequence

from network_topology.geometry import Point

WGS84_A = 6378137.0
WGS84_F = 1.0 / 298.257223563
WGS84_B = WGS84_A * (1.0 - WGS84_F)


def meters_per_degree(
    latitude_deg: float,
    semi_major: float = WGS84_A,
    semi_minor: float = WGS84_B,
) -> tuple[float, float]:
    """Return ``(metres per degree longitude, metres per degree latitude)``."""
    latitude = max(-89.0, min(89.0, latitude_deg))
    phi = math.radians(latitude)
    semi_major = semi_major if semi_major and semi_major > 0 else WGS84_A
    semi_minor = semi_minor if semi_minor and semi_minor > 0 else WGS84_B
    eccentricity_sq = (semi_major * semi_major - semi_minor * semi_minor) / (
        semi_major * semi_major
    )
    sin_phi = math.sin(phi)
    cos_phi = math.cos(phi)
    one_minus = 1.0 - eccentricity_sq * sin_phi * sin_phi
    metres_per_rad_lon = semi_major * cos_phi / math.sqrt(one_minus)
    metres_per_rad_lat = semi_major * (1.0 - eccentricity_sq) / (one_minus ** 1.5)
    to_deg = math.pi / 180.0
    return metres_per_rad_lon * to_deg, metres_per_rad_lat * to_deg


class LocalMeterFrame:
    """Equirectangular metres centred on a longitude/latitude origin."""

    def __init__(
        self,
        latitude_deg: float,
        origin_x: float,
        origin_y: float,
        semi_major: float = WGS84_A,
        semi_minor: float = WGS84_B,
    ):
        lon_scale, lat_scale = meters_per_degree(latitude_deg, semi_major, semi_minor)
        self.lon_scale = lon_scale if lon_scale >= 1.0 else 1.0
        self.lat_scale = lat_scale if lat_scale >= 1.0 else 1.0
        self.origin_x = origin_x
        self.origin_y = origin_y

    def forward(self, point: Point) -> Point:
        return Point(
            (point.x - self.origin_x) * self.lon_scale,
            (point.y - self.origin_y) * self.lat_scale,
            point.z,
            point.m,
        )

    def inverse(self, point: Point) -> Point:
        return Point(
            self.origin_x + point.x / self.lon_scale,
            self.origin_y + point.y / self.lat_scale,
            point.z,
            point.m,
        )


def _coord_y(value: Any) -> float:
    if isinstance(value, Point):
        return value.y
    return float(value[1])


def _coord_x(value: Any) -> float:
    if isinstance(value, Point):
        return value.x
    return float(value[0])


def mean_latitude(features: Sequence[dict[str, Any]]) -> float:
    total = 0.0
    count = 0
    for feature in features:
        for part in feature.get("parts") or []:
            for vertex in part:
                total += _coord_y(vertex)
                count += 1
    if count == 0:
        return 0.0
    return total / count


def mean_xy(features: Sequence[dict[str, Any]]) -> tuple[float, float]:
    total_x = 0.0
    total_y = 0.0
    count = 0
    for feature in features:
        for part in feature.get("parts") or []:
            for vertex in part:
                total_x += _coord_x(vertex)
                total_y += _coord_y(vertex)
                count += 1
    if count == 0:
        return 0.0, 0.0
    return total_x / count, total_y / count


def latitude_band(latitude_deg: float) -> float:
    """Snap a latitude to a band whose east-west scale error stays under 0.2%.

    A degree of longitude shrinks with cos(latitude). One scale for a whole
    dataset is wrong when the lines span a wide range of latitudes, so each
    band keeps its own metres-per-degree.
    """
    latitude = max(-89.0, min(89.0, latitude_deg))
    tangent = abs(math.tan(math.radians(latitude)))
    step = math.degrees(0.002 / max(tangent, 0.05))
    step = min(max(step, 0.02), 2.0)
    return round(latitude / step) * step


def spans_many_latitudes(latitudes: Sequence[float]) -> bool:
    """True when one metres-per-degree value would distort ground distances."""
    if len(latitudes) < 2:
        return False
    low = min(latitudes)
    high = max(latitudes)
    middle = max(-89.0, min(89.0, (low + high) / 2.0))
    half_span = math.radians((high - low) / 2.0)
    tangent = abs(math.tan(math.radians(middle)))
    return half_span * max(tangent, 0.05) > 0.002


def continuous_longitudes(features: Sequence[dict[str, Any]]) -> bool:
    """Shift longitudes by multiples of 360° so a dateline crossing stays short.

    Vertices that do not cross the antimeridian are left unchanged. A vertex
    just west of -180° becomes just east of 180° when the rest of the lines
    sit there, so a 1 m gap is a 1 m gap instead of a 360° jump.
    """
    points: list[Point] = []
    for feature in features:
        for part in feature.get("parts") or []:
            for vertex in part:
                if isinstance(vertex, Point):
                    points.append(vertex)
    if len(points) < 2:
        return False
    values = sorted(point.x % 360.0 for point in points)
    max_gap = (values[0] + 360.0) - values[-1]
    cluster_start = values[0]
    for index in range(len(values) - 1):
        gap = values[index + 1] - values[index]
        if gap > max_gap:
            max_gap = gap
            cluster_start = values[index + 1]
    # Data spread around the whole globe has no single seam to open.
    if max_gap < 1.0:
        return False
    changed = False
    for point in points:
        shifted = cluster_start + (point.x - cluster_start) % 360.0
        turns = round((shifted - point.x) / 360.0)
        if turns == 0:
            continue
        point.x = point.x + turns * 360.0
        changed = True
    return changed


def ellipsoid_axes(spatial_ref: Any) -> tuple[float, float]:
    """Semi-major and semi-minor axes in metres, defaulting to WGS 84."""
    major = getattr(spatial_ref, "semiMajorAxis", None) if spatial_ref is not None else None
    minor = getattr(spatial_ref, "semiMinorAxis", None) if spatial_ref is not None else None
    try:
        major_m = float(major) if major else WGS84_A
    except (TypeError, ValueError):
        major_m = WGS84_A
    try:
        minor_m = float(minor) if minor else WGS84_B
    except (TypeError, ValueError):
        minor_m = WGS84_B
    if major_m <= 0 or minor_m <= 0:
        return WGS84_A, WGS84_B
    return major_m, minor_m
