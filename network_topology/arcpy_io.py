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
# Percent. The line underneath stays visible.
_HIGHLIGHT_TRANSPARENCY = 55


def _paint_layer(layer, rgb, width: float, transparency: float = _HIGHLIGHT_TRANSPARENCY) -> None:
    color = {"RGB": [int(rgb[0]), int(rgb[1]), int(rgb[2]), 255]}
    try:
        symbol = layer.symbology
        symbol.updateRenderer("SimpleRenderer")
        drawn = symbol.renderer.symbol
        try:
            drawn.color = color
        except Exception:
            drawn.color = {"RGB": color["RGB"][:3]}
        if hasattr(drawn, "width"):
            try:
                drawn.width = width
            except Exception:
                pass
        layer.symbology = symbol
        layer.transparency = transparency
        layer.visible = True
    except Exception:
        return


def _layer_line_width(layer) -> float:
    """Symbol width of the line layer being reviewed, in points."""
    if layer is None or isinstance(layer, str):
        return 1.0
    try:
        width = float(layer.symbology.renderer.symbol.width)
        if width > 0:
            return width
    except Exception:
        pass
    try:
        cim = layer.getDefinition("V3")
        for stroke in cim.renderer.symbol.symbol.symbolLayers:
            width = getattr(stroke, "width", None)
            if width and float(width) > 0:
                return float(width)
    except Exception:
        pass
    return 1.0


def _memory_layer(active_map, name: str, shape: str, spatial_ref, rgb, width: float):
    """Put a scratch feature class on the open map as a layer file.

    An in-memory dataset path is rejected as a web service. The scratch
    geodatabase is a dataset Pro can add.
    """
    import os

    import arcpy

    gdb = arcpy.env.scratchGDB
    if not gdb:
        raise RuntimeError("No scratch geodatabase is available for the highlight.")
    catalog = os.path.join(gdb, name)
    if arcpy.Exists(catalog):
        arcpy.management.Delete(catalog)
    if arcpy.Exists(name):
        arcpy.management.Delete(name)
    show_outputs = arcpy.env.addOutputsToMap
    arcpy.env.addOutputsToMap = False
    try:
        arcpy.management.CreateFeatureclass(
            gdb, name, shape, spatial_reference=spatial_ref
        )
        catalog = arcpy.Describe(os.path.join(gdb, name)).catalogPath
        arcpy.management.MakeFeatureLayer(catalog, name)
        folder = arcpy.env.scratchFolder or os.path.dirname(catalog)
        lyrx = os.path.join(folder, f"{name}.lyrx")
        if os.path.exists(lyrx):
            os.remove(lyrx)
        arcpy.management.SaveToLayerFile(name, lyrx, "ABSOLUTE")
        active_map.addLayer(arcpy.mp.LayerFile(lyrx), "TOP")
    finally:
        arcpy.env.addOutputsToMap = show_outputs
    layer = None
    for candidate in active_map.listLayers():
        if candidate.name == name:
            layer = candidate
            break
    if layer is None:
        raise RuntimeError(f"Highlight layer {name} was not added to the map.")
    _paint_layer(layer, rgb, width)
    return catalog, layer


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


def _polygon(points, spatial_ref):
    import arcpy

    array = arcpy.Array([arcpy.Point(point.x, point.y) for point in points])
    array.add(arcpy.Point(points[0].x, points[0].y))
    return arcpy.Polygon(array, spatial_ref)


def _select_source(state: dict, feature_index: int) -> None:
    """Select the feature on the layer already drawn in the map."""
    import arcpy

    layer = state.get("source")
    oids = state.get("oids") or []
    oid_field = state.get("oid_field")
    if layer is None or oid_field is None or feature_index >= len(oids):
        return
    oid = oids[feature_index]
    if oid is None:
        return
    delimited = arcpy.AddFieldDelimiters(layer, oid_field)
    arcpy.management.SelectLayerByAttribute(
        layer, "NEW_SELECTION", f"{delimited} = {int(oid)}"
    )


def _find_map_layer(active_map, in_features):
    """The layer already drawn in the map for this input."""
    import arcpy

    described = arcpy.Describe(in_features)
    data_type = str(getattr(described, "dataType", "") or "")
    catalog = str(getattr(described, "catalogPath", "") or "")
    name = str(getattr(described, "name", "") or "")
    matches = []
    if active_map is not None:
        for lyr in active_map.listLayers():
            try:
                if not lyr.isFeatureLayer:
                    continue
                lyr_desc = arcpy.Describe(lyr)
            except Exception:
                continue
            lyr_path = str(getattr(lyr_desc, "catalogPath", "") or "")
            same_data = bool(catalog) and lyr_path.lower() == catalog.lower()
            same_name = bool(name) and (lyr.name == name or lyr.longName == name)
            if same_data or (data_type == "FeatureLayer" and same_name):
                matches.append(lyr)
    if name:
        for lyr in matches:
            if lyr.name == name or lyr.longName == name:
                return lyr
    if matches:
        return matches[0]
    if data_type == "FeatureLayer":
        return in_features
    return None


def _emphasize_selection(layer, state: dict) -> None:
    """Draw the selected feature with a thick orange stroke."""
    if state.get("symbol_saved") or layer is None or isinstance(layer, str):
        return
    state["symbol_saved"] = True
    try:
        saved = layer.getDefinition("V3")
        cim = layer.getDefinition("V3")
        stroke = cim.selectionSymbol.symbol.symbolLayers[0]
        stroke.color.values = [232, 122, 26, 100]
        if hasattr(stroke, "width"):
            stroke.width = 12
        layer.setDefinition(cim)
        state["saved_definition"] = saved
    except Exception:
        state["saved_definition"] = None


def _ensure_highlight_layers(state: dict, spatial_ref, active_map) -> None:
    """Add the highlight layers. A failure here must not stop the review."""
    if state.get("ready"):
        return
    import arcpy

    state["ready"] = True
    state["map"] = active_map
    state.setdefault("layers", [])
    state.setdefault("paths", [])
    width = float(state.get("line_width") or 1.0)
    specs = (
        ("nt_review_dangle", "POLYLINE", _DANGLE_COLOR, "dangle_path", "dangle_layer"),
        ("nt_review_change", "POLYLINE", _EXTEND_COLOR, "change_path", "change_layer"),
    )
    for name, shape, color, path_key, layer_key in specs:
        try:
            path, layer = _memory_layer(active_map, name, shape, spatial_ref, color, width)
        except Exception as exc:
            arcpy.AddWarning(f"Could not draw {name} ({exc}). The feature is still selected.")
            continue
        state["layers"].append(layer)
        state["paths"].append(path)
        state[path_key] = path
        if layer_key:
            state[layer_key] = layer


def _zoom_to_correction(correction: Correction, spatial_ref, state: dict) -> None:
    """Move the open map onto this dangling end and color that end."""
    import arcpy

    view, active_map = _open_map_view()
    _ensure_highlight_layers(state, spatial_ref, active_map)
    pieces = review_highlight(correction)
    kind = "Undershoot" if correction.kind == "undershoot" else "Overshoot"
    change_color = _EXTEND_COLOR if correction.kind == "undershoot" else _TRIM_COLOR
    width = float(state.get("line_width") or 1.0)
    dangle_layer = state.get("dangle_layer")
    if dangle_layer is not None:
        _paint_layer(dangle_layer, _DANGLE_COLOR, width)
    change_layer = state.get("change_layer")
    if change_layer is not None:
        _paint_layer(change_layer, change_color, width)
        try:
            change_layer.name = "Extension" if correction.kind == "undershoot" else "Tail"
        except Exception:
            pass

    dangle_line = _line_if_separated(pieces["dangle"], spatial_ref)
    change_line = _line_if_separated(pieces["change"], spatial_ref)
    if state.get("dangle_path"):
        _replace_rows(state["dangle_path"], [dangle_line] if dangle_line is not None else [])
    if state.get("change_path"):
        _replace_rows(state["change_path"], [change_line] if change_line is not None else [])
    for layer in state.get("layers") or []:
        try:
            layer.visible = True
        except Exception:
            pass
    _select_source(state, correction.feature_index)

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

    source = state.get("source")
    saved = state.get("saved_definition")
    if saved is not None and source is not None and not isinstance(source, str):
        try:
            source.setDefinition(saved)
        except Exception:
            pass
    if source is not None:
        previous = state.get("previous_selection")
        try:
            if previous:
                arcpy.management.SelectLayerByAttribute(
                    source,
                    "NEW_SELECTION",
                    f"{arcpy.AddFieldDelimiters(source, state.get('oid_field'))} IN ({','.join(str(int(oid)) for oid in previous)})",
                )
            else:
                arcpy.management.SelectLayerByAttribute(source, "CLEAR_SELECTION")
        except Exception:
            pass
    active_map = state.get("map")
    for layer in state.get("layers") or []:
        if active_map is None:
            break
        try:
            active_map.removeLayer(layer)
        except Exception:
            pass
    created = state.get("created_source")
    if created is not None and active_map is not None:
        try:
            active_map.removeLayer(created)
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
    oid_field = arcpy.Describe(in_features).OIDFieldName
    rows = []
    features = []
    oids = []
    with arcpy.da.SearchCursor(in_features, [oid_field, "SHAPE@", *fields_probe]) as cursor:
        for row in cursor:
            oids.append(row[0])
            rows.append(row[2:])
            features.append({"attrs": None, "parts": geometry_to_parts(row[1], has_z, has_m)})

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
    corrections = collect_corrections(features, tolerance, keep_context=False, **kwargs)
    arcpy.AddMessage(f"{len(corrections)} topological error(s) within the tolerance.")

    accepted_flags: list[bool]
    if corrections:
        _view, active_map = _open_map_view()
        source_layer = _find_map_layer(active_map, in_features)
        created_source = None
        previous_selection = None
        try:
            if source_layer is None:
                made = arcpy.management.MakeFeatureLayer(in_features, "nt_review_source")
                created_source = arcpy.mp.Layer(made.getOutput(0))
                active_map.addLayer(created_source, "TOP")
                source_layer = created_source
            else:
                previous_selection = arcpy.Describe(source_layer).FIDSet
        except Exception:
            source_layer = in_features
        overlay: dict = {
            "source": source_layer,
            "oid_field": oid_field,
            "oids": oids,
            "previous_selection": [
                int(piece)
                for piece in str(previous_selection or "").replace(";", " ").split()
                if piece.lstrip("-").isdigit()
            ],
            "created_source": created_source,
            "line_width": _layer_line_width(source_layer),
        }

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
            cursor.insertRow([geometry, *row])
            written += 1

    arcpy.AddMessage(
        f"Accepted {sum(1 for flag in accepted_flags if flag)}, "
        f"rejected {rejected}. "
        f"{result.extended} ends extended, {result.trimmed} ends trimmed. "
        f"{written} features written."
    )
    return result
