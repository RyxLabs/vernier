# -*- coding: utf-8 -*-
# SPDX-FileCopyrightText: 2026 RYXLAB SOFT TECH SRL (RyxLabs)
# SPDX-License-Identifier: GPL-2.0-or-later
"""The Labels column of the DXF export table. Ticking a box has to open the field chooser for that layer by itself, since the panel only ever shows the selected row, and a ticked box with no field has to say so - it writes a DXF with no text. Needs a GUI-enabled QgsApplication, offscreen is fine."""

import os
import sys
import unittest

_PLUGINS_DIR = os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))))
if _PLUGINS_DIR not in sys.path:
    sys.path.insert(0, _PLUGINS_DIR)

from qgis.core import (  # noqa: E402  # type: ignore
    QgsApplication, QgsFeature, QgsGeometry, QgsProject, QgsVectorLayer,
)
from qgis.PyQt.QtCore import Qt  # noqa: E402  # type: ignore

from vernier.dialogs.dxf_export_dialog import (  # noqa: E402
    COL_LABELS, COL_NAME, DxfExportDialog,
)

QGS = None


def setUpModule():
    global QGS
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    QGS = QgsApplication([], True)
    QGS.initQgis()


def tearDownModule():
    QGS.exitQgis()


def _layer(name):
    layer = QgsVectorLayer(
        "Polygon?crs=EPSG:3844&field=id:integer&field=sector:string",
        name, "memory")
    feature = QgsFeature(layer.fields())
    feature.setGeometry(
        QgsGeometry.fromWkt("POLYGON((0 0, 10 0, 10 10, 0 10, 0 0))"))
    feature.setAttributes([1, "S1"])
    layer.dataProvider().addFeatures([feature])
    return layer


class LabelPanelTests(unittest.TestCase):

    def setUp(self):
        self.first = _layer("parcele")
        self.second = _layer("cladiri")
        QgsProject.instance().addMapLayer(self.first)
        QgsProject.instance().addMapLayer(self.second)
        self.dialog = DxfExportDialog(iface=None)
        self.dialog.show()
        QgsApplication.processEvents()
        self.row = self._row_of("parcele")
        self.other_row = self._row_of("cladiri")
        for row in (self.row, self.other_row):
            self.dialog._get_checkbox(row, COL_LABELS).setChecked(False)
        QgsApplication.processEvents()

    def tearDown(self):
        self.dialog.done(0)
        self.dialog.deleteLater()
        QgsProject.instance().removeAllMapLayers()

    def _row_of(self, name):
        for row in range(self.dialog.table.rowCount()):
            if name in self.dialog.table.item(row, COL_NAME).text():
                return row
        raise AssertionError(f"no row for {name}")

    def test_ticking_labels_selects_the_row_and_opens_the_panel(self):
        self.dialog.table.setCurrentCell(self.other_row, COL_NAME)
        self.dialog._get_checkbox(self.row, COL_LABELS).setChecked(True)
        QgsApplication.processEvents()
        self.assertEqual(self.dialog.table.currentRow(), self.row)
        self.assertTrue(self.dialog.label_group.isVisible())
        self.assertEqual(self.dialog._tree_layer_id, self.first.id())

    def test_status_line_names_layers_with_no_field_chosen(self):
        self.dialog._get_checkbox(self.row, COL_LABELS).setChecked(True)
        QgsApplication.processEvents()
        self.assertIn("parcele", self.dialog.label_status.text())
        # ticking a field is what the message asks for
        self.dialog.field_tree.topLevelItem(1).setCheckState(
            0, Qt.CheckState.Checked)
        QgsApplication.processEvents()
        self.assertNotIn("parcele", self.dialog.label_status.text())

    def test_unticking_another_row_leaves_the_open_panel_alone(self):
        for row in (self.row, self.other_row):
            self.dialog._get_checkbox(row, COL_LABELS).setChecked(True)
        self.dialog.table.setCurrentCell(self.row, COL_NAME)
        QgsApplication.processEvents()
        self.dialog._get_checkbox(self.other_row, COL_LABELS).setChecked(False)
        QgsApplication.processEvents()
        self.assertTrue(self.dialog.label_group.isVisible())
        self.assertEqual(self.dialog._tree_layer_id, self.first.id())


if __name__ == "__main__":
    unittest.main()
