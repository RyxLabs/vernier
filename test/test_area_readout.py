# -*- coding: utf-8 -*-
# SPDX-FileCopyrightText: 2026 RYXLAB SOFT TECH SRL (RyxLabs)
# SPDX-License-Identifier: GPL-2.0-or-later
"""The readout's signal wiring and the measurement guard. geometryChanged arrives once per feature, so moving a selection of n parcels would measure n features n times if every signal recomputed - these pin the debounce that collapses a burst into one pass. Needs a GUI-enabled QgsApplication for the status-bar label, offscreen is fine. iface is faked down to the three members AreaReadout touches."""

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
    QgsPointXY, QgsProject, QgsVectorLayer,
)
from qgis.PyQt.QtCore import QObject, pyqtSignal  # noqa: E402  # type: ignore
from qgis.PyQt.QtWidgets import QApplication  # noqa: E402  # type: ignore

from vernier.tools.area_readout import (  # noqa: E402
    _DEBOUNCE_MS, AreaReadout, measure_area_sqm,
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


def _square(x, y, side=100.0):
    return QgsGeometry.fromWkt(
        f"POLYGON(({x} {y}, {x + side} {y}, {x + side} {y + side}, "
        f"{x} {y + side}, {x} {y}))")


class _MeasuringProject(unittest.TestCase):
    """Measurements taken on an ellipsoid. QgsProject
    ignores setEllipsoid() until the project itself has a CRS, and picks the
    matching ellipsoid up on its own once it does - so a project set up like any
    real one measures ellipsoidally, and an unmeasurable layer comes back NaN
    instead of falling through to a planar figure."""

    def setUp(self):
        self.project = QgsProject.instance()
        self._crs = self.project.crs()
        self._ellipsoid = self.project.ellipsoid()
        self.project.setCrs(QgsCoordinateReferenceSystem("EPSG:32635"))
        self.project.setEllipsoid("WGS84")
        # a silently-ignored ellipsoid would leave these tests measuring planar
        self.assertEqual(self.project.ellipsoid(), "WGS84")

    def tearDown(self):
        self.project.setCrs(self._crs)
        self.project.setEllipsoid(self._ellipsoid)


class TestMeasureAreaSqm(_MeasuringProject):
    """QgsDistanceArea returns NaN rather than raising for a layer with no CRS,
    which is what CAD lines built into polygons carry, and for a geometry with a
    NaN vertex. Both used to reach the status bar as "nan m²" - the first now
    measures planar, the second has no figure to give."""

    def test_projected_crs_measures_normally(self):
        crs = QgsCoordinateReferenceSystem("EPSG:32635")
        sqm = measure_area_sqm([_square(500_000, 4_000_000)], crs,
                               self.project)
        self.assertAlmostEqual(sqm, 10_000.0, delta=50.0)

    def test_layer_without_a_crs_measures_planar(self):
        # exact, not approximate: no CRS means no ellipsoid and no reprojection,
        # so this is the raw coordinate area passed through unconverted
        sqm = measure_area_sqm([_square(0, 0)],
                               QgsCoordinateReferenceSystem(), self.project)
        self.assertEqual(sqm, 10_000.0)

    def test_planar_fallback_ignores_the_project_ellipsoid(self):
        # the ellipsoid is what turned this into NaN, so it must not be consulted
        crsless = QgsCoordinateReferenceSystem()
        self.assertEqual(self.project.ellipsoid(), "WGS84")
        self.assertEqual(
            measure_area_sqm([_square(0, 0)], crsless, self.project),
            measure_area_sqm([_square(4_000_000, 4_000_000)], crsless,
                             self.project))

    def test_nan_vertex_is_refused(self):
        nan_geom = QgsGeometry.fromPolygonXY([[
            QgsPointXY(0.0, 0.0), QgsPointXY(float("nan"), 0.0),
            QgsPointXY(100.0, 100.0), QgsPointXY(0.0, 0.0)]])
        sqm = measure_area_sqm(
            [nan_geom], QgsCoordinateReferenceSystem("EPSG:32635"),
            self.project)
        self.assertIsNone(sqm)

    def test_empty_and_missing_geometries_are_skipped(self):
        crs = QgsCoordinateReferenceSystem("EPSG:32635")
        sqm = measure_area_sqm(
            [None, QgsGeometry(), _square(500_000, 4_000_000)], crs,
            self.project)
        self.assertAlmostEqual(sqm, 10_000.0, delta=50.0)


class TestReadoutTextGuard(_MeasuringProject):
    """End to end: the label stays empty instead of printing NaN."""

    def setUp(self):
        super().setUp()
        self.layer = _polygon_layer(3)
        self.layer.setCrs(QgsCoordinateReferenceSystem())
        self.readout = AreaReadout(_FakeIface(self.layer))

    def tearDown(self):
        self.readout.cleanup()
        super().tearDown()

    def test_crsless_layer_reads_a_planar_figure(self):
        self.layer.selectAll()
        text = self.readout._readout_text()
        # the unit the settings resolve to is not this test's business, only that
        # a real figure comes back and NaN never reaches the label again
        self.assertTrue(text)
        self.assertNotIn("nan", text.lower())

    def test_nan_geometry_still_reads_empty(self):
        self.layer.startEditing()
        fid = self.layer.allFeatureIds()[0]
        self.layer.changeGeometry(fid, QgsGeometry.fromPolygonXY([[
            QgsPointXY(0.0, 0.0), QgsPointXY(float("nan"), 0.0),
            QgsPointXY(10.0, 10.0), QgsPointXY(0.0, 0.0)]]))
        self.layer.selectByIds([fid])
        self.assertEqual(self.readout._readout_text(), "")
        self.layer.rollBack()


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
