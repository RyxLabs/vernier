# -*- coding: utf-8 -*-
# SPDX-FileCopyrightText: 2026 RYXLAB SOFT TECH SRL (RyxLabs)
# SPDX-License-Identifier: GPL-2.0-or-later
"""The readout's signal wiring. geometryChanged arrives once per feature, so moving a selection of n parcels would measure n features n times if every signal recomputed - these pin the debounce that collapses a burst into one pass. Needs a GUI-enabled QgsApplication for the status-bar label, offscreen is fine. iface is faked down to the three members AreaReadout touches."""

import os
import sys
import time
import unittest

_PLUGINS_DIR = os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))))
if _PLUGINS_DIR not in sys.path:
    sys.path.insert(0, _PLUGINS_DIR)

from qgis.core import (  # noqa: E402  # type: ignore
    QgsApplication, QgsCoordinateReferenceSystem, QgsFeature, QgsGeometry,
    QgsVectorLayer,
)
from qgis.PyQt.QtCore import QObject, pyqtSignal  # noqa: E402  # type: ignore
from qgis.PyQt.QtWidgets import QApplication  # noqa: E402  # type: ignore

from vernier.tools.area_readout import (  # noqa: E402
    _DEBOUNCE_MS, AreaReadout,
)

QGS = None


def setUpModule():
    global QGS
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    QGS = QgsApplication([], True)
    QGS.initQgis()


def tearDownModule():
    QGS.exitQgis()


def _pump(ms):
    """Run the event loop long enough for a queued timer to fire."""
    deadline = time.monotonic() + ms / 1000.0
    while time.monotonic() < deadline:
        QApplication.processEvents()
        time.sleep(0.005)
    QApplication.processEvents()


def _polygon_layer(count):
    layer = QgsVectorLayer("Polygon?field=id:integer", "parcels", "memory")
    layer.setCrs(QgsCoordinateReferenceSystem("EPSG:32635"))
    feats = []
    for i in range(count):
        f = QgsFeature(layer.fields())
        x = i * 20
        f.setGeometry(QgsGeometry.fromWkt(
            f"POLYGON(({x} 0, {x + 10} 0, {x + 10} 10, {x} 10, {x} 0))"))
        f.setAttributes([i])
        feats.append(f)
    layer.dataProvider().addFeatures(feats)
    return layer


class _FakeStatusBar:

    def __init__(self):
        self.widgets = []

    def addPermanentWidget(self, widget):
        self.widgets.append(widget)

    def removeWidget(self, widget):
        if widget in self.widgets:
            self.widgets.remove(widget)


class _FakeCanvas(QObject):

    currentLayerChanged = pyqtSignal(object)


class _FakeIface(QObject):
    """Only what AreaReadout actually calls."""

    def __init__(self, layer=None):
        super().__init__()
        self._canvas = _FakeCanvas()
        self._status = _FakeStatusBar()
        self._layer = layer

    def statusBarIface(self):
        return self._status

    def mapCanvas(self):
        return self._canvas

    def activeLayer(self):
        return self._layer


class TestReadoutDebounce(unittest.TestCase):

    def setUp(self):
        self.layer = _polygon_layer(6)
        self.readout = AreaReadout(_FakeIface(self.layer))
        self.passes = []
        real = self.readout._readout_text

        def counting():
            self.passes.append(1)
            return real()

        self.readout._readout_text = counting

    def tearDown(self):
        self.readout.cleanup()

    def _edit_every_geometry(self):
        self.layer.selectAll()
        self.layer.startEditing()
        for i, fid in enumerate(self.layer.allFeatureIds()):
            x = i * 20
            self.layer.changeGeometry(fid, QgsGeometry.fromWkt(
                f"POLYGON(({x} 1, {x + 10} 1, {x + 10} 11, {x} 11, {x} 1))"))
        self.layer.rollBack()

    def test_a_burst_of_edits_costs_one_pass(self):
        self.passes.clear()
        self._edit_every_geometry()
        # nothing may run inside the burst, that is the freeze this prevents
        self.assertEqual(self.passes, [])
        _pump(_DEBOUNCE_MS * 4)
        self.assertEqual(len(self.passes), 1)

    def test_selection_change_is_debounced_too(self):
        self.passes.clear()
        self.layer.selectAll()
        self.layer.removeSelection()
        self.layer.selectAll()
        self.assertEqual(self.passes, [])
        _pump(_DEBOUNCE_MS * 4)
        self.assertEqual(len(self.passes), 1)

    def test_cleanup_drops_a_pending_pass(self):
        self.layer.selectAll()
        self.passes.clear()
        self.readout._schedule()
        self.readout.cleanup()
        _pump(_DEBOUNCE_MS * 4)
        # the label is gone by now, so a late timeout would reach a dead widget
        self.assertEqual(self.passes, [])


if __name__ == "__main__":
    unittest.main()
