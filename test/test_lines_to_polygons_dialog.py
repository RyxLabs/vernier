# -*- coding: utf-8 -*-
# SPDX-FileCopyrightText: 2026 RYXLAB SOFT TECH SRL (RyxLabs)
# SPDX-License-Identifier: GPL-2.0-or-later
"""The CRS warning on CAD Lines to Polygons. A DXF added straight to QGIS carries no CRS, the polygons built from it inherit that, and every area measured on them afterwards is the raw coordinate area - so the dialog has to say so while the source can still be fixed. Needs a GUI-enabled QgsApplication, offscreen is fine."""

import os
import sys
import unittest

_PLUGINS_DIR = os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))))
if _PLUGINS_DIR not in sys.path:
    sys.path.insert(0, _PLUGINS_DIR)

from qgis.core import (  # noqa: E402  # type: ignore
    QgsApplication, QgsCoordinateReferenceSystem, QgsFeature, QgsGeometry,
    QgsProject, QgsVectorLayer,
)

from vernier.dialogs.lines_to_polygons_dialog import (  # noqa: E402
    LinesToPolygonsDialog,
)

QGS = None


def setUpModule():
    global QGS
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    QGS = QgsApplication([], True)
    QGS.initQgis()


def tearDownModule():
    QGS.exitQgis()


def _line_layer(name, crs=None, layer_field=True):
    fields = "?field=Layer:string" if layer_field else ""
    layer = QgsVectorLayer(f"LineString{fields}", name, "memory")
    # a memory layer starts out with a CRS, so an unset one has to be set unset
    layer.setCrs(crs or QgsCoordinateReferenceSystem())
    f = QgsFeature(layer.fields())
    f.setGeometry(QgsGeometry.fromWkt("LINESTRING(0 0, 10 0, 10 10, 0 0)"))
    if layer_field:
        f.setAttributes(["CONTUR"])
    layer.dataProvider().addFeatures([f])
    return layer


class TestCrsWarning(unittest.TestCase):
    """isVisibleTo(), not isVisible() - the dialog is never shown in the suite,
    and a child of an unshown window answers False either way, which would let
    the hidden cases pass without proving anything."""

    def tearDown(self):
        self.dialog.done(0)
        self.dialog.deleteLater()
        QgsProject.instance().removeAllMapLayers()

    def _open_with(self, *layers):
        for layer in layers:
            QgsProject.instance().addMapLayer(layer)
        self.dialog = LinesToPolygonsDialog()
        return self.dialog

    def test_hidden_for_a_layer_with_a_crs(self):
        self._open_with(_line_layer(
            "stereo70", QgsCoordinateReferenceSystem("EPSG:3844")))
        self.assertFalse(self.dialog.crs_warning.isVisibleTo(self.dialog))

    def test_shown_for_a_layer_without_a_crs(self):
        self._open_with(_line_layer("cad_drawing"))
        self.assertTrue(self.dialog.crs_warning.isVisibleTo(self.dialog))

    def test_follows_the_layer_combo(self):
        crsless = _line_layer("cad_drawing")
        projected = _line_layer(
            "stereo70", QgsCoordinateReferenceSystem("EPSG:3844"))
        dialog = self._open_with(crsless, projected)
        dialog.layer_combo.setLayer(projected)
        self.assertFalse(dialog.crs_warning.isVisibleTo(dialog))
        dialog.layer_combo.setLayer(crsless)
        self.assertTrue(dialog.crs_warning.isVisibleTo(dialog))

    def test_shown_even_when_the_layer_field_is_missing(self):
        # _populate_values returns early without a "Layer" field, and the
        # warning is set before that return on purpose - a raw DXF that has
        # not been imported properly is exactly the case that needs it
        self._open_with(_line_layer("plain_lines", layer_field=False))
        self.assertTrue(self.dialog.crs_warning.isVisibleTo(self.dialog))

    def test_hidden_with_no_layer_at_all(self):
        self._open_with()
        self.assertFalse(self.dialog.crs_warning.isVisibleTo(self.dialog))


if __name__ == "__main__":
    unittest.main()
