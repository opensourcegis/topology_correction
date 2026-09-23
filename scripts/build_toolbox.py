# SPDX-License-Identifier: GPL-2.0-or-later
"""Build NetworkTopology.atbx next to this project.

Requires the autobox package (https://github.com/realiii/autobox) on PYTHONPATH.
The checked-in .atbx is the file ArcGIS Pro opens; rerun this only to regenerate it.
"""

import json
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TOOL_NAME = "ResolveDangles"
PACKAGE = ROOT / "network_topology"


def _strip_package_imports(source: str) -> str:
    """Drop imports that only exist when the files are separate modules."""
    lines = []
    skipping = False
    for line in source.splitlines(True):
        if line.startswith("from __future__ import annotations"):
            continue
        if skipping:
            if ")" in line:
                skipping = False
            continue
        if line.startswith("from network_topology."):
            if "(" in line and ")" not in line:
                skipping = True
            continue
        lines.append(line)
    return "".join(lines)


def bundle_source() -> str:
    """One script stored inside the .atbx, so Pro does not look for an external file."""
    parts = [
        "# SPDX-License-Identifier: GPL-2.0-or-later\n",
        '"""Resolve dangles. This script is embedded in NetworkTopology.atbx."""\n',
        "from __future__ import annotations\n",
    ]
    for name in (
        "geometry.py",
        "geographic.py",
        "units.py",
        "dangle_resolver.py",
        "review_ui.py",
        "arcpy_io.py",
    ):
        parts.append(_strip_package_imports((PACKAGE / name).read_text(encoding="utf-8")))
        parts.append("\n")
    parts.append(
        '''
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
'''
    )
    source = "".join(parts)
    compile(source, "tool.script.execute.py", "exec")
    return source


def build() -> Path:
    from autobox import ExecutionScript, ScriptTool, Toolbox, Toolset, ValidationScript
    from autobox.default import LinearUnitValue
    from autobox.enum import GeometryType, LinearUnit
    from autobox.filter import FeatureClassTypeFilter
    from autobox.parameter import (
        BooleanParameter,
        FeatureClassParameter,
        FeatureLayerParameter,
        LinearUnitParameter,
        LongParameter,
        StringParameter,
        StringValueFilter,
    )

    mode = StringParameter(
        label="Mode",
        name="mode",
        description=(
            "Automatic applies every correction. "
            "Review moves the map to each dangling end: Enter accepts, Space rejects."
        ),
        default_value="Automatic",
    )
    mode.filter = StringValueFilter(["Automatic", "Review"])

    lines = FeatureLayerParameter(
        label="Input line layer",
        name="in_features",
        description="Polyline layer whose dangling ends will be extended or trimmed.",
    )
    lines.filter = FeatureClassTypeFilter(GeometryType.POLYLINE)

    tolerance = LinearUnitParameter(
        label="Tolerance (max gap to close / tail to trim)",
        name="tolerance",
        description=(
            "Maximum gap to close, or tail to cut, in ground metres. "
            "On EPSG:4326 this is metres, not degrees."
        ),
        default_value=LinearUnitValue(value=0, unit=LinearUnit.METERS),
    )
    tolerance.dependency = lines

    extend = BooleanParameter(
        label="Extend undershoots",
        name="fix_undershoots",
        description="Extend a free end along its own direction until it meets another line.",
        default_value=True,
    )
    trim = BooleanParameter(
        label="Trim overshoots",
        name="fix_overshoots",
        description="Cut a free end back to the crossing nearest that end.",
        default_value=True,
    )
    output = FeatureClassParameter(
        label="Resolved lines",
        name="out_features",
        description="New polyline feature class. One feature is written for each input feature.",
        is_input=False,
    )
    output.filter = FeatureClassTypeFilter(GeometryType.POLYLINE)
    extended = LongParameter(
        label="Ends extended",
        name="extended_count",
        is_input=False,
        is_required=None,
    )
    trimmed = LongParameter(
        label="Ends trimmed",
        name="trimmed_count",
        is_input=False,
        is_required=None,
    )

    tool = ScriptTool(
        name=TOOL_NAME,
        label="Resolve dangles (extend / trim)",
        description=(
            "Automatic applies every correction. Review moves the map to each "
            "dangling end: Enter accepts, Space rejects."
        ),
        summary=(
            "<p><b>Automatic</b> applies every correction inside the tolerance.</p>"
            "<p><b>Review</b> moves the map to each dangling end. "
            "Enter accepts that correction. Space leaves the end unchanged. "
            "Escape cancels and writes nothing. The tool pans the open map "
            "to each dangling end.</p>"
            "<p><b>Undershoot:</b> a free end that stops short of another line is "
            "extended along its own direction until it reaches that line, when the "
            "gap is within the tolerance.</p>"
            "<p><b>Overshoot:</b> a free end that runs past a crossing, leaving a "
            "tail shorter than the tolerance, is cut back to the crossing nearest "
            "that end.</p>"
        ),
    )
    for parameter in (mode, lines, tolerance, extend, trim, output, extended, trimmed):
        tool.add_parameter(parameter)
    tool.execution_script = ExecutionScript.from_code(bundle_source())
    tool.validation_script = ValidationScript.from_file(
        ROOT / "scripts" / "validate_resolve_dangles.py"
    )

    toolset = Toolset(name="Topology")
    toolset.add_script_tool(tool)
    toolbox = Toolbox(
        name="NetworkTopology",
        label="Network Topology",
        alias="networktopology",
        description=(
            "Clean line-network topology. Resolve dangles extends undershoots "
            "and trims overshoots."
        ),
    )
    toolbox.add_toolset(toolset)
    return toolbox.save(ROOT, overwrite=True)


def _normalize(atbx: Path) -> None:
    """Keep a root toolset entry and obtain the output schema from the input."""
    temporary = atbx.with_suffix(".atbx.tmp")
    with zipfile.ZipFile(atbx) as source, zipfile.ZipFile(
        temporary, "w", compression=zipfile.ZIP_DEFLATED
    ) as target:
        for info in source.infolist():
            if info.filename.endswith("tool.script.execute.link"):
                continue
            payload = source.read(info.filename)
            if info.filename == "toolbox.content":
                content = json.loads(payload)
                toolsets = content.get("toolsets", {})
                if "<root>" not in toolsets:
                    content["toolsets"] = {"<root>": {"tools": [""]}, **toolsets}
                payload = (json.dumps(content, indent=4) + "\n").encode("utf-8")
            elif info.filename.endswith(".tool/tool.content"):
                content = json.loads(payload)
                if "out_features" in content.get("params", {}):
                    content["params"]["out_features"]["depends"] = ["in_features"]
                payload = (json.dumps(content, indent=4) + "\n").encode("utf-8")
            target.writestr(info, payload)
    temporary.replace(atbx)


def main() -> None:
    atbx = build()
    _normalize(atbx)
    print(atbx)


if __name__ == "__main__":
    sys.exit(main())
