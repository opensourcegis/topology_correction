# SPDX-License-Identifier: GPL-2.0-or-later

"""Network topology tools for ArcGIS Pro.

The first tool, Resolve dangles, extends undershoots and trims overshoots.
"""

from network_topology.dangle_resolver import resolve_dangles

__all__ = ["resolve_dangles"]
