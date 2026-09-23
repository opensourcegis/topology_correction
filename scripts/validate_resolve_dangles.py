# SPDX-License-Identifier: GPL-2.0-or-later
"""ToolValidator embedded in NetworkTopology.atbx."""


class ToolValidator:
    def __init__(self):
        import arcpy

        self.params = arcpy.GetParameterInfo()

    def initializeParameters(self):
        return

    def updateParameters(self):
        return

    def updateMessages(self):
        tolerance = next(param for param in self.params if param.name == "tolerance")
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
