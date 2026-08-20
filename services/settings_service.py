# -*- coding: utf-8 -*-
# SPDX-FileCopyrightText: 2026 RYXLAB SOFT TECH SRL (RyxLabs)
# SPDX-License-Identifier: GPL-2.0-or-later
"""Typed access to the plugin settings, kept under the Vernier/ group of QgsSettings so they survive updates - the plugin folder gets wiped on every one."""

# DEFAULTS is the source of truth for keys and types, get() coerces the stored value to the default's type so callers never see the strings QSettings hands back on some platforms

from qgis.core import QgsSettings  # type: ignore

GROUP = "Vernier"

DEFAULTS = {
    # one-time welcome after install
    "general/welcome_shown": False,
    # snapping profile the Smart Snapping toggle applies
    "snapping/tolerance_px": 12,
    "snapping/type": "vertex_and_segment",  # vertex_and_segment | vertex | segment
    "snapping/intersection": True,
    "snapping/topological_editing": True,
    # area readout in the status bar
    "area/units": "auto",  # auto | m2 | ha | km2 | acres | ft2
    # the figure in parentheses, "auto" derives it from the primary. see tools.area_readout
    "area/units_secondary": "auto",  # none | auto | m2 | ha | km2 | acres | ft2
    "area/show_readout": True,
    # Split to Target Areas pane heights in pixels, "" falls back to the built-in split
    "detach/splitter_sizes": "",
    "autosave/configured": False,  # flips True on the first saved configuration
    "autosave/interval_minutes": 5,
    "autosave/save_project": True,
    "autosave/save_layers": True,
    "autosave/max_backups": 10,  # backup events kept per project
    # Remove Close Vertices, layer units
    "vertex_cleaner/segment_tolerance": 0.005,
    "vertex_cleaner/dup_tolerance": 0.002,
    "vertex_cleaner/snap_tolerance": 0.001,
    # Topology Validator, layer units
    "topology/snap_tolerance": 0.005,
    "topology/gap_min_area": 0.01,
    "topology/gap_buffer": 0.0005,
    "topology/vertex_tolerance": 0.005,
    # CAD Mode
    "cad_mode/enabled": False,
    "cad_mode/dark_canvas": True,   # sub-toggles apply while the mode is on
    "cad_mode/grid": True,
    "cad_mode/command_bar": True,
    "cad_mode/status_strip": True,
    # whether the panel is on screen, set by its X and by View > Panels. the two sub-toggles above decide what goes inside it; switching CAD Mode on from the toolbar puts it back
    "cad_mode/panel_visible": True,
    # F8 / "bm" basemap. OSM by default, their tile policy allows it - anything else is the user's own choice
    "cad_mode/basemap_url": "https://tile.openstreetmap.org/{z}/{x}/{y}.png",
    "dxf_import/last_crs": "",  # authid or WKT, "" means project CRS
    "dxf_import/skip_enabled": True,
    "dxf_import/skip_keywords": (
        "ANNO, AUX, DEFPOINT, DRAFT, ELEV, GRATICULE, GRID, "
        "HATCH, LEVEL, NODE, PNT, POINT, SCRATCH, SOLID, TEMP, TEXT, "
        "VERTEX, WIPEOUT, WORK"),
}


def get(key: str):
    """The setting, coerced to the type of its default."""
    default = DEFAULTS[key]
    value = QgsSettings().value(f"{GROUP}/{key}", default)
    if isinstance(default, bool):
        # bool before int, isinstance(True, int) is True
        if isinstance(value, str):
            return value.lower() in ("true", "1", "yes")
        return bool(value)
    if isinstance(default, int):
        try:
            return int(value)
        except (TypeError, ValueError):
            return default
    if isinstance(default, float):
        try:
            return float(value)
        except (TypeError, ValueError):
            return default
    return value


def set_(key: str, value):
    """Store a setting. The key has to exist in DEFAULTS."""
    if key not in DEFAULTS:
        raise KeyError(f"Unknown Vernier setting: {key}")
    QgsSettings().setValue(f"{GROUP}/{key}", value)


def reset_all():
    """Remove every stored Vernier setting, reverting to DEFAULTS."""
    settings = QgsSettings()
    settings.beginGroup(GROUP)
    settings.remove("")
    settings.endGroup()
