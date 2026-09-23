# SPDX-License-Identifier: GPL-2.0-or-later

"""Convert an ArcGIS linear-unit string into the horizontal units of a dataset."""

from __future__ import annotations

from typing import Any

from network_topology.geographic import WGS84_A, WGS84_B, meters_per_degree

# Metres per one of the unit names ArcGIS uses on GPLinearUnit values.
_TO_METERS = {
    "meter": 1.0,
    "meters": 1.0,
    "metre": 1.0,
    "metres": 1.0,
    "kilometer": 1000.0,
    "kilometers": 1000.0,
    "centimeter": 0.01,
    "centimeters": 0.01,
    "millimeter": 0.001,
    "millimeters": 0.001,
    "decimeter": 0.1,
    "decimeters": 0.1,
    "foot": 0.3048,
    "feet": 0.3048,
    "foot_us": 1200.0 / 3937.0,
    "us survey foot": 1200.0 / 3937.0,
    "u.s. survey foot": 1200.0 / 3937.0,
    "yard": 0.9144,
    "yards": 0.9144,
    "mile": 1609.344,
    "miles": 1609.344,
    "nautical mile": 1852.0,
    "nautical miles": 1852.0,
    "inch": 0.0254,
    "inches": 0.0254,
    "point": 0.0254 / 72.0,
    "points": 0.0254 / 72.0,
}


def parse_linear_unit(text: str | None) -> tuple[float, str | None]:
    """Split ``"1.5 Meters"`` into ``(1.5, "Meters")``. A bare number has no unit."""
    if text is None:
        return 0.0, None
    raw = str(text).strip()
    if not raw:
        return 0.0, None
    pieces = raw.split()
    try:
        distance = float(pieces[0])
    except ValueError as exc:
        raise ValueError(f"Tolerance must be a number, got {text!r}.") from exc
    unit = " ".join(pieces[1:]) if len(pieces) > 1 else None
    return distance, unit


def is_geographic(spatial_ref: Any) -> bool:
    """True when the dataset stores longitude/latitude in degrees."""
    if spatial_ref is None:
        return False
    kind = str(getattr(spatial_ref, "type", "") or "").strip().lower()
    if kind == "projected":
        return False
    if kind == "geographic":
        return True
    unit_name = str(getattr(spatial_ref, "linearUnitName", "") or "").strip().lower()
    if "degree" in unit_name:
        return True
    if unit_name in _TO_METERS:
        return False
    angular = str(getattr(spatial_ref, "angularUnitName", "") or "").strip().lower()
    if "degree" in angular and not unit_name:
        return True
    code = getattr(spatial_ref, "factoryCode", None)
    try:
        code_int = int(code) if code not in (None, "") else None
    except (TypeError, ValueError):
        code_int = None
    return code_int in (4326, 4269, 4267, 4258)


def _to_meters(distance: float, unit: str | None) -> float | None:
    if unit is None:
        return None
    key = unit.strip().lower()
    if key in ("", "unknown"):
        return None
    if "degree" in key:
        return None
    factor = _TO_METERS.get(key)
    if factor is None:
        raise ValueError(f"Cannot convert tolerance unit {unit!r} to metres.")
    return distance * factor


def tolerance_ground_meters(
    text: str | None,
    latitude_deg: float,
    semi_major: float | None = None,
    semi_minor: float | None = None,
) -> float:
    """Ground metres for a tolerance on a degree-based dataset.

    ``1 Meters`` stays 1 metre. ``0.00001 Decimal Degrees`` is converted with
    the length of one degree of latitude at ``latitude_deg``, about 1.1 m.
    A bare number is treated as degrees, which is the layer unit of EPSG:4326.
    """
    distance, unit = parse_linear_unit(text)
    if distance < 0:
        raise ValueError("Tolerance must be greater than or equal to 0.")
    metres = _to_meters(distance, unit)
    if metres is not None:
        return metres
    _, metres_per_lat = meters_per_degree(
        latitude_deg,
        semi_major or WGS84_A,
        semi_minor or WGS84_B,
    )
    return distance * metres_per_lat


def tolerance_in_xy_units(text: str | None, spatial_ref: Any = None) -> float:
    """Return a tolerance expressed in the spatial reference's XY units.

    ``spatial_ref`` is an ArcPy spatial reference, or any object with
    ``linearUnitName`` and ``metersPerUnit``. When the unit already matches
    the dataset, the numeric value is returned unchanged.
    """
    distance, unit = parse_linear_unit(text)
    if distance < 0:
        raise ValueError("Tolerance must be greater than or equal to 0.")
    if unit is None or spatial_ref is None:
        return distance

    unit_key = unit.strip().lower()
    sr_name = str(getattr(spatial_ref, "linearUnitName", "") or "").strip().lower()
    if unit_key in ("", "unknown") or (sr_name and unit_key == sr_name):
        return distance

    meters = _TO_METERS.get(unit_key)
    if meters is None:
        if "degree" in unit_key and "degree" in sr_name:
            return distance
        raise ValueError(
            f"Cannot convert tolerance unit {unit!r} into the dataset units ({sr_name or 'unknown'})."
        )

    meters_per_unit = getattr(spatial_ref, "metersPerUnit", None) or 0.0
    if meters_per_unit <= 0:
        # A geographic CRS reports a tiny or empty metres-per-unit. The caller
        # passed a linear unit the dataset does not share; refuse the guess.
        raise ValueError(
            "The input is not in a projected coordinate system, so a metre tolerance "
            "cannot be converted. Enter the tolerance in the layer's units."
        )
    return (distance * meters) / meters_per_unit
