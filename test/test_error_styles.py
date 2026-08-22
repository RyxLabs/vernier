# -*- coding: utf-8 -*-
# SPDX-FileCopyrightText: 2026 RYXLAB SOFT TECH SRL (RyxLabs)
# SPDX-License-Identifier: GPL-2.0-or-later
"""The shared error palette. One violet means "duplicate" everywhere in the plugin, so the topology layer and the review layer cannot drift apart."""

import os
import sys
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from qgis.core import (  # type: ignore
    QgsApplication, QgsRenderContext, QgsWkbTypes,
)

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))))

from qgis.core import Qgis as _Qgis  # noqa: E402

from vernier.services import error_styles  # noqa: E402

_ST = getattr(_Qgis, "SymbolType", None)
QgsSymbolTypePolygon = _ST.Fill if _ST else 2
QgsSymbolTypeLine = _ST.Line if _ST else 1
QgsSymbolTypeMarker = _ST.Marker if _ST else 0

QGS = None


def setUpModule():
    global QGS
    QGS = QgsApplication([], True)
    QGS.initQgis()


def tearDownModule():
    QGS.exitQgis()


def _symbol(renderer):
    """An owned copy of the renderer's first symbol. renderer.symbols() hands back symbols owned by the renderer and keyed to the context passed in, so holding one past the end of the expression reads freed memory - which crashes the interpreter or, worse, returns plausible wrong colours."""
    context = QgsRenderContext()
    return renderer.symbols(context)[0].clone()


class TestInvalidRenderer(unittest.TestCase):

    def test_invalid_marks_are_markers(self):
        renderer = error_styles.invalid_renderer()
        self.assertEqual(_symbol(renderer).type(), QgsSymbolTypeMarker)

    def test_invalid_marker_paints_its_declared_colour(self):
        # an X is a stroke-only shape: QGIS paints it from strokeColor and
        # never touches fillColor, so a symbol that declares one and paints
        # the other looks nothing like the palette says it does. asserting
        # both is what makes this immune to the shape changing later
        symbol = _symbol(error_styles.invalid_renderer())
        marker = symbol.symbolLayer(0)  # keep `symbol` alive while reading it
        for role, color in (("stroke", marker.strokeColor()),
                            ("fill", marker.fillColor())):
            self.assertEqual((color.red(), color.green(), color.blue()),
                             error_styles.INVALID_RGB, role)

    def test_invalid_does_not_reuse_the_duplicate_violet(self):
        # five error classes share one map; another violet would be unreadable
        invalid = _symbol(error_styles.invalid_renderer()).color()
        self.assertNotEqual(
            (invalid.red(), invalid.green(), invalid.blue()),
            error_styles.DUPLICATE_RGB)


class TestDuplicateRenderer(unittest.TestCase):

    def test_polygon_layer_gets_a_fill(self):
        renderer = error_styles.duplicate_renderer(
            QgsWkbTypes.GeometryType.PolygonGeometry)
        self.assertEqual(_symbol(renderer).type(), QgsSymbolTypePolygon)

    def test_line_layer_gets_a_line_symbol(self):
        renderer = error_styles.duplicate_renderer(
            QgsWkbTypes.GeometryType.LineGeometry)
        self.assertEqual(_symbol(renderer).type(), QgsSymbolTypeLine)

    def test_point_layer_gets_a_marker(self):
        renderer = error_styles.duplicate_renderer(
            QgsWkbTypes.GeometryType.PointGeometry)
        self.assertEqual(_symbol(renderer).type(), QgsSymbolTypeMarker)

    def test_every_family_uses_the_same_violet(self):
        colors = set()
        for kind in (QgsWkbTypes.GeometryType.PolygonGeometry,
                     QgsWkbTypes.GeometryType.LineGeometry,
                     QgsWkbTypes.GeometryType.PointGeometry):
            color = _symbol(error_styles.duplicate_renderer(kind)).color()
            colors.add((color.red(), color.green(), color.blue()))
        self.assertEqual(colors, {error_styles.DUPLICATE_RGB})

    def test_unknown_geometry_type_still_returns_a_renderer(self):
        # NullGeometry should not raise - a broken layer is not a crash
        self.assertIsNotNone(error_styles.duplicate_renderer(
            QgsWkbTypes.GeometryType.NullGeometry))


if __name__ == "__main__":
    unittest.main()
