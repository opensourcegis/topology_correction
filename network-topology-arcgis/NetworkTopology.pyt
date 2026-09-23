# SPDX-License-Identifier: GPL-2.0-or-later
# ArcGIS Pro Python toolbox.
# Derived from Oksion/network-topology-qgis (GPL-2.0-or-later):
# https://github.com/Oksion/network-topology-qgis

"""Network Topology toolbox for ArcGIS Pro.

Add this file from the Catalog pane (Toolboxes → Add Toolbox). The
``network_topology`` package must stay in the same folder as this file.
"""

import os
import sys

_TOOLBOX_DIR = os.path.dirname(os.path.abspath(__file__))
if _TOOLBOX_DIR not in sys.path:
    sys.path.insert(0, _TOOLBOX_DIR)

import arcpy


def _as_bool(value, default):
    if value is None:
        return default
    if isinstance(value, str):
        return value.strip().lower() in ("true", "1", "yes")
    return bool(value)


class Toolbox(object):
    def __init__(self):
        self.label = "Network Topology"
        self.alias = "network_topology"
        self.tools = [ResolveDangles, ReviewDangles]


class ResolveDangles(object):
    """Extend undershoots and trim overshoots on a line network."""

    def __init__(self):
        self.label = "Resolve dangles (extend / trim)"
        self.description = (
            "Cleans dangling ends of a line network and writes one output feature "
            "for each input feature. Attributes are preserved. Lines are not split.\n\n"
            "Undershoot: a free end that stops short of another line is extended "
            "along its own direction until it reaches that line, when the gap is "
            "within the tolerance.\n\n"
            "Overshoot: a free end that runs past a crossing, leaving a tail shorter "
            "than the tolerance, is cut back to the crossing nearest that end.\n\n"
            "Ends are matched against the input geometry in a single pass. This tool "
            "does not node crossings. Run a planarize or topology-split step afterwards "
            "when the network must be split at every intersection."
        )
        self.category = "Topology"
        self.canRunInBackground = False

    def getParameterInfo(self):
        in_features = arcpy.Parameter(
            displayName="Input line layer",
            name="in_features",
            datatype="GPFeatureLayer",
            parameterType="Required",
            direction="Input",
        )
        in_features.filter.list = ["Polyline"]

        tolerance = arcpy.Parameter(
            displayName="Tolerance (max gap to close / tail to trim)",
            name="tolerance",
            datatype="GPLinearUnit",
            parameterType="Required",
            direction="Input",
        )
        tolerance.value = "0 Meters"

        fix_undershoots = arcpy.Parameter(
            displayName="Extend undershoots",
            name="fix_undershoots",
            datatype="GPBoolean",
            parameterType="Optional",
            direction="Input",
        )
        fix_undershoots.value = True

        fix_overshoots = arcpy.Parameter(
            displayName="Trim overshoots",
            name="fix_overshoots",
            datatype="GPBoolean",
            parameterType="Optional",
            direction="Input",
        )
        fix_overshoots.value = True

        out_features = arcpy.Parameter(
            displayName="Resolved lines",
            name="out_features",
            datatype="DEFeatureClass",
            parameterType="Required",
            direction="Output",
        )
        out_features.parameterDependencies = [in_features.name]
        out_features.schema.clone = True

        extended_count = arcpy.Parameter(
            displayName="Ends extended",
            name="extended_count",
            datatype="GPLong",
            parameterType="Derived",
            direction="Output",
        )
        trimmed_count = arcpy.Parameter(
            displayName="Ends trimmed",
            name="trimmed_count",
            datatype="GPLong",
            parameterType="Derived",
            direction="Output",
        )
        return [
            in_features,
            tolerance,
            fix_undershoots,
            fix_overshoots,
            out_features,
            extended_count,
            trimmed_count,
        ]

    def isLicensed(self):
        return True

    def updateParameters(self, parameters):
        return

    def updateMessages(self, parameters):
        tolerance = parameters[1]
        if not tolerance.altered or not tolerance.valueAsText:
            return
        pieces = str(tolerance.valueAsText).split()
        try:
            distance = float(pieces[0])
        except (TypeError, ValueError):
            tolerance.setErrorMessage("Tolerance must be a number.")
            return
        if distance < 0:
            tolerance.setErrorMessage("Tolerance must be greater than or equal to 0.")

    def execute(self, parameters, messages):
        from network_topology.arcpy_io import execute_resolve_dangles

        result = execute_resolve_dangles(
            parameters[0].valueAsText,
            parameters[4].valueAsText,
            parameters[1].valueAsText,
            _as_bool(parameters[2].value, True),
            _as_bool(parameters[3].value, True),
        )
        parameters[5].value = int(result.extended)
        parameters[6].value = int(result.trimmed)
        arcpy.SetParameter(5, int(result.extended))
        arcpy.SetParameter(6, int(result.trimmed))

    def postExecute(self, parameters):
        return


class ReviewDangles(ResolveDangles):
    """Zoom to each dangling end. Tick autocorrects it. Cross leaves it."""

    def __init__(self):
        super().__init__()
        self.label = "Review dangles (tick / reject)"
        self.description = (
            "Moves the map to each undershoot and overshoot inside the tolerance.\n\n"
            "Green tick, Autocorrect: extend or trim that end.\n"
            "Red cross, Reject: leave that end unchanged.\n\n"
            "Run this tool in the foreground so the map can move. "
            "Closing the window writes nothing."
        )
        self.canRunInBackground = False

    def execute(self, parameters, messages):
        from network_topology.arcpy_io import execute_review_dangles

        result = execute_review_dangles(
            parameters[0].valueAsText,
            parameters[4].valueAsText,
            parameters[1].valueAsText,
            _as_bool(parameters[2].value, True),
            _as_bool(parameters[3].value, True),
        )
        parameters[5].value = int(result.extended)
        parameters[6].value = int(result.trimmed)
        arcpy.SetParameter(5, int(result.extended))
        arcpy.SetParameter(6, int(result.trimmed))
