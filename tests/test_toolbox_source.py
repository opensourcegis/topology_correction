# SPDX-License-Identifier: GPL-2.0-or-later

import unittest
from pathlib import Path


class ToolboxSourceTests(unittest.TestCase):
    def test_python_toolbox_parses(self):
        path = Path(__file__).resolve().parents[1] / "NetworkTopology.pyt"
        source = path.read_text(encoding="utf-8")
        compile(source, str(path), "exec")
        self.assertIn("class Toolbox", source)
        self.assertIn("class ResolveDangles", source)
        self.assertIn("fix_undershoots", source)
        self.assertIn("fix_overshoots", source)


if __name__ == "__main__":
    unittest.main()
