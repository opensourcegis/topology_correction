# SPDX-License-Identifier: GPL-2.0-or-later

import json
import unittest
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ATBX = ROOT / "NetworkTopology.atbx"


class ArcToolboxTests(unittest.TestCase):
    def test_atbx_is_the_arcgis_toolbox(self):
        self.assertTrue(zipfile.is_zipfile(ATBX))
        with zipfile.ZipFile(ATBX) as archive:
            names = set(archive.namelist())
            toolbox = json.loads(archive.read("toolbox.content"))
            resources = json.loads(archive.read("toolbox.content.rc"))
            tool = json.loads(archive.read("ResolveDangles.tool/tool.content"))
            script = archive.read("ResolveDangles.tool/tool.script.execute.py").decode()
            self.assertNotIn("ResolveDangles.tool/tool.script.execute.link", names)
            self.assertIn("ResolveDangles.tool/tool.script.validate.py", names)
            self.assertNotIn("ReviewDangles.tool/tool.content", names)

        self.assertEqual(toolbox["alias"], "networktopology")
        self.assertEqual(toolbox["version"], "1.0")
        self.assertEqual(resources["map"]["title"], "Network Topology")
        self.assertEqual(resources["map"]["toolset1.name"], "Topology")
        self.assertEqual(
            toolbox["toolsets"]["$rc:toolset1.name"]["tools"],
            ["ResolveDangles"],
        )
        self.assertIn("is_review_mode", script)
        self.assertIn("execute_review_dangles", script)
        self.assertIn("review_zoom_extent", script)
        self.assertIn("review_highlight", script)
        self.assertIn("_DANGLE_COLOR", script)
        self.assertIn("ThreadPoolExecutor", script)
        self.assertIn("PARALLEL_MIN_PARTS", script)
        self.assertNotIn("panToExtent", script)
        self.assertNotIn("exportToPNG", script)
        self.assertIn("<root>", toolbox["toolsets"])

        self.assertEqual(tool["type"], "ScriptTool")
        self.assertEqual(
            list(tool["params"]),
            [
                "mode",
                "in_features",
                "tolerance",
                "fix_undershoots",
                "fix_overshoots",
                "out_features",
                "extended_count",
                "trimmed_count",
            ],
        )
        self.assertEqual(tool["params"]["mode"]["datatype"]["type"], "GPString")
        self.assertEqual(tool["params"]["mode"]["value"], "Automatic")
        self.assertEqual(tool["params"]["in_features"]["datatype"]["type"], "GPFeatureLayer")
        self.assertEqual(
            tool["params"]["in_features"]["domain"]["geometrytype"],
            ["Polyline"],
        )
        self.assertEqual(tool["params"]["tolerance"]["datatype"]["type"], "GPLinearUnit")
        self.assertEqual(tool["params"]["tolerance"]["value"], "0 Meters")
        self.assertEqual(tool["params"]["fix_undershoots"]["value"], "true")
        self.assertEqual(tool["params"]["fix_overshoots"]["value"], "true")
        self.assertEqual(tool["params"]["out_features"]["direction"], "out")
        self.assertEqual(tool["params"]["out_features"]["depends"], ["in_features"])
        self.assertEqual(tool["params"]["extended_count"]["type"], "derived")
        self.assertEqual(tool["params"]["trimmed_count"]["type"], "derived")
        compile(script, "tool.script.execute.py", "exec")
        import sys
        import types

        module = types.ModuleType("embedded_resolve_dangles")
        module.__file__ = "tool.script.execute.py"
        sys.modules[module.__name__] = module
        try:
            exec(script, module.__dict__)
            result = module.__dict__["resolve_dangles"](
                [
                    {"attrs": None, "parts": [[(0, 5), (4.6, 5)]]},
                    {"attrs": None, "parts": [[(5, 0), (5, 10)]]},
                ],
                1.0,
                fix_undershoots=True,
                fix_overshoots=False,
            )
        finally:
            sys.modules.pop(module.__name__, None)
        self.assertEqual(result.extended, 1)
        lon_scale, _ = module.meters_per_degree(0.0)
        geographic = module.resolve_dangles(
            [
                {
                    "attrs": None,
                    "parts": [[(0.0, 0.0), (4.6 / lon_scale, 0.0)]],
                },
                {
                    "attrs": None,
                    "parts": [[(5.0 / lon_scale, -0.001), (5.0 / lon_scale, 0.001)]],
                },
            ],
            1.0,
            fix_overshoots=False,
            geographic=True,
        )
        self.assertEqual(geographic.extended, 1)
        self.assertIn("LocalMeterFrame", script)
        self.assertNotIn("tool.script.execute.link", script)


if __name__ == "__main__":
    unittest.main()
