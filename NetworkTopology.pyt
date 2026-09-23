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
        self.tools = [ResolveDangles]


class ResolveDangles(object):
    """Extend undershoots and trim overshoots on a line network."""

    def __init__(self):
        self.label = "Resolve dangles (extend / trim)"
        self.description = (
            "Cleans dangling ends of a line network and writes one output feature "
            "for each input feature. Attributes are preserved. Lines are not split.\n\n"
            "Mode Automatic applies every correction.\n"
            "Mode Review moves the map to each dangling end. Enter accepts that "
            "correction. Space leaves the end unchanged. Escape cancels and writes nothing.\n\n"
            "Undershoot: a free end that stops short of another line is extended "
            "along its own direction until it reaches that line, when the gap is "
            "within the tolerance.\n\n"
            "Overshoot: a free end that runs past a crossing, leaving a tail shorter "
            "than the tolerance, is cut back to the crossing nearest that end.\n\n"
            "Ends are matched against the input geometry in a single pass. This tool "
            "does not node crossings. Run a planarize or topology-split step afterwards "
            "when the network must be split at every intersection. "
            "Review pans the open map to each dangling end."
        )
        self.category = "Topology"
        self.canRunInBackground = False

    def getParameterInfo(self):
        mode = arcpy.Parameter(
            displayName="Mode",
            name="mode",
            datatype="GPString",
            parameterType="Required",
            direction="Input",
        )
        mode.filter.type = "ValueList"
        mode.filter.list = ["Automatic", "Review"]
        mode.value = "Automatic"

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
            mode,
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
        tolerance = parameters[2]
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
        from network_topology.arcpy_io import execute_resolve_dangles, execute_review_dangles
        from network_topology.review_ui import is_review_mode

        runner = execute_review_dangles if is_review_mode(parameters[0].valueAsText) else execute_resolve_dangles
        result = runner(
            parameters[1].valueAsText,
            parameters[5].valueAsText,
            parameters[2].valueAsText,
            _as_bool(parameters[3].value, True),
            _as_bool(parameters[4].value, True),
        )
        parameters[6].value = int(result.extended)
        parameters[7].value = int(result.trimmed)
        arcpy.SetParameter(6, int(result.extended))
        arcpy.SetParameter(7, int(result.trimmed))

    def postExecute(self, parameters):
        return
