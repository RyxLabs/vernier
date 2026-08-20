# -*- coding: utf-8 -*-
# SPDX-FileCopyrightText: 2026 RYXLAB SOFT TECH SRL (RyxLabs)
# SPDX-License-Identifier: GPL-2.0-or-later
"""extract_centerlines and CenterlineTask, skipping themselves when shapely will not import. Rectangle, trapezoid and L-shape fixtures pin the straightened axis geometry - mid-end to mid-end, no corner spurs - and the U-shape cases prove endpoint extension is scale-invariant so degree-based CRSes behave like metric ones."""

import os
import sys
import unittest

from qgis.core import (  # type: ignore
    QgsApplication, QgsFeature, QgsField, QgsGeometry, QgsVectorLayer,
)

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))))

from vernier.qt_compat import FIELD_STRING  # noqa: E402
from vernier.services import centerline_service  # noqa: E402
from vernier.services.centerline_service import (  # noqa: E402
    extract_centerlines,
)

# 100 x 10, long axis along x at y = 5
RECTANGLE = "POLYGON((0 0, 100 0, 100 10, 0 10, 0 0))"
# long thin parcel with slanted end edges - the axis runs between the midpoints of those ends, (0.5 5.5) and (200.5 8)
TRAPEZOID = "POLYGON((0 0, 200 3, 201 13, 1 11, 0 0))"
# L shape: two 10-wide arms, ~100 long each
L_SHAPE = "POLYGON((0 0, 100 0, 100 10, 10 10, 10 100, 0 100, 0 0))"
# U shape, two 30-wide arms on a 10-tall base with the notch open at the top. a perpendicular cut through one arm also crosses the other, so side classification can't lump all the crossings together
U_SHAPE_PTS = [(0, 0), (100, 0), (100, 100), (70, 100), (70, 10),
               (30, 10), (30, 100), (0, 100)]

QGS = None


def setUpModule():
    global QGS
    QGS = QgsApplication([], False)
    QGS.initQgis()


def tearDownModule():
    QGS.exitQgis()


def _polygon_layer(wkts):
    """Memory polygon layer with one 'name' attribute: f0, f1, ..."""
    layer = QgsVectorLayer("Polygon", "fixture", "memory")
    layer.dataProvider().addAttributes([QgsField("name", FIELD_STRING)])
    layer.updateFields()
    features = []
    for i, wkt in enumerate(wkts):
        feature = QgsFeature(layer.fields())
        feature.setGeometry(QgsGeometry.fromWkt(wkt))
        feature.setAttributes([f"f{i}"])
        features.append(feature)
    layer.dataProvider().addFeatures(features)
    return layer


def _vertices(geometry):
    return [(v.x(), v.y()) for v in geometry.vertices()]


@unittest.skipUnless(centerline_service.HAS_SHAPELY, "shapely not installed")
class TestExtractCenterlines(unittest.TestCase):

    def test_rectangle_yields_straight_mid_end_to_mid_end_line(self):
        layer = _polygon_layer([RECTANGLE])
        output, ok, skip, err = extract_centerlines(
            layer, densify_distance=1.0)
        self.assertIsNotNone(output)
        self.assertEqual((ok, skip, err), (1, 0, 0))
        self.assertEqual(output.featureCount(), 1)

        feature = next(output.getFeatures())
        geometry = feature.geometry()
        # one connected line along the axis, straightened down to a handful of vertices - no sampling zigzag, no corner spurs
        self.assertEqual(len(geometry.asMultiPolyline()), 1)
        verts = _vertices(geometry)
        self.assertLessEqual(len(verts), 6)
        self.assertGreater(geometry.length(), 98)
        self.assertLess(geometry.length(), 103)
        xs = [x for x, _ in verts]
        # extended to the middle of each end edge, not into a corner
        self.assertLess(min(xs), 1.0)
        self.assertGreater(max(xs), 99.0)
        self.assertTrue(all(4 <= y <= 6 for _, y in verts))

    def test_trapezoid_axis_hits_the_end_edge_midpoints(self):
        # slanted end edges: (0 0)-(1 11) and (200 3)-(201 13)
        layer = _polygon_layer([TRAPEZOID])
        output, ok, skip, err = extract_centerlines(
            layer, densify_distance=1.0)
        self.assertEqual((ok, skip, err), (1, 0, 0))

        geometry = next(output.getFeatures()).geometry()
        verts = _vertices(geometry)
        self.assertLessEqual(len(verts), 6)
        ends = sorted((verts[0], verts[-1]))
        self.assertAlmostEqual(ends[0][0], 0.5, delta=1.5)
        self.assertAlmostEqual(ends[0][1], 5.5, delta=1.5)
        self.assertAlmostEqual(ends[1][0], 200.5, delta=1.5)
        self.assertAlmostEqual(ends[1][1], 8.0, delta=1.5)

    def test_smoothing_is_off_by_default_and_applies_when_asked(self):
        layer = _polygon_layer([L_SHAPE])
        base_out, ok, _, _ = extract_centerlines(layer, densify_distance=1.0)
        self.assertEqual(ok, 1)
        base_verts = _vertices(next(base_out.getFeatures()).geometry())
        # the default is straight output - a regression back to smooth=True would blow this up to dozens of vertices
        self.assertLessEqual(len(base_verts), 8)

        smooth_out, ok, _, _ = extract_centerlines(
            layer, densify_distance=1.0, smooth=True, smooth_iterations=3)
        self.assertEqual(ok, 1)
        geometry = next(smooth_out.getFeatures()).geometry()
        self.assertEqual(len(geometry.asMultiPolyline()), 1)
        # Chaikin roughly doubles the vertex count per pass, so it becoming a no-op is visible here
        self.assertGreater(len(_vertices(geometry)), len(base_verts) + 4)
        # smoothing rounds the bend but must not shorten the arms away
        self.assertGreater(geometry.length(), 150)

    def test_extend_to_boundary_reaches_the_arm_ends(self):
        from shapely.geometry import LineString, Polygon
        polygon = Polygon(U_SHAPE_PTS)
        # left arm axis, stopping short of the base and the arm top
        line = LineString([(15, 30), (15, 90)])
        extended = centerline_service._extend_to_boundary(line, polygon)
        ys = [y for _, y in extended.coords]
        self.assertAlmostEqual(min(ys), 0.0, delta=1.0)
        self.assertAlmostEqual(max(ys), 100.0, delta=1.0)

    def test_extend_to_boundary_is_scale_invariant(self):
        # same shape in degree-sized coordinates. the tolerances have to scale with the geometry rather than be absolute layer units, or the cut lumps both arms together and the endpoint drifts into the notch
        from shapely.geometry import LineString, Polygon
        s = 1e-4
        polygon = Polygon([(x * s, y * s) for x, y in U_SHAPE_PTS])
        line = LineString([(15 * s, 30 * s), (15 * s, 90 * s)])
        extended = centerline_service._extend_to_boundary(line, polygon)
        ys = [y for _, y in extended.coords]
        self.assertAlmostEqual(min(ys), 0.0, delta=1.0 * s)
        self.assertAlmostEqual(max(ys), 100.0 * s, delta=1.0 * s)

    def test_l_shape_yields_connected_line(self):
        layer = _polygon_layer([L_SHAPE])
        output, ok, skip, err = extract_centerlines(
            layer, densify_distance=1.0)
        self.assertIsNotNone(output)
        self.assertEqual((ok, skip, err), (1, 0, 0))

        feature = next(output.getFeatures())
        geometry = feature.geometry()
        # a single merged part means the axis is connected through the bend
        self.assertEqual(len(geometry.asMultiPolyline()), 1)
        # both arms are ~95 of axis each, so well over one arm's worth
        self.assertGreater(geometry.length(), 150)

    def test_network_mode_keeps_the_full_skeleton(self):
        # T corridor: 100 x 10 bar with a 20-wide stem up to y = 60. The per-chain prune must not eat the axis tip-first - the raw axis is chains of sub-min_length segments
        t_shape = ("POLYGON((0 0, 100 0, 100 10, 60 10, 60 60, 40 60, "
                   "40 10, 0 10, 0 0))")
        layer = _polygon_layer([t_shape])
        output, ok, _, _ = extract_centerlines(
            layer, densify_distance=1.0, trunk_only=False, prune=True)
        self.assertEqual(ok, 1)
        geometry = next(output.getFeatures()).geometry()
        # bar axis ~100 plus stem axis ~50, corner branches on top
        self.assertGreater(geometry.length(), 120)

    def test_attributes_copied_and_cl_fields_populated(self):
        layer = _polygon_layer([RECTANGLE])
        source_id = next(layer.getFeatures()).id()
        output, ok, _, _ = extract_centerlines(layer, densify_distance=1.0)
        self.assertEqual(ok, 1)

        names = {field.name() for field in output.fields()}
        self.assertIn("name", names)
        self.assertIn("cl_length", names)
        self.assertIn("cl_source_id", names)

        feature = next(output.getFeatures())
        self.assertEqual(feature["name"], "f0")
        self.assertEqual(feature["cl_source_id"], source_id)
        self.assertGreater(feature["cl_length"], 80)
        self.assertAlmostEqual(
            feature["cl_length"], feature.geometry().length(), places=2)

    def test_output_crs_matches_input(self):
        layer = QgsVectorLayer("Polygon?crs=EPSG:31700", "fixture", "memory")
        feature = QgsFeature(layer.fields())
        feature.setGeometry(QgsGeometry.fromWkt(RECTANGLE))
        layer.dataProvider().addFeatures([feature])
        output, ok, _, _ = extract_centerlines(layer, densify_distance=1.0)
        self.assertEqual(ok, 1)
        self.assertEqual(output.crs().authid(), "EPSG:31700")

    def test_empty_layer_returns_nothing(self):
        layer = _polygon_layer([])
        output, ok, skip, err = extract_centerlines(layer)
        self.assertIsNone(output)
        self.assertEqual((ok, skip, err), (0, 0, 0))

    def test_selected_only_processes_selection(self):
        layer = _polygon_layer([RECTANGLE, L_SHAPE])
        first_id = next(layer.getFeatures()).id()
        layer.selectByIds([first_id])
        output, ok, _, _ = extract_centerlines(
            layer, densify_distance=1.0, selected_only=True)
        self.assertEqual(ok, 1)
        self.assertEqual(output.featureCount(), 1)
        self.assertEqual(
            next(output.getFeatures())["cl_source_id"], first_id)

    def test_selected_only_with_empty_selection_processes_nothing(self):
        # must not silently fall back to the whole layer
        layer = _polygon_layer([RECTANGLE, L_SHAPE])
        layer.removeSelection()
        output, ok, skip, err = extract_centerlines(
            layer, densify_distance=1.0, selected_only=True)
        self.assertIsNone(output)
        self.assertEqual((ok, skip, err), (0, 0, 0))


class CenterlineTaskTests(unittest.TestCase):
    """The background path has to produce what the blocking one does, and its option defaults have to stay pinned to extract_centerlines - two signatures owning one set of defaults is a standing invitation to drift."""

    def setUp(self):
        if not centerline_service.HAS_SHAPELY:
            self.skipTest("shapely is not installed")

    def test_option_defaults_match_extract_centerlines(self):
        import inspect
        blocking = inspect.signature(extract_centerlines).parameters
        task = inspect.signature(
            centerline_service.CenterlineTask.__init__).parameters
        shared = [
            name for name in blocking
            if name in task and name not in ("self", "layer")
        ]
        # every pipeline option plus output_name and selected_only
        self.assertGreaterEqual(len(shared), 11)
        mismatched = {
            name: (blocking[name].default, task[name].default)
            for name in shared
            if blocking[name].default != task[name].default
        }
        self.assertEqual(mismatched, {})

    def test_task_matches_the_blocking_result(self):
        layer = _polygon_layer([RECTANGLE, L_SHAPE])
        expected, exp_ok, exp_skip, exp_err = extract_centerlines(
            layer, densify_distance=1.0, output_name="ref")

        task = centerline_service.CenterlineTask(
            layer, densify_distance=1.0, output_name="task")
        # run() straight, no task manager: this is about the work, not the scheduling
        self.assertTrue(task.run())
        self.assertEqual((task.ok, task.skipped, task.errors),
                         (exp_ok, exp_skip, exp_err))

        produced = task.build_layer()
        self.assertIsNotNone(produced)
        self.assertEqual(produced.name(), "task")
        self.assertEqual(produced.crs().authid(), expected.crs().authid())
        self.assertEqual(produced.featureCount(), expected.featureCount())
        self.assertEqual(
            [f.name() for f in produced.fields()],
            [f.name() for f in expected.fields()])
        for got, want in zip(produced.getFeatures(), expected.getFeatures()):
            self.assertEqual(got.geometry().asWkt(1),
                             want.geometry().asWkt(1))
            self.assertEqual(got.attributes(), want.attributes())

    def test_task_honors_the_selection(self):
        layer = _polygon_layer([RECTANGLE, L_SHAPE])
        first_id = next(layer.getFeatures()).id()
        layer.selectByIds([first_id])
        task = centerline_service.CenterlineTask(
            layer, densify_distance=1.0, selected_only=True)
        self.assertTrue(task.run())
        self.assertEqual(task.ok, 1)
        produced = task.build_layer()
        self.assertEqual(produced.featureCount(), 1)
        self.assertEqual(
            next(produced.getFeatures())["cl_source_id"], first_id)

    def test_canceling_stops_the_run_and_yields_no_layer(self):
        layer = _polygon_layer([RECTANGLE, L_SHAPE])
        task = centerline_service.CenterlineTask(layer, densify_distance=1.0)
        task.cancel()
        self.assertFalse(task.run())
        self.assertEqual(task.ok, 0)
        self.assertIsNone(task.build_layer())

    def test_reads_the_layer_through_a_snapshot(self):
        """run() must not reach back into the layer, so a source captured up front still yields the original features after the layer changes underneath."""
        layer = _polygon_layer([RECTANGLE])
        task = centerline_service.CenterlineTask(layer, densify_distance=1.0)
        layer.startEditing()
        layer.deleteFeature(next(layer.getFeatures()).id())
        self.assertTrue(task.run())
        self.assertEqual(task.ok, 1)
        layer.rollBack()


if __name__ == "__main__":
    unittest.main()
