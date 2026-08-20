# -*- coding: utf-8 -*-
# SPDX-FileCopyrightText: 2026 RYXLAB SOFT TECH SRL (RyxLabs)
# SPDX-License-Identifier: GPL-2.0-or-later
"""Feature catalog - the toolbar, menu, help dialog and CAD command bar all read from here."""

from dataclasses import dataclass
from typing import Optional


def QT_TRANSLATE_NOOP(context: str, text: str) -> str:
    """Mark a source string for extraction, hand it back unchanged."""
    # defined here instead of imported from Qt so this module keeps zero Qt/QGIS imports and stays importable from tests - pylupdate reads the literals lexically and the real lookup happens in tr() at consumption
    return text


@dataclass(frozen=True)
class Feature:
    """Declarative spec for one Vernier feature."""
    method: str                     # plugin method name (resolved via getattr)
    label: str                      # display text (English source)
    icon: str                       # filename in icons/
    hint: str                       # one-line tooltip / help-table description
    shortcut: Optional[str] = None  # Qt shortcut, e.g. "Ctrl+Alt+S"
    checkable: bool = False         # toggle-style action (e.g. snapping)
    aliases: tuple = ()             # CAD Mode command-bar typed commands


# the plugin class builds its QActions from here and warns on drift, so wiring and docs cannot quietly diverge. aliases must stay collision-free across this catalog and command_bar.BUILTIN_COMMANDS, test_command_registry enforces it
CATALOG: tuple = (
    Feature(
        method="toggle_snapping",
        label=QT_TRANSLATE_NOOP("Vernier", "Smart Snapping"),
        icon="snap.svg",
        hint=QT_TRANSLATE_NOOP(
            "Vernier",
            "Toggle snapping with your saved profile: all layers, vertex "
            "and segment, intersection snapping, topological editing"),
        shortcut="Ctrl+Alt+S",
        checkable=True,
        aliases=("snap", "sn"),
    ),
    Feature(
        method="toggle_autosave",
        label=QT_TRANSLATE_NOOP("Vernier", "Autosave Backups"),
        icon="autosave.svg",
        hint=QT_TRANSLATE_NOOP(
            "Vernier",
            "Toggle timed backups of the project file and editable layers "
            "into a versioned backup folder"),
        checkable=True,
        aliases=("autosave", "as"),
    ),
    Feature(
        method="open_autosave_settings",
        label=QT_TRANSLATE_NOOP("Vernier", "Autosave Settings..."),
        icon="autosave.svg",
        hint=QT_TRANSLATE_NOOP(
            "Vernier",
            "Configure the backup folder, interval and retention, or "
            "restore a previous backup"),
        aliases=("backup", "bak"),
    ),
    Feature(
        method="toggle_cad_mode",
        label=QT_TRANSLATE_NOOP("Vernier", "CAD Mode"),
        icon="cad_mode.svg",
        hint=QT_TRANSLATE_NOOP(
            "Vernier",
            "Toggle the CAD workspace: dark canvas with a scalable grid, a "
            "typed command bar and a status strip"),
        checkable=True,
        aliases=("cad",),
    ),
    Feature(
        method="find_duplicates",
        label=QT_TRANSLATE_NOOP("Vernier", "Find Duplicate Geometries"),
        icon="duplicates.svg",
        hint=QT_TRANSLATE_NOOP(
            "Vernier",
            "Extract every group of identical geometries in the active "
            "layer to a review layer, tagged with a group_id for sorting"),
        shortcut="Ctrl+Alt+W",
        aliases=("duplicates", "dup"),
    ),
    Feature(
        method="remove_close_vertices",
        label=QT_TRANSLATE_NOOP("Vernier", "Remove Close Vertices..."),
        icon="vertices.svg",
        hint=QT_TRANSLATE_NOOP(
            "Vernier",
            "Collapse near-duplicate consecutive vertices into one, "
            "keeping the vertex shared with neighboring features so common "
            "boundaries survive"),
        shortcut="Ctrl+Alt+Q",
        aliases=("clean", "cv"),
    ),
    Feature(
        method="check_topology",
        label=QT_TRANSLATE_NOOP("Vernier", "Topology Validator"),
        icon="topology.svg",
        hint=QT_TRANSLATE_NOOP(
            "Vernier",
            "Run validity, duplicate, overlap, gap and vertex checks on a "
            "layer, with clickable results and styled error layers"),
        shortcut="Ctrl+Alt+E",
        aliases=("topology", "topo"),
    ),
    Feature(
        method="run_buffer",
        label=QT_TRANSLATE_NOOP("Vernier", "Buffer..."),
        icon="buffer.svg",
        hint=QT_TRANSLATE_NOOP(
            "Vernier",
            "Buffer features by a distance, with segments, end cap and "
            "join style control"),
        shortcut="Ctrl+Alt+B",
        aliases=("buffer", "bf"),
    ),
    Feature(
        method="run_intersection",
        label=QT_TRANSLATE_NOOP("Vernier", "Intersection..."),
        icon="intersection.svg",
        hint=QT_TRANSLATE_NOOP(
            "Vernier",
            "Extract the areas where two layers overlap"),
        aliases=("intersection", "int"),
    ),
    Feature(
        method="run_difference",
        label=QT_TRANSLATE_NOOP("Vernier", "Difference..."),
        icon="difference.svg",
        hint=QT_TRANSLATE_NOOP(
            "Vernier",
            "Remove the parts of a layer covered by another layer"),
        aliases=("difference", "diff"),
    ),
    Feature(
        method="run_dissolve",
        label=QT_TRANSLATE_NOOP("Vernier", "Dissolve..."),
        icon="dissolve.svg",
        hint=QT_TRANSLATE_NOOP(
            "Vernier",
            "Merge features into one, optionally grouped by a field"),
        aliases=("dissolve", "dis"),
    ),
    Feature(
        method="run_multipart_to_single",
        label=QT_TRANSLATE_NOOP("Vernier", "Multipart to Singleparts..."),
        icon="multi2single.svg",
        hint=QT_TRANSLATE_NOOP(
            "Vernier",
            "Split multipart features into one feature per part"),
        aliases=("explode", "m2s"),
    ),
    Feature(
        method="open_detach",
        label=QT_TRANSLATE_NOOP("Vernier", "Split to Target Areas"),
        icon="detach.svg",
        hint=QT_TRANSLATE_NOOP(
            "Vernier",
            "Split one selected polygon into pieces of exact target areas "
            "with straight cuts parallel to a drawn direction line, with "
            "Excel paste and remainder tracking"),
        shortcut="Ctrl+Alt+D",
        aliases=("split", "spl"),
    ),
    Feature(
        method="extract_centerline",
        label=QT_TRANSLATE_NOOP("Vernier", "Extract Centerline..."),
        icon="centerline.svg",
        hint=QT_TRANSLATE_NOOP(
            "Vernier",
            "Extract the medial axis of polygon features as a straightened "
            "centerline (parcels, roads, rivers)"),
        aliases=("centerline", "cl"),
    ),
    Feature(
        method="run_attribute_join",
        label=QT_TRANSLATE_NOOP("Vernier", "Join Attributes..."),
        icon="join.svg",
        hint=QT_TRANSLATE_NOOP(
            "Vernier",
            "Pull columns from one or more source layers into a target "
            "layer by matching key fields, with a match-count preview"),
        aliases=("join", "jn"),
    ),
    Feature(
        method="run_spatial_join",
        label=QT_TRANSLATE_NOOP("Vernier", "Spatial Join..."),
        icon="spatial_join.svg",
        hint=QT_TRANSLATE_NOOP(
            "Vernier",
            "Copy attribute values onto a polygon layer from every layer "
            "that intersects it, with multi-value handling and provenance "
            "columns"),
        aliases=("sjoin", "sj"),
    ),
    Feature(
        method="export_kmz",
        label=QT_TRANSLATE_NOOP("Vernier", "Export KMZ..."),
        icon="kmz.svg",
        hint=QT_TRANSLATE_NOOP(
            "Vernier",
            "Export layers to KMZ for Google Earth / Google Maps, with "
            "multi-field labels, per-layer colors and polygon name "
            "placemarks"),
        aliases=("kmz",),
    ),
    Feature(
        method="import_dxf",
        label=QT_TRANSLATE_NOOP("Vernier", "Import DXF / DWG..."),
        icon="dxf_import.svg",
        hint=QT_TRANSLATE_NOOP(
            "Vernier",
            "Import a DXF or DWG drawing to a styled GeoPackage: CAD "
            "colors, lineweights and linetypes preserved, one QGIS layer "
            "per CAD layer"),
        shortcut="Ctrl+Alt+I",
        aliases=("dxfin",),
    ),
    Feature(
        method="export_dxf",
        label=QT_TRANSLATE_NOOP("Vernier", "Export DXF..."),
        icon="dxf_export.svg",
        hint=QT_TRANSLATE_NOOP(
            "Vernier",
            "Export layers to DXF with true colors, lineweights and "
            "attribute labels, optionally split into one file per value of "
            "a field"),
        shortcut="Ctrl+Alt+K",
        aliases=("dxfout",),
    ),
    Feature(
        method="lines_to_polygons",
        label=QT_TRANSLATE_NOOP("Vernier", "CAD Lines to Polygons..."),
        icon="lines_to_polygons.svg",
        hint=QT_TRANSLATE_NOOP(
            "Vernier",
            "Rebuild closed lines from an imported CAD drawing into "
            "polygon layers, one output layer per value of the drawing's "
            "Layer attribute"),
        aliases=("polygonize", "l2p"),
    ),
    Feature(
        method="open_style",
        label=QT_TRANSLATE_NOOP("Vernier", "Quick Symbology..."),
        icon="symbology.svg",
        hint=QT_TRANSLATE_NOOP(
            "Vernier",
            "Style a layer from reusable templates - line style, vertex "
            "markers and multi-field labels bound to your fields by name - "
            "or hand-set a simple style"),
        shortcut="Ctrl+Alt+Y",
        aliases=("style", "sym"),
    ),
    Feature(
        method="open_settings",
        label=QT_TRANSLATE_NOOP("Vernier", "Settings..."),
        icon="settings.svg",
        hint=QT_TRANSLATE_NOOP("Vernier", "Configure Vernier defaults"),
        aliases=("settings", "cfg"),
    ),
    Feature(
        method="open_help",
        label=QT_TRANSLATE_NOOP("Vernier", "Help..."),
        icon="help.svg",
        hint=QT_TRANSLATE_NOOP(
            "Vernier",
            "Tool reference and plugin information"),
        aliases=("help", "?"),
    ),
)


# standalone toolbar buttons before the dropdown groups, in display order
TOOLBAR_LEADING: tuple = (
    "toggle_snapping",
    "toggle_autosave",
    "toggle_cad_mode",
)


# dropdown groups: (label, icon, members). a None member becomes a separator
TOOLBAR_GROUPS: tuple = (
    (QT_TRANSLATE_NOOP("Vernier", "Geoprocessing"), "intersection.svg", (
        "run_buffer",
        "run_intersection",
        "run_difference",
        "run_dissolve",
        "run_multipart_to_single",
        None,
        "open_detach",
        "extract_centerline",
    )),
    (QT_TRANSLATE_NOOP("Vernier", "Data"), "join.svg", (
        "run_attribute_join",
        "run_spatial_join",
    )),
    (QT_TRANSLATE_NOOP("Vernier", "CAD / Export"), "dxf_import.svg", (
        "import_dxf",
        "export_dxf",
        "lines_to_polygons",
        None,
        "export_kmz",
    )),
    (QT_TRANSLATE_NOOP("Vernier", "Validation"), "topology.svg", (
        "check_topology",
        None,
        "find_duplicates",
        "remove_close_vertices",
    )),
)


# standalone toolbar buttons after the groups
TOOLBAR_TRAILING: tuple = (
    "open_style",
    "open_settings",
    "open_help",
)


# Vector-menu layout in display order. QGIS's plugin-menu API only takes actions, so this is one flat list
MENU: tuple = (
    "toggle_snapping",
    "toggle_autosave",
    "open_autosave_settings",
    "toggle_cad_mode",
    "find_duplicates",
    "remove_close_vertices",
    "check_topology",
    "run_buffer",
    "run_intersection",
    "run_difference",
    "run_dissolve",
    "run_multipart_to_single",
    "open_detach",
    "extract_centerline",
    "run_attribute_join",
    "run_spatial_join",
    "import_dxf",
    "export_dxf",
    "lines_to_polygons",
    "export_kmz",
    "open_style",
    "open_settings",
    "open_help",
)
