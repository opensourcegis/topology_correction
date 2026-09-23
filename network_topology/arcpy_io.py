# SPDX-License-Identifier: GPL-2.0-or-later

"""Read and write polyline feature classes for the Resolve dangles tool.

Imported only when the toolbox runs inside ArcGIS Pro.
"""

from __future__ import annotations

from network_topology.dangle_resolver import (
    Correction,
    ResolveResult,
    parallel_thread_count,
    resolve_dangles,
    use_parallel,
)
from network_topology.review_ui import (
    apply_decisions,
    collect_corrections,
    review_corrections,
    review_highlight,
    review_zoom_extent,
)
from network_topology.geographic import ellipsoid_axes, mean_latitude
from network_topology.geometry import Point
from network_topology.units import is_geographic, tolerance_ground_meters, tolerance_in_xy_units


def _coord(value):
    if value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number != number:  # NaN
        return None
    return number


def geometry_to_parts(geom, has_z: bool, has_m: bool) -> list[list[Point]]:
    """Explode an ArcPy polyline into single-part vertex lists."""
    if geom is None:
        return []
    try:
        if int(geom.pointCount or 0) == 0:
            return []
    except Exception:
        return []

    parts: list[list[Point]] = []
    part_count = int(geom.partCount or 0)
    for i in range(part_count):
        part = geom.getPart(i)
        pts: list[Point] = []
        for vertex in part:
            if vertex is None:
                continue
            pts.append(
                Point(
                    vertex.X,
                    vertex.Y,
                    _coord(vertex.Z) if has_z else None,
                    _coord(vertex.M) if has_m else None,
                )
            )
        if len(pts) >= 2:
            parts.append(pts)
    return parts


def points_to_polyline(parts: list[list[Point]], spatial_ref, has_z: bool, has_m: bool):
    import arcpy

    def array_for(part: list[Point]):
        array = arcpy.Array()
        for vertex in part:
            point = arcpy.Point(vertex.x, vertex.y)
            if has_z:
                point.Z = 0.0 if vertex.z is None else vertex.z
            if has_m:
                point.M = 0.0 if vertex.m is None else vertex.m
            array.add(point)
        return array

    built = [array_for(part) for part in parts]
    if len(built) == 1:
        return arcpy.Polyline(built[0], spatial_ref, has_z, has_m)
    return arcpy.Polyline(arcpy.Array(built), spatial_ref, has_z, has_m)


def _output_workspace_and_name(out_features: str) -> tuple[str, str]:
    import os

    text = out_features.replace("/", os.sep)
    workspace, name = os.path.split(text)
    if not workspace:
        import arcpy

        workspace = arcpy.env.workspace or ""
    return workspace, name


def _copyable_fields(in_features: str, out_features: str) -> list[str]:
    import arcpy

    source = {f.name: f for f in arcpy.ListFields(in_features)}
    skipped_types = {"OID", "Geometry", "Blob", "Raster"}
    names = []
    for field in arcpy.ListFields(out_features):
        src = source.get(field.name)
        if src is None or src.type in skipped_types or not src.editable:
            continue
        names.append(field.name)
    return names


def execute_resolve_dangles(
    in_features: str,
    out_features: str,
    tolerance_text: str | None,
    fix_undershoots: bool,
    fix_overshoots: bool,
) -> ResolveResult:
    """Create ``out_features`` with dangling ends extended and/or trimmed."""
    import arcpy

    desc = arcpy.Describe(in_features)
    shape = getattr(desc, "shapeType", None)
    if shape != "Polyline":
        raise arcpy.ExecuteError("Input must be a polyline feature layer.")

    spatial_ref = desc.spatialReference
    has_z = bool(getattr(desc, "hasZ", False))
    has_m = bool(getattr(desc, "hasM", False))
    geographic = is_geographic(spatial_ref)

    if arcpy.env.overwriteOutput and arcpy.Exists(out_features):
        arcpy.management.Delete(out_features)

    workspace, name = _output_workspace_and_name(out_features)
    arcpy.management.CreateFeatureclass(
        workspace,
        name,
        "POLYLINE",
        template=in_features,
        has_z="ENABLED" if has_z else "DISABLED",
        has_m="ENABLED" if has_m else "DISABLED",
        spatial_reference=spatial_ref,
    )

    fields = _copyable_fields(in_features, out_features)
    rows = []
    features = []
    with arcpy.da.SearchCursor(in_features, ["SHAPE@", *fields]) as cursor:
        for row in cursor:
            rows.append(row)
            features.append({"attrs": None, "parts": geometry_to_parts(row[0], has_z, has_m)})

    if geographic:
        latitudes = [
            vertex.y
            for feature in features
            for part in feature["parts"]
            for vertex in part
        ]
        latitude = mean_latitude(features)
        semi_major, semi_minor = ellipsoid_axes(spatial_ref)
        tolerance = tolerance_ground_meters(tolerance_text, latitude, semi_major, semi_minor)
        if latitudes and max(latitudes) - min(latitudes) > 0.25:
            arcpy.AddMessage(
                "Geographic coordinates (degrees). "
                f"Tolerance is {tolerance:.4f} m on the ground. "
                "Each dangling end is measured at its own latitude "
                f"({min(latitudes):.5f}° to {max(latitudes):.5f}°)."
            )
        else:
            arcpy.AddMessage(
                "Geographic coordinates (degrees). "
                f"Tolerance is {tolerance:.4f} m on the ground at latitude {latitude:.5f}°."
            )
    else:
        semi_major = semi_minor = None
        tolerance = tolerance_in_xy_units(tolerance_text, spatial_ref)

    _report_parallel(features)
    arcpy.SetProgressor("default", "Resolving dangles...")
    if geographic:
        result = resolve_dangles(
            features,
            tolerance,
            fix_undershoots=fix_undershoots,
            fix_overshoots=fix_overshoots,
            geographic=True,
            semi_major=semi_major,
            semi_minor=semi_minor,
        )
    else:
        result = resolve_dangles(
            features,
            tolerance,
            fix_undershoots=fix_undershoots,
            fix_overshoots=fix_overshoots,
        )

    written = 0
    with arcpy.da.InsertCursor(out_features, ["SHAPE@", *fields]) as cursor:
        for row, feature in zip(rows, result.features):
            parts = feature["parts"]
            if len(parts) < 1:
                continue
            geom = points_to_polyline(parts, spatial_ref, has_z, has_m)
            cursor.insertRow([geom, *row[1:]])
            written += 1

    arcpy.AddMessage(
        f"Done: {result.extended} ends extended, {result.trimmed} ends trimmed. "
        f"{written} features written."
    )
    arcpy.ResetProgressor()
    return result


def _report_parallel(features) -> None:
    import arcpy

    parts = [part for feature in features for part in feature["parts"]]
    threads = parallel_thread_count()
    if threads > 1 and use_parallel(parts):
        arcpy.AddMessage(
            f"Large input ({len(features)} features). Resolving on {threads} threads."
        )


def _resolve_loaded(
    features,
    tolerance,
    geographic,
    semi_major,
    semi_minor,
    fix_undershoots,
    fix_overshoots,
    decide=None,
) -> ResolveResult:
    if geographic:
        return resolve_dangles(
            features,
            tolerance,
            fix_undershoots=fix_undershoots,
            fix_overshoots=fix_overshoots,
            geographic=True,
            semi_major=semi_major,
            semi_minor=semi_minor,
            decide=decide,
        )
    return resolve_dangles(
        features,
        tolerance,
        fix_undershoots=fix_undershoots,
        fix_overshoots=fix_overshoots,
        decide=decide,
    )


# Orange marks the end being reviewed. Green is an extension, red is a tail.
_DANGLE_COLOR = (232, 122, 26, 255)
_EXTEND_COLOR = (27, 138, 62, 255)
_TRIM_COLOR = (209, 36, 47, 255)


def _paint_layer(layer, rgb, width: float) -> None:
    try:
        symbol = layer.symbology
        symbol.updateRenderer("SimpleRenderer")
        drawn = symbol.renderer.symbol
        drawn.color = {"RGB": list(rgb)}
        for name in ("size", "width"):
            if hasattr(drawn, name):
                try:
                    setattr(drawn, name, width)
                except Exception:
                    pass
        layer.symbology = symbol
    except Exception:
        return


def _memory_layer(active_map, name: str, shape: str, spatial_ref, rgb, width: float):
    import arcpy

    path = rf"memory\{name}"
    if arcpy.Exists(path):
        arcpy.management.Delete(path)
    arcpy.management.CreateFeatureclass(
        "memory", name, shape, spatial_reference=spatial_ref
    )
    layer = active_map.addDataFromPath(path)
    try:
        layer.name = name.replace("nt_review_", "").replace("_", " ").title()
    except Exception:
        pass
    _paint_layer(layer, rgb, width)
    return path, layer


def _replace_rows(path: str, rows: list) -> None:
    import arcpy

    with arcpy.da.UpdateCursor(path, "OID@") as cursor:
        for row in cursor:
            cursor.deleteRow()
    if not rows:
        return
    with arcpy.da.InsertCursor(path, ["SHAPE@"]) as cursor:
        for geometry in rows:
            cursor.insertRow([geometry])


def _open_map_view():
    """The map the tool is running in. Review pans this view itself."""
    import arcpy

    try:
        project = arcpy.mp.ArcGISProject("CURRENT")
    except Exception as exc:
        raise arcpy.ExecuteError(
            "Review moves the open map. Run the tool inside ArcGIS Pro with a map active."
        ) from exc
    view = project.activeView
    active_map = project.activeMap
    if view is None or active_map is None or not hasattr(view, "camera"):
        raise arcpy.ExecuteError(
            "Review moves the open map. Activate a map view, then run the tool."
        )
    return view, active_map


def _move_map_to_extent(view, extent) -> None:
    """Zoom the open map so this extent fills the view."""
    import arcpy

    try:
        camera = view.camera
        camera.setExtent(extent)
        view.camera = camera
    except Exception as exc:
        raise arcpy.ExecuteError(
            f"Could not zoom the map to this dangling end ({exc})."
        ) from exc


def _line_if_separated(points, spatial_ref):
    if len(points) < 2:
        return None
    if points[0].x == points[1].x and points[0].y == points[1].y:
        return None
    return points_to_polyline([points], spatial_ref, False, False)


def _ensure_highlight_layers(state: dict, spatial_ref, active_map) -> None:
    if state.get("ready"):
        return
    dangle_path, dangle_layer = _memory_layer(
        active_map, "nt_review_dangle", "POLYLINE", spatial_ref, _DANGLE_COLOR, 5
    )
    change_path, change_layer = _memory_layer(
        active_map, "nt_review_change", "POLYLINE", spatial_ref, _EXTEND_COLOR, 5
    )
    end_path, end_layer = _memory_layer(
        active_map, "nt_review_end", "POINT", spatial_ref, _DANGLE_COLOR, 14
    )
    state.update(
        {
            "ready": True,
            "map": active_map,
            "layers": [dangle_layer, change_layer, end_layer],
            "paths": [dangle_path, change_path, end_path],
            "dangle_path": dangle_path,
            "change_path": change_path,
            "change_layer": change_layer,
            "end_path": end_path,
        }
    )


def _zoom_to_correction(correction: Correction, spatial_ref, state: dict) -> None:
    """Move the open map onto this dangling end and color that end."""
    import arcpy

    view, active_map = _open_map_view()
    _ensure_highlight_layers(state, spatial_ref, active_map)
    pieces = review_highlight(correction)
    kind = "Undershoot" if correction.kind == "undershoot" else "Overshoot"
    change_color = _EXTEND_COLOR if correction.kind == "undershoot" else _TRIM_COLOR
    _paint_layer(state["change_layer"], change_color, 5)
    try:
        state["change_layer"].name = "Extension" if correction.kind == "undershoot" else "Tail"
    except Exception:
        pass

    dangle = _line_if_separated(pieces["dangle"], spatial_ref)
    change = _line_if_separated(pieces["change"], spatial_ref)
    _replace_rows(state["dangle_path"], [dangle] if dangle is not None else [])
    _replace_rows(state["change_path"], [change] if change is not None else [])
    anchor = pieces["anchor"]
    point = arcpy.PointGeometry(
        arcpy.Point(anchor.x, anchor.y), spatial_ref
    )
    _replace_rows(state["end_path"], [point])

    end = "start" if correction.at_start else "end"
    arcpy.AddMessage(
        f"{kind}, feature {correction.feature_index + 1}, {end}. "
        f"Enter accepts, Space rejects."
    )

    minx, miny, maxx, maxy = review_zoom_extent(correction)
    extent = arcpy.Extent(minx, miny, maxx, maxy, spatial_reference=spatial_ref)
    _move_map_to_extent(view, extent)


def _clear_review_overlay(state: dict) -> None:
    import arcpy

    active_map = state.get("map")
    for layer in state.get("layers") or []:
        if active_map is None:
            break
        try:
            active_map.removeLayer(layer)
        except Exception:
            pass
    for path in state.get("paths") or []:
        if path and arcpy.Exists(path):
            try:
                arcpy.management.Delete(path)
            except Exception:
                pass


def execute_review_dangles(
    in_features: str,
    out_features: str,
    tolerance_text: str | None,
    fix_undershoots: bool,
    fix_overshoots: bool,
) -> ResolveResult:
    """Zoom to each dangling end. Enter accepts it. Space leaves it unchanged."""
    import arcpy

    desc = arcpy.Describe(in_features)
    if getattr(desc, "shapeType", None) != "Polyline":
        raise arcpy.ExecuteError("Input must be a polyline feature layer.")

    spatial_ref = desc.spatialReference
    has_z = bool(getattr(desc, "hasZ", False))
    has_m = bool(getattr(desc, "hasM", False))
    geographic = is_geographic(spatial_ref)

    fields_probe = _copyable_fields(in_features, in_features)
    rows = []
    features = []
    with arcpy.da.SearchCursor(in_features, ["SHAPE@", *fields_probe]) as cursor:
        for row in cursor:
            rows.append(row)
            features.append({"attrs": None, "parts": geometry_to_parts(row[0], has_z, has_m)})

    if geographic:
        latitude = mean_latitude(features)
        semi_major, semi_minor = ellipsoid_axes(spatial_ref)
        tolerance = tolerance_ground_meters(tolerance_text, latitude, semi_major, semi_minor)
        unit = "m"
        arcpy.AddMessage(
            "Geographic coordinates (degrees). "
            f"Tolerance is {tolerance:.4f} m on the ground."
        )
    else:
        semi_major = semi_minor = None
        tolerance = tolerance_in_xy_units(tolerance_text, spatial_ref)
        unit = str(getattr(spatial_ref, "linearUnitName", "") or "")

    kwargs = dict(
        geographic=geographic,
        semi_major=semi_major,
        semi_minor=semi_minor,
        fix_undershoots=fix_undershoots,
        fix_overshoots=fix_overshoots,
    )
    _report_parallel(features)
    corrections = collect_corrections(features, tolerance, **kwargs)
    arcpy.AddMessage(f"{len(corrections)} topological error(s) within the tolerance.")

    accepted_flags: list[bool]
    if corrections:
        overlay: dict = {}

        def on_show(correction: Correction):
            return _zoom_to_correction(correction, spatial_ref, overlay)

        try:
            decisions = review_corrections(corrections, on_show=on_show, unit=unit)
        finally:
            _clear_review_overlay(overlay)
        if decisions is None:
            raise arcpy.ExecuteError("Review cancelled. No output was written.")
        accepted_flags = decisions
    else:
        accepted_flags = []

    result = apply_decisions(features, tolerance, corrections, accepted_flags, **kwargs)
    rejected = len(corrections) - sum(1 for flag in accepted_flags if flag)

    if arcpy.env.overwriteOutput and arcpy.Exists(out_features):
        arcpy.management.Delete(out_features)
    workspace, name = _output_workspace_and_name(out_features)
    arcpy.management.CreateFeatureclass(
        workspace,
        name,
        "POLYLINE",
        template=in_features,
        has_z="ENABLED" if has_z else "DISABLED",
        has_m="ENABLED" if has_m else "DISABLED",
        spatial_reference=spatial_ref,
    )
    written = 0
    with arcpy.da.InsertCursor(out_features, ["SHAPE@", *fields_probe]) as cursor:
        for row, feature in zip(rows, result.features):
            parts = feature["parts"]
            if len(parts) < 1:
                continue
            geometry = points_to_polyline(parts, spatial_ref, has_z, has_m)
            cursor.insertRow([geometry, *row[1:]])
            written += 1

    arcpy.AddMessage(
        f"Accepted {sum(1 for flag in accepted_flags if flag)}, "
        f"rejected {rejected}. "
        f"{result.extended} ends extended, {result.trimmed} ends trimmed. "
        f"{written} features written."
    )
    return result
