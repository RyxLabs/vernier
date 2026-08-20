# -*- coding: utf-8 -*-
# SPDX-FileCopyrightText: 2026 RYXLAB SOFT TECH SRL (RyxLabs)
# SPDX-License-Identifier: GPL-2.0-or-later
"""find_duplicates. Duplicate means same point set, not same vertex list - a rotated starting vertex and a reversed ring have to land in the same group."""

import os
import sys
import unittest

from qgis.core import (  # type: ignore
    QgsApplication, QgsFeature, QgsField, QgsGeometry, QgsVectorLayer,
)

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))))

from vernier.qt_compat import FIELD_STRING  # noqa: E402
from vernier.tools.duplicates_tool import find_duplicates  # noqa: E402

SQUARE = "POLYGON((0 0, 1 0, 1 1, 0 1, 0 0))"
# same ring as SQUARE, started from a different vertex
SQUARE_ROTATED = "POLYGON((1 1, 0 1, 0 0, 1 0, 1 1))"
# same ring as SQUARE, opposite winding direction
SQUARE_REVERSED = "POLYGON((0 0, 0 1, 1 1, 1 0, 0 0))"
TRIANGLE = "POLYGON((5 5, 6 5, 5 6, 5 5))"

QGS = None


def setUpModule():
    global QGS
    QGS = QgsApplication([], False)
    QGS.initQgis()


def tearDownModule():
    QGS.exitQgis()


def _polygon_layer(wkts, with_null_geometry=False):
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
    if with_null_geometry:
        feature = QgsFeature(layer.fields())
        feature.setAttributes(["no_geom"])
        features.append(feature)
    layer.dataProvider().addFeatures(features)
    return layer


def _names(group):
    return sorted(feature["name"] for feature in group)


class TestFindDuplicates(unittest.TestCase):

    def test_exact_duplicate_detected(self):
        layer = _polygon_layer([SQUARE, SQUARE, TRIANGLE])
        groups = find_duplicates(layer)
        self.assertEqual(len(groups), 1)
        self.assertEqual(_names(groups[0]), ["f0", "f1"])

    def test_rotated_vertex_order_detected(self):
        layer = _polygon_layer([SQUARE, SQUARE_ROTATED])
        groups = find_duplicates(layer)
        self.assertEqual(len(groups), 1)
        self.assertEqual(_names(groups[0]), ["f0", "f1"])

    def test_reversed_ring_detected(self):
        layer = _polygon_layer([SQUARE, SQUARE_REVERSED])
        groups = find_duplicates(layer)
        self.assertEqual(len(groups), 1)
        self.assertEqual(_names(groups[0]), ["f0", "f1"])

    def test_distinct_polygons_not_flagged(self):
        layer = _polygon_layer([SQUARE, TRIANGLE])
        self.assertEqual(find_duplicates(layer), [])

    def test_all_group_members_returned(self):
        layer = _polygon_layer([SQUARE, SQUARE, SQUARE])
        groups = find_duplicates(layer)
        self.assertEqual(len(groups), 1)
        self.assertEqual(_names(groups[0]), ["f0", "f1", "f2"])

    def test_two_independent_groups(self):
        layer = _polygon_layer([SQUARE, TRIANGLE, SQUARE_ROTATED, TRIANGLE])
        groups = find_duplicates(layer)
        self.assertEqual(sorted(_names(g) for g in groups),
                         [["f0", "f2"], ["f1", "f3"]])

    def test_null_geometry_ignored(self):
        layer = _polygon_layer([SQUARE, SQUARE], with_null_geometry=True)
        groups = find_duplicates(layer)
        self.assertEqual(len(groups), 1)
        self.assertEqual(_names(groups[0]), ["f0", "f1"])


if __name__ == "__main__":
    unittest.main()
