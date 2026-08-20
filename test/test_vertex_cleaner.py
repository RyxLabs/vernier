# -*- coding: utf-8 -*-
# SPDX-FileCopyrightText: 2026 RYXLAB SOFT TECH SRL (RyxLabs)
# SPDX-License-Identifier: GPL-2.0-or-later
"""clean_layer on memory layers. The interesting cases are the ones the cleaner must NOT touch - vertices shared with a neighbor, ring closure, line endpoints, and features excluded by expression or fid filter."""

import os
import sys
import unittest

from qgis.core import (  # type: ignore
    QgsApplication, QgsFeature, QgsField, QgsGeometry, QgsVectorLayer,
)

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))))

from vernier.qt_compat import FIELD_STRING  # noqa: E402
from vernier.tools.vertex_cleaner import clean_layer  # noqa: E402

# unit square with a stray near-duplicate just after the (1,1) corner. the bump deviates more from the local chord than the corner does, so the collinearity tiebreak alone would delete the corner and only the neighbor count saves it
SQUARE_A_BUMP = "POLYGON((0 0, 1 0, 1 1, 1.002 1.001, 0 1, 0 0))"
# adjacent square sharing the edge x=1, so it shares the (1,1) corner
SQUARE_B = "POLYGON((1 0, 2 0, 2 1, 1 1, 1 0))"
# same bump square translated x+10, away from any neighbor
SQUARE_C_BUMP = "POLYGON((10 0, 11 0, 11 1, 11.002 1.001, 10 1, 10 0))"

# hole ring whose close pair spans the ring closure seam
DONUT = ("POLYGON((0 0, 10 0, 10 10, 0 10, 0 0),"
         "(2 2, 8 2, 8 8, 2 8, 2 2.003, 2 2))")

MULTI = ("MULTIPOLYGON(((0 0, 1 0, 1 1, 1 1.003, 0 1, 0 0)),"
         "((5 5, 6 5, 6 6, 5 6, 5 5)))")

TRIANGLE_TIGHT = "POLYGON((0 0, 0.003 0, 0 0.003, 0 0))"

LINE_MID = "LINESTRING(0 0, 5 0, 5.002 0, 10 0)"
LINE_START = "LINESTRING(0 0, 0.003 0, 10 0)"
# shares the interior vertex (0.003, 0) of LINE_START
LINE_BRANCH = "LINESTRING(0.003 0, 5 5)"

QGS = None


def setUpModule():
    global QGS
    QGS = QgsApplication([], False)
    QGS.initQgis()


def tearDownModule():
    QGS.exitQgis()


def _layer(geometry_type, wkts):
    """Memory layer with one 'name' attribute: f0, f1, ..."""
    layer = QgsVectorLayer(geometry_type, "fixture", "memory")
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


def _geoms_by_name(result_layer):
    return {f["name"]: f.geometry() for f in result_layer.getFeatures()}


def _ring_points(geometry, ring=0):
    return [(p.x(), p.y()) for p in geometry.asPolygon()[ring]]


def _line_points(geometry):
    return [(p.x(), p.y()) for p in geometry.asPolyline()]


class TestSharedBoundary(unittest.TestCase):

    def test_shared_vertex_survives(self):
        layer = _layer("Polygon", [SQUARE_A_BUMP, SQUARE_B])
        result = clean_layer(layer, 0.005)
        geoms = _geoms_by_name(result.layer)
        points = set(_ring_points(geoms["f0"]))
        self.assertIn((1.0, 1.0), points)
        self.assertNotIn((1.002, 1.001), points)
        self.assertEqual(set(_ring_points(geoms["f1"])),
                         {(1.0, 0.0), (2.0, 0.0), (2.0, 1.0), (1.0, 1.0)})
        self.assertEqual(result.removed_count, 1)
        self.assertEqual(
            [(p.x(), p.y()) for p in result.removed_points],
            [(1.002, 1.001)])

    def test_without_neighbor_tiebreak_keeps_bump(self):
        # control for the test above - same geometry, no adjacent square, so the collinearity tiebreak fires and takes the corner
        layer = _layer("Polygon", [SQUARE_A_BUMP])
        result = clean_layer(layer, 0.005)
        points = set(_ring_points(_geoms_by_name(result.layer)["f0"]))
        self.assertIn((1.002, 1.001), points)
        self.assertNotIn((1.0, 1.0), points)


class TestRingsAndParts(unittest.TestCase):

    def test_interior_ring_stays_closed_and_valid(self):
        layer = _layer("Polygon", [DONUT])
        result = clean_layer(layer, 0.005)
        geom = _geoms_by_name(result.layer)["f0"]
        rings = geom.asPolygon()
        self.assertEqual(len(rings), 2)
        hole = rings[1]
        self.assertEqual(hole[0], hole[-1])
        self.assertEqual(len(hole), 5)  # 4 unique points + closure
        self.assertNotIn((2.0, 2.003), {(p.x(), p.y()) for p in hole})
        self.assertTrue(geom.isGeosValid())
        self.assertEqual(result.removed_count, 1)

    def test_multipart_cleaned_per_part(self):
        layer = _layer("MultiPolygon", [MULTI])
        result = clean_layer(layer, 0.005)
        geom = _geoms_by_name(result.layer)["f0"]
        self.assertTrue(geom.isMultipart())
        parts = geom.asMultiPolygon()
        self.assertEqual(len(parts), 2)
        self.assertEqual(len(parts[0][0]), 5)  # one vertex removed
        self.assertEqual(len(parts[1][0]), 5)  # untouched square
        self.assertTrue(geom.isGeosValid())
        self.assertEqual(result.removed_count, 1)

    def test_ring_never_drops_below_three_points(self):
        layer = _layer("Polygon", [TRIANGLE_TIGHT])
        result = clean_layer(layer, 0.005)
        geom = _geoms_by_name(result.layer)["f0"]
        self.assertEqual(len(geom.asPolygon()[0]), 4)
        self.assertEqual(result.removed_count, 0)


class TestLines(unittest.TestCase):

    def test_interior_pair_collapsed(self):
        layer = _layer("LineString", [LINE_MID])
        result = clean_layer(layer, 0.005)
        self.assertEqual(_line_points(_geoms_by_name(result.layer)["f0"]),
                         [(0.0, 0.0), (5.002, 0.0), (10.0, 0.0)])
        self.assertEqual(result.removed_count, 1)

    def test_endpoint_survives(self):
        layer = _layer("LineString", [LINE_START])
        result = clean_layer(layer, 0.005)
        self.assertEqual(_line_points(_geoms_by_name(result.layer)["f0"]),
                         [(0.0, 0.0), (10.0, 0.0)])

    def test_shared_interior_vertex_near_endpoint_left_alone(self):
        # (0.003, 0) is shared with the branch line, so deleting it detaches the branch and deleting the endpoint moves the line end. the pair gets skipped entirely
        layer = _layer("LineString", [LINE_START, LINE_BRANCH])
        result = clean_layer(layer, 0.005)
        geoms = _geoms_by_name(result.layer)
        self.assertEqual(_line_points(geoms["f0"]),
                         [(0.0, 0.0), (0.003, 0.0), (10.0, 0.0)])
        self.assertEqual(_line_points(geoms["f1"]),
                         [(0.003, 0.0), (5.0, 5.0)])
        self.assertEqual(result.removed_count, 0)


class TestFilters(unittest.TestCase):

    def test_skip_expression(self):
        layer = _layer("Polygon", [SQUARE_A_BUMP, SQUARE_C_BUMP])
        result = clean_layer(layer, 0.005,
                             skip_expression="\"name\" = 'f0'")
        geoms = _geoms_by_name(result.layer)
        self.assertEqual(len(_ring_points(geoms["f0"])), 6)  # untouched
        self.assertEqual(len(_ring_points(geoms["f1"])), 5)  # cleaned
        self.assertEqual(result.skipped_count, 1)
        self.assertEqual(result.removed_count, 1)

    def test_invalid_expression_raises(self):
        layer = _layer("Polygon", [SQUARE_A_BUMP])
        with self.assertRaises(ValueError):
            clean_layer(layer, 0.005, skip_expression="not valid ((")

    def test_only_fids_restricts_cleaning(self):
        layer = _layer("Polygon", [SQUARE_A_BUMP, SQUARE_C_BUMP])
        fids = {f["name"]: f.id() for f in layer.getFeatures()}
        result = clean_layer(layer, 0.005, only_fids={fids["f1"]})
        geoms = _geoms_by_name(result.layer)
        self.assertEqual(len(_ring_points(geoms["f0"])), 6)  # untouched
        self.assertEqual(len(_ring_points(geoms["f1"])), 5)  # cleaned


class TestResultLayer(unittest.TestCase):

    def test_crs_and_fields_copied(self):
        layer = _layer("Polygon?crs=EPSG:3844", [SQUARE_A_BUMP])
        result = clean_layer(layer, 0.005)
        self.assertEqual(result.layer.crs().authid(), "EPSG:3844")
        self.assertEqual([f.name() for f in result.layer.fields()],
                         ["name"])
        self.assertEqual(result.layer.featureCount(), 1)


if __name__ == "__main__":
    unittest.main()
