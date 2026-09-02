# -*- coding: utf-8 -*-
# SPDX-FileCopyrightText: 2026 RYXLAB SOFT TECH SRL (RyxLabs)
# SPDX-License-Identifier: GPL-2.0-or-later
"""Shared symbology for the error overlays. The topology panel and the duplicates tool both mark duplicates, on layers of any geometry type, and a colour defined twice is a colour that drifts - so the violet lives here and both import it."""

# not style_templates: that engine serves user-authored JSON in the profile, while these are fixed plugin colours with no template behind them

from qgis.core import (  # type: ignore
    QgsFillSymbol, QgsLineSymbol, QgsMarkerSymbol, QgsSingleSymbolRenderer,
    QgsWkbTypes,
)

# violet, deliberately clear of the overlap crimson and the gap blue: a duplicate marks a whole feature rather than a shared sliver
DUPLICATE_RGB = (123, 44, 191)
DUPLICATE_OUTLINE = "#5a189a"

_FILL_ALPHA = 110
_MARKER_ALPHA = 150


def _rgba(alpha):
    return "{0},{1},{2},{3}".format(*DUPLICATE_RGB, alpha)


# dark red, and an X rather than another circle or square: five error classes
# share one map, so shape carries the meaning where hue has run out
INVALID_RGB = (193, 18, 31)


def invalid_renderer():
    """Marker renderer for the points where geometries break.

    Single symbol on purpose: GEOS complaint strings are an open set, and a categorized renderer draws nothing at all for a category it has never seen - which is the invisible-error bug this layer exists to fix.

    Stroke and fill are the same colour on purpose too. An X is a stroke-only shape, so QGIS paints it from strokeColor and never reads fillColor, and giving the two different values means the palette says one thing while the map shows another."""
    rgba = "{0},{1},{2},255".format(*INVALID_RGB)
    return QgsSingleSymbolRenderer(QgsMarkerSymbol.createSimple({
        "name": "cross2",
        "color": rgba,
        "size": "3.4",
        "outline_color": rgba,
        "outline_width": "0.6",
    }))


def duplicate_renderer(geometry_type):
    """A single-symbol renderer in the duplicate violet, in whichever symbol family the geometry type needs. Anything unrecognised falls through to a marker, so a layer with no usable geometry type still renders instead of raising."""
    if geometry_type == QgsWkbTypes.GeometryType.PolygonGeometry:
        return QgsSingleSymbolRenderer(QgsFillSymbol.createSimple({
            "color": _rgba(_FILL_ALPHA),
            "outline_color": DUPLICATE_OUTLINE,
            "outline_width": "0.66",
        }))
    if geometry_type == QgsWkbTypes.GeometryType.LineGeometry:
        return QgsSingleSymbolRenderer(QgsLineSymbol.createSimple({
            "color": _rgba(255),
            "width": "1.2",
        }))
    return QgsSingleSymbolRenderer(QgsMarkerSymbol.createSimple({
        "name": "circle",
        "color": _rgba(_MARKER_ALPHA),
        "size": "3.2",
        "outline_color": DUPLICATE_OUTLINE,
        "outline_width": "0.4",
    }))
