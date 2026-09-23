# SPDX-License-Identifier: GPL-2.0-or-later
"""Script tool for NetworkTopology.atbx.

ArcGIS Pro runs this file from the toolbox link. The network_topology
package must stay in the folder that contains the .atbx.
"""

import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from network_topology.arcpy_io import execute_resolve_dangles  # noqa: E402


def _flag(value, default):
    if value is None:
        return default
    if isinstance(value, str):
        return value.strip().lower() in ("true", "1", "yes")
    return bool(value)


def main():
    import arcpy

    in_features = arcpy.GetParameterAsText(0)
    tolerance = arcpy.GetParameterAsText(1)
    fix_undershoots = _flag(arcpy.GetParameter(2), True)
    fix_overshoots = _flag(arcpy.GetParameter(3), True)
    out_features = arcpy.GetParameterAsText(4)
    result = execute_resolve_dangles(
        in_features,
        out_features,
        tolerance,
        fix_undershoots,
        fix_overshoots,
    )
    arcpy.SetParameter(5, int(result.extended))
    arcpy.SetParameter(6, int(result.trimmed))


if __name__ == "__main__":
    main()
