# Network Topology for ArcGIS Pro

ArcGIS toolbox (`NetworkTopology.atbx`) that recreates **Resolve dangles** from the QGIS plugin
[Oksion/network-topology-qgis](https://github.com/Oksion/network-topology-qgis)
(`network_topology:resolvedangles`).

It cleans dangling ends of a line network (roads, rivers, pipelines, railways)
and writes **one output feature per input feature**. Attributes stay on the
feature. Lines are not split.

## Undershoot and overshoot

Both fixes share one tolerance. On a projected layer it is in the layer's
linear unit. On a geographic layer (EPSG:4326 and other degree coordinates)
it is metres on the ground. Each fix can be turned off.

**Undershoot → extend.** A free end that stops short of another line is
extended *along its own last segment* until the ray hits that line. The gap
must be within the tolerance. The end is not pulled sideways onto a nearby
line.

```
target                         target
  |                              |
  |                              |
--+   gap <= tolerance      -----+----   the free end moves to the hit
  |
```

**Overshoot → trim.** A free end that runs past a crossing, leaving a tail
shorter than the tolerance, is cut back to the crossing nearest that end.
A longer tail is left alone.

```
  |
  | tail <= tolerance
--+---->
  |
```

An end that already touches another line is connected. It is not extended
and not trimmed.

The pass is single-pass: every end is compared with the **input** geometry,
not with edits made to other ends in the same run. Z and M are kept on
existing vertices. An extended vertex copies Z/M from the free end. A trim
interpolates Z/M.

On **EPSG:4326** the vertex values are degrees, and a degree of longitude is
shorter on the ground than a degree of latitude once you leave the equator.
The tool measures each dangling end in metres at that end's own latitude,
extends or trims along the line as drawn, and writes the vertices back in
degrees. Enter the tolerance as `1 Meters` (or feet, or another linear unit).
`0.00001 Decimal Degrees` is about 1.1 m. The tool message reports the ground
distance it used. A gap that crosses the antimeridian is measured the short
way around.

This is the same rule as the QGIS tool. It is not ArcGIS **Extend Line**
(which extends by a fixed distance with no target) and not **Integrate**
(which snaps vertices sideways).

## Install

`NetworkTopology.atbx` is an ArcGIS toolbox. The Resolve dangles script is
stored inside that file, so you can add the toolbox on its own.

If ArcGIS Pro already has the previous toolbox, remove it from the project
first. The old copy looks for `scripts/resolve_dangles.py` beside the file
and fails with **ERROR 000576: Script associated with this tool does not exist.**

In ArcGIS Pro:

1. Open the **Catalog** pane.
2. Right-click **Toolboxes** → **Add Toolbox**.
3. Choose `NetworkTopology.atbx`.
4. Run **Network Topology → Topology → Resolve dangles (extend / trim)** to fix every end, or **Review dangles (tick / reject)** to look at each one.

**Review dangles** moves the map to every undershoot and overshoot inside the tolerance. **Green tick / Autocorrect** applies that fix. **Red cross / Reject** leaves the end unchanged. Enter accepts, Escape rejects. Closing the window writes nothing. Run it in the foreground so the map can move.

The input layer is not edited. The tool creates a new polyline feature class.
A selection or definition query on the input is honored. ModelBuilder can
read the derived counts **Ends extended** and **Ends trimmed**.

From the ArcGIS Pro Python window, after the toolbox is added to the project:

```python
arcpy.ImportToolbox(r"C:\GIS\network-topology-arcgis\NetworkTopology.atbx")
arcpy.networktopology.ResolveDangles(
    in_features=r"C:\data\roads.gdb\roads",
    tolerance="1 Meters",
    fix_undershoots=True,
    fix_overshoots=True,
    out_features=r"C:\data\roads.gdb\roads_resolved",
)
```

A Python toolbox, `NetworkTopology.pyt`, is also in the folder. Use the
`.atbx` unless you specifically want the Python toolbox.

Enter the tolerance in metres, feet, or another linear unit. On a projected
layer the tool converts that into the layer's unit. On a geographic layer it
keeps the distance in ground metres.

## Parameters

| Parameter | Name | Meaning |
| --- | --- | --- |
| Input line layer | `in_features` | Polyline layer or feature class |
| Tolerance | `tolerance` | Max gap to close, or max tail to trim |
| Extend undershoots | `fix_undershoots` | On by default |
| Trim overshoots | `fix_overshoots` | On by default |
| Resolved lines | `out_features` | New polyline feature class |

A tolerance of 0 writes the lines through unchanged.

## What this toolbox does not do

Resolve dangles does not split lines at crossings. After it, node the
network (ArcGIS **Feature To Line**, **Planarize**, or a geodatabase
topology) if you need a node at every intersection.

The other QGIS tools — topology split, collapse pseudo-nodes, connected
components, cluster extents, and network nodes — are not in this toolbox.

## Tests

ArcGIS Pro is not required to check the geometry rules:

```bash
python3 -m unittest discover -s tests -v
```

The undershoot, overshoot, long-tail, and already-connected cases match the
QGIS plugin tests.

## License

GPL-2.0-or-later. This is a derivative of Network Topology for QGIS,
copyright 2026 oksion. See [LICENSE](LICENSE).
