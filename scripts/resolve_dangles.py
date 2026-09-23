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

from network_topology.arcpy_io import execute_resolve_dangles, execute_review_dangles  # noqa: E402
from network_topology.review_ui import is_review_mode  # noqa: E402


def _flag(value, default):
    if value is None:
        return default
    if isinstance(value, str):
        return value.strip().lower() in ("true", "1", "yes")
    return bool(value)


def main():
    import arcpy

    mode = arcpy.GetParameterAsText(0)
    in_features = arcpy.GetParameterAsText(1)
    tolerance = arcpy.GetParameterAsText(2)
    fix_undershoots = _flag(arcpy.GetParameter(3), True)
    fix_overshoots = _flag(arcpy.GetParameter(4), True)
    out_features = arcpy.GetParameterAsText(5)
    runner = execute_review_dangles if is_review_mode(mode) else execute_resolve_dangles
    result = runner(
        in_features,
        out_features,
        tolerance,
        fix_undershoots,
        fix_overshoots,
    )
    arcpy.SetParameter(6, int(result.extended))
    arcpy.SetParameter(7, int(result.trimmed))


if __name__ == "__main__":
    main()
